"""ORBStrategy / StrategyConfig / 시그널 DTO 공개 계약 단위 테스트.

외부 네트워크·DB·시계 의존 없음 — 순수 로직 검증. 목킹 불필요.
"""

from __future__ import annotations

import decimal
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from stock_agent.data import MinuteBar
from stock_agent.strategy import (
    EntrySignal,
    ExitSignal,
    ORBStrategy,
    StrategyConfig,
    StrategyError,
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


def _now(h: int, m: int, *, date_: date = _DATE) -> datetime:
    """KST aware datetime 헬퍼."""
    return datetime(date_.year, date_.month, date_.day, h, m, tzinfo=KST)


# ---------------------------------------------------------------------------
# 1. OR 누적 (09:00 ~ 09:29)
# ---------------------------------------------------------------------------


def test_or_구간_30개_bar_후_고저_정확():
    """09:00~09:29 bar 30개 주입 후 or_high/or_low 가 실제 max/min 과 일치."""
    strategy = ORBStrategy()
    highs = list(range(70100, 70130))  # 70100 ~ 70129
    lows = list(range(69900, 69930))

    for m in range(30):
        strategy.on_bar(_bar(_SYMBOL, 9, m, 70000, highs[m], lows[m], 70000))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.or_high == Decimal(str(max(highs)))
    assert state.or_low == Decimal(str(min(lows)))


def test_or_09시_이전_bar_무시():
    """08:59 bar 는 OR 누적에 포함되지 않는다."""
    strategy = ORBStrategy()
    # 08:59 bar — 무시돼야 한다
    strategy.on_bar(_bar(_SYMBOL, 8, 59, 70000, 71000, 69000, 70500))
    # 09:00 bar 하나만 넣기
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70200, 69800, 70100))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    # 08:59 의 high(71000)/low(69000) 가 반영되지 않아야 한다
    assert state.or_high == Decimal("70200")
    assert state.or_low == Decimal("69800")


def test_or_첫_bar_09시05분_지각_케이스():
    """첫 bar 가 09:05 여도 이후 bar 만 누적된다 (09:00~09:04 빠짐)."""
    strategy = ORBStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 70000, 70300, 69700, 70100))
    strategy.on_bar(_bar(_SYMBOL, 9, 15, 70100, 70400, 69900, 70200))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.or_high == Decimal("70400")
    assert state.or_low == Decimal("69700")


def test_or_bar_없는_상태로_확정_전이_후_진입_없음():
    """OR 구간에 bar 가 하나도 없으면 or_high 가 None → 돌파 bar 가 와도 진입 없음."""
    strategy = ORBStrategy()
    # 09:30 이후 bar 를 OR bar 없이 바로 주입
    result = strategy.on_bar(_bar(_SYMBOL, 9, 30, 70000, 71000, 70000, 71000))
    assert result == []
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.or_high is None


# ---------------------------------------------------------------------------
# 2. 진입 시그널
# ---------------------------------------------------------------------------


def test_진입_OR확정_후_close_초과_시_EntrySignal():
    """OR 확정 + bar.close > or_high 이면 EntrySignal 1건 반환."""
    strategy = ORBStrategy()
    # OR 구간 누적
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))

    # 09:30 bar — close(71000) > or_high(70500) → 진입
    result = strategy.on_bar(_bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000))

    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, EntrySignal)
    assert sig.symbol == _SYMBOL
    assert sig.price == Decimal("71000")


def test_진입_stop_take_price_정확():
    """EntrySignal 의 stop_price/take_price 가 Decimal 연산 기대값과 일치."""
    strategy = ORBStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))

    result = strategy.on_bar(_bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000))
    sig = result[0]
    assert isinstance(sig, EntrySignal)

    entry = Decimal("71000")
    expected_stop = entry * (Decimal("1") - Decimal("0.015"))
    expected_take = entry * (Decimal("1") + Decimal("0.030"))
    assert sig.stop_price == expected_stop
    assert sig.take_price == expected_take


def test_진입_close_or_high_동일_터치_진입없음():
    """bar.close == or_high (엄밀 초과 아님) → EntrySignal 없음."""
    strategy = ORBStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))

    # close == or_high
    result = strategy.on_bar(_bar(_SYMBOL, 9, 30, 70200, 70800, 70100, 70500))
    assert result == []


def test_진입_OR_구간_bar_진입없음():
    """09:29 bar 는 OR 구간이므로 close > or_high 여도 진입 없음."""
    strategy = ORBStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))

    # 09:29 는 아직 OR 구간 — 누적만, 진입 없음
    result = strategy.on_bar(_bar(_SYMBOL, 9, 29, 70200, 71000, 70100, 71000))
    assert result == []
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.position_state == "flat"


def test_진입_후_상태_long_동일_bar_추가_시그널_없음():
    """진입 bar 에서 EntrySignal 1건만 반환하고 상태가 long 으로 전이된다."""
    strategy = ORBStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))

    result = strategy.on_bar(_bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000))
    assert len(result) == 1
    assert isinstance(result[0], EntrySignal)

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.position_state == "long"


# ---------------------------------------------------------------------------
# 3. 청산 시그널
# ---------------------------------------------------------------------------


def _setup_long(strategy: ORBStrategy, entry_close: int = 71000) -> Decimal:
    """OR 누적 후 진입까지 세팅하고 entry_price 반환."""
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))
    strategy.on_bar(_bar(_SYMBOL, 9, 30, 70200, 71500, 70100, entry_close))
    return Decimal(str(entry_close))


def test_청산_손절_low_leq_stop_price():
    """bar.low <= stop_price → ExitSignal(stop_loss), price=stop_price, closed."""
    strategy = ORBStrategy()
    entry = _setup_long(strategy, 71000)
    stop = entry * (Decimal("1") - Decimal("0.015"))

    # low 가 stop_price 이하
    result = strategy.on_bar(_bar(_SYMBOL, 9, 31, 70500, 70800, stop - Decimal("1"), 70600))
    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, ExitSignal)
    assert sig.reason == "stop_loss"
    assert sig.price == stop
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


def test_청산_익절_high_geq_take_price():
    """bar.high >= take_price → ExitSignal(take_profit), price=take_price, closed."""
    strategy = ORBStrategy()
    entry = _setup_long(strategy, 71000)
    take = entry * (Decimal("1") + Decimal("0.030"))

    # high 가 take_price 이상
    result = strategy.on_bar(_bar(_SYMBOL, 9, 31, 71100, take + Decimal("1"), 70900, 71200))
    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, ExitSignal)
    assert sig.reason == "take_profit"
    assert sig.price == take
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


def test_청산_동일_bar_손절_익절_동시_손절_우선():
    """같은 bar 에서 stop·take 모두 성립 → stop_loss 우선."""
    strategy = ORBStrategy()
    entry = _setup_long(strategy, 71000)
    stop = entry * (Decimal("1") - Decimal("0.015"))
    take = entry * (Decimal("1") + Decimal("0.030"))

    # low <= stop AND high >= take
    result = strategy.on_bar(
        _bar(_SYMBOL, 9, 31, 71000, take + Decimal("10"), stop - Decimal("1"), 71000)
    )
    assert len(result) == 1
    assert result[0].reason == "stop_loss"  # type: ignore[union-attr]


def test_청산_후_closed_재진입_없음():
    """청산(closed) 상태에서 돌파 bar 를 주입해도 재진입 없음."""
    strategy = ORBStrategy()
    entry = _setup_long(strategy, 71000)
    stop = entry * (Decimal("1") - Decimal("0.015"))

    # 손절로 closed
    strategy.on_bar(_bar(_SYMBOL, 9, 31, 70500, 70800, stop - Decimal("1"), 70600))
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]

    # 또 다른 돌파 bar
    result = strategy.on_bar(_bar(_SYMBOL, 9, 35, 70600, 72000, 70500, 72000))
    assert result == []
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 4. 강제청산 (on_time)
# ---------------------------------------------------------------------------


def test_강제청산_15시_long_심볼_force_close():
    """on_time(15:00) 호출 시 long 심볼 → ExitSignal(force_close), price=last_close."""
    strategy = ORBStrategy()
    _setup_long(strategy, 71000)
    # 15:00 이전 분봉으로 last_close 갱신
    strategy.on_bar(_bar(_SYMBOL, 14, 55, 71000, 71200, 70900, 71100))

    signals = strategy.on_time(_now(15, 0))
    assert len(signals) == 1
    sig = signals[0]
    assert isinstance(sig, ExitSignal)
    assert sig.reason == "force_close"
    assert sig.price == Decimal("71100")  # last_close
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


def test_강제청산_flat_심볼_시그널_없음():
    """flat 상태 심볼은 on_time(15:00) 에서 시그널 없음."""
    strategy = ORBStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))
    # 돌파 없음 — flat 유지
    strategy.on_bar(_bar(_SYMBOL, 9, 30, 70200, 70400, 69900, 70300))

    signals = strategy.on_time(_now(15, 0))
    assert signals == []


def test_강제청산_closed_심볼_시그널_없음():
    """closed 상태 심볼은 on_time(15:00) 에서 시그널 없음."""
    strategy = ORBStrategy()
    entry = _setup_long(strategy, 71000)
    stop = entry * (Decimal("1") - Decimal("0.015"))
    strategy.on_bar(_bar(_SYMBOL, 9, 31, 70500, 70800, stop - Decimal("1"), 70600))
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]

    signals = strategy.on_time(_now(15, 0))
    assert signals == []


def test_강제청산_force_close_at_커스터마이즈():
    """force_close_at=14:30 커스텀 config → 14:30 on_time 에서 강제청산."""
    from datetime import time as dtime

    cfg = StrategyConfig(force_close_at=dtime(14, 30))
    strategy = ORBStrategy(config=cfg)
    _setup_long(strategy, 71000)

    # 14:30 미만 → 빈 리스트
    assert strategy.on_time(_now(14, 29)) == []

    # 14:30 이상 → force_close
    signals = strategy.on_time(_now(14, 30))
    assert len(signals) == 1
    assert signals[0].reason == "force_close"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 5. 세션 전환 (날짜 변경)
# ---------------------------------------------------------------------------


def test_세션_전환_상태_리셋_후_새_OR_누적():
    """날짜 변경 bar 주입 시 이전 상태가 리셋되고 새 OR 누적 시작."""
    strategy = ORBStrategy()
    # 4월 20일 OR 누적
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))
    state_before = strategy.get_state(_SYMBOL)
    assert state_before is not None
    assert state_before.or_high == Decimal("70500")

    # 4월 21일 첫 bar (날짜 변경)
    next_day = date(2026, 4, 21)
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 71000, 71200, 70800, 71000, date_=next_day))

    state_after = strategy.get_state(_SYMBOL)
    assert state_after is not None
    assert state_after.session_date == next_day
    assert state_after.or_high == Decimal("71200")
    assert state_after.or_low == Decimal("70800")
    assert state_after.position_state == "flat"


def test_세션_전환_closed_후_새_세션_재진입_가능():
    """전날 closed 상태여도 새 세션(날짜 변경)에서 재진입 가능."""
    strategy = ORBStrategy()
    entry = _setup_long(strategy, 71000)
    stop = entry * (Decimal("1") - Decimal("0.015"))
    strategy.on_bar(_bar(_SYMBOL, 9, 31, 70500, 70800, stop - Decimal("1"), 70600))
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]

    # 새 세션 — OR 누적 후 진입
    next_day = date(2026, 4, 21)
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200, date_=next_day))
    result = strategy.on_bar(_bar(_SYMBOL, 9, 30, 70200, 71500, 70100, 71000, date_=next_day))

    assert len(result) == 1
    assert isinstance(result[0], EntrySignal)


# ---------------------------------------------------------------------------
# 6. 복수 심볼 독립
# ---------------------------------------------------------------------------


def test_복수_심볼_상태_격리():
    """심볼 A 진입해도 심볼 B 는 flat 유지 — 상태 격리."""
    strategy = ORBStrategy()
    sym_a = "005930"
    sym_b = "000660"

    # 두 심볼 OR 누적
    strategy.on_bar(_bar(sym_a, 9, 0, 70000, 70500, 69800, 70200))
    strategy.on_bar(_bar(sym_b, 9, 0, 80000, 80500, 79800, 80200))

    # A 만 돌파
    strategy.on_bar(_bar(sym_a, 9, 30, 70200, 71500, 70100, 71000))
    # B 는 돌파 없음
    strategy.on_bar(_bar(sym_b, 9, 30, 80200, 80400, 79900, 80300))

    state_a = strategy.get_state(sym_a)
    state_b = strategy.get_state(sym_b)
    assert state_a is not None and state_a.position_state == "long"
    assert state_b is not None and state_b.position_state == "flat"


def test_복수_심볼_on_time_대상_심볼만_청산():
    """on_time(15:00) 시 long 심볼만 청산 — flat 심볼은 시그널 없음."""
    strategy = ORBStrategy()
    sym_a = "005930"
    sym_b = "000660"

    # A 진입
    strategy.on_bar(_bar(sym_a, 9, 0, 70000, 70500, 69800, 70200))
    strategy.on_bar(_bar(sym_a, 9, 30, 70200, 71500, 70100, 71000))
    # B OR 누적만, 돌파 없음
    strategy.on_bar(_bar(sym_b, 9, 0, 80000, 80500, 79800, 80200))
    strategy.on_bar(_bar(sym_b, 9, 30, 80200, 80400, 79900, 80300))

    signals = strategy.on_time(_now(15, 0))
    assert len(signals) == 1
    assert signals[0].symbol == sym_a  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 7. 입력 검증·에러
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol",
    ["00593", "AAPL", "", "12345a", "0059300"],
    ids=["5자리", "영문", "빈문자열", "영문혼용", "7자리"],
)
def test_잘못된_symbol_RuntimeError(symbol: str):
    """유효하지 않은 symbol 로 on_bar 호출 → RuntimeError."""
    strategy = ORBStrategy()
    bar = MinuteBar(
        symbol=symbol,
        bar_time=_now(9, 30),
        open=Decimal("70000"),
        high=Decimal("70500"),
        low=Decimal("69800"),
        close=Decimal("70200"),
        volume=0,
    )
    with pytest.raises(RuntimeError, match="6자리 숫자"):
        strategy.on_bar(bar)


def test_naive_bar_time_RuntimeError():
    """bar.bar_time 이 naive datetime → RuntimeError (tz-aware 요구)."""
    strategy = ORBStrategy()
    bar = MinuteBar(
        symbol=_SYMBOL,
        bar_time=datetime(2026, 4, 20, 9, 30),  # naive
        open=Decimal("70000"),
        high=Decimal("70500"),
        low=Decimal("69800"),
        close=Decimal("70200"),
        volume=0,
    )
    with pytest.raises(RuntimeError, match="tz-aware"):
        strategy.on_bar(bar)


def test_naive_now_on_time_RuntimeError():
    """on_time 에 naive datetime → RuntimeError."""
    strategy = ORBStrategy()
    with pytest.raises(RuntimeError, match="tz-aware"):
        strategy.on_time(datetime(2026, 4, 20, 15, 0))  # naive


def test_bar_time_역행_RuntimeError():
    """같은 심볼에서 시간이 역행하는 bar → RuntimeError."""
    strategy = ORBStrategy()
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 70000, 70500, 69800, 70200))
    # 9:05 → 9:04 역행
    with pytest.raises(RuntimeError, match="역행"):
        strategy.on_bar(_bar(_SYMBOL, 9, 4, 70000, 70500, 69800, 70200))


# ---------------------------------------------------------------------------
# 8. StrategyConfig 검증
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"stop_loss_pct": Decimal("0")}, "stop_loss_pct"),
        ({"stop_loss_pct": Decimal("-0.01")}, "stop_loss_pct"),
        ({"take_profit_pct": Decimal("0")}, "take_profit_pct"),
        ({"take_profit_pct": Decimal("-0.05")}, "take_profit_pct"),
    ],
    ids=["stop_loss_zero", "stop_loss_negative", "take_profit_zero", "take_profit_negative"],
)
def test_config_비정상_pct_RuntimeError(kwargs: dict, match: str):
    """stop_loss_pct/take_profit_pct 가 0 이하이면 RuntimeError."""
    with pytest.raises(RuntimeError, match=match):
        StrategyConfig(**kwargs)


def test_config_or_start_geq_or_end_RuntimeError():
    """or_start >= or_end → RuntimeError."""
    from datetime import time as dtime

    with pytest.raises(RuntimeError, match="or_start"):
        StrategyConfig(or_start=dtime(9, 30), or_end=dtime(9, 0))


def test_config_or_end_geq_force_close_at_RuntimeError():
    """or_end >= force_close_at → RuntimeError."""
    from datetime import time as dtime

    with pytest.raises(RuntimeError, match="or_end"):
        StrategyConfig(or_end=dtime(15, 0), force_close_at=dtime(9, 30))


def test_config_기본값_검증():
    """기본 StrategyConfig 는 예외 없이 생성되고 승인된 리스크 한도를 유지한다."""
    cfg = StrategyConfig()
    assert cfg.stop_loss_pct == Decimal("0.015")
    assert cfg.take_profit_pct == Decimal("0.030")


# ---------------------------------------------------------------------------
# 9. 경계 커버리지 (I3)
# ---------------------------------------------------------------------------


def test_or_09시30분_bar_경계_pin_or_high_불변():
    """09:30:00 정각 bar 는 OR 누적에 포함되지 않고 돌파 판정 분기로 진입한다.

    09:00~09:29 bar 30개로 or_high 를 X 로 확정시킨 뒤,
    09:30 bar 의 close ≤ X 이면 EntrySignal 없고 or_high 가 X 그대로임을 검증.
    """
    strategy = ORBStrategy()
    # 09:00~09:29 bar 30개 — or_high = 70129
    for m in range(30):
        strategy.on_bar(_bar(_SYMBOL, 9, m, 70000, 70100 + m, 69900, 70000))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    or_high_fixed = state.or_high  # Decimal("70129")

    # 09:30 bar — close(70129, ≤ or_high) 이므로 진입 없음
    result = strategy.on_bar(_bar(_SYMBOL, 9, 30, 70129, 70200, 70050, 70129))
    assert result == []

    # or_high 가 09:30 bar 의 high(70200) 로 바뀌지 않고 그대로 유지
    state_after = strategy.get_state(_SYMBOL)
    assert state_after is not None
    assert state_after.or_high == or_high_fixed
    assert state_after.or_confirmed is True
    assert state_after.position_state == "flat"


def test_force_close_at_이후_flat_신규_진입_차단():
    """force_close_at(15:00) 이후 돌파 bar 가 와도 EntrySignal 없이 flat 유지."""
    strategy = ORBStrategy()
    # OR 누적
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 70000, 70500, 69800, 70200))

    # 15:00 이후 bar — close(72000) > or_high(70500) 이지만 진입 금지 구간
    result = strategy.on_bar(_bar(_SYMBOL, 15, 0, 70500, 72500, 70400, 72000))
    assert result == []

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.position_state == "flat"


def test_on_time_last_close_none_entry_price_폴백_ExitSignal():
    """on_time 강제청산 시 last_close 가 None 이면 entry_price 로 폴백해 ExitSignal 반환."""
    strategy = ORBStrategy()
    entry = _setup_long(strategy, 71000)  # last_close = Decimal("71000")

    # 의도적으로 last_close 만 None 으로 덮어써 폴백 경로를 강제 실행
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    state.last_close = None  # type: ignore[misc]

    signals = strategy.on_time(_now(15, 0))
    assert len(signals) == 1
    sig = signals[0]
    assert isinstance(sig, ExitSignal)
    assert sig.reason == "force_close"
    # entry_price 폴백이므로 price == entry_price
    assert sig.price == entry
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


def test_on_time_last_close_entry_price_모두_none_StrategyError():
    """on_time 강제청산 시 last_close·entry_price 모두 None → StrategyError(상태 머신 무결성)."""
    strategy = ORBStrategy()
    _setup_long(strategy, 71000)

    # 두 필드 모두 None 으로 강제 세팅 (상태 머신 불가능 경우를 재현)
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    state.last_close = None  # type: ignore[misc]
    state.entry_price = None  # type: ignore[misc]

    with pytest.raises(StrategyError, match="상태 머신 무결성"):
        strategy.on_time(_now(15, 0))


def test_or_confirmed_false_to_true_전이_검증():
    """OR 구간 bar 주입 중 or_confirmed False, 09:30 이상 첫 bar 주입 후 or_confirmed True."""
    strategy = ORBStrategy()

    # OR 구간 bar 주입 — or_confirmed 는 아직 False
    for m in range(30):
        strategy.on_bar(_bar(_SYMBOL, 9, m, 70000, 70200, 69800, 70000))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.or_confirmed is False
    assert state.position_state == "flat"

    # 09:30 bar — close(70000) ≤ or_high(70200) 이므로 진입은 없지만 or_confirmed 전이
    result = strategy.on_bar(_bar(_SYMBOL, 9, 30, 70000, 70100, 69900, 70000))
    assert result == []

    state_after = strategy.get_state(_SYMBOL)
    assert state_after is not None
    assert state_after.or_confirmed is True
    assert state_after.position_state == "flat"


# ---------------------------------------------------------------------------
# restore_long_position (Issue #33)
# ---------------------------------------------------------------------------


class TestRestoreLongPosition:
    """restore_long_position — 재기동 시 open position 의 ORB 상태 복원."""

    def test_position_state_long_설정(self):
        """복원 후 position_state == 'long'."""
        strategy = ORBStrategy()
        strategy.restore_long_position(_SYMBOL, Decimal("70000"), _now(9, 45))
        state = strategy.get_state(_SYMBOL)
        assert state is not None
        assert state.position_state == "long"

    def test_or_confirmed_True_설정(self):
        """복원 후 or_confirmed == True — 이후 bar가 OR 미확정 경로를 타지 않도록."""
        strategy = ORBStrategy()
        strategy.restore_long_position(_SYMBOL, Decimal("70000"), _now(9, 45))
        state = strategy.get_state(_SYMBOL)
        assert state is not None
        assert state.or_confirmed is True

    def test_stop_take_재계산(self):
        """stop_price / take_price 는 기본 config 기준으로 재계산된다."""
        cfg = StrategyConfig()  # stop_loss_pct=0.015, take_profit_pct=0.030
        strategy = ORBStrategy(cfg)
        entry_price = Decimal("70000")
        strategy.restore_long_position(_SYMBOL, entry_price, _now(9, 45))

        state = strategy.get_state(_SYMBOL)
        assert state is not None
        expected_stop = entry_price * (Decimal("1") - cfg.stop_loss_pct)
        expected_take = entry_price * (Decimal("1") + cfg.take_profit_pct)
        assert state.stop_price == pytest.approx(expected_stop)
        assert state.take_price == pytest.approx(expected_take)

    def test_entry_price_설정(self):
        """복원 후 state.entry_price == 주입한 entry_price."""
        entry_price = Decimal("68500")
        strategy = ORBStrategy()
        strategy.restore_long_position(_SYMBOL, entry_price, _now(10, 0))
        state = strategy.get_state(_SYMBOL)
        assert state is not None
        assert state.entry_price == entry_price

    def test_복원_후_stop_loss_bar_주입_exit_신호(self):
        """복원된 long 포지션에 손절가 이하 bar 주입 → stop_loss ExitSignal."""
        entry_price = Decimal("70000")
        cfg = StrategyConfig()
        strategy = ORBStrategy(cfg)
        strategy.restore_long_position(_SYMBOL, entry_price, _now(9, 45))

        stop = entry_price * (Decimal("1") - cfg.stop_loss_pct)
        # low 가 stop 이하인 bar
        signals = strategy.on_bar(_bar(_SYMBOL, 10, 0, 70000, 70000, stop - Decimal("1"), 69500))
        assert len(signals) == 1
        assert isinstance(signals[0], ExitSignal)
        assert signals[0].reason == "stop_loss"

    def test_symbol_포맷_오류_RuntimeError(self):
        """6자리 아닌 symbol → RuntimeError."""
        strategy = ORBStrategy()
        with pytest.raises(RuntimeError, match="symbol"):
            strategy.restore_long_position("1234", Decimal("70000"), _now(9, 45))

    def test_naive_entry_ts_RuntimeError(self):
        """naive datetime entry_ts → RuntimeError."""
        strategy = ORBStrategy()
        naive_ts = datetime(2026, 4, 20, 9, 45)  # tzinfo=None
        with pytest.raises(RuntimeError, match="entry_ts"):
            strategy.restore_long_position(_SYMBOL, Decimal("70000"), naive_ts)

    def test_entry_price_0이하_RuntimeError(self):
        """entry_price ≤ 0 → RuntimeError."""
        strategy = ORBStrategy()
        with pytest.raises(RuntimeError, match="entry_price"):
            strategy.restore_long_position(_SYMBOL, Decimal("0"), _now(9, 45))


class TestRestoreLongPositionDecimalException:
    """restore_long_position — Decimal 연산 실패 시 StrategyError 래핑 (Issue #42).

    현재 restore_long_position 에는 on_bar 의 DecimalException → StrategyError
    래핑 가드(orb.py:182-187)가 없다. 이 클래스는 그 누락을 RED 테스트로 고정해
    구현 후 GREEN 전환을 강제한다.
    """

    def test_decimal_exception_stop_계산_StrategyError_래핑(self):
        """stop = entry_price * (1 - stop_loss_pct) 에서 InvalidOperation 발생 시
        StrategyError 로 래핑되고 __cause__ 가 DecimalException 서브클래스여야 한다.

        decimal.localcontext 로 Inexact 트랩 + prec=1 설정 후
        70000 * Decimal("0.985") 계산이 Inexact → InvalidOperation 을 유발한다.
        """
        strategy = ORBStrategy()
        # prec=1 + Inexact 트랩: "70000 * 0.985" 는 Inexact 이므로 InvalidOperation 발생
        with decimal.localcontext() as ctx:
            ctx.prec = 1
            ctx.traps[decimal.Inexact] = True
            with pytest.raises(StrategyError) as exc_info:
                strategy.restore_long_position(_SYMBOL, Decimal("70000"), _now(9, 45))

        err = exc_info.value
        # __cause__ 가 DecimalException 계열이어야 한다
        assert isinstance(err.__cause__, decimal.DecimalException)
        # 오류 메시지에 symbol 이 포함되어야 한다
        assert _SYMBOL in str(err)

    def test_decimal_exception_take_계산_StrategyError_래핑(self):
        """take = entry_price * (1 + take_profit_pct) 계산에서도 동일하게 래핑된다.

        stop_loss_pct 를 정확히 계산 가능한 Decimal("0") 으로 강제 불가
        (StrategyConfig 검증이 0 을 거부하므로) — 대신 prec=1 + Inexact 트랩으로
        두 곱셈 중 하나에서 트리거한다. 어느 쪽이든 StrategyError 이면 충분.
        """
        strategy = ORBStrategy()
        with decimal.localcontext() as ctx:
            ctx.prec = 1
            ctx.traps[decimal.Inexact] = True
            with pytest.raises(StrategyError) as exc_info:
                strategy.restore_long_position(_SYMBOL, Decimal("68500"), _now(10, 0))

        err = exc_info.value
        assert isinstance(err.__cause__, decimal.DecimalException)
        assert _SYMBOL in str(err)

    def test_runtime_error_경로_변경_없음_symbol_포맷(self):
        """DecimalException 래핑 추가 후에도 symbol 포맷 오류는 여전히 RuntimeError."""
        strategy = ORBStrategy()
        with decimal.localcontext() as ctx:
            ctx.prec = 1
            ctx.traps[decimal.Inexact] = True
            # symbol 검증은 Decimal 연산 이전에 수행되므로 RuntimeError 전파
            with pytest.raises(RuntimeError, match="symbol"):
                strategy.restore_long_position("1234", Decimal("70000"), _now(9, 45))

    def test_runtime_error_경로_변경_없음_naive_ts(self):
        """DecimalException 래핑 추가 후에도 naive entry_ts 는 여전히 RuntimeError."""
        strategy = ORBStrategy()
        naive_ts = datetime(2026, 4, 20, 9, 45)  # tzinfo=None
        with decimal.localcontext() as ctx:
            ctx.prec = 1
            ctx.traps[decimal.Inexact] = True
            with pytest.raises(RuntimeError, match="entry_ts"):
                strategy.restore_long_position(_SYMBOL, Decimal("70000"), naive_ts)


# ---------------------------------------------------------------------------
# mark_session_closed (Issue #33)
# ---------------------------------------------------------------------------


class TestMarkSessionClosed:
    """mark_session_closed — 재기동 시 당일 이미 청산된 심볼을 closed 로 표시."""

    def test_position_state_closed_설정(self):
        """mark_session_closed 후 position_state == 'closed'."""
        strategy = ORBStrategy()
        strategy.mark_session_closed(_SYMBOL, _DATE)
        state = strategy.get_state(_SYMBOL)
        assert state is not None
        assert state.position_state == "closed"

    def test_or_confirmed_True_설정(self):
        """mark_session_closed 후 or_confirmed == True."""
        strategy = ORBStrategy()
        strategy.mark_session_closed(_SYMBOL, _DATE)
        state = strategy.get_state(_SYMBOL)
        assert state is not None
        assert state.or_confirmed is True

    def test_closed_후_돌파_bar_재진입_차단(self):
        """closed 표시 후 or_high 상향 돌파 bar 주입해도 빈 리스트."""
        strategy = ORBStrategy()
        strategy.mark_session_closed(_SYMBOL, _DATE)
        # OR 확인 없이 돌파 bar 주입 — closed 상태이므로 재진입 없어야 함
        signals = strategy.on_bar(_bar(_SYMBOL, 10, 0, 70000, 75000, 69500, 74000))
        assert signals == []

    def test_symbol_포맷_오류_RuntimeError(self):
        """6자리 아닌 symbol → RuntimeError."""
        strategy = ORBStrategy()
        with pytest.raises(RuntimeError, match="symbol"):
            strategy.mark_session_closed("ABCDE", _DATE)

    def test_다른_심볼과_상태_격리(self):
        """closed 표시한 심볼 외 다른 심볼 상태에는 영향 없음."""
        symbol_b = "000660"
        strategy = ORBStrategy()
        strategy.mark_session_closed(_SYMBOL, _DATE)
        # symbol_b 는 아직 상태 없음
        state_b = strategy.get_state(symbol_b)
        assert state_b is None


# ---------------------------------------------------------------------------
# reset_session (Issue #33 — 부분 복원 롤백용)
# ---------------------------------------------------------------------------


class TestResetSession:
    """reset_session — Executor.restore_session 의 ORB 루프 부분 실패 시 롤백 경로."""

    def test_빈_리스트로_모든_states_제거(self):
        """reset_session([]) → _states 가 빈 dict, get_state 가 None 반환."""
        strategy = ORBStrategy()
        strategy.restore_long_position(_SYMBOL, Decimal("70000"), _now(9, 45))
        strategy.mark_session_closed("000660", _DATE)
        assert strategy.get_state(_SYMBOL) is not None
        assert strategy.get_state("000660") is not None

        strategy.reset_session([])

        assert strategy.get_state(_SYMBOL) is None
        assert strategy.get_state("000660") is None

    def test_빈_튜플로_모든_states_제거(self):
        """reset_session(()) — 빈 tuple 도 빈 sequence 이므로 clear 와 동일 동작."""
        strategy = ORBStrategy()
        strategy.restore_long_position(_SYMBOL, Decimal("70000"), _now(9, 45))
        assert strategy.get_state(_SYMBOL) is not None

        strategy.reset_session(())

        assert strategy.get_state(_SYMBOL) is None

    def test_지정_심볼만_제거_나머지_보존(self):
        """reset_session(["005930"]) → 해당 심볼만 제거, 000660 상태는 유지."""
        symbol_b = "000660"
        strategy = ORBStrategy()
        strategy.restore_long_position(_SYMBOL, Decimal("70000"), _now(9, 45))
        strategy.mark_session_closed(symbol_b, _DATE)

        strategy.reset_session([_SYMBOL])

        assert strategy.get_state(_SYMBOL) is None
        # 다른 심볼 상태는 그대로 남아있어야 한다
        state_b = strategy.get_state(symbol_b)
        assert state_b is not None
        assert state_b.position_state == "closed"

    def test_알_수_없는_심볼_조용히_무시(self):
        """존재하지 않는 심볼 지정해도 raise 없이 통과."""
        strategy = ORBStrategy()
        # _states 에 없는 심볼을 지정 — RuntimeError 없이 완료되어야 한다
        strategy.reset_session(["000000"])

    def test_중복_심볼_인자도_raise_없음(self):
        """같은 심볼이 여러 번 들어와도 raise 없이 통과."""
        strategy = ORBStrategy()
        strategy.restore_long_position(_SYMBOL, Decimal("70000"), _now(9, 45))

        strategy.reset_session([_SYMBOL, _SYMBOL])  # 중복

        # 심볼이 제거되고 예외가 없어야 한다
        assert strategy.get_state(_SYMBOL) is None

    def test_restore_후_reset_후_on_bar_fresh_상태(self):
        """restore_long_position → reset_session → on_bar 로 OR 구간 수집 가능.

        reset 후 해당 심볼은 _states 에 없으므로 on_bar 가 새 _SymbolState 를
        생성(setdefault)해 정상 OR 누적이 시작되는지 검증한다.
        """
        strategy = ORBStrategy()
        # 포지션 복원
        strategy.restore_long_position(_SYMBOL, Decimal("70000"), _now(9, 45))
        state_before = strategy.get_state(_SYMBOL)
        assert state_before is not None
        assert state_before.position_state == "long"

        # 롤백
        strategy.reset_session([_SYMBOL])
        assert strategy.get_state(_SYMBOL) is None

        # 새 날짜 OR 구간 분봉 주입 — fresh 상태로 처리되어야 한다
        next_date = date(2026, 4, 21)
        from stock_agent.data import MinuteBar as _MinuteBar

        bar = _MinuteBar(
            symbol=_SYMBOL,
            bar_time=datetime(next_date.year, next_date.month, next_date.day, 9, 5, tzinfo=KST),
            open=Decimal("71000"),
            high=Decimal("71500"),
            low=Decimal("70800"),
            close=Decimal("71200"),
            volume=0,
        )
        signals = strategy.on_bar(bar)
        # OR 구간 bar 이므로 시그널 없고 or_high 가 갱신되어야 한다
        assert signals == []
        state_after = strategy.get_state(_SYMBOL)
        assert state_after is not None
        assert state_after.or_high == Decimal("71500")
        assert state_after.position_state == "flat"
