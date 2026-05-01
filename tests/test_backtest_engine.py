"""BacktestEngine / BacktestConfig / costs / metrics / loader 공개 계약 단위·통합 테스트.

외부 네트워크·DB·시계 의존 없음 — 모든 모듈이 순수 로직이므로 목킹 불필요.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from stock_agent.backtest import (
    BacktestConfig,
    BacktestEngine,
    DailyEquity,
    InMemoryBarLoader,
    TradeRecord,
    costs,
    metrics,
)
from stock_agent.data import MinuteBar
from stock_agent.risk import RiskConfig
from stock_agent.strategy import StrategyConfig

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_DATE = date(2026, 4, 20)
_DATE2 = date(2026, 4, 21)

_SYMBOL = "005930"
_SYMBOL_B = "000660"
_SYMBOL_C = "035720"
_SYMBOL_D = "035420"


def _bar(
    symbol: str,
    h: int,
    m: int,
    open_: int | str | Decimal,
    high: int | str | Decimal,
    low: int | str | Decimal,
    close: int | str | Decimal,
    *,
    date_: date = _DATE,
    volume: int = 0,
) -> MinuteBar:
    """MinuteBar 생성 헬퍼. h/m 은 KST 시·분."""
    return MinuteBar(
        symbol=symbol,
        bar_time=datetime(date_.year, date_.month, date_.day, h, m, tzinfo=KST),
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=volume,
    )


def _default_engine(
    capital: int = 1_000_000,
    strategy_config: StrategyConfig | None = None,
    risk_config: RiskConfig | None = None,
) -> BacktestEngine:
    """기본 BacktestEngine 헬퍼."""
    return BacktestEngine(
        BacktestConfig(
            starting_capital_krw=capital,
            strategy_config=strategy_config,
            risk_config=risk_config,
        )
    )


# ---------------------------------------------------------------------------
# A. costs.py 단위 테스트
# ---------------------------------------------------------------------------


class TestCosts:
    # ---- buy_fill_price ----

    def test_buy_fill_price_정상값(self):
        """buy_fill_price(70000, 0.001) == 70000 * 1.001 = 70070.000"""
        result = costs.buy_fill_price(Decimal("70000"), Decimal("0.001"))
        assert result == Decimal("70070.000")

    def test_buy_fill_price_slippage_zero(self):
        """슬리피지 0 이면 참고가 그대로."""
        result = costs.buy_fill_price(Decimal("50000"), Decimal("0"))
        assert result == Decimal("50000")

    def test_buy_fill_price_음수_reference_RuntimeError(self):
        """reference < 0 → RuntimeError."""
        with pytest.raises(RuntimeError, match="reference"):
            costs.buy_fill_price(Decimal("-1"), Decimal("0.001"))

    def test_buy_fill_price_음수_slippage_RuntimeError(self):
        """slippage_rate < 0 → RuntimeError."""
        with pytest.raises(RuntimeError, match="slippage_rate"):
            costs.buy_fill_price(Decimal("70000"), Decimal("-0.001"))

    # ---- sell_fill_price ----

    def test_sell_fill_price_정상값(self):
        """sell_fill_price(70000, 0.001) == 70000 * 0.999 = 69930.000"""
        result = costs.sell_fill_price(Decimal("70000"), Decimal("0.001"))
        assert result == Decimal("69930.000")

    def test_sell_fill_price_slippage_ge_1_RuntimeError(self):
        """slippage_rate >= 1 → RuntimeError."""
        with pytest.raises(RuntimeError):
            costs.sell_fill_price(Decimal("70000"), Decimal("1"))

    def test_sell_fill_price_slippage_exactly_1_RuntimeError(self):
        """slippage_rate == 1 정확히 경계값 → RuntimeError."""
        with pytest.raises(RuntimeError):
            costs.sell_fill_price(Decimal("70000"), Decimal("1.0"))

    def test_sell_fill_price_음수_reference_RuntimeError(self):
        """reference < 0 → RuntimeError."""
        with pytest.raises(RuntimeError, match="reference"):
            costs.sell_fill_price(Decimal("-1"), Decimal("0.001"))

    def test_sell_fill_price_음수_slippage_RuntimeError(self):
        """slippage_rate < 0 → RuntimeError."""
        with pytest.raises(RuntimeError, match="slippage_rate"):
            costs.sell_fill_price(Decimal("70000"), Decimal("-0.001"))

    # ---- buy_commission ----

    def test_buy_commission_floor(self):
        """buy_commission(142142, 0.00015) = floor(142142 * 0.00015) = 21."""
        result = costs.buy_commission(Decimal("142142"), Decimal("0.00015"))
        assert result == 21

    def test_buy_commission_정수형_반환(self):
        """반환 타입은 int."""
        result = costs.buy_commission(Decimal("100000"), Decimal("0.00015"))
        assert isinstance(result, int)

    def test_buy_commission_음수_notional_RuntimeError(self):
        """notional < 0 → RuntimeError."""
        with pytest.raises(RuntimeError, match="notional"):
            costs.buy_commission(Decimal("-1"), Decimal("0.00015"))

    def test_buy_commission_음수_rate_RuntimeError(self):
        """rate < 0 → RuntimeError."""
        with pytest.raises(RuntimeError, match="rate"):
            costs.buy_commission(Decimal("100000"), Decimal("-0.001"))

    # ---- sell_commission ----

    def test_sell_commission_floor(self):
        """sell_commission(146113, 0.00015) = floor(146113 * 0.00015) = 21."""
        result = costs.sell_commission(Decimal("146113"), Decimal("0.00015"))
        assert result == 21

    def test_sell_commission_음수_notional_RuntimeError(self):
        """notional < 0 → RuntimeError."""
        with pytest.raises(RuntimeError, match="notional"):
            costs.sell_commission(Decimal("-1"), Decimal("0.00015"))

    # ---- sell_tax ----

    def test_sell_tax_floor(self):
        """sell_tax(146113, 0.0018) = floor(146113 * 0.0018) = 263."""
        result = costs.sell_tax(Decimal("146113"), Decimal("0.0018"))
        assert result == 263

    def test_sell_tax_정수형_반환(self):
        """반환 타입은 int."""
        result = costs.sell_tax(Decimal("100000"), Decimal("0.0018"))
        assert isinstance(result, int)

    def test_sell_tax_음수_notional_RuntimeError(self):
        """notional < 0 → RuntimeError."""
        with pytest.raises(RuntimeError, match="notional"):
            costs.sell_tax(Decimal("-1"), Decimal("0.0018"))

    def test_sell_tax_음수_rate_RuntimeError(self):
        """rate < 0 → RuntimeError."""
        with pytest.raises(RuntimeError, match="rate"):
            costs.sell_tax(Decimal("146113"), Decimal("-0.001"))


# ---------------------------------------------------------------------------
# B. metrics.py 단위 테스트
# ---------------------------------------------------------------------------


class TestMetrics:
    # ---- total_return_pct ----

    def test_total_return_pct_1pct_수익(self):
        """(1_010_000 - 1_000_000) / 1_000_000 == 0.01."""
        result = metrics.total_return_pct(1_000_000, 1_010_000)
        assert result == Decimal("0.01")

    def test_total_return_pct_start_zero_분모_방어(self):
        """start=0 → 0 (분모 방어)."""
        result = metrics.total_return_pct(0, 100)
        assert result == Decimal("0")

    def test_total_return_pct_start_음수_분모_방어(self):
        """start < 0 → 0 (분모 방어 — starting_equity_krw <= 0)."""
        result = metrics.total_return_pct(-1, 100)
        assert result == Decimal("0")

    def test_total_return_pct_손실(self):
        """손실 시 음수 반환."""
        result = metrics.total_return_pct(1_000_000, 900_000)
        assert result == Decimal("-0.1")

    # ---- max_drawdown_pct ----

    def test_max_drawdown_pct_시나리오(self):
        """[100, 120, 90, 130, 80] → MDD ≈ (80-130)/130 ≈ -0.3846."""
        result = metrics.max_drawdown_pct([100, 120, 90, 130, 80])
        expected = Decimal(80 - 130) / Decimal(130)  # ≈ -0.38461538...
        assert abs(result - expected) < Decimal("1e-4"), f"MDD={result} expected={expected}"

    def test_max_drawdown_pct_빈_시리즈(self):
        """빈 시리즈 → 0."""
        assert metrics.max_drawdown_pct([]) == Decimal("0")

    def test_max_drawdown_pct_단일_포인트(self):
        """단일 포인트 → 0."""
        assert metrics.max_drawdown_pct([100]) == Decimal("0")

    def test_max_drawdown_pct_단조증가(self):
        """단조증가 시리즈 → 0 (낙폭 없음)."""
        assert metrics.max_drawdown_pct([100, 110, 120, 130]) == Decimal("0")

    def test_max_drawdown_pct_음수_반환(self):
        """MDD 는 0 이하 (낙폭 있을 때 음수)."""
        result = metrics.max_drawdown_pct([100, 80])
        assert result < Decimal("0")

    # ---- sharpe_ratio ----

    def test_sharpe_ratio_stdev_zero(self):
        """동일 수익률 반복 → pstdev=0 → 0 반환."""
        result = metrics.sharpe_ratio([Decimal("0.01")] * 5)
        assert result == Decimal("0")

    def test_sharpe_ratio_빈_입력(self):
        """빈 입력 → 0."""
        assert metrics.sharpe_ratio([]) == Decimal("0")

    def test_sharpe_ratio_단일_표본(self):
        """단일 표본 (표본 ≤ 1) → 0."""
        assert metrics.sharpe_ratio([Decimal("0.01")]) == Decimal("0")

    def test_sharpe_ratio_정상_계산(self):
        """두 개 이상의 서로 다른 수익률 → 0 이 아닌 값 반환 (부호 확인)."""
        returns = [Decimal("0.02"), Decimal("-0.01"), Decimal("0.03"), Decimal("0.01")]
        result = metrics.sharpe_ratio(returns)
        # 평균 양수이므로 샤프 양수
        assert result > Decimal("0")

    # ---- win_rate ----

    def test_win_rate_일반_케이스(self):
        """[100, -50, 200, 0] → winners=2, losers=1, total=3 → 2/3."""
        result = metrics.win_rate([100, -50, 200, 0])
        assert result == Decimal("2") / Decimal("3")

    def test_win_rate_빈_입력(self):
        """빈 입력 → 0."""
        assert metrics.win_rate([]) == Decimal("0")

    def test_win_rate_모두_zero(self):
        """모두 0 (break-even) → total=0 → 0."""
        assert metrics.win_rate([0, 0]) == Decimal("0")

    def test_win_rate_모두_음수(self):
        """모두 패자 → 0."""
        assert metrics.win_rate([-100, -200]) == Decimal("0")

    def test_win_rate_모두_양수(self):
        """모두 승자 → 1."""
        assert metrics.win_rate([100, 200]) == Decimal("1")

    # ---- avg_pnl_ratio ----

    def test_avg_pnl_ratio_정상(self):
        """[100, 200, -50] → mean([100,200])/|mean([-50])| = 150/50 = 3."""
        result = metrics.avg_pnl_ratio([100, 200, -50])
        assert result == Decimal("3")

    def test_avg_pnl_ratio_패자_없음(self):
        """승자만 있으면 0."""
        assert metrics.avg_pnl_ratio([100]) == Decimal("0")

    def test_avg_pnl_ratio_승자_없음(self):
        """패자만 있으면 0."""
        assert metrics.avg_pnl_ratio([-100]) == Decimal("0")

    def test_avg_pnl_ratio_빈_입력(self):
        """빈 입력 → 0."""
        assert metrics.avg_pnl_ratio([]) == Decimal("0")

    # ---- trades_per_day ----

    def test_trades_per_day_정상(self):
        """10 거래 / 5 세션 == 2."""
        result = metrics.trades_per_day(10, 5)
        assert result == Decimal("2")

    def test_trades_per_day_sessions_zero(self):
        """sessions=0 → 0 (분모 방어)."""
        assert metrics.trades_per_day(0, 0) == Decimal("0")

    def test_trades_per_day_거래_없음(self):
        """거래 0 / 세션 5 == 0."""
        result = metrics.trades_per_day(0, 5)
        assert result == Decimal("0")


# ---------------------------------------------------------------------------
# C. loader.py 단위 테스트 (InMemoryBarLoader)
# ---------------------------------------------------------------------------


class TestInMemoryBarLoader:
    def test_정렬_시간순(self):
        """입력 순서 무관하게 bar_time 오름차순으로 정렬된다."""
        bars = [
            _bar(_SYMBOL, 9, 31, 70000, 70500, 69800, 70200),
            _bar(_SYMBOL, 9, 30, 70000, 70500, 69800, 70100),
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000),
        ]
        loader = InMemoryBarLoader(bars)
        times = [b.bar_time for b in loader.bars]
        assert times == sorted(times), "bars 가 시간순 정렬되지 않았다"

    def test_중복_dedupe_나중값_우선(self):
        """동일 (symbol, bar_time) → 나중에 추가된 값이 남는다."""
        early = _bar(_SYMBOL, 9, 30, 70000, 70100, 69900, 70000)
        late = _bar(_SYMBOL, 9, 30, 71000, 71500, 70800, 71200)  # close 가 다름
        loader = InMemoryBarLoader([early, late])
        # dedupe 후 1건만 남아야 한다
        assert len(loader.bars) == 1
        assert loader.bars[0].close == Decimal("71200"), "나중 값 우선 실패"

    def test_stream_날짜_범위_필터링(self):
        """start/end 범위 밖 bar 는 stream 에 포함되지 않는다."""
        bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000, date_=date(2026, 4, 19)),
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000, date_=_DATE),
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000, date_=date(2026, 4, 21)),
        ]
        loader = InMemoryBarLoader(bars)
        result = list(loader.stream(_DATE, _DATE, (_SYMBOL,)))
        assert len(result) == 1
        assert result[0].bar_time.date() == _DATE

    def test_stream_심볼_필터링(self):
        """symbols 에 없는 종목은 stream 에 포함되지 않는다."""
        bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000),
            _bar(_SYMBOL_B, 9, 0, 80000, 80500, 79800, 80000),
        ]
        loader = InMemoryBarLoader(bars)
        result = list(loader.stream(_DATE, _DATE, (_SYMBOL,)))
        assert all(b.symbol == _SYMBOL for b in result)
        assert len(result) == 1

    def test_stream_start_gt_end_RuntimeError(self):
        """start > end → RuntimeError."""
        loader = InMemoryBarLoader([])
        with pytest.raises(RuntimeError, match="start"):
            list(loader.stream(date(2026, 4, 21), date(2026, 4, 20), (_SYMBOL,)))

    def test_stream_빈_심볼_튜플_RuntimeError(self):
        """symbols=() (빈 튜플) → 호출자 오류, RuntimeError."""
        bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000),
            _bar(_SYMBOL_B, 9, 0, 80000, 80500, 79800, 80000),
        ]
        loader = InMemoryBarLoader(bars)
        with pytest.raises(RuntimeError, match="symbols"):
            list(loader.stream(_DATE, _DATE, ()))

    def test_stream_경계_날짜_포함(self):
        """start == end == bar_date 인 경우 포함된다 (경계 inclusive)."""
        loader = InMemoryBarLoader([_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000)])
        result = list(loader.stream(_DATE, _DATE, (_SYMBOL,)))
        assert len(result) == 1

    def test_bars_프로퍼티_스냅샷_불변(self):
        """bars 프로퍼티는 튜플 — 외부 수정 불가."""
        loader = InMemoryBarLoader([_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000)])
        assert isinstance(loader.bars, tuple)


# ---------------------------------------------------------------------------
# D. engine.py 통합 테스트
# ---------------------------------------------------------------------------


class TestBacktestConfig:
    def test_기본값_생성_성공(self):
        """기본 BacktestConfig 는 예외 없이 생성된다."""
        cfg = BacktestConfig(starting_capital_krw=1_000_000)
        assert cfg.starting_capital_krw == 1_000_000
        assert cfg.commission_rate == Decimal("0.00015")
        assert cfg.sell_tax_rate == Decimal("0.0018")
        assert cfg.slippage_rate == Decimal("0.001")

    def test_자본_0_RuntimeError(self):
        """starting_capital_krw=0 → RuntimeError."""
        with pytest.raises(RuntimeError, match="starting_capital_krw"):
            BacktestConfig(starting_capital_krw=0)

    def test_자본_음수_RuntimeError(self):
        """starting_capital_krw < 0 → RuntimeError."""
        with pytest.raises(RuntimeError, match="starting_capital_krw"):
            BacktestConfig(starting_capital_krw=-1)

    def test_commission_rate_음수_RuntimeError(self):
        """commission_rate < 0 → RuntimeError."""
        with pytest.raises(RuntimeError, match="commission_rate"):
            BacktestConfig(starting_capital_krw=1_000_000, commission_rate=Decimal("-0.001"))

    def test_sell_tax_rate_음수_RuntimeError(self):
        """sell_tax_rate < 0 → RuntimeError."""
        with pytest.raises(RuntimeError, match="sell_tax_rate"):
            BacktestConfig(starting_capital_krw=1_000_000, sell_tax_rate=Decimal("-0.001"))

    def test_slippage_rate_음수_RuntimeError(self):
        """slippage_rate < 0 → RuntimeError."""
        with pytest.raises(RuntimeError, match="slippage_rate"):
            BacktestConfig(starting_capital_krw=1_000_000, slippage_rate=Decimal("-0.001"))

    def test_slippage_rate_ge_1_RuntimeError(self):
        """slippage_rate >= 1 → RuntimeError."""
        with pytest.raises(RuntimeError, match="slippage_rate"):
            BacktestConfig(starting_capital_krw=1_000_000, slippage_rate=Decimal("1"))

    # ------------------------------------------------------------------
    # strategy_factory 필드 — RED 케이스 (strategy_factory 미구현 상태에서 FAIL)
    # ------------------------------------------------------------------

    def test_strategy_factory_default_none(self):
        """BacktestConfig 기본 생성 시 strategy_factory 는 None 이어야 한다.

        RED: strategy_factory 필드가 아직 없어 AttributeError 로 FAIL.
        """
        cfg = BacktestConfig(starting_capital_krw=1_000_000)
        assert cfg.strategy_factory is None  # type: ignore[attr-defined]

    def test_strategy_factory_and_config_mutually_exclusive(self):
        """strategy_factory 와 strategy_config 동시 지정 시 RuntimeError.

        RED: strategy_factory 필드 부재로 TypeError('unexpected keyword argument') 발생
        → pytest.raises(RuntimeError) 가 충족되지 않아 FAIL.
        """
        with pytest.raises(RuntimeError, match="strategy_factory"):
            BacktestConfig(
                starting_capital_krw=1_000_000,
                strategy_factory=lambda: None,  # type: ignore[call-arg]
                strategy_config=StrategyConfig(),
            )

    def test_strategy_factory_alone_ok(self):
        """strategy_factory 만 단독 지정 시 예외 없이 생성된다.

        RED: strategy_factory 필드 부재로 TypeError 로 FAIL.
        """
        from stock_agent.strategy import StrategyConfig as _SC  # noqa: N814

        cfg = BacktestConfig(
            starting_capital_krw=1_000_000,
            strategy_factory=lambda: __import__(  # type: ignore[call-arg]
                "stock_agent.strategy", fromlist=["ORBStrategy"]
            ).ORBStrategy(_SC()),
        )
        assert cfg.strategy_factory is not None  # type: ignore[attr-defined]


class TestEngineStrategyFactory:
    """BacktestEngine.run() 에서 strategy_factory 가 올바르게 사용되는지 검증.

    RED 케이스 — strategy_factory 분기가 engine.py 에 미구현 상태이므로
    모두 FAIL 이어야 한다.
    """

    def test_strategy_factory_invoked_at_run(self):
        """factory callable 이 run() 시 정확히 1회 호출된다.

        RED: BacktestConfig 에 strategy_factory 필드 없음 → TypeError 로 FAIL.
        """
        from stock_agent.strategy import ORBStrategy
        from stock_agent.strategy import StrategyConfig as _SC  # noqa: N814

        call_count: list[int] = [0]

        def _factory() -> ORBStrategy:
            call_count[0] += 1
            return ORBStrategy(_SC())

        cfg = BacktestConfig(
            starting_capital_krw=1_000_000,
            strategy_factory=_factory,  # type: ignore[call-arg]
        )
        engine = BacktestEngine(cfg)
        engine.run([])
        assert call_count[0] == 1, f"factory 호출 횟수 기대=1, 실제={call_count[0]}"

    def test_strategy_factory_default_uses_orb(self):
        """factory=None + strategy_config=None 이면 기존 ORBStrategy 디폴트 사용.

        기존 동작 회귀 — 빈 스트림에서도 정상 run() 이 완료돼야 한다.
        현재 엔진은 이미 이 경로를 지원하므로, strategy_factory 필드 추가 후에도
        이 케이스는 GREEN 이어야 한다.

        RED 단계에서는 strategy_factory 필드가 없어 test_strategy_factory_default_none
        이 FAIL 하지만, 이 케이스 자체는 기존 코드로 통과한다 — 의도적으로 분리.
        """
        from stock_agent.strategy import ORBStrategy

        cfg = BacktestConfig(starting_capital_krw=1_000_000)
        engine = BacktestEngine(cfg)
        result = engine.run([])
        # 기존 동작: ORBStrategy 를 사용하는 빈 스트림 → 메트릭 0, 예외 없음
        assert result.trades == ()
        assert result.metrics.total_return_pct == Decimal("0")
        # strategy_factory 필드 확인은 별도 케이스 — 여기선 engine 동작만 검증
        _ = ORBStrategy  # 타입 참조만 (미사용 경고 억제)

    def test_strategy_factory_custom_strategy_runs(self):
        """factory 가 _FakeStrategy 반환 시 monkeypatch 없이 run() 정상 완료.

        RED: BacktestConfig 에 strategy_factory 필드 없음 → TypeError 로 FAIL.
        """
        from stock_agent.strategy import EntrySignal as _ES  # noqa: N814
        from stock_agent.strategy import ExitSignal as _XS  # noqa: N814
        from stock_agent.strategy import StrategyConfig as _SC  # noqa: N814

        class _StubStrategy:
            """Strategy Protocol 을 최소로 구현한 더미. force_close_at 노출 필수."""

            def __init__(self) -> None:
                self._config = _SC()

            @property
            def config(self) -> _SC:
                return self._config

            def on_bar(self, _: object) -> list[object]:
                return []

            def on_time(self, _: object) -> list[object]:
                return []

        cfg = BacktestConfig(
            starting_capital_krw=1_000_000,
            strategy_factory=_StubStrategy,  # type: ignore[call-arg]
        )
        engine = BacktestEngine(cfg)
        result = engine.run([])
        assert result.trades == ()
        _ = _ES, _XS  # noqa: F841


class TestEngineEmptyStream:
    def test_빈_분봉_스트림(self):
        """빈 bar 스트림 → trades=(), daily_equity=(), 모든 메트릭 0."""
        engine = _default_engine()
        result = engine.run([])

        assert result.trades == ()
        assert result.daily_equity == ()
        assert result.metrics.total_return_pct == Decimal("0")
        assert result.metrics.max_drawdown_pct == Decimal("0")
        assert result.metrics.sharpe_ratio == Decimal("0")
        assert result.metrics.win_rate == Decimal("0")
        assert result.metrics.avg_pnl_ratio == Decimal("0")
        assert result.metrics.trades_per_day == Decimal("0")
        assert result.metrics.net_pnl_krw == 0
        assert result.rejected_counts == {}


class TestEngineSingleTrade:
    """시나리오: 005930, 자본 1_000_000, 기본 비용 (슬리피지 0.1%, 수수료 0.015%, 세금 0.18%).

    OR: 09:00 high=70500, low=69800
    09:30 close=71000 → 진입
    qty = int(1_000_000 * 0.20 / 71000) = int(200000/71000) = 2
    entry_fill = 71000 * 1.001 = 71071.000
    entry_notional_int = int(2 * 71071.000) = 142142
    buy_comm = int(142142 * 0.00015) = 21
    cash_after_entry = 1_000_000 - 142142 - 21 = 857_837
    """

    def _or_and_entry_bars(self) -> list[MinuteBar]:
        """OR 구간 1개 + 진입 bar."""
        return [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000),
            _bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000),  # close=71000 > or_high=70500
        ]

    def test_단일_익절_정확값(self):
        """익절 시나리오 — net_pnl, exit_reason, daily_equity 정확값 검증.

        take_price = 71000 * 1.030 = 73130
        09:32 bar high=73130 → 익절 (signal.price = 73130)
        exit_fill = 73130 * 0.999 = 73056.870
        exit_notional_int = int(2 * 73056.870) = 146113
        sell_comm = int(146113 * 0.00015) = 21
        sell_tax = int(146113 * 0.0018) = 263
        gross = 146113 - 142142 = 3971
        net = 3971 - (21+21) - 263 = 3666
        daily_equity = 1_000_000 - 142142 - 21 + 146113 - 21 - 263 = 1_003_666
        """
        bars = self._or_and_entry_bars() + [
            _bar(_SYMBOL, 9, 31, 71000, 72000, 70900, 71100),  # 손절·익절 미성립
            _bar(_SYMBOL, 9, 32, 71100, 73130, 71000, 71200),  # high=73130 → 익절
        ]
        engine = _default_engine()
        result = engine.run(bars)

        assert len(result.trades) == 1, "trade 1건이어야 한다"
        trade = result.trades[0]

        assert trade.symbol == _SYMBOL
        assert trade.exit_reason == "take_profit"
        assert trade.qty == 2
        assert trade.gross_pnl_krw == 3971, f"gross_pnl={trade.gross_pnl_krw}"
        assert trade.commission_krw == 42, f"commission={trade.commission_krw}"
        assert trade.tax_krw == 263, f"tax={trade.tax_krw}"
        assert trade.net_pnl_krw == 3666, f"net_pnl={trade.net_pnl_krw}"

        assert len(result.daily_equity) == 1
        eq = result.daily_equity[0].equity_krw
        assert eq == 1_003_666, f"daily_equity={eq}"

    def test_단일_손절_exit_reason_및_음수_pnl(self):
        """손절 시나리오 — exit_reason="stop_loss", net_pnl < 0.

        stop_price = 71000 * 0.985 = 69935
        09:32 bar low=69935 → 손절 (signal.price = 69935)
        """
        bars = self._or_and_entry_bars() + [
            _bar(_SYMBOL, 9, 31, 71000, 71200, 70900, 71100),
            _bar(_SYMBOL, 9, 32, 71000, 71100, 69935, 70500),  # low=69935 → 손절
        ]
        engine = _default_engine()
        result = engine.run(bars)

        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.exit_reason == "stop_loss"
        assert trade.net_pnl_krw < 0, "손절이므로 net_pnl 은 음수여야 한다"

    def test_단일_force_close_15시_이후_분봉(self):
        """15:00 이후 분봉 도달 → force_close. exit_reason="force_close"."""
        bars = self._or_and_entry_bars() + [
            _bar(_SYMBOL, 9, 31, 71000, 72000, 70900, 71100),
            _bar(_SYMBOL, 15, 0, 71100, 71500, 71000, 71200),  # 15:00 → force_close
        ]
        engine = _default_engine()
        result = engine.run(bars)

        assert len(result.trades) == 1
        assert result.trades[0].exit_reason == "force_close"

    def test_force_close_세션_마감_훅_14시30분_마지막_분봉(self):
        """진입 후 14:30 분봉이 마지막 → 세션 마감 훅이 force_close 처리.

        15:00 분봉이 스트림에 없더라도 _close_session 이 force_close 를 발생시킨다.
        """
        bars = self._or_and_entry_bars() + [
            _bar(_SYMBOL, 9, 31, 71000, 72000, 70900, 71100),
            _bar(_SYMBOL, 14, 30, 71100, 71500, 71000, 71200),  # 마지막 분봉
        ]
        engine = _default_engine()
        result = engine.run(bars)

        assert len(result.trades) == 1, "세션 마감 훅이 force_close 해야 한다"
        assert result.trades[0].exit_reason == "force_close"
        assert len(result.daily_equity) == 1
        assert result.daily_equity[0].equity_krw > 0


class TestEngineMultiSymbol:
    def test_다중_종목_max_positions_한도(self):
        """4종목 동시 OR 돌파 → max_positions=2 → 2건 진입, 2건 phantom_long 거부.

        phantom_longs 메커니즘으로 거부된 2종목의 후속 ExitSignal 을 안전하게
        흡수. trades=2, rejected_counts["max_positions_reached"]==2.

        스트림 순서 (시간 정렬):
          09:00  A,B,C,D OR 누적 (or_high=70500)
          09:30  A,B,C,D 진입 시도 — A·B 승인, C·D 거부(phantom_long)
          09:31  A,B 익절조건(high=73130) + C,D 익절조건 → C,D phantom 흡수
        """
        syms = [_SYMBOL, _SYMBOL_B, _SYMBOL_C, _SYMBOL_D]
        # take_price = 71000 * 1.030 = 73130
        take_h = 73130

        # OR 구간 (09:00) — 4종목
        or_bars = [_bar(s, 9, 0, 70000, 70500, 69800, 70000) for s in syms]
        # 09:30 진입 bar — 4종목 모두 close=71000 > or_high=70500
        entry_bars = [_bar(s, 9, 30, 70200, 71500, 70100, 71000) for s in syms]
        # 09:31 — 4종목 모두 high=take_h
        #   A,B: active → TradeRecord 생성
        #   C,D: phantom_long → ExitSignal 흡수, trade 없음
        exit_bars = [_bar(s, 9, 31, 71100, take_h, 71000, 71200) for s in syms]

        all_bars = or_bars + entry_bars + exit_bars

        engine = _default_engine(
            capital=4_000_000,
            risk_config=RiskConfig(max_positions=2),
        )
        result = engine.run(all_bars)

        trade_count = len(result.trades)
        assert trade_count == 2, f"max_positions=2 → 2건 (실제={trade_count})"
        rejected = result.rejected_counts.get("max_positions_reached", 0)
        assert rejected == 2, f"max_positions_reached 거부 2건 (실제={result.rejected_counts})"

    def test_서킷브레이커_이후_진입_거부(self):
        """첫 거래 큰 손실 → halt → 두 번째 진입 시도 halted_daily_loss 거부.

        phantom_longs 로 거부된 두 번째 심볼의 세션 마감 force_close 를 흡수.

        수치 설계:
          자본 10_000_000, daily_loss_limit_pct=0.001 → threshold=-10_000
          A 심볼: or_high=100_500, 진입 close=101_000
            qty = int(10_000_000*0.20 / 101_000) = int(2_000_000/101_000) = 19
            entry_fill = 101_000 * 1.001 = 101_101.000
            entry_notional = int(19 * 101_101) = 1_920_919
            buy_comm = int(1_920_919 * 0.00015) = 288
            stop_price = 101_000 * 0.985 = 99_485
            low=99_485 → 손절
            exit_fill = 99_485 * 0.999 = 99_385.515
            exit_notional = int(19 * 99_385.515) = 1_888_324
            sell_comm = int(1_888_324 * 0.00015) = 283
            sell_tax  = int(1_888_324 * 0.0018)  = 3_398
            gross = 1_888_324 - 1_920_919 = -32_595
            net   = -32_595 - (288+283) - 3_398 = -36_564
            -36_564 ≤ -10_000 → halt 발동

          B 심볼: halt 후 진입 시도 → halted_daily_loss 거부 + phantom_long
            세션 마감 force_close ExitSignal → phantom 흡수.
        """
        capital = 10_000_000
        risk_cfg = RiskConfig(daily_loss_limit_pct=Decimal("0.001"))

        # A 심볼: OR(09:00) → 진입(09:30, close=101_000) → 손절(09:31, low=99_485)
        a_or = _bar(_SYMBOL, 9, 0, 100_000, 100_500, 99_800, 100_000)
        a_entry = _bar(_SYMBOL, 9, 30, 100_200, 101_500, 100_100, 101_000)
        a_stop = _bar(_SYMBOL, 9, 31, 101_000, 101_100, 99_485, 100_500)

        # B 심볼: OR(09:00) → 진입 시도(09:32, halt 후) → halted_daily_loss 거부 + phantom_long
        # 세션 마감 훅이 B 에 force_close ExitSignal 발생 → phantom 흡수
        b_or = _bar(_SYMBOL_B, 9, 0, 100_000, 100_500, 99_800, 100_000)
        b_entry = _bar(_SYMBOL_B, 9, 32, 100_200, 101_500, 100_100, 101_000)

        all_bars = [a_or, b_or, a_entry, a_stop, b_entry]

        engine = BacktestEngine(
            BacktestConfig(
                starting_capital_krw=capital,
                risk_config=risk_cfg,
            )
        )
        result = engine.run(all_bars)

        # A 손절 확인
        assert any(t.exit_reason == "stop_loss" for t in result.trades), "A 심볼 손절 trade 가 없다"
        # halted_daily_loss 거부 확인
        halt_count = result.rejected_counts.get("halted_daily_loss", 0)
        assert halt_count >= 1, f"halted_daily_loss 거부 없음 (rejected={result.rejected_counts})"


class TestEngineMultiSession:
    def test_세션_경계_D_plus_1_복리_재진입(self):
        """2일치 분봉 — 첫날 익절 → 둘째날 새 세션, 복리 자본 기준 재진입.

        daily_equity 2건. 둘째날 시작 자본 = 첫날 종료 자본.
        """
        # 첫날 OR + 진입 + 익절
        day1_bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000, date_=_DATE),
            _bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000, date_=_DATE),
            _bar(_SYMBOL, 9, 32, 71100, 73130, 71000, 71200, date_=_DATE),  # take_profit
        ]
        # 둘째날 OR + 진입 + 익절
        day2_bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000, date_=_DATE2),
            _bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000, date_=_DATE2),
            _bar(_SYMBOL, 9, 32, 71100, 73130, 71000, 71200, date_=_DATE2),  # take_profit
        ]

        engine = _default_engine()
        result = engine.run(day1_bars + day2_bars)

        assert len(result.daily_equity) == 2, "2일 → DailyEquity 2건"
        day1_equity = result.daily_equity[0].equity_krw
        day2_equity = result.daily_equity[1].equity_krw

        # 첫날 수익이 있으므로 둘째날 자본 > 첫날 자본 (복리 반영)
        assert day2_equity > day1_equity, f"복리 실패: day2={day2_equity} day1={day1_equity}"
        assert len(result.trades) == 2, f"trade 2건이어야 한다 (실제={len(result.trades)})"

    def test_세션_경계_RiskManager_카운터_리셋(self):
        """D+1 세션 시작 시 RiskManager 카운터가 리셋돼 첫날 halt 가 둘째날에 영향 없음.

        첫날: A 심볼 손절로 halt 유도.
        둘째날: 새 세션에서 halt 리셋 → A 재진입·익절 가능.

        수치:
          자본 10_000_000, daily_loss_limit_pct=0.001 → threshold=-10_000
          A: or_high=100_500, 진입 close=101_000
            qty=19, entry_fill=101_101, entry_notional=1_920_919
            stop=99_485 → net ≈ -36_564 → halt
        """
        capital = 10_000_000
        risk_cfg = RiskConfig(daily_loss_limit_pct=Decimal("0.001"))

        # 첫날: OR + 진입 + 손절(halt)
        day1_bars = [
            _bar(_SYMBOL, 9, 0, 100_000, 100_500, 99_800, 100_000, date_=_DATE),
            _bar(_SYMBOL, 9, 30, 100_200, 101_500, 100_100, 101_000, date_=_DATE),
            _bar(_SYMBOL, 9, 31, 101_000, 101_100, 99_485, 100_500, date_=_DATE),
        ]
        # 둘째날: OR + 진입 + 익절 — halt 리셋으로 진입 가능해야 함
        day2_bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000, date_=_DATE2),
            _bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000, date_=_DATE2),
            _bar(_SYMBOL, 9, 32, 71100, 73130, 71000, 71200, date_=_DATE2),  # take_profit
        ]

        engine = BacktestEngine(
            BacktestConfig(
                starting_capital_krw=capital,
                risk_config=risk_cfg,
            )
        )
        result = engine.run(day1_bars + day2_bars)

        assert len(result.daily_equity) == 2, "2일 세션이어야 한다"
        day2_trades = [t for t in result.trades if t.entry_ts.date() == _DATE2]
        assert len(day2_trades) >= 1, "둘째날 거래 없음 — halt 가 세션 경계 이후에도 유지되는 버그"


class TestEngineTimeOrdering:
    def test_시간_역행_분봉_RuntimeError(self):
        """bar_time 이 역행하는 분봉 스트림 → RuntimeError."""
        bars = [
            _bar(_SYMBOL, 9, 31, 70000, 70500, 69800, 70200),
            _bar(_SYMBOL, 9, 30, 70000, 70500, 69800, 70100),  # 역행
        ]
        engine = _default_engine()
        with pytest.raises(RuntimeError):
            engine.run(bars)


class TestEngineForceCloseIdempotency:
    def test_force_close_후_추가_분봉_trade_미중복(self):
        """15:00 force_close 후 추가 분봉이 들어와도 trade 가 중복되지 않는다.

        on_time 이 idempotent 이므로 동일 세션에서 두 번 호출돼도 closed 심볼은
        재청산되지 않는다.
        """
        bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000),
            _bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000),  # 진입
            _bar(_SYMBOL, 15, 0, 71100, 71500, 71000, 71200),  # force_close
            _bar(_SYMBOL, 15, 1, 71200, 71600, 71100, 71300),  # 추가 — 재청산 없어야
            _bar(_SYMBOL, 15, 2, 71300, 71700, 71200, 71400),  # 추가 — 재청산 없어야
        ]
        engine = _default_engine()
        result = engine.run(bars)

        tc = len(result.trades)
        assert tc == 1, f"force_close 이후 trade 중복 금지 (실제={tc})"
        assert result.trades[0].exit_reason == "force_close"


class TestEngineTradeRecordIntegrity:
    def test_trade_record_필드_타입_정합성(self):
        """TradeRecord 의 각 필드가 계약된 타입을 가진다."""
        bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000),
            _bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000),
            _bar(_SYMBOL, 9, 32, 71100, 73130, 71000, 71200),
        ]
        engine = _default_engine()
        result = engine.run(bars)

        assert len(result.trades) == 1
        t = result.trades[0]

        assert isinstance(t, TradeRecord)
        assert isinstance(t.symbol, str)
        assert isinstance(t.entry_ts, datetime)
        assert isinstance(t.exit_ts, datetime)
        assert isinstance(t.entry_price, Decimal)
        assert isinstance(t.exit_price, Decimal)
        assert isinstance(t.qty, int)
        assert isinstance(t.gross_pnl_krw, int)
        assert isinstance(t.commission_krw, int)
        assert isinstance(t.tax_krw, int)
        assert isinstance(t.net_pnl_krw, int)

    def test_trade_record_net_pnl_정합성(self):
        """net_pnl == gross - commission - tax 불변식 검증."""
        bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000),
            _bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000),
            _bar(_SYMBOL, 9, 32, 71100, 73130, 71000, 71200),
        ]
        engine = _default_engine()
        result = engine.run(bars)

        t = result.trades[0]
        expected_net = t.gross_pnl_krw - t.commission_krw - t.tax_krw
        net, gross, comm, tax = t.net_pnl_krw, t.gross_pnl_krw, t.commission_krw, t.tax_krw
        assert t.net_pnl_krw == expected_net, f"net={net} gross={gross} comm={comm} tax={tax}"

    def test_daily_equity_세션_날짜_정합성(self):
        """DailyEquity.session_date 가 실제 bar 의 날짜와 일치한다."""
        bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000),
            _bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000),
        ]
        engine = _default_engine()
        result = engine.run(bars)

        assert len(result.daily_equity) == 1
        assert isinstance(result.daily_equity[0], DailyEquity)
        assert result.daily_equity[0].session_date == _DATE

    def test_entry_exit_ts_tz_aware(self):
        """TradeRecord 의 entry_ts·exit_ts 는 tz-aware datetime 이다."""
        bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000),
            _bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000),
            _bar(_SYMBOL, 9, 32, 71100, 73130, 71000, 71200),
        ]
        engine = _default_engine()
        result = engine.run(bars)

        t = result.trades[0]
        assert t.entry_ts.tzinfo is not None, "entry_ts 가 naive datetime"
        assert t.exit_ts.tzinfo is not None, "exit_ts 가 naive datetime"


class TestEngineMetricsComputation:
    def test_메트릭_총수익률_1거래_익절(self):
        """익절 1건 → total_return_pct > 0."""
        bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000),
            _bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000),
            _bar(_SYMBOL, 9, 32, 71100, 73130, 71000, 71200),
        ]
        engine = _default_engine()
        result = engine.run(bars)

        assert result.metrics.total_return_pct > Decimal("0"), "익절 후 총수익률이 양수가 아니다"

    def test_메트릭_손절_후_음수_net_pnl(self):
        """손절 → metrics.net_pnl_krw < 0."""
        bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000),
            _bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000),
            _bar(_SYMBOL, 9, 32, 71000, 71100, 69935, 70500),  # 손절
        ]
        engine = _default_engine()
        result = engine.run(bars)

        assert result.metrics.net_pnl_krw < 0, "손절 후 net_pnl_krw 가 음수가 아니다"

    def test_메트릭_win_rate_1건_익절(self):
        """익절 1건만 → win_rate == 1."""
        bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000),
            _bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000),
            _bar(_SYMBOL, 9, 32, 71100, 73130, 71000, 71200),
        ]
        engine = _default_engine()
        result = engine.run(bars)

        assert result.metrics.win_rate == Decimal("1"), f"win_rate={result.metrics.win_rate}"

    def test_메트릭_trades_per_day_1일_1거래(self):
        """1일 1거래 → trades_per_day == 1."""
        bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000),
            _bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000),
            _bar(_SYMBOL, 9, 32, 71100, 73130, 71000, 71200),
        ]
        engine = _default_engine()
        result = engine.run(bars)

        tpd = result.metrics.trades_per_day
        assert tpd == Decimal("1"), f"trades_per_day={tpd}"


class TestEngineSafetyNet:
    """커버리지 누락 분기 회귀 테스트 — PR #10 코드 리뷰 지적 4건."""

    # ------------------------------------------------------------------
    # 1. 사후 슬리피지 거부 — post_slippage_rejections 카운터 분리 검증
    # ------------------------------------------------------------------

    def test_사후_슬리피지_거부_분기(self):
        """RiskManager 승인 후 슬리피지·수수료 반영 시 잔액 초과 → 사후 거부.

        RiskManager 사전 판정은 참고가(signal.price) 기준 filled_notional 을 사용하므로
        cash == filled_notional 이면 사전 통과된다. 하지만 엔진이 슬리피지를 반영한
        entry_fill 로 총비용(notional_int + buy_comm)을 재계산하면 cash 를 초과할 수 있다.

        수치:
          capital=10_000, position_pct=1.0, slippage=0.001
          OR bar: or_high=9_500, entry close=10_000
          qty = int(10_000 × 1.0 / 10_000) = 1
          filled_notional(참고가) = 10_000 ≤ cash=10_000 → 사전 통과
          entry_fill = 10_000 × 1.001 = 10_010
          notional_int = 10_010, buy_comm = 1 → total_cost = 10_011 > 10_000 → 사후 거부
        """
        from stock_agent.risk import RiskConfig

        # 수치 설계:
        #   capital=10_000, position_pct=1.0, slippage=0.001
        #   OR bar: high=9_500 → or_high=9_500
        #   entry bar: close=10_000 (> or_high → 진입 조건 성립)
        #   price_signal=10_000
        #   qty = int(10_000 * 1.0 / 10_000) = 1
        #   filled_notional(참고가 기준) = 1 * 10_000 = 10_000
        #   → 사전 insufficient_cash: 10_000 > 10_000 = False → 사전 통과
        #   entry_fill = 10_000 * 1.001 = 10_010
        #   notional_int = 10_010, buy_comm = 1
        #   total_cost = 10_011 > cash=10_000 → 사후 거부 트리거
        capital = 10_000

        bars = [
            _bar(_SYMBOL, 9, 0, 9_000, 9_500, 8_800, 9_200),  # OR bar: or_high=9_500
            _bar(_SYMBOL, 9, 30, 9_500, 10_200, 9_400, 10_000),  # close=10_000 > or_high → 진입
        ]

        engine = BacktestEngine(
            BacktestConfig(
                starting_capital_krw=capital,
                risk_config=RiskConfig(
                    position_pct=Decimal("1.0"),
                    max_positions=3,
                    min_notional_krw=1,
                ),
                slippage_rate=Decimal("0.001"),
            )
        )
        result = engine.run(bars)

        psr = result.post_slippage_rejections
        assert psr == 1, f"사후 슬리피지 거부 1건이어야 한다 (실제={psr})"
        icash = result.rejected_counts.get("insufficient_cash", 0)
        assert icash == 0, f"insufficient_cash must be 0 (rejected={result.rejected_counts})"
        assert len(result.trades) == 0, "사후 거부이므로 체결 trade 가 없어야 한다"
        # 세션 마감 정상 종료 — phantom_long 흡수 확인 (RuntimeError 미발생이 곧 검증)
        assert len(result.daily_equity) == 1, "세션은 정상 마감되어야 한다"

    # ------------------------------------------------------------------
    # 2. phantom_long 세션 마감 정상 흡수
    # ------------------------------------------------------------------

    def test_phantom_long_세션_마감_정상_종결(self):
        """서킷브레이커 halt 후 두 번째 종목 phantom_long → 세션 마감 force_close 흡수.

        D1: SYM_A 큰 손실 → halt. SYM_B OR 돌파 → halted_daily_loss 거부 + phantom_long.
            SYM_B 분봉이 stop/take 미도달 → 세션 마감 훅 on_time(15:00) 으로 phantom 흡수.
        D2: 새 세션(halt 리셋) → SYM_B OR 돌파 + 익절 정상 진입.

        수치 (D1):
          자본 10_000_000, daily_loss_limit_pct=0.001 → threshold=-10_000
          SYM_A: or_high=100_500, 진입 close=101_000
            qty=19, entry_fill=101_101, entry_notional=1_920_919
            stop=99_485 → net≈-36_564 → halt
          SYM_B: halt 후 OR 돌파 → halted_daily_loss 거부 + phantom_long
            SYM_B 분봉 10:00, 11:00 → stop/take 미도달
            세션 마감 훅(15:00) → on_time 이 SYM_B force_close ExitSignal 발생 → phantom 흡수
        D2: SYM_B OR + 진입 + 익절.
        """
        capital = 10_000_000
        risk_cfg = RiskConfig(daily_loss_limit_pct=Decimal("0.001"))

        # D1: SYM_A
        a_or = _bar(_SYMBOL, 9, 0, 100_000, 100_500, 99_800, 100_000, date_=_DATE)
        a_entry = _bar(_SYMBOL, 9, 30, 100_200, 101_500, 100_100, 101_000, date_=_DATE)
        a_stop = _bar(_SYMBOL, 9, 31, 101_000, 101_100, 99_485, 100_500, date_=_DATE)

        # D1: SYM_B — OR 설정 후 halt 발생 이후 돌파 시도, stop/take 미도달 유지
        b_or_d1 = _bar(_SYMBOL_B, 9, 0, 100_000, 100_500, 99_800, 100_000, date_=_DATE)
        # halt 이후 SYM_B 진입 시도: close > or_high
        b_entry_d1 = _bar(_SYMBOL_B, 9, 32, 100_200, 101_500, 100_100, 101_000, date_=_DATE)
        # stop(99_485)·take(101_000*1.03≈104_030) 미도달 분봉들
        b_mid1_d1 = _bar(_SYMBOL_B, 10, 0, 101_000, 101_200, 100_800, 101_000, date_=_DATE)
        b_mid2_d1 = _bar(_SYMBOL_B, 11, 0, 101_000, 101_200, 100_800, 101_000, date_=_DATE)
        # 14:30 마지막 분봉 (15:00 분봉 없음 → 세션 마감 훅이 처리)
        b_last_d1 = _bar(_SYMBOL_B, 14, 30, 101_000, 101_200, 100_800, 101_000, date_=_DATE)

        # D2: SYM_B 정상 진입 + 익절
        b_or_d2 = _bar(_SYMBOL_B, 9, 0, 70_000, 70_500, 69_800, 70_000, date_=_DATE2)
        b_entry_d2 = _bar(_SYMBOL_B, 9, 30, 70_200, 71_500, 70_100, 71_000, date_=_DATE2)
        b_take_d2 = _bar(_SYMBOL_B, 9, 32, 71_100, 73_130, 71_000, 71_200, date_=_DATE2)

        all_bars = [
            a_or,
            b_or_d1,
            a_entry,
            a_stop,
            b_entry_d1,
            b_mid1_d1,
            b_mid2_d1,
            b_last_d1,
            b_or_d2,
            b_entry_d2,
            b_take_d2,
        ]

        engine = BacktestEngine(BacktestConfig(starting_capital_krw=capital, risk_config=risk_cfg))
        # RuntimeError 없이 완료되어야 함 (phantom_long 정상 흡수 검증)
        result = engine.run(all_bars)

        # D1: SYM_A 손절 trade 확인
        d1_trades = [t for t in result.trades if t.entry_ts.date() == _DATE]
        has_sl = any(t.symbol == _SYMBOL and t.exit_reason == "stop_loss" for t in d1_trades)
        assert has_sl, "D1 SYM_A 손절 trade 없음"
        # D1: SYM_B trade 없음 (phantom_long 흡수, 거래 기록 없음)
        assert not any(t.symbol == _SYMBOL_B for t in d1_trades), "D1 SYM_B phantom_long → no trade"
        # D2: SYM_B 정상 진입 trade 확인
        d2_trades = [t for t in result.trades if t.entry_ts.date() == _DATE2]
        assert any(t.symbol == _SYMBOL_B for t in d2_trades), "D2 SYM_B halt 리셋 후 정상 진입 없음"
        assert len(result.daily_equity) == 2, "2일 세션 → DailyEquity 2건"
        # halted_daily_loss 거부 1건 확인
        halt_count2 = result.rejected_counts.get("halted_daily_loss", 0)
        assert halt_count2 >= 1, f"halted_daily_loss 거부 없음 (rejected={result.rejected_counts})"

    # ------------------------------------------------------------------
    # 3. _handle_exit 안전망 — 미보유·비phantom 심볼 ExitSignal → RuntimeError
    # ------------------------------------------------------------------

    def test_handle_exit_미보유_심볼_RuntimeError(self, monkeypatch):
        """진입 없이 ExitSignal 이 도달하면 RuntimeError("활성 포지션 없음").

        stub ORBStrategy: 첫 on_bar 에서 즉시 ExitSignal 반환,
        EntrySignal 없음 → active 에 없고 phantom_longs 에도 없음 → 안전망 발동.
        """
        from stock_agent.strategy import ExitSignal as _ExitSignal
        from stock_agent.strategy import StrategyConfig as _StrategyConfig

        class _FakeStrategy:
            def __init__(self, config=None):
                self._config = config or _StrategyConfig()
                self._fired = False

            @property
            def config(self):
                return self._config

            def on_bar(self, bar):
                if not self._fired:
                    self._fired = True
                    # 진입 없이 ExitSignal 발생 → _handle_exit 안전망 트리거
                    return [
                        _ExitSignal(
                            symbol=bar.symbol,
                            price=bar.close,
                            ts=bar.bar_time,
                            reason="stop_loss",
                        )
                    ]
                return []

            def on_time(self, _):
                return []

        monkeypatch.setattr("stock_agent.backtest.engine.ORBStrategy", _FakeStrategy)

        bars = [_bar(_SYMBOL, 9, 0, 70_000, 70_500, 69_800, 70_000)]
        engine = _default_engine()

        with pytest.raises(RuntimeError, match="활성 포지션 없음"):
            engine.run(bars)

    # ------------------------------------------------------------------
    # 4. _close_session 안전망 — active 잔존 → RuntimeError
    # ------------------------------------------------------------------

    def test_close_session_active_잔존_RuntimeError(self, monkeypatch):
        """on_time 이 항상 빈 리스트 반환 → 세션 마감 후 active 잔존 → RuntimeError.

        stub ORBStrategy: on_bar 에서 EntrySignal 을 발생시켜 진입은 되지만,
        on_time 은 항상 [] 반환 → force_close ExitSignal 미생성 → active 남음.
        _close_session 의 `if active: raise RuntimeError(...)` 발동.
        """
        from stock_agent.strategy import EntrySignal as _EntrySignal
        from stock_agent.strategy import StrategyConfig as _StrategyConfig

        class _NoForceCloseStrategy:
            def __init__(self, config=None):
                self._config = config or _StrategyConfig()
                self._fired = False

            @property
            def config(self):
                return self._config

            def on_bar(self, bar):
                if not self._fired:
                    self._fired = True
                    # 정상 EntrySignal 발생 → 진입 승인 → active 등록
                    return [
                        _EntrySignal(
                            symbol=bar.symbol,
                            price=bar.close,
                            ts=bar.bar_time,
                            stop_price=bar.close * Decimal("0.985"),
                            take_price=bar.close * Decimal("1.030"),
                        )
                    ]
                return []

            def on_time(self, _):
                # force_close ExitSignal 미생성 → active 잔존 → 안전망 트리거
                return []

        monkeypatch.setattr("stock_agent.backtest.engine.ORBStrategy", _NoForceCloseStrategy)

        bars = [
            _bar(_SYMBOL, 9, 0, 70_000, 70_500, 69_800, 70_000),
            # close=71_000 → EntrySignal (stub은 bar_time 무관 첫 on_bar에서 발생)
            _bar(_SYMBOL, 9, 30, 70_200, 71_500, 70_100, 71_000),
            # stop/take 미도달 → 세션 마감까지 포지션 유지
            _bar(_SYMBOL, 14, 30, 71_000, 71_200, 70_800, 71_000),
        ]
        engine = _default_engine(capital=1_000_000)

        with pytest.raises(RuntimeError, match="세션 마감 후에도 활성 포지션 잔존"):
            engine.run(bars)


class TestEngineExactArithmetic:
    def test_익절_정확_수치_전체_검증(self):
        """CLAUDE.md 시나리오 정확값 재현 — 모든 비용 단계별 검증.

        자본 1_000_000, 005930
        OR: 09:00 high=70500, low=69800
        진입: 09:30 close=71000
          qty=2, entry_fill=71071, entry_notional=142142, buy_comm=21
          cash_after_entry = 1_000_000 - 142142 - 21 = 857_837
        익절: take_price=73130, 09:32 high=73130
          exit_fill=73056.870, exit_notional=146113
          sell_comm=21, sell_tax=263
          gross=3971, net=3666
          cash_final = 857_837 + 146113 - 21 - 263 = 1_003_666
        """
        bars = [
            _bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70000),
            _bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000),
            _bar(_SYMBOL, 9, 31, 71000, 72000, 70900, 71100),
            _bar(_SYMBOL, 9, 32, 71100, 73130, 71000, 71200),
        ]
        engine = _default_engine(capital=1_000_000)
        result = engine.run(bars)

        t = result.trades[0]
        eq = result.daily_equity[0]

        assert t.qty == 2, f"qty={t.qty}"
        assert t.exit_reason == "take_profit"
        assert t.gross_pnl_krw == 3971, f"gross={t.gross_pnl_krw}"
        assert t.commission_krw == 42, f"commission={t.commission_krw}"
        assert t.tax_krw == 263, f"tax={t.tax_krw}"
        assert t.net_pnl_krw == 3666, f"net_pnl={t.net_pnl_krw}"
        assert eq.equity_krw == 1_003_666, f"equity={eq.equity_krw}"
        mnp = result.metrics.net_pnl_krw
        assert mnp == 3666, f"metrics.net_pnl_krw={mnp}"
