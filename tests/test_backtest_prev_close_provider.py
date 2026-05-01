"""DailyBarPrevCloseProvider 단위 테스트 (RED 단계).

대상 모듈: src/stock_agent/backtest/prev_close.py (미존재 — ImportError 로 FAIL 예상).
외부 네트워크·DB·실파일 접촉 0. HistoricalDataStore·BusinessDayCalendar 는 fake double 사용.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from stock_agent.data import DailyBar

# ---------------------------------------------------------------------------
# 지연 import — 모듈 미존재 시 ImportError/ModuleNotFoundError 로 RED
# ---------------------------------------------------------------------------


def _import_provider():
    from stock_agent.backtest.prev_close import DailyBarPrevCloseProvider

    return DailyBarPrevCloseProvider


# ---------------------------------------------------------------------------
# Fake doubles
# ---------------------------------------------------------------------------


def _make_daily_bar(
    symbol: str,
    trade_date: date,
    close: Decimal,
) -> DailyBar:
    """테스트용 DailyBar 헬퍼. OHLC 는 close 로 통일."""
    return DailyBar(
        symbol=symbol,
        trade_date=trade_date,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000,
    )


class _FakeStore:
    """HistoricalDataStore fake double.

    fetch_daily_ohlcv 의 반환값을 미리 주입하고 호출 인자를 캡처한다.
    """

    def __init__(self, result: list[DailyBar] | None = None) -> None:
        self._result: list[DailyBar] = result if result is not None else []
        self.fetch_calls: list[tuple[str, date, date]] = []
        self.close_count = 0

    def fetch_daily_ohlcv(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        self.fetch_calls.append((symbol, start, end))
        return self._result

    def close(self) -> None:
        self.close_count += 1


class _FakeCalendar:
    """BusinessDayCalendar fake double.

    날짜 → bool 매핑을 dict 로 주입한다. dict 에 없는 날짜는 False 반환.
    """

    def __init__(self, business_days: dict[date, bool] | None = None) -> None:
        self._bdays: dict[date, bool] = business_days or {}
        self.checked_dates: list[date] = []

    def is_business_day(self, day: date) -> bool:
        self.checked_dates.append(day)
        return self._bdays.get(day, False)


# ---------------------------------------------------------------------------
# 1. 정상 동작 — TestNormalLookup
# ---------------------------------------------------------------------------


class TestNormalLookup:
    """직전 영업일 종가를 정상적으로 반환하는 케이스."""

    def test_직전_영업일_종가_반환(self):
        """session_date=2026-04-21(화), 직전 영업일=2026-04-20(월) → 종가 반환."""
        DailyBarPrevCloseProvider = _import_provider()

        prev_day = date(2026, 4, 20)
        bar = _make_daily_bar("005930", prev_day, Decimal("70000"))
        store = _FakeStore(result=[bar])
        cal = _FakeCalendar(business_days={prev_day: True})
        provider = DailyBarPrevCloseProvider(store, cal)  # type: ignore[arg-type]

        result = provider("005930", date(2026, 4, 21))

        assert result == Decimal("70000")
        # fetch_daily_ohlcv 는 start==end==prev_day 로 호출돼야 한다
        assert len(store.fetch_calls) == 1
        sym, start, end = store.fetch_calls[0]
        assert sym == "005930"
        assert start == prev_day
        assert end == prev_day

    def test_session_date_월요일_금요일_종가(self):
        """session_date=2026-04-27(월). 토·일을 건너뛰어 금요일(2026-04-24)에서 종가 반환."""
        DailyBarPrevCloseProvider = _import_provider()

        fri = date(2026, 4, 24)
        # session_date=월요일, 그 전날=일요일 → False, 토요일 → False, 금요일 → True
        cal = _FakeCalendar(
            business_days={
                date(2026, 4, 26): False,  # 일요일
                date(2026, 4, 25): False,  # 토요일
                fri: True,
            }
        )
        bar = _make_daily_bar("005930", fri, Decimal("68000"))
        store = _FakeStore(result=[bar])
        provider = DailyBarPrevCloseProvider(store, cal)  # type: ignore[arg-type]

        result = provider("005930", date(2026, 4, 27))

        assert result == Decimal("68000")
        # 캘린더 호출 순서: 2026-04-26 → 2026-04-25 → 2026-04-24
        assert cal.checked_dates[0] == date(2026, 4, 26)
        assert cal.checked_dates[1] == date(2026, 4, 25)
        assert cal.checked_dates[2] == fri


# ---------------------------------------------------------------------------
# 2. None 반환 분기 — TestNoneFallback
# ---------------------------------------------------------------------------


class TestNoneFallback:
    """종가를 찾지 못할 때 None 을 반환하는 케이스."""

    def test_휴장일_빈리스트_None(self):
        """calendar 가 영업일 True 반환하지만 daily_store 가 빈 리스트 → None."""
        DailyBarPrevCloseProvider = _import_provider()

        prev_day = date(2026, 4, 20)
        cal = _FakeCalendar(business_days={prev_day: True})
        store = _FakeStore(result=[])
        provider = DailyBarPrevCloseProvider(store, cal)  # type: ignore[arg-type]

        result = provider("005930", date(2026, 4, 21))

        assert result is None

    def test_max_lookback_초과_None_warning(self, capfd):
        """14일(기본값) 역행해도 영업일 못 찾으면 None 반환 + logger.warning 발생."""
        DailyBarPrevCloseProvider = _import_provider()

        # 모든 날짜 False (dict 비워서 기본값 False 사용)
        cal = _FakeCalendar(business_days={})
        store = _FakeStore(result=[])

        # loguru warning 캡처

        from loguru import logger

        log_records: list[str] = []

        def sink(message):
            log_records.append(str(message))

        logger.add(sink, level="WARNING", format="{level}:{message}")

        try:
            provider = DailyBarPrevCloseProvider(store, cal, max_lookback_days=14)  # type: ignore[arg-type]
            result = provider("005930", date(2026, 4, 21))
        finally:
            logger.remove()

        assert result is None
        # fetch_daily_ohlcv 는 호출되지 않아야 한다 (영업일을 못 찾았으므로)
        assert len(store.fetch_calls) == 0
        # warning 로그가 최소 1개 발생해야 한다
        msg = f"logger.warning 이 발생하지 않았습니다. 캡처된 로그: {log_records}"
        assert any("warning" in r.lower() or "WARNING" in r for r in log_records), msg

    def test_max_lookback_커스텀_3일_None(self):
        """max_lookback_days=3, 모든 날짜 False → 3일 역행 후 None."""
        DailyBarPrevCloseProvider = _import_provider()

        cal = _FakeCalendar(business_days={})
        store = _FakeStore(result=[])
        provider = DailyBarPrevCloseProvider(store, cal, max_lookback_days=3)  # type: ignore[arg-type]

        result = provider("005930", date(2026, 4, 21))

        assert result is None
        # 정확히 3번만 is_business_day 를 호출해야 한다
        assert len(cal.checked_dates) == 3


# ---------------------------------------------------------------------------
# 3. 입력 가드 — TestGuards
# ---------------------------------------------------------------------------


class TestGuards:
    """잘못된 입력에 대해 RuntimeError 를 발생시키는 케이스."""

    @pytest.mark.parametrize(
        "bad_symbol",
        [
            pytest.param("ABC", id="영문_3자"),
            pytest.param("12345", id="숫자_5자리"),
            pytest.param("0000001", id="숫자_7자리"),
            pytest.param("", id="빈문자열"),
            pytest.param("12345X", id="숫자혼합영문"),
        ],
    )
    def test_symbol_6자리_위반_RuntimeError(self, bad_symbol: str):
        """6자리 숫자가 아닌 심볼 → RuntimeError."""
        DailyBarPrevCloseProvider = _import_provider()

        cal = _FakeCalendar(business_days={date(2026, 4, 20): True})
        store = _FakeStore()
        provider = DailyBarPrevCloseProvider(store, cal)  # type: ignore[arg-type]

        with pytest.raises(RuntimeError, match="6자리"):
            provider(bad_symbol, date(2026, 4, 21))

    @pytest.mark.parametrize(
        "bad_lookback",
        [
            pytest.param(0, id="0"),
            pytest.param(-1, id="음수_1"),
            pytest.param(-100, id="음수_100"),
        ],
    )
    def test_max_lookback_days_비정상_RuntimeError(self, bad_lookback: int):
        """max_lookback_days <= 0 → 생성자에서 RuntimeError."""
        DailyBarPrevCloseProvider = _import_provider()

        cal = _FakeCalendar()
        store = _FakeStore()

        with pytest.raises(RuntimeError):
            DailyBarPrevCloseProvider(store, cal, max_lookback_days=bad_lookback)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 4. close() 라이프사이클 — TestLifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    """close() 위임 및 컨텍스트 매니저 동작을 검증한다."""

    def test_close_daily_store_close_위임(self):
        """provider.close() → daily_store.close() 1회 호출."""
        DailyBarPrevCloseProvider = _import_provider()

        store = _FakeStore()
        cal = _FakeCalendar()
        provider = DailyBarPrevCloseProvider(store, cal)  # type: ignore[arg-type]

        provider.close()

        assert store.close_count == 1

    def test_close_멱등(self):
        """provider.close() 두 번 호출해도 RuntimeError 없음."""
        DailyBarPrevCloseProvider = _import_provider()

        store = _FakeStore()
        cal = _FakeCalendar()
        provider = DailyBarPrevCloseProvider(store, cal)  # type: ignore[arg-type]

        provider.close()
        provider.close()  # 두 번째 호출도 예외 없어야 한다

        assert store.close_count == 2

    def test_컨텍스트_매니저_close(self):
        """with 블록 종료 시 close() 가 호출된다."""
        DailyBarPrevCloseProvider = _import_provider()

        store = _FakeStore()
        prev_day = date(2026, 4, 20)
        cal = _FakeCalendar(business_days={prev_day: True})
        bar = _make_daily_bar("005930", prev_day, Decimal("72000"))
        store._result = [bar]

        with DailyBarPrevCloseProvider(store, cal) as p:  # type: ignore[arg-type]
            # with 블록 안에서 __call__ 정상 동작 확인
            result = p("005930", date(2026, 4, 21))
            assert result == Decimal("72000")

        # with 블록 exit 시 close() 호출됐어야 한다
        assert store.close_count == 1


# ---------------------------------------------------------------------------
# 5. fetch_daily_ohlcv 인자 검증 — TestStoreCall
# ---------------------------------------------------------------------------


class TestStoreCall:
    """fetch_daily_ohlcv 가 올바른 인자로 호출되는지 검증한다."""

    def test_prev_day_같은_start_end(self):
        """fetch_daily_ohlcv(symbol, start=prev_day, end=prev_day) 로 호출 — start == end."""
        DailyBarPrevCloseProvider = _import_provider()

        prev_day = date(2026, 4, 20)
        cal = _FakeCalendar(business_days={prev_day: True})
        bar = _make_daily_bar("035420", prev_day, Decimal("200000"))
        store = _FakeStore(result=[bar])
        provider = DailyBarPrevCloseProvider(store, cal)  # type: ignore[arg-type]

        provider("035420", date(2026, 4, 21))

        assert len(store.fetch_calls) == 1
        sym, start, end = store.fetch_calls[0]
        assert sym == "035420"
        assert start == end == prev_day

    def test_여러_session_date_매번_새_쿼리(self):
        """같은 symbol 의 다른 session_date 두 번 호출 → fetch_daily_ohlcv 두 번 호출."""
        DailyBarPrevCloseProvider = _import_provider()

        day1 = date(2026, 4, 20)  # session_date=2026-04-21 의 직전 영업일
        day2 = date(2026, 4, 21)  # session_date=2026-04-22 의 직전 영업일
        cal = _FakeCalendar(business_days={day1: True, day2: True})
        bar1 = _make_daily_bar("005930", day1, Decimal("70000"))
        bar2 = _make_daily_bar("005930", day2, Decimal("71000"))

        call_count = 0

        class _CountingStore:
            fetch_calls: list[tuple[str, date, date]] = []
            close_count = 0

            def fetch_daily_ohlcv(self, symbol: str, start: date, end: date) -> list[DailyBar]:
                nonlocal call_count
                call_count += 1
                self.fetch_calls.append((symbol, start, end))
                if start == day1:
                    return [bar1]
                return [bar2]

            def close(self) -> None:
                self.close_count += 1

        store = _CountingStore()
        provider = DailyBarPrevCloseProvider(store, cal)  # type: ignore[arg-type]

        result1 = provider("005930", date(2026, 4, 21))
        result2 = provider("005930", date(2026, 4, 22))

        assert call_count == 2
        assert result1 == Decimal("70000")
        assert result2 == Decimal("71000")
