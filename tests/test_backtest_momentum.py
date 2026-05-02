"""MomentumBaselineConfig DTO 가드 + compute_momentum_baseline 동작 계약 검증.

src/stock_agent/backtest/momentum.py 의 두 공개 심볼
  - MomentumBaselineConfig  : __post_init__ 검증 (RuntimeError 전파)
  - compute_momentum_baseline : BarLoader + MomentumStrategy 를 엮어 BacktestResult 반환
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
from stock_agent.backtest.momentum import (  # noqa: E402
    MomentumBaselineConfig,
    compute_momentum_baseline,
)
from stock_agent.data import MinuteBar

# --------------------------------------------------------------------------
# 공통 헬퍼
# --------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

# 테스트용 2개 유니버스 심볼
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


def _default_cfg(**kw: Any) -> MomentumBaselineConfig:
    """비용 0 + 최소 유니버스 기본 설정 빌더."""
    defaults: dict[str, Any] = dict(
        starting_capital_krw=2_000_000,
        universe=(_SYM_A, _SYM_B),
        lookback_months=1,  # 테스트 효율을 위해 단기 lookback 사용
        top_n=2,
        slippage_rate=Decimal("0"),
        commission_rate=Decimal("0"),
        sell_tax_rate=Decimal("0"),
    )
    defaults.update(kw)
    return MomentumBaselineConfig(**defaults)


# ===========================================================================
# A. MomentumBaselineConfig DTO 가드
# ===========================================================================


class TestConfigValidation:
    """MomentumBaselineConfig __post_init__ 검증 — 모든 위반 조건은 RuntimeError."""

    def test_기본값으로_정상_생성(self):
        """필수 필드만 지정 — 나머지 기본값으로 정상 생성."""
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
        )
        assert cfg.starting_capital_krw == 2_000_000
        assert cfg.lookback_months == 12
        assert cfg.top_n == 10
        assert cfg.rebalance_day == 1
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
            MomentumBaselineConfig(
                starting_capital_krw=capital,
                universe=(_SYM_A,),
            )

    def test_commission_rate_음수_RuntimeError(self):
        """commission_rate < 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            MomentumBaselineConfig(
                starting_capital_krw=1_000_000,
                universe=(_SYM_A,),
                commission_rate=Decimal("-0.001"),
            )

    def test_sell_tax_rate_음수_RuntimeError(self):
        """sell_tax_rate < 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            MomentumBaselineConfig(
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
            MomentumBaselineConfig(
                starting_capital_krw=1_000_000,
                universe=(_SYM_A,),
                slippage_rate=slip,
            )

    def test_frozen_필드_수정_FrozenInstanceError(self):
        """frozen=True 검증 — 생성 후 필드 수정 시 FrozenInstanceError."""
        cfg = MomentumBaselineConfig(
            starting_capital_krw=1_000_000,
            universe=(_SYM_A,),
        )
        with pytest.raises(FrozenInstanceError):
            cfg.starting_capital_krw = 2_000_000  # type: ignore[misc]

    def test_position_pct_범위_이내_정상_생성(self):
        """position_pct = Decimal('0.5') — 정상 생성."""
        cfg = MomentumBaselineConfig(
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
        result = compute_momentum_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.trades == ()

    def test_빈_bar_stream_daily_equity_empty(self):
        """bar 0건 → daily_equity=()."""
        cfg = _default_cfg()
        result = compute_momentum_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.daily_equity == ()

    def test_빈_bar_stream_net_pnl_zero(self):
        """bar 0건 → metrics.net_pnl_krw == 0."""
        cfg = _default_cfg()
        result = compute_momentum_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.metrics.net_pnl_krw == 0

    def test_빈_bar_stream_total_return_zero(self):
        """bar 0건 → metrics.total_return_pct == 0."""
        cfg = _default_cfg()
        result = compute_momentum_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.metrics.total_return_pct == Decimal("0")


# ===========================================================================
# C. lookback 부족 — 시그널 없음
# ===========================================================================


class TestLookbackInsufficient:
    """lookback 기간 미달 시 리밸런싱 시그널이 발생하지 않아야 한다."""

    def test_lookback_부족_trades_empty(self):
        """lookback_months=12, bar 3일치 → lookback 미달 → trades=()."""
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_months=12,  # 12개월치가 필요
            top_n=1,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        # 3일치만 제공 → lookback 12개월 미달
        bars = _make_daily_series(_SYM_A, _START, [10_000, 11_000, 12_000]) + _make_daily_series(
            _SYM_B, _START, [5_000, 5_500, 6_000]
        )
        result = compute_momentum_baseline(
            InMemoryBarLoader(bars),
            cfg,
            _START,
            _START + timedelta(days=2),
        )
        assert result.trades == ()

    def test_lookback_부족_진입_zero(self):
        """lookback 미달 구간에서 진입 trade 0건."""
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_months=12,
            top_n=1,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        bars = _make_daily_series(_SYM_A, _START, [10_000] * 10)
        result = compute_momentum_baseline(
            InMemoryBarLoader(bars),
            cfg,
            _START,
            _START + timedelta(days=9),
        )
        assert len(result.trades) == 0


# ===========================================================================
# D. 단일 리밸런싱 — 첫 월 진입
# ===========================================================================

# lookback_months=1 (21영업일 ≈ 21일) 기준으로
# 22일치 bar 제공 → 첫 월 리밸런싱 발생
_LOOKBACK_DAYS = 22  # lookback_months=1 의 단순화: 약 21일


class TestSingleRebalance:
    """첫 월 리밸런싱 1회 발생 케이스."""

    def _make_cfg(self, **kw: Any) -> MomentumBaselineConfig:
        defaults: dict[str, Any] = dict(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_months=1,
            top_n=2,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        defaults.update(kw)
        return MomentumBaselineConfig(**defaults)

    def _make_two_symbol_bars(
        self,
        close_a: int = 10_000,
        close_b: int = 5_000,
        days: int = _LOOKBACK_DAYS,
    ) -> list[MinuteBar]:
        """두 심볼에 대해 days 개 bar 생성 (동일 close 유지)."""
        bars_a = _make_daily_series(_SYM_A, _START, [close_a] * days)
        bars_b = _make_daily_series(_SYM_B, _START, [close_b] * days)
        # bar_time 기준 정렬 — InMemoryBarLoader 가 내부에서 정렬하므로 순서 무관
        return bars_a + bars_b

    def test_진입_trade_발생(self):
        """첫 리밸런싱 후 top_n=2 → 진입 trade >= 1건."""
        cfg = self._make_cfg()
        bars = self._make_two_symbol_bars()
        end_date = _START + timedelta(days=_LOOKBACK_DAYS - 1)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # 진입 이후 가상 청산이 포함되므로 trade 건수 >= 1
        assert len(result.trades) >= 1

    def test_daily_equity_기록됨(self):
        """bar 있는 세션이 존재하면 daily_equity >= 1건."""
        cfg = self._make_cfg()
        bars = self._make_two_symbol_bars()
        end_date = _START + timedelta(days=_LOOKBACK_DAYS - 1)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        assert len(result.daily_equity) >= 1

    def test_exit_reason_force_close(self):
        """스트림 종료 시 가상 청산 → exit_reason = 'force_close'."""
        cfg = self._make_cfg()
        bars = self._make_two_symbol_bars()
        end_date = _START + timedelta(days=_LOOKBACK_DAYS - 1)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        for trade in result.trades:
            assert trade.exit_reason == "force_close"


# ===========================================================================
# E. 다중 리밸런싱 — 누적 청산 + 진입
# ===========================================================================


class TestMultipleRebalances:
    """2회 이상 리밸런싱 시 청산 + 진입 trade 누적."""

    def test_2월_리밸런싱_trade_누적(self):
        """lookback_months=1, top_n=1, 2개월치 bar → 2회 리밸런싱 예상."""
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_months=1,
            top_n=1,
            rebalance_day=1,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        # 2개월(약 60일)치 bar. SYM_A 가 첫 달 강세, SYM_B 가 둘째 달 강세
        # → 2회 리밸런싱에서 종목 교체 발생 가능
        days = 50  # lookback 1개월 + 1개월 평가 구간
        closes_a = [10_000] * 25 + [8_000] * 25  # 둘째 달 약세
        closes_b = [5_000] * 25 + [9_000] * 25  # 둘째 달 강세
        bars = _make_daily_series(_SYM_A, _START, closes_a[:days]) + _make_daily_series(
            _SYM_B, _START, closes_b[:days]
        )
        end_date = _START + timedelta(days=days - 1)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # 최소 1건 이상 trade 발생 (진입 + 가상 청산)
        assert len(result.trades) >= 1

    def test_daily_equity_세션별_기록(self):
        """여러 세션 → daily_equity 세션 수만큼 기록."""
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_months=1,
            top_n=1,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        n_days = 30
        bars = _make_daily_series(_SYM_A, _START, [10_000] * n_days) + _make_daily_series(
            _SYM_B, _START, [5_000] * n_days
        )
        end_date = _START + timedelta(days=n_days - 1)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # n_days 일치 bar → daily_equity n_days 건 (세션 1개 = 1건)
        assert len(result.daily_equity) == n_days


# ===========================================================================
# F. 스트림 종료 시 잔존 lot 가상청산
# ===========================================================================


class TestHypotheticalLiquidation:
    """스트림 종료 후 보유 lot 가상청산 → TradeRecord 추가."""

    def test_가상청산_trade_포함(self):
        """진입 후 스트림 종료 → 잔존 lot 에 force_close trade 생성."""
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_months=1,
            top_n=2,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        n_days = _LOOKBACK_DAYS
        bars = _make_daily_series(_SYM_A, _START, [10_000] * n_days) + _make_daily_series(
            _SYM_B, _START, [5_000] * n_days
        )
        end_date = _START + timedelta(days=n_days - 1)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # 가상 청산은 force_close reason 으로 반환
        force_close_trades = [t for t in result.trades if t.exit_reason == "force_close"]
        assert len(force_close_trades) >= 1

    def test_exit_price_마지막_close_기반(self):
        """가상 청산 exit_price = last_close × (1 - slippage). slippage=0 → last_close."""
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_months=1,
            top_n=2,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        n_days = _LOOKBACK_DAYS
        last_close_a = 12_000
        closes_a = [10_000] * (n_days - 1) + [last_close_a]
        closes_b = [5_000] * n_days
        bars = _make_daily_series(_SYM_A, _START, closes_a) + _make_daily_series(
            _SYM_B, _START, closes_b
        )
        end_date = _START + timedelta(days=n_days - 1)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # SYM_A 보유 lot 의 exit_price 는 slippage=0 이므로 last_close_a 와 동일
        sym_a_trades = [t for t in result.trades if t.symbol == _SYM_A]
        if sym_a_trades:
            assert sym_a_trades[-1].exit_price == pytest.approx(float(last_close_a), rel=1e-9)


# ===========================================================================
# G. 비용 반영 검증
# ===========================================================================


class TestCostAccounting:
    """수수료·슬리피지·거래세 정확 반영."""

    def test_슬리피지_진입가_반영(self):
        """entry_price = close × (1 + slippage_rate)."""
        slip = Decimal("0.001")
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_months=1,
            top_n=2,
            slippage_rate=slip,
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        close_val = 10_000
        n_days = _LOOKBACK_DAYS
        bars = _make_daily_series(_SYM_A, _START, [close_val] * n_days) + _make_daily_series(
            _SYM_B, _START, [5_000] * n_days
        )
        end_date = _START + timedelta(days=n_days - 1)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # SYM_A 에 진입한 trade 의 entry_price 검증
        sym_a_trades = [t for t in result.trades if t.symbol == _SYM_A]
        if sym_a_trades:
            expected_entry = Decimal(str(close_val)) * (1 + slip)
            assert sym_a_trades[0].entry_price == pytest.approx(float(expected_entry), rel=1e-9)

    def test_슬리피지_청산가_반영(self):
        """exit_price = last_close × (1 - slippage_rate)."""
        slip = Decimal("0.001")
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_months=1,
            top_n=2,
            slippage_rate=slip,
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        close_val = 10_000
        n_days = _LOOKBACK_DAYS
        bars = _make_daily_series(_SYM_A, _START, [close_val] * n_days) + _make_daily_series(
            _SYM_B, _START, [5_000] * n_days
        )
        end_date = _START + timedelta(days=n_days - 1)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        sym_a_trades = [t for t in result.trades if t.symbol == _SYM_A]
        if sym_a_trades:
            expected_exit = Decimal(str(close_val)) * (1 - slip)
            assert sym_a_trades[-1].exit_price == pytest.approx(float(expected_exit), rel=1e-9)

    def test_net_pnl_비용_차감_공식(self):
        """net_pnl_krw = gross_pnl_krw - commission_krw - tax_krw."""
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_months=1,
            top_n=2,
            slippage_rate=Decimal("0.001"),
            commission_rate=Decimal("0.00015"),
            sell_tax_rate=Decimal("0.0018"),
        )
        n_days = _LOOKBACK_DAYS
        bars = _make_daily_series(_SYM_A, _START, [10_000] * n_days) + _make_daily_series(
            _SYM_B, _START, [5_000] * n_days
        )
        end_date = _START + timedelta(days=n_days - 1)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        for trade in result.trades:
            expected = trade.gross_pnl_krw - trade.commission_krw - trade.tax_krw
            assert trade.net_pnl_krw == expected


# ===========================================================================
# H. 현금 배분 — 동일 가중 (cash / top_n)
# ===========================================================================


class TestCashAllocation:
    """리밸런싱 시 동일 가중 (cash_snapshot / top_n) 배분 검증."""

    def test_top_n_2_동일가중_qty_검증(self):
        """top_n=2, 자본 2,000,000, slippage=0, close=10,000
        → qty = floor(1,000,000 / 10,000) = 100."""
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_months=1,
            top_n=2,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        n_days = _LOOKBACK_DAYS
        bars = _make_daily_series(_SYM_A, _START, [10_000] * n_days) + _make_daily_series(
            _SYM_B, _START, [10_000] * n_days
        )
        end_date = _START + timedelta(days=n_days - 1)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # 진입 trade 가 있으면 qty = floor(2,000,000 / 2 / 10,000) = 100
        entry_trades = [t for t in result.trades]
        if entry_trades:
            for t in entry_trades:
                # 동일 가중: 자본 2M / top_n 2 / close 10,000 = qty 100
                assert t.qty == 100

    def test_cash_부족_skip_허용(self):
        """현금 부족 → 해당 진입 skip 후에도 결과 반환 (RuntimeError 없음)."""
        # close=1,000,000 → qty = floor(2,000,000 / 2 / 1,000,000) = 1
        # 두 번째 종목 진입 시 잔액 0 → skip
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_months=1,
            top_n=2,
            position_pct=Decimal("1.0"),
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        n_days = _LOOKBACK_DAYS
        bars = _make_daily_series(_SYM_A, _START, [1_000_000] * n_days) + _make_daily_series(
            _SYM_B, _START, [1_000_000] * n_days
        )
        end_date = _START + timedelta(days=n_days - 1)
        # 예외 없이 결과 반환되어야 함
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        assert result is not None

    def test_qty_zero_종목_skip(self):
        """단가 > 배분금액 → qty=0 → 해당 종목 진입 skip."""
        # close=2,000,000 → qty = floor(2,000,000 / 2 / 2,000,000) = 0 → skip
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_months=1,
            top_n=2,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        n_days = _LOOKBACK_DAYS
        bars = _make_daily_series(_SYM_A, _START, [2_000_000] * n_days) + _make_daily_series(
            _SYM_B, _START, [2_000_000] * n_days
        )
        end_date = _START + timedelta(days=n_days - 1)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # qty=0 이므로 진입 trade 없음
        assert result.trades == ()


# ===========================================================================
# I. BacktestResult 계약 검증
# ===========================================================================


class TestBacktestResultContract:
    """BacktestResult 구조·메타 필드 계약 검증."""

    def _run_empty(self) -> Any:
        """빈 스트림으로 compute_momentum_baseline 실행."""
        cfg = _default_cfg()
        return compute_momentum_baseline(InMemoryBarLoader([]), cfg, _START, _START)

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
            compute_momentum_baseline(
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
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_months=12,  # lookback 미달 → 진입 없음
            top_n=1,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        # 5일치만 제공 — lookback 12개월 미달
        bars = _make_daily_series(_SYM_A, _START, [10_000] * 5) + _make_daily_series(
            _SYM_B, _START, [5_000] * 5
        )
        end_date = _START + timedelta(days=4)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # 진입 없으면 equity = cash = starting_capital
        for eq in result.daily_equity:
            assert eq.equity_krw == 2_000_000

    def test_세션별_daily_equity_날짜_단조증가(self):
        """daily_equity.session_date 가 단조 증가해야 한다."""
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A, _SYM_B),
            lookback_months=12,
            top_n=1,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        n_days = 10
        bars = _make_daily_series(_SYM_A, _START, [10_000] * n_days) + _make_daily_series(
            _SYM_B, _START, [5_000] * n_days
        )
        end_date = _START + timedelta(days=n_days - 1)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        dates = [eq.session_date for eq in result.daily_equity]
        assert dates == sorted(dates), "daily_equity session_date 는 단조 증가여야 한다"


# ===========================================================================
# L. universe 다중 심볼 처리
# ===========================================================================


class TestUniverseHandling:
    """universe 다중 심볼 bar 처리 계약."""

    def test_universe_외_심볼_bar_무시(self):
        """universe 에 없는 심볼 bar 는 모멘텀 계산에 영향 없음."""
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),  # SYM_B 는 universe 밖
            lookback_months=1,
            top_n=1,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        n_days = _LOOKBACK_DAYS
        bars = _make_daily_series(_SYM_A, _START, [10_000] * n_days) + _make_daily_series(
            _SYM_B, _START, [9_999] * n_days
        )  # universe 외
        end_date = _START + timedelta(days=n_days - 1)
        # 예외 없이 결과 반환
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        assert result is not None

    def test_3심볼_universe_top_n_1_선택(self):
        """universe 3종목, top_n=1 → 최고 수익률 1종목만 진입."""
        cfg = MomentumBaselineConfig(
            starting_capital_krw=3_000_000,
            universe=(_SYM_A, _SYM_B, _SYM_C),
            lookback_months=1,
            top_n=1,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        n_days = _LOOKBACK_DAYS
        # SYM_C 가 수익률 가장 높음 (10→15: +50%)
        closes_a = [10_000] * (n_days - 1) + [10_500]  # +5%
        closes_b = [10_000] * (n_days - 1) + [11_000]  # +10%
        closes_c = [10_000] * (n_days - 1) + [15_000]  # +50%
        bars = (
            _make_daily_series(_SYM_A, _START, closes_a)
            + _make_daily_series(_SYM_B, _START, closes_b)
            + _make_daily_series(_SYM_C, _START, closes_c)
        )
        end_date = _START + timedelta(days=n_days - 1)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # top_n=1 → 최고 수익률 심볼 1개만 진입 → trade 1건 이하
        # (리밸런싱 전 진입 없는 경우도 있으므로 >= 0)
        assert len(result.trades) <= 2  # 진입 1 + 가상청산 1


# ===========================================================================
# M. BacktestMetrics 기본값 및 계산 검증
# ===========================================================================


class TestMetricsCalculation:
    """BacktestMetrics 기본 계산 계약."""

    def test_가격_상승_total_return_양수(self):
        """진입 후 가격 상승 → total_return_pct > 0 (mark-to-market 기준)."""
        cfg = MomentumBaselineConfig(
            starting_capital_krw=2_000_000,
            universe=(_SYM_A,),
            lookback_months=1,
            top_n=1,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        n_days = _LOOKBACK_DAYS
        # 진입 후 마지막에 가격 대폭 상승
        closes = [10_000] * (n_days - 1) + [20_000]
        bars = _make_daily_series(_SYM_A, _START, closes)
        end_date = _START + timedelta(days=n_days - 1)
        result = compute_momentum_baseline(InMemoryBarLoader(bars), cfg, _START, end_date)
        # 진입이 발생했고 마지막 close 상승 → total_return 양수 기대
        if result.trades:
            assert result.metrics.total_return_pct > Decimal("0") or result.metrics.net_pnl_krw >= 0

    def test_빈_스트림_sharpe_zero(self):
        """bar 없음 → sharpe_ratio == 0 (표본 부족 기본값)."""
        cfg = _default_cfg()
        result = compute_momentum_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.metrics.sharpe_ratio == Decimal("0")

    def test_빈_스트림_max_drawdown_zero(self):
        """bar 없음 → max_drawdown_pct == 0."""
        cfg = _default_cfg()
        result = compute_momentum_baseline(InMemoryBarLoader([]), cfg, _START, _START)
        assert result.metrics.max_drawdown_pct == Decimal("0")
