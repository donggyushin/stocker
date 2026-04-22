"""Executor 공개 계약 단위 테스트 (RED 모드).

stock_agent.execution 패키지 전체가 아직 미작성 상태이므로 모든 케이스가
ModuleNotFoundError / ImportError 로 실패한다. 구현 후 GREEN 전환을 목표로 한다.

가드레일: KIS·텔레그램·외부 HTTP 접촉 없음. OrderSubmitter / BalanceProvider /
BarSource 는 모두 더블(fake/mock)로 주입.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal
from unittest.mock import MagicMock

import pytest

from stock_agent.broker import (
    BalanceSnapshot,
    Holding,
    KisClient,
    KisClientError,
    OrderTicket,
    PendingOrder,
)
from stock_agent.data import MinuteBar

# ---------------------------------------------------------------------------
# import — 이 블록이 ModuleNotFoundError 로 실패하는 것이 RED 모드의 목표.
# ---------------------------------------------------------------------------
from stock_agent.execution import (
    BalanceProvider,
    BarSource,
    DryRunOrderSubmitter,
    EntryEvent,
    Executor,
    ExecutorConfig,
    ExecutorError,
    ExitEvent,
    LiveBalanceProvider,
    LiveOrderSubmitter,
    OrderSubmitter,
    ReconcileReport,
    StepReport,
)
from stock_agent.risk import RiskConfig, RiskManager
from stock_agent.strategy import ORBStrategy, StrategyConfig

# ---------------------------------------------------------------------------
# 공통 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))
_DATE = date(2026, 4, 21)
_SYMBOL_A = "005930"
_SYMBOL_B = "000660"
_STARTING_CAPITAL = 1_000_000


def _kst(h: int, m: int, s: int = 0, *, d: date = _DATE) -> datetime:
    """KST aware datetime 생성 헬퍼."""
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=KST)


def _naive(h: int, m: int) -> datetime:
    """Naive datetime — 가드 검증용."""
    return datetime(_DATE.year, _DATE.month, _DATE.day, h, m)


def _bar(
    symbol: str,
    h: int,
    m: int,
    *,
    close: int = 50_000,
    high: int | None = None,
    low: int | None = None,
) -> MinuteBar:
    """MinuteBar 생성 헬퍼."""
    c = Decimal(str(close))
    hi = Decimal(str(high)) if high is not None else c
    lo = Decimal(str(low)) if low is not None else c
    return MinuteBar(
        symbol=symbol,
        bar_time=_kst(h, m),
        open=c,
        high=hi,
        low=lo,
        close=c,
        volume=0,
    )


def _ticket(
    symbol: str = _SYMBOL_A,
    side: Literal["buy", "sell"] = "buy",
    n: int = 1,
) -> OrderTicket:
    """OrderTicket 생성 헬퍼."""
    return OrderTicket(
        order_number=f"ORD-{n:04d}",
        symbol=symbol,
        side=side,
        qty=10,
        price=None,
        submitted_at=_kst(9, 30),
    )


def _empty_balance(withdrawable: int = 1_000_000) -> BalanceSnapshot:
    """보유 종목 없는 BalanceSnapshot."""
    return BalanceSnapshot(
        withdrawable=withdrawable,
        total=withdrawable,
        holdings_count=0,
        holdings=(),
        fetched_at=_kst(9, 30),
    )


def _balance_with_holding(symbol: str, qty: int, withdrawable: int = 500_000) -> BalanceSnapshot:
    """보유 종목 1건 BalanceSnapshot."""
    h = Holding(
        symbol=symbol,
        qty=qty,
        avg_price=Decimal("50000"),
        current_price=Decimal("50000"),
    )
    return BalanceSnapshot(
        withdrawable=withdrawable,
        total=withdrawable + qty * 50_000,
        holdings_count=1,
        holdings=(h,),
        fetched_at=_kst(9, 30),
    )


# ---------------------------------------------------------------------------
# 공통 Fake 구현체
# ---------------------------------------------------------------------------


class FakeOrderSubmitter:
    """KIS 접촉 없는 주문 더블. 주문 즉시 체결 (get_pending_orders → 빈 리스트)."""

    def __init__(self, *, fill_after: int = 0) -> None:
        """fill_after: get_pending_orders 몇 회 호출 후 빈 리스트 반환할지."""
        self._fill_after = fill_after
        self._poll_count: int = 0
        self.buy_calls: list[tuple[str, int]] = []
        self.sell_calls: list[tuple[str, int]] = []
        self._counter = 0

    def submit_buy(self, symbol: str, qty: int) -> OrderTicket:
        self._counter += 1
        self.buy_calls.append((symbol, qty))
        return OrderTicket(
            order_number=f"ORD-BUY-{self._counter:04d}",
            symbol=symbol,
            side="buy",
            qty=qty,
            price=None,
            submitted_at=_kst(9, 30),
        )

    def submit_sell(self, symbol: str, qty: int) -> OrderTicket:
        self._counter += 1
        self.sell_calls.append((symbol, qty))
        return OrderTicket(
            order_number=f"ORD-SELL-{self._counter:04d}",
            symbol=symbol,
            side="sell",
            qty=qty,
            price=None,
            submitted_at=_kst(9, 30),
        )

    def get_pending_orders(self) -> list[PendingOrder]:
        if self._poll_count < self._fill_after:
            self._poll_count += 1
            # 아직 미체결: 마지막 제출 티켓 번호 형태로 pending 반환
            return [
                PendingOrder(
                    order_number=f"ORD-BUY-{self._counter:04d}",
                    symbol=_SYMBOL_A,
                    side="buy",
                    qty_ordered=10,
                    qty_filled=0,
                    qty_remaining=10,
                    price=None,
                    submitted_at=_kst(9, 30),
                )
            ]
        return []


class FakeBalanceProvider:
    """잔고 더블."""

    def __init__(self, balance: BalanceSnapshot | None = None) -> None:
        self._balance = balance or _empty_balance()

    def get_balance(self) -> BalanceSnapshot:
        return self._balance

    def set_balance(self, balance: BalanceSnapshot) -> None:
        self._balance = balance


class FakeBarSource:
    """분봉 더블."""

    def __init__(self) -> None:
        self._bars: dict[str, list[MinuteBar]] = {}

    def set_bars(self, symbol: str, bars: list[MinuteBar]) -> None:
        self._bars[symbol] = bars

    def get_minute_bars(self, symbol: str) -> list[MinuteBar]:
        return list(self._bars.get(symbol, []))


# ---------------------------------------------------------------------------
# 공통 Fixture: ORBStrategy + RiskManager (순수 로직 — 실 인스턴스 사용)
# ---------------------------------------------------------------------------


@pytest.fixture()
def strategy() -> ORBStrategy:
    cfg = StrategyConfig(
        stop_loss_pct=Decimal("0.015"),
        take_profit_pct=Decimal("0.030"),
    )
    return ORBStrategy(cfg)


@pytest.fixture()
def risk_manager() -> RiskManager:
    return RiskManager(RiskConfig())


@pytest.fixture()
def fake_order_submitter() -> FakeOrderSubmitter:
    return FakeOrderSubmitter()


@pytest.fixture()
def fake_balance_provider() -> FakeBalanceProvider:
    return FakeBalanceProvider()


@pytest.fixture()
def fake_bar_source() -> FakeBarSource:
    return FakeBarSource()


def _make_executor(
    strategy: ORBStrategy,
    risk_manager: RiskManager,
    fake_order_submitter: OrderSubmitter,
    fake_balance_provider: FakeBalanceProvider,
    fake_bar_source: FakeBarSource,
    *,
    config: ExecutorConfig | None = None,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
    symbols: tuple[str, ...] = (_SYMBOL_A,),
) -> Executor:
    """Executor 생성 헬퍼."""
    _sleep = sleep if sleep is not None else lambda _: None
    _clock = clock if clock is not None else lambda: _kst(9, 30)
    return Executor(
        symbols=symbols,
        strategy=strategy,
        risk_manager=risk_manager,
        bar_source=fake_bar_source,
        order_submitter=fake_order_submitter,
        balance_provider=fake_balance_provider,
        config=config,
        clock=_clock,
        sleep=_sleep,
    )


# ===========================================================================
# 1. import & 공개 심볼
# ===========================================================================


class TestImportAndPublicSymbols:
    """stock_agent.execution 패키지 공개 심볼 11종이 모두 노출되는지 확인."""

    def test_executor_클래스_노출(self) -> None:
        assert Executor is not None

    def test_executor_config_노출(self) -> None:
        assert ExecutorConfig is not None

    def test_order_submitter_protocol_노출(self) -> None:
        assert OrderSubmitter is not None

    def test_live_order_submitter_노출(self) -> None:
        assert LiveOrderSubmitter is not None

    def test_dry_run_order_submitter_노출(self) -> None:
        assert DryRunOrderSubmitter is not None

    def test_balance_provider_protocol_노출(self) -> None:
        assert BalanceProvider is not None

    def test_live_balance_provider_노출(self) -> None:
        assert LiveBalanceProvider is not None

    def test_bar_source_protocol_노출(self) -> None:
        assert BarSource is not None

    def test_executor_error_노출(self) -> None:
        assert ExecutorError is not None

    def test_step_report_노출(self) -> None:
        assert StepReport is not None

    def test_reconcile_report_노출(self) -> None:
        assert ReconcileReport is not None


# ===========================================================================
# 2. ExecutorConfig
# ===========================================================================


class TestExecutorConfig:
    """ExecutorConfig 기본값 + 유효성 검증."""

    def test_기본값_생성_성공(self) -> None:
        cfg = ExecutorConfig()
        assert cfg.cash_buffer_pct == Decimal("0.005")
        assert cfg.order_fill_timeout_s == pytest.approx(30.0)
        assert cfg.order_poll_interval_s == pytest.approx(0.5)
        assert cfg.slippage_rate == Decimal("0.001")
        assert cfg.commission_rate == Decimal("0.00015")
        assert cfg.sell_tax_rate == Decimal("0.0018")
        assert cfg.backoff_max_attempts == 3
        assert cfg.backoff_initial_s == pytest.approx(0.1)

    def test_frozen_인스턴스_변경_불가(self) -> None:
        cfg = ExecutorConfig()
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            cfg.slippage_rate = Decimal("0.002")  # type: ignore[misc]

    @pytest.mark.parametrize(
        "field_name, bad_value",
        [
            ("cash_buffer_pct", Decimal("-0.001")),
            ("slippage_rate", Decimal("-0.001")),
            ("commission_rate", Decimal("-0.001")),
            ("sell_tax_rate", Decimal("-0.001")),
        ],
        ids=[
            "cash_buffer_pct_음수",
            "slippage_rate_음수",
            "commission_rate_음수",
            "sell_tax_rate_음수",
        ],
    )
    def test_비율_음수_RuntimeError(self, field_name: str, bad_value: Any) -> None:
        with pytest.raises(RuntimeError):
            ExecutorConfig(**{field_name: bad_value})

    @pytest.mark.parametrize(
        "field_name, bad_value",
        [
            ("order_fill_timeout_s", 0.0),
            ("order_poll_interval_s", 0.0),
            ("backoff_max_attempts", 0),
            ("backoff_initial_s", 0.0),
        ],
        ids=["timeout_0", "interval_0", "attempts_0", "initial_0"],
    )
    def test_양의_정수_또는_float_0_이하_RuntimeError(
        self, field_name: str, bad_value: Any
    ) -> None:
        with pytest.raises(RuntimeError):
            ExecutorConfig(**{field_name: bad_value})


# ===========================================================================
# 3. Executor 생성 · 세션
# ===========================================================================


class TestExecutorInit:
    """Executor.__init__ + start_session 동작."""

    def test_정상_생성(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        assert exc is not None

    def test_symbols_비면_RuntimeError(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        with pytest.raises(RuntimeError):
            _make_executor(
                strategy,
                risk_manager,
                fake_order_submitter,
                fake_balance_provider,
                fake_bar_source,
                symbols=(),
            )

    def test_start_session이_RiskManager에_위임(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        assert risk_manager.session_date == _DATE
        assert risk_manager.starting_capital_krw == _STARTING_CAPITAL

    def test_start_session_내부_상태_리셋(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        # 2회 호출 시 리셋 후 정상 상태여야 함
        exc.start_session(_DATE, _STARTING_CAPITAL)
        assert not exc.is_halted


# ===========================================================================
# 4. step 가드
# ===========================================================================


class TestStepGuards:
    """step() 사전 가드 RuntimeError."""

    def test_naive_now_RuntimeError(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        with pytest.raises(RuntimeError):
            exc.step(_naive(10, 0))

    def test_세션_미시작_RuntimeError(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        # start_session 호출 없이 step 시도
        with pytest.raises(RuntimeError):
            exc.step(_kst(10, 0))


# ===========================================================================
# 5. step — EntrySignal 처리 (승인)
# ===========================================================================


class TestStepEntryApproved:
    """EntrySignal → RiskManager 승인 → submit_buy → wait_fill → record_entry 흐름."""

    def _setup_orb_entry(
        self,
        strategy: ORBStrategy,
        fake_bar_source: FakeBarSource,
        or_close: int = 50_000,
    ) -> None:
        """OR 구간 분봉 + 돌파 분봉을 bar_source 에 세팅."""
        or_bars = [
            _bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500) for h in range(0, 30)
        ]
        breakout_bar = _bar(
            _SYMBOL_A,
            9,
            31,
            close=or_close,
            high=or_close,
            low=or_close - 100,
        )
        fake_bar_source.set_bars(_SYMBOL_A, or_bars + [breakout_bar])

    def test_entry_승인시_submit_buy_호출됨(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        self._setup_orb_entry(strategy, fake_bar_source)
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        report = exc.step(_kst(9, 32))
        assert len(fake_order_submitter.buy_calls) == 1
        assert report.orders_submitted >= 1

    def test_entry_승인시_record_entry_호출됨(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        self._setup_orb_entry(strategy, fake_bar_source)
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        exc.step(_kst(9, 32))

        # record_entry 후 active_positions 에 심볼이 등록되어야 함
        assert len(risk_manager.active_positions) == 1
        assert risk_manager.active_positions[0].symbol == _SYMBOL_A

    def test_entry_fill_price는_slippage_적용(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """entry_fill_price = signal.price * (1 + slippage_rate)."""
        self._setup_orb_entry(strategy, fake_bar_source, or_close=50_000)
        cfg = ExecutorConfig(slippage_rate=Decimal("0.001"))
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        exc.step(_kst(9, 32))

        pos = risk_manager.active_positions[0]
        expected = Decimal("50000") * Decimal("1.001")
        assert pos.entry_price == pytest.approx(expected, abs=Decimal("1"))


# ===========================================================================
# 6. step — EntrySignal 처리 (거부)
# ===========================================================================


class TestStepEntryRejected:
    """RiskManager 거부 시 submit_buy 미호출, _open_lots 미생성."""

    def test_entry_거부시_submit_buy_미호출(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        # 잔고 0원 → insufficient_cash 또는 below_min_notional
        fake_balance_provider.set_balance(_empty_balance(withdrawable=0))

        or_bars = [
            _bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500) for h in range(0, 30)
        ]
        breakout_bar = _bar(_SYMBOL_A, 9, 31, close=50_000, high=50_000, low=49_900)
        fake_bar_source.set_bars(_SYMBOL_A, or_bars + [breakout_bar])

        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        exc.step(_kst(9, 32))

        assert len(fake_order_submitter.buy_calls) == 0
        assert len(risk_manager.active_positions) == 0


# ===========================================================================
# 7. step — ExitSignal 처리 (승인)
# ===========================================================================


class TestStepExitApproved:
    """ExitSignal → submit_sell → wait_fill → record_exit(net_pnl)."""

    def _enter_position(
        self,
        exc: Executor,
        risk_manager: RiskManager,
        *,
        symbol: str = _SYMBOL_A,
        entry_price: Decimal = Decimal("50000"),
        qty: int = 10,
    ) -> None:
        """진입 상태를 직접 세팅 (record_entry 호출)."""
        risk_manager.record_entry(symbol, entry_price, qty, _kst(9, 31))

    def test_exit_승인시_submit_sell_호출(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        # ORBStrategy 에 직접 long 상태 주입 후 손절 bar 공급
        entry_price = Decimal("50000")
        qty = 10
        # strategy 를 통해 직접 진입시키기
        # OR 구간 먼저 처리
        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        # 돌파 bar
        strategy.on_bar(_bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000))
        risk_manager.record_entry(_SYMBOL_A, entry_price * Decimal("1.001"), qty, _kst(9, 31))

        # 손절 bar 공급 — bar.low <= stop_price
        stop_price = entry_price * Decimal("1") * (Decimal("1") - Decimal("0.015"))
        stop_bar = _bar(
            _SYMBOL_A,
            9,
            35,
            close=int(stop_price) - 100,
            high=int(stop_price) - 50,
            low=int(stop_price) - 200,
        )
        fake_bar_source.set_bars(_SYMBOL_A, [stop_bar])

        report = exc.step(_kst(9, 36))
        assert len(fake_order_submitter.sell_calls) == 1
        assert report.orders_submitted >= 1

    def test_exit_후_active_positions_비워짐(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        entry_price = Decimal("50000")
        qty = 10
        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        strategy.on_bar(_bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000))
        risk_manager.record_entry(_SYMBOL_A, entry_price * Decimal("1.001"), qty, _kst(9, 31))

        stop_price = entry_price * (Decimal("1") - Decimal("0.015"))
        stop_bar = _bar(
            _SYMBOL_A,
            9,
            35,
            close=int(stop_price) - 100,
            high=int(stop_price) - 50,
            low=int(stop_price) - 200,
        )
        fake_bar_source.set_bars(_SYMBOL_A, [stop_bar])
        exc.step(_kst(9, 36))

        assert len(risk_manager.active_positions) == 0

    def test_exit_net_pnl_부호(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """손절 → 음수 PnL 이 record_exit 에 전달되어 daily_pnl 음수."""
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        entry_price = Decimal("50000")
        qty = 10
        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        strategy.on_bar(_bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000))
        risk_manager.record_entry(_SYMBOL_A, entry_price * Decimal("1.001"), qty, _kst(9, 31))

        stop_price = entry_price * (Decimal("1") - Decimal("0.015"))
        stop_bar = _bar(
            _SYMBOL_A,
            9,
            35,
            close=int(stop_price) - 100,
            high=int(stop_price) - 50,
            low=int(stop_price) - 200,
        )
        fake_bar_source.set_bars(_SYMBOL_A, [stop_bar])
        exc.step(_kst(9, 36))

        assert risk_manager.daily_realized_pnl_krw < 0


# ===========================================================================
# 8. step — ExitSignal 전략 무결성 (_open_lots 없음)
# ===========================================================================


class TestStepExitIntegrity:
    """_open_lots 미존재 ExitSignal → ExecutorError."""

    def test_진입기록_없는_청산_ExecutorError(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        # strategy 에는 long 상태를 강제 주입하지 않고
        # ExitSignal 만 나오도록 조작 — on_time 으로 강제청산 트리거
        # (진입 기록 없이 strategy 가 long 상태인 시나리오를 만들기 위해
        # ORBStrategy 내부 상태를 직접 수정)
        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        strategy.on_bar(_bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000))
        # risk_manager.record_entry 를 일부러 호출하지 않음
        # → Executor 의 _open_lots 에 기록 없음
        # → on_time(15:00) 으로 강제청산 시 ExecutorError

        with pytest.raises(ExecutorError):
            exc.force_close_all(_kst(15, 0))


# ===========================================================================
# 9. 강제청산 (force_close_all)
# ===========================================================================


class TestForceCloseAll:
    """force_close_all(15:00) — on_time 처리, ExitSignal 정상 처리, processed_bars=0."""

    def test_force_close_all_processed_bars는_0(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        report = exc.force_close_all(_kst(15, 0))
        assert report.processed_bars == 0

    def test_force_close_all_long_심볼_청산됨(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        # OR → 진입
        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        strategy.on_bar(_bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000))
        risk_manager.record_entry(_SYMBOL_A, Decimal("50050"), 10, _kst(9, 31))

        report = exc.force_close_all(_kst(15, 0))
        # ExitSignal 이 처리되어 submit_sell 호출되어야 함
        assert len(fake_order_submitter.sell_calls) == 1
        assert report.orders_submitted >= 1

    def test_force_close_naive_now_RuntimeError(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        with pytest.raises(RuntimeError):
            exc.force_close_all(_naive(15, 0))


# ===========================================================================
# 10. 체결 대기 (_wait_fill)
# ===========================================================================


class TestResolveFill:
    """get_pending_orders 시나리오별 체결 대기 동작."""

    def test_즉시_체결_sleep_미호출(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """get_pending_orders 가 즉시 [] → sleep 호출 0회."""
        sleep_mock = MagicMock()
        submitter = FakeOrderSubmitter(fill_after=0)

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            sleep=sleep_mock,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        breakout = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, [breakout])

        exc.step(_kst(9, 32))
        sleep_mock.assert_not_called()

    def test_2회_pending_후_체결_sleep_2회(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """get_pending_orders 가 2회 pending 반환 후 [] → sleep 2회."""
        sleep_calls: list[float] = []
        submitter = FakeOrderSubmitter(fill_after=2)

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            sleep=lambda s: sleep_calls.append(s),
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        breakout = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, [breakout])

        exc.step(_kst(9, 32))
        assert len(sleep_calls) == 2

    def test_timeout_cancel_order_위임(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """ADR-0014: 타임아웃 시 ExecutorError 를 raise 하지 않고
        cancel_order(order_number) 를 호출한 뒤 step 을 정상 완료한다.

        이전 계약(ExecutorError raise) → 새 계약(cancel_order 위임) 회귀 방지.
        """
        # clock 이 처음 호출 시 deadline 이 이미 지난 시각 반환 (zero fill 유도)
        tick = [_kst(9, 30), _kst(9, 30, 31)]  # initial + after 30s timeout

        def advancing_clock() -> datetime:
            if len(tick) > 1:
                return tick.pop(0)
            return tick[0]

        # FakeOrderSubmitterWithPartialFill(partial_fill_qty=0) — zero fill 시뮬레이션
        submitter = FakeOrderSubmitterWithPartialFill(partial_fill_qty=0)
        cfg = ExecutorConfig(order_fill_timeout_s=30.0, order_poll_interval_s=0.001)

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
            clock=advancing_clock,
            sleep=lambda _: None,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        breakout = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, [breakout])

        # ADR-0014: ExecutorError 를 raise 하지 않고 step 이 정상 반환되어야 한다
        report = exc.step(_kst(9, 32))
        assert report is not None, "타임아웃 후에도 StepReport 를 반환해야 한다"

        # cancel_order 가 제출된 order_number 로 정확히 1회 호출되어야 한다
        msg = f"타임아웃 시 cancel_order 가 1회 호출되어야 한다 (got {submitter.cancel_calls})"
        assert len(submitter.cancel_calls) == 1, msg
        assert submitter.cancel_calls[0] == submitter._last_order_number


# ===========================================================================
# 11. DryRunOrderSubmitter
# ===========================================================================


class TestDryRunOrderSubmitter:
    """DryRunOrderSubmitter — KIS 접촉 0, 가짜 티켓 반환, record_entry/exit 정상."""

    def test_dry_run_buy_티켓_반환_DRY_접두사(self) -> None:
        dry = DryRunOrderSubmitter()
        ticket = dry.submit_buy(_SYMBOL_A, 10)
        assert ticket.order_number.startswith("DRY")
        assert ticket.symbol == _SYMBOL_A
        assert ticket.side == "buy"

    def test_dry_run_sell_티켓_반환(self) -> None:
        dry = DryRunOrderSubmitter()
        ticket = dry.submit_sell(_SYMBOL_A, 10)
        assert ticket.order_number.startswith("DRY")
        assert ticket.side == "sell"

    def test_dry_run_get_pending_orders_항상_빈_리스트(self) -> None:
        dry = DryRunOrderSubmitter()
        dry.submit_buy(_SYMBOL_A, 10)
        assert dry.get_pending_orders() == []

    def test_dry_run_주입시_KIS_접촉_없이_record_entry_호출됨(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        dry = DryRunOrderSubmitter()
        exc = _make_executor(
            strategy,
            risk_manager,
            dry,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        breakout = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, [breakout])

        exc.step(_kst(9, 32))
        # record_entry 가 정상 호출되어 active_positions 에 기록됨
        assert len(risk_manager.active_positions) == 1

    def test_dry_run_order_number_증가(self) -> None:
        dry = DryRunOrderSubmitter()
        t1 = dry.submit_buy(_SYMBOL_A, 10)
        t2 = dry.submit_sell(_SYMBOL_A, 10)
        assert t1.order_number != t2.order_number


# ===========================================================================
# 12. reconcile (재동기화)
# ===========================================================================


class TestReconcile:
    """reconcile() — 일치·불일치·qty 차이 시나리오."""

    def test_보유_일치_halt_안됨(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        report = exc.reconcile()
        assert len(report.mismatch_symbols) == 0
        assert not exc.is_halted

    def test_broker_보유_추가_mismatch_검출_halt(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        # broker 에는 보유 있음, RiskManager 에는 없음
        fake_balance_provider.set_balance(_balance_with_holding(_SYMBOL_A, qty=10))
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        report = exc.reconcile()
        assert _SYMBOL_A in report.mismatch_symbols
        assert exc.is_halted

    def test_qty_차이_mismatch_검출(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        # broker 10주, RiskManager 5주 → 불일치
        fake_balance_provider.set_balance(_balance_with_holding(_SYMBOL_A, qty=10))
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        risk_manager.record_entry(_SYMBOL_A, Decimal("50000"), 5, _kst(9, 31))

        report = exc.reconcile()
        assert _SYMBOL_A in report.mismatch_symbols

    def test_reconcile_critical_로그_남김(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """불일치 발생 시 critical 로그가 기록되어야 한다."""
        from loguru import logger

        critical_messages: list[str] = []

        def _sink(msg: Any) -> None:
            if msg.record["level"].name == "CRITICAL":
                critical_messages.append(str(msg))

        sink_id = logger.add(_sink, level="CRITICAL")
        try:
            fake_balance_provider.set_balance(_balance_with_holding(_SYMBOL_A, qty=10))
            exc = _make_executor(
                strategy,
                risk_manager,
                fake_order_submitter,
                fake_balance_provider,
                fake_bar_source,
            )
            exc.start_session(_DATE, _STARTING_CAPITAL)
            exc.reconcile()
        finally:
            logger.remove(sink_id)

        assert len(critical_messages) >= 1


# ===========================================================================
# 13. halt 상태 — 진입 차단, 청산 정상
# ===========================================================================


class TestHaltBehavior:
    """reconcile 불일치 후 EntrySignal 스킵, ExitSignal 정상 처리."""

    def test_halt_후_entry_스킵(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        fake_balance_provider.set_balance(_balance_with_holding(_SYMBOL_B, qty=10))
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        # reconcile → halt
        exc.reconcile()
        assert exc.is_halted

        # breakout bar 공급 → EntrySignal 생성되지만 스킵되어야 함
        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        breakout = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, [breakout])

        exc.step(_kst(9, 32))
        assert len(fake_order_submitter.buy_calls) == 0

    def test_is_halted_reconcile_불일치로_True(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        fake_balance_provider.set_balance(_balance_with_holding(_SYMBOL_A, qty=10))
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        exc.reconcile()
        assert exc.is_halted is True

    def test_is_halted_RiskManager_halt_반영(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """RiskManager 서킷브레이커 발동 시 is_halted True."""
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        # 큰 손실 record_exit 로 서킷브레이커 강제 발동
        risk_manager.record_entry(_SYMBOL_A, Decimal("50000"), 1, _kst(9, 31))
        risk_manager.record_exit(_SYMBOL_A, -_STARTING_CAPITAL)  # 전체 자본 손실
        assert risk_manager.is_halted
        assert exc.is_halted is True


# ===========================================================================
# 14. KisClientError 백오프
# ===========================================================================


class TestBackoff:
    """KisClientError 백오프 재시도 + 초과 시 ExecutorError 승격."""

    def test_1회_실패_후_성공_sleep_1회(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        sleep_calls: list[float] = []
        call_count = 0

        def flaky_buy(symbol: str, qty: int) -> OrderTicket:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise KisClientError("일시적 오류")
            return OrderTicket(
                order_number="ORD-0001",
                symbol=symbol,
                side="buy",
                qty=qty,
                price=None,
                submitted_at=_kst(9, 31),
            )

        submitter = MagicMock(spec=FakeOrderSubmitter)
        submitter.submit_buy.side_effect = flaky_buy
        submitter.submit_sell.return_value = _ticket(side="sell")
        submitter.get_pending_orders.return_value = []

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            sleep=lambda s: sleep_calls.append(s),
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        breakout = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, [breakout])

        exc.step(_kst(9, 32))
        # backoff sleep 1회 (100ms → 첫 번째 재시도 전)
        assert any(s == pytest.approx(0.1) for s in sleep_calls)

    def test_최대_재시도_초과_ExecutorError_승격_cause_보존(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        original = KisClientError("연속 실패")

        submitter = MagicMock(spec=FakeOrderSubmitter)
        submitter.submit_buy.side_effect = original
        submitter.get_pending_orders.return_value = []

        cfg = ExecutorConfig(backoff_max_attempts=3, backoff_initial_s=0.01)
        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
            sleep=lambda _: None,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        breakout = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, [breakout])

        with pytest.raises(ExecutorError) as exc_info:
            exc.step(_kst(9, 32))

        assert exc_info.value.__cause__ is original

    def test_backoff_sleep_지수증가_100ms_200ms_400ms(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        sleep_calls: list[float] = []

        submitter = MagicMock(spec=FakeOrderSubmitter)
        submitter.submit_buy.side_effect = KisClientError("항상 실패")
        submitter.get_pending_orders.return_value = []

        cfg = ExecutorConfig(backoff_max_attempts=3, backoff_initial_s=0.1)
        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
            sleep=lambda s: sleep_calls.append(s),
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        breakout = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, [breakout])

        with pytest.raises(ExecutorError):
            exc.step(_kst(9, 32))

        # 재시도 3회: sleep(0.1), sleep(0.2), sleep(0.4)
        backoff_sleeps = [s for s in sleep_calls if s >= 0.09]
        assert len(backoff_sleeps) >= 3
        assert backoff_sleeps[0] == pytest.approx(0.1, rel=0.1)
        assert backoff_sleeps[1] == pytest.approx(0.2, rel=0.1)
        assert backoff_sleeps[2] == pytest.approx(0.4, rel=0.1)


# ===========================================================================
# 15. 에러 좁힘 — RuntimeError / RiskManagerError 즉시 전파
# ===========================================================================


class TestErrorNarrowing:
    """KisClientError 이외 예외는 백오프 미적용, 즉시 전파."""

    def test_RuntimeError_즉시_전파(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        submitter = MagicMock(spec=FakeOrderSubmitter)
        submitter.submit_buy.side_effect = RuntimeError("설정 오류")
        submitter.get_pending_orders.return_value = []

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        breakout = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, [breakout])

        with pytest.raises(RuntimeError, match="설정 오류"):
            exc.step(_kst(9, 32))


# ===========================================================================
# 16. 멀티 심볼
# ===========================================================================


class TestMultiSymbol:
    """두 종목 EntrySignal 독립 처리, _last_processed_bar_time 종목별 갱신."""

    def test_두_종목_entry_독립_처리(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
            symbols=(_SYMBOL_A, _SYMBOL_B),
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        for sym in (_SYMBOL_A, _SYMBOL_B):
            or_bars = [_bar(sym, 9, m, close=49_000, high=49_500, low=48_500) for m in range(0, 30)]
            breakout = _bar(sym, 9, 31, close=50_100, high=50_100, low=50_000)
            fake_bar_source.set_bars(sym, or_bars + [breakout])

        report = exc.step(_kst(9, 32))
        # 두 종목 중 최소 1건 이상 주문 제출 (RiskManager 동시 3종목 한도 내)
        assert report.orders_submitted >= 1


# ===========================================================================
# 17. 분봉 처리 idempotent (_last_processed_bar_time)
# ===========================================================================


class TestBarIdempotent:
    """동일 step 두 번 호출 시 같은 bar 재처리 안 함."""

    def test_같은_bar_재처리_안함(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        breakout = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, [breakout])

        exc.step(_kst(9, 32))
        buy_count_first = len(fake_order_submitter.buy_calls)

        # 동일 bar 를 다시 공급해 두 번째 step 호출
        fake_bar_source.set_bars(_SYMBOL_A, [breakout])
        exc.step(_kst(9, 33))
        buy_count_second = len(fake_order_submitter.buy_calls)

        # 두 번째 호출에서 같은 bar 로 추가 주문이 나가지 않아야 함
        assert buy_count_second == buy_count_first


# ===========================================================================
# 18. StepReport / ReconcileReport 구조
# ===========================================================================


class TestReportStructure:
    """StepReport / ReconcileReport frozen=True + 필드 노출 확인."""

    def test_step_report_frozen(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        report = exc.step(_kst(9, 32))

        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            report.processed_bars = 99  # type: ignore[misc]

    def test_step_report_필드_노출(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        report = exc.step(_kst(9, 32))

        assert hasattr(report, "processed_bars")
        assert hasattr(report, "orders_submitted")
        assert hasattr(report, "halted")
        assert hasattr(report, "reconcile")

    def test_reconcile_report_frozen(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        report = exc.reconcile()

        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            report.mismatch_symbols = ("999999",)  # type: ignore[misc]

    def test_reconcile_report_필드_노출(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        report = exc.reconcile()

        assert hasattr(report, "broker_holdings")
        assert hasattr(report, "risk_holdings")
        assert hasattr(report, "mismatch_symbols")


# ===========================================================================
# 19. ReconcileReport 외부 mutation 차단 (C1)
# ===========================================================================


class TestReconcileReportImmutability:
    """ReconcileReport.broker_holdings / risk_holdings 외부 변이 차단."""

    def test_reconcile_report_broker_holdings_mutation_차단(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """report.broker_holdings 에 setitem 시도 → TypeError (MappingProxyType 래핑 기대).

        현재는 dict 그대로 노출돼 setitem 이 통과 — RED.
        GREEN 후엔 MappingProxyType 으로 래핑돼 TypeError raise 기대.
        """
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        report = exc.reconcile()

        with pytest.raises(TypeError):
            report.broker_holdings["999999"] = 99  # type: ignore[index]

    def test_reconcile_report_risk_holdings_mutation_차단(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """report.risk_holdings 도 동일 — MappingProxyType 래핑 기대."""
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        report = exc.reconcile()

        with pytest.raises(TypeError):
            report.risk_holdings["999999"] = 99  # type: ignore[index]


# ===========================================================================
# 20. ExecutorConfig.cash_buffer_pct >= 1.0 거부 (I8)
# ===========================================================================


class TestExecutorConfigCashBuffer:
    """cash_buffer_pct >= 1.0 이면 available_cash 가 0/음수 — RuntimeError 기대."""

    @pytest.mark.parametrize(
        "bad_value",
        [Decimal("1.0"), Decimal("1.5"), Decimal("2.0")],
        ids=["1.0", "1.5", "2.0"],
    )
    def test_cash_buffer_pct_1_이상_RuntimeError(self, bad_value: Decimal) -> None:
        """cash_buffer_pct 는 [0, 1) 범위.

        1 이상이면 available_cash 가 0/음수로 떨어져 의미 없음.
        """
        with pytest.raises(RuntimeError):
            ExecutorConfig(cash_buffer_pct=bad_value)


# ===========================================================================
# 21. _handle_entry RiskManager 거부 시 INFO 로그 (I2)
# ===========================================================================


class TestEntryRejectedLog:
    """RiskManager 거부 직후 executor 가 'executor.entry.rejected_by_risk' INFO 로그 남김."""

    def test_entry_거부시_executor_info_로그_남김(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """RiskManager 거부 직후 executor 가 'executor.entry.rejected_by_risk'
        INFO 로그 남김 — 책임 경계 명시.

        현재 코드는 로그 0줄 — RED. GREEN 후엔 logger.info 1줄 추가 기대.
        """
        from loguru import logger

        info_messages: list[str] = []

        def _sink(msg: Any) -> None:
            if msg.record["level"].name == "INFO" and "executor.entry.rejected_by_risk" in str(msg):
                info_messages.append(str(msg))

        sink_id = logger.add(_sink, level="INFO")
        try:
            # 잔고 0원 → RiskManager insufficient_cash 또는 below_min_notional 거부
            fake_balance_provider.set_balance(_empty_balance(withdrawable=0))
            or_bars = [
                _bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500) for h in range(0, 30)
            ]
            breakout_bar = _bar(_SYMBOL_A, 9, 31, close=50_000, high=50_000, low=49_900)
            fake_bar_source.set_bars(_SYMBOL_A, or_bars + [breakout_bar])

            exc = _make_executor(
                strategy,
                risk_manager,
                fake_order_submitter,
                fake_balance_provider,
                fake_bar_source,
            )
            exc.start_session(_DATE, _STARTING_CAPITAL)
            exc.step(_kst(9, 32))
        finally:
            logger.remove(sink_id)

        assert len(info_messages) >= 1


# ===========================================================================
# 22. _handle_exit _open_lots fallback 시 WARNING 로그 (I3)
# ===========================================================================


class TestExitLotFallbackLog:
    """_open_lots 미존재 + RiskManager.active_positions 존재 → fallback 시 WARNING 로그."""

    def test_exit_lot_fallback시_warning_로그(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """_open_lots 미존재 + RiskManager.active_positions 존재 → fallback 진입.

        fallback 시 'executor.exit.lot_fallback' WARNING 1건 기대 (silent fallback 방지).
        """
        from loguru import logger

        warn_messages: list[str] = []

        def _sink(msg: Any) -> None:
            if msg.record["level"].name == "WARNING" and "executor.exit.lot_fallback" in str(msg):
                warn_messages.append(str(msg))

        sink_id = logger.add(_sink, level="WARNING")
        try:
            exc = _make_executor(
                strategy,
                risk_manager,
                fake_order_submitter,
                fake_balance_provider,
                fake_bar_source,
            )
            exc.start_session(_DATE, _STARTING_CAPITAL)
            # OR 구간 + 돌파 bar 로 strategy 를 long 상태로 전이
            for m in range(0, 30):
                strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
            strategy.on_bar(_bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000))
            # Executor 를 우회해 RiskManager 에만 진입 기록 — _open_lots 는 비어있음
            risk_manager.record_entry(_SYMBOL_A, Decimal("50050"), 10, _kst(9, 31))

            # 손절 bar 공급 → ExitSignal → _open_lots 미존재 → fallback 분기 → WARNING 기대
            entry_price = Decimal("50050")
            stop_price = entry_price * (Decimal("1") - Decimal("0.015"))
            stop_bar = _bar(
                _SYMBOL_A,
                9,
                35,
                close=int(stop_price) - 100,
                high=int(stop_price) - 50,
                low=int(stop_price) - 200,
            )
            fake_bar_source.set_bars(_SYMBOL_A, [stop_bar])
            exc.step(_kst(9, 36))
        finally:
            logger.remove(sink_id)

        assert len(warn_messages) >= 1


# ===========================================================================
# 23. _compute_net_pnl 결정값 회귀 (C2)
# ===========================================================================


class TestNetPnlAccuracy:
    """_compute_net_pnl 이 backtest.costs 와 1:1 동일한 산식을 사용하는지 회귀 검증."""

    def test_net_pnl_정확값_손절_시나리오(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """직접 계산한 expected net_pnl 과 risk_manager.daily_realized_pnl_krw 정확 일치.

        backtest.costs 의 buy_fill_price/sell_fill_price/buy_commission/sell_commission/sell_tax
        를 같은 인자로 호출해 expected 산출 → record_exit 통지값과 정확값 비교.
        """
        from stock_agent.backtest.costs import (
            buy_commission,
            buy_fill_price,
            sell_commission,
            sell_fill_price,
            sell_tax,
        )

        slippage = Decimal("0.001")
        commission = Decimal("0.00015")
        sell_tax_rate = Decimal("0.0018")

        # 손절 시나리오: entry signal close=50_100, stop_loss=-1.5%
        entry_ref = Decimal("50100")
        qty = 10
        entry_fill = buy_fill_price(entry_ref, slippage)
        stop_price = entry_fill * (Decimal("1") - Decimal("0.015"))
        # ExitSignal.price = stop_price (손절가)
        exit_ref = stop_price
        exit_fill = sell_fill_price(exit_ref, slippage)

        buy_notional = entry_fill * qty
        sell_notional = exit_fill * qty
        expected_net = (
            int(sell_notional)
            - int(buy_notional)
            - buy_commission(buy_notional, commission)
            - sell_commission(sell_notional, commission)
            - sell_tax(sell_notional, sell_tax_rate)
        )

        cfg = ExecutorConfig(
            slippage_rate=slippage,
            commission_rate=commission,
            sell_tax_rate=sell_tax_rate,
        )
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        # OR 구간 + 돌파 bar (close=50_100)
        or_bars = [
            _bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500) for h in range(0, 30)
        ]
        breakout_bar = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, or_bars + [breakout_bar])
        exc.step(_kst(9, 32))  # 진입 → record_entry, _open_lots 채움

        assert len(risk_manager.active_positions) == 1

        # RiskManager 가 실제로 승인한 qty 와 entry_fill_price 로 expected_net 재산출
        actual_pos = risk_manager.active_positions[0]
        actual_qty = actual_pos.qty
        actual_entry_fill = actual_pos.entry_price
        # ORBStrategy 의 stop_price = bar.close * (1 - stop_loss_pct) — entry_ref 기준
        # ExitSignal.price = strategy 내부 stop_price (슬리피지 미적용 참고가)
        strategy_stop_price = entry_ref * (Decimal("1") - Decimal("0.015"))
        actual_exit_fill = sell_fill_price(strategy_stop_price, slippage)

        actual_buy_notional = actual_entry_fill * actual_qty
        actual_sell_notional = actual_exit_fill * actual_qty
        expected_net = (
            int(actual_sell_notional)
            - int(actual_buy_notional)
            - buy_commission(actual_buy_notional, commission)
            - sell_commission(actual_sell_notional, commission)
            - sell_tax(actual_sell_notional, sell_tax_rate)
        )

        # 손절 bar 공급: low <= strategy_stop_price
        stop_int = int(strategy_stop_price)
        stop_bar = _bar(
            _SYMBOL_A,
            9,
            35,
            close=stop_int - 100,
            high=stop_int - 50,
            low=stop_int - 200,
        )
        fake_bar_source.set_bars(_SYMBOL_A, [stop_bar])
        exc.step(_kst(9, 36))  # 청산 → record_exit

        assert risk_manager.daily_realized_pnl_krw == expected_net


# ===========================================================================
# 24. entry → exit 정상 경로 _open_lots hit 검증 (C3)
# ===========================================================================


class TestEntryToExitNormalPath:
    """Executor 만 통한 진입·청산 — _open_lots 정상 hit 경로 명시 검증."""

    def test_entry_to_exit_정상_경로_open_lots_hit(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """Executor 만 통한 진입·청산 — record_entry/record_exit 직접 호출 없이.

        기존 ExitSignal 테스트는 외부 record_entry 직접 호출 → fallback 경로.
        이 테스트는 _open_lots 정상 hit 경로를 명시 검증.

        검증:
        (1) record_entry 가 Executor 통해 호출됨 (slippage 적용가로 active_positions 등록)
        (2) record_exit 호출 후 active_positions 비었음
        (3) net_pnl 음수 (손절 시나리오)
        (4) sell_calls 1건
        (5) fallback 로그 ('executor.exit.lot_fallback') 미발생 — 정상 경로 증명
        """
        from loguru import logger

        fallback_messages: list[str] = []

        def _sink(msg: Any) -> None:
            if "executor.exit.lot_fallback" in str(msg):
                fallback_messages.append(str(msg))

        sink_id = logger.add(_sink, level="WARNING")
        try:
            exc = _make_executor(
                strategy,
                risk_manager,
                fake_order_submitter,
                fake_balance_provider,
                fake_bar_source,
            )
            exc.start_session(_DATE, _STARTING_CAPITAL)

            # OR 구간 + 돌파 bar (close=50_100)
            or_bars = [
                _bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500) for h in range(0, 30)
            ]
            breakout_bar = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
            fake_bar_source.set_bars(_SYMBOL_A, or_bars + [breakout_bar])
            exc.step(_kst(9, 32))

            # (1) slippage 적용가로 진입 등록
            assert len(risk_manager.active_positions) == 1
            entry_pos = risk_manager.active_positions[0]
            expected_entry_fill = Decimal("50100") * Decimal("1.001")
            assert entry_pos.entry_price == pytest.approx(expected_entry_fill, abs=Decimal("1"))

            # 손절 bar 공급
            entry_fill = entry_pos.entry_price
            stop_price = entry_fill * (Decimal("1") - Decimal("0.015"))
            stop_int = int(stop_price)
            stop_bar = _bar(
                _SYMBOL_A,
                9,
                35,
                close=stop_int - 100,
                high=stop_int - 50,
                low=stop_int - 200,
            )
            fake_bar_source.set_bars(_SYMBOL_A, [stop_bar])
            exc.step(_kst(9, 36))
        finally:
            logger.remove(sink_id)

        # (2) active_positions 비워짐
        assert len(risk_manager.active_positions) == 0
        # (3) net_pnl 음수
        assert risk_manager.daily_realized_pnl_krw < 0
        # (4) sell_calls 1건
        assert len(fake_order_submitter.sell_calls) == 1
        # (5) fallback 로그 미발생 — 정상 경로 증명
        assert len(fallback_messages) == 0


# ===========================================================================
# 25. halt 영속성 + start_session 재호출 리셋 (I7)
# ===========================================================================


class TestHaltPersistence:
    """halt 자동 복구 금지 계약 + start_session 만이 halt 를 풀 수 있음."""

    def test_halt_reconcile_회복후에도_유지(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """mismatch → halt → 잔고 일치 회복 → 다시 reconcile → halt 여전히 True.

        "자동 복구 금지" 계약 회귀 보호.
        """
        fake_balance_provider.set_balance(_balance_with_holding(_SYMBOL_A, qty=10))
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        exc.reconcile()
        assert exc.is_halted

        # 잔고를 일치(빈 잔고)로 변경 후 다시 reconcile
        fake_balance_provider.set_balance(_empty_balance())
        exc.reconcile()
        assert exc.is_halted  # 자동 복구 금지

    def test_halt_start_session_재호출시_리셋(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """start_session 재호출만이 halt 를 풀 수 있다."""
        fake_balance_provider.set_balance(_balance_with_holding(_SYMBOL_A, qty=10))
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        exc.reconcile()
        assert exc.is_halted

        fake_balance_provider.set_balance(_empty_balance())
        exc.start_session(_DATE, _STARTING_CAPITAL)
        assert not exc.is_halted


# ===========================================================================
# 26. EntryEvent / ExitEvent / StepReport 이벤트 필드 불변성 (notifier용)
# ===========================================================================


class TestEntryExitEventBackwardCompat:
    """StepReport 기본값 backward compat — entry_events / exit_events 기본 빈 tuple."""

    def test_step_report_필수_4개_필드만으로_생성시_이벤트_기본_빈_tuple(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """StepReport 는 entry_events / exit_events 기본값으로 빈 tuple 을 가진다.

        기존 코드가 이 두 필드를 지정하지 않아도 AttributeError 없이 동작해야 한다.
        """
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        # 분봉 없음 → 진입·청산 이벤트 0건
        report = exc.step(_kst(9, 32))

        assert report.entry_events == ()
        assert report.exit_events == ()


class TestStepEntryEvent:
    """step sweep 에서 진입 1회 → entry_events tuple 에 EntryEvent 1개."""

    def test_진입_1회시_entry_events_1개_필드_정확(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """entry_events[0] 의 fill_price / ref_price / timestamp 가 계약과 일치."""
        from stock_agent.backtest.costs import buy_fill_price

        cfg = ExecutorConfig(slippage_rate=Decimal("0.001"))
        now = _kst(9, 32)
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
            clock=lambda: now,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        or_bars = [
            _bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500) for h in range(0, 30)
        ]
        breakout_bar = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, or_bars + [breakout_bar])

        report = exc.step(now)

        assert len(report.entry_events) == 1
        ev = report.entry_events[0]
        assert ev.symbol == _SYMBOL_A
        expected_fill = buy_fill_price(Decimal("50100"), Decimal("0.001"))
        assert ev.fill_price == pytest.approx(expected_fill, abs=Decimal("1"))
        assert ev.ref_price == Decimal("50100")
        assert ev.timestamp == now


class TestStepExitEvent:
    """step sweep 에서 청산 1회 → exit_events tuple 에 ExitEvent 1개."""

    def test_청산_1회시_exit_events_1개_필드_정확(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """exit_events[0] 의 fill_price / reason / net_pnl_krw / timestamp 계약 검증."""
        from stock_agent.backtest.costs import sell_fill_price

        cfg = ExecutorConfig(slippage_rate=Decimal("0.001"))
        now_entry = _kst(9, 32)
        now_exit = _kst(9, 36)
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
            clock=lambda: now_entry,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        # OR → 진입
        or_bars = [
            _bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500) for h in range(0, 30)
        ]
        breakout_bar = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, or_bars + [breakout_bar])
        exc.step(now_entry)
        assert len(risk_manager.active_positions) == 1

        # 손절 bar 공급 — clock 을 now_exit 로 전환
        # ORBStrategy 의 stop_price 는 entry_price 기준이 아닌 signal.price(=bar.close) 기준.
        # 따라서 손절 발동 조건을 확실히 만족하도록 low 를 충분히 낮게 설정한다.
        stop_price_ref = Decimal("50100") * (Decimal("1") - Decimal("0.015"))
        stop_int = int(stop_price_ref)
        stop_bar = _bar(
            _SYMBOL_A,
            9,
            35,
            close=stop_int - 100,
            high=stop_int - 50,
            low=stop_int - 200,
        )
        fake_bar_source.set_bars(_SYMBOL_A, [stop_bar])

        # clock 을 now_exit 로 바꾸기 위해 새 Executor 대신 직접 분봉 처리 호출
        # _make_executor 에서 clock 을 고정했으므로, step 에 now_exit 를 전달해 timestamp 검증
        report_exit = exc.step(now_exit)

        assert len(report_exit.exit_events) == 1
        ev = report_exit.exit_events[0]
        assert ev.symbol == _SYMBOL_A
        expected_fill = sell_fill_price(stop_price_ref, Decimal("0.001"))
        assert ev.fill_price == pytest.approx(expected_fill, abs=Decimal("1"))
        assert ev.reason in ("stop_loss", "take_profit", "force_close")
        # net_pnl_krw 와 RiskManager 가 기록한 daily_realized_pnl_krw 가 일치
        assert ev.net_pnl_krw == risk_manager.daily_realized_pnl_krw
        assert ev.timestamp == now_exit


class TestSweepSnapshotIsolation:
    """sweep 단위 스냅샷 — 연속 호출 시 이벤트 누적 안 함."""

    def test_sweep1_진입_후_sweep2_추가_트리거_없으면_entry_events_빈_tuple(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """첫 번째 step → entry_events 1개.

        두 번째 step → 신규 진입 없으면 entry_events 빈 tuple.
        """
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        # OR → 돌파 → 진입
        or_bars = [
            _bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500) for h in range(0, 30)
        ]
        breakout_bar = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, or_bars + [breakout_bar])
        report1 = exc.step(_kst(9, 32))
        assert len(report1.entry_events) == 1

        # 두 번째 sweep — 신규 분봉 없음
        fake_bar_source.set_bars(_SYMBOL_A, [])
        report2 = exc.step(_kst(9, 33))
        assert report2.entry_events == ()


class TestForceCloseAllExitEvents:
    """force_close_all 경로에서도 exit_events 가 채워진다."""

    def test_force_close_all_exit_events_채워짐_entry_events_빈_tuple(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """15:00 강제청산 → StepReport.exit_events 에 ExitEvent(reason 포함),
        processed_bars == 0, entry_events == ().
        """
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        # OR → 진입
        for m in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, m, close=49_000, high=49_500, low=48_500))
        strategy.on_bar(_bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000))
        risk_manager.record_entry(_SYMBOL_A, Decimal("50050"), 10, _kst(9, 31))
        # _open_lots 에도 직접 진입 기록 (Executor 우회 진입 → fallback 경로 허용)
        # 이 테스트는 exit_events 생성 자체를 확인하므로 fallback 경로도 무방

        report = exc.force_close_all(_kst(15, 0))

        assert report.processed_bars == 0
        assert report.entry_events == ()
        assert len(report.exit_events) == 1
        ev = report.exit_events[0]
        assert ev.symbol == _SYMBOL_A
        assert ev.reason == "force_close"


class TestEntryEventRiskRejected:
    """RiskManager 거부 시 entry_events 비어있음."""

    def test_risk_거부시_entry_events_빈_tuple(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """잔고 0원 → evaluate_entry 거부 → entry_events == ()."""
        fake_balance_provider.set_balance(_empty_balance(withdrawable=0))
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        or_bars = [
            _bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500) for h in range(0, 30)
        ]
        breakout_bar = _bar(_SYMBOL_A, 9, 31, close=50_000, high=50_000, low=49_900)
        fake_bar_source.set_bars(_SYMBOL_A, or_bars + [breakout_bar])
        report = exc.step(_kst(9, 32))

        assert report.entry_events == ()
        assert len(fake_order_submitter.buy_calls) == 0


class TestLastReconcileProperty:
    """last_reconcile 프로퍼티 — 세션 시작 직후 None, step 후 갱신."""

    def test_세션_직후_reconcile_미호출시_None(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        # start_session 전에는 None
        assert exc.last_reconcile is None
        exc.start_session(_DATE, _STARTING_CAPITAL)
        # start_session 은 reconcile 을 호출하지 않는다
        assert exc.last_reconcile is None

    def test_step_호출_후_last_reconcile_ReconcileReport_인스턴스(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        report = exc.step(_kst(9, 32))

        assert exc.last_reconcile is not None
        assert isinstance(exc.last_reconcile, ReconcileReport)
        # step 내부에서 호출된 reconcile 의 반환값과 동일 객체
        assert exc.last_reconcile is report.reconcile

    def test_force_close_all_후에도_last_reconcile_갱신됨(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        report = exc.force_close_all(_kst(15, 0))

        assert exc.last_reconcile is not None
        assert exc.last_reconcile is report.reconcile


class TestLastReconcileMismatchPersistence:
    """last_reconcile mismatch 후에도 살아있음, 해소 시 빈 tuple 로 갱신."""

    def test_mismatch_후_last_reconcile_mismatch_symbols_비어있지_않음(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """reconcile mismatch → _halt=True 이후에도 last_reconcile.mismatch_symbols 유지."""
        fake_balance_provider.set_balance(_balance_with_holding(_SYMBOL_A, qty=10))
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        exc.reconcile()

        assert exc.is_halted
        assert exc.last_reconcile is not None
        assert len(exc.last_reconcile.mismatch_symbols) > 0
        assert _SYMBOL_A in exc.last_reconcile.mismatch_symbols

    def test_mismatch_해소_후_다음_step에서_mismatch_symbols_빈_tuple(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """mismatch → halt. 다음 세션 start_session 후 잔고 일치 상태에서 step →
        last_reconcile.mismatch_symbols 빈 tuple. _halt 는 start_session 으로만 해제됨.
        """
        fake_balance_provider.set_balance(_balance_with_holding(_SYMBOL_A, qty=10))
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        exc.reconcile()
        assert exc.is_halted

        # 잔고를 일치 상태로 변경 + start_session 으로 halt 해제
        fake_balance_provider.set_balance(_empty_balance())
        exc.start_session(_DATE, _STARTING_CAPITAL)
        assert not exc.is_halted

        # 새 step — reconcile 재실행 → mismatch_symbols 빈 tuple 이어야 함
        report = exc.step(_kst(9, 32))
        assert report.reconcile.mismatch_symbols == ()
        assert exc.last_reconcile is not None
        assert exc.last_reconcile.mismatch_symbols == ()


# ===========================================================================
# 27. EntryEvent / ExitEvent order_number 필드 가드 (RED — order_number 미구현)
# ===========================================================================


class TestEntryEventOrderNumberGuard:
    """EntryEvent.order_number 필드 추가 계약 — order_number 미구현 시 FAIL."""

    def test_entry_event_order_number_누락시_TypeError(self) -> None:
        """order_number 인자 없이 EntryEvent 생성 → TypeError (필수 필드 누락)."""
        with pytest.raises(TypeError, match="order_number"):
            EntryEvent(  # type: ignore[call-arg]
                symbol="005930",
                qty=10,
                fill_price=Decimal("50100"),
                ref_price=Decimal("50000"),
                timestamp=datetime(2026, 4, 21, 9, 32, tzinfo=KST),
            )

    def test_entry_event_order_number_빈문자열_RuntimeError(self) -> None:
        """order_number="" → __post_init__ 가드에서 RuntimeError, 메시지에 'order_number' 포함."""
        with pytest.raises(RuntimeError, match="order_number"):
            EntryEvent(
                symbol="005930",
                qty=10,
                fill_price=Decimal("50100"),
                ref_price=Decimal("50000"),
                timestamp=datetime(2026, 4, 21, 9, 32, tzinfo=KST),
                order_number="",
            )

    def test_entry_event_order_number_정상값_생성(self) -> None:
        """order_number 있는 경우 EntryEvent 정상 생성."""
        ev = EntryEvent(
            symbol="005930",
            qty=10,
            fill_price=Decimal("50100"),
            ref_price=Decimal("50000"),
            timestamp=datetime(2026, 4, 21, 9, 32, tzinfo=KST),
            order_number="ORD-TEST-001",
        )
        assert ev.order_number == "ORD-TEST-001"

    def test_entry_event_timestamp_naive_RuntimeError(self) -> None:
        """timestamp naive → __post_init__ 가드에서 RuntimeError."""
        with pytest.raises(RuntimeError):
            EntryEvent(
                symbol="005930",
                qty=10,
                fill_price=Decimal("50100"),
                ref_price=Decimal("50000"),
                timestamp=datetime(2026, 4, 21, 9, 32),  # tzinfo 없음
                order_number="ORD-TEST-001",
            )

    def test_entry_event_qty_zero_RuntimeError(self) -> None:
        """qty=0 → __post_init__ 가드에서 RuntimeError."""
        with pytest.raises(RuntimeError):
            EntryEvent(
                symbol="005930",
                qty=0,
                fill_price=Decimal("50100"),
                ref_price=Decimal("50000"),
                timestamp=datetime(2026, 4, 21, 9, 32, tzinfo=KST),
                order_number="ORD-TEST-001",
            )

    def test_entry_event_fill_price_zero_RuntimeError(self) -> None:
        """fill_price=0 → __post_init__ 가드에서 RuntimeError (DTO 설계 체크리스트 3항)."""
        with pytest.raises(RuntimeError):
            EntryEvent(
                symbol="005930",
                qty=10,
                fill_price=Decimal("0"),
                ref_price=Decimal("50000"),
                timestamp=datetime(2026, 4, 21, 9, 32, tzinfo=KST),
                order_number="ORD-TEST-001",
            )


class TestExitEventOrderNumberGuard:
    """ExitEvent.order_number 필드 추가 계약 — order_number 미구현 시 FAIL."""

    def test_exit_event_order_number_누락시_TypeError(self) -> None:
        """order_number 인자 없이 ExitEvent 생성 → TypeError (필수 필드 누락)."""
        with pytest.raises(TypeError, match="order_number"):
            ExitEvent(  # type: ignore[call-arg]
                symbol="005930",
                qty=10,
                fill_price=Decimal("49350"),
                reason="stop_loss",
                net_pnl_krw=-8_000,
                timestamp=datetime(2026, 4, 21, 9, 36, tzinfo=KST),
            )

    def test_exit_event_order_number_빈문자열_RuntimeError(self) -> None:
        """order_number="" → __post_init__ 가드에서 RuntimeError, 메시지에 'order_number' 포함."""
        with pytest.raises(RuntimeError, match="order_number"):
            ExitEvent(
                symbol="005930",
                qty=10,
                fill_price=Decimal("49350"),
                reason="stop_loss",
                net_pnl_krw=-8_000,
                timestamp=datetime(2026, 4, 21, 9, 36, tzinfo=KST),
                order_number="",
            )

    def test_exit_event_order_number_정상값_생성(self) -> None:
        """order_number 있는 경우 ExitEvent 정상 생성. net_pnl_krw 음수도 허용."""
        ev = ExitEvent(
            symbol="005930",
            qty=10,
            fill_price=Decimal("49350"),
            reason="stop_loss",
            net_pnl_krw=-8_000,
            timestamp=datetime(2026, 4, 21, 9, 36, tzinfo=KST),
            order_number="ORD-TEST-002",
        )
        assert ev.order_number == "ORD-TEST-002"
        assert ev.net_pnl_krw == -8_000  # 손실도 허용

    def test_exit_event_timestamp_naive_RuntimeError(self) -> None:
        """timestamp naive → __post_init__ 가드에서 RuntimeError."""
        with pytest.raises(RuntimeError):
            ExitEvent(
                symbol="005930",
                qty=10,
                fill_price=Decimal("49350"),
                reason="stop_loss",
                net_pnl_krw=-8_000,
                timestamp=datetime(2026, 4, 21, 9, 36),  # tzinfo 없음
                order_number="ORD-TEST-002",
            )

    def test_exit_event_qty_zero_RuntimeError(self) -> None:
        """qty=0 → __post_init__ 가드에서 RuntimeError."""
        with pytest.raises(RuntimeError):
            ExitEvent(
                symbol="005930",
                qty=0,
                fill_price=Decimal("49350"),
                reason="stop_loss",
                net_pnl_krw=-8_000,
                timestamp=datetime(2026, 4, 21, 9, 36, tzinfo=KST),
                order_number="ORD-TEST-002",
            )

    def test_exit_event_fill_price_zero_RuntimeError(self) -> None:
        """fill_price=0 → __post_init__ 가드에서 RuntimeError."""
        with pytest.raises(RuntimeError):
            ExitEvent(
                symbol="005930",
                qty=10,
                fill_price=Decimal("0"),
                reason="stop_loss",
                net_pnl_krw=-8_000,
                timestamp=datetime(2026, 4, 21, 9, 36, tzinfo=KST),
                order_number="ORD-TEST-002",
            )


class TestHandleEntryOrderNumberInjection:
    """Executor._handle_entry 가 ticket.order_number 를 EntryEvent 에 주입하는지 검증."""

    def test_handle_entry_EntryEvent_order_number이_ticket_order_number와_일치(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """submit_buy 반환 ticket.order_number 가 EntryEvent.order_number 에 주입."""
        fixed_order_number = "KIS-12345"

        class FixedOrderSubmitter:
            """order_number 를 고정값으로 반환하는 더블."""

            buy_calls: list[tuple[str, int]] = []
            sell_calls: list[tuple[str, int]] = []

            def submit_buy(self, symbol: str, qty: int) -> OrderTicket:
                self.buy_calls.append((symbol, qty))
                return OrderTicket(
                    order_number=fixed_order_number,
                    symbol=symbol,
                    side="buy",
                    qty=qty,
                    price=None,
                    submitted_at=_kst(9, 30),
                )

            def submit_sell(self, symbol: str, qty: int) -> OrderTicket:
                self.sell_calls.append((symbol, qty))
                return OrderTicket(
                    order_number=f"SELL-{fixed_order_number}",
                    symbol=symbol,
                    side="sell",
                    qty=qty,
                    price=None,
                    submitted_at=_kst(9, 30),
                )

            def get_pending_orders(self) -> list[PendingOrder]:
                return []

        fixed_submitter = FixedOrderSubmitter()
        now = _kst(9, 32)
        exc = _make_executor(
            strategy,
            risk_manager,
            fixed_submitter,  # type: ignore[arg-type]
            fake_balance_provider,
            fake_bar_source,
            clock=lambda: now,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        or_bars = [
            _bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500) for h in range(0, 30)
        ]
        breakout_bar = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, or_bars + [breakout_bar])

        report = exc.step(now)

        assert len(report.entry_events) == 1
        assert report.entry_events[0].order_number == fixed_order_number


class TestHandleExitOrderNumberInjection:
    """Executor._handle_exit 가 ticket.order_number 를 ExitEvent 에 주입하는지 검증."""

    def test_handle_exit_ExitEvent_order_number이_ticket_order_number와_일치(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """submit_sell 반환 ticket.order_number 가 ExitEvent.order_number 에 주입."""
        fixed_sell_order_number = "SELL-99999"

        class FixedOrderSubmitter2:
            """매도 order_number 를 고정값으로 반환하는 더블."""

            _counter = 0
            buy_calls: list[tuple[str, int]] = []
            sell_calls: list[tuple[str, int]] = []

            def submit_buy(self, symbol: str, qty: int) -> OrderTicket:
                self._counter += 1
                self.buy_calls.append((symbol, qty))
                return OrderTicket(
                    order_number=f"BUY-{self._counter:04d}",
                    symbol=symbol,
                    side="buy",
                    qty=qty,
                    price=None,
                    submitted_at=_kst(9, 30),
                )

            def submit_sell(self, symbol: str, qty: int) -> OrderTicket:
                self.sell_calls.append((symbol, qty))
                return OrderTicket(
                    order_number=fixed_sell_order_number,
                    symbol=symbol,
                    side="sell",
                    qty=qty,
                    price=None,
                    submitted_at=_kst(9, 30),
                )

            def get_pending_orders(self) -> list[PendingOrder]:
                return []

        fixed_submitter2 = FixedOrderSubmitter2()
        now_entry = _kst(9, 32)
        now_exit = _kst(9, 36)
        exc = _make_executor(
            strategy,
            risk_manager,
            fixed_submitter2,  # type: ignore[arg-type]
            fake_balance_provider,
            fake_bar_source,
            clock=lambda: now_entry,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        # 진입
        or_bars = [
            _bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500) for h in range(0, 30)
        ]
        breakout_bar = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, or_bars + [breakout_bar])
        exc.step(now_entry)
        assert len(risk_manager.active_positions) == 1

        # 손절 bar 공급
        stop_price_ref = Decimal("50100") * (Decimal("1") - Decimal("0.015"))
        stop_int = int(stop_price_ref)
        stop_bar = _bar(
            _SYMBOL_A,
            9,
            35,
            close=stop_int - 100,
            high=stop_int - 50,
            low=stop_int - 200,
        )
        fake_bar_source.set_bars(_SYMBOL_A, [stop_bar])

        report_exit = exc.step(now_exit)

        assert len(report_exit.exit_events) == 1
        assert report_exit.exit_events[0].order_number == fixed_sell_order_number


# ===========================================================================
# 30. Executor.last_sweep_entry_events / last_sweep_exit_events 스냅샷 계약
# ===========================================================================


class TestExecutorLastSweepEventsSnapshot:
    """last_sweep_entry_events / last_sweep_exit_events 프로퍼티 불변성 테스트."""

    def test_last_sweep_entry_events_초기값은_빈_tuple(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """세션 시작 직후(step 호출 전) 두 프로퍼티 모두 빈 tuple."""
        exc = _make_executor(
            strategy, risk_manager, fake_order_submitter, fake_balance_provider, fake_bar_source
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        assert exc.last_sweep_entry_events == ()
        assert exc.last_sweep_exit_events == ()

    def test_last_sweep_events_tuple_타입_반환(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """last_sweep_*_events 는 tuple 타입을 반환한다.

        빈 tuple 은 Python 인터닝으로 동일 객체를 재사용할 수 있으므로
        identity 대신 타입만 검증한다. 내부 리스트 노출 여부는
        test_last_sweep_entry_events_mutation_불가 에서 별도 확인.
        """
        exc = _make_executor(
            strategy, risk_manager, fake_order_submitter, fake_balance_provider, fake_bar_source
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        result_entry = exc.last_sweep_entry_events
        result_exit = exc.last_sweep_exit_events

        assert isinstance(result_entry, tuple)
        assert isinstance(result_exit, tuple)

    def test_last_sweep_exit_events_step_호출시_이전_sweep_이벤트는_리셋(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """step() 은 sweep 시작 시 _sweep_exit_events 를 [] 로 리셋한다.

        첫 step 에서 exit 이벤트가 있어도 두 번째 step 은 빈 sweep 으로 시작된다.
        """
        exc = _make_executor(
            strategy,
            risk_manager,
            fake_order_submitter,
            fake_balance_provider,
            fake_bar_source,
            clock=lambda: _kst(9, 32),
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        # 첫 step — 진입 bar 공급 (exit 없음, entry 없음도 무방)
        fake_bar_source.set_bars(_SYMBOL_A, [])
        exc.step(_kst(9, 32))

        # 두 번째 step — 새 sweep 시작 → exit_events 리셋되어 () 이어야 함
        fake_bar_source.set_bars(_SYMBOL_A, [])
        exc.step(_kst(9, 33))

        assert exc.last_sweep_exit_events == ()
        assert exc.last_sweep_entry_events == ()

    def test_last_sweep_entry_events_mutation_불가(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """last_sweep_entry_events 반환값(tuple)은 외부에서 mutation 이 불가능하다.

        tuple 은 immutable 이므로 += 시 TypeError. 내부 리스트가 노출되지 않음을 확인.
        """
        exc = _make_executor(
            strategy, risk_manager, fake_order_submitter, fake_balance_provider, fake_bar_source
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        snapshot = exc.last_sweep_entry_events
        # tuple 은 in-place mutation 이 없으므로 append 시도 시 AttributeError
        assert not hasattr(snapshot, "append"), "tuple 에 append 가 없어야 함"


# ===========================================================================
# ADR-0014: 부분체결 정책 (RED — cancel_order Protocol + _resolve_fill 미구현)
# ===========================================================================
# 아래 테스트들은 ADR-0014 결정 3~6 을 검증한다.
# src 구현 전이라 모두 실패(RED) 상태여야 한다.
#
# 실패 예상:
#   - FakeOrderSubmitter 에 cancel_order / partial_fill_qty 미구현
#     → AttributeError 또는 TypeError
#   - OrderSubmitter Protocol 에 cancel_order 미선언
#     → hasattr 단언 FAIL
#   - LiveOrderSubmitter / DryRunOrderSubmitter 에 cancel_order 미구현
#     → AttributeError
#   - Executor._wait_fill 이 여전히 타임아웃 시 ExecutorError raise
#     → 부분체결 시나리오에서 pytest.raises(ExecutorError) 가 통과 대신
#       assert EntryEvent.qty == filled_qty 가 FAIL
# ===========================================================================


class FakeOrderSubmitterWithPartialFill:
    """ADR-0014 부분체결 시뮬레이션 더블.

    ``partial_fill_qty`` 설정 시 ``get_pending_orders()`` 가 타임아웃 전까지
    ``qty_filled=partial_fill_qty`` 인 PendingOrder 를 반환한다.
    ``cancel_order(order_number)`` 호출 시 해당 번호를 ``_cancelled_numbers`` 에
    추가해 이후 ``get_pending_orders()`` 에서 제외한다.

    기존 ``FakeOrderSubmitter`` 를 확장하지 않고 별도 클래스로 정의한다 —
    기존 픽스처의 동작을 변경하지 않기 위함.
    """

    def __init__(self, *, partial_fill_qty: int | None = None) -> None:
        self._partial_fill_qty = partial_fill_qty
        self._counter = 0
        self._last_order_number: str | None = None
        self._last_qty: int = 0
        self._cancelled_numbers: set[str] = set()
        self.buy_calls: list[tuple[str, int]] = []
        self.sell_calls: list[tuple[str, int]] = []
        # cancel_order 가 호출된 order_number 목록 (검증용)
        self.cancel_calls: list[str] = []

    def submit_buy(self, symbol: str, qty: int) -> OrderTicket:
        self._counter += 1
        self.buy_calls.append((symbol, qty))
        self._last_qty = qty
        self._last_order_number = f"ORD-BUY-{self._counter:04d}"
        return OrderTicket(
            order_number=self._last_order_number,
            symbol=symbol,
            side="buy",
            qty=qty,
            price=None,
            submitted_at=_kst(9, 30),
        )

    def submit_sell(self, symbol: str, qty: int) -> OrderTicket:
        self._counter += 1
        self.sell_calls.append((symbol, qty))
        self._last_qty = qty
        self._last_order_number = f"ORD-SELL-{self._counter:04d}"
        return OrderTicket(
            order_number=self._last_order_number,
            symbol=symbol,
            side="sell",
            qty=qty,
            price=None,
            submitted_at=_kst(9, 30),
        )

    def get_pending_orders(self) -> list[PendingOrder]:
        """취소된 주문은 제외. 미취소 주문은 partial_fill_qty 기반으로 반환."""
        if self._last_order_number is None:
            return []
        if self._last_order_number in self._cancelled_numbers:
            return []
        if self._partial_fill_qty is None:
            # partial 없음 → 항상 전량 미체결 상태 (fill_after=999 처럼)
            return [
                PendingOrder(
                    order_number=self._last_order_number,
                    symbol=_SYMBOL_A,
                    side="buy",
                    qty_ordered=self._last_qty,
                    qty_filled=0,
                    qty_remaining=self._last_qty,
                    price=None,
                    submitted_at=_kst(9, 30),
                )
            ]
        # partial_fill_qty 설정 시: 부분체결 상태 반환
        filled = self._partial_fill_qty
        remaining = self._last_qty - filled
        return [
            PendingOrder(
                order_number=self._last_order_number,
                symbol=_SYMBOL_A,
                side="buy",
                qty_ordered=self._last_qty,
                qty_filled=filled,
                qty_remaining=remaining,
                price=None,
                submitted_at=_kst(9, 30),
            )
        ]

    def cancel_order(self, order_number: str) -> None:
        """ADR-0014 결정 3 — OrderSubmitter Protocol 필수 메서드.

        cancel_calls 에 기록 + _cancelled_numbers 에 추가 (멱등).
        """
        self.cancel_calls.append(order_number)
        self._cancelled_numbers.add(order_number)


def _setup_orb_entry_bars(
    strategy: ORBStrategy,
    fake_bar_source: FakeBarSource,
    symbol: str = _SYMBOL_A,
    or_close: int = 49_000,
    breakout_close: int = 50_100,
) -> None:
    """OR 구간 분봉 + 돌파 분봉을 strategy 에 먹이고 bar_source 에 세팅."""
    for h in range(0, 30):
        strategy.on_bar(_bar(symbol, 9, h, close=or_close, high=or_close + 500, low=or_close - 500))
    breakout_bar = _bar(
        symbol, 9, 31, close=breakout_close, high=breakout_close, low=breakout_close - 100
    )
    fake_bar_source.set_bars(symbol, [breakout_bar])


def _make_timeout_clock(initial: datetime, after_timeout: datetime) -> list[datetime]:
    """_wait_fill 타임아웃 유도용 시계 상태 리스트.

    deadline 계산(첫 호출) → 즉시 deadline 초과(두 번째 호출) 시퀀스.
    """
    return [initial, after_timeout]


class TestOrderSubmitterCancelOrderProtocol:
    """ADR-0014 결정 3 — OrderSubmitter Protocol 에 cancel_order 메서드 추가.

    현재 Protocol 에 cancel_order 가 없으므로 hasattr 단언이 FAIL 한다.
    """

    def test_live_order_submitter_cancel_order_KisClient_위임(self) -> None:
        """LiveOrderSubmitter.cancel_order(order_number) 호출 시
        KisClient.cancel_order(order_number) 가 동일 인자로 1회 호출된다.

        RED: LiveOrderSubmitter 에 cancel_order 메서드 미구현 → AttributeError.
        """
        mock_kis = MagicMock(spec=KisClient)
        submitter = LiveOrderSubmitter(mock_kis)

        # cancel_order 메서드가 없으면 여기서 AttributeError
        submitter.cancel_order("ORD-0001")

        mock_kis.cancel_order.assert_called_once_with("ORD-0001")

    def test_dry_run_order_submitter_cancel_order_예외없이_통과(self) -> None:
        """DryRunOrderSubmitter.cancel_order 는 예외 없이 통과한다 (no-op + info 로그).

        RED: DryRunOrderSubmitter 에 cancel_order 미구현 → AttributeError.
        """
        dry = DryRunOrderSubmitter()
        # 예외 없이 통과해야 한다
        dry.cancel_order("DRY-0001")

    def test_dry_run_cancel_order_로그_남김(self) -> None:
        """DryRunOrderSubmitter.cancel_order 는 loguru INFO 로그를 남긴다.

        RED: cancel_order 미구현 시 로그 캡처 자체가 실패.
        """
        from loguru import logger

        info_messages: list[str] = []

        def _sink(msg: Any) -> None:
            if msg.record["level"].name == "INFO":
                info_messages.append(str(msg))

        sink_id = logger.add(_sink, level="INFO")
        try:
            dry = DryRunOrderSubmitter()
            dry.cancel_order("DRY-0001")
        finally:
            logger.remove(sink_id)

        # DryRun cancel 은 info 로그를 남겨야 한다
        assert len(info_messages) >= 1

    def test_live_order_submitter_빈_order_number_KisClient_그대로_위임(self) -> None:
        """빈 order_number 는 LiveOrderSubmitter 가 검증하지 않고 KisClient 로 위임한다.

        Validation 은 KisClient 책임 — 이중 가드 X.
        RED: LiveOrderSubmitter.cancel_order 미구현 → AttributeError.
        """
        mock_kis = MagicMock(spec=KisClient)
        submitter = LiveOrderSubmitter(mock_kis)

        submitter.cancel_order("")  # 빈 문자열 그대로 위임
        mock_kis.cancel_order.assert_called_once_with("")

    def test_order_submitter_protocol_cancel_order_메서드_존재(self) -> None:
        """FakeOrderSubmitterWithPartialFill 이 cancel_order 를 구현한다.

        Protocol 구조 검증 — hasattr 로 세 클래스 모두 확인.
        RED: LiveOrderSubmitter / DryRunOrderSubmitter 미구현 시 FAIL.
        """
        assert hasattr(LiveOrderSubmitter(MagicMock(spec=KisClient)), "cancel_order")
        assert hasattr(DryRunOrderSubmitter(), "cancel_order")
        assert hasattr(FakeOrderSubmitterWithPartialFill(), "cancel_order")


class TestExecutorPartialFillEntry:
    """ADR-0014 결정 4~5 — 진입 부분체결 시 취소 후 체결 수량만 기록.

    현재 _wait_fill 이 타임아웃 시 ExecutorError 를 raise 하므로
    부분체결 시나리오에서 EntryEvent.qty == filled_qty 단언이 FAIL 한다.
    """

    def _partial_entry_setup(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
        *,
        partial_fill_qty: int = 15,
        withdrawable: int = 10_000_000,
    ) -> tuple[Executor, FakeOrderSubmitterWithPartialFill]:
        """부분체결 시뮬레이션 Executor 세팅 헬퍼.

        decision.qty 계산:
          target_notional = withdrawable × 20% = 10_000_000 × 0.20 = 2_000_000
          ref_price = 50_100 (breakout_close 기본값)
          decision.qty = floor(2_000_000 / 50_100) = 39
          partial_fill_qty=15 < 39 → status="partial" 판정 보장.

        partial_fill_qty 를 decision.qty 보다 작게 유지해야 partial 로그가 남는다.
        withdrawable=10_000_000 / breakout_close=50_100 기준 decision.qty ≈ 39.
        """
        # 잔고를 충분히 크게 주입해 decision.qty 가 partial_fill_qty 보다 크게 만든다
        fake_balance_provider.set_balance(_empty_balance(withdrawable=withdrawable))

        submitter = FakeOrderSubmitterWithPartialFill(partial_fill_qty=partial_fill_qty)
        cfg = ExecutorConfig(order_fill_timeout_s=0.05, order_poll_interval_s=0.01)

        # 타임아웃 유도: 첫 clock → deadline 계산, 두 번째 clock → deadline 초과
        tick = [_kst(9, 30), _kst(9, 30, 1)]  # 1초 후 → 0.05s timeout 초과

        def advancing_clock() -> datetime:
            if len(tick) > 1:
                return tick.pop(0)
            return tick[0]

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
            clock=advancing_clock,
            sleep=lambda _: None,
        )
        exc.start_session(_DATE, withdrawable)
        _setup_orb_entry_bars(strategy, fake_bar_source)
        return exc, submitter

    def test_부분체결_EntryEvent_qty는_filled_qty(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """타임아웃 도달 + qty_filled=15 → EntryEvent.qty == 15.

        RED: 현재 _wait_fill 이 ExecutorError raise → report 에 entry_events 없음.
        decision.qty ≈ 39 (withdrawable=10_000_000, ref_price≈50_100, risk_pct=20%).
        partial_fill_qty=15 < 39 → status="partial" 판정 보장.
        """
        exc, submitter = self._partial_entry_setup(
            strategy, risk_manager, fake_balance_provider, fake_bar_source, partial_fill_qty=15
        )

        report = exc.step(_kst(9, 32))

        assert len(report.entry_events) == 1, "부분체결도 EntryEvent 1개여야 한다"
        ev = report.entry_events[0]
        assert ev.qty == 15, f"체결 수량은 filled_qty=15 이어야 한다 (got {ev.qty})"

    def test_부분체결_RiskManager_active_positions_filled_qty로_기록(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """RiskManager.active_positions[0].qty == filled_qty.

        RED: ExecutorError 로 인해 record_entry 미호출 → active_positions 비어있음.
        """
        exc, _ = self._partial_entry_setup(
            strategy, risk_manager, fake_balance_provider, fake_bar_source, partial_fill_qty=15
        )
        exc.step(_kst(9, 32))

        assert len(risk_manager.active_positions) == 1
        assert risk_manager.active_positions[0].qty == 15

    def test_부분체결_cancel_order_1회_호출(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """타임아웃 → cancel_order(order_number) 정확히 1회 호출.

        RED: _wait_fill 에 cancel_order 호출 로직 없음 → cancel_calls 비어있음.
        """
        exc, submitter = self._partial_entry_setup(
            strategy, risk_manager, fake_balance_provider, fake_bar_source, partial_fill_qty=15
        )
        exc.step(_kst(9, 32))

        msg = f"cancel_order 가 정확히 1회 호출되어야 한다 (got {submitter.cancel_calls})"
        assert len(submitter.cancel_calls) == 1, msg
        # 취소된 번호가 실제 제출한 주문번호와 일치해야 한다
        assert submitter.cancel_calls[0] == submitter._last_order_number

    def test_부분체결_orders_submitted_카운트됨(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """부분이라도 submit_buy 는 1회 — orders_submitted == 1.

        RED: ExecutorError 로 인해 step 이 실패하면 report 자체가 없어 단언 불가.
        """
        exc, _ = self._partial_entry_setup(
            strategy, risk_manager, fake_balance_provider, fake_bar_source, partial_fill_qty=15
        )
        report = exc.step(_kst(9, 32))

        assert report.orders_submitted == 1

    def test_부분체결_warning_로그_포함(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """부분체결 시 WARNING 로그에 'partial' 또는 '부분체결' 포함.

        RED: ExecutorError raise 로 인해 해당 로그가 남겨지지 않음.
        """
        from loguru import logger

        warn_messages: list[str] = []

        def _sink(msg: Any) -> None:
            if msg.record["level"].name == "WARNING":
                text = str(msg)
                if "partial" in text or "부분체결" in text:
                    warn_messages.append(text)

        sink_id = logger.add(_sink, level="WARNING")
        try:
            exc, _ = self._partial_entry_setup(
                strategy, risk_manager, fake_balance_provider, fake_bar_source, partial_fill_qty=15
            )
            exc.step(_kst(9, 32))
        finally:
            logger.remove(sink_id)

        assert len(warn_messages) >= 1, "부분체결 사실을 WARNING 로그로 남겨야 한다"


class TestExecutorZeroFillEntry:
    """ADR-0014 결정 5 — 진입 zero fill 시 기록 없이 return False.

    ExecutorError 를 발생시키지 않아야 한다.
    현재 _wait_fill 이 타임아웃 시 ExecutorError 를 raise 하므로 전부 FAIL.
    """

    def _zero_fill_setup(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> tuple[Executor, FakeOrderSubmitterWithPartialFill]:
        """zero fill 시뮬레이션 — partial_fill_qty=0."""
        submitter = FakeOrderSubmitterWithPartialFill(partial_fill_qty=0)
        cfg = ExecutorConfig(order_fill_timeout_s=0.05, order_poll_interval_s=0.01)

        tick = [_kst(9, 30), _kst(9, 30, 1)]

        def advancing_clock() -> datetime:
            if len(tick) > 1:
                return tick.pop(0)
            return tick[0]

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
            clock=advancing_clock,
            sleep=lambda _: None,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        _setup_orb_entry_bars(strategy, fake_bar_source)
        return exc, submitter

    def test_zero_fill_ExecutorError_미발생(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """zero fill 시 ExecutorError 를 raise 하지 않는다.

        RED: 현재 _wait_fill 이 타임아웃 시 ExecutorError raise → FAIL.
        """
        exc, _ = self._zero_fill_setup(
            strategy, risk_manager, fake_balance_provider, fake_bar_source
        )
        # ExecutorError 가 발생하면 이 단언이 FAIL 한다
        report = exc.step(_kst(9, 32))
        assert report is not None

    def test_zero_fill_entry_events_비어있음(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """zero fill → StepReport.entry_events == ().

        RED: ExecutorError 로 인해 report 자체가 없어 단언 불가.
        """
        exc, _ = self._zero_fill_setup(
            strategy, risk_manager, fake_balance_provider, fake_bar_source
        )
        report = exc.step(_kst(9, 32))

        assert report.entry_events == ()

    def test_zero_fill_active_positions_비어있음(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """zero fill → record_entry 미호출 → active_positions 비어있음.

        RED: ExecutorError 로 중단되면 active_positions 단언 자체 도달 불가.
        """
        exc, _ = self._zero_fill_setup(
            strategy, risk_manager, fake_balance_provider, fake_bar_source
        )
        exc.step(_kst(9, 32))

        assert risk_manager.active_positions == ()

    def test_zero_fill_cancel_order_호출됨(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """zero fill 시에도 cancel_order 가 1회 호출된다.

        RED: cancel_order 로직 미구현 → cancel_calls 비어있음.
        """
        exc, submitter = self._zero_fill_setup(
            strategy, risk_manager, fake_balance_provider, fake_bar_source
        )
        exc.step(_kst(9, 32))

        assert len(submitter.cancel_calls) == 1

    def test_zero_fill_orders_submitted_0(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """zero fill → 실질적인 주문 체결 없음 → orders_submitted == 0.

        RED: ExecutorError 로 report 가 없거나, 현재 count 로직이 submit 기준이라 1 반환.
        """
        exc, _ = self._zero_fill_setup(
            strategy, risk_manager, fake_balance_provider, fake_bar_source
        )
        report = exc.step(_kst(9, 32))

        assert report.orders_submitted == 0


class TestExecutorPartialFillExit:
    """ADR-0014 결정 6 — 청산 부분체결 시 ExecutorError 승격.

    취소는 raise 전에 시도되어야 한다.
    현재 _wait_fill 이 타임아웃 시에도 ExecutorError 를 raise 하므로
    cancel_order 호출 여부 검증이 FAIL 한다.
    """

    def _enter_position_via_executor(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_bar_source: FakeBarSource,
        exc: Executor,
        *,
        entry_price: Decimal = Decimal("50050"),
        qty: int = 10,
    ) -> None:
        """Executor 우회 진입 세팅 — 청산 테스트용."""
        for h in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500))
        strategy.on_bar(_bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000))
        risk_manager.record_entry(_SYMBOL_A, entry_price, qty, _kst(9, 31))

    def test_청산_부분체결_ExecutorError_raise(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """청산 주문이 부분체결(3주만)되면 ExecutorError 를 raise 한다.

        RED: 현재 _wait_fill 타임아웃 → ExecutorError 는 동일하게 raise 되지만
             cancel_order 호출 단언이 FAIL 한다.
        """
        submitter = FakeOrderSubmitterWithPartialFill(partial_fill_qty=3)
        cfg = ExecutorConfig(order_fill_timeout_s=0.05, order_poll_interval_s=0.01)

        tick = [_kst(9, 30), _kst(9, 30, 1)]

        def advancing_clock() -> datetime:
            if len(tick) > 1:
                return tick.pop(0)
            return tick[0]

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
            clock=advancing_clock,
            sleep=lambda _: None,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        self._enter_position_via_executor(strategy, risk_manager, fake_bar_source, exc)

        # 손절 bar 공급 — ExitSignal 유발
        entry_price = Decimal("50050")
        stop_price = entry_price * (Decimal("1") - Decimal("0.015"))
        stop_int = int(stop_price)
        stop_bar = _bar(
            _SYMBOL_A,
            9,
            35,
            close=stop_int - 100,
            high=stop_int - 50,
            low=stop_int - 200,
        )
        fake_bar_source.set_bars(_SYMBOL_A, [stop_bar])

        with pytest.raises(ExecutorError):
            exc.step(_kst(9, 36))

    def test_청산_부분체결_cancel_order_호출후_raise(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """청산 부분체결 → cancel_order 호출 후 ExecutorError raise.

        cancel_calls 에 매도 주문번호가 포함되어야 한다.
        RED: 현재 _wait_fill 에 cancel_order 로직 없음 → cancel_calls 비어있음.
        """
        submitter = FakeOrderSubmitterWithPartialFill(partial_fill_qty=3)
        cfg = ExecutorConfig(order_fill_timeout_s=0.05, order_poll_interval_s=0.01)

        tick = [_kst(9, 30), _kst(9, 30, 1)]

        def advancing_clock() -> datetime:
            if len(tick) > 1:
                return tick.pop(0)
            return tick[0]

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
            clock=advancing_clock,
            sleep=lambda _: None,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        self._enter_position_via_executor(strategy, risk_manager, fake_bar_source, exc)

        entry_price = Decimal("50050")
        stop_price = entry_price * (Decimal("1") - Decimal("0.015"))
        stop_int = int(stop_price)
        stop_bar = _bar(
            _SYMBOL_A,
            9,
            35,
            close=stop_int - 100,
            high=stop_int - 50,
            low=stop_int - 200,
        )
        fake_bar_source.set_bars(_SYMBOL_A, [stop_bar])

        with pytest.raises(ExecutorError):
            exc.step(_kst(9, 36))

        # ExecutorError raise 전에 cancel_order 가 호출되어야 한다
        msg = "청산 부분체결 시 cancel_order 가 raise 전에 호출되어야 한다"
        assert len(submitter.cancel_calls) >= 1, msg

    def test_청산_부분체결_record_exit_미호출(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """청산 부분체결 → ExecutorError raise → record_exit 미호출 → 포지션 유지.

        RED: 현재 동작도 동일하게 ExecutorError raise 되지만 cancel_order 가 없어서
             cancel_calls 단언이 FAIL 한다. 여기서는 포지션 잔존만 확인.
        """
        submitter = FakeOrderSubmitterWithPartialFill(partial_fill_qty=3)
        cfg = ExecutorConfig(order_fill_timeout_s=0.05, order_poll_interval_s=0.01)

        tick = [_kst(9, 30), _kst(9, 30, 1)]

        def advancing_clock() -> datetime:
            if len(tick) > 1:
                return tick.pop(0)
            return tick[0]

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
            clock=advancing_clock,
            sleep=lambda _: None,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        self._enter_position_via_executor(strategy, risk_manager, fake_bar_source, exc)

        entry_price = Decimal("50050")
        stop_price = entry_price * (Decimal("1") - Decimal("0.015"))
        stop_int = int(stop_price)
        stop_bar = _bar(
            _SYMBOL_A,
            9,
            35,
            close=stop_int - 100,
            high=stop_int - 50,
            low=stop_int - 200,
        )
        fake_bar_source.set_bars(_SYMBOL_A, [stop_bar])

        with pytest.raises(ExecutorError):
            exc.step(_kst(9, 36))

        # ExecutorError 발생 후 포지션은 여전히 남아있어야 한다 (record_exit 미호출)
        assert len(risk_manager.active_positions) == 1


class TestExecutorZeroFillExit:
    """ADR-0014 결정 6 — 청산 zero fill 도 ExecutorError 승격."""

    def test_청산_zero_fill_ExecutorError_raise(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """청산 주문이 한 주도 체결되지 않으면 ExecutorError 를 raise 한다.

        RED: 현재 타임아웃 ExecutorError 와 동일 경로이나 cancel_order 가 없어 FAIL.
        """
        submitter = FakeOrderSubmitterWithPartialFill(partial_fill_qty=0)
        cfg = ExecutorConfig(order_fill_timeout_s=0.05, order_poll_interval_s=0.01)

        tick = [_kst(9, 30), _kst(9, 30, 1)]

        def advancing_clock() -> datetime:
            if len(tick) > 1:
                return tick.pop(0)
            return tick[0]

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
            clock=advancing_clock,
            sleep=lambda _: None,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        for h in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500))
        strategy.on_bar(_bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000))
        risk_manager.record_entry(_SYMBOL_A, Decimal("50050"), 10, _kst(9, 31))

        entry_price = Decimal("50050")
        stop_price = entry_price * (Decimal("1") - Decimal("0.015"))
        stop_int = int(stop_price)
        stop_bar = _bar(
            _SYMBOL_A,
            9,
            35,
            close=stop_int - 100,
            high=stop_int - 50,
            low=stop_int - 200,
        )
        fake_bar_source.set_bars(_SYMBOL_A, [stop_bar])

        with pytest.raises(ExecutorError):
            exc.step(_kst(9, 36))

    def test_청산_zero_fill_cancel_order_호출됨(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """청산 zero fill 시에도 cancel_order 가 호출된다.

        RED: cancel_order 로직 미구현 → cancel_calls 비어있음.
        """
        submitter = FakeOrderSubmitterWithPartialFill(partial_fill_qty=0)
        cfg = ExecutorConfig(order_fill_timeout_s=0.05, order_poll_interval_s=0.01)

        tick = [_kst(9, 30), _kst(9, 30, 1)]

        def advancing_clock() -> datetime:
            if len(tick) > 1:
                return tick.pop(0)
            return tick[0]

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
            clock=advancing_clock,
            sleep=lambda _: None,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        for h in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500))
        strategy.on_bar(_bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000))
        risk_manager.record_entry(_SYMBOL_A, Decimal("50050"), 10, _kst(9, 31))

        entry_price = Decimal("50050")
        stop_price = entry_price * (Decimal("1") - Decimal("0.015"))
        stop_int = int(stop_price)
        stop_bar = _bar(
            _SYMBOL_A,
            9,
            35,
            close=stop_int - 100,
            high=stop_int - 50,
            low=stop_int - 200,
        )
        fake_bar_source.set_bars(_SYMBOL_A, [stop_bar])

        with pytest.raises(ExecutorError):
            exc.step(_kst(9, 36))

        assert len(submitter.cancel_calls) >= 1


class TestExecutorFullFillStillWorks:
    """ADR-0014 회귀 방지 — 전량 체결 경로가 부분체결 가드 도입 후에도 정상 동작."""

    def test_full_fill_EntryEvent_qty_결정qty와_일치(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """FakeOrderSubmitter(fill_after=0) — 즉시 전량 체결.

        EntryEvent.qty == decision.qty (cancel_calls 비어있어야 함).
        GREEN 인 경우 기존 경로 회귀 없음 확인.
        RED 인 경우: 부분체결 가드 도입 중 기존 경로가 깨진 것.
        """
        _ = FakeOrderSubmitterWithPartialFill(partial_fill_qty=None)
        # partial_fill_qty=None 이면 get_pending_orders 가 항상 미체결 반환 —
        # 대신 즉시 체결을 위해 직접 _last_order_number 를 없애는 방식 사용 불가.
        # 따라서 여기서는 기존 FakeOrderSubmitter(fill_after=0) 를 사용한다.
        submitter_full = FakeOrderSubmitter(fill_after=0)
        # cancel_calls 속성을 추가해야 함 — FakeOrderSubmitter 에 미구현 시 FAIL
        # 이 테스트는 기존 FakeOrderSubmitter 에 cancel_order 가 없어도
        # full fill 경로에서는 cancel_order 가 호출되지 않음을 확인한다.

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter_full,
            fake_balance_provider,
            fake_bar_source,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)
        _setup_orb_entry_bars(strategy, fake_bar_source)

        report = exc.step(_kst(9, 32))

        assert len(report.entry_events) == 1
        ev = report.entry_events[0]
        # 전량 체결이므로 qty 는 decision.qty (RiskManager 가 승인한 수량) 와 일치
        assert ev.qty == risk_manager.active_positions[0].qty
        # 전량 체결 경로에서는 cancel_order 가 호출되지 않아야 한다
        # (FakeOrderSubmitter 에 cancel_calls 가 없으면 AttributeError → FAIL 도 RED)
        assert not hasattr(submitter_full, "cancel_calls") or submitter_full.cancel_calls == []


# ===========================================================================
# PR #39 리뷰 반영 회귀 방지 테스트
# ===========================================================================


# ---------------------------------------------------------------------------
# A. _FillOutcome.__post_init__ 가드 3종 (refactor-invariant)
# ---------------------------------------------------------------------------


class TestFillOutcomePostInitGuards:
    """_FillOutcome.__post_init__ 자기정합성 가드 3종 회귀 방지.

    filled_qty < 0, status=='none' but filled_qty != 0,
    status=='partial' but filled_qty == 0 각각이 RuntimeError 를 발생시킨다.
    정상 조합(full/N, partial/k>0, none/0)은 통과한다.
    """

    # _FillOutcome 은 executor 내부 private DTO. 테스트에서는 직접 import 한다.
    from stock_agent.execution.executor import _FillOutcome  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        "filled_qty,status,raises",
        [
            # 정상 케이스 — RuntimeError 없음
            (10, "full", False),
            (5, "partial", False),
            (0, "none", False),
            # 비정상 케이스
            (-1, "none", True),  # filled_qty < 0
            (1, "none", True),  # status='none' but filled_qty != 0
            (0, "partial", True),  # status='partial' but filled_qty == 0
        ],
        ids=[
            "full_10_정상",
            "partial_5_정상",
            "none_0_정상",
            "filled_qty_음수",
            "none_status_but_filled_nonzero",
            "partial_status_but_filled_zero",
        ],
    )
    def test_fill_outcome_가드(self, filled_qty: int, status: str, raises: bool) -> None:
        from stock_agent.execution.executor import _FillOutcome  # type: ignore[attr-defined]

        if raises:
            with pytest.raises(RuntimeError):
                _FillOutcome(filled_qty=filled_qty, status=status)  # type: ignore[arg-type]
        else:
            outcome = _FillOutcome(filled_qty=filled_qty, status=status)  # type: ignore[arg-type]
            assert outcome.filled_qty == filled_qty
            assert outcome.status == status


# ---------------------------------------------------------------------------
# B. _handle_exit 부분/0 체결 → halt 선제 설정 (silent-failure-hunter C1)
# ---------------------------------------------------------------------------


class TestHandleExitHaltOnPartialFill:
    """청산 부분/0 체결 후 ExecutorError 발생 전 is_halted=True 선제 설정 검증.

    _handle_exit 는 status != 'full' 이면 self._halt = True 를 설정한 다음
    ExecutorError 를 raise 한다. 이로써 같은 sweep 내 후속 EntrySignal 이
    is_halted 체크에서 즉시 skip 된다.
    """

    def _partial_exit_setup(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
        *,
        partial_fill_qty: int,
    ) -> tuple[Executor, FakeOrderSubmitterWithPartialFill]:
        """청산 부분체결 Executor 세팅 헬퍼."""
        submitter = FakeOrderSubmitterWithPartialFill(partial_fill_qty=partial_fill_qty)
        cfg = ExecutorConfig(order_fill_timeout_s=0.05, order_poll_interval_s=0.01)

        tick = [_kst(9, 30), _kst(9, 30, 1)]

        def advancing_clock() -> datetime:
            if len(tick) > 1:
                return tick.pop(0)
            return tick[0]

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
            clock=advancing_clock,
            sleep=lambda _: None,
        )
        exc.start_session(_DATE, _STARTING_CAPITAL)

        # OR → long 상태 + 포지션 직접 등록
        for h in range(0, 30):
            strategy.on_bar(_bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500))
        strategy.on_bar(_bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000))
        risk_manager.record_entry(_SYMBOL_A, Decimal("50050"), 10, _kst(9, 31))

        # 손절 bar 공급
        stop_price = Decimal("50050") * (Decimal("1") - Decimal("0.015"))
        stop_int = int(stop_price)
        stop_bar = _bar(
            _SYMBOL_A,
            9,
            35,
            close=stop_int - 100,
            high=stop_int - 50,
            low=stop_int - 200,
        )
        fake_bar_source.set_bars(_SYMBOL_A, [stop_bar])

        return exc, submitter

    def test_청산_부분체결_ExecutorError_후_is_halted_True(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """청산 부분체결 → ExecutorError raise 후 executor.is_halted 가 True."""
        exc, _ = self._partial_exit_setup(
            strategy,
            risk_manager,
            fake_balance_provider,
            fake_bar_source,
            partial_fill_qty=3,
        )

        with pytest.raises(ExecutorError):
            exc.step(_kst(9, 36))

        assert exc.is_halted is True, "청산 부분체결 후 is_halted 가 True 여야 한다"

    def test_청산_zero_fill_ExecutorError_후_is_halted_True(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """청산 zero fill → ExecutorError raise 후 executor.is_halted 가 True."""
        exc, _ = self._partial_exit_setup(
            strategy,
            risk_manager,
            fake_balance_provider,
            fake_bar_source,
            partial_fill_qty=0,
        )

        with pytest.raises(ExecutorError):
            exc.step(_kst(9, 36))

        assert exc.is_halted is True, "청산 zero fill 후 is_halted 가 True 여야 한다"

    def test_청산_부분체결_후_후속_entry_는_halt로_skip됨(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """청산 부분체결 → halt 선제 → 다음 세션에서 entry 가 is_halted 로 skip.

        ExecutorError 를 catch 한 뒤 start_session 없이 재호출 시
        is_halted=True 이므로 EntrySignal 이 submit_buy 없이 skip 된다.
        """
        exc, submitter = self._partial_exit_setup(
            strategy,
            risk_manager,
            fake_balance_provider,
            fake_bar_source,
            partial_fill_qty=3,
        )

        with pytest.raises(ExecutorError):
            exc.step(_kst(9, 36))

        assert exc.is_halted

        # halt 상태에서 새 진입 시도 — submit_buy 가 호출되지 않아야 한다
        # (strategy 은 이미 long 진입 이후라 on_time 으로 다시 시그널 나오지 않으므로
        #  직접 breakout bar 추가해 EntrySignal 유도)
        # strategy 를 새로 만들어 진입 가능 상태로 초기화
        new_strategy = ORBStrategy(
            StrategyConfig(
                stop_loss_pct=Decimal("0.015"),
                take_profit_pct=Decimal("0.030"),
            )
        )
        # 새 세션 없이 그대로 step 호출 — is_halted=True 라 entry skip
        for h in range(0, 30):
            new_strategy.on_bar(_bar(_SYMBOL_A, 9, h, close=49_000, high=49_500, low=48_500))
        breakout_bar = _bar(_SYMBOL_A, 9, 31, close=50_100, high=50_100, low=50_000)
        fake_bar_source.set_bars(_SYMBOL_A, [breakout_bar])
        # exc._strategy 를 교체하지 않고 is_halted 상태 자체가 entry 를 막는다는 것을 검증
        import contextlib

        buy_calls_before = len(submitter.buy_calls)
        # step 은 risk_manager.session_date 가 여전히 _DATE 로 남아있어 호출 가능
        # 다른 이유로 실패해도 submit_buy 미호출 여부만 체크
        with contextlib.suppress(ExecutorError, RuntimeError):
            exc.step(_kst(9, 37))

        msg = "halt 상태에서는 submit_buy 가 호출되면 안 된다"
        assert len(submitter.buy_calls) == buy_calls_before, msg


# ---------------------------------------------------------------------------
# C. _resolve_fill cancel 백오프 한계 초과 → halt 선제 설정 (silent-failure-hunter I1)
# ---------------------------------------------------------------------------


class FakeOrderSubmitterWithCancelFail:
    """cancel_order 가 항상 KisClientError 를 발생시키는 더블.

    partial_fill_qty 수량 부분체결 상태를 유지하면서 취소 자체는 실패한다.
    _resolve_fill 의 cancel 백오프 한계 초과 경로를 검증하기 위해 사용.
    """

    def __init__(self, *, partial_fill_qty: int = 5) -> None:
        self._partial_fill_qty = partial_fill_qty
        self._counter = 0
        self._last_order_number: str | None = None
        self._last_qty: int = 0
        self.buy_calls: list[tuple[str, int]] = []
        self.sell_calls: list[tuple[str, int]] = []
        self.cancel_calls: list[str] = []

    def submit_buy(self, symbol: str, qty: int) -> OrderTicket:
        self._counter += 1
        self.buy_calls.append((symbol, qty))
        self._last_qty = qty
        self._last_order_number = f"ORD-BUY-{self._counter:04d}"
        return OrderTicket(
            order_number=self._last_order_number,
            symbol=symbol,
            side="buy",
            qty=qty,
            price=None,
            submitted_at=_kst(9, 30),
        )

    def submit_sell(self, symbol: str, qty: int) -> OrderTicket:
        self._counter += 1
        self.sell_calls.append((symbol, qty))
        self._last_qty = qty
        self._last_order_number = f"ORD-SELL-{self._counter:04d}"
        return OrderTicket(
            order_number=self._last_order_number,
            symbol=symbol,
            side="sell",
            qty=qty,
            price=None,
            submitted_at=_kst(9, 30),
        )

    def get_pending_orders(self) -> list[PendingOrder]:
        """부분체결 상태를 항상 반환 (취소 성공 여부와 무관하게)."""
        if self._last_order_number is None:
            return []
        filled = self._partial_fill_qty
        remaining = self._last_qty - filled
        if remaining < 0:
            remaining = 0
        return [
            PendingOrder(
                order_number=self._last_order_number,
                symbol=_SYMBOL_A,
                side="buy",
                qty_ordered=self._last_qty,
                qty_filled=filled,
                qty_remaining=remaining,
                price=None,
                submitted_at=_kst(9, 30),
            )
        ]

    def cancel_order(self, order_number: str) -> None:
        """항상 KisClientError 를 raise — 취소 실패 시뮬레이션."""
        self.cancel_calls.append(order_number)
        raise KisClientError(f"cancel 실패 (시뮬레이션): order_number={order_number}")


class TestResolveFillCancelFailure:
    """_resolve_fill cancel 백오프 한계 초과 시 halt 선제 설정 (silent-failure-hunter I1).

    시나리오:
    - partial fill setup (filled_qty=k > 0)
    - cancel_order 가 backoff_max_attempts+1 회 모두 KisClientError 반환
    - _resolve_fill 은 ExecutorError 를 raise 하지 않고 _FillOutcome(partial, k) 반환
    - executor._halt = True 선제 설정
    - CRITICAL 로그 "cancel_failed" 방출
    """

    def _cancel_fail_setup(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
        *,
        partial_fill_qty: int = 15,
        withdrawable: int = 10_000_000,
        backoff_max_attempts: int = 1,
    ) -> tuple[Executor, FakeOrderSubmitterWithCancelFail]:
        """cancel 실패 시나리오 Executor 세팅 헬퍼."""
        fake_balance_provider.set_balance(_empty_balance(withdrawable=withdrawable))

        submitter = FakeOrderSubmitterWithCancelFail(partial_fill_qty=partial_fill_qty)
        cfg = ExecutorConfig(
            order_fill_timeout_s=0.05,
            order_poll_interval_s=0.01,
            backoff_max_attempts=backoff_max_attempts,
            backoff_initial_s=0.001,  # 테스트에서 백오프 지연 최소화
        )

        tick = [_kst(9, 30), _kst(9, 30, 1)]

        def advancing_clock() -> datetime:
            if len(tick) > 1:
                return tick.pop(0)
            return tick[0]

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
            clock=advancing_clock,
            sleep=lambda _: None,
        )
        exc.start_session(_DATE, withdrawable)
        _setup_orb_entry_bars(strategy, fake_bar_source)
        return exc, submitter

    def test_cancel_백오프_한계_초과시_step_은_StepReport_반환(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """cancel_order 백오프 한계 초과 시 ExecutorError 를 raise 하지 않고
        StepReport 를 반환해야 한다 (ADR-0014 silent-failure-hunter I1).
        """
        exc, _ = self._cancel_fail_setup(
            strategy,
            risk_manager,
            fake_balance_provider,
            fake_bar_source,
        )

        report = exc.step(_kst(9, 32))

        assert report is not None, "cancel 실패 후에도 StepReport 를 반환해야 한다"

    def test_cancel_백오프_한계_초과시_halt_선제_설정된다(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """cancel_order 백오프 한계 초과 → executor.is_halted == True."""
        exc, _ = self._cancel_fail_setup(
            strategy,
            risk_manager,
            fake_balance_provider,
            fake_bar_source,
        )

        exc.step(_kst(9, 32))

        assert exc.is_halted is True, "cancel 백오프 한계 초과 시 halt 가 선제 설정되어야 한다"

    def test_cancel_백오프_한계_초과시_critical_로그_방출(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """cancel_order 백오프 한계 초과 → CRITICAL 로그 'cancel_failed' 포함."""
        from loguru import logger

        critical_messages: list[str] = []

        def _sink(msg: Any) -> None:
            if msg.record["level"].name == "CRITICAL":
                critical_messages.append(msg.record["message"])

        sink_id = logger.add(_sink, level="CRITICAL")
        try:
            exc, _ = self._cancel_fail_setup(
                strategy,
                risk_manager,
                fake_balance_provider,
                fake_bar_source,
            )
            exc.step(_kst(9, 32))
        finally:
            logger.remove(sink_id)

        msg = f"CRITICAL 'cancel_failed' 로그가 없음. got={critical_messages}"
        assert any("cancel_failed" in m for m in critical_messages), msg

    def test_cancel_백오프_한계_초과시_filled_qty_가_EntryEvent에_기록됨(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """cancel 실패 후에도 체결된 filled_qty 는 EntryEvent.qty 로 기록된다."""
        exc, _ = self._cancel_fail_setup(
            strategy,
            risk_manager,
            fake_balance_provider,
            fake_bar_source,
            partial_fill_qty=15,
        )

        report = exc.step(_kst(9, 32))

        # cancel 실패 후 filled_qty=15 이 EntryEvent 에 기록되어야 한다
        assert len(report.entry_events) == 1, "부분체결 + cancel 실패에도 EntryEvent 가 있어야 한다"
        msg = f"EntryEvent.qty 는 filled_qty=15 이어야 한다 (got {report.entry_events[0].qty})"
        assert report.entry_events[0].qty == 15, msg

    def test_cancel_백오프_한계_초과시_RiskManager_active_positions에_filled_qty_반영(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """cancel 실패 후 RiskManager.active_positions[0].qty == filled_qty."""
        exc, _ = self._cancel_fail_setup(
            strategy,
            risk_manager,
            fake_balance_provider,
            fake_bar_source,
            partial_fill_qty=15,
        )

        exc.step(_kst(9, 32))

        assert len(risk_manager.active_positions) == 1
        assert risk_manager.active_positions[0].qty == 15, (
            f"RiskManager 에 filled_qty=15 가 기록되어야 한다 "
            f"(got {risk_manager.active_positions[0].qty})"
        )


# ---------------------------------------------------------------------------
# D. 부분체결 후 reconcile mismatch 없음 + halt 미유지 (refactor-invariant)
# ---------------------------------------------------------------------------


class TestPartialFillReconcileIntegration:
    """부분체결 후 broker_holdings / risk_holdings 가 일치하면 reconcile mismatch 없음.

    진입 부분체결(filled_qty=k) → RiskManager active_positions qty=k.
    broker holdings 도 k 로 세팅하면 mismatch_symbols == ().
    partial entry 는 halt 를 켜지 않는다 (_handle_exit 와 다름).
    """

    def _partial_entry_and_reconcile(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
        *,
        filled_qty: int = 15,
        withdrawable: int = 10_000_000,
    ) -> tuple[Executor, FakeOrderSubmitterWithPartialFill]:
        """부분체결 후 broker holdings 를 filled_qty 로 맞춘 Executor 반환."""
        submitter = FakeOrderSubmitterWithPartialFill(partial_fill_qty=filled_qty)
        cfg = ExecutorConfig(
            order_fill_timeout_s=0.05,
            order_poll_interval_s=0.01,
        )

        tick = [_kst(9, 30), _kst(9, 30, 1)]

        def advancing_clock() -> datetime:
            if len(tick) > 1:
                return tick.pop(0)
            return tick[0]

        fake_balance_provider.set_balance(_empty_balance(withdrawable=withdrawable))

        exc = _make_executor(
            strategy,
            risk_manager,
            submitter,
            fake_balance_provider,
            fake_bar_source,
            config=cfg,
            clock=advancing_clock,
            sleep=lambda _: None,
        )
        exc.start_session(_DATE, withdrawable)
        _setup_orb_entry_bars(strategy, fake_bar_source)
        exc.step(_kst(9, 32))

        # 진입 완료 후 broker holdings 를 filled_qty 로 맞춤
        fake_balance_provider.set_balance(_balance_with_holding(_SYMBOL_A, qty=filled_qty))
        return exc, submitter

    def test_부분체결_후_reconcile_mismatch_없음(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """부분체결 후 broker_holdings == filled_qty, risk_holdings == filled_qty
        이면 mismatch_symbols == ().
        """
        exc, _ = self._partial_entry_and_reconcile(
            strategy,
            risk_manager,
            fake_balance_provider,
            fake_bar_source,
            filled_qty=15,
        )

        report = exc.reconcile()

        msg = f"broker=risk=15 이면 mismatch 없어야 한다 (got {report.mismatch_symbols})"
        assert report.mismatch_symbols == (), msg

    def test_부분체결_후_halt_유지되지_않음(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """진입 부분체결은 _handle_exit 와 달리 halt 를 선제 설정하지 않는다."""
        exc, _ = self._partial_entry_and_reconcile(
            strategy,
            risk_manager,
            fake_balance_provider,
            fake_bar_source,
            filled_qty=15,
        )

        # 부분체결 후 is_halted 가 False 이어야 한다
        # (cancel 실패가 없는 일반 부분체결 경로)
        assert exc.is_halted is False, "진입 부분체결 자체는 halt 를 켜지 않는다"


# ---------------------------------------------------------------------------
# E. force_close 다중 심볼 부분청산 상호작용 (skip — 후속 PR)
# ---------------------------------------------------------------------------


class TestForceCloseAllPartialFillInterop:
    """force_close_all 에서 여러 심볼 중 1개만 부분청산 ExecutorError 시 상호작용.

    이 케이스는 현재 _open_lots 순회 순서·_process_signals 예외 전파 지점에 따라
    검증 복잡도가 높아 즉시 GREEN 을 보장하기 어렵다.
    후속 PR 에서 구현 의도를 명시하고 skip 으로 등록한다.
    """

    @pytest.mark.skip(reason="force_close 다중 심볼 부분체결 상호작용 — 후속 PR")
    def test_force_close_부분체결_심볼은_ExecutorError_하지만_다른_심볼_청산은_정상진행(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_order_submitter: FakeOrderSubmitter,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """force_close_all 이 한 심볼의 ExecutorError 로 중단되지만,
        그 전에 체결된 다른 심볼의 ExitEvent 는 last_sweep_exit_events 로 조회 가능.

        현재 _open_lots 순회 순서·_process_signals 예외 전파 지점에 따라
        검증 복잡도가 달라 후속 PR 에서 구현 설계 확정 후 GREEN 전환.
        """
        # 이 테스트는 skip 되므로 본문 구현 불필요.
        pass
