"""Executor — 신호 → 주문 → 체결 추적 → 상태 동기화 루프.

책임 범위
- `ORBStrategy` 가 생성한 시그널을 받아 `KisClient` (또는 드라이런 더블) 로
  주문을 제출하고 체결 확정까지 폴링한다.
- `RiskManager` 와 1:1 동기화한다 — 진입 승인은 `evaluate_entry` 게이팅 통과
  분만, 체결 확정은 `record_entry`/`record_exit` 로 통지.
- 매 `step(now)` 마다 `BalanceProvider.get_balance()` 와 `RiskManager`
  활성 포지션을 비교해 불일치 시 신규 진입을 차단(halt) 한다 — 자동 복구
  없이 운영자 개입을 강제.

범위 제외 (의도적 defer)
- 항목 정본은 모듈 CLAUDE.md "범위 제외 (의도적 defer)" 섹션 — 두 곳 동기화
  부담을 피하기 위해 본 docstring 에서는 중복 나열하지 않는다. 핵심: 스케줄링·
  알림·영속화·부분체결·체결조회 정확도 향상은 모두 후속 PR.

에러 정책 (broker/strategy/risk 와 동일 기조)
- `RuntimeError` 는 전파 — 입력 오류 (naive datetime, 세션 미시작 등).
- `RiskManagerError` 는 전파 — 호출 순서 위반.
- `ExecutorError` — 체결 타임아웃·전략 무결성 오류·KIS 백오프 한계 초과.
  운영자 개입 — 자동 재시도 금지.
- `KisClientError` 는 `_with_backoff` 안에서 좁은 지수 백오프(기본 100→200
  →400 ms, 최대 3회 재시도) 로 흡수. 한계 초과 시 `ExecutorError` 로 승격
  (`__cause__` 보존).
- generic `except Exception` 금지. `assert` 대신 명시적 예외.

스레드 모델
- 단일 프로세스 전용. `step` / `force_close_all` / `reconcile` 동시 호출 금지
  (broker/strategy/risk/data 와 동일 기조).
"""

from __future__ import annotations

import time as _time_module
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType
from typing import Any, Protocol

from loguru import logger

from stock_agent.backtest.costs import (
    buy_commission,
    buy_fill_price,
    sell_commission,
    sell_fill_price,
    sell_tax,
)
from stock_agent.broker import (
    BalanceSnapshot,
    KisClientError,
    OrderTicket,
    PendingOrder,
)
from stock_agent.broker.kis_client import KisClient
from stock_agent.data import MinuteBar
from stock_agent.risk import PositionRecord, RiskManager
from stock_agent.strategy import EntrySignal, ExitReason, ExitSignal, ORBStrategy, Signal

KST = timezone(timedelta(hours=9))

ClockFn = Callable[[], datetime]
SleepFn = Callable[[float], None]


class ExecutorError(Exception):
    """Executor 처리 중 발생한 예외.

    체결 타임아웃·전략 무결성 오류(진입 기록 없는 청산)·KIS 백오프 한계
    초과를 포함한다. 운영자 개입을 전제로 자동 재시도하지 않는다. 원본
    예외는 `__cause__` 로 보존된다 (`raise ... from e`).
    """


# ---- Protocol 의존성 역전 -------------------------------------------------


class OrderSubmitter(Protocol):
    """주문 제출·미체결 조회 경계.

    `KisClient` 직접 의존을 끊어 (a) 드라이런 모드를 분기 없이 표현하고
    (b) 단위 테스트에서 KIS 접촉 없이 검증할 수 있게 한다.
    """

    def submit_buy(self, symbol: str, qty: int) -> OrderTicket: ...

    def submit_sell(self, symbol: str, qty: int) -> OrderTicket: ...

    def get_pending_orders(self) -> list[PendingOrder]: ...


class BalanceProvider(Protocol):
    """잔고 조회 경계."""

    def get_balance(self) -> BalanceSnapshot: ...


class BarSource(Protocol):
    """분봉 조회 경계. `RealtimeDataStore` 가 자연스럽게 만족."""

    def get_minute_bars(self, symbol: str) -> list[MinuteBar]: ...


class OpenPositionInput(Protocol):
    """Issue #33 — `Executor.restore_session` 이 받는 오픈 포지션 구조적 타입.

    `storage.OpenPositionRow` 가 자연스럽게 만족한다. Protocol 로 둠으로써
    `execution` → `storage` 역방향 import 를 피한다 (`storage/db.py` 가 이미
    `execution.EntryEvent/ExitEvent` 를 import 하므로 순환 회피).
    """

    @property
    def symbol(self) -> str: ...

    @property
    def qty(self) -> int: ...

    @property
    def entry_price(self) -> Decimal: ...

    @property
    def entry_ts(self) -> datetime: ...

    @property
    def order_number(self) -> str: ...


# ---- 라이브 어댑터 -------------------------------------------------------


class LiveOrderSubmitter:
    """`KisClient` 위임 어댑터. 시장가 주문(`price=None`) 전제."""

    def __init__(self, kis_client: KisClient) -> None:
        self._kis = kis_client

    def submit_buy(self, symbol: str, qty: int) -> OrderTicket:
        return self._kis.place_buy(symbol, qty)

    def submit_sell(self, symbol: str, qty: int) -> OrderTicket:
        return self._kis.place_sell(symbol, qty)

    def get_pending_orders(self) -> list[PendingOrder]:
        return self._kis.get_pending_orders()


class LiveBalanceProvider:
    """`KisClient` 위임 어댑터."""

    def __init__(self, kis_client: KisClient) -> None:
        self._kis = kis_client

    def get_balance(self) -> BalanceSnapshot:
        return self._kis.get_balance()


class DryRunOrderSubmitter:
    """드라이런 주문 더블. KIS 접촉 0.

    `submit_buy`/`submit_sell` 는 `order_number=DRY-NNNN` 형태의 가짜
    `OrderTicket` 을 반환하고, `get_pending_orders` 는 항상 빈 리스트를
    돌려 `_wait_fill` 이 즉시 통과하게 한다. RiskManager 와의 시그널·체결
    동기화는 그대로 유지되므로 모의투자 사전 검증·시뮬레이션 회귀에 사용한다.
    """

    def __init__(self) -> None:
        self._counter: int = 0

    def _next(self, symbol: str, qty: int, side: str) -> OrderTicket:
        self._counter += 1
        return OrderTicket(
            order_number=f"DRY-{self._counter:04d}",
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            qty=qty,
            price=None,
            submitted_at=datetime.now(KST),
        )

    def submit_buy(self, symbol: str, qty: int) -> OrderTicket:
        logger.info(
            "executor.dry_run.submit_buy symbol={symbol} qty={qty}",
            symbol=symbol,
            qty=qty,
        )
        return self._next(symbol, qty, "buy")

    def submit_sell(self, symbol: str, qty: int) -> OrderTicket:
        logger.info(
            "executor.dry_run.submit_sell symbol={symbol} qty={qty}",
            symbol=symbol,
            qty=qty,
        )
        return self._next(symbol, qty, "sell")

    def get_pending_orders(self) -> list[PendingOrder]:
        return []


# ---- 설정 / DTO ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExecutorConfig:
    """Executor 파라미터.

    기본값은 백테스트 엔진(`backtest/engine.py`) 의 비용 모델과 일치한다 —
    실전 코드와 시뮬레이션 코드가 동일 비용 가정을 공유해야 백테스트 결과의
    실전 괴리를 추적 가능하다.

    Raises:
        RuntimeError: 비율 음수, timeout/interval/initial 0 이하,
            backoff_max_attempts 0 이하.
    """

    cash_buffer_pct: Decimal = Decimal("0.005")
    """`withdrawable` 의 0.5% 를 수수료/세금 버퍼로 보수 차감해 RiskManager 에 전달."""

    order_fill_timeout_s: float = 30.0
    """시장가 체결 대기 타임아웃(초). 초과 시 `ExecutorError`."""

    order_poll_interval_s: float = 0.5
    """미체결 폴링 주기(초)."""

    slippage_rate: Decimal = Decimal("0.001")
    """체결가 추정 슬리피지 — 매수 +0.1%, 매도 -0.1%. 백테스트와 동일."""

    commission_rate: Decimal = Decimal("0.00015")
    """수수료율 — 매수·매도 대칭 0.015% (한투 비대면). 백테스트와 동일."""

    sell_tax_rate: Decimal = Decimal("0.0018")
    """거래세율 — 매도 0.18% (KRX). 백테스트와 동일."""

    backoff_max_attempts: int = 3
    """KisClientError 재시도 최대 횟수. 첫 시도 + max_attempts 재시도 = 총 max_attempts+1 회."""

    backoff_initial_s: float = 0.1
    """백오프 초기 지연(초). 지수 증가: initial × 2^attempt — 0.1, 0.2, 0.4 ..."""

    def __post_init__(self) -> None:
        for name, val in (
            ("cash_buffer_pct", self.cash_buffer_pct),
            ("slippage_rate", self.slippage_rate),
            ("commission_rate", self.commission_rate),
            ("sell_tax_rate", self.sell_tax_rate),
        ):
            if val < 0:
                raise RuntimeError(f"{name} 는 0 이상이어야 합니다 (got={val})")
        if self.cash_buffer_pct >= 1:
            # 1 이상이면 available_cash 가 0/음수로 떨어져 모든 진입이 거부됨 — 의미 없음.
            raise RuntimeError(
                f"cash_buffer_pct 는 1 미만이어야 합니다 (got={self.cash_buffer_pct})"
            )
        if self.order_fill_timeout_s <= 0:
            raise RuntimeError(
                f"order_fill_timeout_s 는 양수여야 합니다 (got={self.order_fill_timeout_s})"
            )
        if self.order_poll_interval_s <= 0:
            raise RuntimeError(
                f"order_poll_interval_s 는 양수여야 합니다 (got={self.order_poll_interval_s})"
            )
        if self.backoff_max_attempts <= 0:
            raise RuntimeError(
                f"backoff_max_attempts 는 양의 정수여야 합니다 (got={self.backoff_max_attempts})"
            )
        if self.backoff_initial_s <= 0:
            raise RuntimeError(
                f"backoff_initial_s 는 양수여야 합니다 (got={self.backoff_initial_s})"
            )


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    """`reconcile()` 의 결과.

    `mismatch_symbols` 가 비어있지 않으면 `Executor._halt = True` 가 설정되어
    이후 EntrySignal 은 자동 스킵된다 (ExitSignal 은 정상 처리).

    `broker_holdings` / `risk_holdings` 는 `MappingProxyType` 으로 래핑된
    읽기 전용 뷰다 — frozen dataclass 가 dict 의 내부 mutation 까지는 막지
    못해 별도 보호. setitem 시도 시 `TypeError`.
    """

    broker_holdings: Mapping[str, int]
    risk_holdings: Mapping[str, int]
    mismatch_symbols: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EntryEvent:
    """체결 확정된 진입 이벤트 — notifier / storage 소비용.

    `fill_price` 는 슬리피지 반영된 추정 체결가(`backtest.costs.buy_fill_price`
    와 동일 계산), `ref_price` 는 시그널 생성 당시 참고가. timestamp 는 `step`
    에 주입된 `now` 인자로 KST aware datetime. `order_number` 는 브로커가
    반환한 주문번호(`OrderTicket.order_number`) 로, 감사 추적·storage 원장
    PK 용도. 드라이런은 `DRY-NNNN` 형식.
    """

    symbol: str
    qty: int
    fill_price: Decimal
    ref_price: Decimal
    timestamp: datetime
    order_number: str

    def __post_init__(self) -> None:
        if not self.order_number:
            raise RuntimeError("EntryEvent.order_number 는 비어있을 수 없습니다.")
        if self.timestamp.tzinfo is None:
            raise RuntimeError("EntryEvent.timestamp 는 tz-aware datetime 이어야 합니다.")
        if self.qty <= 0:
            raise RuntimeError(f"EntryEvent.qty 는 양수여야 합니다 (got={self.qty}).")
        if self.fill_price <= 0:
            raise RuntimeError(f"EntryEvent.fill_price 는 양수여야 합니다 (got={self.fill_price}).")
        if self.ref_price <= 0:
            raise RuntimeError(f"EntryEvent.ref_price 는 양수여야 합니다 (got={self.ref_price}).")


@dataclass(frozen=True, slots=True)
class ExitEvent:
    """체결 확정된 청산 이벤트 — notifier / storage 소비용.

    `net_pnl_krw` 는 수수료·거래세 반영 순손익(`_compute_net_pnl` 결과와 동일,
    손실은 음수). `reason` 은 `"stop_loss" | "take_profit" | "force_close"` —
    `ExitReason` 재사용(strategy/base.py). 타입이 `ExitReason` 이므로 소비자
    (notifier 등) 는 값 범위 가정을 정적 타입으로 보장받는다 (ADR-0012 후속
    보강 2026-04-21). `order_number` 는 브로커 주문번호로 storage 원장 PK 및
    감사 추적 용도 (ADR-0013).
    """

    symbol: str
    qty: int
    fill_price: Decimal
    reason: ExitReason
    net_pnl_krw: int
    timestamp: datetime
    order_number: str

    def __post_init__(self) -> None:
        if not self.order_number:
            raise RuntimeError("ExitEvent.order_number 는 비어있을 수 없습니다.")
        if self.timestamp.tzinfo is None:
            raise RuntimeError("ExitEvent.timestamp 는 tz-aware datetime 이어야 합니다.")
        if self.qty <= 0:
            raise RuntimeError(f"ExitEvent.qty 는 양수여야 합니다 (got={self.qty}).")
        if self.fill_price <= 0:
            raise RuntimeError(f"ExitEvent.fill_price 는 양수여야 합니다 (got={self.fill_price}).")


@dataclass(frozen=True, slots=True)
class StepReport:
    """`step(now)` 의 결과 — 한 sweep 의 처리 요약.

    `entry_events` / `exit_events` 는 해당 sweep 에서 체결 확정된 진입·청산을
    순서대로 담는다(빈 tuple 기본값 — backward compat). notifier 는 이 tuple
    을 소비해 텔레그램 알림을 푸시한다. 로그(`executor.entry.filled` 등) 는
    감사 추적 목적으로 유지된다 — 알림과 로그는 독립 채널.
    """

    processed_bars: int
    orders_submitted: int
    halted: bool
    reconcile: ReconcileReport
    entry_events: tuple[EntryEvent, ...] = ()
    exit_events: tuple[ExitEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class _OpenLot:
    """Executor 내부 진입 추적 — 청산 시 PnL 계산 입력으로 재사용."""

    entry_price: Decimal
    qty: int


# ---- Executor ------------------------------------------------------------


class Executor:
    """전략·리스크·브로커 오케스트레이션.

    공개 API: `start_session`, `step`, `force_close_all`, `reconcile`,
    `is_halted`. 모든 시각 인자는 KST aware datetime 이어야 한다.

    스레드 모델: 단일 호출자 스레드에서 `step` 을 순차 호출. 동시 호출 금지.
    """

    def __init__(
        self,
        *,
        symbols: tuple[str, ...],
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        bar_source: BarSource,
        order_submitter: OrderSubmitter,
        balance_provider: BalanceProvider,
        config: ExecutorConfig | None = None,
        clock: ClockFn | None = None,
        sleep: SleepFn | None = None,
    ) -> None:
        if not symbols:
            raise RuntimeError("Executor: symbols 는 최소 1개 이상이어야 합니다 (빈 튜플 거부).")
        self._symbols = tuple(symbols)
        self._strategy = strategy
        self._risk_manager = risk_manager
        self._bar_source = bar_source
        self._order_submitter = order_submitter
        self._balance_provider = balance_provider
        self._config = config or ExecutorConfig()
        self._clock: ClockFn = clock or (lambda: datetime.now(KST))
        self._sleep: SleepFn = sleep or _time_module.sleep
        self._last_processed_bar_time: dict[str, datetime] = {}
        self._open_lots: dict[str, _OpenLot] = {}
        self._halt: bool = False
        self._last_reconcile: ReconcileReport | None = None
        self._sweep_entry_events: list[EntryEvent] = []
        self._sweep_exit_events: list[ExitEvent] = []

    # ---- 세션 -----------------------------------------------------------

    def start_session(self, session_date: date, starting_capital_krw: int) -> None:
        """`RiskManager.start_session` 위임 + 내부 처리 마커·_open_lots·halt 리셋.

        새 세션 시작 시 호출. `RiskManager` 가 입력 검증(자본 양수)을 담당한다.
        """
        self._risk_manager.start_session(session_date, starting_capital_krw)
        self._last_processed_bar_time.clear()
        self._open_lots.clear()
        self._halt = False
        self._last_reconcile = None
        logger.info(
            "executor.start_session date={d} capital={c}",
            d=session_date,
            c=starting_capital_krw,
        )

    def restore_session(
        self,
        session_date: date,
        starting_capital_krw: int,
        *,
        open_positions: Sequence[OpenPositionInput],
        closed_symbols: Sequence[str] = (),
        entries_today: int,
        daily_realized_pnl_krw: int,
    ) -> None:
        """Issue #33 — 세션 중간 재기동 시 Executor 상태 복원 (원자성 보장).

        `start_session` 은 "0 으로 리셋" 이지만 본 메서드는 DB 재생 결과를
        직접 주입한다. 실제 코드 순서:

        1. **사전 계산** (상태 변경 0) — `PositionRecord` 변환·`open_symbols`·
           `overlap`·`effective_closed` 도출. 여기서 `RuntimeError` (심볼
           포맷·qty≤0 등) 가 떠도 RiskManager/Executor/Strategy 상태는 변경
           전이라 롤백 불필요.
        2. **RiskManager** — `restore_session` 위임. RM 내부는 입력 검증
           사전 통과 후 원자 대입 구조라 중간 실패 상태가 없다.
        3. **ORBStrategy 루프** — `restore_long_position` · `mark_session_closed`.
           중간 실패 시 `touched_symbols` 로 **부분 복원된 심볼만 `reset_session`**
           으로 제거하고 `RiskManager.start_session` 으로 clean 세션 리셋 후
           `ExecutorError` 로 래핑 전파. 이 경로는 `_on_session_start` 가
           `except Exception` 으로 받아 `session_status.started=False` + `notify_error`.
        4. **커밋** — Executor 로컬(`_open_lots`, `_halt`, `_last_reconcile`,
           `_last_processed_bar_time`) 는 ORB 루프 성공 이후에만 주입. 실패
           시엔 건드리지 않아 이전 세션의 stale 값이 남지만, 상위에서 세션이
           `started=False` 로 마크되므로 다음 영업일 첫 `start_session` 까지
           사용되지 않는다.

        `closed_symbols ∩ open_positions.symbols` 는 무시(open 쪽 우선) — 정상
        경로에선 발생하지 않음. 발생 시 storage 데이터 이상이므로 warning.

        Args:
            session_date: 복원 대상 세션 날짜.
            starting_capital_krw: 세션 시작 자본. `RiskManager` 가 검증.
            open_positions: `storage.load_open_positions` 결과.
            closed_symbols: `storage.load_daily_pnl().closed_symbols`.
            entries_today: 당일 총 진입 횟수 (청산 포함).
            daily_realized_pnl_krw: 당일 실현손익 누계.

        Raises:
            RuntimeError: 사전 검증 실패 (PositionRecord 생성·RiskManager 입력).
                이 시점엔 RM/Executor/Strategy 상태 불변.
            ExecutorError: ORB 복원 루프 중간 실패를 래핑. `__cause__` 에 원본.
                경고 critical 로그 방출 후 RM 은 `start_session` 으로 clean
                리셋, Strategy 는 해당 심볼들만 `reset_session` 으로 제거.
        """
        positions = tuple(open_positions)
        closed = tuple(closed_symbols)
        position_records = [
            PositionRecord(
                symbol=p.symbol,
                entry_price=p.entry_price,
                qty=p.qty,
                entry_ts=p.entry_ts,
            )
            for p in positions
        ]
        open_symbols = {p.symbol for p in positions}
        overlap = open_symbols.intersection(closed)
        if overlap:
            logger.warning(
                "executor.restore_session: closed_symbols 에 open_positions 심볼이 섞여 있음 — "
                "open 을 우선 적용. overlap={overlap}",
                overlap=sorted(overlap),
            )
        effective_closed = tuple(s for s in closed if s not in open_symbols)

        self._risk_manager.restore_session(
            session_date,
            starting_capital_krw,
            open_positions=position_records,
            entries_today=entries_today,
            daily_realized_pnl_krw=daily_realized_pnl_krw,
        )

        touched_symbols: list[str] = []
        try:
            for pos in positions:
                self._strategy.restore_long_position(pos.symbol, pos.entry_price, pos.entry_ts)
                touched_symbols.append(pos.symbol)
            for sym in effective_closed:
                self._strategy.mark_session_closed(sym, session_date)
                touched_symbols.append(sym)
        except Exception as e:  # noqa: BLE001 — 부분 복원 롤백 경로 (CRITICAL 재발 방지)
            self._strategy.reset_session(touched_symbols)
            self._risk_manager.start_session(session_date, starting_capital_krw)
            logger.critical(
                "executor.restore_session 부분 실패 — ORB/RiskManager clean 세션으로 롤백. "
                "touched={touched} error={cls}: {err}",
                touched=touched_symbols,
                cls=e.__class__.__name__,
                err=str(e),
            )
            raise ExecutorError(
                "restore_session 중 ORBStrategy 복원 실패 — clean 세션으로 롤백: "
                f"{e.__class__.__name__}: {e}"
            ) from e

        self._last_processed_bar_time.clear()
        self._open_lots = {
            p.symbol: _OpenLot(entry_price=p.entry_price, qty=p.qty) for p in positions
        }
        self._halt = False
        self._last_reconcile = None
        logger.warning(
            "executor.restore_session date={d} capital={c} open={op} closed={cl} "
            "entries={e} pnl={pnl}",
            d=session_date,
            c=starting_capital_krw,
            op=len(positions),
            cl=len(closed),
            e=entries_today,
            pnl=daily_realized_pnl_krw,
        )

    # ---- 주 루프 --------------------------------------------------------

    def step(self, now: datetime) -> StepReport:
        """1 sweep — 재동기화 + 신규 분봉 처리 + 시각 트리거 처리.

        외부에서 주기적으로 호출 (APScheduler / main.py 책임).
        """
        self._require_aware(now, "now")
        if self._risk_manager.session_date is None:
            raise RuntimeError(
                "Executor.step: 세션이 시작되지 않았습니다. "
                "start_session(session_date, starting_capital_krw) 를 먼저 호출하세요."
            )

        self._sweep_entry_events = []
        self._sweep_exit_events = []
        reconcile_report = self.reconcile()
        processed_bars = 0
        orders_submitted = 0

        for symbol in self._symbols:
            bars = self._bar_source.get_minute_bars(symbol)
            for bar in bars:
                last_seen = self._last_processed_bar_time.get(symbol)
                if last_seen is not None and bar.bar_time <= last_seen:
                    continue
                signals = self._strategy.on_bar(bar)
                processed_bars += 1
                self._last_processed_bar_time[symbol] = bar.bar_time
                orders_submitted += self._process_signals(signals, now)

        time_signals = self._strategy.on_time(now)
        orders_submitted += self._process_signals(time_signals, now)

        return StepReport(
            processed_bars=processed_bars,
            orders_submitted=orders_submitted,
            halted=self.is_halted,
            reconcile=reconcile_report,
            entry_events=tuple(self._sweep_entry_events),
            exit_events=tuple(self._sweep_exit_events),
        )

    def force_close_all(self, now: datetime) -> StepReport:
        """`on_time(now)` 만 처리해 잔존 long 강제청산. 분봉 처리는 생략.

        15:00 KST 시각에 단발성으로 호출하는 경로.
        """
        self._require_aware(now, "now")
        if self._risk_manager.session_date is None:
            raise RuntimeError(
                "Executor.force_close_all: 세션이 시작되지 않았습니다. "
                "start_session 을 먼저 호출하세요."
            )
        self._sweep_entry_events = []
        self._sweep_exit_events = []
        reconcile_report = self.reconcile()
        signals = self._strategy.on_time(now)
        orders_submitted = self._process_signals(signals, now)
        return StepReport(
            processed_bars=0,
            orders_submitted=orders_submitted,
            halted=self.is_halted,
            reconcile=reconcile_report,
            entry_events=tuple(self._sweep_entry_events),
            exit_events=tuple(self._sweep_exit_events),
        )

    # ---- 상태 동기화 ---------------------------------------------------

    def reconcile(self) -> ReconcileReport:
        """잔고 holdings ↔ RiskManager active_positions 비교.

        불일치 발생 시 `_halt = True` + `logger.critical`. 자동 복구 없음 —
        잘못 보정하면 이중 주문·미청산 위험이 더 크다. 다음 `start_session`
        호출 전까지 신규 진입은 차단된다.
        """
        balance = self._with_backoff(self._balance_provider.get_balance)
        broker_holdings = {h.symbol: h.qty for h in balance.holdings}
        risk_holdings = {p.symbol: p.qty for p in self._risk_manager.active_positions}
        all_symbols = sorted(set(broker_holdings) | set(risk_holdings))
        mismatch = tuple(
            s for s in all_symbols if broker_holdings.get(s, 0) != risk_holdings.get(s, 0)
        )
        if mismatch:
            logger.critical(
                "executor.reconcile mismatch broker={broker} risk={risk} symbols={syms}. "
                "신규 진입 차단, 운영자 수동 정리 필요.",
                broker=broker_holdings,
                risk=risk_holdings,
                syms=mismatch,
            )
            self._halt = True
        report = ReconcileReport(
            broker_holdings=MappingProxyType(broker_holdings),
            risk_holdings=MappingProxyType(risk_holdings),
            mismatch_symbols=mismatch,
        )
        self._last_reconcile = report
        return report

    @property
    def is_halted(self) -> bool:
        """재동기화 불일치(`_halt`) 또는 `RiskManager.is_halted` 둘 중 하나라도 True."""
        return self._halt or self._risk_manager.is_halted

    @property
    def last_reconcile(self) -> ReconcileReport | None:
        """가장 최근 `reconcile()` 결과. 세션 시작 직후·호출 전에는 None.

        notifier 가 일일 요약 생성 시 추가 네트워크 호출 없이 mismatch 상태를
        참조하도록 캐시된 값. `reconcile()` 호출마다 갱신된다.
        """
        return self._last_reconcile

    @property
    def last_sweep_entry_events(self) -> tuple[EntryEvent, ...]:
        """마지막 `step`/`force_close_all` sweep 동안 누적된 EntryEvent 스냅샷.

        sweep 이 정상 종료되면 `StepReport.entry_events` 와 동일 내용. sweep 이
        중간에 예외로 종료돼 `StepReport` 반환에 실패한 경우에도 누적된 부분
        이벤트를 외부에서 읽기 위한 경로(`main._on_force_close` 의 예외 분기
        에서 DB 기록 누락을 막기 위해 사용, ADR-0013 후속 보강).
        """
        return tuple(self._sweep_entry_events)

    @property
    def last_sweep_exit_events(self) -> tuple[ExitEvent, ...]:
        """마지막 `step`/`force_close_all` sweep 동안 누적된 ExitEvent 스냅샷.

        sweep 이 정상 종료되면 `StepReport.exit_events` 와 동일 내용. sweep 이
        중간에 예외로 종료돼 `StepReport` 반환에 실패한 경우에도 누적된 부분
        청산 이벤트를 외부에서 읽기 위한 경로 — 15:00 강제청산 중 특정 심볼의
        청산이 실패하더라도 이미 체결된 청산의 `daily_pnl.realized_pnl_krw`
        와 실 KIS 손익 괴리가 남지 않도록(리뷰 I3).
        """
        return tuple(self._sweep_exit_events)

    # ---- 시그널 처리 ---------------------------------------------------

    def _process_signals(self, signals: list[Signal], now: datetime) -> int:
        count = 0
        for sig in signals:
            if isinstance(sig, EntrySignal):
                if self._handle_entry(sig, now):
                    count += 1
            elif isinstance(sig, ExitSignal):
                if self._handle_exit(sig, now):
                    count += 1
            else:
                # Strategy Protocol 확장으로 새 시그널 타입이 들어왔을 때 — 명시적 실패.
                raise ExecutorError(
                    f"Executor._process_signals: 미지원 시그널 타입 {type(sig).__name__}"
                )
        return count

    def _handle_entry(self, signal: EntrySignal, now: datetime) -> bool:
        if self.is_halted:
            logger.warning(
                "executor.entry.skipped halted symbol={symbol} reason=halt_state",
                symbol=signal.symbol,
            )
            return False

        balance = self._with_backoff(self._balance_provider.get_balance)
        buffer_factor = Decimal("1") - self._config.cash_buffer_pct
        available_cash = max(0, int(Decimal(balance.withdrawable) * buffer_factor))

        decision = self._risk_manager.evaluate_entry(signal, available_cash)
        if not decision.approved:
            # RiskManager 가 이미 사유 로그를 남기지만, executor 호출자가
            # "왜 진입이 안 됐나" 를 executor 로그만 grep 해서도 답을 찾을 수
            # 있도록 책임 경계를 명시한다.
            logger.info(
                "executor.entry.rejected_by_risk symbol={symbol} reason={reason}",
                symbol=signal.symbol,
                reason=decision.reason,
            )
            return False

        ticket = self._with_backoff(
            lambda: self._order_submitter.submit_buy(signal.symbol, decision.qty)
        )
        self._wait_fill(ticket)

        entry_fill_price = buy_fill_price(signal.price, self._config.slippage_rate)
        self._risk_manager.record_entry(signal.symbol, entry_fill_price, decision.qty, now)
        self._open_lots[signal.symbol] = _OpenLot(entry_price=entry_fill_price, qty=decision.qty)
        logger.info(
            "executor.entry.filled symbol={symbol} qty={qty} fill_price={price} ref_price={ref}",
            symbol=signal.symbol,
            qty=decision.qty,
            price=entry_fill_price,
            ref=signal.price,
        )
        self._sweep_entry_events.append(
            EntryEvent(
                symbol=signal.symbol,
                qty=decision.qty,
                fill_price=entry_fill_price,
                ref_price=signal.price,
                timestamp=now,
                order_number=ticket.order_number,
            )
        )
        return True

    def _handle_exit(self, signal: ExitSignal, now: datetime) -> bool:
        lot = self._open_lots.get(signal.symbol)
        if lot is None:
            # _open_lots 에 없어도 RiskManager.active_positions 에 있으면 복원.
            # 외부에서 RiskManager 를 직접 record_entry 한 경우(테스트·수동 시나리오)
            # 와의 호환을 위해. 둘 다 없으면 전략-Executor 동기화 위반.
            risk_pos = next(
                (p for p in self._risk_manager.active_positions if p.symbol == signal.symbol),
                None,
            )
            if risk_pos is None:
                raise ExecutorError(
                    f"_handle_exit: 진입 기록 없는 청산 시그널 (symbol={signal.symbol}). "
                    "전략-Executor 동기화 위반 — record_entry 누락 가능성."
                )
            # fallback 진입 자체가 비정상 신호 — 정상 경로(_handle_entry → _open_lots) 로
            # 들어왔다면 lot is None 이 될 수 없다. 외부에서 RiskManager 를 직접
            # record_entry 한 흔적으로 보고 흔적을 남긴다 (silent fallback 방지).
            logger.warning(
                "executor.exit.lot_fallback symbol={symbol} entry_price={price} qty={qty} "
                "— _open_lots miss, RiskManager.active_positions 에서 복원. "
                "외부 record_entry 또는 Executor 우회 의심.",
                symbol=signal.symbol,
                price=risk_pos.entry_price,
                qty=risk_pos.qty,
            )
            lot = _OpenLot(entry_price=risk_pos.entry_price, qty=risk_pos.qty)

        ticket = self._with_backoff(
            lambda: self._order_submitter.submit_sell(signal.symbol, lot.qty)
        )
        self._wait_fill(ticket)

        exit_fill_price = sell_fill_price(signal.price, self._config.slippage_rate)
        net_pnl = self._compute_net_pnl(lot.entry_price, exit_fill_price, lot.qty)
        self._risk_manager.record_exit(signal.symbol, net_pnl)
        # _open_lots 미존재 fallback 경로(외부 record_entry) 에서는 키가 없을 수 있다.
        self._open_lots.pop(signal.symbol, None)
        logger.info(
            "executor.exit.filled symbol={symbol} qty={qty} fill_price={price} "
            "net_pnl={pnl} reason={reason}",
            symbol=signal.symbol,
            qty=lot.qty,
            price=exit_fill_price,
            pnl=net_pnl,
            reason=signal.reason,
        )
        self._sweep_exit_events.append(
            ExitEvent(
                symbol=signal.symbol,
                qty=lot.qty,
                fill_price=exit_fill_price,
                reason=signal.reason,
                net_pnl_krw=net_pnl,
                timestamp=now,
                order_number=ticket.order_number,
            )
        )
        return True

    def _compute_net_pnl(self, entry_price: Decimal, exit_price: Decimal, qty: int) -> int:
        """net_pnl = gross_pnl − (buy_comm + sell_comm + sell_tax). 백테스트와 동일."""
        buy_notional = entry_price * qty
        sell_notional = exit_price * qty
        b_comm = buy_commission(buy_notional, self._config.commission_rate)
        s_comm = sell_commission(sell_notional, self._config.commission_rate)
        tax = sell_tax(sell_notional, self._config.sell_tax_rate)
        gross = int(sell_notional) - int(buy_notional)
        return gross - b_comm - s_comm - tax

    # ---- 체결 대기 ------------------------------------------------------

    def _wait_fill(self, ticket: OrderTicket) -> None:
        """`get_pending_orders` 폴링으로 체결 확정 대기.

        시장가 주문 전제 — 부분체결은 V0 범위 밖. `order_number` 가 미체결
        목록에서 사라지면 체결 확정으로 간주한다.

        Raises:
            ExecutorError: `order_fill_timeout_s` 초과.
        """
        deadline = self._clock() + timedelta(seconds=self._config.order_fill_timeout_s)
        while True:
            pending = self._with_backoff(self._order_submitter.get_pending_orders)
            if not any(p.order_number == ticket.order_number for p in pending):
                return
            if self._clock() >= deadline:
                raise ExecutorError(
                    f"체결 대기 타임아웃 (order_number={ticket.order_number}, "
                    f"timeout={self._config.order_fill_timeout_s}s) — 운영자 개입 필요."
                )
            self._sleep(self._config.order_poll_interval_s)

    # ---- 백오프 --------------------------------------------------------

    def _with_backoff(self, fn: Callable[[], Any]) -> Any:
        """`KisClientError` 한정 지수 백오프. 기본 100→200→400 ms, 최대 3회 재시도.

        총 시도 = `backoff_max_attempts + 1` 회 (첫 시도 + 재시도). 한계 초과 시
        `ExecutorError(__cause__=KisClientError)` 로 승격. 다른 예외는 즉시 전파.
        """
        last_exc: KisClientError | None = None
        for attempt in range(self._config.backoff_max_attempts + 1):
            try:
                return fn()
            except KisClientError as e:
                last_exc = e
                if attempt >= self._config.backoff_max_attempts:
                    break
                delay = self._config.backoff_initial_s * (2**attempt)
                logger.warning(
                    "executor.backoff attempt={attempt} delay_s={delay} err={err}",
                    attempt=attempt + 1,
                    delay=delay,
                    err=str(e),
                )
                self._sleep(delay)
        # 도달 시점: last_exc 는 항상 KisClientError 인스턴스 (loop 가 1회 이상 돌고 break 했음).
        raise ExecutorError(
            f"KIS 호출 백오프 한계 초과 ({self._config.backoff_max_attempts}회 재시도) — "
            "운영자 개입 필요."
        ) from last_exc

    # ---- 공통 가드 -----------------------------------------------------

    @staticmethod
    def _require_aware(ts: datetime, name: str) -> None:
        if ts.tzinfo is None:
            raise RuntimeError(
                f"{name} 은 tz-aware datetime 이어야 합니다 (got naive {ts.isoformat()})"
            )
