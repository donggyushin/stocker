"""DailyBarLoader 공개 계약 단위 테스트 (RED 단계).

대상 모듈: src/stock_agent/data/daily_bar_loader.py (미존재 — ImportError 로 FAIL 예상).
외부 네트워크·DB·시계 접촉 0. HistoricalDataStore 는 in-memory fake double 사용.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from stock_agent.data import DailyBar, MinuteBar

# ---------------------------------------------------------------------------
# 지연 import — 모듈 미존재 시 ImportError/ModuleNotFoundError 로 RED
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))


def _import_loader():
    from stock_agent.data.daily_bar_loader import DailyBarLoader

    return DailyBarLoader


def _import_kst():
    from stock_agent.data.daily_bar_loader import KST as MODULE_KST

    return MODULE_KST


# ---------------------------------------------------------------------------
# Fake doubles
# ---------------------------------------------------------------------------


def _make_daily_bar(
    trade_date: date,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: int,
    *,
    symbol: str = "069500",
) -> DailyBar:
    """테스트용 DailyBar 빌더 헬퍼."""
    return DailyBar(
        symbol=symbol,
        trade_date=trade_date,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
    )


def _make_expected_bar(
    symbol: str,
    trade_date: date,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: int,
) -> MinuteBar:
    """기대값 MinuteBar 빌더 헬퍼."""
    return MinuteBar(
        symbol=symbol,
        bar_time=datetime(
            trade_date.year,
            trade_date.month,
            trade_date.day,
            9,
            0,
            tzinfo=KST,
        ),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=volume,
    )


class _FakeStore:
    """HistoricalDataStore fake double.

    fetch_daily_ohlcv 의 반환값을 심볼별로 미리 주입하고 호출 인자를 캡처한다.
    """

    def __init__(
        self,
        results: dict[str, list[DailyBar]] | None = None,
        *,
        raise_on_symbol: str | None = None,
    ) -> None:
        self._results: dict[str, list[DailyBar]] = results or {}
        self._raise_on_symbol = raise_on_symbol
        self.fetch_calls: list[tuple[str, date, date]] = []
        self.close_count = 0

    def fetch_daily_ohlcv(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        self.fetch_calls.append((symbol, start, end))
        if self._raise_on_symbol and symbol == self._raise_on_symbol:
            raise RuntimeError(f"store error for symbol={symbol}")
        return self._results.get(symbol, [])

    def close(self) -> None:
        self.close_count += 1


# ---------------------------------------------------------------------------
# TestDailyBarLoaderStream — 정상 동작 6~8건
# ---------------------------------------------------------------------------


class TestDailyBarLoaderStream:
    """stream() 정상 동작 검증."""

    def test_단일심볼_단일영업일_1건_emit(self) -> None:
        """단일 심볼, 단일 영업일: bar_time = datetime(d, 09:00, KST), OHLC·volume 일치."""
        DailyBarLoader = _import_loader()
        d = date(2026, 4, 21)
        db = _make_daily_bar(d, "50000", "51000", "49000", "50500", 100_000)
        store = _FakeStore({"069500": [db]})

        loader = DailyBarLoader(daily_store=store)
        bars = list(loader.stream(d, d, ("069500",)))

        assert len(bars) == 1
        bar = bars[0]
        assert bar.symbol == "069500"
        assert bar.bar_time == datetime(2026, 4, 21, 9, 0, tzinfo=KST)
        assert bar.open == Decimal("50000")
        assert bar.high == Decimal("51000")
        assert bar.low == Decimal("49000")
        assert bar.close == Decimal("50500")
        assert bar.volume == 100_000

    def test_단일심볼_다중영업일_시간순_단조증가(self) -> None:
        """단일 심볼 3 영업일: bar_time 이 단조증가 순서로 emit."""
        DailyBarLoader = _import_loader()
        d1 = date(2026, 4, 21)
        d2 = date(2026, 4, 22)
        d3 = date(2026, 4, 23)
        bars_in = [
            _make_daily_bar(d1, "100", "110", "90", "105", 1000),
            _make_daily_bar(d2, "105", "115", "95", "110", 1100),
            _make_daily_bar(d3, "110", "120", "100", "115", 1200),
        ]
        store = _FakeStore({"069500": bars_in})

        loader = DailyBarLoader(daily_store=store)
        bars = list(loader.stream(d1, d3, ("069500",)))

        assert len(bars) == 3
        bar_times = [b.bar_time for b in bars]
        assert bar_times == sorted(bar_times), "bar_time 단조증가 계약 위반"
        assert bar_times[0].date() == d1
        assert bar_times[1].date() == d2
        assert bar_times[2].date() == d3

    def test_다중심볼_동일날짜_심볼_알파벳_오름차순(self) -> None:
        """2개 심볼 동일 날짜: (bar_time, symbol) 정렬 — 심볼 오름차순."""
        DailyBarLoader = _import_loader()
        d = date(2026, 4, 21)
        db_a = _make_daily_bar(d, "200", "210", "190", "205", 2000, symbol="005930")
        db_b = _make_daily_bar(d, "100", "110", "90", "105", 1000, symbol="069500")
        store = _FakeStore({"005930": [db_a], "069500": [db_b]})

        loader = DailyBarLoader(daily_store=store)
        bars = list(loader.stream(d, d, ("069500", "005930")))

        assert len(bars) == 2
        assert bars[0].symbol == "005930"
        assert bars[1].symbol == "069500"
        assert bars[0].bar_time == bars[1].bar_time  # 동일 날짜

    def test_다중심볼_다중날짜_시간우선_심볼알파벳(self) -> None:
        """2 심볼 × 2 날짜: 시간 우선 → 동일 시각은 심볼 알파벳 순."""
        DailyBarLoader = _import_loader()
        d1 = date(2026, 4, 21)
        d2 = date(2026, 4, 22)
        store = _FakeStore(
            {
                "005930": [
                    _make_daily_bar(d1, "200", "210", "190", "205", 2000, symbol="005930"),
                    _make_daily_bar(d2, "205", "215", "195", "210", 2100, symbol="005930"),
                ],
                "069500": [
                    _make_daily_bar(d1, "100", "110", "90", "105", 1000, symbol="069500"),
                    _make_daily_bar(d2, "105", "115", "95", "110", 1100, symbol="069500"),
                ],
            }
        )

        loader = DailyBarLoader(daily_store=store)
        bars = list(loader.stream(d1, d2, ("005930", "069500")))

        assert len(bars) == 4
        # 날짜 d1 → d1 → d2 → d2, 동일 날짜 내 심볼 오름차순
        assert bars[0].bar_time.date() == d1
        assert bars[1].bar_time.date() == d1
        assert bars[0].symbol < bars[1].symbol
        assert bars[2].bar_time.date() == d2
        assert bars[3].bar_time.date() == d2
        assert bars[2].symbol < bars[3].symbol

    def test_빈결과_빈_iterator(self) -> None:
        """fetch_daily_ohlcv 가 빈 리스트 반환 시 stream 도 빈 Iterator."""
        DailyBarLoader = _import_loader()
        d = date(2026, 4, 21)
        store = _FakeStore({"069500": []})

        loader = DailyBarLoader(daily_store=store)
        bars = list(loader.stream(d, d, ("069500",)))

        assert bars == []

    def test_재호출_안전_동일결과(self) -> None:
        """동일 인자로 stream 두 번 호출 → 각각 독립 소비 가능, 결과 동일."""
        DailyBarLoader = _import_loader()
        d = date(2026, 4, 21)
        db = _make_daily_bar(d, "50000", "51000", "49000", "50500", 100_000)
        # 두 번 호출에 대비해 결과 2번 반환
        store = _FakeStore({"069500": [db]})

        loader = DailyBarLoader(daily_store=store)
        bars1 = list(loader.stream(d, d, ("069500",)))
        bars2 = list(loader.stream(d, d, ("069500",)))

        assert len(bars1) == 1
        assert len(bars2) == 1
        assert bars1[0].bar_time == bars2[0].bar_time
        assert bars1[0].symbol == bars2[0].symbol
        assert bars1[0].close == bars2[0].close

    def test_volume_그대로_전달(self) -> None:
        """DailyBar.volume 이 MinuteBar.volume 으로 그대로 전달된다."""
        DailyBarLoader = _import_loader()
        d = date(2026, 4, 21)
        expected_volume = 9_999_999
        db = _make_daily_bar(d, "1000", "1100", "900", "1050", expected_volume)
        store = _FakeStore({"069500": [db]})

        loader = DailyBarLoader(daily_store=store)
        bars = list(loader.stream(d, d, ("069500",)))

        assert bars[0].volume == expected_volume

    def test_ohlc_decimal_정밀도_보존(self) -> None:
        """DailyBar OHLC Decimal 정밀도가 MinuteBar 로 손실 없이 전달된다."""
        DailyBarLoader = _import_loader()
        d = date(2026, 4, 21)
        open_ = Decimal("12345.67")
        high = Decimal("12400.00")
        low = Decimal("12300.50")
        close = Decimal("12380.25")
        db = DailyBar(
            symbol="069500",
            trade_date=d,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=5000,
        )
        store = _FakeStore({"069500": [db]})

        loader = DailyBarLoader(daily_store=store)
        bars = list(loader.stream(d, d, ("069500",)))

        assert bars[0].open == open_
        assert bars[0].high == high
        assert bars[0].low == low
        assert bars[0].close == close


# ---------------------------------------------------------------------------
# TestDailyBarLoaderInputValidation — 가드 3건
# ---------------------------------------------------------------------------


class TestDailyBarLoaderInputValidation:
    """stream() 입력 검증 — RuntimeError 가드."""

    def test_start_gt_end_RuntimeError(self) -> None:
        """start > end 시 RuntimeError, 메시지에 start/end 포함."""
        DailyBarLoader = _import_loader()
        store = _FakeStore()
        loader = DailyBarLoader(daily_store=store)

        start = date(2026, 4, 22)
        end = date(2026, 4, 21)
        with pytest.raises(RuntimeError, match=r"2026-04-22.*2026-04-21|2026-04-21.*2026-04-22"):
            list(loader.stream(start, end, ("069500",)))

    def test_symbols_빈튜플_RuntimeError(self) -> None:
        """symbols=() 시 RuntimeError."""
        DailyBarLoader = _import_loader()
        store = _FakeStore()
        loader = DailyBarLoader(daily_store=store)

        d = date(2026, 4, 21)
        with pytest.raises(RuntimeError):
            list(loader.stream(d, d, ()))

    def test_store_RuntimeError_전파(self) -> None:
        """store.fetch_daily_ohlcv 가 RuntimeError 던질 때 DailyBarLoader 가 그대로 전파."""
        DailyBarLoader = _import_loader()
        d = date(2026, 4, 21)
        store = _FakeStore(raise_on_symbol="069500")
        loader = DailyBarLoader(daily_store=store)

        with pytest.raises(RuntimeError, match="store error for symbol=069500"):
            list(loader.stream(d, d, ("069500",)))


# ---------------------------------------------------------------------------
# TestDailyBarLoaderLifecycle — 3건
# ---------------------------------------------------------------------------


class TestDailyBarLoaderLifecycle:
    """close() / 컨텍스트 매니저 수명주기 검증."""

    def test_close_호출시_store_close_1회(self) -> None:
        """close() 호출 시 daily_store.close() 정확히 1회 호출."""
        DailyBarLoader = _import_loader()
        store = _FakeStore()
        loader = DailyBarLoader(daily_store=store)

        loader.close()

        assert store.close_count == 1

    def test_컨텍스트_매니저_enter_자기자신_반환_exit_close(self) -> None:
        """with DailyBarLoader(...) as loader: __enter__ 는 self 반환, __exit__ 시 close."""
        DailyBarLoader = _import_loader()
        store = _FakeStore()

        with DailyBarLoader(daily_store=store) as loader:
            assert isinstance(loader, DailyBarLoader)

        assert store.close_count == 1

    def test_close_멱등_두번호출_RuntimeError없음(self) -> None:
        """close() 두 번 호출해도 RuntimeError 없음 (멱등)."""
        DailyBarLoader = _import_loader()
        store = _FakeStore()
        loader = DailyBarLoader(daily_store=store)

        loader.close()
        loader.close()  # 두 번째 호출 — RuntimeError 없어야 함

    # 멱등성 보장 여부 단언은 구현에 위임, 위 테스트는 "예외 없음" 만 검증


# ---------------------------------------------------------------------------
# TestDailyBarLoaderFetchDelegation — 2건
# ---------------------------------------------------------------------------


class TestDailyBarLoaderFetchDelegation:
    """stream() 호출 시 store.fetch_daily_ohlcv 위임 검증."""

    def test_단일심볼_fetch_1회_정확한_인자(self) -> None:
        """stream(start, end, ("069500",)) → fetch_daily_ohlcv("069500", start, end) 1회."""
        DailyBarLoader = _import_loader()
        start = date(2026, 4, 21)
        end = date(2026, 4, 25)
        store = _FakeStore({"069500": []})
        loader = DailyBarLoader(daily_store=store)

        list(loader.stream(start, end, ("069500",)))

        assert store.fetch_calls == [("069500", start, end)]

    def test_두심볼_fetch_각1회(self) -> None:
        """symbols=("069500", "005930") 시 fetch_daily_ohlcv 2회 (각 심볼 1회)."""
        DailyBarLoader = _import_loader()
        d = date(2026, 4, 21)
        store = _FakeStore({"069500": [], "005930": []})
        loader = DailyBarLoader(daily_store=store)

        list(loader.stream(d, d, ("069500", "005930")))

        called_symbols = {call[0] for call in store.fetch_calls}
        assert called_symbols == {"069500", "005930"}
        assert len(store.fetch_calls) == 2
