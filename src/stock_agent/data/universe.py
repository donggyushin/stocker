"""KOSPI 200 종목 유니버스 YAML 로더.

stock-agent 는 ORB 전략의 후보 종목 풀로 KOSPI 200 을 쓴다. pykrx 지수 API 는
현재 KRX 서버와 호환이 깨져 있고 KIS Developers 는 인덱스 구성종목을 제공하지
않아, 구성종목 리스트는 `config/universe.yaml` 에 수동으로 관리한다.

운영 원칙
- KRX 가 분기 리밸런싱(3·6·9·12월) 발표 직후 운영자가 YAML 을 갱신한다.
- 갱신 이력은 git log 로 남겨 감사한다.
- 자동 갱신 스크립트는 Phase 5 후보.

정책
- 파일 없음·파싱 실패·필수 키 누락·티커 포맷 위반 → `UniverseLoadError`.
- `tickers` 가 비어 있으면 `logger.warning` 후 빈 `KospiUniverse` 반환.
  상위 레이어(Phase 3 `main.py`)가 "유니버스 비면 오늘 매매 중단" 을 명시적으로
  판단할 수 있게 하기 위해서다. 예외로 막으면 호출 지점에서 우회 핸들링이 복잡해진다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from loguru import logger

_DEFAULT_UNIVERSE_PATH = Path("config/universe.yaml")
_SYMBOL_RE = re.compile(r"^\d{6}$")
_REQUIRED_KEYS = ("as_of_date", "source", "tickers")


class UniverseLoadError(Exception):
    """유니버스 YAML 로드·검증 실패를 공통 표현."""


@dataclass(frozen=True, slots=True)
class KospiUniverse:
    """KOSPI 200 유니버스 스냅샷.

    - `as_of_date`: 운영자가 기록한 KRX 공지 기준일.
    - `source`: 자유형 출처 문자열. 갱신 근거 추적용.
    - `tickers`: 오름차순 정렬 6자리 티커 tuple. 중복 제거·정렬 후 저장.
    """

    as_of_date: date
    source: str
    tickers: tuple[str, ...]


def load_kospi200_universe(path: str | Path | None = None) -> KospiUniverse:
    """`path` (기본 `config/universe.yaml`) 의 유니버스를 로드해 반환한다."""
    target = Path(path) if path is not None else _DEFAULT_UNIVERSE_PATH

    raw = _read_yaml(target)
    as_of = _parse_as_of_date(raw, target)
    source = _parse_source(raw, target)
    tickers = _parse_tickers(raw, target)

    if not tickers:
        logger.warning(
            f"universe YAML 의 tickers 가 비어 있음 — 호출자가 매매 중단 판단 필요 (path={target})"
        )

    logger.info(f"universe 로드 완료 — path={target}, as_of={as_of.isoformat()}, n={len(tickers)}")
    return KospiUniverse(as_of_date=as_of, source=source, tickers=tickers)


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise UniverseLoadError(f"universe YAML 파일을 찾을 수 없습니다: {path}")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise UniverseLoadError(f"universe YAML 읽기 실패: {path} ({e!r})") from e
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise UniverseLoadError(f"universe YAML 파싱 실패: {path} ({e!r})") from e
    if not isinstance(parsed, dict):
        raise UniverseLoadError(
            f"universe YAML 최상위는 매핑이어야 합니다 (got={type(parsed).__name__}, path={path})"
        )
    missing = [k for k in _REQUIRED_KEYS if k not in parsed]
    if missing:
        raise UniverseLoadError(f"universe YAML 필수 키 누락: {missing} (path={path})")
    return parsed


def _parse_as_of_date(raw: dict, path: Path) -> date:
    value = raw["as_of_date"]
    if isinstance(value, date):
        return value  # yaml.safe_load 가 date 로 파싱한 경우
    if not isinstance(value, str):
        raise UniverseLoadError(
            f"as_of_date 는 'YYYY-MM-DD' 문자열이어야 합니다 "
            f"(got={type(value).__name__}, path={path})"
        )
    try:
        return date.fromisoformat(value)
    except ValueError as e:
        raise UniverseLoadError(f"as_of_date 파싱 실패: {value!r} (path={path})") from e


def _parse_source(raw: dict, path: Path) -> str:
    value = raw["source"]
    if not isinstance(value, str) or not value.strip():
        raise UniverseLoadError(
            f"source 는 비어있지 않은 문자열이어야 합니다 (got={value!r}, path={path})"
        )
    return value


def _parse_tickers(raw: dict, path: Path) -> tuple[str, ...]:
    value = raw["tickers"]
    if value is None:
        return ()
    if not isinstance(value, list):
        raise UniverseLoadError(
            f"tickers 는 리스트여야 합니다 (got={type(value).__name__}, path={path})"
        )
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            raise UniverseLoadError(
                f"tickers 원소는 문자열이어야 합니다 "
                f"(got={item!r}, type={type(item).__name__}, path={path})"
            )
        if not _SYMBOL_RE.match(item):
            raise UniverseLoadError(
                f"ticker 형식 위반 — 6자리 숫자 문자열이어야 합니다 (got={item!r}, path={path})"
            )
        if item in seen:
            raise UniverseLoadError(f"tickers 에 중복된 값이 있습니다: {item!r} (path={path})")
        seen.add(item)
    return tuple(sorted(seen))
