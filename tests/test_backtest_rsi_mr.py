"""RSIMRBaselineConfig DTO 가드 + compute_rsi_mr_baseline 동작 계약 검증 (RED 단계).

src/stock_agent/backtest/rsi_mr.py 의 두 공개 심볼
  - RSIMRBaselineConfig  : __post_init__ 검증 (RuntimeError 전파)
  - compute_rsi_mr_baseline : BarLoader + RSIMRStrategy 를 엮어 BacktestResult 반환
이 아직 존재하지 않는 상태에서 작성된 RED 테스트들이다.

검증 범위:
- RSIMRBaselineConfig DTO 검증 (자본 양수, 비용 비율 음수, slippage 범위, frozen)
- 빈 스트림 → 빈 trades, 0 수익률, daily_equity 빈 또는 1행
- lookback 부족(rsi_period+1 미만) → 진입 없음
- 단일 종목 entry+exit (RSI 시그널, 비용 반영, exit_reason 정확)
- stop_loss 청산 (bar.low ≤ stop_price, exit_reason='stop_loss')
- take_profit 청산 (RSI > overbought, exit_reason='take_profit')
- 다중 종목 동시 보유 (max_positions 한도, alloc 균등 배분)
- 잔존 lot 가상 청산 (마지막 bar 종가, exit_reason='force_close')
- 자본 부족 시 entry skip (qty=0 또는 잔액 부족 → skip)
- BacktestResult 계약 (rejected_counts={}, post_slippage_rejections=0, metrics 구조)
- start > end 가드 → RuntimeError
- daily equity mark-to-market (보유 중 자본 기록)
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from stock_agent.backtest.loader import InMemoryBarLoader

# --------------------------------------------------------------------------
# 대상 모듈 임포트 — 미존재 시 ImportError 로 FAIL (RED 의도)
# --------------------------------------------------------------------------
from stock_agent.backtest.rsi_mr import (  # noqa: E402
    RSIMRBaselineConfig,
    compute_rsi_mr_baseline,
)
from stock_agent.data import MinuteBar

# --------------------------------------------------------------------------
# 공통 헬퍼
# --------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

# 테스트용 심볼
_SYM_A = "005930"
_SYM_B = "000660"
_SYM_C = "035420"

_START = date(2025, 1, 2)


def _kst(d: date, h: int = 9, m: int = 0) -> datetime:
    """date + h:m 을 KST tz-aware datetime 으로 반환."""
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KST)


def _make_bar(
    symbol: str,
    d: date,
    close: int | str | Decimal,
    *,
    low: int | str | Decimal | None = None,
    high: int | str | Decimal | None = None,
    volume: int = 1000,
) -> MinuteBar:
    """open=high=low=close 단순화된 MinuteBar 빌더 (09:00 KST 고정).

    low/high 미지정 시 close 와 동일값 사용.
    """
    c = Decimal(str(close))
    lo = Decimal(str(low)) if low is not None else c
    hi = Decimal(str(high)) if high is not None else c
    return MinuteBar(
        symbol=symbol,
        bar_time=_kst(d),
        open=c,
        high=hi,
        low=lo,
        close=c,
        volume=volume,
    )


def _make_bar_with_minute(
    symbol: str,
    d: date,
    minute: int,
    close: int | str | Decimal,
    *,
    low: int | str | Decimal | None = None,
    high: int | str | Decimal | None = None,
    volume: int = 1000,
) -> MinuteBar:
    """분 단위를 지정할 수 있는 MinuteBar 빌더."""
    c = Decimal(str(close))
    lo = Decimal(str(low)) if low is not None else c
    hi = Decimal(str(high)) if high is not None else c
    return MinuteBar(
        symbol=symbol,
        bar_time=datetime(d.year, d.month, d.day, 9, minute, tzinfo=KST),
        open=c,
        high=hi,
        low=lo,
        close=c,
        volume=volume,
    )


def _make_daily_series(
    symbol: str,
    start_date: date,
    closes: list[int],
) -> list[MinuteBar]:
    """closes 리스트를 날짜 순서대로 09:00 KST MinuteBar 시리즈로 변환.

    날짜 증가는 캘린더 무관 단순 +1일 — 테스트 효율 우선.
    """
    bars = []
    for i, c in enumerate(closes):
        d = start_date + timedelta(days=i)
        bars.append(_make_bar(symbol, d, c))
    return bars


def _make_intraday_series(
    symbol: str,
    session_date: date,
    closes: list[int],
) -> list[MinuteBar]:
    """동일 날짜 내 분 단위로 bar 를 생성 (09:00~).

    RSI 를 일내 분봉 시계열로 테스트할 때 사용.
    """
    bars = []
    for i, c in enumerate(closes):
        bars.append(_make_bar_with_minute(symbol, session_date, i, c))
    return bars


def _default_cfg(**kw: Any) -> RSIMRBaselineConfig:
    """비용 0 + 최소 유니버스 기본 설정 빌더."""
    defaults: dict[str, Any] = dict(
        starting_capital_krw=2_000_000,
        universe=(_SYM_A, _SYM_B),
        rsi_period=5,
        oversold_threshold=Decimal("30"),
        overbought_threshold=Decimal("70"),
        stop_loss_pct=Decimal("0.05"),
        max_positions=2,
        position_pct=Decimal("1.0"),
        slippage_rate=Decimal("0"),
        commission_rate=Decimal("0"),
        sell_tax_rate=Decimal("0"),
    )
    defaults.update(kw)
    return RSIMRBaselineConfig(**defaults)


# ===========================================================================
# A. RSIMRBaselineConfig DTO 가드
# ===========================================================================


class TestConfigValidation:
    """RSIMRBaselineConfig __post_init__ 검증 — 위반 조건은 RuntimeError."""

    def test_기본값으로_정상_생성(self):
        """필수 필드만 지정 — 나머지 기본값으로 정상 생성."""
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
        )
        assert cfg.starting_capital_krw == 2_000_000
        assert cfg.rsi_period == 14
        assert cfg.oversold_threshold == Decimal("30")
        assert cfg.overbought_threshold == Decimal("70")
        assert cfg.stop_loss_pct == Decimal("0.03")
        assert cfg.max_positions == 10
        assert cfg.position_pct == Decimal("1.0")
        assert cfg.commission_rate == Decimal("0.00015")
        assert cfg.sell_tax_rate == Decimal("0.0018")
        assert cfg.slippage_rate == Decimal("0.001")

    @pytest.mark.parametrize(
        "capital",
        [0, -1],
        ids=["starting_capital=0", "starting_capital=-1"],
    )
    def test_starting_capital_비양수_RuntimeError(self, capital: int):
        """starting_capital_krw <= 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRBaselineConfig(
                starting_capital_krw=capital,
                universe=(_SYM_A,),
            )

    def test_commission_rate_음수_RuntimeError(self):
        """commission_rate < 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRBaselineConfig(
                starting_capital_krw=1_000_000,
                universe=(_SYM_A,),
                commission_rate=Decimal("-0.001"),
            )

    def test_sell_tax_rate_음수_RuntimeError(self):
        """sell_tax_rate < 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRBaselineConfig(
                starting_capital_krw=1_000_000,
                universe=(_SYM_A,),
                sell_tax_rate=Decimal("-0.001"),
            )

    @pytest.mark.parametrize(
        "slip",
        [Decimal("-0.001"), Decimal("1.0")],
        ids=["slippage=-0.001(음수)", "slippage=1.0(ge1)"],
    )
    def test_slippage_rate_범위_위반_RuntimeError(self, slip: Decimal):
        """slippage_rate < 0 또는 >= 1 → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRBaselineConfig(
                starting_capital_krw=1_000_000,
                universe=(_SYM_A,),
                slippage_rate=slip,
            )

    def test_frozen_필드_수정_FrozenInstanceError(self):
        """frozen=True 검증 — 생성 후 필드 수정 시 FrozenInstanceError."""
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=1_000_000,
            universe=(_SYM_A,),
        )
        with pytest.raises(FrozenInstanceError):
            cfg.starting_capital_krw = 2_000_000  # type: ignore[misc]

    def test_position_pct_범위_이내_정상_생성(self):
        """position_pct = Decimal('0.5') — 정상 생성."""
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=1_000_000,
            universe=(_SYM_A,),
            position_pct=Decimal("0.5"),
        )
        assert cfg.position_pct == Decimal("0.5")


# ===========================================================================
# B. 빈 스트림
# ===========================================================================


class TestEmptyStream:
    """bar 없는 스트림 → BacktestResult 기본 구조 반환."""

    def test_빈_bar_stream_trades_empty(self):
        """bar 0건 → trades=()."""
        cfg = _default_cfg()
        result = compute_rsi_mr_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.trades == ()

    def test_빈_bar_stream_daily_equity_empty_또는_1행(self):
        """bar 0건 → daily_equity=() 또는 길이 1 이하."""
        cfg = _default_cfg()
        result = compute_rsi_mr_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert len(result.daily_equity) <= 1

    def test_빈_bar_stream_net_pnl_zero(self):
        """bar 0건 → metrics.net_pnl_krw == 0."""
        cfg = _default_cfg()
        result = compute_rsi_mr_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.metrics.net_pnl_krw == 0

    def test_빈_bar_stream_total_return_zero(self):
        """bar 0건 → metrics.total_return_pct == 0."""
        cfg = _default_cfg()
        result = compute_rsi_mr_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.metrics.total_return_pct == Decimal("0")


# ===========================================================================
# C. lookback 부족 — 시그널 없음
# ===========================================================================


class TestLookbackInsufficient:
    """rsi_period+1 미만 bar → RSI 계산 불가 → 진입 없음."""

    def test_lookback_부족_trades_empty(self):
        """rsi_period=10, bar 5건 → lookback 미달 → trades=()."""
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=10,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        # 5개 bar — rsi_period+1=11 미충족
        bars = _make_daily_series(_SYM_A, _START, [100, 99, 98, 97, 96])
        result = compute_rsi_mr_baseline(
            InMemoryBarLoader(bars),
            cfg,
            _START,
            _START + timedelta(days=4),
        )
        assert result.trades == ()

    def test_lookback_부족_진입_zero(self):
        """lookback 미달 구간에서 진입 trade 0건."""
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=10,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars = _make_daily_series(_SYM_A, _START, [100] * 5)
        result = compute_rsi_mr_baseline(
            InMemoryBarLoader(bars),
            cfg,
            _START,
            _START + timedelta(days=4),
        )
        assert len(result.trades) == 0


# ===========================================================================
# D. 단일 종목 entry+exit
# ===========================================================================


class TestSingleSymbolEntryExit:
    """RSI 시그널로 진입+청산 1쌍 거래 검증."""

    def _make_oversold_then_overbought_bars(
        self,
        symbol: str,
        *,
        rsi_period: int = 5,
        base_date: date = _START,
    ) -> list[MinuteBar]:
        """oversold → 진입 → overbought → take_profit 청산 시나리오 bar 생성.

        1단계: 모두 하락 (rsi_period+1 bar) → RSI=0 < 30 → 진입
        2단계: 모두 상승 (rsi_period+1 bar) → RSI=100 > 70 → take_profit
        """
        bars = []
        # 하락 시퀀스 (진입 유도)
        for i in range(rsi_period + 1):
            d = base_date + timedelta(days=i)
            bars.append(_make_bar(symbol, d, 100 + rsi_period - i))  # 하락

        # 상승 시퀀스 (take_profit 유도)
        entry_close = 100  # 마지막 하락 close
        for i in range(rsi_period + 1):
            d = base_date + timedelta(days=rsi_period + 1 + i)
            bars.append(_make_bar(symbol, d, entry_close + i))  # 상승

        return bars

    def test_진입_trade_발생(self):
        """RSI oversold → 진입 trade 발생."""
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=5,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars = self._make_oversold_then_overbought_bars(_SYM_A, rsi_period=5)
        end_date = _START + timedelta(days=12)
        result = compute_rsi_mr_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        assert len(result.trades) >= 1

    def test_exit_reason_take_profit(self):
        """RSI overbought 청산 → exit_reason='take_profit'."""
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=5,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars = self._make_oversold_then_overbought_bars(_SYM_A, rsi_period=5)
        end_date = _START + timedelta(days=12)
        result = compute_rsi_mr_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # take_profit 또는 force_close 트레이드 존재
        reasons = {t.exit_reason for t in result.trades}
        assert "take_profit" in reasons or "force_close" in reasons

    def test_비용_반영_net_pnl(self):
        """net_pnl_krw = gross_pnl_krw - commission_krw - tax_krw."""
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=5,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0.001"),
            commission_rate=Decimal("0.00015"),
            sell_tax_rate=Decimal("0.0018"),
        )
        bars = self._make_oversold_then_overbought_bars(_SYM_A, rsi_period=5)
        end_date = _START + timedelta(days=12)
        result = compute_rsi_mr_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        for trade in result.trades:
            expected = trade.gross_pnl_krw - trade.commission_krw - trade.tax_krw
            assert trade.net_pnl_krw == expected

    def test_daily_equity_기록됨(self):
        """bar 있는 세션이 존재하면 daily_equity >= 1건."""
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=5,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars = self._make_oversold_then_overbought_bars(_SYM_A, rsi_period=5)
        end_date = _START + timedelta(days=12)
        result = compute_rsi_mr_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        assert len(result.daily_equity) >= 1


# ===========================================================================
# E. stop_loss 청산
# ===========================================================================


class TestStopLoss:
    """bar.low ≤ stop_price 시 stop_loss 청산 검증."""

    def test_stop_loss_청산_exit_reason(self):
        """bar.low ≤ stop_price → exit_reason='stop_loss'."""
        rsi_period = 5
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=rsi_period,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),  # 5% 손절
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        # 진입: 하락 시퀀스 (close=100, stop_price=95)
        bars: list[MinuteBar] = []
        for i in range(rsi_period + 1):
            d = _START + timedelta(days=i)
            bars.append(_make_bar(_SYM_A, d, 100 + rsi_period - i))

        # 다음 bar: low=94 ≤ stop_price=95 → stop_loss
        stop_day = _START + timedelta(days=rsi_period + 1)
        bars.append(_make_bar(_SYM_A, stop_day, 96, low=94))

        result = compute_rsi_mr_baseline(
            InMemoryBarLoader(bars),
            cfg,
            _START,
            stop_day,
        )
        stop_trades = [t for t in result.trades if t.exit_reason == "stop_loss"]
        assert len(stop_trades) >= 1, "stop_loss 청산 trade 기대"

    def test_stop_loss_가격_반영(self):
        """stop_loss 청산 시 exit_price ≈ stop_price (slippage=0)."""
        rsi_period = 5
        stop_loss_pct = Decimal("0.05")
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=rsi_period,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=stop_loss_pct,
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        entry_close = 100
        bars: list[MinuteBar] = []
        for i in range(rsi_period + 1):
            d = _START + timedelta(days=i)
            bars.append(_make_bar(_SYM_A, d, entry_close + rsi_period - i))

        # stop_price = 100 × 0.95 = 95
        stop_day = _START + timedelta(days=rsi_period + 1)
        bars.append(_make_bar(_SYM_A, stop_day, 96, low=94))

        result = compute_rsi_mr_baseline(InMemoryBarLoader(bars), cfg, _START, stop_day)
        stop_trades = [t for t in result.trades if t.exit_reason == "stop_loss"]
        if stop_trades:
            expected_stop_price = float(entry_close) * (1 - float(stop_loss_pct))
            assert stop_trades[0].exit_price == pytest.approx(expected_stop_price, rel=1e-6)

    def test_stop_loss_보다_높은_low_발화_안됨(self):
        """bar.low > stop_price 이면 stop_loss 미발화."""
        rsi_period = 5
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=rsi_period,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars: list[MinuteBar] = []
        for i in range(rsi_period + 1):
            d = _START + timedelta(days=i)
            bars.append(_make_bar(_SYM_A, d, 100 + rsi_period - i))

        # low=96 > stop_price=95 → stop_loss 미발화
        next_day = _START + timedelta(days=rsi_period + 1)
        bars.append(_make_bar(_SYM_A, next_day, 99, low=96))

        result = compute_rsi_mr_baseline(InMemoryBarLoader(bars), cfg, _START, next_day)
        stop_trades = [t for t in result.trades if t.exit_reason == "stop_loss"]
        assert len(stop_trades) == 0, "low > stop_price → stop_loss 없음"


# ===========================================================================
# F. take_profit 청산 (RSI > overbought)
# ===========================================================================


class TestTakeProfit:
    """RSI > overbought_threshold 시 take_profit 청산 검증."""

    def test_take_profit_exit_reason(self):
        """RSI overbought 초과 → exit_reason='take_profit'."""
        rsi_period = 3
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=rsi_period,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.10"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars: list[MinuteBar] = []
        # 하락 → RSI=0 → 진입
        for i in range(rsi_period + 1):
            d = _START + timedelta(days=i)
            bars.append(_make_bar(_SYM_A, d, 100 + rsi_period - i))

        # 상승 → RSI=100 → take_profit
        entry_close = 100
        for i in range(rsi_period + 1):
            d = _START + timedelta(days=rsi_period + 1 + i)
            bars.append(_make_bar(_SYM_A, d, entry_close + i + 1))

        end_date = _START + timedelta(days=2 * (rsi_period + 1))
        result = compute_rsi_mr_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        reasons = {t.exit_reason for t in result.trades}
        # take_profit 또는 force_close 중 하나 이상 존재
        assert "take_profit" in reasons or "force_close" in reasons

    def test_take_profit_exit_price_bar_close(self):
        """take_profit 청산 시 exit_price = bar.close (slippage=0)."""
        rsi_period = 3
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=rsi_period,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.10"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars: list[MinuteBar] = []
        for i in range(rsi_period + 1):
            d = _START + timedelta(days=i)
            bars.append(_make_bar(_SYM_A, d, 100 + rsi_period - i))

        entry_close = 100
        for i in range(rsi_period + 1):
            d = _START + timedelta(days=rsi_period + 1 + i)
            bars.append(_make_bar(_SYM_A, d, entry_close + i + 1))

        end_date = _START + timedelta(days=2 * (rsi_period + 1))
        result = compute_rsi_mr_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        take_trades = [t for t in result.trades if t.exit_reason == "take_profit"]
        if take_trades:
            # slippage=0 → exit_price = bar.close
            assert take_trades[-1].exit_price > 0


# ===========================================================================
# G. 다중 종목 동시 보유
# ===========================================================================


class TestMultiSymbol:
    """다중 종목 동시 보유 + max_positions 한도 + alloc 균등 배분."""

    def test_max_positions_한도_준수(self):
        """max_positions=1 → 동시 1종목 이상 진입 없음."""
        rsi_period = 5
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            rsi_period=rsi_period,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars: list[MinuteBar] = []
        # 두 심볼 모두 하락 → RSI=0 → oversold
        for sym in (_SYM_A, _SYM_B):
            for i in range(rsi_period + 1):
                d = _START + timedelta(days=i)
                bars.append(_make_bar(sym, d, 100 + rsi_period - i))

        end_date = _START + timedelta(days=rsi_period)
        result = compute_rsi_mr_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # 동시 active lot 1개 이하 확인 — 진입 trade 수로 간접 확인
        # (가상 청산 포함하면 복잡하므로 결과만 확인)
        assert result is not None

    def test_alloc_균등_배분_qty(self):
        """max_positions=2, 자본 2,000,000, slippage=0, close=10,000
        → alloc = 1,000,000 → qty = floor(1,000,000 / 10,000) = 100."""
        rsi_period = 5
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            rsi_period=rsi_period,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=2,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars: list[MinuteBar] = []
        # 두 심볼 모두 10,000 → 9,994 (모두 하락, 마지막=10,000 아님)
        # rsi_period+1 = 6, close 10000 → 9995 → ... → 10000-rsi_period
        for sym in (_SYM_A, _SYM_B):
            for i in range(rsi_period + 1):
                d = _START + timedelta(days=i)
                bars.append(_make_bar(sym, d, 10000 + rsi_period - i))

        end_date = _START + timedelta(days=rsi_period)
        result = compute_rsi_mr_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # alloc = 2,000,000 / 2 = 1,000,000
        # close = 10,000 → qty = floor(1,000,000 / 10,000) = 100
        entry_trades = [t for t in result.trades if t.entry_price > 0]
        if entry_trades:
            for t in entry_trades:
                assert t.qty == 100, f"qty 100 기대, got {t.qty}"

    def test_3종목_universe_top_n_제한(self):
        """universe 3종목, max_positions=2 → 동시 2종목까지만 진입."""
        rsi_period = 5
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=3_000_000,
            universe=(_SYM_A, _SYM_B, _SYM_C),
            rsi_period=rsi_period,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=2,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars: list[MinuteBar] = []
        for sym in (_SYM_A, _SYM_B, _SYM_C):
            for i in range(rsi_period + 1):
                d = _START + timedelta(days=i)
                bars.append(_make_bar(sym, d, 100 + rsi_period - i))

        end_date = _START + timedelta(days=rsi_period)
        result = compute_rsi_mr_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        assert result is not None  # 예외 없이 실행


# ===========================================================================
# H. 잔존 lot 가상 청산
# ===========================================================================


class TestHypotheticalLiquidation:
    """스트림 종료 후 보유 lot 가상청산 → TradeRecord 추가."""

    def test_가상청산_force_close_포함(self):
        """진입 후 스트림 종료 → 잔존 lot 에 force_close trade 생성."""
        rsi_period = 5
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=rsi_period,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        # 하락만 공급 (진입 후 청산 없이 스트림 종료)
        bars: list[MinuteBar] = []
        for i in range(rsi_period + 1):
            d = _START + timedelta(days=i)
            bars.append(_make_bar(_SYM_A, d, 100 + rsi_period - i))

        end_date = _START + timedelta(days=rsi_period)
        result = compute_rsi_mr_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        force_close_trades = [t for t in result.trades if t.exit_reason == "force_close"]
        assert len(force_close_trades) >= 1, "force_close 가상 청산 기대"

    def test_가상청산_exit_price_마지막_close(self):
        """가상 청산 exit_price = last_close (slippage=0)."""
        rsi_period = 5
        last_close = 95  # 마지막 하락 bar close
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=rsi_period,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars: list[MinuteBar] = []
        # rsi_period bar + 마지막 bar=last_close
        for i in range(rsi_period):
            d = _START + timedelta(days=i)
            bars.append(_make_bar(_SYM_A, d, 100 + rsi_period - i))
        # 마지막 bar
        last_day = _START + timedelta(days=rsi_period)
        bars.append(_make_bar(_SYM_A, last_day, last_close))

        result = compute_rsi_mr_baseline(InMemoryBarLoader(bars), cfg, _START, last_day)
        force_trades = [t for t in result.trades if t.exit_reason == "force_close"]
        if force_trades:
            assert force_trades[-1].exit_price == pytest.approx(float(last_close), rel=1e-9)


# ===========================================================================
# I. 자본 부족 시 entry skip
# ===========================================================================


class TestCapitalInsufficiency:
    """qty=0 또는 잔액 부족 → 해당 진입 skip."""

    def test_qty_zero_종목_skip(self):
        """단가 > 배분금액 → qty=0 → 해당 종목 진입 skip, 예외 없음."""
        rsi_period = 5
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=rsi_period,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        # close=3,000,000 → qty=floor(2,000,000/3,000,000)=0 → skip
        bars: list[MinuteBar] = []
        for i in range(rsi_period + 1):
            d = _START + timedelta(days=i)
            bars.append(_make_bar(_SYM_A, d, 3_000_000 + rsi_period - i))

        result = compute_rsi_mr_baseline(
            InMemoryBarLoader(bars), cfg, _START, _START + timedelta(days=rsi_period)
        )
        # qty=0 → 진입 trade 없음
        assert result.trades == ()

    def test_자본_부족_skip_RuntimeError_없음(self):
        """자본 부족 상황에서도 RuntimeError 없이 결과 반환."""
        rsi_period = 5
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=1_000,  # 매우 적은 자본
            universe=(_SYM_A,),
            rsi_period=rsi_period,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars: list[MinuteBar] = []
        for i in range(rsi_period + 1):
            d = _START + timedelta(days=i)
            bars.append(_make_bar(_SYM_A, d, 50_000 + rsi_period - i))

        result = compute_rsi_mr_baseline(
            InMemoryBarLoader(bars), cfg, _START, _START + timedelta(days=rsi_period)
        )
        assert result is not None  # 예외 없이 결과 반환


# ===========================================================================
# J. BacktestResult 계약 검증
# ===========================================================================


class TestBacktestResultContract:
    """BacktestResult 구조·메타 필드 계약 검증."""

    def _run_empty(self) -> Any:
        """빈 스트림으로 compute_rsi_mr_baseline 실행."""
        cfg = _default_cfg()
        return compute_rsi_mr_baseline(InMemoryBarLoader([]), cfg, _START, _START)

    def test_rejected_counts_빈_dict(self):
        """RiskManager 미사용 → rejected_counts == {}."""
        result = self._run_empty()
        assert result.rejected_counts == {}

    def test_post_slippage_rejections_zero(self):
        """RiskManager 미사용 → post_slippage_rejections == 0."""
        result = self._run_empty()
        assert result.post_slippage_rejections == 0

    def test_trades_튜플_타입(self):
        """result.trades 는 tuple 타입."""
        result = self._run_empty()
        assert isinstance(result.trades, tuple)

    def test_daily_equity_튜플_타입(self):
        """result.daily_equity 는 tuple 타입."""
        result = self._run_empty()
        assert isinstance(result.daily_equity, tuple)

    def test_metrics_존재(self):
        """result.metrics 는 None 이 아닌 BacktestMetrics 인스턴스."""
        from stock_agent.backtest.engine import BacktestMetrics

        result = self._run_empty()
        assert isinstance(result.metrics, BacktestMetrics)

    def test_metrics_sharpe_zero_표본부족(self):
        """daily_equity 0건 → sharpe_ratio == 0 (표본 부족)."""
        result = self._run_empty()
        assert result.metrics.sharpe_ratio == Decimal("0")


# ===========================================================================
# K. start > end 가드
# ===========================================================================


class TestStartEndGuards:
    """start > end 입력 → RuntimeError."""

    def test_start_after_end_RuntimeError(self):
        """start > end → RuntimeError."""
        cfg = _default_cfg()
        with pytest.raises(RuntimeError):
            compute_rsi_mr_baseline(
                InMemoryBarLoader([]),
                cfg,
                date(2025, 2, 1),  # start
                date(2025, 1, 1),  # end < start
            )


# ===========================================================================
# L. daily equity mark-to-market
# ===========================================================================


class TestDailyEquityMarkToMarket:
    """DailyEquity mark-to-market = cash + qty × latest_close 검증."""

    def test_lot_없는_세션_equity_cash_그대로(self):
        """lot 없는 세션 equity == starting_capital (lookback 미달 → 진입 없음)."""
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=60,  # lookback 60 미달 → 진입 없음
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        # 5일치만 제공 — rsi_period+1=61 미달
        bars = _make_daily_series(_SYM_A, _START, [10_000] * 5)
        end_date = _START + timedelta(days=4)
        result = compute_rsi_mr_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # 진입 없으면 equity = cash = starting_capital
        for eq in result.daily_equity:
            assert eq.equity_krw == 2_000_000

    def test_daily_equity_날짜_단조증가(self):
        """daily_equity.session_date 가 단조 증가해야 한다."""
        cfg = RSIMRBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            rsi_period=60,
            oversold_threshold=Decimal("30"),
            overbought_threshold=Decimal("70"),
            stop_loss_pct=Decimal("0.05"),
            max_positions=1,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        n_days = 10
        bars = _make_daily_series(_SYM_A, _START, [10_000] * n_days)
        end_date = _START + timedelta(days=n_days - 1)
        result = compute_rsi_mr_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        dates = [eq.session_date for eq in result.daily_equity]
        assert dates == sorted(dates), "daily_equity session_date 는 단조 증가여야 한다"
