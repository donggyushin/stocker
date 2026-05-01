"""Golden Cross (200d SMA) baseline 평가 함수.

ADR-0019 Step F PR2 — F2 Golden Cross. KOSPI 200 ETF (069500 KODEX 200) 단일
종목에 대해 200일 SMA cross-up/cross-down 시그널을 받아 단일 lot 진입/청산을
시뮬레이션한다. ADR-0022 게이트 (DCA baseline 대비 알파 + MDD > -25% +
연환산 Sharpe > 0.3) 평가.

설계 결정 — `BacktestEngine` 우회 (DCA 와 동일 기조)
- `BacktestEngine` 은 `force_close_at` 마감 가정이 강제. Golden Cross 는 SMA
  하향 이탈 시점에만 청산하므로 force_close 가정과 비호환.
- DCA 와 다르게 단일 lot (한 번에 1 lot 만) — cross-down 마다 청산 후 cash 회수.
  추가 cross-up 마다 다시 진입.
- 결과는 `BacktestResult` 형식으로 산출 — CLI · runbook · 메트릭 비교 파이프라인
  재사용.

비용 계약 (DCABaselineConfig 와 동일)
- 슬리피지: 시장가 0.1% 불리.
- 수수료: 매수·매도 대칭 0.015%.
- 거래세: 매도만 0.18%.

체결 흐름
1. `GoldenCrossStrategy.on_bar` 가 cross-up 시 `EntrySignal` 발생.
2. `entry_fill = bar.close * (1 + slippage)` 계산.
3. `target_notional = int(cash * position_pct)`.
4. `qty = int(target_notional / entry_fill)` (floor). qty=0 → skip.
5. `total_cost = int(entry_fill * qty) + buy_commission`.
6. `total_cost > cash` → skip. 그 외 lot 보관 + cash 차감.

청산 흐름 (cross-down ExitSignal 수신)
1. `exit_fill = bar.close * (1 - slippage)`.
2. `exit_notional_int = int(exit_fill * lot.qty)`.
3. `sell_comm = int(exit_fill * lot.qty * commission_rate)`.
4. `tax = int(exit_fill * lot.qty * sell_tax_rate)`.
5. `cash += exit_notional_int - sell_comm - tax`.
6. `TradeRecord(reason="force_close")` 누적.
7. lot 비움 → 다음 EntrySignal 대기 (재진입 가능).

Hypothetical 청산 (스트림 종료 후 lot 잔존 시 1회)
- DCA 와 동일 패턴. 마지막 close 기준 가상 청산 → `TradeRecord` 1건.

DailyEquity
- 세션 경계마다 `cash + (lot 보유 시 lot.qty * last_close)` 를 mark-to-market 기록.
"""

from __future__ import annotations

import re
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
from stock_agent.strategy.base import EntrySignal, ExitSignal
from stock_agent.strategy.golden_cross import GoldenCrossConfig, GoldenCrossStrategy

_SYMBOL_RE = re.compile(r"^\d{6}$")


@dataclass(frozen=True, slots=True)
class GoldenCrossBaselineConfig:
    """Golden Cross baseline 백테스트 파라미터.

    Raises:
        RuntimeError: 자본 비양수, symbol 정규식 위반, sma_period 비양수,
            position_pct 범위 위반, 비율 음수, slippage_rate `[0, 1)` 범위 위반.
    """

    starting_capital_krw: int
    target_symbol: str = "069500"
    sma_period: int = 200
    position_pct: Decimal = Decimal("1.0")
    commission_rate: Decimal = Decimal("0.00015")
    sell_tax_rate: Decimal = Decimal("0.0018")
    slippage_rate: Decimal = Decimal("0.001")

    def __post_init__(self) -> None:
        if self.starting_capital_krw <= 0:
            raise RuntimeError(
                f"starting_capital_krw 는 양수여야 합니다 (got={self.starting_capital_krw})"
            )
        if not _SYMBOL_RE.match(self.target_symbol):
            raise RuntimeError(
                f"target_symbol 은 6자리 숫자 문자열이어야 합니다 (got={self.target_symbol!r})"
            )
        if self.sma_period <= 0:
            raise RuntimeError(f"sma_period 는 양수여야 합니다 (got={self.sma_period})")
        if self.position_pct <= 0 or self.position_pct > 1:
            raise RuntimeError(f"position_pct 는 (0, 1] 범위여야 합니다 (got={self.position_pct})")
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
    """단일 lot — 진입~청산 1쌍 추적용."""

    qty: int
    entry_fill_price: Decimal
    entry_ts: datetime
    entry_notional_krw: int
    buy_commission_krw: int


def compute_golden_cross_baseline(
    loader: BarLoader,
    config: GoldenCrossBaselineConfig,
    start: date,
    end: date,
) -> BacktestResult:
    """Golden Cross 200d SMA 시뮬레이션 → BacktestResult.

    `loader.stream(start, end, (target_symbol,))` 로 단일 심볼 일봉 스트림 소비.
    `GoldenCrossStrategy` 가 cross-up 시 EntrySignal · cross-down 시 ExitSignal
    발행. 단일 lot 진입/청산 후 lot 비우면 다음 cross-up 대기 (재진입 가능).
    스트림 종료 시 lot 잔존하면 마지막 close 기준 가상 청산.

    Args:
        loader: `BarLoader` Protocol — 보통 `DailyBarLoader` 일봉 경로 주입.
        config: Golden Cross 파라미터.
        start, end: 백테스트 구간 (경계 포함).

    Returns:
        `BacktestResult` — `trades`, `daily_equity`, `metrics`. `rejected_counts`
        은 항상 빈 dict, `post_slippage_rejections=0` (RiskManager 미사용).
    """
    gc_cfg = GoldenCrossConfig(
        target_symbol=config.target_symbol,
        sma_period=config.sma_period,
        position_pct=config.position_pct,
    )
    strategy = GoldenCrossStrategy(gc_cfg)

    cash: int = config.starting_capital_krw
    active_lot: _ActiveLot | None = None
    trades: list[TradeRecord] = []
    daily_equity: list[DailyEquity] = []
    last_session_date: date | None = None
    last_target_close: Decimal | None = None
    last_target_bar_time: datetime | None = None

    def _equity_at(close_price: Decimal | None) -> int:
        if active_lot is None or close_price is None:
            return cash
        return cash + int(close_price * Decimal(active_lot.qty))

    bars: Iterable[MinuteBar] = loader.stream(start, end, (config.target_symbol,))

    for bar in bars:
        bar_date = bar.bar_time.date()
        if last_session_date is None:
            last_session_date = bar_date
        elif bar_date != last_session_date:
            daily_equity.append(
                DailyEquity(
                    session_date=last_session_date,
                    equity_krw=_equity_at(last_target_close),
                )
            )
            last_session_date = bar_date

        if bar.symbol == config.target_symbol:
            last_target_close = bar.close
            last_target_bar_time = bar.bar_time

        signals = strategy.on_bar(bar)
        for sig in signals:
            if isinstance(sig, EntrySignal):
                if active_lot is not None:
                    logger.debug(
                        "GoldenCross EntrySignal 무시 — 이미 보유 (sym={s})",
                        s=sig.symbol,
                    )
                    continue
                cash, lot = _attempt_buy(cash, sig, config)
                if lot is not None:
                    active_lot = lot
            elif isinstance(sig, ExitSignal):
                if active_lot is None:
                    logger.debug(
                        "GoldenCross ExitSignal 무시 — 보유 없음 (sym={s})",
                        s=sig.symbol,
                    )
                    continue
                cash, trade = _execute_exit(cash, active_lot, sig, config)
                trades.append(trade)
                active_lot = None

    if last_session_date is not None:
        daily_equity.append(
            DailyEquity(
                session_date=last_session_date,
                equity_krw=_equity_at(last_target_close),
            )
        )

    if (
        active_lot is not None
        and last_target_close is not None
        and last_target_bar_time is not None
    ):
        cash, trade = _hypothetical_exit(
            cash, active_lot, last_target_close, last_target_bar_time, config
        )
        trades.append(trade)
        active_lot = None

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
    config: GoldenCrossBaselineConfig,
) -> tuple[int, _ActiveLot | None]:
    """EntrySignal 처리. 매수 성공 시 (`new_cash`, lot), 실패 시 (`cash`, None)."""
    entry_fill = signal.price * (Decimal("1") + config.slippage_rate)
    if entry_fill <= 0:
        logger.debug("GoldenCross skip: entry_fill 비양수 ({})", entry_fill)
        return cash, None

    target_notional = Decimal(cash) * config.position_pct
    qty = int(target_notional / entry_fill)
    if qty <= 0:
        logger.debug(
            "GoldenCross skip: position_pct 적용 후 qty=0 (cash={}, pct={}, fill={})",
            cash,
            config.position_pct,
            entry_fill,
        )
        return cash, None

    notional_dec = entry_fill * Decimal(qty)
    notional_int = int(notional_dec)
    buy_comm = int(notional_dec * config.commission_rate)
    total_cost = notional_int + buy_comm
    if total_cost > cash:
        logger.debug("GoldenCross skip: 잔액 부족 (need={}, have={})", total_cost, cash)
        return cash, None

    lot = _ActiveLot(
        qty=qty,
        entry_fill_price=entry_fill,
        entry_ts=signal.ts,
        entry_notional_krw=notional_int,
        buy_commission_krw=buy_comm,
    )
    logger.info(
        "GoldenCross buy: {s} qty={q} entry={p} cost={c}",
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
    config: GoldenCrossBaselineConfig,
) -> tuple[int, TradeRecord]:
    """ExitSignal 처리. (`new_cash`, TradeRecord)."""
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
        "GoldenCross sell: {s} qty={q} exit={p} pnl={pnl}",
        s=signal.symbol,
        q=lot.qty,
        p=exit_fill,
        pnl=net_pnl,
    )
    return new_cash, trade


def _hypothetical_exit(
    cash: int,
    lot: _ActiveLot,
    last_close: Decimal,
    last_bar_time: datetime,
    config: GoldenCrossBaselineConfig,
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
        symbol=config.target_symbol,
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
    """`BacktestEngine._compute_metrics` 와 동일 계산 — DCA 와 동일."""
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
