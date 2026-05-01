"""build_strategy_factory 헬퍼 및 STRATEGY_CHOICES 상수 단위 테스트 (RED 단계).

대상 모듈: src/stock_agent/strategy/factory.py (미작성 — ImportError 로 FAIL 예상).
외부 네트워크·DB·시계 의존 없음 — 순수 로직 검증.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from stock_agent.data import MinuteBar
from stock_agent.strategy import (
    GapReversalConfig,
    GapReversalStrategy,
    ORBStrategy,
    StrategyConfig,
    VWAPMRConfig,
    VWAPMRStrategy,
)

# factory 모듈은 미존재 — 아래 import 자체가 ImportError 로 FAIL 되어야 한다.
from stock_agent.strategy.factory import (
    STRATEGY_CHOICES,
    build_strategy_factory,
)

# ---------------------------------------------------------------------------
# 공통 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))
_SYMBOL = "005930"
_DATE = date(2026, 4, 20)


def _bar(
    h: int,
    m: int,
    open_: int | str | Decimal,
    high: int | str | Decimal,
    low: int | str | Decimal,
    close: int | str | Decimal,
    *,
    volume: int = 1000,
) -> MinuteBar:
    """MinuteBar 생성 헬퍼. h/m 은 KST 시·분."""
    return MinuteBar(
        symbol=_SYMBOL,
        bar_time=datetime(_DATE.year, _DATE.month, _DATE.day, h, m, tzinfo=KST),
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=volume,
    )


# ---------------------------------------------------------------------------
# 1. STRATEGY_CHOICES 상수
# ---------------------------------------------------------------------------


class TestStrategyChoices:
    def test_STRATEGY_CHOICES_정확히_3종_고정_순서(self) -> None:
        assert STRATEGY_CHOICES == ("orb", "vwap-mr", "gap-reversal")

    def test_STRATEGY_CHOICES_길이_3(self) -> None:
        assert len(STRATEGY_CHOICES) == 3


# ---------------------------------------------------------------------------
# 2. build_strategy_factory("orb")
# ---------------------------------------------------------------------------


class TestBuildStrategyFactoryOrb:
    def test_orb_반환값_callable(self) -> None:
        factory = build_strategy_factory("orb")
        assert callable(factory)

    def test_orb_호출_결과_ORBStrategy_인스턴스(self) -> None:
        factory = build_strategy_factory("orb")
        strategy = factory()
        assert isinstance(strategy, ORBStrategy)

    def test_orb_strategy_config_주입_stop_loss_반영(self) -> None:
        cfg = StrategyConfig(stop_loss_pct=Decimal("0.02"))
        factory = build_strategy_factory("orb", strategy_config=cfg)
        strategy = factory()
        assert isinstance(strategy, ORBStrategy)
        assert strategy._config.stop_loss_pct == Decimal("0.02")

    def test_orb_두번_호출_다른_인스턴스(self) -> None:
        """동일 팩토리를 두 번 호출하면 독립된 인스턴스를 반환해야 한다."""
        factory = build_strategy_factory("orb")
        s1 = factory()
        s2 = factory()
        assert s1 is not s2


# ---------------------------------------------------------------------------
# 3. build_strategy_factory("vwap-mr")
# ---------------------------------------------------------------------------


class TestBuildStrategyFactoryVwapMr:
    def test_vwap_mr_반환값_callable(self) -> None:
        factory = build_strategy_factory("vwap-mr")
        assert callable(factory)

    def test_vwap_mr_호출_결과_VWAPMRStrategy_인스턴스(self) -> None:
        factory = build_strategy_factory("vwap-mr")
        strategy = factory()
        assert isinstance(strategy, VWAPMRStrategy)

    def test_vwap_mr_config_주입_threshold_반영(self) -> None:
        cfg = VWAPMRConfig(threshold_pct=Decimal("0.02"))
        factory = build_strategy_factory("vwap-mr", vwap_mr_config=cfg)
        strategy = factory()
        assert isinstance(strategy, VWAPMRStrategy)
        assert strategy.config.threshold_pct == Decimal("0.02")

    def test_vwap_mr_두번_호출_다른_인스턴스(self) -> None:
        factory = build_strategy_factory("vwap-mr")
        s1 = factory()
        s2 = factory()
        assert s1 is not s2


# ---------------------------------------------------------------------------
# 4. build_strategy_factory("gap-reversal")
# ---------------------------------------------------------------------------


class TestBuildStrategyFactoryGapReversal:
    def test_gap_reversal_반환값_callable(self) -> None:
        factory = build_strategy_factory("gap-reversal")
        assert callable(factory)

    def test_gap_reversal_호출_결과_GapReversalStrategy_인스턴스(self) -> None:
        factory = build_strategy_factory("gap-reversal")
        strategy = factory()
        assert isinstance(strategy, GapReversalStrategy)

    def test_gap_reversal_prev_close_provider_미주입_stub_폴백_진입_없음(self) -> None:
        """stub provider 는 항상 None 반환 → 첫 분봉 on_bar 에서 진입 시그널 0."""
        factory = build_strategy_factory("gap-reversal")
        strategy = factory()
        # 갭 하락이 충분히 큰 시가(5% 하락)를 가진 첫 분봉 흘리기
        bar = _bar(9, 0, open_=95000, high=96000, low=94000, close=95500)
        signals = strategy.on_bar(bar)
        # prev_close=None 이므로 갭 평가 불가 → 진입 시그널 없음
        assert signals == []

    def test_gap_reversal_prev_close_provider_명시_주입_prev_close_반영(self) -> None:
        """명시 주입 provider 가 반환하는 값이 전략 내부 state.prev_close 에 반영되어야 한다."""

        def provider(_symbol, _d):
            return Decimal("70000")

        factory = build_strategy_factory("gap-reversal", prev_close_provider=provider)
        instance = factory()
        # 첫 분봉 수신 후 prev_close 가 설정되어야 함
        bar = _bar(9, 0, open_=70000, high=71000, low=69500, close=70000)
        instance.on_bar(bar)
        # GapReversalStrategy 로 좁혀야 get_state 접근 가능 (Strategy Protocol 에 없는 메서드)
        assert isinstance(instance, GapReversalStrategy)
        state = instance.get_state(_SYMBOL)
        assert state is not None
        assert state.prev_close == Decimal("70000")

    def test_gap_reversal_config_주입_gap_threshold_반영(self) -> None:
        cfg = GapReversalConfig(gap_threshold_pct=Decimal("0.05"))
        factory = build_strategy_factory("gap-reversal", gap_reversal_config=cfg)
        strategy = factory()
        assert isinstance(strategy, GapReversalStrategy)
        assert strategy.config.gap_threshold_pct == Decimal("0.05")

    def test_gap_reversal_두번_호출_다른_인스턴스(self) -> None:
        factory = build_strategy_factory("gap-reversal")
        s1 = factory()
        s2 = factory()
        assert s1 is not s2


# ---------------------------------------------------------------------------
# 5. 알 수 없는 strategy_type → RuntimeError
# ---------------------------------------------------------------------------


class TestBuildStrategyFactoryUnknownType:
    def test_알수없는_타입_RuntimeError(self) -> None:
        with pytest.raises(RuntimeError, match="unknown"):
            build_strategy_factory("unknown")  # type: ignore[arg-type]

    def test_빈_문자열_RuntimeError(self) -> None:
        with pytest.raises(RuntimeError):
            build_strategy_factory("")  # type: ignore[arg-type]

    def test_None_RuntimeError(self) -> None:
        with pytest.raises(RuntimeError):
            build_strategy_factory(None)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "bad_type",
        ["ORB", "Orb", "vwap_mr", "VWAP-MR", "gap_reversal"],
        ids=[
            "대문자ORB",
            "혼합Orb",
            "언더스코어vwap_mr",
            "대문자VWAP-MR",
            "언더스코어gap_reversal",
        ],
    )
    def test_유사하지만_잘못된_타입_RuntimeError(self, bad_type: str) -> None:
        with pytest.raises(RuntimeError):
            build_strategy_factory(bad_type)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 6. 반환 타입 — Strategy Protocol 호환성
# ---------------------------------------------------------------------------


class TestStrategyProtocolCompatibility:
    @pytest.mark.parametrize(
        "strategy_type",
        ["orb", "vwap-mr", "gap-reversal"],
        ids=["orb", "vwap-mr", "gap-reversal"],
    )
    def test_반환된_전략_on_bar_메서드_존재(self, strategy_type: str) -> None:
        factory = build_strategy_factory(strategy_type)  # type: ignore[arg-type]
        strategy = factory()
        assert hasattr(strategy, "on_bar") and callable(strategy.on_bar)

    @pytest.mark.parametrize(
        "strategy_type",
        ["orb", "vwap-mr", "gap-reversal"],
        ids=["orb", "vwap-mr", "gap-reversal"],
    )
    def test_반환된_전략_on_time_메서드_존재(self, strategy_type: str) -> None:
        factory = build_strategy_factory(strategy_type)  # type: ignore[arg-type]
        strategy = factory()
        assert hasattr(strategy, "on_time") and callable(strategy.on_time)

    def test_orb_on_bar_빈_리스트_반환_타입(self) -> None:
        """Strategy Protocol 계약: 시그널 없을 때 빈 list 반환 (None 아님)."""
        factory = build_strategy_factory("orb")
        strategy = factory()
        bar = _bar(9, 0, open_=70000, high=71000, low=69500, close=70000)
        result = strategy.on_bar(bar)
        assert isinstance(result, list)

    def test_vwap_mr_on_bar_빈_리스트_반환_타입(self) -> None:
        factory = build_strategy_factory("vwap-mr")
        strategy = factory()
        bar = _bar(9, 0, open_=70000, high=71000, low=69500, close=70000)
        result = strategy.on_bar(bar)
        assert isinstance(result, list)

    def test_gap_reversal_on_bar_빈_리스트_반환_타입(self) -> None:
        factory = build_strategy_factory("gap-reversal")
        strategy = factory()
        bar = _bar(9, 0, open_=70000, high=71000, low=69500, close=70000)
        result = strategy.on_bar(bar)
        assert isinstance(result, list)
