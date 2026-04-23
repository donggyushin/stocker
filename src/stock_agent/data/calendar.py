"""한국 증시 영업일 캘린더 — `BusinessDayCalendar` Protocol + YAML 기반 구현.

책임 범위
- KRX 휴장일 정보를 `config/holidays.yaml` 에서 로드해 영업일 판정
  (`is_business_day(day) -> bool`) 을 제공.
- 토(weekday=5)·일(weekday=6) 또는 등록된 공휴일 → `False`. 그 외 → `True`.

책임 범위 밖 (의도적 defer)
- 자동 캘린더 갱신 (pykrx, KRX 스크래핑 등). `config/universe.yaml` 운영 정책과
  동일하게 운영자가 KRX [12001] 휴장일 정보를 매년 12월 공식 공지 후 수동 갱신.
- 임시공휴일 자동 감지 — 정부 발표 후 운영자가 즉시 YAML 갱신.
- 거래시간(09:00~15:30) 판정 — 본 모듈은 날짜 단위만, 분 단위 판정은 호출자 책임.

설계 결정 (ADR-0018)
- 데이터 소스는 YAML 수동 관리. 결정론·네트워크 0·ADR-0011 "공휴일 수동 판정"
  기조 일치. pykrx 1.2.7 지수 API 가 KRX 서버와 호환 깨진 선례(historical.py
  주석 참조) 도 자동화 회피의 근거.
- `BusinessDayCalendar` Protocol 분리로 향후 다른 소스(pykrx 캐시 등) 도입 시
  `KisMinuteBarLoader` 등 소비자 변경 없이 교체 가능.
- `YamlBusinessDayCalendar` 는 `calendar` 프로퍼티에서 lazy YAML 로드
  (인스턴스화 시점에는 파일 접근 0). 첫 `is_business_day` 호출 시 1회만 로드 후
  캐시. 동일 인스턴스 재사용 시 파일 시스템 접근 1회 보장.

에러 정책 (`universe.py` 와 동일 기조)
- 파일 없음·파싱 실패·필수 키 누락·포맷 위반·중복 → `HolidayCalendarError`
  (메시지에 path 포함).
- `holidays: []` 빈 리스트는 허용 + `logger.warning` (호출자가 "공휴일 정보 없음
  → 주말만 차단" 을 명시적으로 인지).
- 모든 검증 실패 시점은 첫 `load_kospi_holidays` 호출 — 인스턴스화 자체는 부작용 0.

운영자 갱신 절차
- 매년 12월 KRX 공식 "다음 해 휴장일" 공지 발표 직후 `config/holidays.yaml`
  1년치 추가 + git commit. 임시공휴일은 발생 즉시 추가.
- KRX [12001] 휴장일 정보 (https://data.krx.co.kr) CSV 다운로드 활용.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml
from loguru import logger

_DEFAULT_HOLIDAYS_PATH = Path("config/holidays.yaml")
_REQUIRED_KEYS = ("as_of_date", "source", "holidays")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class HolidayCalendarError(Exception):
    """공휴일 YAML 로드·검증 실패 공통 예외."""


@dataclass(frozen=True, slots=True)
class HolidayCalendar:
    """공휴일 캘린더 스냅샷.

    - `as_of_date`: 운영자가 기록한 KRX 공지 기준일.
    - `source`: 자유형 출처 문자열. 갱신 근거 추적용.
    - `holidays`: 영업 휴장 날짜의 frozenset. 중복은 로드 단계에서 차단.
    """

    as_of_date: date
    source: str
    holidays: frozenset[date]


@runtime_checkable
class BusinessDayCalendar(Protocol):
    """영업일 판정 인터페이스.

    구현체는 토·일 차단 + 공휴일 차단을 모두 포함해야 한다 — 호출자는
    `is_business_day(day)` 한 번 호출로 영업일 여부를 단정한다.
    """

    def is_business_day(self, day: date) -> bool: ...


class YamlBusinessDayCalendar:
    """`config/holidays.yaml` 기반 한국 증시 영업일 판정.

    공개 동작:
        `is_business_day(day) -> bool` — 주말 또는 등록 공휴일 → `False`.
        `calendar` 프로퍼티 — 로드된 `HolidayCalendar` 반환.

    Lazy 로드: 인스턴스화 시점에는 파일 접근 0. 첫 `is_business_day` 또는
    `calendar` 접근 시 1회 로드 후 캐시. 같은 인스턴스 재사용 시 디스크 I/O 1회.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        """
        Args:
            path: 공휴일 YAML 경로. `None` → `config/holidays.yaml` 기본값.

        부작용 없음 — 실제 파일 로드는 `calendar` / `is_business_day` 첫 접근 시.
        """
        self._path = path
        self._calendar: HolidayCalendar | None = None

    @property
    def calendar(self) -> HolidayCalendar:
        """로드된 `HolidayCalendar` 반환. 첫 접근 시 YAML 로드 + 캐시."""
        if self._calendar is None:
            self._calendar = load_kospi_holidays(self._path)
        return self._calendar

    def is_business_day(self, day: date) -> bool:
        """`day` 가 영업일이면 `True`. 토·일·등록 공휴일 → `False`."""
        if day.weekday() >= 5:
            return False
        return day not in self.calendar.holidays


def load_kospi_holidays(path: str | Path | None = None) -> HolidayCalendar:
    """`path` (기본 `config/holidays.yaml`) 의 공휴일 YAML 을 로드해 반환."""
    target = Path(path) if path is not None else _DEFAULT_HOLIDAYS_PATH

    raw = _read_yaml(target)
    as_of = _parse_as_of_date(raw, target)
    source = _parse_source(raw, target)
    holidays = _parse_holidays(raw, target)

    if not holidays:
        logger.warning(
            f"holidays YAML 의 holidays 가 비어 있음 — 영업일 가드가 주말만 차단함 (path={target})"
        )

    logger.info(f"holidays 로드 완료 — path={target}, as_of={as_of.isoformat()}, n={len(holidays)}")
    return HolidayCalendar(
        as_of_date=as_of,
        source=source,
        holidays=frozenset(holidays),
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise HolidayCalendarError(f"holidays YAML 파일을 찾을 수 없습니다: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise HolidayCalendarError(f"holidays YAML 읽기 실패: {path} ({e!r})") from e
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise HolidayCalendarError(f"holidays YAML 파싱 실패: {path} ({e!r})") from e
    if not isinstance(parsed, dict):
        raise HolidayCalendarError(
            f"holidays YAML 최상위는 매핑이어야 합니다 (got={type(parsed).__name__}, path={path})"
        )
    missing = [k for k in _REQUIRED_KEYS if k not in parsed]
    if missing:
        raise HolidayCalendarError(f"holidays YAML 필수 키 누락: {missing} (path={path})")
    return parsed


def _parse_as_of_date(raw: dict[str, Any], path: Path) -> date:
    value = raw["as_of_date"]
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise HolidayCalendarError(
            f"as_of_date 는 'YYYY-MM-DD' 문자열이어야 합니다 "
            f"(got={type(value).__name__}, path={path})"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise HolidayCalendarError(f"as_of_date 파싱 실패: {value!r} (path={path})") from e


def _parse_source(raw: dict[str, Any], path: Path) -> str:
    value = raw["source"]
    if not isinstance(value, str) or not value.strip():
        raise HolidayCalendarError(
            f"source 는 비어있지 않은 문자열이어야 합니다 (got={value!r}, path={path})"
        )
    return value


def _parse_holidays(raw: dict[str, Any], path: Path) -> set[date]:
    value = raw["holidays"]
    if value is None:
        return set()
    if not isinstance(value, list):
        raise HolidayCalendarError(
            f"holidays 는 리스트여야 합니다 (got={type(value).__name__}, path={path})"
        )
    seen: set[date] = set()
    for item in value:
        day = _coerce_date_item(item, path)
        if day in seen:
            raise HolidayCalendarError(f"holidays 에 중복된 값이 있습니다: {day} (path={path})")
        seen.add(day)
    return seen


def _coerce_date_item(item: Any, path: Path) -> date:
    """holidays 원소 1건을 `date` 로 정규화. 실패 시 `HolidayCalendarError`."""
    if isinstance(item, datetime):
        return item.date()
    if isinstance(item, date):
        return item
    if isinstance(item, str):
        if not _DATE_RE.match(item):
            raise HolidayCalendarError(
                f"holidays 원소는 'YYYY-MM-DD' 포맷이어야 합니다 (got={item!r}, path={path})"
            )
        try:
            return date.fromisoformat(item)
        except ValueError as e:
            raise HolidayCalendarError(f"holidays 원소 파싱 실패: {item!r} (path={path})") from e
    raise HolidayCalendarError(
        f"holidays 원소는 문자열 또는 date 여야 합니다 "
        f"(got={item!r}, type={type(item).__name__}, path={path})"
    )
