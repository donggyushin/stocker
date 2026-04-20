"""sensitivity — ORB 파라미터 민감도 그리드 실행 CLI.

사용 예시:

```
uv run python scripts/sensitivity.py \
  --csv-dir data/minute_csv \
  --from 2023-01-01 --to 2025-12-31 \
  --symbols 005930,000660,035420 \
  --starting-capital 1000000 \
  --output-markdown data/sensitivity_report.md \
  --output-csv data/sensitivity_report.csv \
  --sort-by total_return_pct
```

동작
- `--csv-dir` 하위의 `{symbol}.csv` 를 `MinuteCsvBarLoader` 로 읽어 분봉 스트림
  공급.
- `--symbols` 미지정 시 `config/universe.yaml` 의 KOSPI 200 전체 사용.
- 기본 그리드 (`default_grid()`) — OR 구간 2종 × 손절 4종 × 익절 4종 = 32 조합.
  축을 코드에서 수정하고 싶으면 `default_grid()` 소스를 직접 편집 (YAML 외부화는
  YAGNI — plan.md 기조).
- 각 조합마다 `BacktestEngine` 을 새로 생성·실행. 결정론.

제약
- 외부 네트워크·KIS·pykis 접촉 없음 — 순수 CSV + 엔진.
- plan.md PASS 기준 (2~3년 실데이터 MDD < -15%) 판정은 이 스크립트 범위 밖 —
  운영자가 출력 테이블을 육안 검토해 운영 파라미터 교체 결정을 내린다.
- 민감도 리포트는 sanity check 이지 과적합 허가가 아니다 — 최종 파라미터
  교체는 Walk-forward 검증 (Phase 5) 후에만.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from loguru import logger

from stock_agent.backtest import (
    BacktestConfig,
    default_grid,
    render_markdown_table,
    run_sensitivity,
    write_csv,
)
from stock_agent.data import MinuteCsvBarLoader, load_kospi200_universe

_SORTABLE_KEYS = (
    "total_return_pct",
    "max_drawdown_pct",
    "sharpe_ratio",
    "win_rate",
    "avg_pnl_ratio",
    "trades_per_day",
    "net_pnl_krw",
    "trade_count",
    "rejected_total",
    "post_slippage_rejections",
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ORB 파라미터 민감도 그리드 실행",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--csv-dir",
        type=Path,
        required=True,
        help="분봉 CSV 디렉토리 ({symbol}.csv 레이아웃).",
    )
    parser.add_argument(
        "--from",
        dest="start",
        type=date.fromisoformat,
        required=True,
        help="구간 시작 (YYYY-MM-DD, 경계 포함).",
    )
    parser.add_argument(
        "--to",
        dest="end",
        type=date.fromisoformat,
        required=True,
        help="구간 종료 (YYYY-MM-DD, 경계 포함).",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="쉼표 구분 종목 코드 (미지정 시 config/universe.yaml 전체 사용).",
    )
    parser.add_argument(
        "--starting-capital",
        type=int,
        default=1_000_000,
        help="시작 자본 (KRW).",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=Path("data/sensitivity_report.md"),
        help="Markdown 리포트 출력 경로.",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("data/sensitivity_report.csv"),
        help="CSV 리포트 출력 경로.",
    )
    parser.add_argument(
        "--sort-by",
        type=str,
        default="total_return_pct",
        choices=_SORTABLE_KEYS,
        help="Markdown 표 정렬 기준 메트릭.",
    )
    parser.add_argument(
        "--ascending",
        action="store_true",
        help="Markdown 표를 오름차순으로 정렬 (기본 내림차순).",
    )
    return parser.parse_args(argv)


def _resolve_symbols(raw: str) -> tuple[str, ...]:
    """`--symbols` 인자 해석 — 빈 값이면 유니버스 YAML 전체."""
    if raw.strip():
        parts = tuple(s.strip() for s in raw.split(",") if s.strip())
        if not parts:
            raise RuntimeError("--symbols 가 쉼표만 포함되어 있습니다.")
        return parts
    universe = load_kospi200_universe()
    if not universe.tickers:
        raise RuntimeError(
            "config/universe.yaml 이 비어있습니다 — --symbols 로 명시하거나 "
            "유니버스 YAML 을 갱신하세요."
        )
    return universe.tickers


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.start > args.end:
        logger.error(f"--from({args.start}) 는 --to({args.end}) 이전이어야 합니다.")
        return 1

    try:
        symbols = _resolve_symbols(args.symbols)
    except Exception as e:
        logger.exception(f"symbols 해석 실패: {e}")
        return 1

    try:
        loader = MinuteCsvBarLoader(args.csv_dir)
    except Exception as e:
        logger.exception(f"MinuteCsvBarLoader 초기화 실패 (csv_dir={args.csv_dir}): {e}")
        return 1

    base_config = BacktestConfig(starting_capital_krw=args.starting_capital)
    grid = default_grid()
    logger.info(
        "sensitivity.start from={s} to={e} symbols={n} combos={c}",
        s=args.start,
        e=args.end,
        n=len(symbols),
        c=grid.size,
    )

    try:
        rows = run_sensitivity(
            loader=loader,
            start=args.start,
            end=args.end,
            symbols=symbols,
            base_config=base_config,
            grid=grid,
        )
    except Exception as e:
        logger.exception(f"run_sensitivity 실패: {e}")
        return 1

    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)

    try:
        markdown = render_markdown_table(
            rows,
            sort_by=args.sort_by,
            descending=not args.ascending,
        )
        args.output_markdown.write_text(markdown, encoding="utf-8")
        write_csv(rows, args.output_csv)
    except Exception as e:
        logger.exception(f"리포트 출력 실패: {e}")
        return 1

    logger.info(
        "sensitivity.done rows={n} markdown={m} csv={c}",
        n=len(rows),
        m=args.output_markdown,
        c=args.output_csv,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
