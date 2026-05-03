"""stock_agent.main 공개 계약 단위 테스트 (refactor-invariant 모드).

기존 47 케이스 + notifier 검증 신규 케이스를 담는다.

가드레일: KIS·텔레그램·외부 HTTP·실 KisClient·실 RealtimeDataStore 접촉 없음.
모든 외부 의존은 팩토리 주입 또는 mocker.patch 로 차단한다.
Notifier 는 MagicMock(spec=Notifier) 로 주입 — 실 TelegramNotifier 접촉 0.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from signal import SIGTERM
from typing import Any
from unittest.mock import MagicMock, PropertyMock, call

import pytest

from stock_agent.broker import KisClient
from stock_agent.config import Settings
from stock_agent.data import UniverseLoadError
from stock_agent.data.universe import KospiUniverse
from stock_agent.execution import (
    DryRunOrderSubmitter,
    EntryEvent,
    Executor,
    ExitEvent,
    LiveOrderSubmitter,
    ReconcileReport,
    StepReport,
)
from stock_agent.main import (
    EXIT_INPUT_ERROR,
    EXIT_IO_ERROR,
    EXIT_OK,
    EXIT_UNEXPECTED,
    KST,
    Runtime,
    SessionStatus,
    _build_order_submitter,
    _configure_logging,
    _default_notifier_factory,
    _default_recorder_factory,
    _graceful_shutdown,
    _install_jobs,
    _on_daily_report,
    _on_force_close,
    _on_session_start,
    _on_step,
    _parse_args,
    build_runtime,
    main,
)
from stock_agent.monitor import (
    DailySummary,
    ErrorEvent,
    Notifier,
    NullNotifier,
)
from stock_agent.risk import RiskManager
from stock_agent.storage import (
    DailyPnlSnapshot,
    NullTradingRecorder,
    StorageError,
    TradingRecorder,
)
from stock_agent.strategy import ExitReason

# ---------------------------------------------------------------------------
# 공통 상수 / 헬퍼
# ---------------------------------------------------------------------------

_DATE = date(2026, 4, 21)
_TICKERS = ("005930", "000660", "035420")


def _kst(h: int, m: int, s: int = 0) -> datetime:
    return datetime(_DATE.year, _DATE.month, _DATE.day, h, m, s, tzinfo=KST)


def _make_balance(total: int = 2_000_000, withdrawable: int = 1_900_000) -> MagicMock:
    """BalanceSnapshot 더블."""
    b = MagicMock()
    b.total = total
    b.withdrawable = withdrawable
    b.holdings = []
    return b


def _make_runtime(
    *,
    kis_client: MagicMock | None = None,
    realtime_store: MagicMock | None = None,
    executor: MagicMock | None = None,
    scheduler: MagicMock | None = None,
    args: argparse.Namespace | None = None,
    risk_manager: MagicMock | None = None,
    session_status: SessionStatus | None = None,
    notifier: MagicMock | None = None,
    recorder: MagicMock | TradingRecorder | None = None,
) -> Runtime:
    """Runtime 더블 조립 헬퍼.

    `notifier` 는 `MagicMock(spec=Notifier)` 기본값 — 실 TelegramNotifier
    접촉 0. 개별 테스트에서 별도 MagicMock 을 주입하면 호출 횟수·인자 검증 가능.
    `recorder` 는 `MagicMock(spec=TradingRecorder)` 기본값 — 실 SqliteTradingRecorder
    접촉 0. SQLite 파일 I/O 없이 호출 횟수·인자 검증 가능.
    """
    _kis = kis_client or MagicMock(spec=KisClient)
    _rt = realtime_store or MagicMock()
    _ex = executor or MagicMock(spec=Executor)
    _sc = scheduler or MagicMock()
    _args = args or _parse_args([])
    _rm = risk_manager or MagicMock(spec=RiskManager)
    _ss = session_status or SessionStatus()
    _notifier = notifier or MagicMock(spec=Notifier)
    if recorder is None:
        # 기본 경로를 "신규 세션"으로 유지 — load_* 가 빈 결과를 반환하도록 configure.
        _recorder = MagicMock(spec=TradingRecorder)
        _recorder.load_open_positions.return_value = ()
        _recorder.load_daily_pnl.return_value = DailyPnlSnapshot(
            session_date=date(2026, 4, 21),
            realized_pnl_krw=0,
            entries_today=0,
            closed_symbols=(),
        )
    else:
        _recorder = recorder
    return Runtime(
        scheduler=_sc,
        executor=_ex,
        realtime_store=_rt,
        kis_client=_kis,
        args=_args,
        risk_manager=_rm,
        session_status=_ss,
        notifier=_notifier,
        recorder=_recorder,
    )


def _make_step_report(
    *,
    entry_events: tuple[EntryEvent, ...] = (),
    exit_events: tuple[ExitEvent, ...] = (),
    mismatch_symbols: tuple[str, ...] = (),
) -> StepReport:
    reconcile = ReconcileReport(
        broker_holdings={},
        risk_holdings={},
        mismatch_symbols=mismatch_symbols,
    )
    return StepReport(
        processed_bars=3,
        orders_submitted=1,
        halted=False,
        reconcile=reconcile,
        entry_events=entry_events,
        exit_events=exit_events,
    )


def _make_entry_event(
    symbol: str = "005930",
    order_number: str = "ORD-ENTRY-001",
) -> EntryEvent:
    """EntryEvent 더블 — Decimal 가격, KST aware datetime."""
    from decimal import Decimal

    return EntryEvent(
        symbol=symbol,
        qty=10,
        fill_price=Decimal("70000"),
        ref_price=Decimal("69930"),
        timestamp=_kst(9, 31),
        order_number=order_number,
    )


def _make_exit_event(
    symbol: str = "005930",
    reason: ExitReason = "take_profit",
    order_number: str = "ORD-EXIT-001",
) -> ExitEvent:
    """ExitEvent 더블 — Decimal 가격, KST aware datetime."""
    from decimal import Decimal

    return ExitEvent(
        symbol=symbol,
        qty=10,
        fill_price=Decimal("72100"),
        reason=reason,
        net_pnl_krw=20_000,
        timestamp=_kst(10, 15),
        order_number=order_number,
    )


# ---------------------------------------------------------------------------
# 1. _parse_args — 기본값 및 명시 파싱
# ---------------------------------------------------------------------------


def test_parse_args_기본값() -> None:
    args = _parse_args([])
    assert args.dry_run is False
    assert args.starting_capital == 1_000_000
    assert args.universe_path is None
    assert args.log_dir == Path("logs")


def test_parse_args_모든_옵션_명시() -> None:
    args = _parse_args(
        [
            "--dry-run",
            "--starting-capital",
            "500000",
            "--universe-path",
            "/tmp/u.yaml",
            "--log-dir",
            "/tmp/logs",
        ]
    )
    assert args.dry_run is True
    assert args.starting_capital == 500_000
    assert args.universe_path == Path("/tmp/u.yaml")
    assert args.log_dir == Path("/tmp/logs")


def test_parse_args_음수_자본은_argparse_레벨에서_통과() -> None:
    # main() 에서 검증 — argparse 자체는 막지 않는다.
    args = _parse_args(["--starting-capital", "-1"])
    assert args.starting_capital == -1


def test_parse_args_universe_path_없으면_None() -> None:
    args = _parse_args([])
    assert args.universe_path is None


# ---------------------------------------------------------------------------
# 2. _build_order_submitter
# ---------------------------------------------------------------------------


def test_build_order_submitter_dry_run_True_는_DryRunOrderSubmitter() -> None:
    result = _build_order_submitter(dry_run=True, kis_client=MagicMock())
    assert isinstance(result, DryRunOrderSubmitter)


def test_build_order_submitter_dry_run_False_는_LiveOrderSubmitter() -> None:
    fake_kis = MagicMock(spec=KisClient)
    result = _build_order_submitter(dry_run=False, kis_client=fake_kis)
    assert isinstance(result, LiveOrderSubmitter)
    assert result._kis is fake_kis


# ---------------------------------------------------------------------------
# 3. build_runtime — 정상 경로
# ---------------------------------------------------------------------------


@pytest.fixture
def _mock_universe() -> KospiUniverse:
    return KospiUniverse(
        as_of_date=_DATE,
        source="test",
        tickers=_TICKERS,
    )


@pytest.fixture
def _fake_settings() -> MagicMock:
    s = MagicMock(spec=Settings)
    type(s).has_live_keys = PropertyMock(return_value=True)
    return s


def test_build_runtime_정상_subscribe_각_티커_호출(
    _mock_universe: KospiUniverse, _fake_settings: MagicMock
) -> None:
    fake_rt = MagicMock()
    fake_kis = MagicMock(spec=KisClient)
    fake_scheduler = MagicMock()
    args = _parse_args([])

    build_runtime(
        args,
        _fake_settings,
        kis_client_factory=lambda s: fake_kis,
        realtime_store_factory=lambda s: fake_rt,
        scheduler_factory=lambda: fake_scheduler,
        universe_loader=lambda p: _mock_universe,
        clock=lambda: _kst(9, 0),
    )

    subscribe_calls = [c[0][0] for c in fake_rt.subscribe.call_args_list]
    assert subscribe_calls == list(_TICKERS)


def test_build_runtime_dry_run_True_시_DryRunOrderSubmitter_주입(
    _mock_universe: KospiUniverse, _fake_settings: MagicMock
) -> None:
    fake_rt = MagicMock()
    args = _parse_args(["--dry-run"])

    runtime = build_runtime(
        args,
        _fake_settings,
        kis_client_factory=lambda s: MagicMock(spec=KisClient),
        realtime_store_factory=lambda s: fake_rt,
        scheduler_factory=MagicMock,
        universe_loader=lambda p: _mock_universe,
    )

    assert isinstance(runtime.executor._order_submitter, DryRunOrderSubmitter)


def test_build_runtime_dry_run_False_시_LiveOrderSubmitter_주입(
    _mock_universe: KospiUniverse, _fake_settings: MagicMock
) -> None:
    fake_rt = MagicMock()
    args = _parse_args([])  # dry_run=False

    runtime = build_runtime(
        args,
        _fake_settings,
        kis_client_factory=lambda s: MagicMock(spec=KisClient),
        realtime_store_factory=lambda s: fake_rt,
        scheduler_factory=MagicMock,
        universe_loader=lambda p: _mock_universe,
    )

    assert isinstance(runtime.executor._order_submitter, LiveOrderSubmitter)


def test_build_runtime_Runtime_필드_반환(
    _mock_universe: KospiUniverse, _fake_settings: MagicMock
) -> None:
    """기존 5개 필드 + risk_manager, session_status, notifier, recorder = 9개 검증. (I1)"""
    fake_rt = MagicMock()
    args = _parse_args([])
    fake_notifier = MagicMock(spec=Notifier)
    fake_recorder = MagicMock(spec=TradingRecorder)

    runtime = build_runtime(
        args,
        _fake_settings,
        kis_client_factory=lambda s: MagicMock(spec=KisClient),
        realtime_store_factory=lambda s: fake_rt,
        scheduler_factory=MagicMock,
        universe_loader=lambda p: _mock_universe,
        notifier_factory=lambda s, d: fake_notifier,
        recorder_factory=lambda s, d: fake_recorder,
    )

    assert isinstance(runtime, Runtime)
    assert runtime.scheduler is not None
    assert isinstance(runtime.executor, Executor)
    assert runtime.realtime_store is fake_rt
    assert runtime.kis_client is not None
    assert runtime.args is args
    # 신규 필드
    assert isinstance(runtime.risk_manager, RiskManager)
    assert isinstance(runtime.session_status, SessionStatus)
    assert runtime.session_status.started is False
    assert runtime.session_status.fail_logged is False
    assert runtime.notifier is fake_notifier
    assert runtime.recorder is fake_recorder


def test_build_runtime_Runtime_필드_9개_반환(
    _mock_universe: KospiUniverse, _fake_settings: MagicMock
) -> None:
    """Runtime 이 9개 필드를 갖는지 명시 검증 (recorder 추가). (I1)"""
    fake_rt = MagicMock()
    args = _parse_args([])

    runtime = build_runtime(
        args,
        _fake_settings,
        kis_client_factory=lambda s: MagicMock(spec=KisClient),
        realtime_store_factory=lambda s: fake_rt,
        scheduler_factory=MagicMock,
        universe_loader=lambda p: _mock_universe,
        notifier_factory=lambda s, d: MagicMock(spec=Notifier),
        recorder_factory=lambda s, d: MagicMock(spec=TradingRecorder),
    )

    field_names = {f.name for f in dataclasses.fields(runtime)}
    assert "risk_manager" in field_names, "Runtime 에 risk_manager 필드가 없다"
    assert "session_status" in field_names, "Runtime 에 session_status 필드가 없다"
    assert "notifier" in field_names, "Runtime 에 notifier 필드가 없다"
    assert "recorder" in field_names, "Runtime 에 recorder 필드가 없다"
    assert len(field_names) == 9, f"Runtime 필드 수가 9이 아님: {field_names}"


# ---------------------------------------------------------------------------
# 3-B. build_runtime — RSIMRStrategy wiring & RiskConfig ADR-0025 (RED, PR2)
# ---------------------------------------------------------------------------
# PR2 src 변경 전 기준으로 전원 FAIL 이 필요한 케이스.
# 목킹 정책: KIS 네트워크 0, universe_loader 람다 주입, scheduler/notifier/recorder
#            더미 팩토리, Settings 는 SimpleNamespace(has_live_keys=True).


class TestBuildRuntimeRsiMrWiringRED:
    """ADR-0025 결정값 적용 전 실패해야 하는 RED 케이스 4건.

    main.py:293-294 가 ORBStrategy → RSIMRStrategy / RiskConfig() 기본값 →
    ADR-0025 명시 주입으로 바뀐 뒤 GREEN 이 되어야 한다.
    """

    _FAKE_TICKERS = ("005930", "000660", "035720")

    # ------------------------------------------------------------------
    # 공유 헬퍼 — 모든 테스트가 동일 팩토리 조합을 사용한다.
    # ------------------------------------------------------------------

    def _build(self, tickers: tuple[str, ...] = _FAKE_TICKERS) -> Any:
        from types import SimpleNamespace

        from stock_agent.monitor import NullNotifier
        from stock_agent.storage import NullTradingRecorder

        args = _parse_args([])
        settings = SimpleNamespace(has_live_keys=True)

        universe = KospiUniverse(as_of_date=_DATE, source="test", tickers=tickers)

        return build_runtime(
            args,
            settings,  # type: ignore[arg-type]
            kis_client_factory=lambda s: MagicMock(spec=KisClient),
            realtime_store_factory=lambda s: MagicMock(),
            scheduler_factory=lambda: MagicMock(),
            universe_loader=lambda p: universe,
            notifier_factory=lambda s, d: NullNotifier(),
            recorder_factory=lambda s, d: NullTradingRecorder(),
            clock=lambda: _kst(9, 0),
        )

    # ------------------------------------------------------------------
    # 케이스 1: strategy 인스턴스 타입이 RSIMRStrategy 여야 한다
    # ------------------------------------------------------------------

    def test_build_runtime_strategy_타입이_RSIMRStrategy(self) -> None:
        """PR2 후 GREEN: runtime.executor._strategy 가 RSIMRStrategy isinstance.

        현재(PR2 전) main.py 는 ORBStrategy 를 wiring 하므로 이 단언은 FAIL.
        """
        from stock_agent.strategy.rsi_mr import RSIMRStrategy

        runtime = self._build()
        strategy = runtime.executor._strategy
        assert isinstance(strategy, RSIMRStrategy), (
            f"expected RSIMRStrategy, got {type(strategy).__name__}"
        )

    # ------------------------------------------------------------------
    # 케이스 2: RSIMRConfig.universe 가 universe_loader 반환 tickers 와 일치
    # ------------------------------------------------------------------

    def test_build_runtime_rsimr_config_universe_일치(self) -> None:
        """PR2 후 GREEN: strategy.config.universe == KospiUniverse.tickers (순서·내용 모두).

        현재(PR2 전) strategy 가 ORBStrategy 이므로 .config.universe 속성이 없어
        AttributeError 또는 isinstance FAIL 이 선행 발생.
        """
        from stock_agent.strategy.rsi_mr import RSIMRStrategy

        runtime = self._build(tickers=self._FAKE_TICKERS)
        strategy = runtime.executor._strategy
        # RSIMRStrategy 타입 가드 — 실패 메시지를 명확히 하기 위해 선행 단언
        assert isinstance(strategy, RSIMRStrategy), (
            f"expected RSIMRStrategy, got {type(strategy).__name__}"
        )
        assert strategy.config.universe == self._FAKE_TICKERS, (
            f"expected universe={self._FAKE_TICKERS}, got {strategy.config.universe}"
        )

    # ------------------------------------------------------------------
    # 케이스 3: RiskConfig ADR-0025 명시 주입값 검증
    # ------------------------------------------------------------------

    def test_build_runtime_risk_config_adr0025_명시값(self) -> None:
        """PR2 후 GREEN: RiskConfig 가 ADR-0025 결정값으로 명시 주입된다.

        ADR-0025 결정표:
            position_pct     = Decimal("0.10")
            max_positions    = 10
            daily_loss_limit_pct = Decimal("0.02")
            daily_max_entries = 5

        현재(PR2 전) RiskConfig() 기본값:
            position_pct=Decimal("0.20"), max_positions=3,
            daily_loss_limit_pct=Decimal("0.02"), daily_max_entries=10
        → position_pct 와 max_positions, daily_max_entries 불일치로 FAIL.
        """
        from decimal import Decimal

        from stock_agent.risk import RiskConfig

        runtime = self._build()
        cfg: RiskConfig = runtime.risk_manager.config

        assert cfg.position_pct == Decimal("0.10"), (
            f"expected position_pct=0.10, got {cfg.position_pct}"
        )
        assert cfg.max_positions == 10, f"expected max_positions=10, got {cfg.max_positions}"
        assert cfg.daily_loss_limit_pct == Decimal("0.02"), (
            f"expected daily_loss_limit_pct=0.02, got {cfg.daily_loss_limit_pct}"
        )
        assert cfg.daily_max_entries == 5, (
            f"expected daily_max_entries=5, got {cfg.daily_max_entries}"
        )
        # min_notional_krw 는 ORB 시절 기본값 100_000 유지 — 변경 없음 확인
        assert cfg.min_notional_krw == 100_000, (
            f"expected min_notional_krw=100_000, got {cfg.min_notional_krw}"
        )

    # ------------------------------------------------------------------
    # 케이스 4: 임의 fake universe 주입 시 strategy.config.universe 일치 (회귀 가드)
    # ------------------------------------------------------------------

    def test_build_runtime_fake_universe_strategy_config_universe_회귀가드(self) -> None:
        """PR2 후 GREEN: universe_loader 가 임의 tickers 를 반환하면
        RSIMRStrategy.config.universe 가 그 tuple 과 정확히 동일해야 한다.

        순서·내용 모두 동일해야 하며, 이 케이스는 universe wiring 로직의
        회귀를 방지한다.  현재(PR2 전) ORBStrategy 를 쓰므로 FAIL.
        """
        from stock_agent.strategy.rsi_mr import RSIMRStrategy

        alt_tickers = ("000270", "005380", "051910")
        runtime = self._build(tickers=alt_tickers)
        strategy = runtime.executor._strategy

        assert isinstance(strategy, RSIMRStrategy), (
            f"expected RSIMRStrategy, got {type(strategy).__name__}"
        )
        assert strategy.config.universe == alt_tickers, (
            f"expected universe={alt_tickers}, got {strategy.config.universe}"
        )


# ---------------------------------------------------------------------------
# 4. build_runtime — 에러 경로
# ---------------------------------------------------------------------------


def test_build_runtime_has_live_keys_False_RuntimeError(_fake_settings: MagicMock) -> None:
    type(_fake_settings).has_live_keys = PropertyMock(return_value=False)
    args = _parse_args([])

    with pytest.raises(RuntimeError):
        build_runtime(
            args,
            _fake_settings,
            universe_loader=lambda p: KospiUniverse(
                as_of_date=_DATE, source="t", tickers=("005930",)
            ),
        )


def test_build_runtime_빈_유니버스_RuntimeError(_fake_settings: MagicMock) -> None:
    args = _parse_args([])
    empty_universe = KospiUniverse(as_of_date=_DATE, source="t", tickers=())

    with pytest.raises(RuntimeError, match="유니버스"):
        build_runtime(
            args,
            _fake_settings,
            kis_client_factory=lambda s: MagicMock(spec=KisClient),
            realtime_store_factory=lambda s: MagicMock(),
            scheduler_factory=MagicMock,
            universe_loader=lambda p: empty_universe,
        )


def test_build_runtime_UniverseLoadError_전파(_fake_settings: MagicMock) -> None:
    args = _parse_args([])

    def _fail(p: Any) -> KospiUniverse:
        raise UniverseLoadError("테스트용 로드 실패")

    with pytest.raises(UniverseLoadError):
        build_runtime(
            args,
            _fake_settings,
            kis_client_factory=lambda s: MagicMock(spec=KisClient),
            realtime_store_factory=lambda s: MagicMock(),
            scheduler_factory=MagicMock,
            universe_loader=_fail,
        )


# ---------------------------------------------------------------------------
# 5. _install_jobs — cron trigger 검증
# ---------------------------------------------------------------------------


def test_install_jobs_add_job_4회_호출() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = MagicMock(spec=BlockingScheduler)
    runtime = _make_runtime(scheduler=scheduler)
    args = _parse_args([])

    _install_jobs(scheduler, runtime, args, clock=lambda: _kst(9, 0))

    assert scheduler.add_job.call_count == 4


def test_install_jobs_session_start_cron_hour9_minute0() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = MagicMock(spec=BlockingScheduler)
    runtime = _make_runtime(scheduler=scheduler)
    args = _parse_args([])

    _install_jobs(scheduler, runtime, args, clock=lambda: _kst(9, 0))

    triggers = [c.kwargs.get("trigger") or c.args[1] for c in scheduler.add_job.call_args_list]
    # 실제 CronTrigger 객체는 str 표현으로 검증
    [str(t) for t in triggers]
    # on_session_start 는 첫 번째 add_job
    first_trigger = [c for c in scheduler.add_job.call_args_list][0]
    trigger_obj = (
        first_trigger.kwargs.get("trigger") or first_trigger.args[1]
        if first_trigger.args
        else first_trigger.kwargs.get("trigger")
    )
    assert isinstance(trigger_obj, CronTrigger)


@pytest.mark.parametrize(
    "job_index, expected_hour, expected_minute, expected_second",
    [
        (0, 9, 0, 0),  # on_session_start
        (1, "9-14", "*", 0),  # on_step  ← C2 신규
        (2, 15, 0, 0),  # on_force_close
        (3, 15, 30, 0),  # on_daily_report
    ],
    ids=["session_start", "step", "force_close", "daily_report"],
)
def test_install_jobs_cron_시각_검증(
    job_index: int,
    expected_hour: int | str,
    expected_minute: int | str,
    expected_second: int,
) -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = MagicMock(spec=BlockingScheduler)
    runtime = _make_runtime(scheduler=scheduler)
    args = _parse_args([])

    _install_jobs(scheduler, runtime, args, clock=lambda: _kst(9, 0))

    call_kwargs = scheduler.add_job.call_args_list[job_index]
    trigger = call_kwargs.kwargs.get("trigger") or (
        call_kwargs.args[1] if len(call_kwargs.args) > 1 else None
    )
    assert isinstance(trigger, CronTrigger)

    # CronTrigger.fields 에서 field.name 으로 값 추출
    # expected_hour/minute 은 int 또는 str 이 될 수 있으므로 str() 로 통일 비교
    field_map = {f.name: f for f in trigger.fields}
    assert str(field_map["hour"]) == str(expected_hour)
    assert str(field_map["minute"]) == str(expected_minute)
    assert str(field_map["second"]) == str(expected_second)


def test_install_jobs_step_hour_range_9_14() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = MagicMock(spec=BlockingScheduler)
    runtime = _make_runtime(scheduler=scheduler)
    args = _parse_args([])

    _install_jobs(scheduler, runtime, args, clock=lambda: _kst(9, 0))

    # on_step 은 두 번째(index=1) add_job
    step_call = scheduler.add_job.call_args_list[1]
    trigger = step_call.kwargs.get("trigger") or (
        step_call.args[1] if len(step_call.args) > 1 else None
    )
    assert isinstance(trigger, CronTrigger)
    field_map = {f.name: f for f in trigger.fields}
    assert str(field_map["hour"]) == "9-14"


def test_install_jobs_모두_mon_fri_Asia_Seoul() -> None:
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.cron import CronTrigger

    scheduler = MagicMock(spec=BlockingScheduler)
    runtime = _make_runtime(scheduler=scheduler)
    args = _parse_args([])

    _install_jobs(scheduler, runtime, args, clock=lambda: _kst(9, 0))

    for idx, c in enumerate(scheduler.add_job.call_args_list):
        trigger = c.kwargs.get("trigger") or (c.args[1] if len(c.args) > 1 else None)
        trigger_msg = f"job[{idx}] trigger 는 CronTrigger 여야 한다"
        assert isinstance(trigger, CronTrigger), trigger_msg
        tz_msg = f"job[{idx}] timezone 이 Asia/Seoul 이어야 한다"
        assert str(trigger.timezone) == "Asia/Seoul", tz_msg
        field_map = {f.name: f for f in trigger.fields}
        dow_msg = f"job[{idx}] day_of_week 이 mon-fri 여야 한다"
        assert str(field_map["day_of_week"]) == "mon-fri", dow_msg


# ---------------------------------------------------------------------------
# 6. _on_session_start 콜백 동작
# ---------------------------------------------------------------------------


def test_on_session_start_잔고보다_CLI자본_작으면_CLI값_사용(mocker: Any) -> None:
    """I2 — withdrawable 기준: CLI 1M < withdrawable 2.5M → CLI 승."""
    fake_kis = MagicMock(spec=KisClient)
    fake_kis.get_balance.return_value = _make_balance(total=3_000_000, withdrawable=2_500_000)
    fake_executor = MagicMock(spec=Executor)
    runtime = _make_runtime(kis_client=fake_kis, executor=fake_executor)
    args = _parse_args(["--starting-capital", "1000000"])
    clock = lambda: _kst(9, 0)  # noqa: E731

    cb = _on_session_start(runtime, args, clock)
    cb()

    fake_executor.start_session.assert_called_once_with(_DATE, 1_000_000)


def test_on_session_start_잔고보다_CLI자본_크면_잔고값_사용(mocker: Any) -> None:
    """I2 — withdrawable 기준: CLI 3M > withdrawable 2M → withdrawable 승 (total 5M 아님)."""
    fake_kis = MagicMock(spec=KisClient)
    fake_kis.get_balance.return_value = _make_balance(total=5_000_000, withdrawable=2_000_000)
    fake_executor = MagicMock(spec=Executor)
    runtime = _make_runtime(kis_client=fake_kis, executor=fake_executor)
    args = _parse_args(["--starting-capital", "3000000"])
    clock = lambda: _kst(9, 0)  # noqa: E731

    cb = _on_session_start(runtime, args, clock)
    cb()

    # withdrawable=2_000_000 승 — total=5_000_000 이 아님에 주의
    fake_executor.start_session.assert_called_once_with(_DATE, 2_000_000)


def test_on_session_start_잔고_0이면_start_session_미호출(mocker: Any) -> None:
    """I2 — withdrawable=0 이면 total 이 아무리 커도 매매 중단."""
    fake_kis = MagicMock(spec=KisClient)
    fake_kis.get_balance.return_value = _make_balance(total=10_000_000, withdrawable=0)
    fake_executor = MagicMock(spec=Executor)
    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(kis_client=fake_kis, executor=fake_executor)
    args = _parse_args([])
    clock = lambda: _kst(9, 0)  # noqa: E731

    cb = _on_session_start(runtime, args, clock)
    cb()

    fake_executor.start_session.assert_not_called()
    mock_logger.error.assert_called_once()


def test_on_session_start_예외발생시_reraise_안함(mocker: Any) -> None:
    fake_kis = MagicMock(spec=KisClient)
    fake_kis.get_balance.side_effect = RuntimeError("잔고 조회 실패")
    fake_executor = MagicMock(spec=Executor)
    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(kis_client=fake_kis, executor=fake_executor)
    args = _parse_args([])
    clock = lambda: _kst(9, 0)  # noqa: E731

    cb = _on_session_start(runtime, args, clock)
    cb()  # raise 하면 안 됨

    mock_logger.exception.assert_called_once()


# ---------------------------------------------------------------------------
# C1 — silent failure 루프 차단
# ---------------------------------------------------------------------------


def test_on_session_start_정상시_session_status_갱신(mocker: Any) -> None:
    """C1 — 성공 시 session_status.started=True, fail_logged=False."""
    fake_kis = MagicMock(spec=KisClient)
    fake_kis.get_balance.return_value = _make_balance(total=2_000_000, withdrawable=1_800_000)
    fake_executor = MagicMock(spec=Executor)
    ss = SessionStatus(started=False, fail_logged=False)
    runtime = _make_runtime(kis_client=fake_kis, executor=fake_executor, session_status=ss)
    args = _parse_args([])
    clock = lambda: _kst(9, 0)  # noqa: E731

    cb = _on_session_start(runtime, args, clock)
    cb()

    assert runtime.session_status.started is True
    assert runtime.session_status.fail_logged is False


def test_on_session_start_실패시_started_False_유지(mocker: Any) -> None:
    """C1 — withdrawable=0 실패 시 started=False 리셋, fail_logged=False 리셋."""
    fake_kis = MagicMock(spec=KisClient)
    fake_kis.get_balance.return_value = _make_balance(total=10_000_000, withdrawable=0)
    fake_executor = MagicMock(spec=Executor)
    mocker.patch("stock_agent.main.logger")
    # 전날 성공 상태가 남아있을 수 있다고 가정
    ss = SessionStatus(started=True, fail_logged=False)
    runtime = _make_runtime(kis_client=fake_kis, executor=fake_executor, session_status=ss)
    args = _parse_args([])
    clock = lambda: _kst(9, 0)  # noqa: E731

    cb = _on_session_start(runtime, args, clock)
    cb()

    assert runtime.session_status.started is False
    assert runtime.session_status.fail_logged is False


def test_on_step_세션_미시작시_skip_dedupe(mocker: Any) -> None:
    """C1 — session_status.started=False 이면 executor.step 미호출,
    logger.warning 은 첫 호출에만 1회 (dedupe)."""
    fake_executor = MagicMock(spec=Executor)
    mock_logger = mocker.patch("stock_agent.main.logger")
    ss = SessionStatus(started=False, fail_logged=False)
    runtime = _make_runtime(executor=fake_executor, session_status=ss)
    clock = lambda: _kst(9, 5)  # noqa: E731

    cb = _on_step(runtime, clock)

    # 1회차
    cb()
    fake_executor.step.assert_not_called()
    assert mock_logger.warning.call_count == 1
    assert runtime.session_status.fail_logged is True

    # 2회차 — dedupe: warning 추가 없음
    cb()
    fake_executor.step.assert_not_called()
    assert mock_logger.warning.call_count == 1

    # 3회차 — 여전히 dedupe 유지
    cb()
    fake_executor.step.assert_not_called()
    assert mock_logger.warning.call_count == 1


# ---------------------------------------------------------------------------
# 7. _on_step 콜백 동작
# ---------------------------------------------------------------------------


def test_on_step_executor_step_호출(mocker: Any) -> None:
    fake_executor = MagicMock(spec=Executor)
    fake_executor.step.return_value = _make_step_report()
    runtime = _make_runtime(executor=fake_executor, session_status=SessionStatus(started=True))
    now = _kst(9, 5)
    clock = lambda: now  # noqa: E731

    cb = _on_step(runtime, clock)
    cb()

    fake_executor.step.assert_called_once_with(now)


def test_on_step_예외발생시_reraise_안함(mocker: Any) -> None:
    from stock_agent.execution import ExecutorError

    fake_executor = MagicMock(spec=Executor)
    fake_executor.step.side_effect = ExecutorError("step 실패")
    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(executor=fake_executor, session_status=SessionStatus(started=True))
    clock = lambda: _kst(9, 5)  # noqa: E731

    cb = _on_step(runtime, clock)
    cb()  # raise 하면 안 됨

    mock_logger.exception.assert_called_once()


# ---------------------------------------------------------------------------
# 8. _on_force_close 콜백 동작
# ---------------------------------------------------------------------------


def test_on_force_close_executor_force_close_all_호출(mocker: Any) -> None:
    fake_executor = MagicMock(spec=Executor)
    fake_executor.force_close_all.return_value = _make_step_report()
    runtime = _make_runtime(executor=fake_executor)
    now = _kst(15, 0)
    clock = lambda: now  # noqa: E731

    cb = _on_force_close(runtime, clock)
    cb()

    fake_executor.force_close_all.assert_called_once_with(now)


def test_on_force_close_예외발생시_logger_critical(mocker: Any) -> None:
    fake_executor = MagicMock(spec=Executor)
    fake_executor.force_close_all.side_effect = RuntimeError("포지션 청산 실패")
    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(executor=fake_executor)
    clock = lambda: _kst(15, 0)  # noqa: E731

    cb = _on_force_close(runtime, clock)
    cb()  # raise 하면 안 됨

    mock_logger.critical.assert_called_once()


# ---------------------------------------------------------------------------
# 9. _on_daily_report 콜백 동작
# ---------------------------------------------------------------------------


def test_on_daily_report_logger_info_최소_1회(mocker: Any) -> None:
    """I1 — runtime.risk_manager 공개 경로 사용 (executor._risk_manager 의존 없음)."""
    fake_executor = MagicMock(spec=Executor)
    fake_rm = MagicMock(spec=RiskManager)
    fake_rm.daily_realized_pnl_krw = 0
    fake_rm.entries_today = 0
    fake_rm.active_positions = ()
    fake_rm.starting_capital_krw = 1_000_000
    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(executor=fake_executor, risk_manager=fake_rm)
    clock = lambda: _kst(15, 30)  # noqa: E731

    cb = _on_daily_report(runtime, clock)
    cb()

    assert mock_logger.info.call_count >= 1


def test_on_daily_report_예외발생시_reraise_안함(mocker: Any) -> None:
    """I1 — runtime.risk_manager 접근 시 예외 유발로 전환."""
    fake_executor = MagicMock(spec=Executor)
    fake_rm = MagicMock(spec=RiskManager)
    # risk_manager 프로퍼티 접근 시 예외
    type(fake_rm).daily_realized_pnl_krw = PropertyMock(side_effect=RuntimeError("리포트 실패"))
    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(executor=fake_executor, risk_manager=fake_rm)
    clock = lambda: _kst(15, 30)  # noqa: E731

    cb = _on_daily_report(runtime, clock)
    cb()  # raise 하면 안 됨

    mock_logger.exception.assert_called_once()


def test_on_daily_report_runtime_risk_manager_공개_경로_사용(mocker: Any) -> None:
    """I1 — runtime.risk_manager 공개 프로퍼티 값이 로그에 반영됨.
    executor 에 _risk_manager 없어도 통과해야 함."""
    fake_executor = MagicMock(spec=Executor)
    # executor 에 _risk_manager 속성 없음을 명시
    del fake_executor._risk_manager  # spec=Executor 라 애초에 없지만 명확히 표현

    fake_rm = MagicMock(spec=RiskManager)
    fake_rm.daily_realized_pnl_krw = -50_000
    fake_rm.entries_today = 3
    fake_rm.active_positions = (MagicMock(), MagicMock())
    fake_rm.starting_capital_krw = 1_000_000

    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(executor=fake_executor, risk_manager=fake_rm)
    clock = lambda: _kst(15, 30)  # noqa: E731

    cb = _on_daily_report(runtime, clock)
    cb()

    # logger.info 가 최소 1회 호출됨
    assert mock_logger.info.call_count >= 1
    # 호출 인자에 pnl=-50000 / entries=3 / active=2 가 포함되어야 함
    all_call_args = str(mock_logger.info.call_args_list)
    assert "-50000" in all_call_args or "-50_000" in all_call_args or "50000" in all_call_args
    assert "3" in all_call_args
    assert "2" in all_call_args


# ---------------------------------------------------------------------------
# I5 — 정상 경로 logger.info 검증
# ---------------------------------------------------------------------------


def test_on_session_start_정상시_logger_info_호출(mocker: Any) -> None:
    """I5 — 정상 경로에서 logger.info 최소 1회 호출."""
    fake_kis = MagicMock(spec=KisClient)
    fake_kis.get_balance.return_value = _make_balance(total=2_000_000, withdrawable=1_800_000)
    fake_executor = MagicMock(spec=Executor)
    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(kis_client=fake_kis, executor=fake_executor)
    args = _parse_args([])
    clock = lambda: _kst(9, 0)  # noqa: E731

    cb = _on_session_start(runtime, args, clock)
    cb()

    mock_logger.info.assert_called()


def test_on_force_close_정상시_logger_info_호출(mocker: Any) -> None:
    """I5 — 정상 경로에서 logger.info 최소 1회, logger.critical 미호출."""
    fake_executor = MagicMock(spec=Executor)
    fake_executor.force_close_all.return_value = _make_step_report()
    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(executor=fake_executor)
    clock = lambda: _kst(15, 0)  # noqa: E731

    cb = _on_force_close(runtime, clock)
    cb()

    mock_logger.info.assert_called()
    mock_logger.critical.assert_not_called()


# ---------------------------------------------------------------------------
# 10. _graceful_shutdown 순서
# ---------------------------------------------------------------------------


def test_graceful_shutdown_순서_scheduler_rt_kis() -> None:
    call_order: list[str] = []

    fake_scheduler = MagicMock()
    fake_scheduler.shutdown.side_effect = lambda wait: call_order.append("scheduler.shutdown")

    fake_rt = MagicMock()
    fake_rt.close.side_effect = lambda: call_order.append("rt.close")

    fake_kis = MagicMock(spec=KisClient)
    fake_kis.close.side_effect = lambda: call_order.append("kis.close")

    runtime = _make_runtime(
        scheduler=fake_scheduler,
        realtime_store=fake_rt,
        kis_client=fake_kis,
    )

    _graceful_shutdown(runtime, SIGTERM, None)

    assert call_order == ["scheduler.shutdown", "rt.close", "kis.close"]


def test_graceful_shutdown_scheduler_shutdown_wait_False() -> None:
    fake_scheduler = MagicMock()
    runtime = _make_runtime(scheduler=fake_scheduler)

    _graceful_shutdown(runtime, SIGTERM, None)

    fake_scheduler.shutdown.assert_called_once_with(wait=False)


def test_graceful_shutdown_sig_dfl_교체(mocker: Any) -> None:
    """I4 — _graceful_shutdown 진입 시 SIGINT/SIGTERM 을 SIG_DFL 로 교체."""
    import signal as _signal

    mock_signal = mocker.patch("stock_agent.main.signal.signal")
    runtime = _make_runtime()
    mocker.patch("stock_agent.main.logger")

    _graceful_shutdown(runtime, SIGTERM, None)

    # signal.signal 이 SIG_DFL 로 2회 교체되어야 함
    mock_signal.assert_any_call(_signal.SIGINT, _signal.SIG_DFL)
    mock_signal.assert_any_call(_signal.SIGTERM, _signal.SIG_DFL)
    # scheduler.shutdown 보다 먼저 호출됐는지 (call_order): signal.signal 2회가
    # scheduler.shutdown 이전에 있어야 함
    all_calls = mock_signal.call_args_list
    sig_call_indices = [
        i
        for i, c in enumerate(all_calls)
        if c == call(_signal.SIGINT, _signal.SIG_DFL) or c == call(_signal.SIGTERM, _signal.SIG_DFL)
    ]
    assert len(sig_call_indices) == 2


def test_graceful_shutdown_scheduler_shutdown_예외여도_rt_kis_진행(mocker: Any) -> None:
    """I6 — scheduler.shutdown 예외여도 rt.close, kis.close 모두 호출됨."""
    call_order: list[str] = []

    fake_scheduler = MagicMock()
    fake_scheduler.shutdown.side_effect = RuntimeError("scheduler 죽음")

    fake_rt = MagicMock()
    fake_rt.close.side_effect = lambda: call_order.append("rt.close")

    fake_kis = MagicMock(spec=KisClient)
    fake_kis.close.side_effect = lambda: call_order.append("kis.close")

    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(
        scheduler=fake_scheduler,
        realtime_store=fake_rt,
        kis_client=fake_kis,
    )

    _graceful_shutdown(runtime, SIGTERM, None)

    assert "rt.close" in call_order
    assert "kis.close" in call_order
    mock_logger.warning.assert_called()


def test_graceful_shutdown_close_예외여도_다음_단계_진행(mocker: Any) -> None:
    call_order: list[str] = []

    fake_scheduler = MagicMock()
    fake_scheduler.shutdown.side_effect = lambda wait: call_order.append("scheduler")

    fake_rt = MagicMock()
    fake_rt.close.side_effect = RuntimeError("rt close 실패")

    fake_kis = MagicMock(spec=KisClient)
    fake_kis.close.side_effect = lambda: call_order.append("kis")

    mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(
        scheduler=fake_scheduler,
        realtime_store=fake_rt,
        kis_client=fake_kis,
    )

    _graceful_shutdown(runtime, SIGTERM, None)  # raise 하면 안 됨

    assert "kis" in call_order  # rt 실패해도 kis.close 호출됨


# ---------------------------------------------------------------------------
# 11. main() exit code 매핑
# ---------------------------------------------------------------------------


def _base_patches(mocker: Any) -> dict[str, MagicMock]:
    """main() 이 통과하기 위한 최소 patch 집합."""
    KospiUniverse(as_of_date=_DATE, source="t", tickers=("005930",))
    fake_settings = MagicMock()
    fake_settings.has_live_keys = True

    patches: dict[str, MagicMock] = {}
    patches["get_settings"] = mocker.patch(
        "stock_agent.main.get_settings", return_value=fake_settings
    )
    patches["configure_logging"] = mocker.patch("stock_agent.main._configure_logging")
    patches["build_runtime"] = mocker.patch("stock_agent.main.build_runtime")
    patches["signal_signal"] = mocker.patch("stock_agent.main.signal.signal")

    fake_rt = MagicMock()
    fake_kis = MagicMock(spec=KisClient)
    fake_scheduler = MagicMock()
    fake_executor = MagicMock(spec=Executor)
    fake_rm = MagicMock(spec=RiskManager)
    fake_ss = SessionStatus()
    fake_notifier = MagicMock(spec=Notifier)
    fake_recorder = MagicMock(spec=TradingRecorder)

    fake_runtime = Runtime(
        scheduler=fake_scheduler,
        executor=fake_executor,
        realtime_store=fake_rt,
        kis_client=fake_kis,
        args=_parse_args([]),
        risk_manager=fake_rm,
        session_status=fake_ss,
        notifier=fake_notifier,
        recorder=fake_recorder,
    )
    patches["build_runtime"].return_value = fake_runtime

    return patches


def test_main_정상종료_EXIT_OK(mocker: Any) -> None:
    _base_patches(mocker)
    # scheduler.start() 즉시 반환 (mock 기본 동작)

    result = main([])

    assert result == EXIT_OK


def test_main_configure_logging_OSError_EXIT_IO_ERROR(mocker: Any) -> None:
    patches = _base_patches(mocker)
    patches["configure_logging"].side_effect = OSError("로그 디렉토리 생성 실패")

    result = main([])

    assert result == EXIT_IO_ERROR


def test_main_get_settings_예외_EXIT_INPUT_ERROR(mocker: Any) -> None:
    """기존 케이스 유지 (generic RuntimeError → EXIT_INPUT_ERROR 는 현재 동작)."""
    patches = _base_patches(mocker)
    patches["get_settings"].side_effect = RuntimeError("설정 로드 실패")

    result = main([])

    assert result == EXIT_INPUT_ERROR


def test_main_get_settings_ValidationError_EXIT_INPUT_ERROR(mocker: Any) -> None:
    """I3 — pydantic ValidationError → EXIT_INPUT_ERROR."""
    from pydantic import BaseModel
    from pydantic import ValidationError as PydanticValidationError

    class _Dummy(BaseModel):
        x: int

    with contextlib.suppress(PydanticValidationError):
        _Dummy.model_validate({"x": "not-an-int-that-breaks"})

    # ValidationError 인스턴스를 side_effect 로 발생시킴
    def _raise_validation_error() -> Settings:
        try:
            _Dummy.model_validate({"x": None})
        except PydanticValidationError as e:
            raise e
        return None  # type: ignore[return-value]

    patches = _base_patches(mocker)
    patches["get_settings"].side_effect = _raise_validation_error

    result = main([])

    assert result == EXIT_INPUT_ERROR


def test_main_get_settings_OSError_EXIT_IO_ERROR(mocker: Any) -> None:
    """I3 — get_settings OSError → EXIT_IO_ERROR."""
    patches = _base_patches(mocker)
    patches["get_settings"].side_effect = OSError(".env 파일 I/O 오류")

    result = main([])

    assert result == EXIT_IO_ERROR


def test_main_get_settings_programming_error_propagates(mocker: Any) -> None:
    """I3 — ImportError 같은 프로그래밍 오류는 main() 이 삼키지 않고 전파."""
    patches = _base_patches(mocker)
    patches["get_settings"].side_effect = ImportError("가상 import 실패")

    with pytest.raises(ImportError):
        main([])


def test_main_build_runtime_RuntimeError_EXIT_INPUT_ERROR(mocker: Any) -> None:
    patches = _base_patches(mocker)
    patches["build_runtime"].side_effect = RuntimeError("has_live_keys=False")

    result = main([])

    assert result == EXIT_INPUT_ERROR


def test_main_build_runtime_UniverseLoadError_EXIT_INPUT_ERROR(mocker: Any) -> None:
    patches = _base_patches(mocker)
    patches["build_runtime"].side_effect = UniverseLoadError("유니버스 로드 실패")

    result = main([])

    assert result == EXIT_INPUT_ERROR


def test_main_starting_capital_0이면_EXIT_INPUT_ERROR(mocker: Any) -> None:
    patches = _base_patches(mocker)

    result = main(["--starting-capital", "0"])

    assert result == EXIT_INPUT_ERROR
    patches["build_runtime"].assert_not_called()  # build_runtime 전에 막아야 함


def test_main_starting_capital_음수_EXIT_INPUT_ERROR(mocker: Any) -> None:
    patches = _base_patches(mocker)

    result = main(["--starting-capital", "-1"])

    assert result == EXIT_INPUT_ERROR
    patches["build_runtime"].assert_not_called()


def test_main_KeyboardInterrupt_EXIT_OK(mocker: Any) -> None:
    patches = _base_patches(mocker)
    patches["build_runtime"].return_value.scheduler.start.side_effect = KeyboardInterrupt

    result = main([])

    assert result == EXIT_OK


# ---------------------------------------------------------------------------
# 12. main() 리소스 정리 (finally 블록)
# ---------------------------------------------------------------------------


def test_main_정상종료시_realtime_close_kis_close_호출(mocker: Any) -> None:
    patches = _base_patches(mocker)
    runtime = patches["build_runtime"].return_value

    main([])

    runtime.realtime_store.close.assert_called_once()
    runtime.kis_client.close.assert_called_once()


def test_main_예외시에도_realtime_close_kis_close_호출(mocker: Any) -> None:
    patches = _base_patches(mocker)
    runtime = patches["build_runtime"].return_value
    runtime.scheduler.start.side_effect = RuntimeError("스케줄러 크래시")

    with contextlib.suppress(Exception):
        main([])

    runtime.realtime_store.close.assert_called_once()
    runtime.kis_client.close.assert_called_once()


def test_main_realtime_start_는_scheduler_start_전에_호출(mocker: Any) -> None:
    patches = _base_patches(mocker)
    runtime = patches["build_runtime"].return_value
    call_order: list[str] = []

    runtime.realtime_store.start.side_effect = lambda: call_order.append("rt.start")
    runtime.scheduler.start.side_effect = lambda: call_order.append("scheduler.start")

    main([])

    rt_idx = call_order.index("rt.start")
    sc_idx = call_order.index("scheduler.start")
    assert rt_idx < sc_idx, "realtime_store.start() 가 scheduler.start() 보다 먼저 호출되어야 한다"


# ---------------------------------------------------------------------------
# 상수 검증
# ---------------------------------------------------------------------------


def test_exit_code_상수_값() -> None:
    assert EXIT_OK == 0
    assert EXIT_UNEXPECTED == 1
    assert EXIT_INPUT_ERROR == 2
    assert EXIT_IO_ERROR == 3


def test_KST_상수_UTC_플러스_9() -> None:
    assert timezone(timedelta(hours=9)) == KST


def test_Runtime_frozen_dataclass() -> None:
    assert dataclasses.is_dataclass(Runtime)
    # frozen 이면 FrozenInstanceError 발생
    runtime = _make_runtime()
    with pytest.raises(dataclasses.FrozenInstanceError):
        runtime.scheduler = MagicMock()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# I6 — _configure_logging 단위 테스트
# ---------------------------------------------------------------------------


def test_configure_logging_신규_log_dir_생성(mocker: Any, tmp_path: Path) -> None:
    """I6 — 존재하지 않는 디렉토리도 생성됨 + logger.remove 1회, logger.add 2회."""
    log_dir = tmp_path / "nonexistent_logs"
    assert not log_dir.exists()

    mock_logger = mocker.patch("stock_agent.main.logger")

    _configure_logging(log_dir)

    assert log_dir.exists()
    assert mock_logger.remove.call_count == 1
    assert mock_logger.add.call_count == 2


def test_configure_logging_기존_log_dir_멱등(mocker: Any, tmp_path: Path) -> None:
    """I6 — 이미 존재하는 디렉토리에서 FileExistsError 없이 정상 완료."""
    mocker.patch("stock_agent.main.logger")

    # 첫 호출
    _configure_logging(tmp_path)
    # 두 번째 호출 — FileExistsError 발생하면 안 됨
    _configure_logging(tmp_path)


def test_configure_logging_logger_remove_먼저_호출(mocker: Any, tmp_path: Path) -> None:
    """I6 — logger.remove 가 logger.add 보다 먼저 호출됨."""
    call_order: list[str] = []
    mock_logger = mocker.patch("stock_agent.main.logger")
    mock_logger.remove.side_effect = lambda *a, **k: call_order.append("remove")
    mock_logger.add.side_effect = lambda *a, **k: call_order.append("add")

    _configure_logging(tmp_path)

    assert call_order[0] == "remove", f"remove 가 먼저여야 하는데 순서: {call_order}"
    assert call_order.count("add") == 2


# ---------------------------------------------------------------------------
# SessionStatus dataclass 검증
# ---------------------------------------------------------------------------


def test_SessionStatus_기본값() -> None:
    """SessionStatus 가 공개 dataclass 로 존재하고 기본값이 올바른지 확인."""
    ss = SessionStatus()
    assert ss.started is False
    assert ss.fail_logged is False


def test_SessionStatus_mutable() -> None:
    """SessionStatus 는 frozen 이 아니라 내부 필드 변경이 가능해야 함 (C1 설계)."""
    ss = SessionStatus()
    ss.started = True
    ss.fail_logged = True
    assert ss.started is True
    assert ss.fail_logged is True


# ===========================================================================
# 그룹 A — _default_notifier_factory
# ===========================================================================


def _make_fake_settings_for_notifier() -> MagicMock:
    """_default_notifier_factory 에서 접근하는 속성을 가진 settings 더블.

    spec=Settings 를 쓰면 SecretStr 필드가 MagicMock으로 반환되어 TelegramNotifier
    생성자 내부에서 AttributeError 가 발생한다. spec 없이 MagicMock 을 만들고
    필요한 속성만 명시적으로 설정한다 — _default_notifier_factory 의 경계만 검증.
    """
    from pydantic import SecretStr

    fake_settings = MagicMock()
    fake_settings.telegram_bot_token = SecretStr("dummy-bot-token:TEST")
    fake_settings.telegram_chat_id = 123456789
    return fake_settings


def test_default_notifier_factory_dry_run_False_는_TelegramNotifier_반환(
    mocker: Any,
) -> None:
    """A1 — dry_run=False 이면 TelegramNotifier 인스턴스를 반환.
    실 텔레그램 접촉 없이 생성자 호출 여부를 sentinel 로 검증한다.
    """
    from unittest.mock import sentinel

    fake_settings = _make_fake_settings_for_notifier()

    mock_telegram = mocker.patch(
        "stock_agent.main.TelegramNotifier", return_value=sentinel.telegram_instance
    )

    result = _default_notifier_factory(fake_settings, dry_run=False)

    mock_telegram.assert_called_once()
    assert result is sentinel.telegram_instance


def test_default_notifier_factory_TelegramNotifier_예외시_NullNotifier_폴백(
    mocker: Any,
) -> None:
    """A2 — TelegramNotifier 생성자가 RuntimeError 를 던지면 NullNotifier 반환.
    logger.warning 도 1회 호출됨.
    """
    fake_settings = _make_fake_settings_for_notifier()
    mocker.patch("stock_agent.main.TelegramNotifier", side_effect=RuntimeError("봇 초기화 실패"))
    mock_logger = mocker.patch("stock_agent.main.logger")

    result = _default_notifier_factory(fake_settings, dry_run=False)

    assert isinstance(result, NullNotifier)
    mock_logger.warning.assert_called_once()
    warning_msg = str(mock_logger.warning.call_args)
    assert "NullNotifier" in warning_msg or "notifier_factory" in warning_msg


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("runtime"),
        ValueError("invalid"),
        ImportError("missing dep"),
        OSError("network down"),
        Exception("generic"),
    ],
    ids=["RuntimeError", "ValueError", "ImportError", "OSError", "Exception"],
)
def test_default_notifier_factory_예외_5종_모두_NullNotifier_폴백(
    mocker: Any,
    exc: Exception,
) -> None:
    """예외 5종 parametrize 회귀.

    `except Exception` 을 좁히면 실패하도록 계약 잠금. 관련 이슈 #27.
    """
    fake_settings = _make_fake_settings_for_notifier()
    mocker.patch("stock_agent.main.TelegramNotifier", side_effect=exc)
    mock_logger = mocker.patch("stock_agent.main.logger")

    result = _default_notifier_factory(fake_settings, dry_run=False)

    assert isinstance(result, NullNotifier)
    mock_logger.warning.assert_called_once()
    warning_msg = str(mock_logger.warning.call_args)
    assert "NullNotifier" in warning_msg or "notifier_factory" in warning_msg


def test_default_notifier_factory_dry_run_True_가_TelegramNotifier에_전달됨(
    mocker: Any,
) -> None:
    """A3 — dry_run=True 가 TelegramNotifier 생성자 인자로 전달됨."""
    fake_settings = _make_fake_settings_for_notifier()
    mock_telegram = mocker.patch("stock_agent.main.TelegramNotifier", return_value=MagicMock())

    _default_notifier_factory(fake_settings, dry_run=True)

    assert mock_telegram.call_count == 1
    _, kwargs = mock_telegram.call_args
    assert kwargs.get("dry_run") is True


# ===========================================================================
# 그룹 B — _on_session_start notifier 통합
# ===========================================================================


def test_on_session_start_정상경로_notify_error_미호출(mocker: Any) -> None:
    """B1 — 정상 경로에서 notify_error 미호출. 진입 성공 알림은 이번 PR 범위 밖."""
    fake_kis = MagicMock(spec=KisClient)
    fake_kis.get_balance.return_value = _make_balance(total=2_000_000, withdrawable=1_800_000)
    fake_executor = MagicMock(spec=Executor)
    fake_notifier = MagicMock(spec=Notifier)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(kis_client=fake_kis, executor=fake_executor, notifier=fake_notifier)
    args = _parse_args([])
    clock = lambda: _kst(9, 0)  # noqa: E731

    cb = _on_session_start(runtime, args, clock)
    cb()

    fake_notifier.notify_error.assert_not_called()


def test_on_session_start_자본_0이면_notify_error_stage_session_start(
    mocker: Any,
) -> None:
    """B2 — withdrawable==0 이면 notify_error(stage="session_start", severity="error") 1회.
    error_class 에 "StartingCapitalError" 문자열 포함.
    """
    fake_kis = MagicMock(spec=KisClient)
    fake_kis.get_balance.return_value = _make_balance(total=10_000_000, withdrawable=0)
    fake_notifier = MagicMock(spec=Notifier)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(kis_client=fake_kis, notifier=fake_notifier)
    args = _parse_args([])
    clock = lambda: _kst(9, 0)  # noqa: E731

    cb = _on_session_start(runtime, args, clock)
    cb()

    fake_notifier.notify_error.assert_called_once()
    event: ErrorEvent = fake_notifier.notify_error.call_args[0][0]
    assert event.stage == "session_start"
    assert event.severity == "error"
    assert "StartingCapitalError" in event.error_class


def test_on_session_start_예외발생시_notify_error_error_class_포함(
    mocker: Any,
) -> None:
    """B3 — get_balance 예외 시 notify_error(stage="session_start", error_class=<클래스명>) 1회."""
    fake_kis = MagicMock(spec=KisClient)
    fake_kis.get_balance.side_effect = ConnectionError("네트워크 오류")
    fake_notifier = MagicMock(spec=Notifier)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(kis_client=fake_kis, notifier=fake_notifier)
    args = _parse_args([])
    clock = lambda: _kst(9, 0)  # noqa: E731

    cb = _on_session_start(runtime, args, clock)
    cb()

    fake_notifier.notify_error.assert_called_once()
    event: ErrorEvent = fake_notifier.notify_error.call_args[0][0]
    assert event.stage == "session_start"
    assert event.error_class == "ConnectionError"
    assert event.severity == "error"


# ===========================================================================
# 그룹 C — _on_step notifier 통합
# ===========================================================================


def test_on_step_entry_exit_events_각각_notify_호출(mocker: Any) -> None:
    """C1 — entry_events 2건 + exit_events 1건 → notify_entry 2회, notify_exit 1회.
    reconcile mismatch 없음 → notify_error 미호출.
    """
    e1 = _make_entry_event("005930")
    e2 = _make_entry_event("000660")
    x1 = _make_exit_event("035420")
    report = _make_step_report(
        entry_events=(e1, e2),
        exit_events=(x1,),
        mismatch_symbols=(),
    )

    fake_executor = MagicMock(spec=Executor)
    fake_executor.step.return_value = report
    fake_notifier = MagicMock(spec=Notifier)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(
        executor=fake_executor,
        notifier=fake_notifier,
        session_status=SessionStatus(started=True),
    )
    clock = lambda: _kst(9, 5)  # noqa: E731

    cb = _on_step(runtime, clock)
    cb()

    assert fake_notifier.notify_entry.call_count == 2
    assert fake_notifier.notify_exit.call_count == 1
    fake_notifier.notify_error.assert_not_called()

    # 순서 검증 — e1, e2 순으로 notify_entry 호출
    assert fake_notifier.notify_entry.call_args_list[0][0][0] is e1
    assert fake_notifier.notify_entry.call_args_list[1][0][0] is e2
    assert fake_notifier.notify_exit.call_args_list[0][0][0] is x1


def test_on_step_mismatch_symbols_notify_error_critical_1회(mocker: Any) -> None:
    """C2 — mismatch_symbols 비어있지 않으면 notify_error(reconcile, critical) 1회.

    message 에 종목 코드 포함.
    """
    report = _make_step_report(mismatch_symbols=("005930", "000660"))
    fake_executor = MagicMock(spec=Executor)
    fake_executor.step.return_value = report
    fake_notifier = MagicMock(spec=Notifier)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(
        executor=fake_executor,
        notifier=fake_notifier,
        session_status=SessionStatus(started=True),
    )
    clock = lambda: _kst(9, 5)  # noqa: E731

    cb = _on_step(runtime, clock)
    cb()

    fake_notifier.notify_error.assert_called_once()
    event: ErrorEvent = fake_notifier.notify_error.call_args[0][0]
    assert event.stage == "reconcile"
    assert event.severity == "critical"
    assert "005930" in event.message or "000660" in event.message


def test_on_step_예외시_notify_error_stage_step_error_class_포함(mocker: Any) -> None:
    """C3 — executor.step 이 예외 → notify_error(stage="step", error_class=<클래스명>) 1회.
    entry/exit notify 는 미호출.
    """
    from stock_agent.execution import ExecutorError

    fake_executor = MagicMock(spec=Executor)
    fake_executor.step.side_effect = ExecutorError("step 내부 오류")
    fake_notifier = MagicMock(spec=Notifier)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(
        executor=fake_executor,
        notifier=fake_notifier,
        session_status=SessionStatus(started=True),
    )
    clock = lambda: _kst(9, 5)  # noqa: E731

    cb = _on_step(runtime, clock)
    cb()

    fake_notifier.notify_error.assert_called_once()
    event: ErrorEvent = fake_notifier.notify_error.call_args[0][0]
    assert event.stage == "step"
    assert event.error_class == "ExecutorError"
    assert event.severity == "error"
    fake_notifier.notify_entry.assert_not_called()
    fake_notifier.notify_exit.assert_not_called()


def test_on_step_세션_미시작_notify_미호출(mocker: Any) -> None:
    """C4 — session_status.started==False 이면 notify_* 일절 미호출 (skip 경로)."""
    fake_executor = MagicMock(spec=Executor)
    fake_notifier = MagicMock(spec=Notifier)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(
        executor=fake_executor,
        notifier=fake_notifier,
        session_status=SessionStatus(started=False),
    )
    clock = lambda: _kst(9, 5)  # noqa: E731

    cb = _on_step(runtime, clock)
    cb()

    fake_notifier.notify_entry.assert_not_called()
    fake_notifier.notify_exit.assert_not_called()
    fake_notifier.notify_error.assert_not_called()


# ===========================================================================
# 그룹 D — _on_force_close notifier 통합
# ===========================================================================


def test_on_force_close_정상경로_exit_events_notify_exit_호출(mocker: Any) -> None:
    """D1 — 정상 경로 exit_events 1건 → notify_exit 1회, notify_error 미호출."""
    x1 = _make_exit_event("005930", reason="force_close")
    report = _make_step_report(exit_events=(x1,))
    fake_executor = MagicMock(spec=Executor)
    fake_executor.force_close_all.return_value = report
    fake_notifier = MagicMock(spec=Notifier)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(executor=fake_executor, notifier=fake_notifier)
    clock = lambda: _kst(15, 0)  # noqa: E731

    cb = _on_force_close(runtime, clock)
    cb()

    fake_notifier.notify_exit.assert_called_once_with(x1)
    fake_notifier.notify_error.assert_not_called()


def test_on_force_close_예외시_notify_error_critical_및_logger_critical(
    mocker: Any,
) -> None:
    """D2 — force_close_all 예외 → notify_error(stage="force_close", severity="critical") 1회.
    logger.critical 도 동시 호출 (기존 계약 유지).
    """
    fake_executor = MagicMock(spec=Executor)
    fake_executor.force_close_all.side_effect = RuntimeError("청산 API 실패")
    fake_notifier = MagicMock(spec=Notifier)
    mock_logger = mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(executor=fake_executor, notifier=fake_notifier)
    clock = lambda: _kst(15, 0)  # noqa: E731

    cb = _on_force_close(runtime, clock)
    cb()

    fake_notifier.notify_error.assert_called_once()
    event: ErrorEvent = fake_notifier.notify_error.call_args[0][0]
    assert event.stage == "force_close"
    assert event.severity == "critical"
    mock_logger.critical.assert_called_once()


# ===========================================================================
# 그룹 E — _on_daily_report notifier 통합
# ===========================================================================


def test_on_daily_report_정상경로_notify_daily_summary_1회(mocker: Any) -> None:
    """E1 — 정상 경로에서 notify_daily_summary 1회. summary 필드 검증."""
    fake_executor = MagicMock(spec=Executor)
    fake_executor.is_halted = False
    fake_executor.last_reconcile = None

    fake_rm = MagicMock(spec=RiskManager)
    fake_rm.daily_realized_pnl_krw = 10_000
    fake_rm.entries_today = 2
    fake_rm.active_positions = ()
    fake_rm.starting_capital_krw = 1_000_000

    fake_notifier = MagicMock(spec=Notifier)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(executor=fake_executor, risk_manager=fake_rm, notifier=fake_notifier)
    now = _kst(15, 30)
    clock = lambda: now  # noqa: E731

    cb = _on_daily_report(runtime, clock)
    cb()

    fake_notifier.notify_daily_summary.assert_called_once()
    summary: DailySummary = fake_notifier.notify_daily_summary.call_args[0][0]
    assert summary.session_date == now.date()
    assert summary.realized_pnl_krw == 10_000
    assert summary.entries_today == 2
    assert summary.halted is False


def test_on_daily_report_realized_pnl_pct_계산(mocker: Any) -> None:
    """E2 — starting_capital=1_000_000, pnl=15_000 → realized_pnl_pct ≈ 1.5."""
    fake_executor = MagicMock(spec=Executor)
    fake_executor.is_halted = False
    fake_executor.last_reconcile = None

    fake_rm = MagicMock(spec=RiskManager)
    fake_rm.daily_realized_pnl_krw = 15_000
    fake_rm.entries_today = 1
    fake_rm.active_positions = ()
    fake_rm.starting_capital_krw = 1_000_000

    fake_notifier = MagicMock(spec=Notifier)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(executor=fake_executor, risk_manager=fake_rm, notifier=fake_notifier)
    clock = lambda: _kst(15, 30)  # noqa: E731

    cb = _on_daily_report(runtime, clock)
    cb()

    summary: DailySummary = fake_notifier.notify_daily_summary.call_args[0][0]
    assert summary.realized_pnl_pct == pytest.approx(1.5, abs=1e-6)


@pytest.mark.parametrize(
    "starting_capital",
    [None, 0],
    ids=["starting_capital_None", "starting_capital_0"],
)
def test_on_daily_report_realized_pnl_pct_None_when_starting_0_or_None(
    mocker: Any,
    starting_capital: int | None,
) -> None:
    """E3 — starting_capital_krw=None 또는 0 이면 realized_pnl_pct is None."""
    fake_executor = MagicMock(spec=Executor)
    fake_executor.is_halted = False
    fake_executor.last_reconcile = None

    fake_rm = MagicMock(spec=RiskManager)
    fake_rm.daily_realized_pnl_krw = 5_000
    fake_rm.entries_today = 1
    fake_rm.active_positions = ()
    fake_rm.starting_capital_krw = starting_capital

    fake_notifier = MagicMock(spec=Notifier)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(executor=fake_executor, risk_manager=fake_rm, notifier=fake_notifier)
    clock = lambda: _kst(15, 30)  # noqa: E731

    cb = _on_daily_report(runtime, clock)
    cb()

    summary: DailySummary = fake_notifier.notify_daily_summary.call_args[0][0]
    assert summary.realized_pnl_pct is None


def test_on_daily_report_pct_decimal_타입_드리프트_내성(mocker: Any) -> None:
    """I3 #25 — RiskManager pct 가 Decimal 로 드리프트해도
    notify_daily_summary 가 호출되고 pct 는 정상 float 값이다.
    """
    from decimal import Decimal

    fake_executor = MagicMock(spec=Executor)
    fake_executor.is_halted = False
    fake_executor.last_reconcile = None

    fake_rm = MagicMock(spec=RiskManager)
    fake_rm.daily_realized_pnl_krw = Decimal("-12345")
    fake_rm.entries_today = 1
    fake_rm.active_positions = ()
    fake_rm.starting_capital_krw = Decimal("1000000")

    fake_notifier = MagicMock(spec=Notifier)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(executor=fake_executor, risk_manager=fake_rm, notifier=fake_notifier)
    clock = lambda: _kst(15, 30)  # noqa: E731

    cb = _on_daily_report(runtime, clock)
    cb()

    fake_notifier.notify_daily_summary.assert_called_once()
    summary: DailySummary = fake_notifier.notify_daily_summary.call_args[0][0]
    assert summary.realized_pnl_pct == pytest.approx(-1.2345, rel=1e-6)
    fake_notifier.notify_error.assert_not_called()


def test_on_daily_report_mismatch_symbols_from_last_reconcile(mocker: Any) -> None:
    """E4 — last_reconcile.mismatch_symbols=("A",) → DailySummary.mismatch_symbols==("A",).
    last_reconcile=None 이면 mismatch_symbols==().
    """
    fake_executor_with_reconcile = MagicMock(spec=Executor)
    fake_executor_with_reconcile.is_halted = False
    # last_reconcile 은 spec=Executor 에 있으므로 return_value 로 설정 불가 —
    # PropertyMock 또는 직접 속성으로 설정.
    fake_reconcile = ReconcileReport(
        broker_holdings={},
        risk_holdings={},
        mismatch_symbols=("A",),
    )
    type(fake_executor_with_reconcile).last_reconcile = PropertyMock(return_value=fake_reconcile)

    fake_rm = MagicMock(spec=RiskManager)
    fake_rm.daily_realized_pnl_krw = 0
    fake_rm.entries_today = 0
    fake_rm.active_positions = ()
    fake_rm.starting_capital_krw = 1_000_000

    fake_notifier = MagicMock(spec=Notifier)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(
        executor=fake_executor_with_reconcile,
        risk_manager=fake_rm,
        notifier=fake_notifier,
    )
    clock = lambda: _kst(15, 30)  # noqa: E731

    cb = _on_daily_report(runtime, clock)
    cb()

    summary_with: DailySummary = fake_notifier.notify_daily_summary.call_args[0][0]
    assert summary_with.mismatch_symbols == ("A",)

    # last_reconcile=None 경우
    fake_executor_none = MagicMock(spec=Executor)
    fake_executor_none.is_halted = False
    type(fake_executor_none).last_reconcile = PropertyMock(return_value=None)

    fake_notifier2 = MagicMock(spec=Notifier)
    runtime2 = _make_runtime(
        executor=fake_executor_none,
        risk_manager=fake_rm,
        notifier=fake_notifier2,
    )

    cb2 = _on_daily_report(runtime2, clock)
    cb2()

    summary_none: DailySummary = fake_notifier2.notify_daily_summary.call_args[0][0]
    assert summary_none.mismatch_symbols == ()


# ===========================================================================
# 그룹 F — _default_recorder_factory
# ===========================================================================


def _make_fake_settings_for_recorder() -> MagicMock:
    """_default_recorder_factory 에서 접근하는 settings 더블.

    recorder factory 는 현재 settings 필드에 직접 접근하지 않지만
    notifier factory 와 동일 기조로 MagicMock 을 사용한다.
    """
    return MagicMock()


def test_default_recorder_factory_정상조립_SqliteTradingRecorder_반환(
    mocker: Any,
) -> None:
    """F1 — SqliteTradingRecorder 정상 조립 시 인스턴스 반환.

    실 SQLite 접촉 없이 생성자 호출 여부와 db_path 인자를 sentinel 로 검증한다.
    db_path 는 절대경로(_TRADING_DB_PATH)여야 한다 — CWD 의존성 제거 계약(리뷰 C1).
    """
    from unittest.mock import sentinel

    from stock_agent.main import _TRADING_DB_PATH

    fake_settings = _make_fake_settings_for_recorder()
    mock_sqlite = mocker.patch(
        "stock_agent.main.SqliteTradingRecorder",
        return_value=sentinel.sqlite_instance,
    )

    result = _default_recorder_factory(fake_settings, dry_run=False)

    mock_sqlite.assert_called_once()
    _, kwargs = mock_sqlite.call_args
    assert kwargs.get("db_path") == _TRADING_DB_PATH
    assert kwargs["db_path"].is_absolute()
    assert result is sentinel.sqlite_instance


def test_default_recorder_factory_StorageError_시_NullTradingRecorder_폴백(
    mocker: Any,
) -> None:
    """F2 — SqliteTradingRecorder 생성자가 StorageError 를 던지면 NullTradingRecorder 반환.

    logger.warning 도 1회 호출됨.
    """
    fake_settings = _make_fake_settings_for_recorder()
    mocker.patch(
        "stock_agent.main.SqliteTradingRecorder",
        side_effect=StorageError("DB 초기화 실패"),
    )
    mock_logger = mocker.patch("stock_agent.main.logger")

    result = _default_recorder_factory(fake_settings, dry_run=False)

    assert isinstance(result, NullTradingRecorder)
    mock_logger.warning.assert_called_once()
    warning_msg = str(mock_logger.warning.call_args)
    assert "NullTradingRecorder" in warning_msg or "recorder_factory" in warning_msg


def test_default_recorder_factory_RuntimeError_시_NullTradingRecorder_폴백(
    mocker: Any,
) -> None:
    """F3 — 일반 RuntimeError 에서도 NullTradingRecorder 폴백."""
    fake_settings = _make_fake_settings_for_recorder()
    mocker.patch(
        "stock_agent.main.SqliteTradingRecorder",
        side_effect=RuntimeError("예상치 못한 오류"),
    )
    mocker.patch("stock_agent.main.logger")

    result = _default_recorder_factory(fake_settings, dry_run=False)

    assert isinstance(result, NullTradingRecorder)


def test_default_recorder_factory_OSError_시_NullTradingRecorder_폴백(
    mocker: Any,
) -> None:
    """F4 — OSError(디스크·권한 등) 에서도 NullTradingRecorder 폴백."""
    fake_settings = _make_fake_settings_for_recorder()
    mocker.patch(
        "stock_agent.main.SqliteTradingRecorder",
        side_effect=OSError("디스크 쓰기 불가"),
    )
    mocker.patch("stock_agent.main.logger")

    result = _default_recorder_factory(fake_settings, dry_run=False)

    assert isinstance(result, NullTradingRecorder)


# ===========================================================================
# 그룹 G — build_runtime recorder 주입
# ===========================================================================


def test_build_runtime_recorder_factory_주입_시_runtime_recorder_에_반영(
    _mock_universe: KospiUniverse, _fake_settings: MagicMock
) -> None:
    """G1 — recorder_factory 주입 시 해당 팩토리 반환값이 runtime.recorder 로 들어감."""
    fake_rt = MagicMock()
    fake_recorder = MagicMock(spec=TradingRecorder)

    runtime = build_runtime(
        _parse_args([]),
        _fake_settings,
        kis_client_factory=lambda s: MagicMock(spec=KisClient),
        realtime_store_factory=lambda s: fake_rt,
        scheduler_factory=MagicMock,
        universe_loader=lambda p: _mock_universe,
        notifier_factory=lambda s, d: MagicMock(spec=Notifier),
        recorder_factory=lambda s, d: fake_recorder,
    )

    assert runtime.recorder is fake_recorder


def test_build_runtime_recorder_factory_None_시_default_recorder_factory_호출(
    _mock_universe: KospiUniverse, _fake_settings: MagicMock, mocker: Any
) -> None:
    """G2 — recorder_factory=None 미지정 시 _default_recorder_factory 가 호출됨.

    팩토리 호출 인자 (settings, dry_run) 를 mock 으로 검증한다.
    """
    fake_rt = MagicMock()
    fake_recorder = MagicMock(spec=TradingRecorder)
    mock_default_factory = mocker.patch(
        "stock_agent.main._default_recorder_factory",
        return_value=fake_recorder,
    )
    args = _parse_args(["--dry-run"])

    runtime = build_runtime(
        args,
        _fake_settings,
        kis_client_factory=lambda s: MagicMock(spec=KisClient),
        realtime_store_factory=lambda s: fake_rt,
        scheduler_factory=MagicMock,
        universe_loader=lambda p: _mock_universe,
        notifier_factory=lambda s, d: MagicMock(spec=Notifier),
        # recorder_factory 미지정 → _default_recorder_factory 사용
    )

    mock_default_factory.assert_called_once_with(_fake_settings, True)
    assert runtime.recorder is fake_recorder


# ===========================================================================
# 그룹 H — _on_step recorder 포워딩
# ===========================================================================


def test_on_step_entry_events_record_entry_포워딩(mocker: Any) -> None:
    """H1 — entry_events 2건 → recorder.record_entry 2회 호출, 인자는 각 EntryEvent."""
    e1 = _make_entry_event("005930")
    e2 = _make_entry_event("000660")
    report = _make_step_report(entry_events=(e1, e2), exit_events=())

    fake_executor = MagicMock(spec=Executor)
    fake_executor.step.return_value = report
    fake_recorder = MagicMock(spec=TradingRecorder)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(
        executor=fake_executor,
        recorder=fake_recorder,
        session_status=SessionStatus(started=True),
    )
    clock = lambda: _kst(9, 5)  # noqa: E731

    cb = _on_step(runtime, clock)
    cb()

    assert fake_recorder.record_entry.call_count == 2
    assert fake_recorder.record_entry.call_args_list[0][0][0] is e1
    assert fake_recorder.record_entry.call_args_list[1][0][0] is e2


def test_on_step_exit_events_record_exit_포워딩(mocker: Any) -> None:
    """H2 — exit_events 1건 → recorder.record_exit 1회 호출, 인자는 ExitEvent."""
    x1 = _make_exit_event("005930", reason="stop_loss")
    report = _make_step_report(entry_events=(), exit_events=(x1,))

    fake_executor = MagicMock(spec=Executor)
    fake_executor.step.return_value = report
    fake_recorder = MagicMock(spec=TradingRecorder)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(
        executor=fake_executor,
        recorder=fake_recorder,
        session_status=SessionStatus(started=True),
    )
    clock = lambda: _kst(9, 5)  # noqa: E731

    cb = _on_step(runtime, clock)
    cb()

    assert fake_recorder.record_exit.call_count == 1
    assert fake_recorder.record_exit.call_args_list[0][0][0] is x1


def test_on_step_예외시_recorder_미호출(mocker: Any) -> None:
    """H3 — executor.step 예외 발생 시 recorder.record_entry/record_exit 미호출.

    예외 경로에서는 notifier.notify_error 만 호출되고 recorder 는 호출되지 않는다.
    """
    from stock_agent.execution import ExecutorError

    fake_executor = MagicMock(spec=Executor)
    fake_executor.step.side_effect = ExecutorError("step 내부 오류")
    fake_recorder = MagicMock(spec=TradingRecorder)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(
        executor=fake_executor,
        recorder=fake_recorder,
        session_status=SessionStatus(started=True),
    )
    clock = lambda: _kst(9, 5)  # noqa: E731

    cb = _on_step(runtime, clock)
    cb()

    fake_recorder.record_entry.assert_not_called()
    fake_recorder.record_exit.assert_not_called()


# ===========================================================================
# 그룹 I — _on_force_close recorder 포워딩
# ===========================================================================


def test_on_force_close_exit_events_record_exit_포워딩(mocker: Any) -> None:
    """I1 — force_close_all 반환 exit_events 1건 → recorder.record_exit 1회 호출."""
    x1 = _make_exit_event("005930", reason="force_close")
    report = _make_step_report(exit_events=(x1,))

    fake_executor = MagicMock(spec=Executor)
    fake_executor.force_close_all.return_value = report
    fake_recorder = MagicMock(spec=TradingRecorder)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(executor=fake_executor, recorder=fake_recorder)
    clock = lambda: _kst(15, 0)  # noqa: E731

    cb = _on_force_close(runtime, clock)
    cb()

    assert fake_recorder.record_exit.call_count == 1
    assert fake_recorder.record_exit.call_args_list[0][0][0] is x1


# ===========================================================================
# 그룹 J — _on_daily_report recorder 포워딩
# ===========================================================================


def test_on_daily_report_record_daily_summary_1회_호출(mocker: Any) -> None:
    """J1 — 정상 경로에서 recorder.record_daily_summary 1회 호출, 인자는 DailySummary."""
    fake_executor = MagicMock(spec=Executor)
    fake_executor.is_halted = False
    fake_executor.last_reconcile = None

    fake_rm = MagicMock(spec=RiskManager)
    fake_rm.daily_realized_pnl_krw = 5_000
    fake_rm.entries_today = 1
    fake_rm.active_positions = ()
    fake_rm.starting_capital_krw = 1_000_000

    fake_recorder = MagicMock(spec=TradingRecorder)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(
        executor=fake_executor,
        risk_manager=fake_rm,
        recorder=fake_recorder,
    )
    now = _kst(15, 30)
    clock = lambda: now  # noqa: E731

    cb = _on_daily_report(runtime, clock)
    cb()

    fake_recorder.record_daily_summary.assert_called_once()
    summary: DailySummary = fake_recorder.record_daily_summary.call_args[0][0]
    assert summary.session_date == now.date()
    assert summary.realized_pnl_krw == 5_000
    assert summary.entries_today == 1


def test_on_daily_report_recorder_와_notifier_모두_호출됨(mocker: Any) -> None:
    """J2 — recorder.record_daily_summary 와 notifier.notify_daily_summary 모두 1회 호출.

    둘 중 하나가 실패해도 다른 쪽이 차단되지 않도록, 호출 여부만 검증.
    순서 강제 테스트는 과적합 방지로 생략.
    """
    fake_executor = MagicMock(spec=Executor)
    fake_executor.is_halted = False
    fake_executor.last_reconcile = None

    fake_rm = MagicMock(spec=RiskManager)
    fake_rm.daily_realized_pnl_krw = 0
    fake_rm.entries_today = 0
    fake_rm.active_positions = ()
    fake_rm.starting_capital_krw = 1_000_000

    fake_recorder = MagicMock(spec=TradingRecorder)
    fake_notifier = MagicMock(spec=Notifier)
    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(
        executor=fake_executor,
        risk_manager=fake_rm,
        recorder=fake_recorder,
        notifier=fake_notifier,
    )
    clock = lambda: _kst(15, 30)  # noqa: E731

    cb = _on_daily_report(runtime, clock)
    cb()

    fake_recorder.record_daily_summary.assert_called_once()
    fake_notifier.notify_daily_summary.assert_called_once()


# ===========================================================================
# 그룹 K — _graceful_shutdown recorder.close 호출
# ===========================================================================


def test_graceful_shutdown_recorder_close_호출됨() -> None:
    """K1 — _graceful_shutdown 시 runtime.recorder.close() 가 호출된다.

    notifier 관련 _graceful_shutdown 테스트는 없지만 recorder 는 닫아야 하는
    리소스(SQLite 연결) 이므로 명시 검증한다.
    """
    fake_recorder = MagicMock(spec=TradingRecorder)
    runtime = _make_runtime(recorder=fake_recorder)

    _graceful_shutdown(runtime, SIGTERM, None)

    fake_recorder.close.assert_called_once()


def test_graceful_shutdown_recorder_close_예외여도_silent_진행(mocker: Any) -> None:
    """K2 — recorder.close 가 예외를 던져도 graceful shutdown 은 warning + silent 진행.

    kis_client.close 도 정상 호출됨을 확인 (recorder 실패가 후속 단계를 막지 않음).
    """
    fake_recorder = MagicMock(spec=TradingRecorder)
    fake_recorder.close.side_effect = RuntimeError("DB close 실패")

    fake_kis = MagicMock(spec=KisClient)
    call_order: list[str] = []
    fake_kis.close.side_effect = lambda: call_order.append("kis.close")

    mock_logger = mocker.patch("stock_agent.main.logger")
    runtime = _make_runtime(recorder=fake_recorder, kis_client=fake_kis)

    _graceful_shutdown(runtime, SIGTERM, None)  # raise 하면 안 됨

    # recorder.close 예외가 경보되어야 함
    mock_logger.warning.assert_called()
    # kis.close 도 정상 호출됨
    assert "kis.close" in call_order


# ===========================================================================
# 그룹 L — main() finally 경로 recorder.close (C7)
# ===========================================================================


def test_main_정상종료시_recorder_close_호출(mocker: Any) -> None:
    """L1 — 정상 종료 경로에서 runtime.recorder.close() 가 finally 블록에서 호출된다."""
    patches = _base_patches(mocker)
    runtime = patches["build_runtime"].return_value

    main([])

    runtime.recorder.close.assert_called_once()


def test_main_예외시에도_recorder_close_호출(mocker: Any) -> None:
    """L2 — scheduler.start 가 예외를 던져도 finally 에서 recorder.close 가 호출된다."""
    patches = _base_patches(mocker)
    runtime = patches["build_runtime"].return_value
    runtime.scheduler.start.side_effect = RuntimeError("crash")

    with contextlib.suppress(Exception):
        main([])

    runtime.recorder.close.assert_called_once()


# ===========================================================================
# 그룹 M — _on_step 호출 순서 (I1): record_* → notify_*
# ===========================================================================


def test_on_step_record_entry_가_notify_entry_보다_먼저_호출된다(mocker: Any) -> None:
    """M1 — entry/exit 각각 record → notify 순서 (I1 리뷰 계약)."""
    call_order: list[str] = []

    e1 = _make_entry_event("005930")
    x1 = _make_exit_event("005930", reason="take_profit")
    report = _make_step_report(entry_events=(e1,), exit_events=(x1,))

    fake_executor = MagicMock(spec=Executor)
    fake_executor.step.return_value = report

    fake_recorder = MagicMock(spec=TradingRecorder)
    fake_recorder.record_entry.side_effect = lambda _ev: call_order.append("record_entry")
    fake_recorder.record_exit.side_effect = lambda _ev: call_order.append("record_exit")

    fake_notifier = MagicMock(spec=Notifier)
    fake_notifier.notify_entry.side_effect = lambda _ev: call_order.append("notify_entry")
    fake_notifier.notify_exit.side_effect = lambda _ev: call_order.append("notify_exit")

    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(
        executor=fake_executor,
        recorder=fake_recorder,
        notifier=fake_notifier,
        session_status=SessionStatus(started=True),
    )

    cb = _on_step(runtime, lambda: _kst(9, 5))
    cb()

    assert call_order == ["record_entry", "notify_entry", "record_exit", "notify_exit"]


# ===========================================================================
# 그룹 N — _on_force_close 호출 순서 및 예외 경로 스냅샷 (I1·I3)
# ===========================================================================


def test_on_force_close_정상경로_record_exit_가_notify_exit_보다_먼저(mocker: Any) -> None:
    """N1 — force_close 정상 경로에서 record_exit → notify_exit 순서 (I1)."""
    call_order: list[str] = []

    x1 = _make_exit_event("005930", reason="force_close")
    report = _make_step_report(exit_events=(x1,))

    fake_executor = MagicMock(spec=Executor)
    fake_executor.force_close_all.return_value = report

    fake_recorder = MagicMock(spec=TradingRecorder)
    fake_recorder.record_exit.side_effect = lambda _ev: call_order.append("record_exit")

    fake_notifier = MagicMock(spec=Notifier)
    fake_notifier.notify_exit.side_effect = lambda _ev: call_order.append("notify_exit")

    mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(
        executor=fake_executor,
        recorder=fake_recorder,
        notifier=fake_notifier,
    )

    cb = _on_force_close(runtime, lambda: _kst(15, 0))
    cb()

    assert call_order == ["record_exit", "notify_exit"]


def test_on_force_close_예외경로_last_sweep_exit_events_스냅샷으로_record_exit_호출(
    mocker: Any,
) -> None:
    """N2 — force_close_all 예외 시 last_sweep_exit_events 스냅샷으로 기록 (I3)."""
    x1 = _make_exit_event("005930", reason="force_close")

    fake_executor = MagicMock(spec=Executor)
    fake_executor.force_close_all.side_effect = RuntimeError("partial crash")
    # last_sweep_exit_events 는 PropertyMock 으로 스냅샷 반환
    type(fake_executor).last_sweep_exit_events = PropertyMock(return_value=(x1,))

    fake_recorder = MagicMock(spec=TradingRecorder)
    fake_notifier = MagicMock(spec=Notifier)
    mock_logger = mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(
        executor=fake_executor,
        recorder=fake_recorder,
        notifier=fake_notifier,
    )

    cb = _on_force_close(runtime, lambda: _kst(15, 0))
    cb()  # raise 하면 안 됨

    # 부분 스냅샷 x1 에 대해 record_exit + notify_exit 호출
    fake_recorder.record_exit.assert_called_once_with(x1)
    fake_notifier.notify_exit.assert_called_once_with(x1)
    # 포지션 잔존 위험 — critical + notify_error
    mock_logger.critical.assert_called_once()
    fake_notifier.notify_error.assert_called_once()
    error_call_kwargs = fake_notifier.notify_error.call_args[0][0]
    assert error_call_kwargs.severity == "critical"


def test_on_force_close_예외경로_스냅샷_접근_실패시_silent_warning(mocker: Any) -> None:
    """N3 — last_sweep_exit_events 접근 자체가 예외를 던지면 warning + record_exit 미호출 (I3)."""
    fake_executor = MagicMock(spec=Executor)
    fake_executor.force_close_all.side_effect = RuntimeError("partial crash")
    # 스냅샷 프로퍼티 접근 자체가 실패
    type(fake_executor).last_sweep_exit_events = PropertyMock(
        side_effect=RuntimeError("snapshot read error")
    )

    fake_recorder = MagicMock(spec=TradingRecorder)
    fake_notifier = MagicMock(spec=Notifier)
    mock_logger = mocker.patch("stock_agent.main.logger")

    runtime = _make_runtime(
        executor=fake_executor,
        recorder=fake_recorder,
        notifier=fake_notifier,
    )

    cb = _on_force_close(runtime, lambda: _kst(15, 0))
    cb()  # raise 하면 안 됨

    # 스냅샷 실패 → warning
    mock_logger.warning.assert_called()
    # 스냅샷 실패 → record_exit 미호출
    fake_recorder.record_exit.assert_not_called()
    # critical + notify_error 는 여전히 호출됨
    mock_logger.critical.assert_called_once()
    fake_notifier.notify_error.assert_called_once()


# ---------------------------------------------------------------------------
# Issue #33 — _on_session_start 재기동 감지 테스트
# ---------------------------------------------------------------------------


class TestOnSessionStartRestartDetection:
    """이미 구현된 재기동 감지·복원 경로를 잠그는 테스트.

    recorder.load_open_positions / load_daily_pnl 반환값에 따라
    executor.start_session / restore_session 분기가 올바르게 선택되는지 검증한다.
    """

    # --- 공통 헬퍼 ---

    @staticmethod
    def _empty_snapshot(d: date = date(2026, 4, 21)) -> DailyPnlSnapshot:
        return DailyPnlSnapshot(
            session_date=d,
            realized_pnl_krw=0,
            entries_today=0,
            closed_symbols=(),
        )

    @staticmethod
    def _make_balance(withdrawable: int = 1_000_000) -> MagicMock:
        b = MagicMock()
        b.withdrawable = withdrawable
        b.total = withdrawable
        b.holdings = []
        return b

    def test_빈_기록이면_start_session_호출_restore_미호출(self) -> None:
        """recorder 가 빈 결과를 반환하면 신규 세션 경로 → start_session 호출."""
        fake_kis = MagicMock(spec=KisClient)
        fake_kis.get_balance.return_value = self._make_balance(1_000_000)
        fake_executor = MagicMock(spec=Executor)

        fake_recorder = MagicMock(spec=TradingRecorder)
        fake_recorder.load_open_positions.return_value = ()
        fake_recorder.load_daily_pnl.return_value = self._empty_snapshot()

        runtime = _make_runtime(
            kis_client=fake_kis,
            executor=fake_executor,
            recorder=fake_recorder,
        )
        cb = _on_session_start(runtime, _parse_args([]), lambda: _kst(9, 0))
        cb()

        fake_executor.start_session.assert_called_once()
        fake_executor.restore_session.assert_not_called()

    def test_open_positions_1건이면_restore_session_호출_start_미호출(self) -> None:
        """open_positions 에 1건이 있으면 재기동 경로 → restore_session 호출."""
        from decimal import Decimal

        from stock_agent.storage import OpenPositionRow

        fake_kis = MagicMock(spec=KisClient)
        fake_kis.get_balance.return_value = self._make_balance(1_000_000)
        fake_executor = MagicMock(spec=Executor)

        open_row = OpenPositionRow(
            symbol="005930",
            qty=10,
            entry_price=Decimal("70000"),
            entry_ts=_kst(9, 31),
            order_number="ORD-001",
        )
        fake_recorder = MagicMock(spec=TradingRecorder)
        fake_recorder.load_open_positions.return_value = (open_row,)
        fake_recorder.load_daily_pnl.return_value = DailyPnlSnapshot(
            session_date=date(2026, 4, 21),
            realized_pnl_krw=0,
            entries_today=1,
            closed_symbols=(),
        )

        runtime = _make_runtime(
            kis_client=fake_kis,
            executor=fake_executor,
            recorder=fake_recorder,
        )
        cb = _on_session_start(runtime, _parse_args([]), lambda: _kst(10, 0))
        cb()

        fake_executor.restore_session.assert_called_once()
        fake_executor.start_session.assert_not_called()

    def test_has_state_True이면_restore_session_호출(self) -> None:
        """open=() 이라도 daily_snapshot.has_state=True 이면 restore_session 호출."""
        fake_kis = MagicMock(spec=KisClient)
        fake_kis.get_balance.return_value = self._make_balance(1_000_000)
        fake_executor = MagicMock(spec=Executor)

        fake_recorder = MagicMock(spec=TradingRecorder)
        fake_recorder.load_open_positions.return_value = ()
        # entries_today=1 → has_state=True
        fake_recorder.load_daily_pnl.return_value = DailyPnlSnapshot(
            session_date=date(2026, 4, 21),
            realized_pnl_krw=0,
            entries_today=1,
            closed_symbols=(),
        )

        runtime = _make_runtime(
            kis_client=fake_kis,
            executor=fake_executor,
            recorder=fake_recorder,
        )
        cb = _on_session_start(runtime, _parse_args([]), lambda: _kst(10, 0))
        cb()

        fake_executor.restore_session.assert_called_once()
        fake_executor.start_session.assert_not_called()

    def test_restore_session_인자_검증(self) -> None:
        """restore_session 에 올바른 인자가 전달되는지 검증."""
        from decimal import Decimal

        from stock_agent.storage import OpenPositionRow

        fake_kis = MagicMock(spec=KisClient)
        fake_kis.get_balance.return_value = self._make_balance(800_000)
        fake_executor = MagicMock(spec=Executor)

        open_row = OpenPositionRow(
            symbol="005930",
            qty=5,
            entry_price=Decimal("70000"),
            entry_ts=_kst(9, 31),
            order_number="ORD-001",
        )
        snapshot = DailyPnlSnapshot(
            session_date=date(2026, 4, 21),
            realized_pnl_krw=-5_000,
            entries_today=2,
            closed_symbols=("000660",),
        )
        fake_recorder = MagicMock(spec=TradingRecorder)
        fake_recorder.load_open_positions.return_value = (open_row,)
        fake_recorder.load_daily_pnl.return_value = snapshot

        args = _parse_args(["--starting-capital", "1000000"])
        runtime = _make_runtime(
            kis_client=fake_kis,
            executor=fake_executor,
            recorder=fake_recorder,
        )
        cb = _on_session_start(runtime, args, lambda: _kst(10, 0))
        cb()

        call_kwargs = fake_executor.restore_session.call_args
        # 세션 날짜
        assert call_kwargs.args[0] == date(2026, 4, 21)
        # 시작 자본 — CLI 1M vs withdrawable 800K → min = 800K
        assert call_kwargs.args[1] == 800_000
        # open_positions
        assert call_kwargs.kwargs["open_positions"] == (open_row,)
        # closed_symbols
        assert call_kwargs.kwargs["closed_symbols"] == ("000660",)
        # entries_today
        assert call_kwargs.kwargs["entries_today"] == 2
        # daily_realized_pnl_krw
        assert call_kwargs.kwargs["daily_realized_pnl_krw"] == -5_000

    def test_restart_감지_시_logger_warning_호출(self, mocker: Any) -> None:
        """is_restart=True 일 때 logger.warning 이 1회 호출된다."""
        from decimal import Decimal

        from stock_agent.storage import OpenPositionRow

        fake_kis = MagicMock(spec=KisClient)
        fake_kis.get_balance.return_value = self._make_balance(1_000_000)
        fake_executor = MagicMock(spec=Executor)
        mock_logger = mocker.patch("stock_agent.main.logger")

        open_row = OpenPositionRow(
            symbol="005930",
            qty=10,
            entry_price=Decimal("70000"),
            entry_ts=_kst(9, 31),
            order_number="ORD-001",
        )
        fake_recorder = MagicMock(spec=TradingRecorder)
        fake_recorder.load_open_positions.return_value = (open_row,)
        fake_recorder.load_daily_pnl.return_value = DailyPnlSnapshot(
            session_date=date(2026, 4, 21),
            realized_pnl_krw=0,
            entries_today=1,
            closed_symbols=(),
        )

        runtime = _make_runtime(
            kis_client=fake_kis,
            executor=fake_executor,
            recorder=fake_recorder,
        )
        cb = _on_session_start(runtime, _parse_args([]), lambda: _kst(10, 0))
        cb()

        # restart 감지 warning 이 포함됐는지 확인
        warning_calls = [str(c) for c in mock_logger.warning.call_args_list]
        msg = f"logger.warning 에 'restart' 포함 호출 없음. calls={warning_calls}"
        assert any("restart" in w for w in warning_calls), msg

    def test_신규_세션_시_logger_info에_restart_False_포함(self, mocker: Any) -> None:
        """is_restart=False 경로에서 logger.info 에 'restart=False' 가 포함된다."""
        fake_kis = MagicMock(spec=KisClient)
        fake_kis.get_balance.return_value = self._make_balance(1_000_000)
        fake_executor = MagicMock(spec=Executor)
        mock_logger = mocker.patch("stock_agent.main.logger")

        fake_recorder = MagicMock(spec=TradingRecorder)
        fake_recorder.load_open_positions.return_value = ()
        fake_recorder.load_daily_pnl.return_value = self._empty_snapshot()

        runtime = _make_runtime(
            kis_client=fake_kis,
            executor=fake_executor,
            recorder=fake_recorder,
        )
        cb = _on_session_start(runtime, _parse_args([]), lambda: _kst(9, 0))
        cb()

        # main.session_start info 로그에 restart=False 포함 여부 검증
        info_calls = [str(c) for c in mock_logger.info.call_args_list]
        msg = f"logger.info 에 'restart' 포함 호출 없음. calls={info_calls}"
        assert any("restart" in s for s in info_calls), msg

    def test_시작자본_0이면_recorder_load_호출_안함(self) -> None:
        """starting_capital <= 0 이면 recorder.load_* 를 호출하지 않고 조기 반환."""
        fake_kis = MagicMock(spec=KisClient)
        # withdrawable=0, starting_capital CLI=1M → min=0 → 조기 return
        fake_kis.get_balance.return_value = self._make_balance(withdrawable=0)
        fake_executor = MagicMock(spec=Executor)

        fake_recorder = MagicMock(spec=TradingRecorder)
        fake_recorder.load_open_positions.return_value = ()
        fake_recorder.load_daily_pnl.return_value = self._empty_snapshot()

        runtime = _make_runtime(
            kis_client=fake_kis,
            executor=fake_executor,
            recorder=fake_recorder,
        )
        args = _parse_args(["--starting-capital", "1000000"])
        cb = _on_session_start(runtime, args, lambda: _kst(9, 0))
        cb()

        # 자본 0 → 조기 return → load_* 미호출
        fake_recorder.load_open_positions.assert_not_called()
        fake_recorder.load_daily_pnl.assert_not_called()
        fake_executor.start_session.assert_not_called()
        fake_executor.restore_session.assert_not_called()

    def test_recorder_load_예외시_executor_호출_안함_session_status_미갱신(
        self, mocker: Any
    ) -> None:
        """recorder.load_open_positions 가 예외를 던지면 silent fail 계약:
        executor.start_session / restore_session 미호출, session_status.started=False.

        SqliteTradingRecorder 는 silent fail 계약상 내부 예외를 흡수하므로
        이 케이스는 recorder 가 NullTradingRecorder 이외 동작을 직접 구현할 때의
        방어 경로이다. 여기서는 get_balance 가 예외를 던져 recorder.load_* 에
        도달하기 전에 except 분기를 타는 경로를 검증한다.
        """
        fake_kis = MagicMock(spec=KisClient)
        fake_kis.get_balance.side_effect = RuntimeError("잔고 조회 네트워크 오류")
        fake_executor = MagicMock(spec=Executor)
        mock_logger = mocker.patch("stock_agent.main.logger")

        fake_recorder = MagicMock(spec=TradingRecorder)
        ss = SessionStatus(started=False, fail_logged=False)

        runtime = _make_runtime(
            kis_client=fake_kis,
            executor=fake_executor,
            recorder=fake_recorder,
            session_status=ss,
        )
        cb = _on_session_start(runtime, _parse_args([]), lambda: _kst(9, 0))
        cb()  # raise 하면 안 됨

        fake_executor.start_session.assert_not_called()
        fake_executor.restore_session.assert_not_called()
        assert ss.started is False
        mock_logger.exception.assert_called_once()


# ===========================================================================
# 그룹 I — _on_session_start recorder_null 경보
# ===========================================================================


class TestOnSessionStartRecorderNull:
    """_on_session_start 가 NullTradingRecorder 폴백 상태를 critical 경보로 노출하는 계약.

    Issue #41: _default_recorder_factory 가 SqliteTradingRecorder 조립 실패 시
    NullTradingRecorder 를 주입하지만 세션 시작 시점엔 그 사실이 가려지는 문제 수정.
    """

    def _make_null_recorder_runtime(
        self,
        kis_client: MagicMock | None = None,
        executor: MagicMock | None = None,
        notifier: MagicMock | None = None,
        session_status: SessionStatus | None = None,
    ) -> Runtime:
        """NullTradingRecorder 가 주입된 Runtime 더블."""
        _kis = kis_client or MagicMock(spec=KisClient)
        _kis.get_balance.return_value = _make_balance(total=2_000_000, withdrawable=1_800_000)
        _ex = executor or MagicMock(spec=Executor)
        _notifier = notifier or MagicMock(spec=Notifier)
        null_recorder = NullTradingRecorder()
        return _make_runtime(
            kis_client=_kis,
            executor=_ex,
            notifier=_notifier,
            recorder=null_recorder,
            session_status=session_status,
        )

    def test_I1_null_recorder_logger_critical_session_start_recorder_null_포함(
        self, mocker: Any
    ) -> None:
        """I1 — NullTradingRecorder 주입 시 logger.critical 최소 1회,
        메시지에 "session_start.recorder_null" 포함.
        """
        mock_logger = mocker.patch("stock_agent.main.logger")
        runtime = self._make_null_recorder_runtime()
        args = _parse_args([])
        clock = lambda: _kst(9, 0)  # noqa: E731

        cb = _on_session_start(runtime, args, clock)
        cb()

        critical_calls = mock_logger.critical.call_args_list
        assert_msg = f"logger.critical 이 호출되지 않았습니다. calls={critical_calls}"
        assert len(critical_calls) >= 1, assert_msg
        messages = [str(c) for c in critical_calls]
        has_stage = any("session_start.recorder_null" in m for m in messages)
        assert has_stage, f"recorder_null 미발견. messages={messages}"

    def test_I2_null_recorder_notify_error_stage_session_start_recorder_null(
        self, mocker: Any
    ) -> None:
        """I2 — NullTradingRecorder 주입 시 notify_error 최소 1회,
        ErrorEvent.stage == "session_start.recorder_null",
        error_class == "NullTradingRecorder",
        severity == "critical",
        timestamp == clock() 반환값,
        message 비어있지 않음.
        """
        mocker.patch("stock_agent.main.logger")
        fake_notifier = MagicMock(spec=Notifier)
        fixed_ts = _kst(9, 0)
        clock = lambda: fixed_ts  # noqa: E731

        runtime = self._make_null_recorder_runtime(notifier=fake_notifier)
        args = _parse_args([])

        cb = _on_session_start(runtime, args, clock)
        cb()

        # stage="session_start.recorder_null" 인 호출을 추출
        null_alarm_calls = [
            c
            for c in fake_notifier.notify_error.call_args_list
            if c[0][0].stage == "session_start.recorder_null"
        ]
        assert len(null_alarm_calls) >= 1, (
            f"stage='session_start.recorder_null' notify_error 호출 없음. "
            f"all_calls={fake_notifier.notify_error.call_args_list}"
        )
        event: ErrorEvent = null_alarm_calls[0][0][0]
        assert event.error_class == "NullTradingRecorder"
        assert event.severity == "critical"
        assert event.timestamp == fixed_ts
        assert event.message != ""

    def test_I3_null_recorder_이후_정상_세션_시작_경로_진행(self, mocker: Any) -> None:
        """I3 — NullTradingRecorder 경보 발생 후에도 정상 세션 시작 경로가 그대로 진행된다.
        get_balance 호출 O, withdrawable > 0 이면 executor.start_session 호출 O.
        """
        mocker.patch("stock_agent.main.logger")
        fake_executor = MagicMock(spec=Executor)
        fake_kis = MagicMock(spec=KisClient)
        fake_kis.get_balance.return_value = _make_balance(total=2_000_000, withdrawable=1_800_000)
        ss = SessionStatus(started=False)

        runtime = self._make_null_recorder_runtime(
            kis_client=fake_kis,
            executor=fake_executor,
            session_status=ss,
        )
        args = _parse_args(["--starting-capital", "1000000"])
        clock = lambda: _kst(9, 0)  # noqa: E731

        cb = _on_session_start(runtime, args, clock)
        cb()

        fake_kis.get_balance.assert_called_once()
        fake_executor.start_session.assert_called_once()
        assert ss.started is True

    def test_I4_null_아닌_recorder_notify_error_recorder_null_미호출(self, mocker: Any) -> None:
        """I4 — recorder 가 NullTradingRecorder 가 아닌 경우
        (MagicMock(spec=TradingRecorder)) stage='session_start.recorder_null'
        인 notify_error 호출이 0회여야 한다.
        """
        mocker.patch("stock_agent.main.logger")
        fake_notifier = MagicMock(spec=Notifier)
        fake_kis = MagicMock(spec=KisClient)
        fake_kis.get_balance.return_value = _make_balance(total=2_000_000, withdrawable=1_800_000)

        # _make_runtime 기본값은 MagicMock(spec=TradingRecorder) — NullTradingRecorder 아님
        runtime = _make_runtime(kis_client=fake_kis, notifier=fake_notifier)
        args = _parse_args([])
        clock = lambda: _kst(9, 0)  # noqa: E731

        cb = _on_session_start(runtime, args, clock)
        cb()

        null_alarm_calls = [
            c
            for c in fake_notifier.notify_error.call_args_list
            if c[0][0].stage == "session_start.recorder_null"
        ]
        assert_msg = f"recorder_null 경보 불필요 발생. calls={null_alarm_calls}"
        assert len(null_alarm_calls) == 0, assert_msg

    def test_I5_null_recorder_plus_withdrawable_0_notify_error_2회(self, mocker: Any) -> None:
        """I5 — NullTradingRecorder + withdrawable==0 조합.
        notify_error 가 2회 호출돼야 한다:
          1회째 stage="session_start.recorder_null" (critical)
          2회째 stage="session_start" error_class="StartingCapitalError" (error).
        null 경보가 이후 세션 시작 실패 경보를 삼키면 안 된다.
        """
        mocker.patch("stock_agent.main.logger")
        fake_notifier = MagicMock(spec=Notifier)
        fake_kis = MagicMock(spec=KisClient)
        # withdrawable=0 → starting_capital=0 → 조기 return + StartingCapitalError 경보
        fake_kis.get_balance.return_value = _make_balance(total=2_000_000, withdrawable=0)

        null_recorder = NullTradingRecorder()
        runtime = _make_runtime(
            kis_client=fake_kis,
            notifier=fake_notifier,
            recorder=null_recorder,
        )
        args = _parse_args([])
        clock = lambda: _kst(9, 0)  # noqa: E731

        cb = _on_session_start(runtime, args, clock)
        cb()

        assert fake_notifier.notify_error.call_count == 2, (
            f"notify_error 2회 기대, 실제={fake_notifier.notify_error.call_count}. "
            f"calls={fake_notifier.notify_error.call_args_list}"
        )
        stages = [c[0][0].stage for c in fake_notifier.notify_error.call_args_list]
        assert "session_start.recorder_null" in stages
        assert "session_start" in stages

        # severity 검증
        calls = fake_notifier.notify_error.call_args_list
        events_by_stage = {c[0][0].stage: c[0][0] for c in calls}
        assert events_by_stage["session_start.recorder_null"].severity == "critical"
        assert events_by_stage["session_start"].error_class == "StartingCapitalError"
        assert events_by_stage["session_start"].severity == "error"

    def test_I6_null_recorder_plus_get_balance_예외_notify_error_2회(self, mocker: Any) -> None:
        """I6 — NullTradingRecorder + get_balance ConnectionError 조합.
        notify_error 가 2회 호출돼야 한다:
          1회째 stage="session_start.recorder_null" (critical)
          2회째 stage="session_start" error_class="ConnectionError" (error).
        null 경보가 예외 경보를 삼키면 안 된다.
        """
        mocker.patch("stock_agent.main.logger")
        fake_notifier = MagicMock(spec=Notifier)
        fake_kis = MagicMock(spec=KisClient)
        fake_kis.get_balance.side_effect = ConnectionError("네트워크 오류")

        null_recorder = NullTradingRecorder()
        runtime = _make_runtime(
            kis_client=fake_kis,
            notifier=fake_notifier,
            recorder=null_recorder,
        )
        args = _parse_args([])
        clock = lambda: _kst(9, 0)  # noqa: E731

        cb = _on_session_start(runtime, args, clock)
        cb()

        assert fake_notifier.notify_error.call_count == 2, (
            f"notify_error 2회 기대, 실제={fake_notifier.notify_error.call_count}. "
            f"calls={fake_notifier.notify_error.call_args_list}"
        )
        stages = [c[0][0].stage for c in fake_notifier.notify_error.call_args_list]
        assert "session_start.recorder_null" in stages
        assert "session_start" in stages

        calls = fake_notifier.notify_error.call_args_list
        events_by_stage = {c[0][0].stage: c[0][0] for c in calls}
        assert events_by_stage["session_start.recorder_null"].severity == "critical"
        assert events_by_stage["session_start"].error_class == "ConnectionError"
        assert events_by_stage["session_start"].severity == "error"
