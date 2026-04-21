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
from typing import Any
from unittest.mock import MagicMock

import pytest

from stock_agent.broker import (
    BalanceSnapshot,
    Holding,
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
    Executor,
    ExecutorConfig,
    ExecutorError,
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


def _ticket(symbol: str = _SYMBOL_A, side: str = "buy", n: int = 1) -> OrderTicket:
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
    fake_order_submitter: FakeOrderSubmitter,
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
    def test_비율_음수_RuntimeError(self, field_name: str, bad_value: Decimal) -> None:
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


class TestWaitFill:
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

    def test_timeout_ExecutorError(
        self,
        strategy: ORBStrategy,
        risk_manager: RiskManager,
        fake_balance_provider: FakeBalanceProvider,
        fake_bar_source: FakeBarSource,
    ) -> None:
        """clock 이 deadline 을 초과하면 ExecutorError."""
        # clock 이 처음 호출 시 deadline 이 이미 지난 시각 반환
        tick = [_kst(9, 30), _kst(9, 30, 31)]  # initial + after 30s timeout

        def advancing_clock() -> datetime:
            if len(tick) > 1:
                return tick.pop(0)
            return tick[0]

        submitter = FakeOrderSubmitter(fill_after=999)  # 절대 체결 안 됨
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

        with pytest.raises(ExecutorError, match="타임아웃"):
            exc.step(_kst(9, 32))


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
