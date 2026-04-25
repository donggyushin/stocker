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
import functools
import os
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from loguru import logger

from stock_agent.backtest import (
    BacktestConfig,
    SensitivityRow,
    append_sensitivity_row,
    default_grid,
    filter_remaining_combos,
    load_sensitivity_rows,
    merge_sensitivity_rows,
    render_markdown_table,
    run_sensitivity_combos,
    run_sensitivity_combos_parallel,
    write_csv,
)
from stock_agent.backtest.loader import BarLoader
from stock_agent.config import get_settings
from stock_agent.data import (
    KisMinuteBarLoader,
    KisMinuteBarLoadError,
    MinuteCsvBarLoader,
    MinuteCsvLoadError,
    UniverseLoadError,
    load_kospi200_universe,
)

# exit code 규약: 2 = 입력·설정 오류 (재시도 무의미), 3 = I/O 오류 (재시도 가치 있음).
_EXIT_INPUT_ERROR = 2
_EXIT_IO_ERROR = 3

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
        "--loader",
        choices=["csv", "kis"],
        default="csv",
        help=(
            "분봉 소스. csv=MinuteCsvBarLoader(--csv-dir 필수), "
            "kis=KisMinuteBarLoader(실전 APP_KEY 3종 + IP 화이트리스트 필요, "
            "KIS 서버 최대 1년 보관)."
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
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help=(
            "병렬 워커 수 (ProcessPool, ADR-0020). 미지정 시 기본값 "
            "min(os.cpu_count() - 1, 8). 1 이면 직렬 경로(run_sensitivity) — "
            "회귀 안전망. 0·음수 거부 (RuntimeError, exit 2). KIS·CSV loader "
            "모두 워커별 새 인스턴스를 생성 (loader 는 pickle 불가)."
        ),
    )
    parser.add_argument(
        "--resume",
        type=Path,
        default=None,
        help=(
            "이전 실행의 CSV 경로. 존재하면 거기 담긴 조합은 skip 하고 "
            "미완료 조합만 실행 후 병합해 새 CSV/Markdown 으로 덮어쓴다. "
            "경로를 지정했지만 파일이 없으면 전체 실행(신규)으로 간주. "
            "컴퓨터 freeze / 재부팅 / 세션 종료에 대한 내성을 제공 — "
            "같은 --output-csv 경로를 지정하면 idempotent 하게 이어서 실행 가능."
        ),
    )
    args = parser.parse_args(argv)
    if args.loader == "csv" and args.csv_dir is None:
        parser.error("--loader=csv 에는 --csv-dir 이 필요합니다.")
    return args


def _build_loader_primitive(loader_kind: str, csv_dir: Path | None) -> BarLoader:
    """`--loader` 분기로 `BarLoader` 구현체 생성 — primitive 인자만 받는다.

    `argparse.Namespace` 를 받지 않는 이유: `functools.partial(_build_loader_primitive,
    args.loader, args.csv_dir)` 형태로 ProcessPool 워커에 전달할 때 pickle 호환을
    보장하기 위함 (Namespace 자체는 pickleable 이지만 primitive 만 받는 편이
    spawn 컨텍스트에서 안정적).
    """
    if loader_kind == "kis":
        settings = get_settings()
        return KisMinuteBarLoader(settings)
    assert csv_dir is not None, "csv 모드에서 csv_dir 는 _parse_args 가 강제한다"
    return MinuteCsvBarLoader(csv_dir)


def _build_loader(args: argparse.Namespace) -> BarLoader:
    """`_build_loader_primitive` 의 Namespace 어댑터 — 직렬 경로 호환."""
    return _build_loader_primitive(args.loader, args.csv_dir)


def _resolve_workers(raw: int | None) -> int:
    """`--workers` 인자 해석. `None` → `min(os.cpu_count() - 1, 8)`.

    Raises:
        RuntimeError: `raw <= 0`. (`main()` 의 except RuntimeError 가 exit 2 매핑.)
    """
    if raw is None:
        cpu = os.cpu_count() or 2
        return max(1, min(cpu - 1, 8))
    if raw < 1:
        raise RuntimeError(f"--workers 는 1 이상이어야 합니다 (got={raw})")
    return raw


def _resolve_symbols(raw: str) -> tuple[str, ...]:
    """`--symbols` 인자 해석 — 빈 값이면 유니버스 YAML 전체.

    `raw` 에 쉼표만 들어오는 극단 케이스는 `raw.strip()` 이 falsy 로 평가되어
    자동으로 유니버스 로드 분기로 빠진다 (별도 RuntimeError 불필요 — 현재
    분기 구조상 도달 불가능한 방어 코드는 두지 않는다).
    """
    if raw.strip():
        parts = tuple(s.strip() for s in raw.split(",") if s.strip())
        return parts
    universe = load_kospi200_universe()
    if not universe.tickers:
        raise RuntimeError(
            "config/universe.yaml 이 비어있습니다 — --symbols 로 명시하거나 "
            "유니버스 YAML 을 갱신하세요."
        )
    return universe.tickers


def _run_pipeline(args: argparse.Namespace) -> None:
    """실제 파이프라인 — 호출자가 예외 분기를 책임진다.

    경계를 single-purpose 로 분리해 `main()` 의 wrapper 는 오직 예외 → exit
    code 매핑에만 집중한다. 라이브러리 모듈의 `RuntimeError` fail-fast 기조를
    그대로 전파.

    `--workers` 분기 (ADR-0020):
    - `1` → 직렬 경로 `run_sensitivity_combos` (회귀 안전망).
    - `>= 2` → `run_sensitivity_combos_parallel` (`ProcessPoolExecutor`).
      loader 는 워커별 새 인스턴스로 생성하므로 메인 프로세스에서 loader 를
      만들지 않는다.

    `--resume` 분기:
    - 미지정 → 전체 그리드 실행 (기존 동작), incremental flush 없음.
    - 지정 + 파일 부재 → 전체 실행 + 조합 단위 incremental flush (Issue #82).
    - 지정 + 파일 존재 → 기존 row 로드 → 미완료 조합만 실행 + 조합 단위
      incremental flush. 이미 모두 완료된 상태면 엔진 호출 0회 + 마지막 1회
      재렌더만. freeze / 재부팅 내성.

    Incremental flush (Issue #82): `--resume` 가 지정되면 `on_row=_flush` 를
    엔진 함수에 주입해 조합 1개 종료 시점마다 `args.output_csv` 에 atomic
    append. 직렬·병렬 양쪽 동일. 메인 프로세스 단일 writer.
    """
    symbols = _resolve_symbols(args.symbols)
    workers = _resolve_workers(args.workers)
    base_config = BacktestConfig(starting_capital_krw=args.starting_capital)
    grid = default_grid()

    existing_rows: tuple[SensitivityRow, ...] = ()
    remaining_combos = list(grid.iter_combinations())
    if args.resume is not None and args.resume.exists():
        existing_rows = load_sensitivity_rows(args.resume, grid)
        completed = {row.params for row in existing_rows}
        remaining_combos = filter_remaining_combos(grid, completed)
        logger.info(
            "sensitivity.resume loaded={loaded} remaining={remaining} path={p}",
            loaded=len(existing_rows),
            remaining=len(remaining_combos),
            p=args.resume,
        )

    logger.info(
        "sensitivity.start loader={l} from={s} to={e} symbols={n} "
        "combos_total={c} combos_remaining={r} workers={w}",
        l=args.loader,
        s=args.start,
        e=args.end,
        n=len(symbols),
        c=grid.size,
        r=len(remaining_combos),
        w=workers,
    )

    # output_csv 디렉터리 미리 생성 — incremental flush 가 첫 호출에서 디렉터리
    # 부재로 실패하지 않도록 엔진 실행 전에 보장.
    args.output_csv.parent.mkdir(parents=True, exist_ok=True)

    # `--resume` 지정 시에만 incremental flush 콜백 주입 (Issue #82).
    on_row_callback: Callable[[SensitivityRow], None] | None = None
    if args.resume is not None:

        def _flush(row: SensitivityRow) -> None:
            append_sensitivity_row(row, args.output_csv, grid)

        on_row_callback = _flush

    new_rows: tuple[SensitivityRow, ...] = ()
    if remaining_combos:
        if workers == 1:
            loader = _build_loader(args)
            try:
                new_rows = run_sensitivity_combos(
                    loader=loader,
                    start=args.start,
                    end=args.end,
                    symbols=symbols,
                    base_config=base_config,
                    combos=remaining_combos,
                    on_row=on_row_callback,
                )
            finally:
                close = getattr(loader, "close", None)
                if callable(close):
                    close()
        else:
            loader_factory = functools.partial(
                _build_loader_primitive,
                args.loader,
                args.csv_dir,
            )
            new_rows = run_sensitivity_combos_parallel(
                loader_factory=loader_factory,
                start=args.start,
                end=args.end,
                symbols=symbols,
                base_config=base_config,
                combos=remaining_combos,
                max_workers=workers,
                on_row=on_row_callback,
            )
    else:
        logger.info("sensitivity.skip_engine — 이미 완료된 조합으로 재렌더만 수행")

    merged_rows = merge_sensitivity_rows(existing_rows, new_rows, grid)

    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown = render_markdown_table(
        merged_rows,
        sort_by=args.sort_by,
        descending=not args.ascending,
    )
    args.output_markdown.write_text(markdown, encoding="utf-8")
    # 마지막 1회 fully render — incremental flush 의 최종본 (정렬·서식 일관성
    # 보강). 동일 컨텐츠라도 멱등.
    write_csv(merged_rows, args.output_csv)
    logger.info(
        "sensitivity.done rows={n} markdown={m} csv={c}",
        n=len(merged_rows),
        m=args.output_markdown,
        c=args.output_csv,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI 엔트리포인트 — 예외 → exit code 매핑만 책임진다.

    예외 분류 (프로젝트 가드레일 "generic except Exception 금지" 기조 준수 —
    좁힌 타입으로만 잡고 나머지는 Python 기본 전파):

    - `MinuteCsvLoadError` · `KisMinuteBarLoadError` · `UniverseLoadError` ·
      `RuntimeError` → exit 2 (입력·설정 오류, 재시도 무의미). CSV 스키마
      오류, 알 수 없는 prefix/필드, 범위 검증 위반, 유니버스 YAML 결손 등.
      `UniverseLoadError` 는 `Exception` 직상속이라 `RuntimeError` 분기에
      잡히지 않으므로 별도 분기 필요 (`scripts/backtest.py` 와 동일 계약).
    - `OSError` → exit 3 (I/O 오류, 재시도 가치 있음). 디스크 풀·권한·경로
      오류 등.
    - 위 이외의 예외는 버그로 간주해 Python traceback 그대로 종료 (loguru 가
      stderr 에 기록).
    """
    args = _parse_args(argv)

    if args.start > args.end:
        logger.error(f"--from({args.start}) 는 --to({args.end}) 이전이어야 합니다.")
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
