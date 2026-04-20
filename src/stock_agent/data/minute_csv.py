"""심볼별 CSV 디렉토리를 BarLoader Protocol 로 스트리밍하는 과거 분봉 어댑터.

책임 범위
- `{csv_dir}/{symbol}.csv` 에서 과거 분봉을 시간 정렬 스트림으로 공급
- CSV 포맷 계약 검증 (헤더·정렬·중복·분 경계·OHLC 일관성)
- 여러 심볼의 파일을 `heapq.merge` 로 `(bar_time, symbol)` 순서 병합

범위 제외 (의도적)
- SQLite 캐시: 순수 스트리밍만. 성능 이슈 실측 후 후속 PR.
- KIS 과거 분봉 API: 30일 롤링 제약으로 2~3년 백테스트 부적합. 별도 PR.
- CSV 자동 생성·수집: 운영자가 외부에서 준비.

에러 정책 (`historical.py` / `realtime.py` 와 동일 기조)
- `RuntimeError` 는 전파 (`start > end`, 빈 `symbols` 등 호출자 계약 오류).
- 그 외 `Exception` 은 `MinuteCsvLoadError` 로 래핑 + `loguru.error` 로그.
  stdlib 원본 예외(파일/CSV/Decimal) 가 있는 경로는 `__cause__` 로 보존
  (`raise ... from exc`). 검증-실패형(포맷 계약 위반) 은 `from None` 으로
  체인을 끊어 "원본 예외 없음" 을 명시한다.
- 생성자는 디렉토리 경로 검증만 수행. 실제 파일 오픈은 `stream` 호출 시 지연.

CSV 포맷 계약
- 헤더: `bar_time,open,high,low,close,volume` (정확한 순서, 누락·오타 시 에러)
- `bar_time`: naive `YYYY-MM-DD HH:MM:SS` 또는 `YYYY-MM-DD HH:MM` (공백 구분).
  `T` 구분자 ISO8601·`+HH:MM` / `-HH:MM` / `Z` 오프셋은 모두 거부 (naive 명시적 강제).
- 가격: `Decimal(str)` 파싱 (float 우회 금지, `10000.1` 같은 소수 정밀도 보존).
  음수·0·`NaN`·`Infinity` 거부.
- 파일 내부 `bar_time` 단조증가 + `(symbol, bar_time)` 중복 금지
- 분 경계 (`second==0, microsecond==0`) 필수
- OHLC: 모두 양수 + `low <= min(open,close) <= max(open,close) <= high`
- 빈 파일(헤더만): 에러 아님 — 해당 심볼 빈 스트림

fail-fast 시점
- `stream()` 호출 즉시: `start>end`, 빈 symbols, 심볼 포맷 위반, 심볼 CSV 파일 누락.
- 소비(`next()`) 시점: 헤더 불일치·행 파싱 오류·OHLC 위반 등 파일 내용 기반 오류.
  (제너레이터 지연 오픈이라 `stream()` 호출만으로는 파일 내용 검증되지 않는다.)
"""

from __future__ import annotations

import csv
import heapq
import re
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from loguru import logger

from stock_agent.data.realtime import MinuteBar

KST = timezone(timedelta(hours=9))
_SYMBOL_RE = re.compile(r"^\d{6}$")
_TZ_OFFSET_RE = re.compile(r"(?:[+-]\d{2}:?\d{2}|Z)$")
"""bar_time 꼬리에 `+HH:MM` / `-HH:MM` / `+HHMM` / `-HHMM` / `Z` 가 붙으면 naive 계약 위반.

꼬리 앵커 `$` 로 고정해 'YYYY-MM-DD' 내부의 `-` 는 오탐하지 않는다.
"""
_EXPECTED_HEADER: tuple[str, ...] = (
    "bar_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
)
_BAR_TIME_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
)


class MinuteCsvLoadError(Exception):
    """CSV 분봉 로드 실패를 공통 표현.

    stdlib 원본 예외(`OSError` / `csv.Error` / `InvalidOperation` /
    `UnicodeDecodeError` 등)가 있는 경로는 `__cause__` 로 보존된다
    (`raise ... from exc`). 검증-실패형(포맷 계약 위반) 은 원본 예외가
    없으므로 `from None` 으로 체인을 끊어 "래핑 아님" 을 명시한다.
    """


class MinuteCsvBarLoader:
    """`{csv_dir}/{symbol}.csv` 레이아웃의 과거 분봉 어댑터.

    `BarLoader` Protocol (structural) 을 만족한다:
    `stream(start, end, symbols) -> Iterator[MinuteBar]`.

    스트리밍 보장:
    - `start <= bar.bar_time.date() <= end` (경계 포함)
    - `bar.symbol in symbols`
    - 시간 단조증가 (동일 시각 허용, `(bar_time, symbol)` 으로 tie-break)
    - `(symbol, bar_time)` 중복 없음 (파일 내 중복은 에러)

    Raises:
        MinuteCsvLoadError: 생성자 — `csv_dir` 가 `pathlib.Path` 가 아니거나
            존재하지 않거나 디렉토리가 아닐 때.
    """

    def __init__(self, csv_dir: Path) -> None:
        if not isinstance(csv_dir, Path):
            _raise_error(f"csv_dir 는 pathlib.Path 이어야 합니다: {type(csv_dir).__name__}")
        if not csv_dir.exists():
            _raise_error(f"csv_dir 가 존재하지 않습니다: {csv_dir}")
        if not csv_dir.is_dir():
            _raise_error(f"csv_dir 가 디렉토리가 아닙니다: {csv_dir}")
        self._csv_dir: Path = csv_dir

    @property
    def csv_dir(self) -> Path:
        """루트 디렉토리 Path 참조. 테스트·디버깅용 (`Path` 는 불변)."""
        return self._csv_dir

    def stream(
        self,
        start: date,
        end: date,
        symbols: tuple[str, ...],
    ) -> Iterator[MinuteBar]:
        """지정 구간·심볼의 분봉을 `(bar_time, symbol)` 순으로 yield.

        Args:
            start: 포함 시작 날짜 (KST 기준 trade_date).
            end: 포함 종료 날짜.
            symbols: 6자리 숫자 심볼 튜플 (1개 이상).

        Returns:
            `MinuteBar` 를 yield 하는 이터레이터. `heapq.merge` 기반 지연 병합.
            호출 시점에 파일 누락·심볼 포맷은 즉시 검증되지만, 헤더·행 포맷 오류는
            소비(`next()`) 시점에 `MinuteCsvLoadError` 로 raise 된다.

        Raises:
            RuntimeError: `start > end` 또는 `symbols` 가 빈 튜플일 때.
            MinuteCsvLoadError: 심볼 포맷 위반 또는 요청 심볼 CSV 파일 누락.
            MinuteCsvLoadError (지연): 소비 시점 파일 내용 포맷 위반.
        """
        if start > end:
            raise RuntimeError(f"start({start}) 는 end({end}) 이전이어야 합니다.")
        if not symbols:
            raise RuntimeError("symbols 는 1개 이상이어야 합니다.")
        for symbol in symbols:
            if not _SYMBOL_RE.match(symbol):
                _raise_error(f"symbol 은 6자리 숫자여야 합니다: {symbol!r}")

        per_symbol_iters = [
            _sorted_bar_iter(self._csv_dir, symbol, start, end) for symbol in symbols
        ]
        return heapq.merge(*per_symbol_iters, key=lambda b: (b.bar_time, b.symbol))


def _raise_error(msg: str, *, cause: BaseException | None = None) -> None:
    """`MinuteCsvLoadError` 로깅 + raise 헬퍼.

    `cause=None` → `from None` (검증-실패형, 원본 예외 없음).
    `cause=exc` → `from exc` (stdlib 예외 래핑, `__cause__` 보존).
    """
    logger.error(msg)
    if cause is None:
        raise MinuteCsvLoadError(msg) from None
    raise MinuteCsvLoadError(msg) from cause


def _sorted_bar_iter(
    csv_dir: Path,
    symbol: str,
    start: date,
    end: date,
) -> Iterator[MinuteBar]:
    """단일 심볼 CSV 를 지연 오픈해 MinuteBar 이터레이터로 변환.

    파일 존재 여부는 이 함수 진입 시점(`stream()` 호출 즉시)에 검증된다.
    파일 내부 단조증가·중복 금지 계약은 `_iter_symbol_file` 에서 강제한다.
    """
    file_path = csv_dir / f"{symbol}.csv"
    if not file_path.exists():
        _raise_error(f"심볼 {symbol} 의 CSV 가 없습니다: {file_path}")
    if not file_path.is_file():
        _raise_error(f"CSV 경로가 파일이 아닙니다: {file_path}")
    return _iter_symbol_file(file_path, symbol, start, end)


def _iter_symbol_file(
    file_path: Path,
    symbol: str,
    start: date,
    end: date,
) -> Iterator[MinuteBar]:
    try:
        handle = file_path.open("r", encoding="utf-8", newline="")
    except OSError as exc:
        _raise_error(f"CSV 파일 오픈 실패: {file_path}", cause=exc)
        return  # pragma: no cover — _raise_error 는 항상 raise.

    with handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            _raise_error(f"빈 CSV (헤더 없음): {file_path}")
        if tuple(header) != _EXPECTED_HEADER:
            _raise_error(f"헤더가 {_EXPECTED_HEADER} 와 일치하지 않습니다: {header} ({file_path})")

        last_bar_time: datetime | None = None
        try:
            for line_no, row in enumerate(reader, start=2):
                if len(row) != len(_EXPECTED_HEADER):
                    _raise_error(
                        f"컬럼 수 불일치 (기대 {len(_EXPECTED_HEADER)}, 실제 {len(row)}): "
                        f"{file_path}:{line_no}"
                    )
                bar = _parse_row(symbol, row, file_path, line_no)

                if last_bar_time is not None:
                    if bar.bar_time < last_bar_time:
                        _raise_error(
                            f"bar_time 역행: 이전 {last_bar_time.isoformat()} → "
                            f"현재 {bar.bar_time.isoformat()} ({file_path}:{line_no})"
                        )
                    if bar.bar_time == last_bar_time:
                        _raise_error(
                            f"bar_time 중복: {bar.bar_time.isoformat()} ({file_path}:{line_no})"
                        )
                last_bar_time = bar.bar_time

                bar_date = bar.bar_time.date()
                if bar_date < start:
                    continue
                if bar_date > end:
                    return
                yield bar
        except csv.Error as exc:
            _raise_error(f"CSV 파싱 실패: {file_path}", cause=exc)
        except UnicodeDecodeError as exc:
            _raise_error(f"CSV 인코딩 실패(UTF-8 아님): {file_path}", cause=exc)


def _parse_row(
    symbol: str,
    row: list[str],
    file_path: Path,
    line_no: int,
) -> MinuteBar:
    raw_time, raw_open, raw_high, raw_low, raw_close, raw_volume = row

    bar_time = _parse_bar_time(raw_time, file_path, line_no)
    open_ = _parse_price(raw_open, "open", file_path, line_no)
    high = _parse_price(raw_high, "high", file_path, line_no)
    low = _parse_price(raw_low, "low", file_path, line_no)
    close = _parse_price(raw_close, "close", file_path, line_no)
    volume = _parse_volume(raw_volume, file_path, line_no)

    _validate_ohlc(open_, high, low, close, file_path, line_no)

    return MinuteBar(
        symbol=symbol,
        bar_time=bar_time,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _parse_bar_time(raw: str, file_path: Path, line_no: int) -> datetime:
    text = raw.strip()
    if not text:
        _raise_error(f"bar_time 빈 값 ({file_path}:{line_no})")
    if _TZ_OFFSET_RE.search(text):
        _raise_error(
            f"bar_time 에 타임존 오프셋 포함 — naive 포맷만 허용: {raw!r} ({file_path}:{line_no})"
        )

    parsed: datetime | None = None
    for fmt in _BAR_TIME_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        _raise_error(
            f"bar_time 파싱 실패 (기대 'YYYY-MM-DD HH:MM[:SS]'): {raw!r} ({file_path}:{line_no})"
        )
    assert parsed is not None  # _raise_error 는 항상 raise — 타입 좁히기용.
    if parsed.second != 0 or parsed.microsecond != 0:
        _raise_error(f"bar_time 이 분 경계가 아닙니다: {raw!r} ({file_path}:{line_no})")
    return parsed.replace(tzinfo=KST)


def _parse_price(raw: str, field: str, file_path: Path, line_no: int) -> Decimal:
    text = raw.strip()
    if not text:
        _raise_error(f"{field} 빈 값 ({file_path}:{line_no})")
    try:
        value = Decimal(text)
    except InvalidOperation as exc:
        _raise_error(
            f"{field} 파싱 실패: {raw!r} ({file_path}:{line_no})",
            cause=exc,
        )
        return Decimal(0)  # pragma: no cover
    if not value.is_finite():
        _raise_error(f"{field} 가 유한값이 아닙니다: {raw!r} ({file_path}:{line_no})")
    if value <= 0:
        _raise_error(f"{field} 는 양수여야 합니다: {raw!r} ({file_path}:{line_no})")
    return value


def _parse_volume(raw: str, file_path: Path, line_no: int) -> int:
    text = raw.strip()
    if not text:
        _raise_error(f"volume 빈 값 ({file_path}:{line_no})")
    try:
        decimal_value = Decimal(text)
    except InvalidOperation as exc:
        _raise_error(
            f"volume 파싱 실패: {raw!r} ({file_path}:{line_no})",
            cause=exc,
        )
        return 0  # pragma: no cover
    if not decimal_value.is_finite():
        _raise_error(f"volume 이 유한값이 아닙니다: {raw!r} ({file_path}:{line_no})")
    if decimal_value < 0:
        _raise_error(f"volume 은 0 이상이어야 합니다: {raw!r} ({file_path}:{line_no})")
    if decimal_value != decimal_value.to_integral_value():
        _raise_error(f"volume 은 정수여야 합니다: {raw!r} ({file_path}:{line_no})")
    return int(decimal_value)


def _validate_ohlc(
    open_: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
    file_path: Path,
    line_no: int,
) -> None:
    if low > high:
        _raise_error(f"OHLC 불일치: low({low}) > high({high}) ({file_path}:{line_no})")
    body_low = min(open_, close)
    body_high = max(open_, close)
    if body_low < low:
        _raise_error(f"OHLC 불일치: min(open,close)={body_low} < low={low} ({file_path}:{line_no})")
    if body_high > high:
        _raise_error(
            f"OHLC 불일치: max(open,close)={body_high} > high={high} ({file_path}:{line_no})"
        )
