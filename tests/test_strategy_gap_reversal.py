"""GapReversalStrategy / GapReversalConfig 공개 계약 단위 테스트 (RED 단계).

외부 네트워크·DB·시계 의존 없음 — 순수 로직 검증. prev_close_provider 는 테스트 내 클로저로 제공.
대상 모듈: src/stock_agent/strategy/gap_reversal.py (아직 없음 — ImportError 로 FAIL 예상).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from stock_agent.data import MinuteBar
from stock_agent.strategy import (
    EntrySignal,
    ExitSignal,
    GapReversalConfig,
    GapReversalStrategy,
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


def _make_provider(prev_close: Decimal | None):
    """단순 클로저 — symbol/session_date 무관하게 고정값 반환."""

    def provider(symbol: str, session_date: date) -> Decimal | None:
        _ = (symbol, session_date)
        return prev_close

    return provider


# ---------------------------------------------------------------------------
# 1. GapReversalConfig 검증 (5건)
# ---------------------------------------------------------------------------


def test_config_gap_threshold_pct_zero_RuntimeError():
    """gap_threshold_pct = 0 → RuntimeError."""
    with pytest.raises(RuntimeError, match="gap_threshold_pct"):
        GapReversalConfig(gap_threshold_pct=Decimal("0"))


def test_config_gap_threshold_pct_negative_RuntimeError():
    """gap_threshold_pct < 0 → RuntimeError."""
    with pytest.raises(RuntimeError, match="gap_threshold_pct"):
        GapReversalConfig(gap_threshold_pct=Decimal("-0.01"))


def test_config_take_profit_pct_zero_RuntimeError():
    """take_profit_pct = 0 → RuntimeError."""
    with pytest.raises(RuntimeError, match="take_profit_pct"):
        GapReversalConfig(take_profit_pct=Decimal("0"))


def test_config_stop_loss_pct_zero_RuntimeError():
    """stop_loss_pct = 0 → RuntimeError."""
    with pytest.raises(RuntimeError, match="stop_loss_pct"):
        GapReversalConfig(stop_loss_pct=Decimal("0"))


def test_config_session_start_geq_entry_window_end_RuntimeError():
    """session_start >= entry_window_end → RuntimeError."""
    with pytest.raises(RuntimeError):
        GapReversalConfig(session_start=time(9, 30), entry_window_end=time(9, 0))


def test_config_entry_window_end_geq_force_close_at_RuntimeError():
    """entry_window_end >= force_close_at → RuntimeError."""
    with pytest.raises(RuntimeError):
        GapReversalConfig(entry_window_end=time(15, 0), force_close_at=time(9, 30))


def test_config_기본값_검증():
    """기본 GapReversalConfig 는 예외 없이 생성되고 승인된 수치를 유지한다."""
    cfg = GapReversalConfig()
    assert cfg.gap_threshold_pct == Decimal("0.02")
    assert cfg.take_profit_pct == Decimal("0.015")
    assert cfg.stop_loss_pct == Decimal("0.01")
    assert cfg.session_start < cfg.entry_window_end < cfg.force_close_at


# ---------------------------------------------------------------------------
# 2. prev_close_provider 호출 (3건)
# ---------------------------------------------------------------------------


def test_provider_새_session_reset_시_symbol_session_date_인자_호출됨():
    """새 session 진입 시 provider 가 (symbol, session_date) 로 호출된다."""
    calls: list[tuple[str, date]] = []

    def spy_provider(symbol: str, session_date: date) -> Decimal | None:
        calls.append((symbol, session_date))
        return Decimal("10000")

    strategy = GapReversalStrategy(prev_close_provider=spy_provider)
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 9800, 9850, 9780, 9810))

    assert len(calls) == 1
    assert calls[0] == (_SYMBOL, _DATE)


def test_provider_None_반환_진입_거부():
    """provider 가 None 반환 → 진입 거부. flat 유지, gap_evaluated 도 False 유지."""
    strategy = GapReversalStrategy(prev_close_provider=_make_provider(None))
    result = strategy.on_bar(_bar(_SYMBOL, 9, 0, 9800, 9850, 9780, 9810))

    assert result == []
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.position_state == "flat"
    # provider 결과 부재 시 gap_evaluated 는 True 로 전이하지 않음
    assert state.gap_evaluated is False


def test_provider_Decimal_반환_prev_close_캐싱():
    """provider 가 Decimal 반환 시 state.prev_close 에 캐싱된다."""
    strategy = GapReversalStrategy(prev_close_provider=_make_provider(Decimal("10000")))
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 9800, 9850, 9780, 9810))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.prev_close == Decimal("10000")


# ---------------------------------------------------------------------------
# 3. 갭 진입 (5건)
# ---------------------------------------------------------------------------


def test_갭_하락_2pct_이상_EntrySignal_생성():
    """gap_pct = -2% (정확히 threshold) → EntrySignal + long 전이."""
    # prev_close=10000, bar.open=9800 → gap_pct = (9800-10000)/10000 = -2%
    cfg = GapReversalConfig(gap_threshold_pct=Decimal("0.02"))
    strategy = GapReversalStrategy(
        prev_close_provider=_make_provider(Decimal("10000")),
        config=cfg,
    )
    bar = _bar(_SYMBOL, 9, 0, 9800, 9850, 9780, 9810)
    result = strategy.on_bar(bar)

    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, EntrySignal)
    assert sig.symbol == _SYMBOL
    assert sig.price == Decimal("9810")  # bar.close

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.position_state == "long"


def test_갭_하락_threshold_미달_거부():
    """gap_pct = -1% (threshold 2% 미달) → 거부. gap_evaluated=True."""
    # prev_close=10000, bar.open=9900 → gap_pct = -1%
    cfg = GapReversalConfig(gap_threshold_pct=Decimal("0.02"))
    strategy = GapReversalStrategy(
        prev_close_provider=_make_provider(Decimal("10000")),
        config=cfg,
    )
    result = strategy.on_bar(_bar(_SYMBOL, 9, 0, 9900, 9950, 9890, 9920))

    assert result == []
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.position_state == "flat"
    assert state.gap_evaluated is True


def test_갭_상승_long_only_진입_거부():
    """gap_pct = +3% (갭 상승) → 거부 (long-only 정책)."""
    # prev_close=10000, bar.open=10300 → gap_pct = +3%
    cfg = GapReversalConfig(gap_threshold_pct=Decimal("0.02"))
    strategy = GapReversalStrategy(
        prev_close_provider=_make_provider(Decimal("10000")),
        config=cfg,
    )
    result = strategy.on_bar(_bar(_SYMBOL, 9, 0, 10300, 10350, 10280, 10310))

    assert result == []
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.position_state == "flat"
    assert state.gap_evaluated is True


def test_갭_하락_정확히_2pct_경계_진입():
    """gap_pct 가 정확히 -gap_threshold_pct (≤ 강이등호) → 진입."""
    # prev_close=10000, open=9800 → gap_pct=-0.02 == -threshold → 진입
    cfg = GapReversalConfig(gap_threshold_pct=Decimal("0.02"))
    strategy = GapReversalStrategy(
        prev_close_provider=_make_provider(Decimal("10000")),
        config=cfg,
    )
    result = strategy.on_bar(_bar(_SYMBOL, 9, 0, 9800, 9850, 9790, 9820))

    assert len(result) == 1
    assert isinstance(result[0], EntrySignal)


def test_갭_entry_window_end_이후_첫_bar_거부():
    """bar_t >= entry_window_end → 거부. debug 로그 수준이지만 flat 유지."""
    cfg = GapReversalConfig(entry_window_end=time(9, 30))
    strategy = GapReversalStrategy(
        prev_close_provider=_make_provider(Decimal("10000")),
        config=cfg,
    )
    # 09:30 bar — entry_window_end 이후이므로 거부
    result = strategy.on_bar(_bar(_SYMBOL, 9, 30, 9800, 9850, 9780, 9810))

    assert result == []
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.position_state == "flat"


def test_갭_EntrySignal_stop_take_price_정확():
    """EntrySignal 의 stop_price / take_price 가 설정값 기준으로 정확히 계산된다."""
    cfg = GapReversalConfig(
        gap_threshold_pct=Decimal("0.02"),
        stop_loss_pct=Decimal("0.01"),
        take_profit_pct=Decimal("0.015"),
    )
    strategy = GapReversalStrategy(
        prev_close_provider=_make_provider(Decimal("10000")),
        config=cfg,
    )
    # prev_close=10000, open=9800 → gap_pct=-2% → 진입, entry=bar.close=9810
    result = strategy.on_bar(_bar(_SYMBOL, 9, 0, 9800, 9850, 9780, 9810))

    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, EntrySignal)

    entry = Decimal("9810")
    expected_stop = entry * (Decimal("1") - Decimal("0.01"))
    expected_take = entry * (Decimal("1") + Decimal("0.015"))
    assert sig.stop_price == expected_stop
    assert sig.take_price == expected_take


# ---------------------------------------------------------------------------
# 4. 갭 평가 1회 가드 (2건)
# ---------------------------------------------------------------------------


def test_갭_평가_1회_gap_up_거부_후_재평가_안함():
    """첫 bar 에서 갭 상승 거부 후 같은 세션에서 다시 on_bar 호출 시 재평가 없음."""
    cfg = GapReversalConfig(gap_threshold_pct=Decimal("0.02"))
    strategy = GapReversalStrategy(
        prev_close_provider=_make_provider(Decimal("10000")),
        config=cfg,
    )
    # 첫 bar: gap_up → 거부, gap_evaluated=True
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 10300, 10350, 10280, 10310))

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.gap_evaluated is True

    # 두 번째 bar: 가격이 하락 상태여도 gap_evaluated=True 이므로 재평가 없음
    result = strategy.on_bar(_bar(_SYMBOL, 9, 1, 9800, 9850, 9780, 9810))
    assert result == []
    assert strategy.get_state(_SYMBOL).position_state == "flat"  # type: ignore[union-attr]


def test_갭_평가_1회_거부_후_flat_유지():
    """첫 bar 에서 threshold 미달 거부 후 flat 유지 — closed 전이 아님."""
    cfg = GapReversalConfig(gap_threshold_pct=Decimal("0.02"))
    strategy = GapReversalStrategy(
        prev_close_provider=_make_provider(Decimal("10000")),
        config=cfg,
    )
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 9950, 9980, 9940, 9960))  # gap=-0.5%, 미달

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.position_state == "flat"
    assert state.gap_evaluated is True


# ---------------------------------------------------------------------------
# 5. 청산 (3건)
# ---------------------------------------------------------------------------


def _setup_long_gap(
    strategy: GapReversalStrategy,
) -> Decimal:
    """prev_close=10000, open=9800 → gap_pct=-2% 진입. entry_price(bar.close) 반환."""
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 9800, 9850, 9780, 9810))
    return Decimal("9810")


def test_청산_bar_low_leq_stop_price_stop_loss():
    """bar.low ≤ stop_price → ExitSignal(stop_loss), closed 전이."""
    cfg = GapReversalConfig(
        gap_threshold_pct=Decimal("0.02"),
        stop_loss_pct=Decimal("0.01"),
        take_profit_pct=Decimal("0.015"),
    )
    strategy = GapReversalStrategy(
        prev_close_provider=_make_provider(Decimal("10000")),
        config=cfg,
    )
    _setup_long_gap(strategy)

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    stop = state.stop_price
    assert stop is not None

    result = strategy.on_bar(_bar(_SYMBOL, 9, 5, 9810, 9820, stop - Decimal("1"), 9815))

    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, ExitSignal)
    assert sig.reason == "stop_loss"
    assert sig.price == stop
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


def test_청산_bar_high_geq_take_price_take_profit():
    """bar.high ≥ take_price → ExitSignal(take_profit), closed 전이."""
    cfg = GapReversalConfig(
        gap_threshold_pct=Decimal("0.02"),
        stop_loss_pct=Decimal("0.01"),
        take_profit_pct=Decimal("0.015"),
    )
    strategy = GapReversalStrategy(
        prev_close_provider=_make_provider(Decimal("10000")),
        config=cfg,
    )
    _setup_long_gap(strategy)

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    take = state.take_price
    assert take is not None

    result = strategy.on_bar(_bar(_SYMBOL, 9, 5, 9810, take + Decimal("1"), 9800, 9820))

    assert len(result) == 1
    sig = result[0]
    assert isinstance(sig, ExitSignal)
    assert sig.reason == "take_profit"
    assert sig.price == take
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


def test_청산_stop_take_동시_성립_stop_우선():
    """같은 bar 에서 stop·take 모두 성립 → stop_loss 우선."""
    cfg = GapReversalConfig(
        gap_threshold_pct=Decimal("0.02"),
        stop_loss_pct=Decimal("0.01"),
        take_profit_pct=Decimal("0.015"),
    )
    strategy = GapReversalStrategy(
        prev_close_provider=_make_provider(Decimal("10000")),
        config=cfg,
    )
    _setup_long_gap(strategy)

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    stop = state.stop_price
    take = state.take_price
    assert stop is not None
    assert take is not None

    # low <= stop AND high >= take 동시 성립
    result = strategy.on_bar(
        _bar(_SYMBOL, 9, 5, 9810, take + Decimal("10"), stop - Decimal("1"), 9810)
    )

    assert len(result) == 1
    assert result[0].reason == "stop_loss"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 6. 강제청산 on_time (2건)
# ---------------------------------------------------------------------------


def test_강제청산_on_time_force_close_at_long_force_close():
    """on_time(force_close_at) 호출 시 long 심볼 → ExitSignal(force_close), price=last_close."""
    cfg = GapReversalConfig(
        gap_threshold_pct=Decimal("0.02"),
        stop_loss_pct=Decimal("0.01"),
        take_profit_pct=Decimal("0.015"),
        force_close_at=time(15, 0),
    )
    strategy = GapReversalStrategy(
        prev_close_provider=_make_provider(Decimal("10000")),
        config=cfg,
    )
    # 진입
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 9800, 9850, 9780, 9810))
    # stop/take 미성립인 분봉으로 last_close 갱신
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    stop = state.stop_price
    take = state.take_price
    assert stop is not None and take is not None
    # high < take, low > stop 인 bar
    strategy.on_bar(_bar(_SYMBOL, 14, 55, 9810, take - Decimal("10"), stop + Decimal("10"), 9815))

    signals = strategy.on_time(_now(15, 0))
    assert len(signals) == 1
    sig = signals[0]
    assert isinstance(sig, ExitSignal)
    assert sig.reason == "force_close"
    assert sig.price == Decimal("9815")  # last_close
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


def test_강제청산_flat_closed_심볼_무시():
    """flat / closed 상태 심볼은 on_time(force_close_at) 에서 시그널 없음."""
    cfg = GapReversalConfig(gap_threshold_pct=Decimal("0.02"))
    strategy = GapReversalStrategy(
        prev_close_provider=_make_provider(Decimal("10000")),
        config=cfg,
    )
    # flat 상태 — 갭 상승으로 진입 거부
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 10300, 10350, 10280, 10310))

    signals = strategy.on_time(_now(15, 0))
    assert signals == []


# ---------------------------------------------------------------------------
# 7. 세션 경계 (2건)
# ---------------------------------------------------------------------------


def test_세션_경계_새_date_진입_state_reset_provider_재호출():
    """새 날짜 bar 진입 시 state 리셋 + provider 가 새 session_date 인자로 재호출된다."""
    calls: list[tuple[str, date]] = []
    prev_closes = {_DATE: Decimal("10000"), date(2026, 4, 21): Decimal("9810")}

    def spy_provider(symbol: str, session_date: date) -> Decimal | None:
        calls.append((symbol, session_date))
        return prev_closes.get(session_date)

    cfg = GapReversalConfig(gap_threshold_pct=Decimal("0.02"))
    strategy = GapReversalStrategy(prev_close_provider=spy_provider, config=cfg)

    # 첫 날 — gap_up 으로 거부
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 10300, 10350, 10280, 10310))
    assert len(calls) == 1
    assert calls[0][1] == _DATE

    # 다음 날 — provider 재호출, session_date 갱신 확인
    next_day = date(2026, 4, 21)
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 9600, 9650, 9580, 9620, date_=next_day))
    assert len(calls) == 2
    assert calls[1] == (_SYMBOL, next_day)

    state = strategy.get_state(_SYMBOL)
    assert state is not None
    assert state.session_date == next_day


def test_세션_경계_어제_closed_새_세션_flat_재진입_가능():
    """전날 closed 상태여도 새 세션에서 flat 으로 리셋 → 재진입 가능."""
    cfg = GapReversalConfig(
        gap_threshold_pct=Decimal("0.02"),
        stop_loss_pct=Decimal("0.01"),
        take_profit_pct=Decimal("0.015"),
    )
    strategy = GapReversalStrategy(
        prev_close_provider=_make_provider(Decimal("10000")),
        config=cfg,
    )

    # 첫 날 진입 후 손절 → closed
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 9800, 9850, 9780, 9810))
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    stop = state.stop_price
    assert stop is not None
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 9810, 9820, stop - Decimal("1"), 9815))
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]

    # 새 세션 — 재진입 가능
    next_day = date(2026, 4, 21)
    result = strategy.on_bar(_bar(_SYMBOL, 9, 0, 9800, 9850, 9780, 9810, date_=next_day))

    assert len(result) == 1
    assert isinstance(result[0], EntrySignal)


# ---------------------------------------------------------------------------
# 8. 재진입 금지 (1건)
# ---------------------------------------------------------------------------


def test_재진입_금지_closed_후_같은_세션_재돌파():
    """closed 후 같은 세션에서 갭 진입 조건이 다시 성립해도 빈 리스트."""
    cfg = GapReversalConfig(
        gap_threshold_pct=Decimal("0.02"),
        stop_loss_pct=Decimal("0.01"),
        take_profit_pct=Decimal("0.015"),
    )
    strategy = GapReversalStrategy(
        prev_close_provider=_make_provider(Decimal("10000")),
        config=cfg,
    )

    # 진입 후 손절 → closed
    strategy.on_bar(_bar(_SYMBOL, 9, 0, 9800, 9850, 9780, 9810))
    state = strategy.get_state(_SYMBOL)
    assert state is not None
    stop = state.stop_price
    assert stop is not None
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 9810, 9820, stop - Decimal("1"), 9815))
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]

    # closed 후 같은 세션에서 추가 bar → 재진입 없음
    result = strategy.on_bar(_bar(_SYMBOL, 9, 10, 9800, 9850, 9780, 9810))
    assert result == []
    assert strategy.get_state(_SYMBOL).position_state == "closed"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 9. 입력 검증 (3건)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol",
    ["00593", "AAPL", "", "12345a", "0059300"],
    ids=["5자리", "영문", "빈문자열", "영문혼용", "7자리"],
)
def test_잘못된_symbol_RuntimeError(symbol: str):
    """유효하지 않은 symbol 로 on_bar 호출 → RuntimeError."""
    strategy = GapReversalStrategy(prev_close_provider=_make_provider(Decimal("10000")))
    bar = MinuteBar(
        symbol=symbol,
        bar_time=_now(9, 0),
        open=Decimal("9800"),
        high=Decimal("9850"),
        low=Decimal("9780"),
        close=Decimal("9810"),
        volume=1000,
    )
    with pytest.raises(RuntimeError, match="6자리 숫자"):
        strategy.on_bar(bar)


def test_naive_bar_time_RuntimeError():
    """bar.bar_time 이 naive datetime → RuntimeError (tz-aware 요구)."""
    strategy = GapReversalStrategy(prev_close_provider=_make_provider(Decimal("10000")))
    bar = MinuteBar(
        symbol=_SYMBOL,
        bar_time=datetime(2026, 4, 20, 9, 0),  # naive
        open=Decimal("9800"),
        high=Decimal("9850"),
        low=Decimal("9780"),
        close=Decimal("9810"),
        volume=1000,
    )
    with pytest.raises(RuntimeError, match="tz-aware"):
        strategy.on_bar(bar)


def test_bar_time_역행_RuntimeError():
    """같은 심볼에서 시간이 역행하는 bar → RuntimeError."""
    strategy = GapReversalStrategy(prev_close_provider=_make_provider(Decimal("10000")))
    strategy.on_bar(_bar(_SYMBOL, 9, 5, 10000, 10100, 9900, 10000))
    with pytest.raises(RuntimeError, match="역행"):
        strategy.on_bar(_bar(_SYMBOL, 9, 4, 10000, 10100, 9900, 10000))


def test_on_time_naive_RuntimeError():
    """on_time(naive datetime) → RuntimeError."""
    strategy = GapReversalStrategy(prev_close_provider=_make_provider(Decimal("10000")))
    with pytest.raises(RuntimeError):
        strategy.on_time(datetime(2026, 4, 20, 15, 0))  # naive
