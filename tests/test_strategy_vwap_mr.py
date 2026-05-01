"""VWAPMRStrategy / VWAPMRConfig 공개 계약 단위 테스트 (RED 단계).

외부 네트워크·DB·시계 의존 없음 — 순수 로직 검증. 목킹 불필요.
대상 모듈: src/stock_agent/strategy/vwap_mr.py (아직 없음 — ImportError 로 FAIL 예상).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from stock_agent.data import MinuteBar
from stock_agent.strategy import (
    EntrySignal,
    ExitSignal,
    VWAPMRConfig,
    VWAPMRStrategy,
)

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_SYMBOL = "005930"
_DATE = date(2026, 4, 20)


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
    volume: int = 1000,
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


def _now(h: int, m: int, *, date_: date = _DATE) -> datetime:
    """KST aware datetime 헬퍼."""
    return datetime(date_.year, date_.month, date_.day, h, m, tzinfo=KST)


# ---------------------------------------------------------------------------
# 1. VWAP 누적
# ---------------------------------------------------------------------------


def test_vwap_단일_bar_volume_양수_vwap_equals_close():
    """volume > 0 인 bar 1개 주입 시 vwap == close."""
    strategy = VWAPMRStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10050, volume=500))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.vwap == Decimal("10050")


def test_vwap_다중_bar_가중평균_정확():
    """다중 bar 주입 시 VWAP = Σ(close×vol) / Σvol 와 일치."""
    strategy = VWAPMRStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=100))
    strategy.on_bar(_bar(_SYMBOL, 9, 6, 10000, 10200, 9800, 11000, volume=200))
    strategy.on_bar(_bar(_SYMBOL, 9, 7, 10000, 10300, 9700, 12000, volume=300))

    # (10000×100 + 11000×200 + 12000×300) / (100+200+300)
    # = (1000000 + 2200000 + 3600000) / 600
    # = 6800000 / 600 ≈ 11333.333...
    expected = (Decimal("10000") * 100 + Decimal("11000") * 200 + Decimal("12000") * 300) / (
        100 + 200 + 300
    )

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.vwap == pytest.approx(expected)


def test_vwap_volume_0_bar_무시_vwap_none_유지():
    """volume=0 인 bar 만 주입 시 vwap 는 None 유지 (sum_v=0)."""
    strategy = VWAPMRStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10050, volume=0))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.vwap is None


def test_vwap_누적_decimal_정밀도_손실_없음():
    """큰 수 가중평균 계산에서 Decimal 정밀도가 유지된다 (float 반올림 오류 없음)."""
    strategy = VWAPMRStrategy()
    # close=70000, vol=3 / close=70001, vol=7 → vwap = (210000 + 490007) / 10 = 70000.7
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 70000, 70100, 69900, 70000, volume=3))
    strategy.on_bar(_bar(_SYMBOL, 9, 6, 70000, 70100, 69900, 70001, volume=7))

    expected = (Decimal("70000") * 3 + Decimal("70001") * 7) / 10
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.vwap == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 2. 진입 시그널
# ---------------------------------------------------------------------------


def test_진입_거부_vwap_미확정_sum_v_0():
    """거래량 누적 없이(vwap None) 이탈 bar 주입 시 진입 거부 — 빈 리스트."""
    strategy = VWAPMRStrategy()
    # volume=0 bar 로 VWAP 확정 안 된 상태에서 이탈 가격 bar 주입
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10050, volume=0))
    result = strategy.on_bar(_bar(_SYMBOL, 9, 6, 9800, 9850, 9750, 9800, volume=0))

    assert result == []
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.position_state == "flat"


def test_진입_close_leq_vwap_곱_threshold_EntrySignal():
    """close ≤ vwap × (1 - threshold_pct) → EntrySignal 1건 + 상태 long 전이."""
    cfg = VWAPMRConfig(threshold_pct=Decimal("0.01"))
    strategy = VWAPMRStrategy(cfg)
    # VWAP 확정: close=10000, vol=1000 → vwap=10000
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=1000))
    # 이탈 진입: close ≤ 10000 × 0.99 = 9900
    result = strategy.on_bar(_bar(_SYMBOL, 9, 6, 9900, 9900, 9880, 9900, volume=500))

    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, EntrySignal)
    assert sig.symbol == _SYMBOL
    assert sig.price == Decimal("9900")

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.position_state == "long"


def test_진입_거부_close_gt_vwap_곱_threshold():
    """close > vwap × (1 - threshold_pct) 이면 진입 없음 — flat 유지."""
    cfg = VWAPMRConfig(threshold_pct=Decimal("0.01"))
    strategy = VWAPMRStrategy(cfg)
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=1000))
    # close=9901 > 9900 (threshold 경계 밖)
    result = strategy.on_bar(_bar(_SYMBOL, 9, 6, 9901, 9910, 9890, 9901, volume=500))

    assert result == []
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.position_state == "flat"


def test_진입_거부_force_close_at_이후():
    """bar_time >= force_close_at 이면 진입 없음 — force_close_at 이후 진입 금지."""
    cfg = VWAPMRConfig(threshold_pct=Decimal("0.01"), force_close_at=time(15, 0))
    strategy = VWAPMRStrategy(cfg)
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=1000))
    # force_close_at 이후 bar — 이탈해도 진입 금지
    result = strategy.on_bar(_bar(_SYMBOL, 15, 0, 9800, 9850, 9780, 9800, volume=500))

    assert result == []
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.position_state == "flat"


def test_진입_거부_session_start_미만_bar_skip():
    """bar_time < session_start 이면 누적도 진입도 없이 skip."""
    cfg = VWAPMRConfig(session_start=time(9, 0))
    strategy = VWAPMRStrategy(cfg)
    # 08:59 bar — skip 되어야 함
    strategy.on_bar(_bar(_SYMBOL, 8, 59, 10000, 10100, 9900, 10050, volume=1000))

    state = strategy.get_state(_SYMBOL)
    # 상태가 없거나 sum_v=0 이어야 함 (vwap None)
    if state is not None:
        assert state.vwap is None


def test_진입_EntrySignal_stop_take_price_정확():
    """EntrySignal 의 stop_price / take_price 가 설정값 기준으로 정확히 계산된다."""
    cfg = VWAPMRConfig(
        threshold_pct=Decimal("0.01"),
        stop_loss_pct=Decimal("0.015"),
        take_profit_pct=Decimal("0.005"),
    )
    strategy = VWAPMRStrategy(cfg)
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=1000))
    result = strategy.on_bar(_bar(_SYMBOL, 9, 6, 9900, 9900, 9880, 9900, volume=500))

    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, EntrySignal)

    entry = Decimal("9900")
    expected_stop = entry * (Decimal("1") - Decimal("0.015"))
    expected_take = entry * (Decimal("1") + Decimal("0.005"))
    assert sig.stop_price == expected_stop
    assert sig.take_price == expected_take


# ---------------------------------------------------------------------------
# 헬퍼: long 상태 셋업
# ---------------------------------------------------------------------------


def _setup_long(
    strategy: VWAPMRStrategy,
    *,
    vwap_close: int = 10000,
    vwap_vol: int = 1000,
    entry_close: int = 9900,
    entry_vol: int = 500,
    threshold_pct: Decimal = Decimal("0.01"),
) -> tuple[Decimal, Decimal]:
    """VWAP 확정 후 진입까지 세팅. (entry_price, vwap) 반환."""
    # strategy 에 config 를 주입할 수 없으므로 기존 strategy 가 이미 해당 config 를
    # 가진 상태여야 한다 — 호출 전 strategy 는 VWAPMRStrategy(VWAPMRConfig(...)) 여야 함.
    strategy.on_bar(
        _bar(
            _SYMBOL,
            9,
            5,
            vwap_close,
            vwap_close + 100,
            vwap_close - 100,
            vwap_close,
            volume=vwap_vol,
        )
    )
    strategy.on_bar(
        _bar(
            _SYMBOL,
            9,
            6,
            entry_close,
            entry_close,
            entry_close - 20,
            entry_close,
            volume=entry_vol,
        )
    )
    return Decimal(str(entry_close)), Decimal(str(vwap_close))


# ---------------------------------------------------------------------------
# 3. 청산 시그널
# ---------------------------------------------------------------------------


def test_청산_bar_low_leq_stop_price_stop_loss():
    """bar.low ≤ stop_price → ExitSignal(stop_loss), price=stop_price, closed 전이."""
    cfg = VWAPMRConfig(
        threshold_pct=Decimal("0.01"),
        stop_loss_pct=Decimal("0.015"),
        take_profit_pct=Decimal("0.005"),
    )
    strategy = VWAPMRStrategy(cfg)
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=1000))
    strategy.on_bar(_bar(_SYMBOL, 9, 6, 9900, 9900, 9880, 9900, volume=500))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    stop = state.stop_price
    assert stop is not None

    result = strategy.on_bar(_bar(_SYMBOL, 9, 7, 9900, 9900, stop - Decimal("1"), 9850, volume=300))

    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, ExitSignal)
    assert sig.reason == "stop_loss"
    assert sig.price == stop
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


def test_청산_bar_high_geq_take_price_take_profit():
    """bar.high ≥ take_price → ExitSignal(take_profit), price=take_price, closed 전이."""
    cfg = VWAPMRConfig(
        threshold_pct=Decimal("0.01"),
        stop_loss_pct=Decimal("0.015"),
        take_profit_pct=Decimal("0.005"),
    )
    strategy = VWAPMRStrategy(cfg)
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=1000))
    strategy.on_bar(_bar(_SYMBOL, 9, 6, 9900, 9900, 9880, 9900, volume=500))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    take = state.take_price
    assert take is not None

    result = strategy.on_bar(_bar(_SYMBOL, 9, 7, 9900, take + Decimal("1"), 9880, 9950, volume=300))

    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, ExitSignal)
    assert sig.reason == "take_profit"
    assert sig.price == take
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


def test_청산_bar_high_geq_vwap_회귀_take_profit():
    """bar.high ≥ vwap (VWAP 회귀) → ExitSignal(take_profit), price=vwap, closed 전이."""
    cfg = VWAPMRConfig(
        threshold_pct=Decimal("0.01"),
        stop_loss_pct=Decimal("0.015"),
        take_profit_pct=Decimal("0.5"),  # 익절 목표를 크게 잡아 VWAP 회귀가 먼저 성립
    )
    strategy = VWAPMRStrategy(cfg)
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=1000))
    # close=9900 <= 10000 × 0.99 = 9900 → 진입
    strategy.on_bar(_bar(_SYMBOL, 9, 6, 9900, 9900, 9880, 9900, volume=500))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    vwap_val = state.vwap
    assert vwap_val is not None

    # bar.high >= vwap (10000) — VWAP 회귀
    result = strategy.on_bar(
        _bar(_SYMBOL, 9, 7, 9900, vwap_val + Decimal("1"), 9880, 9950, volume=300)
    )

    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, ExitSignal)
    assert sig.reason == "take_profit"
    assert sig.price == pytest.approx(vwap_val)
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


def test_청산_stop_take_동시_성립_stop_우선():
    """같은 bar 에서 stop·take 모두 성립 → stop_loss 우선."""
    cfg = VWAPMRConfig(
        threshold_pct=Decimal("0.01"),
        stop_loss_pct=Decimal("0.015"),
        take_profit_pct=Decimal("0.005"),
    )
    strategy = VWAPMRStrategy(cfg)
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=1000))
    strategy.on_bar(_bar(_SYMBOL, 9, 6, 9900, 9900, 9880, 9900, volume=500))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    stop = state.stop_price
    take = state.take_price
    assert stop is not None
    assert take is not None

    # low <= stop AND high >= take 동시 성립
    result = strategy.on_bar(
        _bar(_SYMBOL, 9, 7, 9900, take + Decimal("10"), stop - Decimal("1"), 9900, volume=300)
    )

    assert len(result) == 1
    assert result[0].reason == "stop_loss"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 4. 강제청산 (on_time)
# ---------------------------------------------------------------------------


def test_강제청산_on_time_force_close_at_long_force_close():
    """on_time(force_close_at) 호출 시 long 심볼 → ExitSignal(force_close), price=last_close."""
    cfg = VWAPMRConfig(
        threshold_pct=Decimal("0.01"),
        stop_loss_pct=Decimal("0.015"),
        take_profit_pct=Decimal("0.005"),
        force_close_at=time(15, 0),
    )
    strategy = VWAPMRStrategy(cfg)
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=1000))
    strategy.on_bar(_bar(_SYMBOL, 9, 6, 9900, 9900, 9880, 9900, volume=500))
    # last_close 갱신 — take_price=9949.50, vwap≈9966.67 모두 하회하도록 설정
    # high=9810 < take_price=9949.50 AND high=9810 < vwap≈9966.67 → 청산 없음
    # low=9780 > stop_price=9751.50 → 손절 없음
    strategy.on_bar(_bar(_SYMBOL, 14, 55, 9800, 9810, 9780, 9805, volume=200))

    signals = strategy.on_time(_now(15, 0))
    assert len(signals) == 1
    sig = signals[0]
    assert isinstance(sig, ExitSignal)
    assert sig.reason == "force_close"
    assert sig.price == Decimal("9805")  # last_close
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


def test_강제청산_flat_closed_심볼_무시():
    """flat / closed 상태 심볼은 on_time(force_close_at) 에서 시그널 없음."""
    cfg = VWAPMRConfig(threshold_pct=Decimal("0.01"))
    strategy = VWAPMRStrategy(cfg)
    # flat 상태 (진입 없음)
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=1000))

    signals = strategy.on_time(_now(15, 0))
    assert signals == []


def test_강제청산_last_close_없을때_entry_price_폴백():
    """on_time 강제청산 시 last_close 없으면 entry_price 로 폴백해 ExitSignal 반환."""
    cfg = VWAPMRConfig(
        threshold_pct=Decimal("0.01"),
        stop_loss_pct=Decimal("0.015"),
        take_profit_pct=Decimal("0.005"),
        force_close_at=time(15, 0),
    )
    strategy = VWAPMRStrategy(cfg)
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=1000))
    strategy.on_bar(_bar(_SYMBOL, 9, 6, 9900, 9900, 9880, 9900, volume=500))

    # last_close 를 None 으로 덮어써 폴백 경로 강제
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    state.last_close = None  # type: ignore[misc]

    signals = strategy.on_time(_now(15, 0))
    assert len(signals) == 1
    sig = signals[0]
    assert isinstance(sig, ExitSignal)
    assert sig.reason == "force_close"
    assert sig.price == Decimal("9900")  # entry_price 폴백


# ---------------------------------------------------------------------------
# 5. 세션 경계
# ---------------------------------------------------------------------------


def test_세션_경계_새_date_sum_pv_sum_v_리셋():
    """날짜 변경 bar 주입 시 sum_pv / sum_v 이 0 으로 리셋된다."""
    strategy = VWAPMRStrategy()
    # 4월 20일 VWAP 누적
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=1000))

    state_before = strategy.get_state(_SYMBOL)
    assert state_before is not None
    assert state_before.vwap is not None

    # 4월 21일 첫 bar — 리셋
    next_day = date(2026, 4, 21)
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 11000, 11100, 10900, 11000, volume=500, date_=next_day))

    state_after = strategy.get_state(_SYMBOL)
    assert state_after is not None
    assert state_after.session_date == next_day
    # 새 세션 첫 bar 로만 vwap 계산 → 11000
    assert state_after.vwap == Decimal("11000")
    assert state_after.position_state == "flat"


def test_세션_경계_어제_closed_새_세션_flat_재진입_가능():
    """전날 closed 상태여도 새 세션에서 재진입 가능."""
    cfg = VWAPMRConfig(
        threshold_pct=Decimal("0.01"),
        stop_loss_pct=Decimal("0.015"),
        take_profit_pct=Decimal("0.005"),
    )
    strategy = VWAPMRStrategy(cfg)
    # 4월 20일 진입 후 손절 → closed
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=1000))
    strategy.on_bar(_bar(_SYMBOL, 9, 6, 9900, 9900, 9880, 9900, volume=500))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    stop = state.stop_price
    assert stop is not None
    strategy.on_bar(_bar(_SYMBOL, 9, 7, 9900, 9900, stop - Decimal("1"), 9850, volume=300))
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]

    # 4월 21일 새 세션
    next_day = date(2026, 4, 21)
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=1000, date_=next_day))
    result = strategy.on_bar(
        _bar(_SYMBOL, 9, 6, 9900, 9900, 9880, 9900, volume=500, date_=next_day)
    )

    assert len(result) == 1
    assert isinstance(result[0], EntrySignal)


# ---------------------------------------------------------------------------
# 6. 재진입 금지
# ---------------------------------------------------------------------------


def test_재진입_금지_closed_후_같은_세션_재돌파():
    """closed 후 같은 세션에서 재돌파 시도 → 빈 리스트 (당일 재진입 금지)."""
    cfg = VWAPMRConfig(
        threshold_pct=Decimal("0.01"),
        stop_loss_pct=Decimal("0.015"),
        take_profit_pct=Decimal("0.005"),
    )
    strategy = VWAPMRStrategy(cfg)
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=1000))
    strategy.on_bar(_bar(_SYMBOL, 9, 6, 9900, 9900, 9880, 9900, volume=500))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    stop = state.stop_price
    assert stop is not None
    strategy.on_bar(_bar(_SYMBOL, 9, 7, 9900, 9900, stop - Decimal("1"), 9850, volume=300))
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]

    # 같은 세션에서 다시 이탈 → 재진입 없음
    result = strategy.on_bar(_bar(_SYMBOL, 9, 10, 9800, 9800, 9780, 9800, volume=400))
    assert result == []
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 7. 입력 검증
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol",
    ["00593", "AAPL", "", "12345a", "0059300"],
    ids=["5자리", "영문", "빈문자열", "영문혼용", "7자리"],
)
def test_잘못된_symbol_RuntimeError(symbol: str):
    """유효하지 않은 symbol 로 on_bar 호출 → RuntimeError."""
    strategy = VWAPMRStrategy()
    bar = MinuteBar(
        symbol=symbol,
        bar_time=_now(9, 5),
        open=Decimal("10000"),
        high=Decimal("10100"),
        low=Decimal("9900"),
        close=Decimal("10050"),
        volume=500,
    )
    with pytest.raises(RuntimeError, match="6자리 숫자"):
        strategy.on_bar(bar)


def test_naive_bar_time_RuntimeError():
    """bar.bar_time 이 naive datetime → RuntimeError (tz-aware 요구)."""
    strategy = VWAPMRStrategy()
    bar = MinuteBar(
        symbol=_SYMBOL,
        bar_time=datetime(2026, 4, 20, 9, 5),  # naive
        open=Decimal("10000"),
        high=Decimal("10100"),
        low=Decimal("9900"),
        close=Decimal("10050"),
        volume=500,
    )
    with pytest.raises(RuntimeError, match="tz-aware"):
        strategy.on_bar(bar)


def test_bar_time_역행_RuntimeError():
    """같은 심볼에서 시간이 역행하는 bar → RuntimeError."""
    strategy = VWAPMRStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000, volume=500))
    with pytest.raises(RuntimeError, match="역행"):
        strategy.on_bar(_bar(_SYMBOL, 9, 4, 10000, 10100, 9900, 10000, volume=500))


# ---------------------------------------------------------------------------
# 8. VWAPMRConfig 검증
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, match_fragment",
    [
        ({"threshold_pct": Decimal("0")}, "threshold_pct"),
        ({"threshold_pct": Decimal("-0.01")}, "threshold_pct"),
        ({"take_profit_pct": Decimal("0")}, "take_profit_pct"),
        ({"take_profit_pct": Decimal("-0.01")}, "take_profit_pct"),
        ({"stop_loss_pct": Decimal("0")}, "stop_loss_pct"),
        ({"stop_loss_pct": Decimal("-0.01")}, "stop_loss_pct"),
    ],
    ids=[
        "threshold_zero",
        "threshold_negative",
        "take_profit_zero",
        "take_profit_negative",
        "stop_loss_zero",
        "stop_loss_negative",
    ],
)
def test_config_비정상_pct_RuntimeError(kwargs: dict, match_fragment: str):
    """pct 계열 필드가 0 이하이면 RuntimeError."""
    with pytest.raises(RuntimeError, match=match_fragment):
        VWAPMRConfig(**kwargs)


def test_config_session_start_geq_force_close_at_RuntimeError():
    """session_start >= force_close_at → RuntimeError."""
    with pytest.raises(RuntimeError):
        VWAPMRConfig(session_start=time(15, 0), force_close_at=time(9, 0))


def test_config_기본값_검증():
    """기본 VWAPMRConfig 는 예외 없이 생성되고 승인된 손익 비율을 유지한다."""
    cfg = VWAPMRConfig()
    assert cfg.threshold_pct == Decimal("0.01")
    assert cfg.stop_loss_pct == Decimal("0.015")
    assert cfg.take_profit_pct == Decimal("0.005")
    assert cfg.session_start < cfg.force_close_at
