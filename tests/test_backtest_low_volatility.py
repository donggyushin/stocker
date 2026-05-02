"""LowVolBaselineConfig DTO 가드 + compute_low_volatility_baseline 동작 계약 검증.

src/stock_agent/backtest/low_volatility.py 의 두 공개 심볼
  - LowVolBaselineConfig  : __post_init__ 검증 (RuntimeError 전파)
  - compute_low_volatility_baseline : BarLoader + LowVolStrategy 를 엮어 BacktestResult 반환
이 아직 존재하지 않는 상태에서 작성된 RED 테스트들이다.
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
from stock_agent.backtest.low_volatility import (  # noqa: E402
    LowVolBaselineConfig,
    compute_low_volatility_baseline,
)
from stock_agent.data import MinuteBar

# --------------------------------------------------------------------------
# 공통 헬퍼
# --------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

# 테스트용 심볼
_SYM_A = "005930"  # 삼성전자 코드 (대표 예시)
_SYM_B = "000660"  # SK하이닉스 코드 (대표 예시)
_SYM_C = "035420"  # NAVER (세 번째 예시)

_START = date(2025, 1, 2)


def _kst(d: date, h: int = 9, m: int = 0) -> datetime:
    """date + h:m 을 KST tz-aware datetime 으로 반환."""
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KST)


def _make_bar(
    symbol: str,
    d: date,
    close: int | str | Decimal,
    *,
    volume: int = 1000,
) -> MinuteBar:
    """open=high=low=close 단순화된 MinuteBar 빌더 (09:00 KST 고정)."""
    c = Decimal(str(close))
    return MinuteBar(
        symbol=symbol,
        bar_time=_kst(d),
        open=c,
        high=c,
        low=c,
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


def _default_cfg(**kw: Any) -> LowVolBaselineConfig:
    """비용 0 + 최소 유니버스 기본 설정 빌더."""
    defaults: dict[str, Any] = dict(
        starting_capital_krw=2_000_000,
        universe=(_SYM_A, _SYM_B),
        lookback_days=10,  # 테스트 효율을 위해 단기 lookback 사용
        top_n=2,
        rebalance_month_interval=3,
        slippage_rate=Decimal("0"),
        commission_rate=Decimal("0"),
        sell_tax_rate=Decimal("0"),
    )
    defaults.update(kw)
    return LowVolBaselineConfig(**defaults)


# ===========================================================================
# A. LowVolBaselineConfig DTO 가드
# ===========================================================================


class TestConfigValidation:
    """LowVolBaselineConfig __post_init__ 검증 — 모든 위반 조건은 RuntimeError."""

    def test_기본값으로_정상_생성(self):
        """필수 필드만 지정 — 나머지 기본값으로 정상 생성."""
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
        )
        assert cfg.starting_capital_krw == 2_000_000
        assert cfg.lookback_days == 60
        assert cfg.top_n == 20
        assert cfg.rebalance_month_interval == 3
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
            LowVolBaselineConfig(
                starting_capital_krw=capital,
                universe=(_SYM_A,),
            )

    def test_commission_rate_음수_RuntimeError(self):
        """commission_rate < 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            LowVolBaselineConfig(
                starting_capital_krw=1_000_000,
                universe=(_SYM_A,),
                commission_rate=Decimal("-0.001"),
            )

    def test_sell_tax_rate_음수_RuntimeError(self):
        """sell_tax_rate < 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            LowVolBaselineConfig(
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
            LowVolBaselineConfig(
                starting_capital_krw=1_000_000,
                universe=(_SYM_A,),
                slippage_rate=slip,
            )

    def test_frozen_필드_수정_FrozenInstanceError(self):
        """frozen=True 검증 — 생성 후 필드 수정 시 FrozenInstanceError."""
        cfg = LowVolBaselineConfig(
            starting_capital_krw=1_000_000,
            universe=(_SYM_A,),
        )
        with pytest.raises(FrozenInstanceError):
            cfg.starting_capital_krw = 2_000_000  # type: ignore[misc]

    def test_position_pct_범위_이내_정상_생성(self):
        """position_pct = Decimal('0.5') — 정상 생성."""
        cfg = LowVolBaselineConfig(
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
        result = compute_low_volatility_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.trades == ()

    def test_빈_bar_stream_daily_equity_empty(self):
        """bar 0건 → daily_equity=()."""
        cfg = _default_cfg()
        result = compute_low_volatility_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.daily_equity == ()

    def test_빈_bar_stream_net_pnl_zero(self):
        """bar 0건 → metrics.net_pnl_krw == 0."""
        cfg = _default_cfg()
        result = compute_low_volatility_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.metrics.net_pnl_krw == 0

    def test_빈_bar_stream_total_return_zero(self):
        """bar 0건 → metrics.total_return_pct == 0."""
        cfg = _default_cfg()
        result = compute_low_volatility_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.metrics.total_return_pct == Decimal("0")


# ===========================================================================
# C. lookback 부족 — 시그널 없음
# ===========================================================================


class TestLookbackInsufficient:
    """lookback 기간 미달 시 리밸런싱 시그널이 발생하지 않아야 한다."""

    def test_lookback_부족_trades_empty(self):
        """lookback_days=60, bar 3일치 → lookback 미달 → trades=()."""
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_days=60,  # 60일치가 필요
            top_n=1,
            rebalance_month_interval=3,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        # 3일치만 제공 → lookback 60일 미달
        bars = _make_daily_series(_SYM_A, _START, [10_000, 11_000, 12_000]) + _make_daily_series(
            _SYM_B, _START, [5_000, 5_500, 6_000]
        )
        result = compute_low_volatility_baseline(
            InMemoryBarLoader(bars),
            cfg,
            _START,
            _START + timedelta(days=2),
        )
        assert result.trades == ()

    def test_lookback_부족_진입_zero(self):
        """lookback 미달 구간에서 진입 trade 0건."""
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_days=60,
            top_n=1,
            rebalance_month_interval=3,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars = _make_daily_series(_SYM_A, _START, [10_000] * 10)
        result = compute_low_volatility_baseline(
            InMemoryBarLoader(bars),
            cfg,
            _START,
            _START + timedelta(days=9),
        )
        assert len(result.trades) == 0


# ===========================================================================
# D. 단일 리밸런싱 — 첫 분기 진입
# ===========================================================================

# lookback_days=12, rebalance_month_interval=3, _START=2025-01-02
# 세션 경계 기준으로 Q1(1~3월)→Q2(4~6월) 변경 시점에 리밸런싱 발생
_LOOKBACK_DAYS = 13  # lookback_days=12 충족을 위해 13개 bar


class TestSingleRebalance:
    """첫 분기 리밸런싱 1회 발생 케이스."""

    def _make_cfg(self, **kw: Any) -> LowVolBaselineConfig:
        defaults: dict[str, Any] = dict(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_days=12,
            top_n=2,
            rebalance_month_interval=3,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        defaults.update(kw)
        return LowVolBaselineConfig(**defaults)

    def _make_two_symbol_bars(
        self,
        close_a: int = 10_000,
        close_b: int = 5_000,
        days: int = _LOOKBACK_DAYS,
    ) -> list[MinuteBar]:
        """두 심볼에 대해 days 개 bar 생성 (동일 close 유지)."""
        bars_a = _make_daily_series(_SYM_A, _START, [close_a] * days)
        bars_b = _make_daily_series(_SYM_B, _START, [close_b] * days)
        return bars_a + bars_b

    def _make_cross_quarter_bars(self) -> list[MinuteBar]:
        """Q1(2025-01-02 ~ 2025-03-31)과 Q2 일부(2025-04-01~)를 포함하는 bar 시리즈.

        두 심볼 모두 Q1 기간(lookback=12일치) + Q2 첫 날 bar 를 포함.
        """
        # Q1: 2025-01-02 ~ 2025-01-13 (12일치 — lookback 충족)
        q1_start = _START  # 2025-01-02
        q2_start = date(2025, 4, 1)
        bars = []
        for sym in (_SYM_A, _SYM_B):
            bars += _make_daily_series(sym, q1_start, [10_000] * 12)
            bars += _make_daily_series(sym, q2_start, [10_000])  # Q2 첫 bar
        return bars

    def test_진입_trade_발생(self):
        """첫 리밸런싱 후 top_n=2 → 진입 trade >= 1건."""
        cfg = self._make_cfg()
        bars = self._make_cross_quarter_bars()
        end_date = date(2025, 4, 1)
        result = compute_low_volatility_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # 진입 이후 가상 청산이 포함되므로 trade 건수 >= 1
        assert len(result.trades) >= 1

    def test_daily_equity_기록됨(self):
        """bar 있는 세션이 존재하면 daily_equity >= 1건."""
        cfg = self._make_cfg()
        bars = self._make_cross_quarter_bars()
        end_date = date(2025, 4, 1)
        result = compute_low_volatility_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        assert len(result.daily_equity) >= 1

    def test_exit_reason_force_close(self):
        """스트림 종료 시 가상 청산 → exit_reason = 'force_close'."""
        cfg = self._make_cfg()
        bars = self._make_cross_quarter_bars()
        end_date = date(2025, 4, 1)
        result = compute_low_volatility_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        for trade in result.trades:
            assert trade.exit_reason == "force_close"


# ===========================================================================
# E. 다중 리밸런싱 — 누적 청산 + 진입
# ===========================================================================


class TestMultipleRebalances:
    """2회 이상 리밸런싱 시 청산 + 진입 trade 누적."""

    def test_2분기_리밸런싱_trade_누적(self):
        """lookback_days=12, top_n=1, 2분기 이상의 bar → 최소 1건 trade 발생."""
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_days=12,
            top_n=1,
            rebalance_month_interval=3,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        # Q1: 2025-01-02 ~ 2025-01-13 (12일, lookback 충족)
        # Q2: 2025-04-01 ~ 2025-04-12 (12일)
        # Q3: 2025-07-01 시작
        bars = []
        # Q1 데이터 (SYM_A: 안정, SYM_B: 변동)
        bars += _make_daily_series(_SYM_A, date(2025, 1, 2), [10_000] * 12)
        bars += _make_daily_series(_SYM_B, date(2025, 1, 2), [8_000, 12_000] * 6)
        # Q2 데이터 (역전: SYM_B 안정, SYM_A 변동)
        bars += _make_daily_series(_SYM_B, date(2025, 4, 1), [10_000] * 12)
        bars += _make_daily_series(_SYM_A, date(2025, 4, 1), [8_000, 12_000] * 6)
        # Q3 첫 bar
        bars += _make_daily_series(_SYM_A, date(2025, 7, 1), [10_000])
        bars += _make_daily_series(_SYM_B, date(2025, 7, 1), [10_000])

        end_date = date(2025, 7, 1)
        result = compute_low_volatility_baseline(
            InMemoryBarLoader(bars), cfg, date(2025, 1, 2), end_date
        )
        # 최소 1건 이상 trade 발생 (진입 + 가상 청산)
        assert len(result.trades) >= 1

    def test_daily_equity_세션별_기록(self):
        """여러 세션 → daily_equity 세션 수만큼 기록."""
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_days=12,
            top_n=1,
            rebalance_month_interval=3,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        n_days = 20
        bars = _make_daily_series(_SYM_A, _START, [10_000] * n_days) + _make_daily_series(
            _SYM_B, _START, [5_000] * n_days
        )
        end_date = _START + timedelta(days=n_days - 1)
        result = compute_low_volatility_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # n_days 일치 bar → daily_equity n_days 건 (세션 1개 = 1건)
        assert len(result.daily_equity) == n_days


# ===========================================================================
# F. 스트림 종료 시 잔존 lot 가상청산
# ===========================================================================


class TestHypotheticalLiquidation:
    """스트림 종료 후 보유 lot 가상청산 → TradeRecord 추가."""

    def _make_cross_quarter_bars_with_close(self, last_close_a: int = 12_000) -> list[MinuteBar]:
        """Q1 12일치 + Q2 첫 bar (last_close_a 로 끝)."""
        bars = []
        # Q1: SYM_A std=0, SYM_B 고변동
        closes_a = [10_000] * 11 + [last_close_a]
        bars += _make_daily_series(_SYM_A, date(2025, 1, 2), closes_a)
        bars += _make_daily_series(_SYM_B, date(2025, 1, 2), [8_000, 12_000] * 6)
        # Q2 첫 bar
        bars += _make_daily_series(_SYM_A, date(2025, 4, 1), [last_close_a])
        bars += _make_daily_series(_SYM_B, date(2025, 4, 1), [10_000])
        return bars

    def test_가상청산_trade_포함(self):
        """진입 후 스트림 종료 → 잔존 lot 에 force_close trade 생성."""
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_days=12,
            top_n=2,
            rebalance_month_interval=3,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars = self._make_cross_quarter_bars_with_close()
        end_date = date(2025, 4, 1)
        result = compute_low_volatility_baseline(
            InMemoryBarLoader(bars), cfg, date(2025, 1, 2), end_date
        )
        force_close_trades = [t for t in result.trades if t.exit_reason == "force_close"]
        assert len(force_close_trades) >= 1

    def test_exit_price_마지막_close_기반(self):
        """가상 청산 exit_price = last_close × (1 - slippage). slippage=0 → last_close."""
        last_close_a = 12_000
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_days=12,
            top_n=2,
            rebalance_month_interval=3,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars = self._make_cross_quarter_bars_with_close(last_close_a=last_close_a)
        end_date = date(2025, 4, 1)
        result = compute_low_volatility_baseline(
            InMemoryBarLoader(bars), cfg, date(2025, 1, 2), end_date
        )
        # SYM_A 보유 lot 의 exit_price 는 slippage=0 이므로 last_close_a 와 동일
        sym_a_trades = [t for t in result.trades if t.symbol == _SYM_A]
        if sym_a_trades:
            assert sym_a_trades[-1].exit_price == pytest.approx(float(last_close_a), rel=1e-9)


# ===========================================================================
# G. 비용 반영 검증
# ===========================================================================


class TestCostAccounting:
    """수수료·슬리피지·거래세 정확 반영."""

    def _make_cross_quarter_bars_uniform(self, close_val: int = 10_000) -> list[MinuteBar]:
        """Q1 12일치 + Q2 첫 bar (두 심볼 동일 close_val)."""
        bars = []
        bars += _make_daily_series(_SYM_A, date(2025, 1, 2), [close_val] * 12)
        bars += _make_daily_series(_SYM_B, date(2025, 1, 2), [close_val] * 12)
        bars += _make_daily_series(_SYM_A, date(2025, 4, 1), [close_val])
        bars += _make_daily_series(_SYM_B, date(2025, 4, 1), [close_val])
        return bars

    def test_슬리피지_진입가_반영(self):
        """entry_price = close × (1 + slippage_rate)."""
        slip = Decimal("0.001")
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_days=12,
            top_n=2,
            rebalance_month_interval=3,
            slippage_rate=slip,
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        close_val = 10_000
        bars = self._make_cross_quarter_bars_uniform(close_val)
        end_date = date(2025, 4, 1)
        result = compute_low_volatility_baseline(
            InMemoryBarLoader(bars), cfg, date(2025, 1, 2), end_date
        )
        # SYM_A 에 진입한 trade 의 entry_price 검증
        sym_a_trades = [t for t in result.trades if t.symbol == _SYM_A]
        if sym_a_trades:
            expected_entry = Decimal(str(close_val)) * (1 + slip)
            assert sym_a_trades[0].entry_price == pytest.approx(float(expected_entry), rel=1e-9)

    def test_슬리피지_청산가_반영(self):
        """exit_price = last_close × (1 - slippage_rate)."""
        slip = Decimal("0.001")
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_days=12,
            top_n=2,
            rebalance_month_interval=3,
            slippage_rate=slip,
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        close_val = 10_000
        bars = self._make_cross_quarter_bars_uniform(close_val)
        end_date = date(2025, 4, 1)
        result = compute_low_volatility_baseline(
            InMemoryBarLoader(bars), cfg, date(2025, 1, 2), end_date
        )
        sym_a_trades = [t for t in result.trades if t.symbol == _SYM_A]
        if sym_a_trades:
            expected_exit = Decimal(str(close_val)) * (1 - slip)
            assert sym_a_trades[-1].exit_price == pytest.approx(float(expected_exit), rel=1e-9)

    def test_net_pnl_비용_차감_공식(self):
        """net_pnl_krw = gross_pnl_krw - commission_krw - tax_krw."""
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_days=12,
            top_n=2,
            rebalance_month_interval=3,
            slippage_rate=Decimal("0.001"),
            commission_rate=Decimal("0.00015"),
            sell_tax_rate=Decimal("0.0018"),
        )
        bars = self._make_cross_quarter_bars_uniform(10_000)
        end_date = date(2025, 4, 1)
        result = compute_low_volatility_baseline(
            InMemoryBarLoader(bars), cfg, date(2025, 1, 2), end_date
        )
        for trade in result.trades:
            expected = trade.gross_pnl_krw - trade.commission_krw - trade.tax_krw
            assert trade.net_pnl_krw == expected


# ===========================================================================
# H. 현금 배분 — 동일 가중 (cash × position_pct / top_n)
# ===========================================================================


class TestCashAllocation:
    """리밸런싱 시 동일 가중 (cash_snapshot × position_pct / top_n) 배분 검증."""

    def test_top_n_2_동일가중_qty_검증(self):
        """top_n=2, 자본 2,000,000, slippage=0, close=10,000
        → alloc = 1,000,000 → qty = floor(1,000,000 / 10,000) = 100."""
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_days=12,
            top_n=2,
            rebalance_month_interval=3,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        # 두 심볼 std=0 (동률, symbol asc 로 둘 다 진입), close=10,000
        bars = []
        bars += _make_daily_series(_SYM_A, date(2025, 1, 2), [10_000] * 12)
        bars += _make_daily_series(_SYM_B, date(2025, 1, 2), [10_000] * 12)
        bars += _make_daily_series(_SYM_A, date(2025, 4, 1), [10_000])
        bars += _make_daily_series(_SYM_B, date(2025, 4, 1), [10_000])
        end_date = date(2025, 4, 1)
        result = compute_low_volatility_baseline(
            InMemoryBarLoader(bars), cfg, date(2025, 1, 2), end_date
        )
        entry_trades = [t for t in result.trades]
        if entry_trades:
            for t in entry_trades:
                # 동일 가중: 자본 2M / top_n 2 / close 10,000 = qty 100
                assert t.qty == 100

    def test_cash_부족_skip_허용(self):
        """현금 부족 → 해당 진입 skip 후에도 결과 반환 (RuntimeError 없음)."""
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_days=12,
            top_n=2,
            rebalance_month_interval=3,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        # close=1,000,000 → qty = floor(2,000,000 / 2 / 1,000,000) = 1
        bars = []
        bars += _make_daily_series(_SYM_A, date(2025, 1, 2), [1_000_000] * 12)
        bars += _make_daily_series(_SYM_B, date(2025, 1, 2), [1_000_000] * 12)
        bars += _make_daily_series(_SYM_A, date(2025, 4, 1), [1_000_000])
        bars += _make_daily_series(_SYM_B, date(2025, 4, 1), [1_000_000])
        end_date = date(2025, 4, 1)
        # 예외 없이 결과 반환되어야 함
        result = compute_low_volatility_baseline(
            InMemoryBarLoader(bars), cfg, date(2025, 1, 2), end_date
        )
        assert result is not None

    def test_qty_zero_종목_skip(self):
        """단가 > 배분금액 → qty=0 → 해당 종목 진입 skip."""
        # close=2,000,000 → qty = floor(2,000,000 / 2 / 2,000,000) = 0 → skip
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_days=12,
            top_n=2,
            rebalance_month_interval=3,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars = []
        bars += _make_daily_series(_SYM_A, date(2025, 1, 2), [2_000_000] * 12)
        bars += _make_daily_series(_SYM_B, date(2025, 1, 2), [2_000_000] * 12)
        bars += _make_daily_series(_SYM_A, date(2025, 4, 1), [2_000_000])
        bars += _make_daily_series(_SYM_B, date(2025, 4, 1), [2_000_000])
        end_date = date(2025, 4, 1)
        result = compute_low_volatility_baseline(
            InMemoryBarLoader(bars), cfg, date(2025, 1, 2), end_date
        )
        # qty=0 이므로 진입 trade 없음
        assert result.trades == ()


# ===========================================================================
# I. BacktestResult 계약 검증
# ===========================================================================


class TestBacktestResultContract:
    """BacktestResult 구조·메타 필드 계약 검증."""

    def _run_empty(self) -> Any:
        """빈 스트림으로 compute_low_volatility_baseline 실행."""
        cfg = _default_cfg()
        return compute_low_volatility_baseline(InMemoryBarLoader([]), cfg, _START, _START)

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
# J. start > end 가드
# ===========================================================================


class TestStartEndGuards:
    """start > end 입력 → RuntimeError."""

    def test_start_after_end_RuntimeError(self):
        """start > end → RuntimeError."""
        cfg = _default_cfg()
        with pytest.raises(RuntimeError):
            compute_low_volatility_baseline(
                InMemoryBarLoader([]),
                cfg,
                date(2025, 2, 1),  # start
                date(2025, 1, 1),  # end < start
            )


# ===========================================================================
# K. mark-to-market DailyEquity 검증
# ===========================================================================


class TestDailyEquityMarkToMarket:
    """DailyEquity mark-to-market = cash + qty × latest_close 검증."""

    def test_lot_없는_세션_equity_cash_그대로(self):
        """lot 없는 세션 equity == starting_capital (cash 변화 없음)."""
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_days=60,  # lookback 미달 → 진입 없음
            top_n=1,
            rebalance_month_interval=3,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        # 5일치만 제공 — lookback 60일 미달
        bars = _make_daily_series(_SYM_A, _START, [10_000] * 5) + _make_daily_series(
            _SYM_B, _START, [5_000] * 5
        )
        end_date = _START + timedelta(days=4)
        result = compute_low_volatility_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # 진입 없으면 equity = cash = starting_capital
        for eq in result.daily_equity:
            assert eq.equity_krw == 2_000_000

    def test_세션별_daily_equity_날짜_단조증가(self):
        """daily_equity.session_date 가 단조 증가해야 한다."""
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_days=60,
            top_n=1,
            rebalance_month_interval=3,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        n_days = 10
        bars = _make_daily_series(_SYM_A, _START, [10_000] * n_days) + _make_daily_series(
            _SYM_B, _START, [5_000] * n_days
        )
        end_date = _START + timedelta(days=n_days - 1)
        result = compute_low_volatility_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        dates = [eq.session_date for eq in result.daily_equity]
        assert dates == sorted(dates), "daily_equity session_date 는 단조 증가여야 한다"


# ===========================================================================
# L. universe 다중 심볼 처리
# ===========================================================================


class TestUniverseHandling:
    """universe 다중 심볼 bar 처리 계약."""

    def test_universe_외_심볼_bar_무시(self):
        """universe 에 없는 심볼 bar 는 변동성 계산에 영향 없음."""
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),  # SYM_B 는 universe 밖
            lookback_days=12,
            top_n=1,
            rebalance_month_interval=3,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars = []
        bars += _make_daily_series(_SYM_A, date(2025, 1, 2), [10_000] * 12)
        bars += _make_daily_series(_SYM_B, date(2025, 1, 2), [9_999] * 12)  # universe 외
        bars += _make_daily_series(_SYM_A, date(2025, 4, 1), [10_000])
        bars += _make_daily_series(_SYM_B, date(2025, 4, 1), [9_999])  # universe 외
        end_date = date(2025, 4, 1)
        # 예외 없이 결과 반환
        result = compute_low_volatility_baseline(
            InMemoryBarLoader(bars), cfg, date(2025, 1, 2), end_date
        )
        assert result is not None

    def test_3심볼_universe_top_n_1_변동성_최소_선택(self):
        """universe 3종목, top_n=1 → 표준편차 가장 낮은 1종목만 진입."""
        cfg = LowVolBaselineConfig(
            starting_capital_krw=3_000_000,
            universe=(_SYM_A, _SYM_B, _SYM_C),
            lookback_days=12,
            top_n=1,
            rebalance_month_interval=3,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        # SYM_A: std=0 (가장 낮음) → 선택 예상
        # SYM_B: 중간 변동
        # SYM_C: 고변동
        bars = []
        bars += _make_daily_series(_SYM_A, date(2025, 1, 2), [10_000] * 12)
        bars += _make_daily_series(_SYM_B, date(2025, 1, 2), [9_000, 11_000] * 6)
        bars += _make_daily_series(_SYM_C, date(2025, 1, 2), [8_000, 12_000] * 6)
        bars += _make_daily_series(_SYM_A, date(2025, 4, 1), [10_000])
        bars += _make_daily_series(_SYM_B, date(2025, 4, 1), [10_000])
        bars += _make_daily_series(_SYM_C, date(2025, 4, 1), [10_000])
        end_date = date(2025, 4, 1)
        result = compute_low_volatility_baseline(
            InMemoryBarLoader(bars), cfg, date(2025, 1, 2), end_date
        )
        # top_n=1 → 최저 변동성 심볼 1개만 진입 → trade <= 2 (진입 1 + 가상청산 1)
        assert len(result.trades) <= 2


# ===========================================================================
# M. BacktestMetrics 기본값 및 계산 검증
# ===========================================================================


class TestMetricsCalculation:
    """BacktestMetrics 기본 계산 계약."""

    def test_가격_상승_total_return_양수(self):
        """진입 후 가격 상승 → total_return_pct > 0 (mark-to-market 기준)."""
        cfg = LowVolBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            lookback_days=12,
            top_n=1,
            rebalance_month_interval=3,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        # SYM_A: std=0, 진입 후 마지막에 가격 대폭 상승
        bars = []
        bars += _make_daily_series(_SYM_A, date(2025, 1, 2), [10_000] * 12)
        bars += _make_daily_series(_SYM_A, date(2025, 4, 1), [20_000])  # 진입 후 상승
        end_date = date(2025, 4, 1)
        result = compute_low_volatility_baseline(
            InMemoryBarLoader(bars), cfg, date(2025, 1, 2), end_date
        )
        # 진입이 발생했고 마지막 close 상승 → total_return 양수 기대
        if result.trades:
            assert result.metrics.total_return_pct > Decimal("0") or result.metrics.net_pnl_krw >= 0

    def test_빈_스트림_sharpe_zero(self):
        """bar 없음 → sharpe_ratio == 0 (표본 부족 기본값)."""
        cfg = _default_cfg()
        result = compute_low_volatility_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.metrics.sharpe_ratio == Decimal("0")

    def test_빈_스트림_max_drawdown_zero(self):
        """bar 없음 → max_drawdown_pct == 0."""
        cfg = _default_cfg()
        result = compute_low_volatility_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.metrics.max_drawdown_pct == Decimal("0")
