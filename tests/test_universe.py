"""universe.py 단위 테스트. load_kospi200_universe 공개 API와 KospiUniverse DTO를 검증한다."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

from stock_agent.data.universe import (
    KospiUniverse,
    UniverseLoadError,
    load_kospi200_universe,
)

# ---------------------------------------------------------------------------
# 헬퍼: tmp_path 에 universe YAML 파일 작성
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, content: Any, filename: str = "universe.yaml") -> Path:
    """`content` 를 YAML 직렬화해 tmp_path 에 저장하고 경로를 반환한다."""
    path = tmp_path / filename
    path.write_text(yaml.dump(content, allow_unicode=True), encoding="utf-8")
    return path


def _write_raw(tmp_path: Path, text: str, filename: str = "universe.yaml") -> Path:
    """raw 문자열을 그대로 파일에 저장한다 (파싱 오류 유발 등에 사용)."""
    path = tmp_path / filename
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 테스트 1: 정상 로드 — 오름차순 정렬·tuple 검증
# ---------------------------------------------------------------------------


def test_정상_로드(tmp_path: Path) -> None:
    """올바른 YAML → KospiUniverse 반환. 티커는 입력 순서와 무관하게 오름차순 tuple."""
    path = _write_yaml(
        tmp_path,
        {
            "as_of_date": "2026-03-01",
            "source": "KRX 2026 1Q 리밸런싱",
            "tickers": ["005930", "000660", "035420"],
        },
    )

    result = load_kospi200_universe(path)

    assert isinstance(result, KospiUniverse)
    assert isinstance(result.as_of_date, date)
    assert result.as_of_date == date(2026, 3, 1)
    assert result.source == "KRX 2026 1Q 리밸런싱"
    # 입력은 ["005930", "000660", "035420"] — 오름차순 정렬 확인
    assert result.tickers == ("000660", "005930", "035420")
    assert isinstance(result.tickers, tuple)


# ---------------------------------------------------------------------------
# 테스트 2: 파일 없음 → UniverseLoadError (경로 메시지 포함)
# ---------------------------------------------------------------------------


def test_파일_없음_UniverseLoadError(tmp_path: Path) -> None:
    """존재하지 않는 경로를 넘기면 UniverseLoadError 를 발생시킨다.
    오류 메시지에 경로 문자열이 포함되어야 한다."""
    missing = tmp_path / "nonexistent" / "universe.yaml"

    with pytest.raises(UniverseLoadError) as excinfo:
        load_kospi200_universe(missing)

    assert str(missing) in str(excinfo.value)


# ---------------------------------------------------------------------------
# 테스트 3: YAML 파싱 오류 → UniverseLoadError (__cause__ = yaml.YAMLError)
# ---------------------------------------------------------------------------


def test_YAML_파싱_오류_UniverseLoadError(tmp_path: Path) -> None:
    """손상된 YAML → UniverseLoadError 로 래핑. raw yaml.YAMLError 는 __cause__ 에 보존."""
    # 닫히지 않은 블록 스칼라 — yaml.safe_load 가 YAMLError 를 던진다.
    broken_yaml = 'tickers: [\n  - "005930\n'
    path = _write_raw(tmp_path, broken_yaml)

    with pytest.raises(UniverseLoadError) as excinfo:
        load_kospi200_universe(path)

    assert excinfo.value.__cause__ is not None
    assert isinstance(excinfo.value.__cause__, yaml.YAMLError)


# ---------------------------------------------------------------------------
# 테스트 4: 필수 키 누락 → UniverseLoadError (파라미터라이즈 3건)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing_key",
    ["as_of_date", "source", "tickers"],
    ids=["누락_as_of_date", "누락_source", "누락_tickers"],
)
def test_필수_키_누락_UniverseLoadError(tmp_path: Path, missing_key: str) -> None:
    """as_of_date / source / tickers 중 하나라도 빠지면 UniverseLoadError."""
    full: dict[str, Any] = {
        "as_of_date": "2026-03-01",
        "source": "KRX 2026 1Q",
        "tickers": ["005930"],
    }
    del full[missing_key]
    path = _write_yaml(tmp_path, full)

    with pytest.raises(UniverseLoadError):
        load_kospi200_universe(path)


# ---------------------------------------------------------------------------
# 테스트 5: 티커 포맷 위반 → UniverseLoadError (파라미터라이즈 4건)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_tickers",
    [
        ["12345"],  # 5자리
        ["ABCDEF"],  # 영문
        ["005930", "005930"],  # 중복
        [""],  # 빈 문자열
    ],
    ids=["5자리_티커", "영문_티커", "중복_티커", "빈문자열_티커"],
)
def test_티커_포맷_위반_UniverseLoadError(tmp_path: Path, bad_tickers: list[str]) -> None:
    """형식 위반 티커가 포함되면 UniverseLoadError."""
    path = _write_yaml(
        tmp_path,
        {
            "as_of_date": "2026-03-01",
            "source": "KRX 2026 1Q",
            "tickers": bad_tickers,
        },
    )

    with pytest.raises(UniverseLoadError):
        load_kospi200_universe(path)


# ---------------------------------------------------------------------------
# 테스트 6: 빈 tickers → 예외 없이 KospiUniverse(tickers=()) + logger.warning
# ---------------------------------------------------------------------------


def test_빈_tickers_경고_후_빈_유니버스_반환(tmp_path: Path, mocker: Any) -> None:
    """tickers: [] → UniverseLoadError 아님. 빈 KospiUniverse 반환 + warning 로그 1회."""
    path = _write_yaml(
        tmp_path,
        {
            "as_of_date": "2026-03-01",
            "source": "KRX 2026 1Q",
            "tickers": [],
        },
    )

    # loguru logger.warning 을 패치해 호출 여부 확인
    mock_warning = mocker.patch(
        "stock_agent.data.universe.logger.warning",
    )

    result = load_kospi200_universe(path)

    assert isinstance(result, KospiUniverse)
    assert result.tickers == ()
    mock_warning.assert_called_once()
