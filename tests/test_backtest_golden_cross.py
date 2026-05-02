"""GoldenCrossBaselineConfig DTO 가드 + compute_golden_cross_baseline 동작 계약 검증.

src/stock_agent/backtest/golden_cross.py 의 두 공개 심볼
  - GoldenCrossBaselineConfig : __post_init__ 검증 (RuntimeError 전파)
  - compute_golden_cross_baseline : BarLoader + GoldenCrossStrategy 를 엮어 BacktestResult 반환
이 아직 존재하지 않는 상태에서 작성된 RED 테스트들이다.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from decimal import Decimal
from typing import Any

import pytest

# --------------------------------------------------------------------------
# 대상 모듈 임포트 — 미존재 시 ImportError 로 FAIL (RED 의도)
# --------------------------------------------------------------------------
from stock_agent.backtest.golden_cross import (
    GoldenCrossBaselineConfig,
    compute_golden_cross_baseline,
)
from stock_agent.backtest.loader import InMemoryBarLoader
from stock_agent.data import MinuteBar

# --------------------------------------------------------------------------
# 공통 헬퍼
# --------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))
_SYMBOL = "069500"
_START = date(2025, 1, 2)


def _make_minute_bar(
    symbol: str,
    dt: datetime,
    close: int | str | Decimal,
    *,
    open: int | str | Decimal | None = None,
    high: int | str | Decimal | None = None,
    low: int | str | Decimal | None = None,
    volume: int = 1000,
) -> MinuteBar:
    """open=high=low=close 단순화된 MinuteBar 빌더."""
    c = Decimal(str(close))
    o = Decimal(str(open)) if open is not None else c
    h = Decimal(str(high)) if high is not None else c
    lo = Decimal(str(low)) if low is not None else c
    return MinuteBar(
        symbol=symbol,
        bar_time=dt,
        open=o,
        high=h,
        low=lo,
        close=c,
        volume=volume,
    )


def _make_daily_stream(
    symbol: str,
    start_date: date,
    closes: list[int],
) -> list[MinuteBar]:
    """closes 시퀀스를 09:00 KST MinuteBar 시리즈로 변환."""
    bars = []
    for i, c in enumerate(closes):
        dt = datetime.combine(
            start_date + timedelta(days=i),
            dtime(9, 0),
            tzinfo=KST,
        )
        bars.append(_make_minute_bar(symbol, dt, c))
    return bars


def _cfg(**kw: Any) -> GoldenCrossBaselineConfig:
    """기본 GoldenCrossBaselineConfig 빌더 (최소 필수 인자 포함)."""
    defaults: dict[str, Any] = dict(starting_capital_krw=2_000_000)
    defaults.update(kw)
    return GoldenCrossBaselineConfig(**defaults)


# ===========================================================================
# A. GoldenCrossBaselineConfig DTO 가드
# ===========================================================================


class TestConfigValidation:
    """GoldenCrossBaselineConfig __post_init__ 검증 — 모든 위반 조건은 RuntimeError."""

    def test_기본값으로_정상_생성(self):
        """starting_capital_krw 만 필수 — 나머지 기본값으로 인스턴스 생성 가능."""
        cfg = GoldenCrossBaselineConfig(starting_capital_krw=1_000_000)
        assert cfg.starting_capital_krw == 1_000_000
        assert cfg.target_symbol == "069500"
        assert cfg.sma_period == 200
        assert cfg.position_pct == Decimal("1.0")

    @pytest.mark.parametrize(
        "capital",
        [0, -1],
        ids=["starting_capital=0", "starting_capital=-1"],
    )
    def test_starting_capital_비양수_RuntimeError(self, capital: int):
        """starting_capital_krw <= 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            GoldenCrossBaselineConfig(starting_capital_krw=capital)

    @pytest.mark.parametrize(
        "symbol",
        ["ABC123", "12345"],
        ids=["symbol=ABC123(영문포함)", "symbol=12345(5자리)"],
    )
    def test_target_symbol_정규식_위반_RuntimeError(self, symbol: str):
        """target_symbol 이 6자리 숫자 정규식 위반 → RuntimeError."""
        with pytest.raises(RuntimeError):
            _cfg(target_symbol=symbol)

    def test_sma_period_비양수_RuntimeError(self):
        """sma_period <= 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            _cfg(sma_period=0)

    @pytest.mark.parametrize(
        "pct",
        [Decimal("0"), Decimal("1.1")],
        ids=["position_pct=0", "position_pct=1.1"],
    )
    def test_position_pct_범위_위반_RuntimeError(self, pct: Decimal):
        """position_pct <= 0 또는 > 1 → RuntimeError."""
        with pytest.raises(RuntimeError):
            _cfg(position_pct=pct)

    def test_commission_rate_음수_RuntimeError(self):
        """commission_rate < 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            _cfg(commission_rate=Decimal("-0.001"))

    def test_sell_tax_rate_음수_RuntimeError(self):
        """sell_tax_rate < 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            _cfg(sell_tax_rate=Decimal("-0.001"))

    @pytest.mark.parametrize(
        "slip",
        [Decimal("-0.001"), Decimal("1.0")],
        ids=["slippage=-0.001(음수)", "slippage=1.0(ge1)"],
    )
    def test_slippage_rate_범위_위반_RuntimeError(self, slip: Decimal):
        """slippage_rate < 0 또는 >= 1 → RuntimeError."""
        with pytest.raises(RuntimeError):
            _cfg(slippage_rate=slip)

    def test_frozen_필드_수정_FrozenInstanceError(self):
        """frozen=True 검증 — 생성 후 필드 수정 시 FrozenInstanceError."""
        cfg = GoldenCrossBaselineConfig(starting_capital_krw=1_000_000)
        with pytest.raises(FrozenInstanceError):
            cfg.starting_capital_krw = 2_000_000  # type: ignore[misc]


# ===========================================================================
# B. 정상 실행 케이스
# ===========================================================================


class TestNormalExecution:
    """compute_golden_cross_baseline 정상 경로 검증."""

    def _zero_cost_cfg(self, **kw: Any) -> GoldenCrossBaselineConfig:
        """비용 0 설정 (비용 격리 목적)."""
        defaults: dict[str, Any] = dict(
            starting_capital_krw=2_000_000,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        defaults.update(kw)
        return GoldenCrossBaselineConfig(**defaults)

    def test_빈_스트림_빈_trades_반환(self):
        """bar 없으면 trades=(), daily_equity=() 반환, total_return_pct=0."""
        cfg = self._zero_cost_cfg()
        result = compute_golden_cross_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.trades == ()
        assert result.daily_equity == ()
        assert result.metrics.total_return_pct == Decimal("0")

    def test_SMA_lookback_미만_데이터_시그널없음(self):
        """sma_period=5, bar 4개 → SMA 미확정 → EntrySignal 0, trades=()."""
        cfg = self._zero_cost_cfg(sma_period=5)
        # SMA 5 기준 4개는 lookback 미만 → 시그널 없음
        bars = _make_daily_stream(_SYMBOL, _START, [100, 100, 100, 100])
        result = compute_golden_cross_baseline(
            InMemoryBarLoader(bars), cfg, _START, _START + timedelta(days=3)
        )
        assert result.trades == ()

    def test_cross_up_단일_진입_후_가상청산_1건(self):
        """SMA 아래 → 위 cross-up 후 스트림 종료 → 가상 청산으로 trades 1건."""
        # sma_period=3, close 시퀀스: [50, 50, 50] → sma=50, 이후 [100] → cross-up
        cfg = self._zero_cost_cfg(sma_period=3)
        closes = [50, 50, 50, 100]  # 4번째 bar: close=100 > sma=50 → 진입
        bars = _make_daily_stream(_SYMBOL, _START, closes)
        result = compute_golden_cross_baseline(
            InMemoryBarLoader(bars), cfg, _START, _START + timedelta(days=3)
        )
        assert len(result.trades) == 1

    def test_cross_up_후_cross_down_실청산_1건(self):
        """cross-up 진입 → cross-down 청산 → trades 1건 (실 청산, 가상 아님)."""
        cfg = self._zero_cost_cfg(sma_period=3)
        # [50,50,50] → sma=50, close=100 cross-up
        # 이후 [10] → sma=(50+50+100)/3=66.7, close=10 < sma
        closes = [50, 50, 50, 100, 10]
        bars = _make_daily_stream(_SYMBOL, _START, closes)
        result = compute_golden_cross_baseline(
            InMemoryBarLoader(bars), cfg, _START, _START + timedelta(days=4)
        )
        assert len(result.trades) == 1

    def test_cross_up_cross_down_cross_up_2사이클_trades_2건(self):
        """cross-up → cross-down → cross-up → 가상청산 = trades 2건."""
        cfg = self._zero_cost_cfg(sma_period=3)
        # sma_period=3이므로 3개 lookback 후 시그널 가능
        # [50,50,50] sma=50 → close=100 cross-up(1번째 진입)
        # → [10] sma≈53.3, close=10 cross-down(1번째 청산)
        # → [10,10] sma≈40 → close=100 cross-up(2번째 진입)
        # 스트림 종료 → 가상청산(2번째 청산)
        closes = [50, 50, 50, 100, 10, 10, 100]
        bars = _make_daily_stream(_SYMBOL, _START, closes)
        result = compute_golden_cross_baseline(
            InMemoryBarLoader(bars), cfg, _START, _START + timedelta(days=6)
        )
        assert len(result.trades) == 2

    def test_position_pct_0점5_notional_자본절반(self):
        """position_pct=0.5 → notional = cash * 0.5 검증 (qty 경유)."""
        cfg = self._zero_cost_cfg(
            starting_capital_krw=2_000_000,
            sma_period=3,
            position_pct=Decimal("0.5"),
        )
        # close=10000 cross-up (sma=[50,50,50]=50 → close=10000 확실히 초과)
        closes = [50, 50, 50, 10_000]
        bars = _make_daily_stream(_SYMBOL, _START, closes)
        result = compute_golden_cross_baseline(
            InMemoryBarLoader(bars), cfg, _START, _START + timedelta(days=3)
        )
        assert len(result.trades) == 1
        # qty = floor(2_000_000 * 0.5 / 10_000) = 100
        assert result.trades[0].qty == 100

    def test_starting_capital_부족_qty_zero_skip(self):
        """qty=0 이면 매수 skip → trades=()."""
        # close=3_000_000 → qty = floor(500_000 / 3_000_000) = 0 → skip
        cfg = self._zero_cost_cfg(
            starting_capital_krw=500_000,
            sma_period=3,
        )
        closes = [50, 50, 50, 3_000_000]
        bars = _make_daily_stream(_SYMBOL, _START, closes)
        result = compute_golden_cross_baseline(
            InMemoryBarLoader(bars), cfg, _START, _START + timedelta(days=3)
        )
        assert result.trades == ()

    def test_비타겟_심볼_분봉_무시(self):
        """비타겟 심볼 bar는 SMA 누적에 영향 없음 (loader가 target_symbol만 stream)."""
        cfg = self._zero_cost_cfg(sma_period=3)
        # 타겟 bar 3개 → cross-up
        target_bars = _make_daily_stream(_SYMBOL, _START, [50, 50, 50, 100])
        other_bar = _make_minute_bar(
            "005930",
            datetime.combine(_START, dtime(9, 0), tzinfo=KST),
            close=99999,
        )
        loader = InMemoryBarLoader(target_bars + [other_bar])
        result = compute_golden_cross_baseline(loader, cfg, _START, _START + timedelta(days=3))
        # 타겟 심볼 기준으로만 진입 1건
        assert len(result.trades) == 1


# ===========================================================================
# C. 비용 반영 검증
# ===========================================================================


class TestCostReflection:
    """슬리피지·수수료·거래세 비용 적용 정확성 검증."""

    def _make_entry_trade(
        self,
        *,
        close: int = 10_000,
        slip: str = "0.001",
        comm: str = "0.00015",
        tax: str = "0.0018",
    ) -> Any:
        """sma_period=3, cross-up 단 1건 → trades[0] 반환 헬퍼.

        position_pct=0.99 로 commission 여유를 확보해 잔액 부족 skip 회피.
        """
        cfg = GoldenCrossBaselineConfig(
            starting_capital_krw=10_000_000,
            sma_period=3,
            position_pct=Decimal("0.99"),
            slippage_rate=Decimal(slip),
            commission_rate=Decimal(comm),
            sell_tax_rate=Decimal(tax),
        )
        closes = [50, 50, 50, close]
        bars = _make_daily_stream(_SYMBOL, _START, closes)
        result = compute_golden_cross_baseline(
            InMemoryBarLoader(bars),
            cfg,
            _START,
            _START + timedelta(days=3),
        )
        assert len(result.trades) == 1, "헬퍼 사전조건 실패: trade 1건 기대"
        return result.trades[0]

    def test_entry_fill_슬리피지_반영(self):
        """entry_price = close × (1 + slippage_rate)."""
        close = 10_000
        slip = Decimal("0.001")
        trade = self._make_entry_trade(close=close, slip=str(slip))
        expected = Decimal(str(close)) * (1 + slip)
        assert trade.entry_price == pytest.approx(float(expected), rel=1e-9)

    def test_exit_fill_슬리피지_반영(self):
        """가상청산: exit_price = last_close × (1 - slippage_rate)."""
        close = 10_000
        slip = Decimal("0.001")
        trade = self._make_entry_trade(close=close, slip=str(slip))
        expected = Decimal(str(close)) * (1 - slip)
        assert trade.exit_price == pytest.approx(float(expected), rel=1e-9)

    def test_commission_krw_매수_매도_합산(self):
        """commission_krw = buy_comm + sell_comm (각각 notional * rate floor)."""
        close = 10_000
        comm = Decimal("0.00015")
        trade = self._make_entry_trade(close=close, slip="0", comm=str(comm))

        # position_pct=0.99 → qty = floor(10_000_000 * 0.99 / 10_000) = 990
        qty = int(Decimal("10000000") * Decimal("0.99") / Decimal(str(close)))
        entry_notional = Decimal(str(close)) * qty
        exit_notional = Decimal(str(close)) * qty  # slip=0
        expected_comm = int(entry_notional * comm) + int(exit_notional * comm)
        assert trade.commission_krw == expected_comm

    def test_tax_krw_매도_거래세(self):
        """tax_krw = floor(exit_notional * sell_tax_rate)."""
        close = 10_000
        tax_rate = Decimal("0.0018")
        trade = self._make_entry_trade(close=close, slip="0", tax=str(tax_rate))

        # position_pct=0.99 → qty = floor(10_000_000 * 0.99 / 10_000) = 990
        qty = int(Decimal("10000000") * Decimal("0.99") / Decimal(str(close)))
        exit_notional = Decimal(str(close)) * qty
        expected_tax = int(exit_notional * tax_rate)
        assert trade.tax_krw == expected_tax


# ===========================================================================
# D. DailyEquity 검증
# ===========================================================================


class TestDailyEquity:
    """세션별 mark-to-market daily_equity 기록 검증."""

    def _zero_cost_cfg(self, sma_period: int = 3) -> GoldenCrossBaselineConfig:
        return GoldenCrossBaselineConfig(
            starting_capital_krw=2_000_000,
            sma_period=sma_period,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )

    def test_세션_변경마다_daily_equity_1건씩(self):
        """3개 날짜 bar → daily_equity 3건."""
        cfg = self._zero_cost_cfg()
        closes = [50, 50, 50]  # cross-up 없음
        bars = _make_daily_stream(_SYMBOL, _START, closes)
        result = compute_golden_cross_baseline(
            InMemoryBarLoader(bars), cfg, _START, _START + timedelta(days=2)
        )
        assert len(result.daily_equity) == 3

    def test_lot_보유중_mark_to_market_cash_plus_qty_close(self):
        """lot 보유 중 세션 equity = cash + qty * last_close."""
        cfg = self._zero_cost_cfg(sma_period=3)
        # 4번째 bar: close=10_000 → cross-up 진입
        # 5번째 bar: close=12_000 (보유 중) — 이 날의 equity 검증
        closes = [50, 50, 50, 10_000, 12_000]
        bars = _make_daily_stream(_SYMBOL, _START, closes)
        result = compute_golden_cross_baseline(
            InMemoryBarLoader(bars), cfg, _START, _START + timedelta(days=4)
        )
        # 진입 후 마지막 daily_equity 는 mark-to-market
        # qty = floor(2_000_000 / 10_000) = 200
        # cost = 200 * 10_000 = 2_000_000
        # cash = 2_000_000 - 2_000_000 = 0
        # equity_last = 0 + 200 * 12_000 = 2_400_000
        last_equity = result.daily_equity[-1].equity_krw
        assert last_equity == 2_400_000

    def test_lot_없는_세션_equity_cash_그대로(self):
        """lot 없으면 equity = cash (변화 없음)."""
        cfg = self._zero_cost_cfg()
        # SMA 미확정 → 시그널 없음 → cash 변화 없음
        closes = [100, 100]
        bars = _make_daily_stream(_SYMBOL, _START, closes)
        result = compute_golden_cross_baseline(
            InMemoryBarLoader(bars), cfg, _START, _START + timedelta(days=1)
        )
        for eq in result.daily_equity:
            assert eq.equity_krw == 2_000_000


# ===========================================================================
# E. TradeRecord 필드 검증
# ===========================================================================


class TestTradeRecordFields:
    """TradeRecord 개별 필드 계약 검증."""

    def _single_trade(
        self,
        close: int = 10_000,
        slip: str = "0",
    ) -> Any:
        """sma_period=3, cross-up 1건 → trades[0]."""
        cfg = GoldenCrossBaselineConfig(
            starting_capital_krw=2_000_000,
            sma_period=3,
            slippage_rate=Decimal(slip),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        closes = [50, 50, 50, close]
        bars = _make_daily_stream(_SYMBOL, _START, closes)
        result = compute_golden_cross_baseline(
            InMemoryBarLoader(bars),
            cfg,
            _START,
            _START + timedelta(days=3),
        )
        assert len(result.trades) == 1
        return result.trades[0]

    def test_exit_reason_force_close(self):
        """가상청산 exit_reason = 'force_close'."""
        trade = self._single_trade()
        assert trade.exit_reason == "force_close"

    def test_entry_price_슬리피지_반영가(self):
        """entry_price = entry_fill = close × (1 + slippage)."""
        close = 10_000
        slip = "0.001"
        trade = self._single_trade(close=close, slip=slip)
        expected = Decimal(str(close)) * (1 + Decimal(slip))
        assert trade.entry_price == pytest.approx(float(expected), rel=1e-9)

    def test_net_pnl_krw_비용_차감_공식(self):
        """net_pnl_krw = gross - (buy_comm + sell_comm) - tax."""
        trade = self._single_trade(close=10_000)
        assert trade.net_pnl_krw == trade.gross_pnl_krw - trade.commission_krw - trade.tax_krw


# ===========================================================================
# F. BacktestResult 계약 검증
# ===========================================================================


class TestBacktestResultContract:
    """BacktestResult 구조·메타 필드 계약 검증."""

    def _run(self, bars: list[MinuteBar]) -> Any:
        cfg = GoldenCrossBaselineConfig(
            starting_capital_krw=2_000_000,
            sma_period=3,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        if bars:
            start_d = bars[0].bar_time.date()
            end_d = bars[-1].bar_time.date()
        else:
            start_d = _START
            end_d = _START
        return compute_golden_cross_baseline(
            InMemoryBarLoader(bars),
            cfg,
            start_d,
            end_d,
        )

    def test_rejected_counts_빈_dict(self):
        """RiskManager 미사용 → rejected_counts == {}."""
        result = self._run([])
        assert result.rejected_counts == {}

    def test_post_slippage_rejections_zero(self):
        """RiskManager 미사용 → post_slippage_rejections == 0."""
        result = self._run([])
        assert result.post_slippage_rejections == 0
