"""C4 — RSI 평균회귀 sensitivity grid 실행 CLI (ADR-0023).

ADR-0023 의 Phase 3 진입 게이팅 4 검증 중 C4 (PR5 sensitivity grid). 5축 96
조합 (`step_f_rsi_mr_grid`) 을 일봉 캐시 + ``compute_rsi_mr_baseline`` 로 평가하고
ADR-0022 게이트 3종 (MDD>-25% · DCA 알파 · Sharpe>0.3) 을 자동 판정.

사용 예시 (직렬, 디버그용):

```
uv run python scripts/c4_rsi_mr_sensitivity.py \
  --from 2025-04-01 --to 2026-04-21 \
  --universe-yaml config/universe.yaml \
  --starting-capital 2000000 \
  --output-markdown data/c4_rsi_mr_grid.md \
  --output-csv data/c4_rsi_mr_grid.csv
```

병렬 + incremental flush (8 워커, 권장):

```
uv run python scripts/c4_rsi_mr_sensitivity.py \
  --from 2025-04-01 --to 2026-04-21 \
  --workers 8 \
  --output-markdown data/c4_rsi_mr_grid.md \
  --output-csv data/c4_rsi_mr_grid.csv \
  --resume data/c4_rsi_mr_grid.csv
```

DCA baseline (게이트 2 비교) 은 same-window 1회만 측정해 모든 조합에 공유.

exit code 규약 (다른 스크립트와 동일):
- 0 정상
- 2 입력·설정 오류 (`RuntimeError`, `UniverseLoadError`)
- 3 I/O 오류 (`OSError`)
"""

from __future__ import annotations

import argparse
import functools
import sys
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path

from loguru import logger

from stock_agent.backtest.dca import DCABaselineConfig, compute_dca_baseline
from stock_agent.backtest.loader import BarLoader
from stock_agent.backtest.rsi_mr import RSIMRBaselineConfig
from stock_agent.backtest.rsi_mr_sensitivity import (
    RSIMRSensitivityRow,
    append_sensitivity_row,
    filter_remaining_combos,
    load_sensitivity_rows,
    merge_sensitivity_rows,
    render_markdown_table,
    run_rsi_mr_sensitivity_combos,
    run_rsi_mr_sensitivity_combos_parallel,
    step_f_rsi_mr_grid,
    write_csv,
)
from stock_agent.data import (
    HistoricalDataStore,
    UniverseLoadError,
    load_kospi200_universe,
)
from stock_agent.data.daily_bar_loader import DailyBarLoader

_EXIT_INPUT_ERROR = 2
_EXIT_IO_ERROR = 3


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ADR-0023 C4 — RSI 평균회귀 sensitivity grid (96 조합).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--from",
        dest="start",
        type=date.fromisoformat,
        required=True,
        help="평가 구간 시작 (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--to",
        dest="end",
        type=date.fromisoformat,
        required=True,
        help="평가 구간 종료 (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--universe-yaml",
        type=Path,
        default=Path("config/universe.yaml"),
        help="universe YAML 경로.",
    )
    parser.add_argument(
        "--starting-capital",
        type=int,
        default=2_000_000,
        help="시작 자본 (KRW).",
    )
    parser.add_argument(
        "--position-pct",
        type=Decimal,
        default=Decimal("1.0"),
        help="자본 투입 비율 (0 < pct ≤ 1, 기본 1.0).",
    )
    parser.add_argument(
        "--dca-symbol",
        type=str,
        default="069500",
        help="게이트 2 비교용 DCA baseline 종목 (기본 069500).",
    )
    parser.add_argument(
        "--dca-monthly-investment",
        type=int,
        default=200_000,
        help="DCA baseline 월 투자금 (KRW).",
    )
    parser.add_argument(
        "--dca-starting-capital",
        type=int,
        default=10_000_000,
        help="DCA baseline 시작 자본 (KRW).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            "병렬 워커 수 (ProcessPool, ADR-0020). 미지정 시 직렬 1 워커. "
            "1 이면 직렬 경로 (회귀 안전망). 0·음수 거부."
        ),
    )
    parser.add_argument(
        "--resume",
        type=Path,
        default=None,
        help=(
            "이전 실행 CSV 경로. 존재하면 완료 조합 skip + 미완료만 실행 후 "
            "병합·재렌더. 부재 시 신규 작성. freeze·재부팅 내성."
        ),
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=Path("data/c4_rsi_mr_grid.md"),
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("data/c4_rsi_mr_grid.csv"),
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="HistoricalDataStore SQLite 경로 (기본 stock-agent 설정값).",
    )
    parser.add_argument(
        "--sort-by",
        type=str,
        default="all_gates_pass_then_return",
        help=(
            "Markdown 표 정렬 기준. 'all_gates_pass_then_return' (기본) 은 "
            "all_gates_pass DESC + total_return_pct DESC 복합 정렬. 그 외는 "
            "render_markdown_table 의 sort_by 키 (예: total_return_pct, "
            "max_drawdown_pct, sharpe_ratio, dca_alpha_pct)."
        ),
    )
    return parser.parse_args(argv)


def _resolve_universe(universe_yaml: Path | None) -> tuple[str, ...]:
    if universe_yaml is None:
        universe = load_kospi200_universe()
    else:
        universe = load_kospi200_universe(universe_yaml)
    if not universe.tickers:
        raise RuntimeError(
            f"universe YAML 이 비어있습니다 (path={universe_yaml or 'config/universe.yaml'})."
        )
    return universe.tickers


def _build_loader(db_path: Path | None) -> BarLoader:
    """DailyBarLoader 생성. ProcessPool 워커 안에서도 호출 가능 (loader_factory)."""
    store = HistoricalDataStore() if db_path is None else HistoricalDataStore(db_path=db_path)
    return DailyBarLoader(store)


def _resolve_workers(raw: int | None) -> int:
    if raw is None:
        return 1
    if raw < 1:
        raise RuntimeError(f"--workers 는 1 이상이어야 합니다 (got={raw})")
    return raw


def _measure_dca_baseline(
    loader: BarLoader,
    *,
    target_symbol: str,
    monthly_investment: int,
    dca_starting_capital: int,
    start: date,
    end: date,
) -> Decimal:
    """게이트 2 비교용 same-window DCA baseline 측정 — 96 조합 공유."""
    cfg = DCABaselineConfig(
        starting_capital_krw=dca_starting_capital,
        monthly_investment_krw=monthly_investment,
        target_symbol=target_symbol,
        purchase_day=1,
    )
    result = compute_dca_baseline(loader, cfg, start, end)
    logger.info(
        "c4.dca_baseline target={t} window=[{s}~{e}] return={r:+.4%}",
        t=target_symbol,
        s=start.isoformat(),
        e=end.isoformat(),
        r=float(result.metrics.total_return_pct),
    )
    return result.metrics.total_return_pct


def _build_base_config(
    args: argparse.Namespace,
    universe: tuple[str, ...],
) -> RSIMRBaselineConfig:
    """그리드 변동 외 필드 (자본·universe·position_pct·비용) 의 기본값."""
    return RSIMRBaselineConfig(
        starting_capital_krw=args.starting_capital,
        universe=universe,
        position_pct=args.position_pct,
    )


def _sort_rows_for_render(
    rows: tuple[RSIMRSensitivityRow, ...],
    sort_by: str,
) -> tuple[RSIMRSensitivityRow, ...]:
    """all_gates_pass_then_return 기본 정렬 처리. 그 외는 호출자가 render 의 sort_by 사용."""
    if sort_by != "all_gates_pass_then_return":
        return rows
    return tuple(
        sorted(
            rows,
            key=lambda r: (r.all_gates_pass, r.metrics.total_return_pct),
            reverse=True,
        )
    )


def _summary_lines(
    rows: tuple[RSIMRSensitivityRow, ...],
    *,
    grid_size: int,
    args: argparse.Namespace,
    universe_size: int,
    dca_baseline_return_pct: Decimal,
) -> list[str]:
    """Phase 3 진입 게이트 자동 판정 — 전체 PASS 비율 + 현행 인접 PASS 비율."""
    pass_count = sum(1 for r in rows if r.all_gates_pass)
    pass_ratio = (pass_count / grid_size) if grid_size else 0.0

    current_target = {
        "rsi_period": 14,
        "oversold_threshold": Decimal("30"),
        "overbought_threshold": Decimal("70"),
        "stop_loss_pct": Decimal("0.03"),
        "max_positions": 10,
    }
    current_row = _find_row(rows, current_target)
    current_pass = "PASS" if (current_row and current_row.all_gates_pass) else "FAIL"

    neighbors = _adjacent_combos(current_target)
    neighbor_rows = [_find_row(rows, n) for n in neighbors]
    found_neighbors = [r for r in neighbor_rows if r is not None]
    neighbor_pass_count = sum(1 for r in found_neighbors if r.all_gates_pass)
    neighbor_pass_ratio = (neighbor_pass_count / len(found_neighbors)) if found_neighbors else 0.0

    overall_gate_pass = pass_ratio >= 0.5
    neighbor_gate_pass = neighbor_pass_ratio >= 0.7
    phase3_verdict = "PASS" if (overall_gate_pass and neighbor_gate_pass) else "FAIL"

    lines: list[str] = [
        "## C4 집계",
        "",
        f"- 평가 구간: {args.start.isoformat()} ~ {args.end.isoformat()}",
        f"- universe 크기: {universe_size}",
        f"- DCA baseline 총수익률 ({args.dca_symbol}): "
        f"{float(dca_baseline_return_pct) * 100:+.4f}%",
        f"- 그리드 크기: {grid_size}",
        f"- 게이트 3종 동시 PASS 조합 수: {pass_count} / {grid_size} ({pass_ratio * 100:.2f}%)",
        f"- 현행 파라미터 (14/30/70/0.03/10) 게이트: {current_pass}",
        f"- 현행 인접 조합 PASS 비율: {neighbor_pass_count} / {len(found_neighbors)} "
        f"({neighbor_pass_ratio * 100:.2f}%)",
        "",
        "### Phase 3 진입 게이트",
        "",
        f"- 전체 PASS 비율 ≥ 50%: {'PASS' if overall_gate_pass else 'FAIL'} "
        f"({pass_ratio * 100:.2f}%)",
        f"- 현행 인접 PASS 비율 ≥ 70%: {'PASS' if neighbor_gate_pass else 'FAIL'} "
        f"({neighbor_pass_ratio * 100:.2f}%)",
        f"- **종합 Phase 3 진입 판정: {phase3_verdict}**",
        "",
    ]
    return lines


def _find_row(
    rows: tuple[RSIMRSensitivityRow, ...],
    target: dict[str, object],
) -> RSIMRSensitivityRow | None:
    for row in rows:
        params = dict(row.params)
        if all(params.get(k) == v for k, v in target.items()):
            return row
    return None


def _adjacent_combos(current: dict[str, object]) -> list[dict[str, object]]:
    """현행 파라미터 + 1축만 변동한 인접 조합 후보 목록.

    그리드의 5축 후보값 중 현행 외 값으로 1축씩 교체. 그리드 전체 (96) 안에
    실제로 존재하는 조합만 후속 ``_find_row`` 가 매칭.
    """
    grid = step_f_rsi_mr_grid()
    axis_values: dict[str, tuple[object, ...]] = {ax.name: ax.values for ax in grid.axes}
    out: list[dict[str, object]] = []
    for axis_name, candidates in axis_values.items():
        for v in candidates:
            if v == current[axis_name]:
                continue
            adj = dict(current)
            adj[axis_name] = v
            out.append(adj)
    return out


def _run_pipeline(args: argparse.Namespace) -> None:
    universe = _resolve_universe(args.universe_yaml)
    workers = _resolve_workers(args.workers)
    grid = step_f_rsi_mr_grid()
    base_config = _build_base_config(args, universe)

    loader = _build_loader(args.db_path)
    try:
        dca_baseline_return_pct = _measure_dca_baseline(
            loader,
            target_symbol=args.dca_symbol,
            monthly_investment=args.dca_monthly_investment,
            dca_starting_capital=args.dca_starting_capital,
            start=args.start,
            end=args.end,
        )

        existing_rows: tuple[RSIMRSensitivityRow, ...] = ()
        remaining_combos = list(grid.iter_combinations())
        if args.resume is not None and args.resume.exists():
            existing_rows = load_sensitivity_rows(args.resume, grid)
            completed = {row.params for row in existing_rows}
            remaining_combos = filter_remaining_combos(grid, completed)
            logger.info(
                "c4.resume loaded={n} remaining={r}",
                n=len(existing_rows),
                r=len(remaining_combos),
            )

        logger.info(
            "c4.start universe={u} workers={w} grid={g} remaining={r}",
            u=len(universe),
            w=workers,
            g=grid.size,
            r=len(remaining_combos),
        )

        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        on_row_callback: Callable[[RSIMRSensitivityRow], None] | None = None
        if args.resume is not None:

            def _flush(row: RSIMRSensitivityRow) -> None:
                append_sensitivity_row(row, args.output_csv, grid)

            on_row_callback = _flush

        new_rows: tuple[RSIMRSensitivityRow, ...] = ()
        if remaining_combos:
            if workers == 1:
                new_rows = run_rsi_mr_sensitivity_combos(
                    loader=loader,
                    base_config=base_config,
                    combos=remaining_combos,
                    start=args.start,
                    end=args.end,
                    dca_baseline_return_pct=dca_baseline_return_pct,
                    on_row=on_row_callback,
                )
            else:
                loader_factory = functools.partial(_build_loader, args.db_path)
                new_rows = run_rsi_mr_sensitivity_combos_parallel(
                    loader_factory=loader_factory,
                    base_config=base_config,
                    combos=remaining_combos,
                    start=args.start,
                    end=args.end,
                    dca_baseline_return_pct=dca_baseline_return_pct,
                    max_workers=workers,
                    on_row=on_row_callback,
                )
        else:
            logger.info("c4.skip_engine — 모든 조합 완료, 재렌더만 수행")

        merged = merge_sensitivity_rows(existing_rows, new_rows, grid)

        sorted_rows = _sort_rows_for_render(merged, args.sort_by)
        render_sort_by = (
            "total_return_pct" if args.sort_by == "all_gates_pass_then_return" else args.sort_by
        )
        markdown_table = render_markdown_table(
            sorted_rows,
            sort_by=render_sort_by,
            descending=True,
        )

        summary = _summary_lines(
            merged,
            grid_size=grid.size,
            args=args,
            universe_size=len(universe),
            dca_baseline_return_pct=dca_baseline_return_pct,
        )
        markdown = _render_md_header(args, len(universe), grid.size, dca_baseline_return_pct)
        markdown.extend(summary)
        markdown.append("## 조합별 결과")
        markdown.append("")
        markdown.append(markdown_table)

        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.write_text("\n".join(markdown), encoding="utf-8")
        write_csv(merged, args.output_csv)

        logger.info(
            "c4.done rows={n} markdown={m} csv={c}",
            n=len(merged),
            m=args.output_markdown,
            c=args.output_csv,
        )
    finally:
        close = getattr(loader, "close", None)
        if callable(close):
            close()


def _render_md_header(
    args: argparse.Namespace,
    universe_size: int,
    grid_size: int,
    dca_baseline_return_pct: Decimal,
) -> list[str]:
    return [
        "# ADR-0023 C4 — RSI 평균회귀 sensitivity grid",
        "",
        "## 실행 파라미터",
        "",
        f"- 평가 구간: {args.start.isoformat()} ~ {args.end.isoformat()}",
        f"- universe 크기: {universe_size}",
        f"- 시작 자본: {args.starting_capital:,} KRW",
        f"- position_pct: {args.position_pct}",
        f"- DCA baseline 종목: {args.dca_symbol} (월 {args.dca_monthly_investment:,} KRW)",
        f"- DCA baseline 총수익률: {float(dca_baseline_return_pct) * 100:+.4f}%",
        f"- 그리드 크기: {grid_size}",
        "",
        "## 게이트 정의 (ADR-0022)",
        "",
        "- 게이트 1: max_drawdown_pct > -25%",
        "- 게이트 2: dca_alpha_pct > 0 (RSI MR 총수익률 - DCA baseline 총수익률)",
        "- 게이트 3: 연환산 Sharpe > 0.3",
        "- 종합 PASS: 세 게이트 동시 통과",
        "",
        "## Phase 3 진입 게이트 (ADR-0023 C4)",
        "",
        "- 전체 PASS 비율 ≥ 50%",
        "- 현행 인접 (1축 변동) 조합 PASS 비율 ≥ 70%",
        "- 두 조건 동시 충족 시 Phase 3 진입 판정 PASS",
        "",
    ]


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.start > args.end:
        logger.error(f"--from({args.start}) 는 --to({args.end}) 이전이어야 합니다.")
        return _EXIT_INPUT_ERROR

    try:
        _run_pipeline(args)
    except UniverseLoadError as exc:
        logger.error(f"universe load error: {exc}")
        return _EXIT_INPUT_ERROR
    except RuntimeError as exc:
        logger.error(f"input/runtime error: {exc}")
        return _EXIT_INPUT_ERROR
    except OSError as exc:
        logger.exception(f"I/O error: {exc}")
        return _EXIT_IO_ERROR
    return 0


if __name__ == "__main__":
    sys.exit(main())
