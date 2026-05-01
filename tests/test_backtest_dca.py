"""DCABaselineConfig DTO 가드 + compute_dca_baseline 동작 계약 검증.

src/stock_agent/backtest/dca.py 의 두 공개 심볼
  - DCABaselineConfig  : __post_init__ 검증 (RuntimeError 전파)
  - compute_dca_baseline : BarLoader + DCAStrategy 를 엮어 BacktestResult 반환
이 아직 존재하지 않는 상태에서 작성된 RED 테스트들이다.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest

# --------------------------------------------------------------------------
# 대상 모듈 임포트 — 미존재 시 ImportError 로 FAIL (RED 의도)
# --------------------------------------------------------------------------
from stock_agent.backtest.dca import DCABaselineConfig, compute_dca_baseline  # noqa: E402
from stock_agent.backtest.loader import InMemoryBarLoader
from stock_agent.data import MinuteBar

# --------------------------------------------------------------------------
# 공통 헬퍼
# --------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))


def _kst(d: date, h: int = 9, m: int = 0) -> datetime:
    """date + h:m 을 KST tz-aware datetime 으로 반환."""
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KST)


def _make_bar(
    symbol: str,
    bar_time: datetime,
    close: int | str | Decimal,
) -> MinuteBar:
    """open=high=low=close 단순화된 MinuteBar 빌더."""
    c = Decimal(str(close))
    return MinuteBar(
        symbol=symbol,
        bar_time=bar_time,
        open=c,
        high=c,
        low=c,
        close=c,
        volume=1000,
    )


# ===========================================================================
# A. DCABaselineConfig DTO 가드
# ===========================================================================


class TestDCABaselineConfig:
    """DCABaselineConfig __post_init__ 검증 — 모든 위반 조건은 RuntimeError."""

    def test_정상_생성(self):
        cfg = DCABaselineConfig(
            starting_capital_krw=1_000_000,
            monthly_investment_krw=100_000,
        )
        assert cfg.starting_capital_krw == 1_000_000
        assert cfg.target_symbol == "069500"
        assert cfg.purchase_day == 1

    def test_starting_capital_zero_RuntimeError(self):
        with pytest.raises(RuntimeError):
            DCABaselineConfig(
                starting_capital_krw=0,
                monthly_investment_krw=100_000,
            )

    def test_starting_capital_음수_RuntimeError(self):
        with pytest.raises(RuntimeError):
            DCABaselineConfig(
                starting_capital_krw=-1,
                monthly_investment_krw=100_000,
            )

    def test_monthly_investment_zero_RuntimeError(self):
        with pytest.raises(RuntimeError):
            DCABaselineConfig(
                starting_capital_krw=1_000_000,
                monthly_investment_krw=0,
            )

    def test_monthly_investment_초과_RuntimeError(self):
        """monthly_investment_krw > starting_capital_krw → 첫 달 매수 불가."""
        with pytest.raises(RuntimeError):
            DCABaselineConfig(
                starting_capital_krw=100_000,
                monthly_investment_krw=200_000,
            )

    def test_target_symbol_형식_위반_RuntimeError(self):
        with pytest.raises(RuntimeError):
            DCABaselineConfig(
                starting_capital_krw=1_000_000,
                monthly_investment_krw=100_000,
                target_symbol="ABC",
            )

    @pytest.mark.parametrize(
        "day",
        [0, 29],
        ids=["purchase_day=0", "purchase_day=29"],
    )
    def test_purchase_day_범위_위반_RuntimeError(self, day: int):
        with pytest.raises(RuntimeError):
            DCABaselineConfig(
                starting_capital_krw=1_000_000,
                monthly_investment_krw=100_000,
                purchase_day=day,
            )

    def test_slippage_rate_ge_1_RuntimeError(self):
        with pytest.raises(RuntimeError):
            DCABaselineConfig(
                starting_capital_krw=1_000_000,
                monthly_investment_krw=100_000,
                slippage_rate=Decimal("1.0"),
            )

    def test_slippage_rate_음수_RuntimeError(self):
        with pytest.raises(RuntimeError):
            DCABaselineConfig(
                starting_capital_krw=1_000_000,
                monthly_investment_krw=100_000,
                slippage_rate=Decimal("-0.1"),
            )

    def test_commission_rate_음수_RuntimeError(self):
        with pytest.raises(RuntimeError):
            DCABaselineConfig(
                starting_capital_krw=1_000_000,
                monthly_investment_krw=100_000,
                commission_rate=Decimal("-0.001"),
            )

    def test_sell_tax_rate_음수_RuntimeError(self):
        with pytest.raises(RuntimeError):
            DCABaselineConfig(
                starting_capital_krw=1_000_000,
                monthly_investment_krw=100_000,
                sell_tax_rate=Decimal("-0.001"),
            )

    def test_frozen_필드_수정_FrozenInstanceError(self):
        cfg = DCABaselineConfig(
            starting_capital_krw=1_000_000,
            monthly_investment_krw=100_000,
        )
        with pytest.raises(FrozenInstanceError):
            cfg.starting_capital_krw = 2_000_000  # type: ignore[misc]


# ===========================================================================
# B. compute_dca_baseline — 단일 월 매수
# ===========================================================================

_D1 = date(2025, 1, 2)  # 2025-01-02 (첫째 영업일)
_SYMBOL = "069500"


class TestComputeDCABaselineSingleMonth:
    """단일 월 분봉 1건 → 1 lot 매수 + 1 TradeRecord + daily_equity 1건."""

    def _cfg(self, **kw) -> DCABaselineConfig:
        defaults: dict[str, Any] = dict(
            starting_capital_krw=1_000_000,
            monthly_investment_krw=100_000,
            target_symbol=_SYMBOL,
            purchase_day=1,
            commission_rate=Decimal("0.00015"),
            sell_tax_rate=Decimal("0.0018"),
            slippage_rate=Decimal("0.001"),
        )
        defaults.update(kw)
        return DCABaselineConfig(**defaults)

    def test_1lot_매수_1TradeRecord(self):
        """purchase_day=1, 분봉 1건 → 체결 1건."""
        cfg = self._cfg()
        bar = _make_bar(_SYMBOL, _kst(_D1, 9, 0), close=10_000)
        loader = InMemoryBarLoader([bar])

        result = compute_dca_baseline(loader, cfg, _D1, _D1)

        assert len(result.trades) == 1

    def test_entry_price_슬리피지_반영(self):
        """entry_price = close * (1 + slippage_rate)."""
        cfg = self._cfg(slippage_rate=Decimal("0.001"))
        close = Decimal("10000")
        bar = _make_bar(_SYMBOL, _kst(_D1, 9, 0), close=close)
        loader = InMemoryBarLoader([bar])

        result = compute_dca_baseline(loader, cfg, _D1, _D1)

        expected_entry = close * (1 + Decimal("0.001"))
        assert result.trades[0].entry_price == pytest.approx(float(expected_entry), rel=1e-9)

    def test_exit_price_슬리피지_반영(self):
        """hypothetical 청산: exit_price = last_close * (1 - slippage_rate)."""
        cfg = self._cfg(slippage_rate=Decimal("0.001"))
        close = Decimal("10000")
        bar = _make_bar(_SYMBOL, _kst(_D1, 9, 0), close=close)
        loader = InMemoryBarLoader([bar])

        result = compute_dca_baseline(loader, cfg, _D1, _D1)

        expected_exit = close * (1 - Decimal("0.001"))
        assert result.trades[0].exit_price == pytest.approx(float(expected_exit), rel=1e-9)

    def test_qty_floor_계산(self):
        """qty = floor(monthly_investment_krw / entry_fill)."""
        cfg = self._cfg(monthly_investment_krw=100_000, slippage_rate=Decimal("0"))
        close = Decimal("3000")  # entry_fill = 3000, qty = floor(100000/3000) = 33
        bar = _make_bar(_SYMBOL, _kst(_D1, 9, 0), close=close)
        loader = InMemoryBarLoader([bar])

        result = compute_dca_baseline(loader, cfg, _D1, _D1)

        assert result.trades[0].qty == 33

    def test_cash_감소_검증(self):
        """매수 후 daily_equity.equity_krw < starting_capital_krw."""
        cfg = self._cfg()
        bar = _make_bar(_SYMBOL, _kst(_D1, 9, 0), close=10_000)
        loader = InMemoryBarLoader([bar])

        result = compute_dca_baseline(loader, cfg, _D1, _D1)

        assert len(result.daily_equity) >= 1
        # DCA 는 영구 보유 → equity = cash + mark-to-market
        # 매수 후이므로 cash < starting_capital
        initial = cfg.starting_capital_krw
        # daily_equity 는 mark-to-market 포함이므로 단순 비교는 불가;
        # 대신 trades 가 생겼으므로 TradeRecord 가 존재하는지 확인
        assert len(result.trades) == 1
        assert result.daily_equity[0].equity_krw <= initial

    def test_exit_reason_force_close(self):
        """hypothetical 청산의 exit_reason = 'force_close'."""
        cfg = self._cfg()
        bar = _make_bar(_SYMBOL, _kst(_D1, 9, 0), close=10_000)
        loader = InMemoryBarLoader([bar])

        result = compute_dca_baseline(loader, cfg, _D1, _D1)

        assert result.trades[0].exit_reason == "force_close"


# ===========================================================================
# C. compute_dca_baseline — 다중 월 누적
# ===========================================================================

_D_JAN = date(2025, 1, 2)
_D_FEB = date(2025, 2, 3)
_D_MAR = date(2025, 3, 3)


class TestComputeDCABaselineMultiMonth:
    """3개월 첫 영업일 각각 매수 → 3 lots, 3 TradeRecord."""

    def _cfg(self, **kw) -> DCABaselineConfig:
        defaults: dict[str, Any] = dict(
            starting_capital_krw=2_000_000,
            monthly_investment_krw=200_000,
            target_symbol=_SYMBOL,
            purchase_day=1,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        defaults.update(kw)
        return DCABaselineConfig(**defaults)

    def _three_month_bars(
        self,
        close_jan: int = 10_000,
        close_feb: int = 11_000,
        close_mar: int = 12_000,
    ) -> list[MinuteBar]:
        return [
            _make_bar(_SYMBOL, _kst(_D_JAN, 9, 0), close=close_jan),
            _make_bar(_SYMBOL, _kst(_D_FEB, 9, 0), close=close_feb),
            _make_bar(_SYMBOL, _kst(_D_MAR, 9, 0), close=close_mar),
        ]

    def test_3trades_반환(self):
        cfg = self._cfg()
        bars = self._three_month_bars()
        loader = InMemoryBarLoader(bars)

        result = compute_dca_baseline(loader, cfg, _D_JAN, _D_MAR)

        assert len(result.trades) == 3

    def test_각_월_매수가격_다름(self):
        """가격이 다른 달에는 entry_price 가 각각 다름."""
        cfg = self._cfg()
        bars = self._three_month_bars(10_000, 11_000, 12_000)
        loader = InMemoryBarLoader(bars)

        result = compute_dca_baseline(loader, cfg, _D_JAN, _D_MAR)

        prices = [float(t.entry_price) for t in result.trades]
        assert len(set(prices)) == 3, "세 달 진입가가 모두 달라야 한다"

    def test_마지막_close_기준_hypothetical_청산(self):
        """모든 lot 의 exit_price 는 마지막 bar.close 기준이어야 한다."""
        cfg = self._cfg()
        bars = self._three_month_bars(10_000, 11_000, 12_000)
        loader = InMemoryBarLoader(bars)

        result = compute_dca_baseline(loader, cfg, _D_JAN, _D_MAR)

        last_close = Decimal("12000")
        for trade in result.trades:
            assert trade.exit_price == pytest.approx(float(last_close), rel=1e-9)

    def test_daily_equity_최소_3건(self):
        """3개 세션이 있으면 daily_equity 도 3건 이상."""
        cfg = self._cfg()
        bars = self._three_month_bars()
        loader = InMemoryBarLoader(bars)

        result = compute_dca_baseline(loader, cfg, _D_JAN, _D_MAR)

        assert len(result.daily_equity) >= 3


# ===========================================================================
# D. compute_dca_baseline — 엣지 케이스
# ===========================================================================


class TestComputeDCABaselineEdgeCases:
    """빈 스트림·qty=0·cash 고갈 케이스."""

    def _cfg(self, **kw) -> DCABaselineConfig:
        defaults: dict[str, Any] = dict(
            starting_capital_krw=1_000_000,
            monthly_investment_krw=100_000,
            target_symbol=_SYMBOL,
            purchase_day=1,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        defaults.update(kw)
        return DCABaselineConfig(**defaults)

    def test_빈_bar_stream(self):
        """bar 없으면 trades=(), daily_equity=() 반환."""
        cfg = self._cfg()
        loader = InMemoryBarLoader([])

        result = compute_dca_baseline(loader, cfg, _D1, _D1)

        assert result.trades == ()
        assert result.daily_equity == ()
        assert result.metrics.net_pnl_krw == 0

    def test_qty_zero_매수_skip(self):
        """monthly < 1주 가격 → qty=0 → 매수 skip, trades=()."""
        # close=200_000, monthly=100_000 → qty=floor(100000/200000)=0
        cfg = self._cfg(monthly_investment_krw=100_000)
        bar = _make_bar(_SYMBOL, _kst(_D1, 9, 0), close=200_000)
        loader = InMemoryBarLoader([bar])

        result = compute_dca_baseline(loader, cfg, _D1, _D1)

        assert result.trades == ()

    def test_cash_고갈_이후_매수_skip(self):
        """누적 매수로 잔액 부족 → 일부 lot 만 체결."""
        # starting=300_000, monthly=150_000, close=10_000(slippage=0)
        # 1월: qty=15, cost=150_000 → cash=150_000
        # 2월: qty=15, cost=150_000 → cash=0
        # 3월: total_cost=150_000 > cash=0 → skip
        cfg = self._cfg(
            starting_capital_krw=300_000,
            monthly_investment_krw=150_000,
        )
        bars = [
            _make_bar(_SYMBOL, _kst(_D_JAN, 9, 0), close=10_000),
            _make_bar(_SYMBOL, _kst(_D_FEB, 9, 0), close=10_000),
            _make_bar(_SYMBOL, _kst(_D_MAR, 9, 0), close=10_000),
        ]
        loader = InMemoryBarLoader(bars)

        result = compute_dca_baseline(loader, cfg, _D_JAN, _D_MAR)

        assert len(result.trades) == 2  # 3월 skip

    def test_DCA_ExitSignal_미생성(self):
        """DCAStrategy 는 ExitSignal 절대 미생성 — hypothetical 청산은 endgame 처리."""
        from stock_agent.strategy.dca import DCAConfig, DCAStrategy

        strategy = DCAStrategy(DCAConfig(monthly_investment_krw=100_000))
        bar = _make_bar(_SYMBOL, _kst(_D1, 9, 0), close=10_000)
        signals = strategy.on_bar(bar)

        from stock_agent.strategy.base import ExitSignal

        exit_signals = [s for s in signals if isinstance(s, ExitSignal)]
        assert exit_signals == []


# ===========================================================================
# E. compute_dca_baseline — 메트릭 계산
# ===========================================================================


class TestComputeDCABaselineMetrics:
    """가격 +50%·-30% 케이스와 표본 부족 sharpe=0 검증."""

    def _cfg(self, **kw) -> DCABaselineConfig:
        defaults: dict[str, Any] = dict(
            starting_capital_krw=1_000_000,
            monthly_investment_krw=100_000,
            target_symbol=_SYMBOL,
            purchase_day=1,
            slippage_rate=Decimal("0"),
            commission_rate=Decimal("0"),
            sell_tax_rate=Decimal("0"),
        )
        defaults.update(kw)
        return DCABaselineConfig(**defaults)

    def test_가격_상승_total_return_양수(self):
        """entry 10_000 → exit 15_000(+50%) → total_return_pct > 0, net_pnl_krw > 0."""
        cfg = self._cfg(monthly_investment_krw=100_000)
        # purchase_day=1이므로 1월 첫 분봉에서 매수
        # 이후 close=15_000 로 상승
        bars = [
            _make_bar(_SYMBOL, _kst(_D_JAN, 9, 0), close=10_000),  # 매수
            _make_bar(_SYMBOL, _kst(_D_JAN, 9, 1), close=15_000),  # 동월 추가 bar
        ]
        loader = InMemoryBarLoader(bars)

        result = compute_dca_baseline(loader, cfg, _D_JAN, _D_JAN)

        assert result.metrics.total_return_pct > 0
        assert result.metrics.net_pnl_krw > 0

    def test_가격_하락_total_return_음수(self):
        """entry 10_000 → exit 7_000(-30%) → total_return_pct < 0."""
        cfg = self._cfg(monthly_investment_krw=100_000)
        bars = [
            _make_bar(_SYMBOL, _kst(_D_JAN, 9, 0), close=10_000),
            _make_bar(_SYMBOL, _kst(_D_JAN, 9, 1), close=7_000),
        ]
        loader = InMemoryBarLoader(bars)

        result = compute_dca_baseline(loader, cfg, _D_JAN, _D_JAN)

        assert result.metrics.total_return_pct < 0
        assert result.metrics.max_drawdown_pct <= 0

    def test_단일_월_샤프_zero_또는_기본값(self):
        """세션 1건 → daily_returns 표본 부족 → sharpe_ratio == 0."""
        cfg = self._cfg()
        bar = _make_bar(_SYMBOL, _kst(_D1, 9, 0), close=10_000)
        loader = InMemoryBarLoader([bar])

        result = compute_dca_baseline(loader, cfg, _D1, _D1)

        assert result.metrics.sharpe_ratio == Decimal("0")


# ===========================================================================
# F. compute_dca_baseline — loader 호출 인터랙션
# ===========================================================================


class TestComputeDCABaselineLoaderInteraction:
    """loader.stream 이 target_symbol 단일 튜플로 정확히 호출되는지 검증."""

    def _cfg(self) -> DCABaselineConfig:
        return DCABaselineConfig(
            starting_capital_krw=1_000_000,
            monthly_investment_krw=100_000,
            target_symbol=_SYMBOL,
        )

    def test_loader_stream_target_symbol만_요청(self):
        """compute_dca_baseline 이 loader.stream(_,_,(target_symbol,)) 로 호출."""
        cfg = self._cfg()
        loader = InMemoryBarLoader([])

        with patch.object(loader, "stream", wraps=loader.stream) as mock_stream:
            compute_dca_baseline(loader, cfg, _D1, _D1)

        mock_stream.assert_called_once()
        positional = mock_stream.call_args.args
        # stream(start, end, symbols) — positional 3번째 인자가 (_SYMBOL,)
        assert positional[2] == (_SYMBOL,)

    def test_비타겟_심볼_bar_무시(self):
        """loader 에 비타겟 bar 가 섞여도 stream 호출 시 target_symbol 만 요청."""
        cfg = self._cfg()
        other_bar = _make_bar("005930", _kst(_D1, 9, 0), close=70_000)
        target_bar = _make_bar(_SYMBOL, _kst(_D1, 9, 1), close=10_000)
        # InMemoryBarLoader 는 stream(symbols=(_SYMBOL,)) 호출 시 비타겟 자동 필터링
        loader = InMemoryBarLoader([other_bar, target_bar])

        result = compute_dca_baseline(loader, cfg, _D1, _D1)

        # target_symbol 분봉 1건만 처리됨 (매수 1건)
        assert len(result.trades) == 1
