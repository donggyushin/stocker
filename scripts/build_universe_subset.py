"""유동성 랭킹 기반 KOSPI 200 서브셋 yaml 생성 CLI — Step C (Issue #76, ADR-0019).

`scripts/build_liquidity_ranking.py` 가 산출한 `data/liquidity_ranking.csv` 를
입력으로 받아 `rank_value <= top_n` 인 종목만 추출해 KOSPI 200 서브셋 yaml 을
만든다. 출력 yaml schema 는 `config/universe.yaml` 정본과 동일 — 작성 직후
`load_kospi200_universe` 로 자체 검증한다.

ADR-0004 (KOSPI 200 수동 관리) 정책 유지: 본 스크립트는 yaml 을 자동 작성하지만,
운영자가 결과를 검토 후 git add 하는 단계에서 책임을 진다 (자동 git 커밋 아님).

사용 예시:

```
uv run python scripts/build_universe_subset.py \
  --ranking-csv data/liquidity_ranking.csv \
  --top-n 50 \
  --output-yaml config/universe_top50.yaml \
  --source "Step C — Top 50 by avg_value_krw, window=2024-04-22..2025-04-21" \
  --as-of 2025-04-21
```

exit code (build_liquidity_ranking 와 동일)
- 0: 정상.
- 2: 입력·설정 오류 (`top_n<=0`, CSV 헤더·rank 검증 실패 등) — 재시도 무의미.
- 3: I/O 오류 — 재시도 가치 있음.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from stock_agent.data import load_kospi200_universe

_EXIT_OK = 0
_EXIT_INPUT_ERROR = 2
_EXIT_IO_ERROR = 3

_REQUIRED_COLUMNS = ("symbol", "rank_value")


def _read_ranking_rows(ranking_csv: Path) -> list[dict[str, str]]:
    """ranking CSV 를 읽어 dict list 반환. 헤더 누락 시 RuntimeError."""
    with ranking_csv.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise RuntimeError(f"ranking CSV 가 헤더를 갖지 않습니다 (path={ranking_csv})")
        missing = [c for c in _REQUIRED_COLUMNS if c not in reader.fieldnames]
        if missing:
            raise RuntimeError(
                f"ranking CSV 에 필수 컬럼이 없습니다: {missing} "
                f"(path={ranking_csv}, fields={list(reader.fieldnames)})"
            )
        return list(reader)


def _validate_ranks(rows: list[dict[str, str]], path: Path) -> dict[int, str]:
    """rank_value 가 정확히 1..N contiguous, 음수·중복·결손 모두 거부.

    반환: {rank: symbol} 매핑.
    """
    if not rows:
        raise RuntimeError(f"ranking CSV row 가 비어있습니다 (path={path})")

    rank_to_sym: dict[int, str] = {}
    for row in rows:
        sym = row.get("symbol", "")
        rank_str = row.get("rank_value", "")
        try:
            rank = int(rank_str)
        except (ValueError, TypeError) as e:
            raise RuntimeError(
                f"rank_value 파싱 실패: {rank_str!r} (symbol={sym}, path={path})"
            ) from e
        if rank in rank_to_sym:
            raise RuntimeError(
                f"rank_value 중복: {rank} (symbols={rank_to_sym[rank]!r}, {sym!r}, path={path})"
            )
        rank_to_sym[rank] = sym

    expected = set(range(1, len(rows) + 1))
    actual = set(rank_to_sym.keys())
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RuntimeError(
            f"rank_value 가 1..{len(rows)} contiguous 아님 — "
            f"missing={missing}, extra={extra} (path={path})"
        )
    return rank_to_sym


def build_subset(
    *,
    ranking_csv: Path,
    top_n: int,
    output_yaml: Path,
    source: str,
    as_of: date,
) -> None:
    """ranking CSV 에서 rank_value 1..top_n 인 종목으로 서브셋 yaml 작성."""
    if top_n <= 0:
        raise RuntimeError(f"top_n 은 1 이상이어야 합니다: {top_n}")

    rows = _read_ranking_rows(ranking_csv)
    rank_to_sym = _validate_ranks(rows, ranking_csv)

    if top_n > len(rows):
        raise RuntimeError(
            f"top_n={top_n} 이 CSV row 수({len(rows)}) 를 초과합니다 (path={ranking_csv})"
        )

    selected = sorted(rank_to_sym[r] for r in range(1, top_n + 1))

    payload: dict[str, Any] = {
        "as_of_date": as_of.isoformat(),
        "source": source,
        "tickers": selected,
    }

    output_yaml.parent.mkdir(parents=True, exist_ok=True)
    with output_yaml.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, sort_keys=False)

    universe = load_kospi200_universe(output_yaml)
    logger.info(
        "universe_subset.done top_n={n} written={p} verified_tickers={t}",
        n=top_n,
        p=str(output_yaml),
        t=len(universe.tickers),
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="유동성 랭킹 기반 KOSPI 200 서브셋 yaml 생성 (Step C, ADR-0019)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--ranking-csv",
        type=Path,
        required=True,
        help="유동성 랭킹 CSV 경로 (build_liquidity_ranking.py 산출물).",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        required=True,
        help="추출할 상위 N (rank_value 1..N).",
    )
    parser.add_argument(
        "--output-yaml",
        type=Path,
        required=True,
        help="서브셋 universe yaml 출력 경로.",
    )
    parser.add_argument(
        "--source",
        type=str,
        required=True,
        help="universe.yaml source 필드 — 갱신 근거 추적용 자유 문자열.",
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        required=True,
        help="universe.yaml as_of_date (YYYY-MM-DD).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        build_subset(
            ranking_csv=args.ranking_csv,
            top_n=args.top_n,
            output_yaml=args.output_yaml,
            source=args.source,
            as_of=args.as_of,
        )
    except RuntimeError as e:
        logger.error(f"입력·설정 오류: {e}")
        return _EXIT_INPUT_ERROR
    except FileNotFoundError as e:
        logger.error(f"입력 파일 없음: {e}")
        return _EXIT_INPUT_ERROR
    except OSError as e:
        logger.error(f"I/O 오류: {e}")
        return _EXIT_IO_ERROR

    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
