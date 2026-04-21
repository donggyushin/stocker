"""HistoricalDataStore 단위 테스트. pykrx/네트워크 호출은 전부 목킹한다."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pandas as pd
import pytest

from stock_agent.data.historical import (
    DailyBar,
    HistoricalDataError,
    HistoricalDataStore,
)

# ---------------------------------------------------------------------------
# 공통 상수
# ---------------------------------------------------------------------------

_TODAY = date(2026, 4, 19)
_CLOCK = lambda: datetime(2026, 4, 19, 10, 0)  # noqa: E731

_SYMBOL = "005930"
_START = date(2026, 4, 14)
_END = date(2026, 4, 18)  # < _TODAY → 캐시 적중 가능한 과거 구간


# ---------------------------------------------------------------------------
# 내부 헬퍼: DataFrame 더블 생성
# ---------------------------------------------------------------------------


def _make_ohlcv_df(
    rows: list[tuple[str, int, int, int, int, int]],
) -> pd.DataFrame:
    """한국어 컬럼 + pandas.Timestamp 인덱스를 가진 진짜 DataFrame 을 생성한다.

    rows: [(YYYY-MM-DD, open, high, low, close, volume), ...]
    pykrx get_market_ohlcv 단일 종목 모드는 거래대금을 반환하지 않으므로
    테스트 더블도 5컬럼(시가/고가/저가/종가/거래량)만 사용한다.
    """
    index = [pd.Timestamp(r[0]) for r in rows]
    data = {
        "시가": [r[1] for r in rows],
        "고가": [r[2] for r in rows],
        "저가": [r[3] for r in rows],
        "종가": [r[4] for r in rows],
        "거래량": [r[5] for r in rows],
    }
    # pandas stubs: Axes|None 만 허용 — list[Timestamp]/list[str] 가 좁혀지지 않는 한계
    return pd.DataFrame(data, index=cast(Any, index))


def _empty_ohlcv_df() -> pd.DataFrame:
    # pandas stubs: Axes|None 만 허용 — list[Timestamp]/list[str] 가 좁혀지지 않는 한계
    return pd.DataFrame(columns=cast(Any, ["시가", "고가", "저가", "종가", "거래량"]))


# ---------------------------------------------------------------------------
# 공통 픽스처
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_pykrx() -> MagicMock:
    """pykrx.stock 을 대체하는 MagicMock."""
    return MagicMock()


@pytest.fixture
def store(fake_pykrx: MagicMock) -> HistoricalDataStore:
    """:memory: DB + 고정 clock + fake_pykrx 주입 HistoricalDataStore."""
    return HistoricalDataStore(
        pykrx_factory=lambda: fake_pykrx,
        db_path=":memory:",
        clock=_CLOCK,
    )


# ---------------------------------------------------------------------------
# 테스트 1: pykrx_factory 주입 경로 — 첫 public API 호출 시 팩토리 호출
# ---------------------------------------------------------------------------


def test_pykrx_factory_주입된_팩토리가_첫_공개API_호출_시점에_실행된다(
    fake_pykrx: MagicMock,
) -> None:
    call_count = 0

    def counting_factory() -> MagicMock:
        nonlocal call_count
        call_count += 1
        return fake_pykrx

    df = _make_ohlcv_df([("2026-04-18", 73_000, 74_500, 72_800, 74_000, 12_000_000)])
    fake_pykrx.get_market_ohlcv.return_value = df

    s = HistoricalDataStore(
        pykrx_factory=counting_factory,
        db_path=":memory:",
        clock=_CLOCK,
    )
    # 아직 factory 호출 없어야 함
    assert call_count == 0

    s.fetch_daily_ohlcv(_SYMBOL, _START, _END)
    # 첫 public API 호출 후 팩토리가 정확히 1 회 실행
    assert call_count == 1

    # 두 번째 호출 시 (과거 구간 캐시 적중) — 팩토리 재호출 없음
    s.fetch_daily_ohlcv(_SYMBOL, _START, _END)
    assert call_count == 1


# ---------------------------------------------------------------------------
# 테스트 4: 일봉 정상 반환 + DTO 정규화
# ---------------------------------------------------------------------------


def test_일봉_정상_반환_및_DTO_정규화(
    store: HistoricalDataStore,
    fake_pykrx: MagicMock,
) -> None:
    df = _make_ohlcv_df(
        [
            ("2026-04-14", 73_000, 74_500, 72_800, 74_000, 12_000_000),
            ("2026-04-15", 74_200, 75_000, 73_500, 74_800, 9_500_000),
        ]
    )
    fake_pykrx.get_market_ohlcv.return_value = df

    result = store.fetch_daily_ohlcv(_SYMBOL, _START, _END)

    assert len(result) == 2
    bar = result[0]
    assert isinstance(bar, DailyBar)
    assert bar.symbol == _SYMBOL
    assert isinstance(bar.trade_date, date)
    assert bar.trade_date == date(2026, 4, 14)
    assert isinstance(bar.open, Decimal)
    assert isinstance(bar.high, Decimal)
    assert isinstance(bar.low, Decimal)
    assert isinstance(bar.close, Decimal)
    assert bar.open == Decimal("73000")
    assert bar.high == Decimal("74500")
    assert bar.low == Decimal("72800")
    assert bar.close == Decimal("74000")
    assert isinstance(bar.volume, int)
    assert bar.volume == 12_000_000


# ---------------------------------------------------------------------------
# 테스트 5: 과거 구간 캐시 적중 — end < today, DB 에 해당 행 존재
# ---------------------------------------------------------------------------


def test_과거_구간_캐시_적중_시_pykrx_재호출_없음(
    store: HistoricalDataStore,
    fake_pykrx: MagicMock,
) -> None:
    df = _make_ohlcv_df(
        [
            ("2026-04-14", 73_000, 74_500, 72_800, 74_000, 12_000_000),
            ("2026-04-18", 74_200, 75_000, 73_500, 74_800, 9_500_000),
        ]
    )
    fake_pykrx.get_market_ohlcv.return_value = df

    # 1차 호출 — 캐시 미스, pykrx 호출
    result1 = store.fetch_daily_ohlcv(_SYMBOL, _START, _END)
    assert fake_pykrx.get_market_ohlcv.call_count == 1

    # 2차 호출 — end(_END=2026-04-18) < today(2026-04-19), DB 에 end 행 존재 → 캐시 적중
    result2 = store.fetch_daily_ohlcv(_SYMBOL, _START, _END)
    assert fake_pykrx.get_market_ohlcv.call_count == 1  # 증가 없음
    assert len(result2) == len(result1)


# ---------------------------------------------------------------------------
# 테스트 6: 당일 포함 시 항상 재조회
# ---------------------------------------------------------------------------


def test_당일_포함_시_캐시_무관_pykrx_재호출(
    store: HistoricalDataStore,
    fake_pykrx: MagicMock,
) -> None:
    past_end = date(2026, 4, 18)
    today_end = _TODAY  # 2026-04-19

    df_past = _make_ohlcv_df([("2026-04-18", 74_200, 75_000, 73_500, 74_800, 9_500_000)])
    df_today = _make_ohlcv_df(
        [
            ("2026-04-18", 74_200, 75_000, 73_500, 74_800, 9_500_000),
            ("2026-04-19", 75_000, 76_000, 74_500, 75_500, 8_000_000),
        ]
    )

    # 과거 구간으로 1차 채우기
    fake_pykrx.get_market_ohlcv.return_value = df_past
    store.fetch_daily_ohlcv(_SYMBOL, past_end, past_end)
    assert fake_pykrx.get_market_ohlcv.call_count == 1

    # end = today 인 경우 — DB 에 end 이전 날짜 행이 있어도 반드시 재조회
    fake_pykrx.get_market_ohlcv.return_value = df_today
    store.fetch_daily_ohlcv(_SYMBOL, _START, today_end)
    assert fake_pykrx.get_market_ohlcv.call_count == 2  # 재호출 발생


# ---------------------------------------------------------------------------
# 테스트 7: pykrx None 반환 → HistoricalDataError
# ---------------------------------------------------------------------------


def test_pykrx_None_반환_시_HistoricalDataError(
    store: HistoricalDataStore,
    fake_pykrx: MagicMock,
) -> None:
    fake_pykrx.get_market_ohlcv.return_value = None

    with pytest.raises(HistoricalDataError):
        store.fetch_daily_ohlcv(_SYMBOL, _START, _END)


# ---------------------------------------------------------------------------
# 테스트 8: pykrx 빈 DataFrame → 빈 리스트, DB 에 행 없음
# ---------------------------------------------------------------------------


def test_pykrx_빈_DataFrame_반환_시_빈_리스트_반환(
    store: HistoricalDataStore,
    fake_pykrx: MagicMock,
) -> None:
    fake_pykrx.get_market_ohlcv.return_value = _empty_ohlcv_df()

    result = store.fetch_daily_ohlcv(_SYMBOL, _START, _END)

    assert result == []

    # DB 에도 행이 없어야 함
    cur = store._conn.cursor()
    count = cur.execute("SELECT COUNT(*) FROM daily_bars WHERE symbol = ?", (_SYMBOL,)).fetchone()[
        0
    ]
    cur.close()
    assert count == 0


# ---------------------------------------------------------------------------
# 테스트 9: 입력 가드 — symbol 포맷 오류 및 start > end
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol,start,end",
    [
        ("", date(2026, 4, 14), date(2026, 4, 18)),
        ("12345", date(2026, 4, 14), date(2026, 4, 18)),
        ("ABCDEF", date(2026, 4, 14), date(2026, 4, 18)),
        (_SYMBOL, date(2026, 4, 18), date(2026, 4, 14)),  # start > end
    ],
    ids=["symbol_빈문자열", "symbol_5자리", "symbol_영문", "start_gt_end"],
)
def test_입력_가드_유효하지않은_인자는_HistoricalDataError(
    store: HistoricalDataStore,
    fake_pykrx: MagicMock,
    symbol: str,
    start: date,
    end: date,
) -> None:
    with pytest.raises(HistoricalDataError):
        store.fetch_daily_ohlcv(symbol, start, end)

    fake_pykrx.get_market_ohlcv.assert_not_called()


# ---------------------------------------------------------------------------
# 테스트 10: pykrx 내부 예외 → HistoricalDataError 래핑, __cause__ 보존
# ---------------------------------------------------------------------------


def test_pykrx_내부예외_HistoricalDataError로_래핑_cause_보존(
    store: HistoricalDataStore,
    fake_pykrx: MagicMock,
) -> None:
    original = ValueError("pykrx 내부 오류")
    fake_pykrx.get_market_ohlcv.side_effect = original

    with pytest.raises(HistoricalDataError) as excinfo:
        store.fetch_daily_ohlcv(_SYMBOL, _START, _END)

    assert excinfo.value.__cause__ is original


# ---------------------------------------------------------------------------
# 테스트 11: db_path 격리 — :memory: 및 tmp_path 양쪽 정상 동작
# ---------------------------------------------------------------------------


def test_db_path_격리_메모리와_파일_모두_정상_동작(
    tmp_path: Path,
    fake_pykrx: MagicMock,
) -> None:
    df = _make_ohlcv_df([("2026-04-18", 73_000, 74_500, 72_800, 74_000, 12_000_000)])
    fake_pykrx.get_market_ohlcv.return_value = df

    # :memory: 케이스
    s_mem = HistoricalDataStore(
        pykrx_factory=lambda: fake_pykrx,
        db_path=":memory:",
        clock=_CLOCK,
    )
    result_mem = s_mem.fetch_daily_ohlcv(_SYMBOL, _START, _END)
    assert len(result_mem) == 1
    assert result_mem[0].symbol == _SYMBOL
    s_mem.close()

    # tmp_path / "t.db" 케이스
    db_file = tmp_path / "t.db"
    assert not db_file.exists()

    s_file = HistoricalDataStore(
        pykrx_factory=lambda: fake_pykrx,
        db_path=db_file,
        clock=_CLOCK,
    )
    result_file = s_file.fetch_daily_ohlcv(_SYMBOL, _START, _END)
    assert len(result_file) == 1
    assert result_file[0].symbol == _SYMBOL
    s_file.close()

    # 파일이 실제로 생성됐는지 확인
    assert db_file.exists()


# ---------------------------------------------------------------------------
# 테스트 12: 컨텍스트 매니저 — with 블록 종료 후 close 호출, 이후 API 사용 시 에러
# ---------------------------------------------------------------------------


def test_컨텍스트_매니저_블록_종료_후_close_호출_이후_에러(
    fake_pykrx: MagicMock,
) -> None:
    df = _make_ohlcv_df([("2026-04-18", 73_000, 74_500, 72_800, 74_000, 12_000_000)])
    fake_pykrx.get_market_ohlcv.return_value = df

    s = HistoricalDataStore(
        pykrx_factory=lambda: fake_pykrx,
        db_path=":memory:",
        clock=_CLOCK,
    )

    with s:
        result = s.fetch_daily_ohlcv(_SYMBOL, _START, _END)
        assert len(result) == 1

    # with 블록 종료 후 close 됨 — public API 호출 시 HistoricalDataError
    with pytest.raises(HistoricalDataError, match="close"):
        s.fetch_daily_ohlcv(
            _SYMBOL,
            _START,
            _END,
        )

    # close() 멱등 — 추가 호출 시 예외 없음
    s.close()
    s.close()


# ---------------------------------------------------------------------------
# 테스트 14: INSERT OR REPLACE 덮어쓰기 — 두 번째 pykrx 결과가 DB 에 반영됨
# ---------------------------------------------------------------------------


def test_insert_or_replace_두번째_pykrx_결과가_DB에_반영됨(
    fake_pykrx: MagicMock,
) -> None:
    # end = today(2026-04-19) 로 설정해 캐시 미스 경로를 두 번 강제
    today = _TODAY
    start = date(2026, 4, 19)

    df_first = _make_ohlcv_df([("2026-04-19", 73_000, 74_500, 72_800, 74_000, 12_000_000)])
    df_second = _make_ohlcv_df(
        # close 가 74_000 → 75_500 으로 바뀜
        [("2026-04-19", 73_000, 76_000, 72_500, 75_500, 13_000_000)]
    )

    s = HistoricalDataStore(
        pykrx_factory=lambda: fake_pykrx,
        db_path=":memory:",
        clock=_CLOCK,
    )

    # 1차 호출 (end=today → 항상 pykrx 재조회)
    fake_pykrx.get_market_ohlcv.return_value = df_first
    result1 = s.fetch_daily_ohlcv(_SYMBOL, start, today)
    assert result1[0].close == Decimal("74000")

    # 2차 호출 (end=today → 캐시 무관 재조회, 다른 값 주입)
    fake_pykrx.get_market_ohlcv.return_value = df_second
    result2 = s.fetch_daily_ohlcv(_SYMBOL, start, today)
    assert result2[0].close == Decimal("75500")

    # DB 에도 최신값이 반영됐는지 직접 확인
    cur = s._conn.cursor()
    row = cur.execute(
        "SELECT close FROM daily_bars WHERE symbol = ? AND trade_date = '2026-04-19'",
        (_SYMBOL,),
    ).fetchone()
    cur.close()
    assert row is not None
    assert Decimal(row[0]) == Decimal("75500")

    s.close()
