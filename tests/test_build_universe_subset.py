"""scripts/build_universe_subset.py 공개 계약 단위 테스트 (RED 명세).

build_subset / main(exit code) 를 검증한다.
실 KIS·pykrx·네트워크 호출 0. 파일 I/O 는 tmp_path 만.
load_kospi200_universe 는 실제 구현을 사용하되 tmp_path yaml 에만 접근한다.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# scripts/build_universe_subset.py 동적 로드
# (scripts/ 에 __init__.py 없음 — spec_from_file_location, build_liquidity_ranking 과 동일 패턴)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "build_universe_subset.py"

_src = str(_PROJECT_ROOT / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

_LOAD_ERROR: Exception | None = None
subset_cli = None  # type: ignore[assignment]

try:
    _spec = importlib.util.spec_from_file_location("subset_cli", _SCRIPT_PATH)
    if _spec is None or _spec.loader is None:
        raise ImportError(f"spec_from_file_location 이 None 반환: {_SCRIPT_PATH}")
    subset_cli = importlib.util.module_from_spec(_spec)
    sys.modules["subset_cli"] = subset_cli
    _spec.loader.exec_module(subset_cli)  # type: ignore[union-attr]
except Exception as _e:
    _LOAD_ERROR = _e


def _require_module() -> None:
    """각 테스트 시작 시 호출 — 모듈 로드 실패면 pytest.fail() 로 FAIL."""
    if _LOAD_ERROR is not None:
        pytest.fail(
            f"scripts/build_universe_subset.py 로드 실패 (RED 예상): {_LOAD_ERROR}",
            pytrace=False,
        )


# ---------------------------------------------------------------------------
# 공개 심볼 참조 래퍼
# ---------------------------------------------------------------------------


def build_subset(**kwargs):  # type: ignore[misc]
    _require_module()
    return subset_cli.build_subset(**kwargs)  # type: ignore[union-attr]


def main(argv=None):  # type: ignore[misc]
    _require_module()
    return subset_cli.main(argv)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 외부 의존 심볼
# ---------------------------------------------------------------------------

from stock_agent.data import (  # noqa: E402
    KospiUniverse,
    load_kospi200_universe,
)

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

# 테스트용 6자리 티커 5종 (알파벳 정렬 순서 고정)
_SYM_1 = "000660"  # rank_value=1 (가장 앞 alphabetically, 첫 번째로 테스트)
_SYM_2 = "005930"
_SYM_3 = "035420"
_SYM_4 = "051910"
_SYM_5 = "066570"

_ALL_SYMS = [_SYM_1, _SYM_2, _SYM_3, _SYM_4, _SYM_5]


def _write_ranking_csv(
    path: Path,
    rows: list[dict[str, str]],
) -> None:
    """ranking CSV 를 tmp_path 에 작성한다. rows 는 헤더 순서대로 dict."""
    fieldnames = ["symbol", "avg_value_krw", "daily_return_std", "sample_days", "rank_value"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _make_standard_ranking_csv(tmp_path: Path, symbols: list[str] | None = None) -> Path:
    """5종목 표준 ranking CSV 를 tmp_path/ranking.csv 에 작성한다.
    rank_value 는 1~5, avg_value_krw 내림차순 (rank 1 이 최대)."""
    syms = symbols if symbols is not None else _ALL_SYMS
    csv_path = tmp_path / "ranking.csv"
    rows = []
    for i, sym in enumerate(syms):
        rank = i + 1
        rows.append(
            {
                "symbol": sym,
                "avg_value_krw": str(1_000_000 - i * 100_000),
                "daily_return_std": "0.010000",
                "sample_days": "20",
                "rank_value": str(rank),
            }
        )
    _write_ranking_csv(csv_path, rows)
    return csv_path


def _read_output_yaml(path: Path) -> dict:
    """작성된 output yaml 을 dict 로 읽는다."""
    import yaml

    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ===========================================================================
# A. build_subset 정상 케이스
# ===========================================================================


class TestBuildSubsetNormal:
    """A-1 ~ A-4: 정상 경로 검증."""

    _AS_OF = date(2025, 4, 21)
    _SOURCE = "liquidity_ranking_top3"

    def test_A1_top3_추출_tickers_길이_및_포함(self, tmp_path: Path):
        """5종목 ranking CSV + top_n=3 → yaml tickers 길이 3, rank 1·2·3 종목 포함."""
        _require_module()
        csv_path = _make_standard_ranking_csv(tmp_path)
        output_yaml = tmp_path / "subset.yaml"

        build_subset(
            ranking_csv=csv_path,
            top_n=3,
            output_yaml=output_yaml,
            source=self._SOURCE,
            as_of=self._AS_OF,
        )

        assert output_yaml.exists(), "output_yaml 이 생성되어야 한다"
        data = _read_output_yaml(output_yaml)
        tickers = data["tickers"]
        assert len(tickers) == 3, f"tickers 길이 3 기대, got {len(tickers)}"
        # rank 1·2·3 종목이 포함되어야 한다
        for sym in _ALL_SYMS[:3]:
            assert sym in tickers, f"{sym} 이 tickers 에 없다"
        # rank 4·5 는 제외
        for sym in _ALL_SYMS[3:]:
            assert sym not in tickers, f"{sym} 이 tickers 에 포함되면 안 된다"

    def test_A2_yaml_schema_정확(self, tmp_path: Path):
        """as_of_date 가 ISO 문자열, source 가 인자 그대로, tickers 가 6자리 + 정렬."""
        _require_module()
        csv_path = _make_standard_ranking_csv(tmp_path)
        output_yaml = tmp_path / "subset.yaml"
        source_str = "test-source-2026"

        build_subset(
            ranking_csv=csv_path,
            top_n=3,
            output_yaml=output_yaml,
            source=source_str,
            as_of=self._AS_OF,
        )

        data = _read_output_yaml(output_yaml)

        # as_of_date 는 YYYY-MM-DD ISO 문자열
        assert "as_of_date" in data, "as_of_date 키 없음"
        as_of_val = data["as_of_date"]
        # yaml 로더가 date 객체로 파싱할 수 있으므로 str 또는 date 모두 허용
        as_of_str = as_of_val.isoformat() if isinstance(as_of_val, date) else str(as_of_val)
        assert as_of_str == self._AS_OF.isoformat(), f"as_of_date 불일치: {as_of_str!r}"

        # source 가 인자 그대로
        assert data.get("source") == source_str, f"source 불일치: {data.get('source')!r}"

        # tickers 는 리스트, 6자리 숫자 문자열, 정렬
        tickers = data["tickers"]
        assert isinstance(tickers, list), "tickers 가 list 가 아님"
        for t in tickers:
            ticker_msg = f"ticker {t!r} 가 6자리 숫자 문자열이 아님"
            assert isinstance(t, str) and len(t) == 6 and t.isdigit(), ticker_msg
        assert tickers == sorted(tickers), f"tickers 가 정렬되지 않음: {tickers}"

    def test_A3_load_kospi200_universe_자체검증_통과(self, tmp_path: Path):
        """작성된 yaml 을 load_kospi200_universe 로 다시 읽어 KospiUniverse 복원."""
        _require_module()
        csv_path = _make_standard_ranking_csv(tmp_path)
        output_yaml = tmp_path / "subset.yaml"
        source_str = "step-c-test"

        build_subset(
            ranking_csv=csv_path,
            top_n=3,
            output_yaml=output_yaml,
            source=source_str,
            as_of=self._AS_OF,
        )

        # 로더로 재검증
        universe = load_kospi200_universe(output_yaml)
        assert isinstance(universe, KospiUniverse)
        assert universe.as_of_date == self._AS_OF
        assert universe.source == source_str
        assert len(universe.tickers) == 3
        # rank 1~3 종목이 tuple 에 포함
        for sym in _ALL_SYMS[:3]:
            assert sym in universe.tickers, f"{sym} 이 universe.tickers 에 없다"

    def test_A4_출력_디렉터리_자동_생성(self, tmp_path: Path):
        """output_yaml.parent 미존재 → mkdir 후 파일 작성."""
        _require_module()
        csv_path = _make_standard_ranking_csv(tmp_path)
        deep_dir = tmp_path / "nested" / "deep" / "dir"
        output_yaml = deep_dir / "subset.yaml"
        assert not deep_dir.exists(), "사전 조건: 디렉터리가 없어야 한다"

        build_subset(
            ranking_csv=csv_path,
            top_n=2,
            output_yaml=output_yaml,
            source="auto-mkdir-test",
            as_of=self._AS_OF,
        )

        assert output_yaml.exists(), "출력 파일이 생성되어야 한다"


# ===========================================================================
# B. fail-fast / 입력 가드
# ===========================================================================


class TestBuildSubsetGuards:
    """B-5 ~ B-9: 입력 검증·fail-fast."""

    _AS_OF = date(2025, 4, 21)

    def test_B5_top_n_zero_RuntimeError(self, tmp_path: Path):
        """top_n=0 → RuntimeError."""
        _require_module()
        csv_path = _make_standard_ranking_csv(tmp_path)
        output_yaml = tmp_path / "subset.yaml"

        with pytest.raises(RuntimeError):
            build_subset(
                ranking_csv=csv_path,
                top_n=0,
                output_yaml=output_yaml,
                source="test",
                as_of=self._AS_OF,
            )

    def test_B5_top_n_negative_RuntimeError(self, tmp_path: Path):
        """top_n=-1 → RuntimeError."""
        _require_module()
        csv_path = _make_standard_ranking_csv(tmp_path)
        output_yaml = tmp_path / "subset.yaml"

        with pytest.raises(RuntimeError):
            build_subset(
                ranking_csv=csv_path,
                top_n=-1,
                output_yaml=output_yaml,
                source="test",
                as_of=self._AS_OF,
            )

    def test_B6_top_n_초과_RuntimeError(self, tmp_path: Path):
        """top_n=10 인데 CSV 에 종목이 5개뿐 → RuntimeError."""
        _require_module()
        csv_path = _make_standard_ranking_csv(tmp_path)  # 5종목
        output_yaml = tmp_path / "subset.yaml"

        with pytest.raises(RuntimeError):
            build_subset(
                ranking_csv=csv_path,
                top_n=10,
                output_yaml=output_yaml,
                source="test",
                as_of=self._AS_OF,
            )

    def test_B7_ranking_csv_누락_FileNotFoundError_또는_OSError(self, tmp_path: Path):
        """ranking_csv 가 존재하지 않으면 FileNotFoundError 또는 OSError."""
        _require_module()
        missing_csv = tmp_path / "nonexistent_ranking.csv"
        output_yaml = tmp_path / "subset.yaml"

        with pytest.raises((FileNotFoundError, OSError)):
            build_subset(
                ranking_csv=missing_csv,
                top_n=3,
                output_yaml=output_yaml,
                source="test",
                as_of=self._AS_OF,
            )

    def test_B8_csv_헤더_rank_value_컬럼_부재_RuntimeError(self, tmp_path: Path):
        """rank_value 컬럼이 없는 CSV → RuntimeError."""
        _require_module()
        csv_path = tmp_path / "bad_ranking.csv"
        # rank_value 없는 헤더
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["symbol", "avg_value_krw", "daily_return_std", "sample_days"]
            )
            writer.writeheader()
            writer.writerow(
                {
                    "symbol": "005930",
                    "avg_value_krw": "1000000",
                    "daily_return_std": "0.01",
                    "sample_days": "20",
                }
            )
        output_yaml = tmp_path / "subset.yaml"

        with pytest.raises(RuntimeError):
            build_subset(
                ranking_csv=csv_path,
                top_n=1,
                output_yaml=output_yaml,
                source="test",
                as_of=self._AS_OF,
            )

    def test_B8_csv_헤더_완전_누락_RuntimeError(self, tmp_path: Path):
        """헤더가 전혀 없는 빈 CSV (0바이트) → RuntimeError."""
        _require_module()
        csv_path = tmp_path / "empty.csv"
        csv_path.write_text("", encoding="utf-8")
        output_yaml = tmp_path / "subset.yaml"

        with pytest.raises(RuntimeError):
            build_subset(
                ranking_csv=csv_path,
                top_n=1,
                output_yaml=output_yaml,
                source="test",
                as_of=self._AS_OF,
            )

    @pytest.mark.parametrize(
        "bad_rows",
        [
            pytest.param(
                [
                    {
                        "symbol": "000660",
                        "avg_value_krw": "900000",
                        "daily_return_std": "0.01",
                        "sample_days": "20",
                        "rank_value": "-1",
                    },
                    {
                        "symbol": "005930",
                        "avg_value_krw": "800000",
                        "daily_return_std": "0.01",
                        "sample_days": "20",
                        "rank_value": "2",
                    },
                ],
                id="음수_rank_value",
            ),
            pytest.param(
                [
                    {
                        "symbol": "000660",
                        "avg_value_krw": "900000",
                        "daily_return_std": "0.01",
                        "sample_days": "20",
                        "rank_value": "1",
                    },
                    {
                        "symbol": "005930",
                        "avg_value_krw": "800000",
                        "daily_return_std": "0.01",
                        "sample_days": "20",
                        "rank_value": "1",
                    },
                ],
                id="중복_rank_value",
            ),
            pytest.param(
                [
                    {
                        "symbol": "000660",
                        "avg_value_krw": "900000",
                        "daily_return_std": "0.01",
                        "sample_days": "20",
                        "rank_value": "2",
                    },
                    {
                        "symbol": "005930",
                        "avg_value_krw": "800000",
                        "daily_return_std": "0.01",
                        "sample_days": "20",
                        "rank_value": "3",
                    },
                ],
                id="rank_value_1_누락",
            ),
        ],
    )
    def test_B9_잘못된_rank_value_RuntimeError(self, tmp_path: Path, bad_rows: list[dict]):
        """음수·중복·1..top_n 빠진 rank_value → RuntimeError."""
        _require_module()
        csv_path = tmp_path / "bad_rank.csv"
        _write_ranking_csv(csv_path, bad_rows)
        output_yaml = tmp_path / "subset.yaml"

        with pytest.raises(RuntimeError):
            build_subset(
                ranking_csv=csv_path,
                top_n=1,
                output_yaml=output_yaml,
                source="test",
                as_of=self._AS_OF,
            )


# ===========================================================================
# C. CLI main()
# ===========================================================================


class TestMainCli:
    """C-10 ~ C-12: CLI main() exit code 검증."""

    _AS_OF_STR = "2025-04-21"

    def _patch_build_subset(self, monkeypatch, *, side_effect=None, return_value=None):
        """build_subset 을 monkeypatch 로 대체."""
        _require_module()
        if side_effect is not None:
            mock = MagicMock(side_effect=side_effect)
        else:
            mock = MagicMock(return_value=return_value)
        monkeypatch.setattr(subset_cli, "build_subset", mock)
        return mock

    def test_C10_정상_return_0_yaml_생성(self, tmp_path: Path, monkeypatch):
        """정상 경로 — build_subset mock 정상 반환 → return 0."""
        _require_module()
        csv_path = _make_standard_ranking_csv(tmp_path)
        output_yaml = tmp_path / "subset.yaml"

        mock_bs = self._patch_build_subset(monkeypatch)

        result = main(
            [
                f"--ranking-csv={csv_path}",
                "--top-n=3",
                f"--output-yaml={output_yaml}",
                "--source=test-source",
                f"--as-of={self._AS_OF_STR}",
            ]
        )

        assert result == 0
        mock_bs.assert_called_once()

    def test_C11_입력_오류_RuntimeError_return_2(self, tmp_path: Path, monkeypatch):
        """build_subset 이 RuntimeError → return 2."""
        _require_module()
        csv_path = _make_standard_ranking_csv(tmp_path)
        output_yaml = tmp_path / "subset.yaml"

        self._patch_build_subset(monkeypatch, side_effect=RuntimeError("top_n <= 0"))

        result = main(
            [
                f"--ranking-csv={csv_path}",
                "--top-n=0",
                f"--output-yaml={output_yaml}",
                "--source=test-source",
                f"--as-of={self._AS_OF_STR}",
            ]
        )

        assert result == 2

    def test_C12_IO_오류_OSError_return_3(self, tmp_path: Path, monkeypatch):
        """build_subset 이 OSError → return 3."""
        _require_module()
        csv_path = _make_standard_ranking_csv(tmp_path)
        output_yaml = tmp_path / "subset.yaml"

        self._patch_build_subset(monkeypatch, side_effect=OSError("Permission denied"))

        result = main(
            [
                f"--ranking-csv={csv_path}",
                "--top-n=3",
                f"--output-yaml={output_yaml}",
                "--source=test-source",
                f"--as-of={self._AS_OF_STR}",
            ]
        )

        assert result == 3
