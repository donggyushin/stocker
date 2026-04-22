"""stock-agent 장중 실행 진입점 (APScheduler + Executor 오케스트레이터).

Phase 3 두 번째 산출물. 이 모듈은 **조립만** 한다 — 전략·리스크·브로커·시세의
단독 동작 계약은 이미 잠겨 있다.

스케줄 (plan.md Phase 3, ADR-0011) — `_install_jobs` 가 등록하는 cron 4종
    09:00 KST  → on_session_start      (RiskManager/Executor 세션 리셋)
    매분 00s  → on_step                (Executor.step, 9-14시 평일)
    15:00 KST  → on_force_close         (Executor.force_close_all)
    15:30 KST  → on_daily_report        (일일 요약 로그)

※ 09:30 OR-High 확정은 별도 cron 이 아닌 `on_step` 루프의 `ORBStrategy.on_bar` 부작용이다.

범위 (이번 PR)
    - `main.py` + APScheduler wiring + 드라이런 CLI 플래그
    - 주문 분기는 Protocol 어댑터(`DryRunOrderSubmitter` / `LiveOrderSubmitter`) 로 표현.

의도적으로 미포함 (후속 PR)
    - `monitor/notifier.py` (텔레그램 알림)
    - `storage/db.py` (SQLite 체결·PnL 기록)
    - `config/strategy.yaml` / `config/risk.yaml` 로더 — 현재는 코드 상수 주입.
    - 공휴일 자동 판정 — cron `day_of_week='mon-fri'` 만. 휴장일은 운영자가 프로세스 안 띄움.
    - APScheduler job store 영속화 — 단일 프로세스 인메모리 스케줄.

예외 정책 (project-wide)
    - exit code 0: 정상 종료 (SIGTERM/SIGINT 포함)
    - exit code 1: 예기치 않은 런타임 예외
    - exit code 2: 설정·입력 오류 (RuntimeError, UniverseLoadError, has_live_keys=False, 자본 ≤ 0)
    - exit code 3: I/O 오류 (로그 디렉토리 생성 실패 등 OSError)

스레드 모델
    단일 프로세스 전용 (ADR-0008). `BlockingScheduler` 로 전경 점유 — SIGINT 자연 도달.
    `RealtimeDataStore` 는 자체 데몬 스레드로 시세 수집, Executor 콜백은 스케줄러 스레드에서만 호출.
"""

from __future__ import annotations

import argparse
import signal
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from typing import Any

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger
from pydantic import ValidationError

from stock_agent.broker import KisClient
from stock_agent.config import Settings, get_settings
from stock_agent.data import (
    KospiUniverse,
    RealtimeDataStore,
    UniverseLoadError,
    load_kospi200_universe,
)
from stock_agent.execution import (
    BalanceProvider,
    DryRunOrderSubmitter,
    Executor,
    LiveBalanceProvider,
    LiveOrderSubmitter,
    OrderSubmitter,
)
from stock_agent.monitor import (
    DailySummary,
    ErrorEvent,
    Notifier,
    NullNotifier,
    TelegramNotifier,
)
from stock_agent.risk import RiskConfig, RiskManager
from stock_agent.storage import (
    NullTradingRecorder,
    SqliteTradingRecorder,
    StorageError,
    TradingRecorder,
)
from stock_agent.strategy import ORBStrategy, StrategyConfig

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_INPUT_ERROR = 2
EXIT_IO_ERROR = 3

KST = timezone(timedelta(hours=9))

# 프로젝트 루트(main.py = .../<root>/src/stock_agent/main.py) 기반 절대경로.
# 리뷰 C1: `Path("data/trading.db")` 상대경로는 프로세스 CWD 에 따라 원장
# DB 파일이 갈라져 일부 세션 기록이 누락될 수 있어 실운영 진입점에서는 절대
# 경로로 고정한다. 상대경로 기본값은 `SqliteTradingRecorder` 의 하위 호환성·
# 테스트용으로만 유지한다.
_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
_TRADING_DB_PATH: Path = _PROJECT_ROOT / "data" / "trading.db"

ClockFn = Callable[[], datetime]
"""KST aware datetime 을 반환하는 시계 함수. 테스트는 `lambda: _kst(9, 0)` 주입."""


@dataclass(slots=True)
class SessionStatus:
    """일일 세션 상태 — silent failure 루프 차단용 (C1, ADR-0011 결정 5 연장).

    `on_session_start` 성공 여부와 `on_step` 의 경고 로그 dedupe 플래그를 담는다.
    `Runtime` 자체는 frozen 이지만 본 객체는 내부 필드 mutation 이 필요하므로
    별도 컨테이너로 분리. 다음 영업일 `on_session_start` 가 성공하면 자연 복구.
    """

    started: bool = False
    fail_logged: bool = False


@dataclass(frozen=True, slots=True)
class Runtime:
    """조립 완료된 실행 환경.

    `main()` 은 이 컨테이너를 들고 시그널 핸들러·리소스 정리·스케줄러 시작을
    조율한다. `build_runtime` 이 반환. `risk_manager` 는 `_on_daily_report` 가
    공개 경로로 접근하기 위한 참조(Executor private 의존 제거), `session_status`
    는 silent failure 루프 차단용 (C1). `notifier` 는 텔레그램 알림 라우팅 —
    주입 실패 시 `NullNotifier` 로 폴백(알림 없이 세션 지속). `recorder` 는
    SQLite 원장(ADR-0013) — 주입 실패 시 `NullTradingRecorder` 폴백(영속화
    없이 세션 지속, 로그 sink 로 사후 재구성 경로 보존).
    """

    scheduler: BlockingScheduler
    executor: Executor
    realtime_store: RealtimeDataStore
    kis_client: KisClient
    args: argparse.Namespace
    risk_manager: RiskManager
    session_status: SessionStatus
    notifier: Notifier
    recorder: TradingRecorder


# ---- CLI -----------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """CLI 인자 파싱. `--starting-capital` 양수 검증은 `main()` 책임."""
    parser = argparse.ArgumentParser(
        description="stock-agent 장중 실행 (KOSPI 200 · ORB · APScheduler)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="주문 API 호출 없이 시그널·로그만 남긴다 (DryRunOrderSubmitter 주입).",
    )
    parser.add_argument(
        "--starting-capital",
        type=int,
        default=1_000_000,
        help=(
            "시작 자본(KRW). 잔고 withdrawable 과 min 을 취해 보수적으로 적용 "
            "— RiskManager 진입 사이징·서킷브레이커가 매수 가능 현금 기준으로 동작."
        ),
    )
    parser.add_argument(
        "--universe-path",
        type=Path,
        default=None,
        help="유니버스 YAML 경로. 미지정 시 config/universe.yaml 기본.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path("logs"),
        help="loguru 파일 sink 디렉토리.",
    )
    return parser.parse_args(argv)


# ---- 어댑터 분기 ----------------------------------------------------------


def _build_order_submitter(dry_run: bool, kis_client: KisClient) -> OrderSubmitter:
    """드라이런/라이브 주문 어댑터 선택. 분기 로직은 전 프로젝트에서 이 한 곳만."""
    if dry_run:
        return DryRunOrderSubmitter()
    return LiveOrderSubmitter(kis_client)


def _default_scheduler_factory() -> BlockingScheduler:
    """기본 스케줄러 — KST 타임존 고정."""
    return BlockingScheduler(timezone="Asia/Seoul")


def _default_notifier_factory(settings: Settings, dry_run: bool) -> Notifier:
    """기본 notifier 팩토리 — TelegramNotifier 조립.

    `TelegramNotifier` 생성자에서 봇 조립·가드 검증이 일어나며, 실패 시
    `RuntimeError` 전파 대신 `NullNotifier` 폴백(알림 부재가 세션 전체 실패
    보다 위험하지 않음 — logger sink 로 동일 정보 획득 가능).
    """
    try:
        return TelegramNotifier(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
            dry_run=dry_run,
        )
    except Exception as e:  # noqa: BLE001 — notifier 조립 실패는 세션을 죽이지 않는다
        logger.warning(
            f"main.notifier_factory 초기화 실패 — NullNotifier 폴백: {e.__class__.__name__}: {e}"
        )
        return NullNotifier()


def _default_recorder_factory(settings: Settings, dry_run: bool) -> TradingRecorder:  # noqa: ARG001
    """기본 recorder 팩토리 — `SqliteTradingRecorder(db_path=<프로젝트 루트>/data/trading.db)` 조립.

    `StorageError`·`RuntimeError`·`OSError` 등 초기화 실패 시 `NullTradingRecorder`
    폴백(영속화 부재가 세션 전체 실패보다 덜 위험 — 로그 sink 로 사후 재구성
    경로 보존, ADR-0013). `settings`·`dry_run` 은 현재 사용하지 않지만 `notifier`
    팩토리와 시그니처를 맞춰 장래 설정 주입 경로를 열어둔다.

    폴백 catch 를 ADR-0013 결정 7 이 명시한 3종(`StorageError`/`RuntimeError`/
    `OSError`)으로 좁힌다 — 과거 `except Exception` 블록은 `ImportError`·
    `TypeError`·`AttributeError` 같은 프로그래밍 오류까지 `NullTradingRecorder`
    폴백으로 삼켜 10영업일 내내 조용히 무영속화되는 경로를 만들 수 있어
    제거한다(리뷰 C2).
    """
    try:
        return SqliteTradingRecorder(db_path=_TRADING_DB_PATH)
    except (StorageError, RuntimeError, OSError) as e:
        logger.warning(
            "main.recorder_factory 초기화 실패 — NullTradingRecorder 폴백: "
            f"{e.__class__.__name__}: {e}"
        )
        return NullTradingRecorder()


# ---- 런타임 조립 ---------------------------------------------------------


def build_runtime(
    args: argparse.Namespace,
    settings: Settings,
    *,
    kis_client_factory: Callable[[Settings], KisClient] | None = None,
    realtime_store_factory: Callable[[Settings], RealtimeDataStore] | None = None,
    scheduler_factory: Callable[[], BlockingScheduler] | None = None,
    universe_loader: Callable[[Path | None], KospiUniverse] | None = None,
    notifier_factory: Callable[[Settings, bool], Notifier] | None = None,
    recorder_factory: Callable[[Settings, bool], TradingRecorder] | None = None,
    clock: ClockFn | None = None,
) -> Runtime:
    """전략·리스크·브로커·시세·Executor·스케줄러를 조립해 `Runtime` 반환.

    팩토리 주입을 통해 단위 테스트에서 실제 KIS/PyKis/threading 접촉을 0 으로
    만든다. `realtime_store.start()` 는 호출하지 않는다 — `main()` 이 SIGTERM
    핸들러 등록 이후에 호출해 정리 경로를 일원화한다.

    Raises:
        RuntimeError: `settings.has_live_keys is False` (시세 키 미주입) 또는
            유니버스 `tickers` 가 비어있는 경우.
        UniverseLoadError: `universe_loader` 가 yaml 파싱에서 실패한 경우.
        OSError: `kis_client_factory` / `realtime_store_factory` 초기화 중
            발생 가능 (네트워크·토큰 파일 I/O). `main()` 이 exit 3 으로 매핑.
    """
    if not settings.has_live_keys:
        raise RuntimeError(
            "has_live_keys=False — 시세 전용 실전 키 3종"
            "(KIS_LIVE_APP_KEY/APP_SECRET/ACCOUNT_NO) 이 주입되지 않아 "
            "RealtimeDataStore 를 기동할 수 없습니다. .env 에 실전 키를 넣고 "
            "IP 화이트리스트 등록을 마친 뒤 다시 실행하세요."
        )

    load_universe = universe_loader or load_kospi200_universe
    universe = load_universe(args.universe_path)
    if not universe.tickers:
        raise RuntimeError(
            "유니버스 비어있음 — config/universe.yaml 의 tickers 가 0건입니다. "
            "오늘은 매매 중단 판정."
        )

    build_kis = kis_client_factory or KisClient
    build_rt = realtime_store_factory or RealtimeDataStore
    kis_client = build_kis(settings)
    realtime_store = build_rt(settings)

    for ticker in universe.tickers:
        realtime_store.subscribe(ticker)

    order_submitter = _build_order_submitter(args.dry_run, kis_client)
    balance_provider: BalanceProvider = LiveBalanceProvider(kis_client)

    strategy = ORBStrategy(StrategyConfig())
    risk_manager = RiskManager(RiskConfig())
    executor = Executor(
        symbols=universe.tickers,
        strategy=strategy,
        risk_manager=risk_manager,
        bar_source=realtime_store,  # BarSource Protocol 자연 만족
        order_submitter=order_submitter,
        balance_provider=balance_provider,
        clock=clock,
    )

    build_scheduler = scheduler_factory or _default_scheduler_factory
    scheduler = build_scheduler()
    build_notifier = notifier_factory or _default_notifier_factory
    notifier = build_notifier(settings, args.dry_run)
    build_recorder = recorder_factory or _default_recorder_factory
    recorder = build_recorder(settings, args.dry_run)
    runtime = Runtime(
        scheduler=scheduler,
        executor=executor,
        realtime_store=realtime_store,
        kis_client=kis_client,
        args=args,
        risk_manager=risk_manager,
        session_status=SessionStatus(),
        notifier=notifier,
        recorder=recorder,
    )
    _install_jobs(scheduler, runtime, args, clock=clock)
    return runtime


# ---- cron job 등록 -------------------------------------------------------


def _install_jobs(
    scheduler: BlockingScheduler,
    runtime: Runtime,
    args: argparse.Namespace,
    *,
    clock: ClockFn | None = None,
) -> None:
    """4종 cron job 을 `scheduler` 에 등록. 호출 순서는 테스트가 index 로 검증.

    모든 job 은 `day_of_week='mon-fri'`, `timezone='Asia/Seoul'`. 공휴일 자동
    판정은 범위 밖 — 운영자가 프로세스 안 띄우는 방식으로 처리.
    """
    effective_clock: ClockFn = clock or (lambda: datetime.now(KST))
    tz = "Asia/Seoul"

    scheduler.add_job(
        _on_session_start(runtime, args, effective_clock),
        trigger=CronTrigger(day_of_week="mon-fri", hour=9, minute=0, second=0, timezone=tz),
        name="on_session_start",
    )
    scheduler.add_job(
        _on_step(runtime, effective_clock),
        trigger=CronTrigger(day_of_week="mon-fri", hour="9-14", minute="*", second=0, timezone=tz),
        name="on_step",
    )
    scheduler.add_job(
        _on_force_close(runtime, effective_clock),
        trigger=CronTrigger(day_of_week="mon-fri", hour=15, minute=0, second=0, timezone=tz),
        name="on_force_close",
    )
    scheduler.add_job(
        _on_daily_report(runtime, effective_clock),
        trigger=CronTrigger(day_of_week="mon-fri", hour=15, minute=30, second=0, timezone=tz),
        name="on_daily_report",
    )


# ---- 콜백 4종 ------------------------------------------------------------
#
# 모든 콜백은 **예외를 re-raise 하지 않는다** — APScheduler 가 예외를 받으면
# job 이 기본적으로 미스파이어 처리되거나 이후 실행에 영향이 갈 수 있어, 단일
# sweep 실패가 세션을 죽이지 않게 로깅으로만 처리한다. 단, `on_force_close`
# 만 `logger.critical` — 포지션 잔존 운영 리스크라 경보 레벨을 올린다.


def _on_session_start(
    runtime: Runtime,
    args: argparse.Namespace,
    clock: ClockFn,
) -> Callable[[], None]:
    """09:00 — 잔고 조회 후 보수적으로 `min(CLI 자본, 잔고 withdrawable)` 로 세션 시작.

    `withdrawable` (실제 매수 가능 현금) 기준이라 RiskManager 진입 사이징·
    서킷브레이커(`-starting_capital × 2%`) 가 실제 손실과 맞물린다. `total`
    (평가금액 포함) 이면 일일 손실 한도가 구조적으로 느슨해지므로 사용 금지.

    **재기동 복원 경로 (Issue #33, ADR-0014)** — `runtime.recorder` 에 당일
    기록이 있으면 "세션 중간 재기동" 으로 판정하고 `Executor.restore_session`
    을 호출해 오픈 포지션·일일 실현 PnL·진입 횟수·청산 심볼을 복원한다.
    당일 기록이 없으면 기존과 동일하게 `start_session` 신규 개시.

    실패 분기 (withdrawable ≤ 0 또는 예외) 에선 `session_status.started = False`
    로 리셋해 이후 `on_step` 이 silent failure 루프에 빠지지 않고 첫 진입에서
    warning 1회만 남기고 스킵한다 (C1 — `on_step` 의 dedupe 가드와 연결).

    예외 전파 금지 — ADR-0011 결정 5 (콜백 예외로 스케줄러 연속성 파괴 방지).
    """

    def callback() -> None:
        if isinstance(runtime.recorder, NullTradingRecorder):
            # Issue #41 — `_default_recorder_factory` 가 SqliteTradingRecorder 조립
            # 실패 시 남기는 `logger.warning` 은 프로세스 시작 시점에만 남아, 세션
            # 시작 이후에는 recorder 가 폴백 상태인지 DB 가 비어 있는지 구분이
            # 불가능하다. 재기동 복원 경로(Issue #33/ADR-0014) 는 폴백 상태에서
            # `load_open_positions=()` + `has_state=False` → 신규 세션 분기로 빠지며,
            # 실제 KIS 잔고에 포지션이 남아있으면 첫 reconcile mismatch 까지 이벤트
            # 손실이 발생한다. 매일 09:00 callback 진입 시 운영자에게 1회 경보를
            # 방출해 DB 파일·권한 점검을 유도한다. 이후 정상 세션 시작 경로는
            # 그대로 진행 — Null 폴백이라도 신규 세션 시작 자체는 막지 않는다.
            logger.critical(
                "main.session_start.recorder_null — SqliteTradingRecorder 조립 실패 "
                "폴백 상태. 재기동 복원 불가, 신규 세션으로 시작. DB 파일·권한 확인 필요."
            )
            runtime.notifier.notify_error(
                ErrorEvent(
                    stage="session_start.recorder_null",
                    error_class="NullTradingRecorder",
                    message=(
                        "영속화 폴백 상태 — SqliteTradingRecorder 조립 실패. "
                        "재기동 복원 불가, DB 파일·권한 확인 필요."
                    ),
                    timestamp=clock(),
                    severity="critical",
                )
            )
        try:
            balance = runtime.kis_client.get_balance()
            starting_capital = min(int(args.starting_capital), int(balance.withdrawable))
            if starting_capital <= 0:
                logger.error(
                    "main.session_start: 시작 자본이 0 이하입니다 "
                    "(cli={cli}, balance_withdrawable={w}). 오늘은 매매 중단.",
                    cli=args.starting_capital,
                    w=balance.withdrawable,
                )
                runtime.session_status.started = False
                runtime.session_status.fail_logged = False
                runtime.notifier.notify_error(
                    ErrorEvent(
                        stage="session_start",
                        error_class="StartingCapitalError",
                        message=(
                            f"시작 자본 0 이하 — cli={args.starting_capital} "
                            f"withdrawable={balance.withdrawable}"
                        ),
                        timestamp=clock(),
                        severity="error",
                    )
                )
                return
            now = clock()
            today = now.date()

            # Issue #33: 당일 이전 기록이 있으면 재기동 복원 분기.
            # recorder.load_* 는 silent fail 계약(SqliteTradingRecorder 는
            # DB 예외를 빈 결과로 흡수) 이므로 여기서 별도 except 는 불필요.
            open_positions = runtime.recorder.load_open_positions(today)
            daily_snapshot = runtime.recorder.load_daily_pnl(today)
            is_restart = bool(open_positions) or daily_snapshot.has_state

            if is_restart:
                logger.warning(
                    "main.session_start.restart 감지 — 당일 이전 세션 상태 복원. "
                    "open={op} entries_today={et} realized_pnl={pnl} closed={cl}",
                    op=len(open_positions),
                    et=daily_snapshot.entries_today,
                    pnl=daily_snapshot.realized_pnl_krw,
                    cl=len(daily_snapshot.closed_symbols),
                )
                runtime.executor.restore_session(
                    today,
                    starting_capital,
                    open_positions=open_positions,
                    closed_symbols=daily_snapshot.closed_symbols,
                    entries_today=daily_snapshot.entries_today,
                    daily_realized_pnl_krw=daily_snapshot.realized_pnl_krw,
                )
            else:
                runtime.executor.start_session(today, starting_capital)

            runtime.session_status.started = True
            runtime.session_status.fail_logged = False
            logger.info(
                "main.session_start date={d} capital={c} restart={r}",
                d=today,
                c=starting_capital,
                r=is_restart,
            )
        except Exception as e:  # noqa: BLE001 — ADR-0011 결정 5 (스케줄러 연속성)
            logger.exception(f"main.session_start 실패: {e.__class__.__name__}: {e}")
            runtime.session_status.started = False
            runtime.session_status.fail_logged = False
            runtime.notifier.notify_error(
                ErrorEvent(
                    stage="session_start",
                    error_class=e.__class__.__name__,
                    message=str(e),
                    timestamp=clock(),
                    severity="error",
                )
            )

    return callback


def _on_step(runtime: Runtime, clock: ClockFn) -> Callable[[], None]:
    """매분 00s — `Executor.step(now)` 로 reconcile + 분봉 처리 + 시각 트리거.

    세션 미시작(`on_session_start` 실패) 상태면 `executor.step` 을 건너뛰고
    `logger.warning` 을 **첫 호출에만 1회** 남긴다 (C1 silent failure 루프 차단).
    dedupe 플래그는 `session_status.fail_logged` 에 기록, 다음 영업일
    `on_session_start` 성공 시 자동 해제된다.

    예외 전파 금지 — ADR-0011 결정 5.
    """

    def callback() -> None:
        if runtime.session_status.started is False:
            if not runtime.session_status.fail_logged:
                logger.warning(
                    "main.step.skip: 세션 미시작 — 오늘은 매매 중단 "
                    "(on_session_start 실패). 다음 영업일 09:00 까지 스킵 유지."
                )
                runtime.session_status.fail_logged = True
            return
        try:
            now = clock()
            report = runtime.executor.step(now)
            logger.info(
                "main.step processed={p} orders={o} halted={h} mismatch={m}",
                p=report.processed_bars,
                o=report.orders_submitted,
                h=report.halted,
                m=len(report.reconcile.mismatch_symbols),
            )
            # I1: record 를 notify 앞으로. notifier 가 (설계상 silent fail 이지만
            # 장래 확장 또는 외부 예외로) 전파하면 같은 이벤트의 DB 기록이 누락된다.
            # `_on_daily_report` 는 이미 record 선행 — 콜백 전반을 한 방향으로 통일.
            for entry in report.entry_events:
                runtime.recorder.record_entry(entry)
                runtime.notifier.notify_entry(entry)
            for exit_ev in report.exit_events:
                runtime.recorder.record_exit(exit_ev)
                runtime.notifier.notify_exit(exit_ev)
            if report.reconcile.mismatch_symbols:
                runtime.notifier.notify_error(
                    ErrorEvent(
                        stage="reconcile",
                        error_class="ReconcileMismatch",
                        message=(
                            "잔고↔RiskManager 포지션 불일치 — 운영자 수동 정리 필요. "
                            f"symbols={list(report.reconcile.mismatch_symbols)}"
                        ),
                        timestamp=clock(),
                        severity="critical",
                    )
                )
        except Exception as e:  # noqa: BLE001 — ADR-0011 결정 5 (스케줄러 연속성)
            logger.exception(f"main.step 실패: {e.__class__.__name__}: {e}")
            runtime.notifier.notify_error(
                ErrorEvent(
                    stage="step",
                    error_class=e.__class__.__name__,
                    message=str(e),
                    timestamp=clock(),
                    severity="error",
                )
            )

    return callback


def _on_force_close(runtime: Runtime, clock: ClockFn) -> Callable[[], None]:
    """15:00 — 잔존 long 포지션 강제청산.

    실패 시 `logger.critical` 로 severity 격상 (포지션 잔존 = 운영 리스크).
    예외 전파 금지 — ADR-0011 결정 5.
    """

    def callback() -> None:
        try:
            now = clock()
            report = runtime.executor.force_close_all(now)
            logger.info(
                "main.force_close orders={o} halted={h}",
                o=report.orders_submitted,
                h=report.halted,
            )
            # I1: record 선행. _on_step 과 동일 순서로 통일.
            for exit_ev in report.exit_events:
                runtime.recorder.record_exit(exit_ev)
                runtime.notifier.notify_exit(exit_ev)
        except Exception as e:  # noqa: BLE001 — ADR-0011 결정 5 (스케줄러 연속성)
            # I3: `force_close_all` 이 `_process_signals` 중간에 예외를 던져도
            # 그 시점까지 Executor 내부 `_sweep_exit_events` 에 누적된 부분
            # 청산 이벤트는 유지된다. 이를 `last_sweep_exit_events` 로 읽어
            # DB 기록 누락을 막는다 — `daily_pnl.realized_pnl_krw` 와 실 KIS
            # 손익 괴리를 최소화.
            try:
                partial_exits = runtime.executor.last_sweep_exit_events
            except Exception as snap_err:  # noqa: BLE001 — 스냅샷 접근 실패도 삼킨다
                logger.warning(
                    "main.force_close: 부분 exit_events 스냅샷 실패 (무시): "
                    f"{snap_err.__class__.__name__}: {snap_err}"
                )
                partial_exits = ()
            for exit_ev in partial_exits:
                runtime.recorder.record_exit(exit_ev)
                runtime.notifier.notify_exit(exit_ev)
            # 포지션 잔존 운영 리스크 — 텔레그램 + logger.critical 이중 경보.
            logger.critical(
                f"main.force_close 실패 — 포지션 잔존 위험: {e.__class__.__name__}: {e}"
            )
            runtime.notifier.notify_error(
                ErrorEvent(
                    stage="force_close",
                    error_class=e.__class__.__name__,
                    message=f"강제청산 실패 — 포지션 잔존 위험: {e}",
                    timestamp=clock(),
                    severity="critical",
                )
            )

    return callback


def _safe_pct(pnl: Any, starting: Any) -> float | None:
    """pnl / starting × 100.0. 실패 시 None.

    계산 실패와 텔레그램 발송 실패를 직교화한다. `RiskManager` 의
    `starting_capital_krw`/`daily_realized_pnl_krw` 타입이 향후 Decimal 등으로
    드리프트하더라도 `float()` 변환 + 명시 except 로 `TypeError`/`ValueError`/
    `ArithmeticError` 를 흡수해 None 을 반환한다. 발송 경로(`notify_daily_summary`)
    는 pct=None 을 "n/a" 로 출력하므로 계산 실패가 일일 요약 발송 자체를
    막지 않는다 (Issue #25, PR #18 리뷰 후속 I3).
    """
    try:
        if starting is None:
            return None
        s = float(starting)
        if s <= 0:
            return None
        return (float(pnl) / s) * 100.0
    except (TypeError, ValueError, ArithmeticError):
        return None


def _on_daily_report(runtime: Runtime, clock: ClockFn) -> Callable[[], None]:
    """15:30 — 당일 요약 로그 (`runtime.risk_manager` 공개 프로퍼티 사용).

    Executor private 속성(`_risk_manager`) 우회 경로를 제거하고 `Runtime` 에
    직접 주입된 `RiskManager` 참조를 사용한다. 후속 PR(`monitor/notifier.py`)
    이 같은 경로를 공유하도록 공개 경계를 확정한다.

    예외 전파 금지 — ADR-0011 결정 5.
    """

    def callback() -> None:
        try:
            now = clock()
            halted = runtime.executor.is_halted
            rm = runtime.risk_manager
            pnl = rm.daily_realized_pnl_krw
            entries = rm.entries_today
            active = len(rm.active_positions)
            starting = rm.starting_capital_krw
            pct = _safe_pct(pnl, starting)
            last_rec = runtime.executor.last_reconcile
            mismatch = last_rec.mismatch_symbols if last_rec is not None else ()
            logger.info(
                "main.daily_report date={d} realized_pnl={pnl} entries={e} active={a} halted={h}",
                d=now.date(),
                pnl=pnl,
                e=entries,
                a=active,
                h=halted,
            )
            summary = DailySummary(
                session_date=now.date(),
                starting_capital_krw=starting,
                realized_pnl_krw=pnl,
                realized_pnl_pct=pct,
                entries_today=entries,
                halted=halted,
                mismatch_symbols=mismatch,
            )
            # SQLite 원장 기록을 먼저 시도 — DB 기록 실패가 알림을 막지 않도록
            # recorder 는 silent fail 정책을 유지한다 (ADR-0013).
            runtime.recorder.record_daily_summary(summary)
            runtime.notifier.notify_daily_summary(summary)
        except Exception as e:  # noqa: BLE001 — ADR-0011 결정 5 (스케줄러 연속성)
            logger.exception(f"main.daily_report 실패: {e.__class__.__name__}: {e}")
            runtime.notifier.notify_error(
                ErrorEvent(
                    stage="daily_report",
                    error_class=e.__class__.__name__,
                    message=str(e),
                    timestamp=clock(),
                    severity="error",
                )
            )

    return callback


# ---- 로깅·시그널 ---------------------------------------------------------


def _configure_logging(log_dir: Path) -> None:
    """loguru sink 를 stderr(INFO) + 일 단위 회전 파일(DEBUG) 로 설정.

    Raises:
        OSError: `log_dir` 생성 실패 (디스크 권한·용량 등). `main()` 이 잡아서
            exit 3 으로 매핑.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        format=(
            "<level>{level: <8}</level> | {time:YYYY-MM-DD HH:mm:ss} | "
            "{name}:{function}:{line} - {message}"
        ),
    )
    logger.add(
        log_dir / "stock-agent-{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="00:00",
        retention="14 days",
        encoding="utf-8",
    )


def _graceful_shutdown(runtime: Runtime, signum: int, frame: Any) -> None:
    """SIGINT/SIGTERM 핸들러 — 스케줄러·시세·KIS 리소스를 순서대로 정리.

    **재진입 방어 (I4)**: 진입 즉시 SIGINT/SIGTERM 을 `SIG_DFL` 로 교체한다.
    2차 시그널 도달 시 Python 기본 동작(즉시 종료)으로 넘겨, 정리 중 데몬
    스레드 락 경합을 회피 — 포지션 잔존보다 락 경합이 더 위험.

    각 close 는 예외를 삼키고 warning 로그만 — 한 단계 실패가 이후 정리를
    막지 않게. `main()` 의 finally 블록과 중복 호출돼도 `close()` 는 멱등
    (broker·realtime 모듈의 `_closed` 플래그로 보장).
    """
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    logger.warning(f"main.graceful_shutdown signum={signum} — 리소스 정리 시작")
    try:
        runtime.scheduler.shutdown(wait=False)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"scheduler.shutdown 중 예외 (무시): {e!r}")
    try:
        runtime.realtime_store.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"realtime_store.close 중 예외 (무시): {e!r}")
    try:
        runtime.kis_client.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"kis_client.close 중 예외 (무시): {e!r}")
    try:
        runtime.recorder.close()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"recorder.close 중 예외 (무시): {e!r}")


# ---- 진입점 --------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI 엔트리포인트.

    흐름:
        1. argparse → args
        2. args.starting_capital ≤ 0 이면 exit 2 (build_runtime 전에 차단)
        3. _configure_logging — OSError 시 exit 3
        4. get_settings — 실패 시 exit 2 (OSError 는 3)
        5. build_runtime — RuntimeError/UniverseLoadError 시 exit 2, OSError 시 3
        6. SIGINT/SIGTERM 핸들러 등록
        7. realtime_store.start() → scheduler.start() (blocking)
        8. KeyboardInterrupt 는 정상 종료 경로 (exit 0)
        9. finally: realtime_store.close() + kis_client.close()
    """
    args = _parse_args(argv)

    if args.starting_capital <= 0:
        logger.error(f"--starting-capital 은 양수여야 합니다 (got={args.starting_capital})")
        return EXIT_INPUT_ERROR

    try:
        _configure_logging(args.log_dir)
    except OSError as e:
        logger.exception(f"로그 디렉토리 초기화 실패: {e}")
        return EXIT_IO_ERROR

    try:
        settings = get_settings()
    except OSError as e:
        logger.exception(f"설정 로드 I/O 오류: {e}")
        return EXIT_IO_ERROR
    except (ValidationError, RuntimeError) as e:
        # ValidationError: pydantic 설정 검증 실패. RuntimeError: 프로젝트 가드레일
        # (키 원점 불일치·live 슬롯 부분 주입 등) 명시 전파 (broker/config 기조).
        # ImportError 같은 프로그래밍 오류는 catch 하지 않고 전파시켜 traceback 으로
        # 실패 — generic except 금지 기조(I3).
        logger.error(f"설정 로드 실패: {e.__class__.__name__}: {e}")
        return EXIT_INPUT_ERROR

    try:
        runtime = build_runtime(args, settings)
    except UniverseLoadError as e:
        logger.error(f"유니버스 로드 실패: {e}")
        return EXIT_INPUT_ERROR
    except RuntimeError as e:
        logger.error(f"런타임 구성 오류: {e}")
        return EXIT_INPUT_ERROR
    except OSError as e:
        logger.exception(f"런타임 I/O 오류: {e}")
        return EXIT_IO_ERROR

    signal.signal(signal.SIGINT, partial(_graceful_shutdown, runtime))
    signal.signal(signal.SIGTERM, partial(_graceful_shutdown, runtime))

    exit_code = EXIT_OK
    try:
        runtime.realtime_store.start()
        try:
            runtime.scheduler.start()
        except KeyboardInterrupt:
            logger.info("main: KeyboardInterrupt — 정상 종료 경로로 처리")
            exit_code = EXIT_OK
        except SystemExit:
            exit_code = EXIT_OK
        except Exception as e:  # noqa: BLE001
            logger.exception(f"스케줄러 실행 중 예외: {e.__class__.__name__}: {e}")
            exit_code = EXIT_UNEXPECTED
    finally:
        try:
            runtime.realtime_store.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"realtime_store.close 중 예외 (finally): {e!r}")
        try:
            runtime.kis_client.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"kis_client.close 중 예외 (finally): {e!r}")
        try:
            runtime.recorder.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"recorder.close 중 예외 (finally): {e!r}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
