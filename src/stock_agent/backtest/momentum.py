"""Cross-sectional Momentum baseline 평가 함수 (ADR-0019 Step F PR3 — F3).

KOSPI 200 종목의 월별 리밸런싱 모멘텀 전략 백테스트. ``MomentumStrategy`` 가
universe close 를 누적하고 월 변경 시점에 ``ExitSignal`` · ``EntrySignal``
다중 emit. 본 함수는 다중 lot 동시 보유 + mark-to-market 평가를 담당.

설계 결정 — ``BacktestEngine`` 우회 (DCA · GoldenCross 와 동일 기조)
- ``BacktestEngine`` 은 단일 lot 가정 + ``force_close_at`` 청산 가정 — 다중
  종목 동시 보유 + 월 단위 리밸런싱과 비호환.
- ``EntrySignal.stop_price=0 / take_price=0`` 마커 인식 — 손익절 판정 건너뜀.

비용 계약 (DCABaselineConfig · GoldenCrossBaselineConfig 와 동일)
- 슬리피지: 시장가 0.1% 불리.
- 수수료: 매수·매도 대칭 0.015%.
- 거래세: 매도만 0.18%.

리밸런싱 흐름
1. ``loader.stream(start, end, universe)`` 로 multi-symbol 일봉 스트림 수신.
2. 매 분봉 처리:
   - 세션 경계 감지 (``bar_date != last_session_date``):
     a. 직전 세션 ``DailyEquity`` 기록 (``cash + Σ lot.qty × latest_close``).
     b. ``strategy.on_time(bar.bar_time)`` 호출 → 시그널 처리.
     c. ``last_session_date = bar_date``.
   - ``latest_close[bar.symbol] = bar.close`` 갱신.
   - ``strategy.on_bar(bar)`` (close 누적, 시그널 없음).
3. 시그널 처리 — Exit 먼저 (cash 회수) → Entry (cash 분배):
   - Exit: lot 청산 (slippage·commission·tax 반영) → ``TradeRecord`` 누적.
   - Entry 자본 분배: ``alloc_per = cash_snapshot × position_pct / top_n`` (floor).
     ``qty = floor(alloc_per / entry_fill)`` (floor). qty=0 또는 잔액 부족 시 skip.
4. 스트림 종료 시:
   - 마지막 세션 ``DailyEquity`` 기록.
   - 잔존 lot 가상 청산 (``last_close × (1 - slippage)`` 기준) — ``TradeRecord``
     의 ``exit_reason="force_close"``.

``BacktestResult.rejected_counts = {}`` · ``post_slippage_rejections = 0``
(RiskManager 미사용 — DCA · GoldenCross 와 동일).

체결 흐름은 GoldenCross 의 단일 종목 1 lot 계약을 다중 종목·다중 lot 으로
확장한 것. 비용 산식·반올림 정책 모두 동일.

스레드 모델
- 단일 프로세스 전용.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from loguru import logger

from stock_agent.backtest import metrics as metrics_mod
from stock_agent.backtest.engine import (
    BacktestMetrics,
    BacktestResult,
    DailyEquity,
    TradeRecord,
)
from stock_agent.backtest.loader import BarLoader
from stock_agent.data import MinuteBar
from stock_agent.strategy.base import EntrySignal, ExitSignal, Signal
from stock_agent.strategy.momentum import MomentumConfig, MomentumStrategy


@dataclass(frozen=True, slots=True)
class MomentumBaselineConfig:
    """Cross-sectional Momentum baseline 백테스트 파라미터.

    Raises:
        RuntimeError: 자본 비양수, 비율 음수, ``slippage_rate`` ``[0, 1)`` 범위
            위반. ``universe`` · ``lookback_months`` · ``top_n`` 등은
            ``MomentumConfig`` 검증으로 위임 — 잘못된 값은 ``compute_momentum_baseline``
            호출 시점에 ``MomentumConfig`` ``__post_init__`` 가 ``RuntimeError`` 전파.
    """

    starting_capital_krw: int
    universe: tuple[str, ...]
    lookback_months: int = 12
    top_n: int = 10
    rebalance_day: int = 1
    position_pct: Decimal = Decimal("1.0")
    commission_rate: Decimal = Decimal("0.00015")
    sell_tax_rate: Decimal = Decimal("0.0018")
    slippage_rate: Decimal = Decimal("0.001")

    def __post_init__(self) -> None:
        if self.starting_capital_krw <= 0:
            raise RuntimeError(
                f"starting_capital_krw 는 양수여야 합니다 (got={self.starting_capital_krw})"
            )
        if self.commission_rate < 0:
            raise RuntimeError(
                f"commission_rate 는 0 이상이어야 합니다 (got={self.commission_rate})"
            )
        if self.sell_tax_rate < 0:
            raise RuntimeError(f"sell_tax_rate 는 0 이상이어야 합니다 (got={self.sell_tax_rate})")
        if self.slippage_rate < 0 or self.slippage_rate >= 1:
            raise RuntimeError(
                f"slippage_rate 는 [0, 1) 범위여야 합니다 (got={self.slippage_rate})"
            )


@dataclass(slots=True)
class _ActiveLot:
    """종목별 단일 lot — 진입~청산 1쌍 추적용."""

    qty: int
    entry_fill_price: Decimal
    entry_ts: datetime
    entry_notional_krw: int
    buy_commission_krw: int


def compute_momentum_baseline(
    loader: BarLoader,
    config: MomentumBaselineConfig,
    start: date,
    end: date,
) -> BacktestResult:
    """Cross-sectional Momentum 시뮬레이션 → ``BacktestResult``.

    Args:
        loader: ``BarLoader`` Protocol 구현체. 보통 ``DailyBarLoader``.
        config: Momentum baseline 파라미터.
        start, end: 백테스트 구간 (경계 포함).

    Raises:
        RuntimeError: ``start > end``, ``MomentumConfig`` 검증 위반.

    Returns:
        ``BacktestResult`` — ``trades``, ``daily_equity``, ``metrics``.
        ``rejected_counts={}``, ``post_slippage_rejections=0`` (RiskManager 미사용).
    """
    if start > end:
        raise RuntimeError(
            f"start({start.isoformat()}) 는 end({end.isoformat()}) 이전이어야 합니다."
        )

    momentum_cfg = MomentumConfig(
        universe=config.universe,
        lookback_months=config.lookback_months,
        top_n=config.top_n,
        rebalance_day=config.rebalance_day,
        position_pct=config.position_pct,
    )
    strategy = MomentumStrategy(momentum_cfg)

    cash: int = config.starting_capital_krw
    active_lots: dict[str, _ActiveLot] = {}
    trades: list[TradeRecord] = []
    daily_equity: list[DailyEquity] = []
    last_session_date: date | None = None
    last_bar_time: datetime | None = None
    latest_close: dict[str, Decimal] = {}
    universe_set = set(config.universe)

    def _equity_snapshot() -> int:
        unrealized = 0
        for sym, lot in active_lots.items():
            close = latest_close.get(sym)
            if close is None:
                continue
            unrealized += int(close * Decimal(lot.qty))
        return cash + unrealized

    def _process_signals(signals: list[Signal]) -> None:
        nonlocal cash
        exits = [s for s in signals if isinstance(s, ExitSignal)]
        entries = [s for s in signals if isinstance(s, EntrySignal)]
        for sig in exits:
            lot = active_lots.get(sig.symbol)
            if lot is None:
                logger.debug("Momentum exit skip: 보유 없음 (sym={s})", s=sig.symbol)
                continue
            cash, trade = _execute_exit(cash, lot, sig, config)
            trades.append(trade)
            del active_lots[sig.symbol]
        if not entries:
            return
        cash_snapshot = cash
        alloc_per = Decimal(cash_snapshot) * config.position_pct / Decimal(config.top_n)
        for sig in entries:
            if sig.symbol in active_lots:
                logger.debug("Momentum entry skip: 이미 보유 (sym={s})", s=sig.symbol)
                continue
            cash, lot = _attempt_buy(cash, sig, alloc_per, config)
            if lot is not None:
                active_lots[sig.symbol] = lot

    bars: Iterable[MinuteBar] = loader.stream(start, end, config.universe)

    for bar in bars:
        bar_date = bar.bar_time.date()
        if last_session_date is None:
            last_session_date = bar_date
        elif bar_date != last_session_date:
            daily_equity.append(
                DailyEquity(
                    session_date=last_session_date,
                    equity_krw=_equity_snapshot(),
                )
            )
            on_time_signals = strategy.on_time(bar.bar_time)
            _process_signals(on_time_signals)
            last_session_date = bar_date

        if bar.symbol in universe_set:
            latest_close[bar.symbol] = bar.close

        strategy.on_bar(bar)
        last_bar_time = bar.bar_time

    if last_session_date is not None:
        daily_equity.append(
            DailyEquity(
                session_date=last_session_date,
                equity_krw=_equity_snapshot(),
            )
        )

    if active_lots and last_bar_time is not None:
        for sym in sorted(active_lots):
            lot = active_lots[sym]
            close = latest_close.get(sym)
            if close is None:
                continue
            cash, trade = _hypothetical_exit(cash, lot, sym, close, last_bar_time, config)
            trades.append(trade)
        active_lots.clear()

    starting = config.starting_capital_krw
    ending = daily_equity[-1].equity_krw if daily_equity else starting
    metrics = _compute_metrics(
        starting=starting,
        ending=ending,
        equity_series=[eq.equity_krw for eq in daily_equity],
        net_pnls=[t.net_pnl_krw for t in trades],
        trade_count=len(trades),
        session_count=len(daily_equity),
    )

    return BacktestResult(
        trades=tuple(trades),
        daily_equity=tuple(daily_equity),
        metrics=metrics,
        rejected_counts={},
        post_slippage_rejections=0,
    )


# ---- internal -----------------------------------------------------------


def _attempt_buy(
    cash: int,
    signal: EntrySignal,
    alloc_per_position: Decimal,
    config: MomentumBaselineConfig,
) -> tuple[int, _ActiveLot | None]:
    """``EntrySignal`` 처리. 매수 성공 시 ``(new_cash, lot)``, 실패 시 ``(cash, None)``."""
    entry_fill = signal.price * (Decimal("1") + config.slippage_rate)
    if entry_fill <= 0:
        logger.debug("Momentum skip: entry_fill 비양수 ({})", entry_fill)
        return cash, None

    qty = int(alloc_per_position / entry_fill)
    if qty <= 0:
        logger.debug(
            "Momentum skip: alloc 적용 후 qty=0 (alloc={}, fill={})",
            alloc_per_position,
            entry_fill,
        )
        return cash, None

    notional_dec = entry_fill * Decimal(qty)
    notional_int = int(notional_dec)
    buy_comm = int(notional_dec * config.commission_rate)
    total_cost = notional_int + buy_comm
    if total_cost > cash:
        logger.debug("Momentum skip: 잔액 부족 (need={}, have={})", total_cost, cash)
        return cash, None

    lot = _ActiveLot(
        qty=qty,
        entry_fill_price=entry_fill,
        entry_ts=signal.ts,
        entry_notional_krw=notional_int,
        buy_commission_krw=buy_comm,
    )
    logger.info(
        "Momentum buy: {s} qty={q} entry={p} cost={c}",
        s=signal.symbol,
        q=qty,
        p=entry_fill,
        c=total_cost,
    )
    return cash - total_cost, lot


def _execute_exit(
    cash: int,
    lot: _ActiveLot,
    signal: ExitSignal,
    config: MomentumBaselineConfig,
) -> tuple[int, TradeRecord]:
    """``ExitSignal`` 처리. ``(new_cash, TradeRecord)``."""
    exit_fill = signal.price * (Decimal("1") - config.slippage_rate)
    exit_notional_dec = exit_fill * Decimal(lot.qty)
    exit_notional_int = int(exit_notional_dec)
    sell_comm = int(exit_notional_dec * config.commission_rate)
    tax = int(exit_notional_dec * config.sell_tax_rate)
    gross_pnl = exit_notional_int - lot.entry_notional_krw
    commission_total = lot.buy_commission_krw + sell_comm
    net_pnl = gross_pnl - commission_total - tax
    new_cash = cash + exit_notional_int - sell_comm - tax
    trade = TradeRecord(
        symbol=signal.symbol,
        entry_ts=lot.entry_ts,
        entry_price=lot.entry_fill_price,
        exit_ts=signal.ts,
        exit_price=exit_fill,
        qty=lot.qty,
        exit_reason="force_close",
        gross_pnl_krw=gross_pnl,
        commission_krw=commission_total,
        tax_krw=tax,
        net_pnl_krw=net_pnl,
    )
    logger.info(
        "Momentum sell: {s} qty={q} exit={p} pnl={pnl}",
        s=signal.symbol,
        q=lot.qty,
        p=exit_fill,
        pnl=net_pnl,
    )
    return new_cash, trade


def _hypothetical_exit(
    cash: int,
    lot: _ActiveLot,
    symbol: str,
    last_close: Decimal,
    last_bar_time: datetime,
    config: MomentumBaselineConfig,
) -> tuple[int, TradeRecord]:
    """스트림 종료 시 잔존 lot 가상 청산."""
    exit_fill = last_close * (Decimal("1") - config.slippage_rate)
    exit_notional_dec = exit_fill * Decimal(lot.qty)
    exit_notional_int = int(exit_notional_dec)
    sell_comm = int(exit_notional_dec * config.commission_rate)
    tax = int(exit_notional_dec * config.sell_tax_rate)
    gross_pnl = exit_notional_int - lot.entry_notional_krw
    commission_total = lot.buy_commission_krw + sell_comm
    net_pnl = gross_pnl - commission_total - tax
    new_cash = cash + exit_notional_int - sell_comm - tax
    trade = TradeRecord(
        symbol=symbol,
        entry_ts=lot.entry_ts,
        entry_price=lot.entry_fill_price,
        exit_ts=last_bar_time,
        exit_price=exit_fill,
        qty=lot.qty,
        exit_reason="force_close",
        gross_pnl_krw=gross_pnl,
        commission_krw=commission_total,
        tax_krw=tax,
        net_pnl_krw=net_pnl,
    )
    return new_cash, trade


def _compute_metrics(
    *,
    starting: int,
    ending: int,
    equity_series: list[int],
    net_pnls: list[int],
    trade_count: int,
    session_count: int,
) -> BacktestMetrics:
    """``BacktestEngine._compute_metrics`` 와 동일 산식 — DCA / GoldenCross 와 동일."""
    daily_returns: list[Decimal] = []
    prev = starting
    for eq in equity_series:
        if prev > 0:
            daily_returns.append(Decimal(eq - prev) / Decimal(prev))
        prev = eq

    return BacktestMetrics(
        total_return_pct=metrics_mod.total_return_pct(starting, ending),
        max_drawdown_pct=metrics_mod.max_drawdown_pct(equity_series),
        sharpe_ratio=metrics_mod.sharpe_ratio(daily_returns),
        win_rate=metrics_mod.win_rate(net_pnls),
        avg_pnl_ratio=metrics_mod.avg_pnl_ratio(net_pnls),
        trades_per_day=metrics_mod.trades_per_day(trade_count, session_count),
        net_pnl_krw=ending - starting,
    )
