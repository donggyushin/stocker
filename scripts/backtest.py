"""backtest — ORB 전략 단일 런 백테스트 CLI.

사용 예시:

```
uv run python scripts/backtest.py \
  --csv-dir data/minute_csv \
  --from 2023-01-01 --to 2025-12-31 \
  --symbols 005930,000660,035420 \
  --starting-capital 1000000 \
  --output-markdown data/backtest_report.md \
  --output-csv data/backtest_metrics.csv \
  --output-trades-csv data/backtest_trades.csv
```

동작
- `--csv-dir` 하위의 `{symbol}.csv` 를 `MinuteCsvBarLoader` 로 읽어 분봉 스트림
  공급.
- `--symbols` 미지정 시 `config/universe.yaml` 의 KOSPI 200 전체 사용.
- `BacktestEngine` 1회 실행. `StrategyConfig`/`RiskConfig`/비용률은 코드 기본값
  사용 (plan.md Phase 2 운영 기본값과 동일). 파라미터를 바꿀 경우
  `scripts/sensitivity.py` 를 먼저 돌려 sanity check 를 한다.
- 리포트 3종 출력: Markdown (육안), metrics CSV, trades CSV (운영자 재검증 용).

PASS 판정
- 리포트 상단에 `max_drawdown_pct > -0.15` (낙폭 절대값 15% 미만) 이면 PASS,
  아니면 FAIL 라벨을 기록한다. 경계 `-0.15` 정확값은 FAIL (strict greater).
  즉 MDD = -10% → PASS, MDD = -15% → FAIL, MDD = -20% → FAIL.
  PASS/FAIL 라벨은 리포트 Markdown 에만 기록하며 **exit code 에는 반영하지
  않는다** — Phase 2 전체 PASS 선언은 운영자가 walk-forward·데이터 편향·
  슬리피지 실측 괴리까지 수동 검토하는 영역이다 (CI 자동화 금지). exit code
  는 오류 분류(2 입력, 3 I/O) 전용.

제약
- 외부 네트워크·KIS·pykis 접촉 없음 — 순수 CSV + 엔진.
- 기본 출력 경로 `data/*` 는 `.gitignore` 대상. 실데이터 산출물을 커밋하지
  않는다.
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from loguru import logger

from stock_agent.backtest import (
    BacktestConfig,
    BacktestEngine,
    BacktestMetrics,
    BacktestResult,
    TradeRecord,
)
from stock_agent.backtest.dca import DCABaselineConfig, compute_dca_baseline
from stock_agent.backtest.loader import BarLoader
from stock_agent.backtest.prev_close import DailyBarPrevCloseProvider
from stock_agent.config import get_settings
from stock_agent.data import (
    HistoricalDataStore,
    KisMinuteBarLoader,
    KisMinuteBarLoadError,
    MinuteCsvBarLoader,
    MinuteCsvLoadError,
    UniverseLoadError,
    YamlBusinessDayCalendar,
    load_kospi200_universe,
)
from stock_agent.data.daily_bar_loader import DailyBarLoader
from stock_agent.strategy.factory import STRATEGY_CHOICES, build_strategy_factory

# `--strategy-type` argparse choices — factory 의 STRATEGY_CHOICES (orb/vwap-mr/
# gap-reversal) + "dca". DCA 는 BacktestEngine 우회 경로(`compute_dca_baseline`)
# 라 factory 에 들어가지 않는다.
_CLI_STRATEGY_CHOICES: tuple[str, ...] = (*STRATEGY_CHOICES, "dca")

# exit code 규약 (scripts/sensitivity.py 와 동일): 2 = 입력·설정 오류 (재시도
# 무의미), 3 = I/O 오류 (재시도 가치 있음).
_EXIT_INPUT_ERROR = 2
_EXIT_IO_ERROR = 3

# Phase 2 PASS 임계값 — plan.md Verification 섹션.
_MDD_PASS_THRESHOLD: Decimal = Decimal("-0.15")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ORB 단일 런 백테스트",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--loader",
        choices=["csv", "kis", "daily"],
        default="csv",
        help=(
            "분봉 소스. csv=MinuteCsvBarLoader(--csv-dir 필수), "
            "kis=KisMinuteBarLoader(실전 APP_KEY 3종 + IP 화이트리스트 필요, "
            "KIS 서버 최대 1년 보관), "
            "daily=DailyBarLoader(`HistoricalDataStore` 일봉 → 09:00 KST MinuteBar). "
            "Step F 일/월 단위 가설 (DCA·Golden Cross 등) 평가용."
        ),
    )
    parser.add_argument(
        "--csv-dir",
        type=Path,
        default=None,
        help="분봉 CSV 디렉토리 ({symbol}.csv). --loader=csv 때 필수, --loader=kis 때 무시.",
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
        help="쉼표 구분 종목 코드 (미지정 시 --universe-yaml 전체 사용).",
    )
    parser.add_argument(
        "--universe-yaml",
        type=Path,
        default=Path("config/universe.yaml"),
        help=(
            "유니버스 YAML 경로. --symbols 미지정 시 이 YAML 의 tickers 를 사용 "
            "(서브셋 백테스트 시 config/universe_top50.yaml 등 지정)."
        ),
    )
    parser.add_argument(
        "--starting-capital",
        type=int,
        default=1_000_000,
        help="시작 자본 (KRW).",
    )
    parser.add_argument(
        "--strategy-type",
        type=str,
        default="orb",
        choices=_CLI_STRATEGY_CHOICES,
        help=(
            "전략 선택. orb=ORBStrategy(기본), vwap-mr=VWAPMRStrategy, "
            "gap-reversal=GapReversalStrategy (ADR-0019 Step E). "
            "dca=DCABaselineStrategy (ADR-0019 Step F PR1 — `--loader=daily` 권장). "
            "dca 는 `BacktestEngine` 우회·`compute_dca_baseline` 경로."
        ),
    )
    parser.add_argument(
        "--monthly-investment",
        type=int,
        default=100_000,
        help=(
            "DCA 월 투자금 (KRW). `--strategy-type=dca` 분기에서만 사용. "
            "`starting_capital_krw` 이하여야 한다."
        ),
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=Path("data/backtest_report.md"),
        help="Markdown 리포트 출력 경로.",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("data/backtest_metrics.csv"),
        help="메트릭 CSV 출력 경로.",
    )
    parser.add_argument(
        "--output-trades-csv",
        type=Path,
        default=Path("data/backtest_trades.csv"),
        help="체결 기록 CSV 출력 경로 (TradeRecord 전체 필드).",
    )
    args = parser.parse_args(argv)
    # --loader 별 필수 인자 조건부 검증. argparse required 만으로는 표현 불가.
    if args.loader == "csv" and args.csv_dir is None:
        parser.error("--loader=csv 에는 --csv-dir 이 필요합니다.")
    return args


def _resolve_symbols(raw: str, universe_yaml: Path | None = None) -> tuple[str, ...]:
    """`--symbols` 인자 해석 — 빈 값이면 유니버스 YAML 전체.

    `scripts/sensitivity.py:_resolve_symbols` 와 동일 계약. 공용 헬퍼로
    승격은 YAGNI (현재 소비자 2개).

    `universe_yaml=None` 이면 backward-compat 으로 `load_kospi200_universe()` 를
    인자 없이 호출 (기존 단인자 호출 경로 보존). path 가 주어지면 해당 path 를
    `load_kospi200_universe(path)` 에 전달.
    """
    if raw.strip():
        parts = tuple(s.strip() for s in raw.split(",") if s.strip())
        return parts
    if universe_yaml is None:
        universe = load_kospi200_universe()
    else:
        universe = load_kospi200_universe(universe_yaml)
    if not universe.tickers:
        raise RuntimeError(
            f"유니버스 YAML 이 비어있습니다 — --symbols 로 명시하거나 "
            f"YAML 을 갱신하세요 (path={universe_yaml or 'config/universe.yaml'})."
        )
    return universe.tickers


@dataclass(frozen=True, slots=True)
class _ReportContext:
    """Markdown 렌더링에 필요한 런타임 컨텍스트 (엔진 밖 정보)."""

    start: date
    end: date
    symbols: tuple[str, ...]
    starting_capital_krw: int


def _build_backtest_config(
    args: argparse.Namespace,
    *,
    prev_close_provider: DailyBarPrevCloseProvider | None = None,
) -> BacktestConfig:
    """`--strategy-type` 분기 — `BacktestConfig` 생성.

    `orb` 분기는 `strategy_factory` 미주입 (BacktestEngine 의 ORBStrategy 디폴트
    경로 사용 — 회귀 0 보장). 그 외 분기는 `build_strategy_factory(strategy_type)`
    를 주입해 매 세션 새 인스턴스를 보장한다. `gap-reversal` 은 `prev_close_provider`
    를 함께 전달 (없으면 stub 폴백).
    """
    if args.strategy_type == "orb":
        return BacktestConfig(starting_capital_krw=args.starting_capital)
    return BacktestConfig(
        starting_capital_krw=args.starting_capital,
        strategy_factory=build_strategy_factory(
            args.strategy_type,
            prev_close_provider=prev_close_provider,
        ),
    )


def _build_prev_close_provider() -> DailyBarPrevCloseProvider:
    """`gap-reversal` 분기용 `DailyBarPrevCloseProvider` 인스턴스화.

    `HistoricalDataStore` (기본 `data/stock_agent.db`) + `YamlBusinessDayCalendar`
    (기본 `config/holidays.yaml`) 조합. 호출자가 `try/finally` 로
    `provider.close()` 보장.
    """
    daily_store = HistoricalDataStore()
    calendar = YamlBusinessDayCalendar()
    return DailyBarPrevCloseProvider(daily_store, calendar)


def _build_loader(args: argparse.Namespace) -> BarLoader:
    """`--loader` 분기로 `BarLoader` 구현체를 생성한다.

    `kis` 모드는 `get_settings()` 호출로 `.env` 실전 키를 로드해
    `KisMinuteBarLoader` 를 반환한다 (실전 키 미주입 시 생성자에서
    `KisMinuteBarLoadError` fail-fast).

    `daily` 모드는 `HistoricalDataStore` (기본 `data/stock_agent.db`) 일봉을
    09:00 KST MinuteBar 로 래핑한 `DailyBarLoader` 를 반환한다. DB 미백필 상태
    이면 `fetch_daily_ohlcv` 가 pykrx 네트워크 호출 — 결정론 보장을 위해
    `scripts/backfill_daily_bars.py` 선행 권장.
    """
    if args.loader == "kis":
        settings = get_settings()
        return KisMinuteBarLoader(settings)
    if args.loader == "daily":
        return DailyBarLoader(HistoricalDataStore())
    # csv: --csv-dir 은 _parse_args 단계에서 conditional required 통과 후.
    assert args.csv_dir is not None, "csv 모드에서 csv_dir 는 _parse_args 가 강제한다"
    return MinuteCsvBarLoader(args.csv_dir)


def _run_pipeline(args: argparse.Namespace) -> None:
    """실제 파이프라인 — 호출자가 예외 분기를 책임진다.

    엔진·로더 공개 API 만 호출. 경계를 single-purpose 로 분리해 `main()` 은
    예외 → exit code 매핑에 집중한다 (sensitivity 와 동일 기조). `KisMinuteBarLoader`
    는 SQLite 커넥션을 닫아야 하므로 `try/finally` 로 `close()` 호출.

    `--strategy-type=dca` 분기는 BacktestEngine 우회 — `_run_dca_pipeline` 위임.
    """
    if args.strategy_type == "dca":
        _run_dca_pipeline(args)
        return

    symbols = _resolve_symbols(args.symbols, args.universe_yaml)
    loader = _build_loader(args)
    prev_close_provider: DailyBarPrevCloseProvider | None = None
    if args.strategy_type == "gap-reversal":
        prev_close_provider = _build_prev_close_provider()
    config = _build_backtest_config(args, prev_close_provider=prev_close_provider)
    engine = BacktestEngine(config)

    logger.info(
        "backtest.start loader={l} from={s} to={e} symbols={n} capital={c}",
        l=args.loader,
        s=args.start,
        e=args.end,
        n=len(symbols),
        c=args.starting_capital,
    )
    try:
        result = engine.run(loader.stream(args.start, args.end, symbols))

        context = _ReportContext(
            start=args.start,
            end=args.end,
            symbols=symbols,
            starting_capital_krw=args.starting_capital,
        )

        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        args.output_trades_csv.parent.mkdir(parents=True, exist_ok=True)

        args.output_markdown.write_text(_render_markdown(result, context), encoding="utf-8")
        _write_metrics_csv(result.metrics, args.output_csv)
        _write_trades_csv(result.trades, args.output_trades_csv)
    finally:
        if prev_close_provider is not None:
            prev_close_provider.close()
        close = getattr(loader, "close", None)
        if callable(close):
            close()

    logger.info(
        "backtest.done trades={t} rejected={r} post_slippage={p} mdd={m} verdict={v}",
        t=len(result.trades),
        r=sum(result.rejected_counts.values()),
        p=result.post_slippage_rejections,
        m=_format_pct(result.metrics.max_drawdown_pct),
        v=_verdict_label(
            result.metrics.max_drawdown_pct,
            daily_equity_len=len(result.daily_equity),
            symbol_count=len(symbols),
        ),
    )


def _run_dca_pipeline(args: argparse.Namespace) -> None:
    """`--strategy-type=dca` 전용 파이프라인 — `compute_dca_baseline` 호출.

    BacktestEngine 우회. `--symbols` 미지정 시 기본 target_symbol="069500" 단일.
    `--symbols` 명시 시 첫 번째 심볼을 target_symbol 로 사용 (DCA 는 단일 종목).
    """
    target_symbol = _resolve_dca_target_symbol(args.symbols)
    loader = _build_loader(args)

    config = DCABaselineConfig(
        starting_capital_krw=args.starting_capital,
        monthly_investment_krw=args.monthly_investment,
        target_symbol=target_symbol,
    )

    logger.info(
        "dca.start loader={l} from={s} to={e} target={t} capital={c} monthly={m}",
        l=args.loader,
        s=args.start,
        e=args.end,
        t=target_symbol,
        c=args.starting_capital,
        m=args.monthly_investment,
    )

    try:
        result = compute_dca_baseline(loader, config, args.start, args.end)

        context = _ReportContext(
            start=args.start,
            end=args.end,
            symbols=(target_symbol,),
            starting_capital_krw=args.starting_capital,
        )

        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_csv.parent.mkdir(parents=True, exist_ok=True)
        args.output_trades_csv.parent.mkdir(parents=True, exist_ok=True)

        args.output_markdown.write_text(
            _render_dca_markdown(
                result,
                context,
                target_symbol=target_symbol,
                monthly_investment_krw=args.monthly_investment,
            ),
            encoding="utf-8",
        )
        _write_metrics_csv(result.metrics, args.output_csv)
        _write_trades_csv(result.trades, args.output_trades_csv)
    finally:
        close = getattr(loader, "close", None)
        if callable(close):
            close()

    logger.info(
        "dca.done trades={t} mdd={m} sharpe={s} verdict={v}",
        t=len(result.trades),
        m=_format_pct(result.metrics.max_drawdown_pct),
        s=_format_decimal(result.metrics.sharpe_ratio, 4),
        v=_dca_verdict_label(result.metrics, daily_equity_len=len(result.daily_equity)),
    )


def _resolve_dca_target_symbol(raw_symbols: str) -> str:
    """`--symbols` 인자에서 DCA target_symbol 추출. 미지정 시 기본값 069500."""
    parts: list[str] = [s.strip() for s in raw_symbols.split(",") if s.strip()]
    if not parts:
        return "069500"
    first = parts[0]
    if len(parts) > 1:
        logger.warning(
            "DCA 는 단일 심볼 — `--symbols={}` 중 첫 번째({})만 사용",
            raw_symbols,
            first,
        )
    return first


# ADR-0022 Step F 게이트 — Step F 가설 풀 평가용 임계값.
_DCA_MDD_THRESHOLD: Decimal = Decimal("-0.25")  # 게이트 1: MDD > -25%
_DCA_SHARPE_THRESHOLD: Decimal = Decimal("0.3")  # 게이트 3: 연환산 Sharpe > 0.3
_DCA_MIN_SESSIONS_FOR_PASS: int = 240  # ADR-0017 — 240 영업일 미만은 PASS 라벨 신뢰도 낮음.


def _dca_verdict_label(metrics: BacktestMetrics, *, daily_equity_len: int) -> str:
    """ADR-0022 게이트 1 + 게이트 3 동시 판정. 게이트 2 (DCA 대비 알파) 는 자기 자신 N/A.

    게이트 1: `max_drawdown_pct > -0.25`.
    게이트 3: `sharpe_ratio > 0.3` (240 영업일 이상 표본에서만 신뢰).

    Args:
        metrics: BacktestMetrics — `max_drawdown_pct`/`sharpe_ratio` 검사.
        daily_equity_len: 영업일 수. 240 미만이면 caveat 라벨.

    Returns:
        "PASS" / "FAIL (게이트 1)" / "FAIL (게이트 3)" / "FAIL (게이트 1·3)" /
        "PASS (참고용 — 표본 240 미만)" 형태.
    """
    gate1_pass = metrics.max_drawdown_pct > _DCA_MDD_THRESHOLD
    gate3_pass = metrics.sharpe_ratio > _DCA_SHARPE_THRESHOLD
    failed: list[str] = []
    if not gate1_pass:
        failed.append("게이트 1")
    if not gate3_pass:
        failed.append("게이트 3")
    if failed:
        return f"FAIL ({'·'.join(failed)})"
    if daily_equity_len < _DCA_MIN_SESSIONS_FOR_PASS:
        return "PASS (참고용 — 표본 240 미만)"
    return "PASS"


def _render_dca_markdown(
    result: BacktestResult,
    context: _ReportContext,
    *,
    target_symbol: str,
    monthly_investment_krw: int,
) -> str:
    """DCA baseline 전용 Markdown 리포트 — ADR-0022 게이트 적용."""
    metrics = result.metrics
    verdict = _dca_verdict_label(metrics, daily_equity_len=len(result.daily_equity))
    lines: list[str] = []

    lines.append("# DCA Baseline 백테스트 리포트 (ADR-0019 Step F PR1)")
    lines.append("")
    lines.append(f"- 기간: `{context.start.isoformat()}` ~ `{context.end.isoformat()}`")
    lines.append(f"- target_symbol: `{target_symbol}`")
    lines.append(f"- 시작 자본: {context.starting_capital_krw:,} KRW")
    lines.append(f"- 월 투자금: {monthly_investment_krw:,} KRW")
    lines.append(f"- 매수 횟수 (lots): {len(result.trades)}")
    lines.append("")
    lines.append(f"## ADR-0022 게이트 판정: **{verdict}**")
    lines.append("")
    lines.append(
        f"- 게이트 1 (MDD > {_format_pct(_DCA_MDD_THRESHOLD)}): "
        f"`{_format_pct(metrics.max_drawdown_pct)}`"
    )
    lines.append("- 게이트 2 (DCA 대비 알파): **N/A** — 자기 자신이 baseline.")
    lines.append(
        f"- 게이트 3 (Sharpe > {_format_decimal(_DCA_SHARPE_THRESHOLD, 2)}): "
        f"`{_format_decimal(metrics.sharpe_ratio, 4)}`"
    )
    lines.append("")
    lines.append("## 메트릭")
    lines.append("")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---|")
    lines.append(f"| 총수익률 (mark-to-market) | {_format_pct(metrics.total_return_pct)} |")
    lines.append(f"| 최대 낙폭 (MDD) | {_format_pct(metrics.max_drawdown_pct)} |")
    lines.append(f"| 샤프 비율 (연환산) | {_format_decimal(metrics.sharpe_ratio, 4)} |")
    lines.append(f"| 승률 (lot 별 가상 청산) | {_format_pct(metrics.win_rate)} |")
    lines.append(f"| 평균 손익비 | {_format_decimal(metrics.avg_pnl_ratio, 4)} |")
    lines.append(f"| 순손익 (KRW, mark-to-market) | {metrics.net_pnl_krw:,} |")
    lines.append("")
    lines.append("## 일일 자본 요약")
    lines.append("")
    if result.daily_equity:
        equities = [row.equity_krw for row in result.daily_equity]
        first = result.daily_equity[0]
        last = result.daily_equity[-1]
        trough = min(result.daily_equity, key=lambda r: r.equity_krw)
        lines.append(f"- 세션 수: {len(result.daily_equity)}")
        lines.append(f"- 시작: `{first.session_date.isoformat()}` {first.equity_krw:,} KRW")
        lines.append(f"- 종료: `{last.session_date.isoformat()}` {last.equity_krw:,} KRW")
        lines.append(f"- 최저점: `{trough.session_date.isoformat()}` {trough.equity_krw:,} KRW")
        lines.append(f"- 최고점 자본: {max(equities):,} KRW")
    else:
        lines.append("- 세션 없음 (입력 분봉이 비어있거나 날짜 필터 결과가 0건)")
    lines.append("")
    lines.append("## 주의")
    lines.append("")
    lines.append(
        "- DCA 는 영구 보유 가정 — 본 리포트의 `total_return_pct` 는 mark-to-market "
        "(슬리피지·세금 미반영). `TradeRecord.net_pnl_krw` 는 lot 별 가상 청산 비용 반영값 — "
        "실제 청산 의도 없음."
    )
    lines.append(
        "- 후속 PR (F2~F5) 의 알파 비교는 본 리포트의 `total_return_pct` 를 baseline 으로 사용."
    )
    lines.append("")
    return "\n".join(lines)


def _render_markdown(result: BacktestResult, context: _ReportContext) -> str:
    """`BacktestResult` → 사람이 읽는 Markdown 리포트."""
    metrics = result.metrics
    verdict = _verdict_label(
        metrics.max_drawdown_pct,
        daily_equity_len=len(result.daily_equity),
        symbol_count=len(context.symbols),
    )
    lines: list[str] = []

    lines.append("# ORB 백테스트 리포트")
    lines.append("")
    lines.append(f"- 기간: `{context.start.isoformat()}` ~ `{context.end.isoformat()}`")
    lines.append(f"- 종목 수: {len(context.symbols)}")
    lines.append(f"- 시작 자본: {context.starting_capital_krw:,} KRW")
    lines.append(f"- 거래 수: {len(result.trades)}")
    lines.append("")
    lines.append(f"## Phase 2 PASS 판정: **{verdict}**")
    lines.append("")
    lines.append(
        f"- 기준: `max_drawdown_pct > {_format_pct(_MDD_PASS_THRESHOLD)}` "
        f"(낙폭 절대값 15% 미만 — plan.md Verification § Phase 2). "
        f"경계 `{_format_pct(_MDD_PASS_THRESHOLD)}` 정확값은 FAIL."
    )
    lines.append(f"- 실측: `{_format_pct(metrics.max_drawdown_pct)}`")
    lines.append("")
    lines.append("## 메트릭")
    lines.append("")
    lines.append("| 항목 | 값 |")
    lines.append("|---|---|")
    lines.append(f"| 총수익률 | {_format_pct(metrics.total_return_pct)} |")
    lines.append(f"| 최대 낙폭 (MDD) | {_format_pct(metrics.max_drawdown_pct)} |")
    lines.append(f"| 샤프 비율 (연환산) | {_format_decimal(metrics.sharpe_ratio, 4)} |")
    lines.append(f"| 승률 | {_format_pct(metrics.win_rate)} |")
    lines.append(f"| 평균 손익비 | {_format_decimal(metrics.avg_pnl_ratio, 4)} |")
    lines.append(f"| 일평균 거래 수 | {_format_decimal(metrics.trades_per_day, 3)} |")
    lines.append(f"| 순손익 (KRW) | {metrics.net_pnl_krw:,} |")
    lines.append("")
    lines.append("## 일일 자본 요약")
    lines.append("")
    if result.daily_equity:
        equities = [row.equity_krw for row in result.daily_equity]
        first = result.daily_equity[0]
        last = result.daily_equity[-1]
        trough = min(result.daily_equity, key=lambda r: r.equity_krw)
        lines.append(f"- 세션 수: {len(result.daily_equity)}")
        lines.append(f"- 시작: `{first.session_date.isoformat()}` {first.equity_krw:,} KRW")
        lines.append(f"- 종료: `{last.session_date.isoformat()}` {last.equity_krw:,} KRW")
        lines.append(f"- 최저점: `{trough.session_date.isoformat()}` {trough.equity_krw:,} KRW")
        lines.append(f"- 최고점 자본: {max(equities):,} KRW")
    else:
        lines.append("- 세션 없음 (입력 분봉이 비어있거나 날짜 필터 결과가 0건)")
    lines.append("")
    lines.append("## 거부 카운트")
    lines.append("")
    if result.rejected_counts:
        lines.append("| 사유 | 카운트 |")
        lines.append("|---|---|")
        for reason in sorted(result.rejected_counts):
            lines.append(f"| `{reason}` | {result.rejected_counts[reason]} |")
    else:
        lines.append("- RiskManager 사전 거부 0건")
    lines.append("")
    lines.append(f"- 사후 슬리피지 거부: {result.post_slippage_rejections}건")
    lines.append("")
    lines.append("## 주의")
    lines.append("")
    lines.append(
        "- 이 리포트의 `PASS` 라벨은 단일 구간 MDD 만 본다. "
        "실전 전환은 walk-forward 검증(Phase 5 후보) + 모의투자 2주 무사고(Phase 3) "
        "선행을 전제한다."
    )
    lines.append(
        "- 슬리피지·수수료·거래세는 백테스트 기본값이며 실전 괴리는 Phase 4 주간 회고로 "
        "측정한다 (plan.md Phase 4)."
    )
    lines.append("")
    return "\n".join(lines)


def _write_metrics_csv(metrics: BacktestMetrics, path: Path) -> None:
    """`metric,value` 2열 CSV — 프로그래매틱 후처리 용."""
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(("metric", "value"))
        writer.writerow(("total_return_pct", str(metrics.total_return_pct)))
        writer.writerow(("max_drawdown_pct", str(metrics.max_drawdown_pct)))
        writer.writerow(("sharpe_ratio", str(metrics.sharpe_ratio)))
        writer.writerow(("win_rate", str(metrics.win_rate)))
        writer.writerow(("avg_pnl_ratio", str(metrics.avg_pnl_ratio)))
        writer.writerow(("trades_per_day", str(metrics.trades_per_day)))
        writer.writerow(("net_pnl_krw", str(metrics.net_pnl_krw)))


def _write_trades_csv(trades: tuple[TradeRecord, ...], path: Path) -> None:
    """체결 1쌍(entry~exit) 단위 전체 덤프 — 운영자 재검증 용."""
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            (
                "symbol",
                "entry_ts",
                "entry_price",
                "exit_ts",
                "exit_price",
                "qty",
                "exit_reason",
                "gross_pnl_krw",
                "commission_krw",
                "tax_krw",
                "net_pnl_krw",
            )
        )
        for trade in trades:
            writer.writerow(
                (
                    trade.symbol,
                    trade.entry_ts.isoformat(),
                    str(trade.entry_price),
                    trade.exit_ts.isoformat(),
                    str(trade.exit_price),
                    trade.qty,
                    trade.exit_reason,
                    trade.gross_pnl_krw,
                    trade.commission_krw,
                    trade.tax_krw,
                    trade.net_pnl_krw,
                )
            )


def _verdict_label(
    mdd: Decimal,
    *,
    daily_equity_len: int | None = None,
    symbol_count: int | None = None,
) -> str:
    """`mdd > -0.15` (낙폭 절대값 15% 미만) 이면 PASS.

    MDD 는 음수 또는 0 (`BacktestMetrics.max_drawdown_pct` 계약). 임계값
    `-0.15` 보다 **더 얕은 음수**(0 에 가까움 — 손실이 적음) 이면 PASS.
    경계값 `-0.15` 정확일치는 FAIL (strict greater). 예: `-0.10 → PASS`,
    `-0.15 → FAIL`, `-0.20 → FAIL`, `0 → PASS`.

    의도: plan.md Phase 2 Verification "MDD 낙폭 15% 이내" — "낙폭 제한
    기준" 이므로 낙폭이 더 깊을수록 FAIL 이 되어야 한다.

    Caveat (ADR-0017 결정 3·4 코드 반영):
    - `daily_equity_len < 240` → "표본 240 미만" 주의 추가.
    - `symbol_count == 1` → "단일 종목" 주의 추가.
    PASS 인 경우에만 caveat 를 합쳐 `"PASS (참고용 — ...)"` 로 반환하고,
    FAIL 은 caveat 무관 `"FAIL"` 만 반환. 두 인자 모두 `None` 이면 기존
    단순 이진 라벨 동작 (backward compat).
    """
    if mdd <= _MDD_PASS_THRESHOLD:
        return "FAIL"
    caveats: list[str] = []
    if daily_equity_len is not None and daily_equity_len < 240:
        caveats.append("표본 240 미만")
    if symbol_count is not None and symbol_count == 1:
        caveats.append("단일 종목")
    if not caveats:
        return "PASS"
    return f"PASS (참고용 — {', '.join(caveats)})"


def _format_pct(value: Decimal) -> str:
    """`Decimal(0.1234)` → `"12.34%"`. float 경유 — 리포트 용 2자리."""
    return f"{float(value) * 100:.2f}%"


def _format_decimal(value: Decimal, digits: int) -> str:
    """`Decimal` → 고정 소수점 문자열. 샤프·손익비·일평균 거래 수 공용."""
    return f"{float(value):.{digits}f}"


def main(argv: list[str] | None = None) -> int:
    """CLI 엔트리포인트 — 예외 → exit code 매핑만 책임진다.

    예외 분류 (프로젝트 가드레일 "generic except Exception 금지" 기조 준수):

    - `MinuteCsvLoadError` · `UniverseLoadError` · `RuntimeError` → exit 2
      (입력·설정 오류, 재시도 무의미). `UniverseLoadError` 는 `Exception`
      직상속이라 `RuntimeError` 에 잡히지 않으므로 별도 분기 필요.
    - `OSError` → exit 3 (I/O 오류, 재시도 가치 있음).
    - 그 외는 버그로 간주해 Python traceback 그대로 종료.
    """
    args = _parse_args(argv)

    if args.start > args.end:
        logger.error(f"--from({args.start}) 는 --to({args.end}) 이전이어야 합니다.")
        return _EXIT_INPUT_ERROR
    if args.starting_capital <= 0:
        logger.error(f"--starting-capital 은 양수여야 합니다 (got={args.starting_capital}).")
        return _EXIT_INPUT_ERROR

    try:
        _run_pipeline(args)
    except MinuteCsvLoadError as e:
        logger.error(f"CSV 입력 오류: {e}")
        return _EXIT_INPUT_ERROR
    except KisMinuteBarLoadError as e:
        logger.error(f"KIS 분봉 입력 오류: {e}")
        return _EXIT_INPUT_ERROR
    except UniverseLoadError as e:
        logger.error(f"유니버스 YAML 오류: {e}")
        return _EXIT_INPUT_ERROR
    except RuntimeError as e:
        logger.error(f"설정·검증 오류: {e}")
        return _EXIT_INPUT_ERROR
    except OSError as e:
        logger.exception(f"I/O 오류 (재시도 가능): {e}")
        return _EXIT_IO_ERROR
    return 0


if __name__ == "__main__":
    sys.exit(main())
