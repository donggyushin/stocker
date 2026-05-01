"""DCA (Dollar-Cost Averaging) baseline 평가 함수.

ADR-0019 Step F PR1 — F1 DCA baseline. KOSPI 200 ETF (069500 KODEX 200) 매월
정해진 영업일에 정액 시장가 매수 + 영구 보유 시뮬레이션. ADR-0022 게이트 2
(DCA baseline 대비 알파) 의 비교 기준 산출.

설계 결정 — `BacktestEngine` 우회
- `BacktestEngine` 은 단일 lot 가정 (`active[symbol]` 단일 키 + `_handle_entry`
  덮어쓰기) + 매 세션 force_close 가정 (`_close_session` 이 잔존 active 잔여 시
  `RuntimeError`) 이라 DCA (다중 lot 누적·영구 보유) 와 비호환. 별도 평가 함수
  신설.
- 결과는 동일 `BacktestResult` 형식으로 산출 — CLI · runbook · 메트릭 비교
  파이프라인을 그대로 재사용.

비용 계약 (`DCABaselineConfig` 기본값은 `BacktestConfig` 와 동일)
- 슬리피지: 시장가 0.1% 불리 (매수 +방향, 매도 -방향).
- 수수료: 매수·매도 대칭 0.015% (KIS 한투 비대면).
- 거래세: 매도만 0.18% (KRX 2026-04).

체결 흐름
1. `DCAStrategy.on_bar` 가 매월 N 번째 영업일에 `EntrySignal` 1건 발생.
2. `entry_fill = bar.close * (1 + slippage_rate)` 계산.
3. `qty = int(monthly_investment_krw / entry_fill)` (floor). qty=0 → skip.
4. `notional_dec = entry_fill * qty`, `buy_commission = int(notional * commission_rate)`.
5. `total_cost = int(notional) + buy_commission` 가 `cash` 초과 → skip.
6. 그 외: `cash -= total_cost`, lot 누적.

Hypothetical 청산 (스트림 종료 후 1회)
- DCA 는 영구 보유라 실제 청산 없음. 하지만 `BacktestResult.trades` 의 정합성
  + ADR-0022 게이트 1 (MDD) 비교 합리성을 위해 마지막 close 기준 가상 청산
  모델링.
- `exit_fill = last_target_close * (1 - slippage_rate)`. 각 lot 별 `TradeRecord`
  1건 (`exit_reason="force_close"`).
- `BacktestMetrics.net_pnl_krw` 는 `ending - starting` (mark-to-market 기준,
  슬리피지·세금 미반영). 거래별 손익은 `TradeRecord.net_pnl_krw` (실제 청산 비용
  반영).

DailyEquity
- 세션 경계마다 `cash + sum(lot.qty) * last_close` 를 mark-to-market 기록.
- 스트림 종료 시 마지막 세션 1건 추가 기록.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Final

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
from stock_agent.strategy.base import EntrySignal
from stock_agent.strategy.dca import DCAConfig, DCAStrategy

_KST: Final = timezone(timedelta(hours=9))
_SYMBOL_RE = re.compile(r"^\d{6}$")
_PURCHASE_DAY_MAX = 28


@dataclass(frozen=True, slots=True)
class DCABaselineConfig:
    """DCA baseline 백테스트 파라미터.

    Raises:
        RuntimeError: 자본·투자금 비양수, monthly > starting (첫 달 매수 불가),
            symbol 정규식 위반, purchase_day 범위 [1, 28] 위반, 비율 음수,
            slippage_rate `[0, 1)` 범위 위반.
    """

    starting_capital_krw: int
    monthly_investment_krw: int
    target_symbol: str = "069500"
    purchase_day: int = 1
    commission_rate: Decimal = Decimal("0.00015")
    sell_tax_rate: Decimal = Decimal("0.0018")
    slippage_rate: Decimal = Decimal("0.001")

    def __post_init__(self) -> None:
        if self.starting_capital_krw <= 0:
            raise RuntimeError(
                f"starting_capital_krw 는 양수여야 합니다 (got={self.starting_capital_krw})"
            )
        if self.monthly_investment_krw <= 0:
            raise RuntimeError(
                f"monthly_investment_krw 는 양수여야 합니다 (got={self.monthly_investment_krw})"
            )
        if self.monthly_investment_krw > self.starting_capital_krw:
            raise RuntimeError(
                f"monthly_investment_krw({self.monthly_investment_krw}) 는 "
                f"starting_capital_krw({self.starting_capital_krw}) 이하여야 합니다 — "
                "첫 달 매수 자체가 불가능."
            )
        if not _SYMBOL_RE.match(self.target_symbol):
            raise RuntimeError(
                f"target_symbol 은 6자리 숫자 문자열이어야 합니다 (got={self.target_symbol!r})"
            )
        if self.purchase_day < 1 or self.purchase_day > _PURCHASE_DAY_MAX:
            raise RuntimeError(
                f"purchase_day 는 [1, {_PURCHASE_DAY_MAX}] 범위여야 합니다 "
                f"(got={self.purchase_day})"
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
class _DCALot:
    """DCA 누적 lot — 월별 매수 1건 단위."""

    qty: int
    entry_fill_price: Decimal
    entry_ts: datetime
    entry_notional_krw: int
    buy_commission_krw: int


def compute_dca_baseline(
    loader: BarLoader,
    config: DCABaselineConfig,
    start: date,
    end: date,
) -> BacktestResult:
    """DCA baseline 시뮬레이션 → BacktestResult.

    `loader.stream(start, end, (target_symbol,))` 로 단일 심볼 분봉 스트림을
    소비하고, `DCAStrategy` 가 발생시킨 `EntrySignal` 마다 매수 처리. 스트림
    종료 후 마지막 close 기준 가상 청산으로 `TradeRecord` 생성.

    Args:
        loader: `BarLoader` Protocol 만족 — 보통 `DailyBarLoader` 를 일봉 경로로
            주입. 멀티 심볼 분봉이어도 `target_symbol` 만 요청하므로 안전.
        config: DCA 파라미터.
        start, end: 백테스트 구간 (경계 포함).

    Returns:
        `BacktestResult` — `trades`, `daily_equity`, `metrics`. `rejected_counts`
        은 항상 빈 dict, `post_slippage_rejections=0` (DCA 는 RiskManager 미사용).
    """
    dca_cfg = DCAConfig(
        monthly_investment_krw=config.monthly_investment_krw,
        target_symbol=config.target_symbol,
        purchase_day=config.purchase_day,
    )
    strategy = DCAStrategy(dca_cfg)

    cash: int = config.starting_capital_krw
    lots: list[_DCALot] = []
    daily_equity: list[DailyEquity] = []
    last_session_date: date | None = None
    last_target_close: Decimal | None = None
    last_target_bar_time: datetime | None = None

    def _equity_at(close_price: Decimal | None) -> int:
        if close_price is None or not lots:
            return cash
        total_qty = sum(lot.qty for lot in lots)
        return cash + int(close_price * Decimal(total_qty))

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
            if not isinstance(sig, EntrySignal):
                continue  # DCAStrategy 는 ExitSignal 미생성 — defensive
            cash, lot = _attempt_buy(cash, bar, sig, config)
            if lot is not None:
                lots.append(lot)

    if last_session_date is not None:
        daily_equity.append(
            DailyEquity(
                session_date=last_session_date,
                equity_krw=_equity_at(last_target_close),
            )
        )

    trades = _hypothetical_liquidation(
        lots=lots,
        last_target_close=last_target_close,
        last_target_bar_time=last_target_bar_time,
        config=config,
    )

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
    bar: MinuteBar,
    signal: EntrySignal,
    config: DCABaselineConfig,
) -> tuple[int, _DCALot | None]:
    """단일 EntrySignal 처리. 매수 성공 시 (`new_cash`, lot), 실패 시 (`cash`, None)."""
    entry_fill = bar.close * (Decimal("1") + config.slippage_rate)
    if entry_fill <= 0:
        logger.debug("DCA skip: entry_fill 비양수 ({})", entry_fill)
        return cash, None

    target_notional = Decimal(config.monthly_investment_krw)
    qty = int(target_notional / entry_fill)
    if qty <= 0:
        logger.debug(
            "DCA skip: monthly_investment 가 1주 단가 미만 (close={}, monthly={})",
            bar.close,
            config.monthly_investment_krw,
        )
        return cash, None

    notional_dec = entry_fill * Decimal(qty)
    notional_int = int(notional_dec)
    buy_comm = int(notional_dec * config.commission_rate)
    total_cost = notional_int + buy_comm
    if total_cost > cash:
        logger.debug(
            "DCA skip: 잔액 부족 (need={}, have={})",
            total_cost,
            cash,
        )
        return cash, None

    lot = _DCALot(
        qty=qty,
        entry_fill_price=entry_fill,
        entry_ts=signal.ts,
        entry_notional_krw=notional_int,
        buy_commission_krw=buy_comm,
    )
    logger.info(
        "DCA buy: {s} qty={q} entry={p} cost={c}",
        s=signal.symbol,
        q=qty,
        p=entry_fill,
        c=total_cost,
    )
    return cash - total_cost, lot


def _hypothetical_liquidation(
    *,
    lots: list[_DCALot],
    last_target_close: Decimal | None,
    last_target_bar_time: datetime | None,
    config: DCABaselineConfig,
) -> list[TradeRecord]:
    """스트림 종료 후 마지막 close 기준 가상 청산 → TradeRecord 리스트."""
    if not lots or last_target_close is None or last_target_bar_time is None:
        return []

    exit_fill = last_target_close * (Decimal("1") - config.slippage_rate)
    trades: list[TradeRecord] = []
    for lot in lots:
        exit_notional_dec = exit_fill * Decimal(lot.qty)
        exit_notional_int = int(exit_notional_dec)
        sell_comm = int(exit_notional_dec * config.commission_rate)
        tax = int(exit_notional_dec * config.sell_tax_rate)
        gross_pnl = exit_notional_int - lot.entry_notional_krw
        commission_total = lot.buy_commission_krw + sell_comm
        net_pnl = gross_pnl - commission_total - tax
        trades.append(
            TradeRecord(
                symbol=config.target_symbol,
                entry_ts=lot.entry_ts,
                entry_price=lot.entry_fill_price,
                exit_ts=last_target_bar_time,
                exit_price=exit_fill,
                qty=lot.qty,
                exit_reason="force_close",
                gross_pnl_krw=gross_pnl,
                commission_krw=commission_total,
                tax_krw=tax,
                net_pnl_krw=net_pnl,
            )
        )
    return trades


def _compute_metrics(
    *,
    starting: int,
    ending: int,
    equity_series: list[int],
    net_pnls: list[int],
    trade_count: int,
    session_count: int,
) -> BacktestMetrics:
    """`BacktestEngine._compute_metrics` 와 동일 계산 — 메트릭 형식 회귀 0."""
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
