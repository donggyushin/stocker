"""walk-forward — RSI 평균회귀 walk-forward 검증 CLI (ADR-0023 C2).

사용 예시:

```
uv run python scripts/walk_forward_rsi_mr.py \
  --from 2024-04-01 --to 2026-04-21 \
  --train-months 12 --test-months 6 --step-months 6 \
  --pass-threshold 0.3 \
  --universe-yaml config/universe.yaml \
  --starting-capital 2000000 \
  --output-markdown data/c2_walk_forward_rsi_mr.md \
  --output-csv data/c2_walk_forward_rsi_mr.csv
```

동작
- ``HistoricalDataStore`` (기본 ``data/stock_agent.db``) 일봉 캐시 + ``DailyBarLoader``
  로 한 번 로드. 미백필 종목은 ``fetch_daily_ohlcv`` 가 pykrx 로 폴백 — 결정론
  보장을 위해 ``scripts/backfill_daily_bars.py`` 선행 권장.
- ``generate_windows`` 로 rolling window 생성 후 각 window 마다:
    1. ``compute_rsi_mr_baseline`` (train 구간) → train_total_return_pct.
    2. ``compute_rsi_mr_baseline`` (test 구간) → test BacktestMetrics.
    3. ``compute_dca_baseline`` (test 구간, 069500) → DCA baseline 알파 비교.
- aggregate degradation_pct = (train_avg - test_avg) / train_avg 와
  ``--pass-threshold`` 비교 → ``is_pass`` 판정.
- 산출물: Markdown 리포트 + window별 metrics CSV.

PASS/FAIL 라벨
- 리포트는 (a) aggregate degradation PASS/FAIL + (b) 각 test window 의
  ADR-0022 게이트 3종 (MDD>-25% · DCA 알파 · Sharpe>0.3) 판정을 함께 표기.
- exit code 에는 반영 안 함 — 운영자 수동 검토 보존.

exit code 규약 (``scripts/backtest.py`` 와 동일):
- 0 정상
- 2 입력·설정 오류 (`RuntimeError`, `UniverseLoadError` 등)
- 3 I/O 오류 (`OSError`)
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from loguru import logger

from stock_agent.backtest.dca import DCABaselineConfig, compute_dca_baseline
from stock_agent.backtest.rsi_mr import (
    RSIMRBaselineConfig,
    compute_rsi_mr_baseline,
)
from stock_agent.backtest.walk_forward import (
    WalkForwardWindow,
    generate_windows,
)
from stock_agent.data import (
    HistoricalDataStore,
    UniverseLoadError,
    load_kospi200_universe,
)
from stock_agent.data.daily_bar_loader import DailyBarLoader

_EXIT_INPUT_ERROR = 2
_EXIT_IO_ERROR = 3

# ADR-0022 게이트 임계값 (RSI 평균회귀).
_GATE1_MDD_THRESHOLD = Decimal("-0.25")
_GATE3_SHARPE_THRESHOLD = Decimal("0.3")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RSI 평균회귀 walk-forward 검증 (ADR-0023 C2).",
    )
    parser.add_argument("--from", dest="start", type=date.fromisoformat, required=True)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, required=True)
    parser.add_argument("--train-months", type=int, default=12)
    parser.add_argument("--test-months", type=int, default=6)
    parser.add_argument("--step-months", type=int, default=6)
    parser.add_argument(
        "--pass-threshold",
        type=Decimal,
        default=Decimal("0.3"),
        help="degradation 허용 임계치 (소수, 기본 0.3 = 30%%).",
    )
    parser.add_argument(
        "--universe-yaml",
        type=Path,
        default=Path("config/universe.yaml"),
    )
    parser.add_argument("--starting-capital", type=int, default=2_000_000)
    parser.add_argument("--rsi-period", type=int, default=14)
    parser.add_argument("--oversold-threshold", type=Decimal, default=Decimal("30"))
    parser.add_argument("--overbought-threshold", type=Decimal, default=Decimal("70"))
    parser.add_argument("--stop-loss-pct", type=Decimal, default=Decimal("0.03"))
    parser.add_argument("--max-positions", type=int, default=10)
    parser.add_argument(
        "--dca-symbol",
        type=str,
        default="069500",
        help="게이트 2 비교용 DCA baseline 종목 코드 (기본 069500 KODEX 200).",
    )
    parser.add_argument(
        "--dca-monthly-investment",
        type=int,
        default=200_000,
        help="DCA baseline 월 투자금 (기본 200,000 KRW).",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=Path("data/c2_walk_forward_rsi_mr.md"),
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("data/c2_walk_forward_rsi_mr.csv"),
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=None,
        help="HistoricalDataStore SQLite 경로 (기본 stock-agent 설정값).",
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


def _build_loader(db_path: Path | None) -> DailyBarLoader:
    store = HistoricalDataStore() if db_path is None else HistoricalDataStore(db_path=db_path)
    return DailyBarLoader(store)


def _format_pct(d: Decimal) -> str:
    return f"{float(d) * 100:+.2f}%"


def _format_decimal(d: Decimal, places: int = 4) -> str:
    return f"{float(d):.{places}f}"


def _gate1_label(mdd: Decimal) -> str:
    return "PASS" if mdd > _GATE1_MDD_THRESHOLD else "FAIL"


def _gate3_label(sharpe: Decimal) -> str:
    return "PASS" if sharpe > _GATE3_SHARPE_THRESHOLD else "FAIL"


def _gate2_label(rsi_return: Decimal, dca_return: Decimal) -> tuple[str, Decimal]:
    alpha = rsi_return - dca_return
    return ("PASS" if alpha > 0 else "FAIL"), alpha


def _verdict_label(*labels: str) -> str:
    return "PASS" if all(label == "PASS" for label in labels) else "FAIL"


def _run_pipeline(args: argparse.Namespace) -> None:
    universe = _resolve_universe(args.universe_yaml)
    loader = _build_loader(args.db_path)

    rsi_config = RSIMRBaselineConfig(
        starting_capital_krw=args.starting_capital,
        universe=universe,
        rsi_period=args.rsi_period,
        oversold_threshold=args.oversold_threshold,
        overbought_threshold=args.overbought_threshold,
        stop_loss_pct=args.stop_loss_pct,
        max_positions=args.max_positions,
    )

    dca_strategy_config = _build_dca_config(
        target_symbol=args.dca_symbol,
        monthly_investment=args.dca_monthly_investment,
    )

    windows = generate_windows(
        args.start,
        args.end,
        train_months=args.train_months,
        test_months=args.test_months,
        step_months=args.step_months,
    )

    logger.info(
        "walk_forward.start universe={u} windows={w} train={tm} test={te} step={st}",
        u=len(universe),
        w=len(windows),
        tm=args.train_months,
        te=args.test_months,
        st=args.step_months,
    )

    rows: list[dict[str, str]] = []
    train_returns: list[Decimal] = []
    test_returns: list[Decimal] = []
    md_lines: list[str] = _render_md_header(args, len(universe), windows)

    try:
        for idx, window in enumerate(windows):
            logger.info(
                "window[{i}] train=[{tf}~{tt}] test=[{sf}~{st}]",
                i=idx,
                tf=window.train_from,
                tt=window.train_to,
                sf=window.test_from,
                st=window.test_to,
            )
            rsi_train = compute_rsi_mr_baseline(
                loader, rsi_config, window.train_from, window.train_to
            )
            rsi_test = compute_rsi_mr_baseline(loader, rsi_config, window.test_from, window.test_to)
            dca_test = compute_dca_baseline(
                loader,
                dca_strategy_config,
                window.test_from,
                window.test_to,
            )
            train_returns.append(rsi_train.metrics.total_return_pct)
            test_returns.append(rsi_test.metrics.total_return_pct)

            mdd = rsi_test.metrics.max_drawdown_pct
            sharpe = rsi_test.metrics.sharpe_ratio
            gate1 = _gate1_label(mdd)
            gate3 = _gate3_label(sharpe)
            gate2, alpha = _gate2_label(
                rsi_test.metrics.total_return_pct,
                dca_test.metrics.total_return_pct,
            )
            verdict = _verdict_label(gate1, gate2, gate3)

            rows.append(
                {
                    "window_idx": str(idx),
                    "train_from": window.train_from.isoformat(),
                    "train_to": window.train_to.isoformat(),
                    "test_from": window.test_from.isoformat(),
                    "test_to": window.test_to.isoformat(),
                    "train_total_return_pct": _format_decimal(
                        rsi_train.metrics.total_return_pct, 6
                    ),
                    "test_total_return_pct": _format_decimal(rsi_test.metrics.total_return_pct, 6),
                    "test_max_drawdown_pct": _format_decimal(mdd, 6),
                    "test_sharpe_ratio": _format_decimal(sharpe, 4),
                    "test_win_rate": _format_decimal(rsi_test.metrics.win_rate, 4),
                    "test_avg_pnl_ratio": _format_decimal(rsi_test.metrics.avg_pnl_ratio, 4),
                    "test_trades": str(len(rsi_test.trades)),
                    "test_net_pnl_krw": str(rsi_test.metrics.net_pnl_krw),
                    "dca_test_return_pct": _format_decimal(dca_test.metrics.total_return_pct, 6),
                    "alpha_pct": _format_decimal(alpha, 6),
                    "gate1_mdd": gate1,
                    "gate2_alpha": gate2,
                    "gate3_sharpe": gate3,
                    "window_verdict": verdict,
                }
            )

            md_lines.extend(
                _render_window_md_block(
                    idx=idx,
                    window=window,
                    rsi_train=rsi_train.metrics,
                    rsi_test=rsi_test.metrics,
                    rsi_test_trades=len(rsi_test.trades),
                    dca_return=dca_test.metrics.total_return_pct,
                    alpha=alpha,
                    gate1=gate1,
                    gate2=gate2,
                    gate3=gate3,
                    verdict=verdict,
                )
            )

        n = Decimal(len(windows))
        train_avg = sum(train_returns, Decimal("0")) / n
        test_avg = sum(test_returns, Decimal("0")) / n
        degradation = (train_avg - test_avg) / train_avg if train_avg > 0 else Decimal("0")
        is_pass = degradation <= args.pass_threshold

        md_lines.extend(
            _render_aggregate_md_block(
                train_avg=train_avg,
                test_avg=test_avg,
                degradation=degradation,
                pass_threshold=args.pass_threshold,
                is_pass=is_pass,
                rows=rows,
            )
        )

        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
        _write_csv(args.output_csv, rows)
    finally:
        close = getattr(loader, "close", None)
        if callable(close):
            close()

    logger.info(
        "walk_forward.done train_avg={ta} test_avg={te} degradation={d} verdict={v}",
        ta=_format_pct(train_avg),
        te=_format_pct(test_avg),
        d=_format_pct(degradation),
        v="PASS" if is_pass else "FAIL",
    )


def _build_dca_config(*, target_symbol: str, monthly_investment: int) -> DCABaselineConfig:
    return DCABaselineConfig(
        starting_capital_krw=10_000_000,
        monthly_investment_krw=monthly_investment,
        target_symbol=target_symbol,
        purchase_day=1,
    )


def _render_md_header(
    args: argparse.Namespace,
    universe_size: int,
    windows: tuple[WalkForwardWindow, ...],
) -> list[str]:
    return [
        "# RSI 평균회귀 walk-forward 검증 (ADR-0023 C2)",
        "",
        "## 실행 파라미터",
        "",
        f"- 평가 구간: {args.start.isoformat()} ~ {args.end.isoformat()}",
        f"- train_months: {args.train_months}",
        f"- test_months: {args.test_months}",
        f"- step_months: {args.step_months}",
        f"- 생성 windows: {len(windows)}",
        f"- universe 크기: {universe_size}",
        f"- 시작 자본: {args.starting_capital:,} KRW",
        f"- RSI period: {args.rsi_period} / oversold: {args.oversold_threshold} / "
        f"overbought: {args.overbought_threshold}",
        f"- stop_loss_pct: {args.stop_loss_pct} / max_positions: {args.max_positions}",
        f"- pass_threshold (degradation): {args.pass_threshold}",
        "",
        "## 게이트 정의",
        "",
        "- 게이트 1: test 구간 MDD > -25%",
        "- 게이트 2: test 구간 RSI MR 총수익률 > DCA baseline 총수익률 (양의 알파)",
        "- 게이트 3: test 구간 연환산 Sharpe > 0.3",
        "- 종합 윈도우 PASS: 세 게이트 동시 통과",
        "- 집계 PASS: degradation_pct ≤ pass_threshold",
        "",
        "## 윈도우별 결과",
        "",
    ]


def _render_window_md_block(
    *,
    idx: int,
    window: WalkForwardWindow,
    rsi_train,
    rsi_test,
    rsi_test_trades: int,
    dca_return: Decimal,
    alpha: Decimal,
    gate1: str,
    gate2: str,
    gate3: str,
    verdict: str,
) -> list[str]:
    return [
        f"### 윈도우 {idx} — 종합 {verdict}",
        "",
        f"- train: {window.train_from} ~ {window.train_to} (RSI MR 총수익률 "
        f"{_format_pct(rsi_train.total_return_pct)})",
        f"- test: {window.test_from} ~ {window.test_to}",
        f"- test 거래수: {rsi_test_trades}",
        f"- test 총수익률: {_format_pct(rsi_test.total_return_pct)}",
        f"- test MDD: {_format_pct(rsi_test.max_drawdown_pct)} (게이트 1 {gate1})",
        f"- test Sharpe: {_format_decimal(rsi_test.sharpe_ratio, 4)} (게이트 3 {gate3})",
        f"- test 승률: {_format_pct(rsi_test.win_rate)}",
        f"- test 평균 손익비: {_format_decimal(rsi_test.avg_pnl_ratio, 4)}",
        f"- DCA baseline test 총수익률: {_format_pct(dca_return)}",
        f"- 알파: {_format_pct(alpha)} (게이트 2 {gate2})",
        "",
    ]


def _render_aggregate_md_block(
    *,
    train_avg: Decimal,
    test_avg: Decimal,
    degradation: Decimal,
    pass_threshold: Decimal,
    is_pass: bool,
    rows: list[dict[str, str]],
) -> list[str]:
    window_pass_count = sum(1 for r in rows if r["window_verdict"] == "PASS")
    return [
        "## 집계",
        "",
        f"- train 평균 총수익률: {_format_pct(train_avg)}",
        f"- test 평균 총수익률: {_format_pct(test_avg)}",
        f"- degradation_pct: {_format_pct(degradation)}",
        f"- pass_threshold: {_format_pct(pass_threshold)}",
        f"- 집계 판정: {'PASS' if is_pass else 'FAIL'}",
        "",
        f"- 윈도우 PASS 수: {window_pass_count} / {len(rows)}",
        "",
    ]


def _write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        _run_pipeline(args)
    except UniverseLoadError as exc:
        logger.error("universe load error: {e}", e=exc)
        return _EXIT_INPUT_ERROR
    except RuntimeError as exc:
        logger.error("input/runtime error: {e}", e=exc)
        return _EXIT_INPUT_ERROR
    except OSError as exc:
        logger.error("I/O error: {e}", e=exc)
        return _EXIT_IO_ERROR
    return 0


if __name__ == "__main__":
    sys.exit(main())
