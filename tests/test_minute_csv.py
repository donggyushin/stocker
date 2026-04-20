"""MinuteCsvBarLoader / MinuteCsvLoadError 공개 계약 단위 테스트.

CSV 분봉 어댑터의 포맷 검증·날짜 필터·심볼 필터·다중 심볼 병합을 검증한다.
실 네트워크·KIS API·외부 시계 의존은 없다 — tmp_path 격리 CSV 파일 전용.
"""

from __future__ import annotations

from datetime import date, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from stock_agent.data.minute_csv import MinuteCsvBarLoader, MinuteCsvLoadError
from stock_agent.data.realtime import MinuteBar

# ---------------------------------------------------------------------------
# 공통 상수
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_HEADER = "bar_time,open,high,low,close,volume"

_SYMBOL = "005930"
_SYMBOL_B = "000660"

_DATE = date(2026, 4, 20)
_DATE2 = date(2026, 4, 21)


# ---------------------------------------------------------------------------
# 헬퍼 함수
# ---------------------------------------------------------------------------


def _write_csv(
    tmp_path: Path,
    symbol: str,
    rows: list[str],
    *,
    header: str | None = _HEADER,
) -> Path:
    """tmp_path 안에 {symbol}.csv 를 작성하고 경로를 반환한다.

    header=None 이면 헤더 없이 rows 만 기록 (헤더 오류 테스트용).
    header 를 임의 문자열로 주면 그 내용을 그대로 첫 줄에 기록한다.
    """
    path = tmp_path / f"{symbol}.csv"
    lines: list[str] = []
    if header is not None:
        lines.append(header)
    lines.extend(rows)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _stream_list(
    loader: MinuteCsvBarLoader, start: date, end: date, symbols: tuple[str, ...]
) -> list[MinuteBar]:
    """stream() 결과를 list 로 소진해 반환하는 편의 래퍼."""
    return list(loader.stream(start, end, symbols))


# ---------------------------------------------------------------------------
# 시나리오 1 · 2 · 3 · 4 — 생성자 경로 검증
# ---------------------------------------------------------------------------


def test_생성자_정상_디렉토리_성공(tmp_path: Path) -> None:
    """시나리오 1: 올바른 디렉토리 Path 로 생성자 호출 → 성공."""
    loader = MinuteCsvBarLoader(tmp_path)
    assert loader.csv_dir == tmp_path


def test_생성자_파일_경로_전달시_MinuteCsvLoadError(tmp_path: Path) -> None:
    """시나리오 2: 디렉토리 대신 파일 경로 전달 → MinuteCsvLoadError."""
    file_path = tmp_path / "not_a_dir.csv"
    file_path.write_text("dummy", encoding="utf-8")

    with pytest.raises(MinuteCsvLoadError):
        MinuteCsvBarLoader(file_path)


def test_생성자_존재하지_않는_경로_MinuteCsvLoadError(tmp_path: Path) -> None:
    """시나리오 3: 존재하지 않는 경로 → MinuteCsvLoadError."""
    nonexistent = tmp_path / "no_such_dir"

    with pytest.raises(MinuteCsvLoadError):
        MinuteCsvBarLoader(nonexistent)


def test_생성자_문자열_전달시_MinuteCsvLoadError(tmp_path: Path) -> None:
    """시나리오 4: Path 가 아닌 str 전달 → MinuteCsvLoadError."""
    with pytest.raises(MinuteCsvLoadError):
        MinuteCsvBarLoader(str(tmp_path))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 시나리오 5 — 단일 심볼 정상 2행 stream
# ---------------------------------------------------------------------------


def test_단일_심볼_정상_2행_stream(tmp_path: Path) -> None:
    """시나리오 5: 정상 2행 → 순서대로 2개 MinuteBar 반환, 모두 KST."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        [
            "2026-04-20 09:00,10000,10500,9900,10200,1000",
            "2026-04-20 09:01,10200,10600,10100,10400,800",
        ],
    )
    loader = MinuteCsvBarLoader(tmp_path)
    bars = _stream_list(loader, _DATE, _DATE, (_SYMBOL,))

    assert len(bars) == 2
    b0, b1 = bars

    # symbol 은 파일명(stem) 과 일치 (시나리오 22)
    assert b0.symbol == _SYMBOL
    assert b1.symbol == _SYMBOL

    # tzinfo 는 항상 KST (시나리오 21)
    assert b0.bar_time.utcoffset() == timedelta(hours=9)
    assert b1.bar_time.utcoffset() == timedelta(hours=9)

    # OHLC 는 Decimal 타입 (시나리오 23)
    assert isinstance(b0.open, Decimal)
    assert isinstance(b0.high, Decimal)
    assert isinstance(b0.low, Decimal)
    assert isinstance(b0.close, Decimal)

    # 값 정확성
    assert b0.open == Decimal("10000")
    assert b0.high == Decimal("10500")
    assert b0.low == Decimal("9900")
    assert b0.close == Decimal("10200")
    assert b0.volume == 1000

    assert b1.open == Decimal("10200")
    assert b1.volume == 800


# ---------------------------------------------------------------------------
# 시나리오 6 — 심볼 2개 교차 시각 stream: (bar_time, symbol) 정렬
# ---------------------------------------------------------------------------


def test_다중_심볼_교차_시각_bar_time_symbol_정렬_순서(tmp_path: Path) -> None:
    """시나리오 6: 두 심볼 파일의 시각이 엇갈려 있을 때 heapq.merge 정렬 보장."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        [
            "2026-04-20 09:00,10000,10500,9900,10200,100",
            "2026-04-20 09:02,10300,10700,10200,10500,200",
        ],
    )
    _write_csv(
        tmp_path,
        _SYMBOL_B,
        [
            "2026-04-20 09:01,20000,20500,19900,20200,300",
            "2026-04-20 09:03,20300,20700,20200,20500,400",
        ],
    )
    loader = MinuteCsvBarLoader(tmp_path)
    bars = _stream_list(loader, _DATE, _DATE, (_SYMBOL, _SYMBOL_B))

    assert len(bars) == 4
    times = [b.bar_time.strftime("%H:%M") for b in bars]
    symbols = [b.symbol for b in bars]

    assert times == ["09:00", "09:01", "09:02", "09:03"]
    assert symbols == [_SYMBOL, _SYMBOL_B, _SYMBOL, _SYMBOL_B]


def test_다중_심볼_동일_시각_symbol_알파벳순_tie_break(tmp_path: Path) -> None:
    """두 심볼이 정확히 같은 bar_time 을 가질 때 symbol 오름차순으로 tie-break."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        ["2026-04-20 09:00,10000,10500,9900,10200,100"],
    )
    _write_csv(
        tmp_path,
        _SYMBOL_B,
        ["2026-04-20 09:00,20000,20500,19900,20200,200"],
    )
    loader = MinuteCsvBarLoader(tmp_path)
    bars = _stream_list(loader, _DATE, _DATE, (_SYMBOL, _SYMBOL_B))

    assert len(bars) == 2
    # _SYMBOL("005930") < _SYMBOL_B("000660") 는 사전순 비교: "000660" < "005930"
    assert bars[0].symbol == _SYMBOL_B
    assert bars[1].symbol == _SYMBOL


# ---------------------------------------------------------------------------
# 시나리오 7 · 8 · 9 · 32 — 날짜 필터
# ---------------------------------------------------------------------------


def test_날짜_필터_start_eq_end_eq_bar_date_포함(tmp_path: Path) -> None:
    """시나리오 7: start == end == bar.date → 포함."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        ["2026-04-20 09:00,10000,10500,9900,10200,100"],
    )
    loader = MinuteCsvBarLoader(tmp_path)
    bars = _stream_list(loader, _DATE, _DATE, (_SYMBOL,))

    assert len(bars) == 1


def test_날짜_필터_bar_date_lt_start_제외(tmp_path: Path) -> None:
    """시나리오 8: bar.date < start → 제외."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        [
            "2026-04-19 09:00,10000,10500,9900,10200,100",  # < start
            "2026-04-20 09:00,10100,10600,10000,10300,200",
        ],
    )
    loader = MinuteCsvBarLoader(tmp_path)
    bars = _stream_list(loader, _DATE, _DATE, (_SYMBOL,))

    assert len(bars) == 1
    assert bars[0].bar_time.date() == _DATE


def test_날짜_필터_bar_date_gt_end_제외(tmp_path: Path) -> None:
    """시나리오 9: bar.date > end → 제외."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        [
            "2026-04-20 09:00,10000,10500,9900,10200,100",
            "2026-04-21 09:00,10100,10600,10000,10300,200",  # > end
        ],
    )
    loader = MinuteCsvBarLoader(tmp_path)
    bars = _stream_list(loader, _DATE, _DATE, (_SYMBOL,))

    assert len(bars) == 1
    assert bars[0].bar_time.date() == _DATE


def test_날짜_필터_동일_날짜_하루치만_반환(tmp_path: Path) -> None:
    """시나리오 32: start==end 하루치 필터 — 해당 날짜만 반환."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        [
            "2026-04-19 15:00,9800,9900,9700,9800,50",
            "2026-04-20 09:00,10000,10500,9900,10200,100",
            "2026-04-20 09:01,10200,10600,10100,10400,80",
            "2026-04-21 09:00,10300,10700,10200,10500,120",
        ],
    )
    loader = MinuteCsvBarLoader(tmp_path)
    bars = _stream_list(loader, _DATE, _DATE, (_SYMBOL,))

    assert len(bars) == 2
    for b in bars:
        assert b.bar_time.date() == _DATE


# ---------------------------------------------------------------------------
# 시나리오 10 — 심볼 필터: 미요청 심볼 파일 오픈 안 함
# ---------------------------------------------------------------------------


def test_심볼_필터_미요청_심볼_파일은_오픈하지_않음(tmp_path: Path) -> None:
    """시나리오 10: symbols 에 없는 심볼의 CSV 는 열지 않는다."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        ["2026-04-20 09:00,10000,10500,9900,10200,100"],
    )
    # _SYMBOL_B 파일은 존재하지 않음 — 요청하지 않으면 에러가 나선 안 됨
    loader = MinuteCsvBarLoader(tmp_path)
    bars = _stream_list(loader, _DATE, _DATE, (_SYMBOL,))

    assert len(bars) == 1
    assert bars[0].symbol == _SYMBOL


# ---------------------------------------------------------------------------
# 시나리오 11 — 헤더만 있는 빈 파일 → 빈 stream
# ---------------------------------------------------------------------------


def test_헤더만_있는_빈_파일_빈_stream(tmp_path: Path) -> None:
    """시나리오 11: 헤더만 있고 데이터 행이 없는 파일 → 에러 없이 빈 스트림."""
    _write_csv(tmp_path, _SYMBOL, [])  # 헤더만 작성
    loader = MinuteCsvBarLoader(tmp_path)
    bars = _stream_list(loader, _DATE, _DATE, (_SYMBOL,))

    assert bars == []


# ---------------------------------------------------------------------------
# 시나리오 12 — volume "12345.0" → 정수 MinuteBar
# ---------------------------------------------------------------------------


def test_volume_실수_표기_정수값이면_정상_파싱(tmp_path: Path) -> None:
    """시나리오 12: volume='12345.0' → volume==12345 의 정수 MinuteBar."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        ["2026-04-20 09:00,10000,10500,9900,10200,12345.0"],
    )
    loader = MinuteCsvBarLoader(tmp_path)
    bars = _stream_list(loader, _DATE, _DATE, (_SYMBOL,))

    assert len(bars) == 1
    assert bars[0].volume == 12345
    assert isinstance(bars[0].volume, int)


# ---------------------------------------------------------------------------
# 시나리오 13 — start > end → RuntimeError
# ---------------------------------------------------------------------------


def test_start_gt_end_RuntimeError(tmp_path: Path) -> None:
    """시나리오 13: start > end 는 RuntimeError (MinuteCsvLoadError 아님)."""
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(RuntimeError):
        _stream_list(loader, _DATE2, _DATE, (_SYMBOL,))


# ---------------------------------------------------------------------------
# 시나리오 14 — 요청 심볼 CSV 누락 → MinuteCsvLoadError (경로 포함)
# ---------------------------------------------------------------------------


def test_요청_심볼_CSV_누락_MinuteCsvLoadError_경로포함(tmp_path: Path) -> None:
    """시나리오 14: 요청 심볼에 대응하는 CSV 없음 → MinuteCsvLoadError (경로 포함)."""
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError, match=str(tmp_path)):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


# ---------------------------------------------------------------------------
# 시나리오 15 · 16 — 헤더 오류
# ---------------------------------------------------------------------------


def test_헤더_없는_빈_파일_MinuteCsvLoadError(tmp_path: Path) -> None:
    """시나리오 15: 헤더가 전혀 없는 완전 빈 파일 → MinuteCsvLoadError."""
    _write_csv(tmp_path, _SYMBOL, [], header=None)
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


@pytest.mark.parametrize(
    "bad_header",
    [
        "bar_time,open,high,low,close",  # 컬럼 하나 누락
        "bar_time,open,high,low,close,vol",  # 마지막 컬럼 오타
        "bar_time,close,high,low,open,volume",  # 순서 다름
        "time,open,high,low,close,volume",  # 첫 컬럼 오타
    ],
    ids=["컬럼_누락", "컬럼_오타", "순서_다름", "첫컬럼_오타"],
)
def test_헤더_오타_또는_순서_다름_MinuteCsvLoadError(tmp_path: Path, bad_header: str) -> None:
    """시나리오 16: 헤더 오타 또는 컬럼 순서 다름 → MinuteCsvLoadError."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        ["2026-04-20 09:00,10000,10500,9900,10200,100"],
        header=bad_header,
    )
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


# ---------------------------------------------------------------------------
# 시나리오 17 — 컬럼 수 불일치 행 → MinuteCsvLoadError
# ---------------------------------------------------------------------------


def test_컬럼_수_불일치_행_MinuteCsvLoadError(tmp_path: Path) -> None:
    """시나리오 17: 데이터 행의 컬럼 수가 헤더와 다름 → MinuteCsvLoadError."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        ["2026-04-20 09:00,10000,10500,9900,10200"],  # 컬럼 하나 부족
    )
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


# ---------------------------------------------------------------------------
# 시나리오 18 · 19 · 20 — bar_time 파싱 오류
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_time",
    [
        "20260420 09:00",  # 날짜 구분자 없음
        "2026/04/20 09:00",  # 슬래시 구분자
        "not-a-time",  # 완전히 잘못된 형식
        "",  # 빈 값
    ],
    ids=["날짜구분자_없음", "슬래시_구분자", "완전_잘못된_형식", "빈값"],
)
def test_bar_time_파싱_실패_MinuteCsvLoadError(tmp_path: Path, bad_time: str) -> None:
    """시나리오 18: bar_time 파싱 불가 형식 → MinuteCsvLoadError."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        [f"{bad_time},10000,10500,9900,10200,100"],
    )
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


@pytest.mark.parametrize(
    "offset_time",
    [
        "2026-04-20 09:00:00+09:00",  # 양수 오프셋
        "2026-04-20 09:00:00Z",  # UTC Z
        "2026-04-20 09:00-05:00",  # 음수 오프셋
    ],
    ids=["양수_오프셋", "UTC_Z", "음수_오프셋"],
)
def test_bar_time_오프셋_포함_MinuteCsvLoadError(tmp_path: Path, offset_time: str) -> None:
    """시나리오 19: bar_time 에 타임존 오프셋 포함 → MinuteCsvLoadError."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        [f"{offset_time},10000,10500,9900,10200,100"],
    )
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


def test_bar_time_second_nonzero_MinuteCsvLoadError(tmp_path: Path) -> None:
    """시나리오 20: bar_time.second != 0 → 분 경계 위반 → MinuteCsvLoadError."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        ["2026-04-20 09:00:30,10000,10500,9900,10200,100"],  # second=30
    )
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


# ---------------------------------------------------------------------------
# 시나리오 21 · 22 — bar_time 역행·중복
# ---------------------------------------------------------------------------


def test_bar_time_역행_MinuteCsvLoadError(tmp_path: Path) -> None:
    """시나리오 21: 파일 내 bar_time 이 역행 → MinuteCsvLoadError."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        [
            "2026-04-20 09:01,10000,10500,9900,10200,100",
            "2026-04-20 09:00,10100,10600,10000,10300,80",  # 역행
        ],
    )
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


def test_bar_time_중복_MinuteCsvLoadError(tmp_path: Path) -> None:
    """시나리오 22: 파일 내 bar_time 중복 → MinuteCsvLoadError."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        [
            "2026-04-20 09:00,10000,10500,9900,10200,100",
            "2026-04-20 09:00,10100,10600,10000,10300,80",  # 중복
        ],
    )
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


# ---------------------------------------------------------------------------
# 시나리오 23 · 24 — 가격 음수·0·NaN·Infinity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_open",
    ["-1000", "0", "-0.1"],
    ids=["음수_가격", "0_가격", "음수_소수"],
)
def test_가격_음수_또는_0_MinuteCsvLoadError(tmp_path: Path, bad_open: str) -> None:
    """시나리오 23: 음수 또는 0 가격 → MinuteCsvLoadError."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        [f"2026-04-20 09:00,{bad_open},10500,9900,10200,100"],
    )
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


@pytest.mark.parametrize(
    "bad_price",
    ["NaN", "Inf", "-Inf", "Infinity", "-Infinity"],
    ids=["NaN", "Inf", "neg_Inf", "Infinity", "neg_Infinity"],
)
def test_가격_NaN_또는_Infinity_MinuteCsvLoadError(tmp_path: Path, bad_price: str) -> None:
    """시나리오 24: NaN / Infinity 가격 → MinuteCsvLoadError."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        [f"2026-04-20 09:00,{bad_price},10500,9900,10200,100"],
    )
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


# ---------------------------------------------------------------------------
# 시나리오 25 · 26 · 27 — OHLC 불일치
# ---------------------------------------------------------------------------


def test_OHLC_low_gt_high_MinuteCsvLoadError(tmp_path: Path) -> None:
    """시나리오 25: low > high → MinuteCsvLoadError."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        ["2026-04-20 09:00,10000,9000,9500,9800,100"],  # high=9000 < low=9500
    )
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


def test_OHLC_open_lt_low_MinuteCsvLoadError(tmp_path: Path) -> None:
    """시나리오 26: open < low → MinuteCsvLoadError."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        ["2026-04-20 09:00,9800,10500,10000,10200,100"],  # open=9800 < low=10000
    )
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


def test_OHLC_close_gt_high_MinuteCsvLoadError(tmp_path: Path) -> None:
    """시나리오 27: close > high → MinuteCsvLoadError."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        ["2026-04-20 09:00,10000,10500,9900,11000,100"],  # close=11000 > high=10500
    )
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


# ---------------------------------------------------------------------------
# 시나리오 28 · 29 — volume 오류
# ---------------------------------------------------------------------------


def test_volume_음수_MinuteCsvLoadError(tmp_path: Path) -> None:
    """시나리오 28: volume 음수 → MinuteCsvLoadError."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        ["2026-04-20 09:00,10000,10500,9900,10200,-100"],
    )
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


def test_volume_소수_MinuteCsvLoadError(tmp_path: Path) -> None:
    """시나리오 29: volume 이 정수 아닌 소수 (12.5) → MinuteCsvLoadError."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        ["2026-04-20 09:00,10000,10500,9900,10200,12.5"],
    )
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (_SYMBOL,))


# ---------------------------------------------------------------------------
# 시나리오 30 — 심볼 포맷 위반
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_symbol",
    ["ABC123", "12345", "1234567", "00593O", ""],
    ids=["영문_포함", "5자리", "7자리", "O가_포함", "빈문자열"],
)
def test_심볼_포맷_위반_MinuteCsvLoadError(tmp_path: Path, bad_symbol: str) -> None:
    """시나리오 30: 6자리 숫자 외 심볼 포맷 위반 → MinuteCsvLoadError."""
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(MinuteCsvLoadError):
        _stream_list(loader, _DATE, _DATE, (bad_symbol,))


# ---------------------------------------------------------------------------
# 시나리오 31 — symbols=() → RuntimeError (호출자 계약 위반)
# ---------------------------------------------------------------------------


def test_symbols_빈_튜플_RuntimeError(tmp_path: Path) -> None:
    """시나리오 31: symbols=() → RuntimeError (호출자 계약 위반)."""
    loader = MinuteCsvBarLoader(tmp_path)

    with pytest.raises(RuntimeError, match="symbols"):
        _stream_list(loader, _DATE, _DATE, ())


# ---------------------------------------------------------------------------
# 중복 심볼 전달 — 각각 독립 스트림으로 동일 bar 2배 yield
# ---------------------------------------------------------------------------


def test_중복_심볼_전달_각각_독립_스트림(tmp_path: Path) -> None:
    """symbols=("005930", "005930") → 각 심볼이 독립적으로 _sorted_bar_iter 에 연결되어
    동일 bar 가 2번 yield 된다 (현재 구현은 사전 dedup 을 하지 않는다는 계약 고정).
    """
    _write_csv(
        tmp_path,
        _SYMBOL,
        [
            "2026-04-20 09:00,10000,10500,9900,10200,100",
            "2026-04-20 09:01,10200,10600,10100,10400,80",
        ],
    )
    loader = MinuteCsvBarLoader(tmp_path)
    bars = _stream_list(loader, _DATE, _DATE, (_SYMBOL, _SYMBOL))

    # 원본 파일의 bar 수(2) × 중복 심볼 수(2) = 4
    assert len(bars) == 4


# ---------------------------------------------------------------------------
# 추가 경계 케이스: MinuteBar 필드 타입 일관성 및 tzinfo 보장
# ---------------------------------------------------------------------------


def test_MinuteBar_tzinfo_항상_KST(tmp_path: Path) -> None:
    """반환된 모든 MinuteBar 의 tzinfo 가 KST (UTC+09:00) 임을 확인한다."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        [
            "2026-04-20 09:00,10000,10500,9900,10200,100",
            "2026-04-20 09:01,10200,10600,10100,10400,80",
        ],
    )
    loader = MinuteCsvBarLoader(tmp_path)
    bars = _stream_list(loader, _DATE, _DATE, (_SYMBOL,))

    for bar in bars:
        assert bar.bar_time.utcoffset() == timedelta(hours=9)
        assert bar.bar_time.tzinfo == KST


def test_MinuteBar_symbol_파일명_stem과_일치(tmp_path: Path) -> None:
    """반환된 MinuteBar.symbol 이 CSV 파일명(stem) 과 일치하는지 확인한다."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        ["2026-04-20 09:00,10000,10500,9900,10200,100"],
    )
    loader = MinuteCsvBarLoader(tmp_path)
    bars = _stream_list(loader, _DATE, _DATE, (_SYMBOL,))

    assert bars[0].symbol == _SYMBOL


def test_bar_time_HH_MM_포맷_KST_부여(tmp_path: Path) -> None:
    """'YYYY-MM-DD HH:MM' (초 없는) 포맷도 KST 를 부여해 올바르게 파싱한다."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        ["2026-04-20 09:00,10000,10500,9900,10200,100"],  # 초 없는 형식
    )
    loader = MinuteCsvBarLoader(tmp_path)
    bars = _stream_list(loader, _DATE, _DATE, (_SYMBOL,))

    assert len(bars) == 1
    assert bars[0].bar_time.second == 0
    assert bars[0].bar_time.utcoffset() == timedelta(hours=9)


def test_volume_0은_허용(tmp_path: Path) -> None:
    """volume=0 은 음수가 아니므로 유효한 값이다."""
    _write_csv(
        tmp_path,
        _SYMBOL,
        ["2026-04-20 09:00,10000,10500,9900,10200,0"],
    )
    loader = MinuteCsvBarLoader(tmp_path)
    bars = _stream_list(loader, _DATE, _DATE, (_SYMBOL,))

    assert len(bars) == 1
    assert bars[0].volume == 0
