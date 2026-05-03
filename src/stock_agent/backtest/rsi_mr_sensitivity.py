"""ADR-0023 C4 검증 — RSI 평균회귀 sensitivity grid.

ADR-0023 의 C4 게이팅 작업 — `RSIMRStrategy` 5축 파라미터 sensitivity 평가.
기존 `backtest/sensitivity.py` 는 ORB 전용 (`BacktestEngine.run()` 경로 +
`strategy.*`/`risk.*`/`engine.*` prefix 강제) 이라 `compute_rsi_mr_baseline`
(`backtest/rsi_mr.py`) + `RSIMRConfig` 와 비호환. 본 모듈은 RSI MR 전용 자체
DTO + grid + runner.

책임 범위
- 5축 그리드 (`rsi_period` / `oversold_threshold` / `overbought_threshold` /
  `stop_loss_pct` / `max_positions`) Cartesian product 생성.
- 각 조합마다 `RSIMRBaselineConfig` 의 해당 필드를 `dataclasses.replace` 로
  갱신 후 `compute_rsi_mr_baseline` 호출.
- 결과를 `RSIMRSensitivityRow` 로 변환 — ADR-0022 게이트 3종 자동 판정.
- 직렬 경로 (`run_rsi_mr_sensitivity`) + 병렬 경로 (`run_rsi_mr_sensitivity_parallel`,
  ADR-0020 분석 도구 예외).
- CSV / Markdown 렌더 + incremental flush (`append_row` / `load_completed_combos`).

설계 원칙 (`backtest/sensitivity.py` 와 동일 기조)
- 외부 I/O = CSV 쓰기 경로 1개만.
- 결정론 — 그리드 순회는 축 선언 순서 + 각 축 후보값 선언 순서 고정.
- generic ``except Exception`` 금지. 사용자 입력 오류 → ``RuntimeError`` 전파.
- ``@dataclass(frozen=True, slots=True)`` 로 DTO 불변화.

ADR-0022 게이트 (자동 판정)
- 게이트 1 (MDD): ``metrics.max_drawdown_pct > Decimal("-0.25")``
- 게이트 2 (DCA 대비 알파): ``dca_alpha_pct > Decimal("0")`` —
  ``dca_alpha_pct = metrics.total_return_pct - dca_baseline_return_pct``
- 게이트 3 (Sharpe): ``metrics.sharpe_ratio > Decimal("0.3")``

엄격 부등호 (경계값 = FAIL).
"""

from __future__ import annotations

import contextlib
import csv
import dataclasses
import os
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from multiprocessing.context import BaseContext
from pathlib import Path
from typing import Any

from loguru import logger

from stock_agent.backtest.engine import BacktestMetrics, BacktestResult
from stock_agent.backtest.loader import BarLoader
from stock_agent.backtest.rsi_mr import RSIMRBaselineConfig, compute_rsi_mr_baseline

# RSIMRBaselineConfig 의 필드 중 그리드 변동 허용 5종.
# universe / position_pct / starting_capital / 비용 필드는 그리드 대상 아님.
_TUNABLE_FIELDS: frozenset[str] = frozenset(
    {
        "rsi_period",
        "oversold_threshold",
        "overbought_threshold",
        "stop_loss_pct",
        "max_positions",
    }
)

# render_markdown_table / write_csv 의 sort_by 허용 키 — BacktestMetrics 필드 7 +
# 보조 5 (trade_count, dca_alpha_pct, gate1_pass, gate2_pass, gate3_pass).
_SORTABLE_KEYS: frozenset[str] = frozenset(
    {
        "total_return_pct",
        "max_drawdown_pct",
        "sharpe_ratio",
        "win_rate",
        "avg_pnl_ratio",
        "trades_per_day",
        "net_pnl_krw",
        "trade_count",
        "dca_alpha_pct",
    }
)

# 게이트 임계값 — ADR-0022 명시.
_GATE1_MDD_THRESHOLD: Decimal = Decimal("-0.25")
_GATE2_ALPHA_THRESHOLD: Decimal = Decimal("0")
_GATE3_SHARPE_THRESHOLD: Decimal = Decimal("0.3")


@dataclass(frozen=True, slots=True)
class RSIMRParameterAxis:
    """RSI MR 그리드의 단일 축 — ``name`` 과 ``values`` 후보 리스트.

    ``name`` 은 ``RSIMRBaselineConfig`` 필드명 그대로 — 별도 prefix 없음
    (전 축이 동일 config 의 필드를 대상으로 함, 라우팅 불필요).

    ``values`` 는 비어있을 수 없고 ``==`` 기준 중복도 허용하지 않는다.

    Raises:
        RuntimeError: ``name`` 이 빈 문자열, ``values`` 가 비어있거나 중복 포함.
    """

    name: str
    values: tuple[Any, ...]

    def __post_init__(self) -> None:
        if not self.name:
            raise RuntimeError("RSIMRParameterAxis.name 은 비어있을 수 없습니다")
        if not self.values:
            raise RuntimeError(
                f"RSIMRParameterAxis.values 는 1개 이상이어야 합니다 (name={self.name})"
            )
        if len(frozenset(self.values)) != len(self.values):
            seen: set[Any] = set()
            for v in self.values:
                if v in seen:
                    raise RuntimeError(
                        "RSIMRParameterAxis.values 에 중복 값이 있습니다 "
                        f"(name={self.name}, value={v!r})"
                    )
                seen.add(v)


@dataclass(frozen=True, slots=True)
class RSIMRSensitivityGrid:
    """축 목록을 Cartesian product 로 조합하는 그리드.

    축 순서 = 조합 dict 의 키 삽입 순서 → 결정론적 순회.

    Raises:
        RuntimeError: ``axes`` 가 빈 튜플이거나 축 이름이 중복될 때.
    """

    axes: tuple[RSIMRParameterAxis, ...]

    def __post_init__(self) -> None:
        if not self.axes:
            raise RuntimeError("RSIMRSensitivityGrid.axes 는 1개 이상이어야 합니다")
        names = [axis.name for axis in self.axes]
        if len(set(names)) != len(names):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise RuntimeError(f"RSIMRSensitivityGrid.axes 에 중복된 이름이 있습니다: {dupes}")

    def iter_combinations(self) -> Iterator[dict[str, Any]]:
        """각 조합을 ``{name: value}`` dict 로 yield.

        축 선언 순서 고정 — 마지막 축이 가장 빠른 회전 (inner loop).
        """
        indices = [0] * len(self.axes)
        sizes = [len(axis.values) for axis in self.axes]
        while True:
            pairs = zip(self.axes, indices, strict=True)
            yield {axis.name: axis.values[idx] for axis, idx in pairs}
            pos = len(self.axes) - 1
            while pos >= 0:
                indices[pos] += 1
                if indices[pos] < sizes[pos]:
                    break
                indices[pos] = 0
                pos -= 1
            if pos < 0:
                return

    @property
    def size(self) -> int:
        total = 1
        for axis in self.axes:
            total *= len(axis.values)
        return total


@dataclass(frozen=True, slots=True)
class RSIMRSensitivityRow:
    """RSI MR 그리드 1 조합의 실행 결과 + ADR-0022 게이트 판정.

    ``params`` 는 ``(축 이름, 적용된 값)`` 의 순서 있는 튜플 (축 선언 순서).
    ``metrics`` 는 ``BacktestMetrics`` 그대로 재사용 — 엔진 진화 자동 추종.

    게이트 ``gate{1,2,3}_pass`` 와 ``all_gates_pass`` 는 호출자 (`run_rsi_mr_sensitivity`)
    가 임계값 비교 결과를 주입한 값. ``__post_init__`` 에서 일관성 검증 없음 —
    렌더러는 주입된 값을 그대로 사용한다 (단, 본 클래스 외부에서 만들 때는
    `_compute_gate_flags` 헬퍼 사용 권장).

    Raises:
        RuntimeError: ``params`` 튜플에 중복된 축 이름이 포함될 때.
    """

    params: tuple[tuple[str, Any], ...]
    metrics: BacktestMetrics
    trade_count: int
    dca_alpha_pct: Decimal
    gate1_pass: bool
    gate2_pass: bool
    gate3_pass: bool
    all_gates_pass: bool

    def __post_init__(self) -> None:
        names = [name for name, _ in self.params]
        if len(set(names)) != len(names):
            raise RuntimeError(
                f"RSIMRSensitivityRow.params 에 중복된 축 이름이 있습니다: {sorted(set(names))}"
            )

    def params_dict(self) -> dict[str, Any]:
        return dict(self.params)


def step_f_rsi_mr_grid() -> RSIMRSensitivityGrid:
    """ADR-0023 C4 RSI MR sensitivity grid — 96 조합.

    5 축 (3×2×2×4×2 = 96):

    - ``rsi_period``: (10, 14, 21)
    - ``oversold_threshold``: (Decimal("25"), Decimal("30"))
    - ``overbought_threshold``: (Decimal("70"), Decimal("75"))
    - ``stop_loss_pct``: (Decimal("0.02"), Decimal("0.03"), Decimal("0.04"), Decimal("0.05"))
    - ``max_positions``: (5, 10)

    현행 PR5 파라미터 (rsi_period=14, oversold=30, overbought=70, stop_loss=0.03,
    max_positions=10) 가 그리드 내부에 포함된다 — "현행 vs 최상위" 비교 자동.
    """
    return RSIMRSensitivityGrid(
        axes=(
            RSIMRParameterAxis(
                name="rsi_period",
                values=(10, 14, 21),
            ),
            RSIMRParameterAxis(
                name="oversold_threshold",
                values=(Decimal("25"), Decimal("30")),
            ),
            RSIMRParameterAxis(
                name="overbought_threshold",
                values=(Decimal("70"), Decimal("75")),
            ),
            RSIMRParameterAxis(
                name="stop_loss_pct",
                values=(
                    Decimal("0.02"),
                    Decimal("0.03"),
                    Decimal("0.04"),
                    Decimal("0.05"),
                ),
            ),
            RSIMRParameterAxis(
                name="max_positions",
                values=(5, 10),
            ),
        ),
    )


def run_rsi_mr_sensitivity(
    loader: BarLoader,
    base_config: RSIMRBaselineConfig,
    grid: RSIMRSensitivityGrid,
    start: date,
    end: date,
    *,
    dca_baseline_return_pct: Decimal,
    on_row: Callable[[RSIMRSensitivityRow], None] | None = None,
) -> tuple[RSIMRSensitivityRow, ...]:
    """그리드의 각 조합으로 ``compute_rsi_mr_baseline`` 을 반복 실행.

    동일 ``loader`` 를 조합마다 재호출 — ``BarLoader`` Protocol 의 "재호출 안전"
    계약에 의존. ``base_config`` 의 ``universe`` / ``position_pct`` / 비용 / 자본
    필드는 모든 조합에서 그대로 유지.

    **Fail-fast 정책**: 조합 N 에서 예외가 발생하면 호출자로 전파. 이전 N-1
    성공 결과도 함께 버려진다.

    Args:
        loader: 일봉 스트림 소스 — 조합마다 ``stream`` 을 새로 호출.
        base_config: 그리드로 덮어쓰지 않은 필드의 기본값.
        grid: 축 조합. 빈 axes 는 ``RSIMRSensitivityGrid`` 가 차단.
        start, end: 백테스트 구간 (경계 포함). ``start > end`` → ``RuntimeError``.
        dca_baseline_return_pct: 게이트 2 비교용 DCA same-window 총수익률.
        on_row: 조합 1개 완료마다 호출되는 콜백 (incremental flush 용).
            콜백 예외는 호출자로 전파.

    Returns:
        조합 순서대로 생성된 ``RSIMRSensitivityRow`` 튜플.

    Raises:
        RuntimeError: 알 수 없는 축 이름, ``RSIMRBaselineConfig`` /
            ``RSIMRConfig`` ``__post_init__`` 가드 위반, ``start > end``.
    """
    if start > end:
        raise RuntimeError(
            f"start({start.isoformat()}) 는 end({end.isoformat()}) 이전이어야 합니다."
        )
    return run_rsi_mr_sensitivity_combos(
        loader=loader,
        base_config=base_config,
        combos=list(grid.iter_combinations()),
        start=start,
        end=end,
        dca_baseline_return_pct=dca_baseline_return_pct,
        on_row=on_row,
    )


def run_rsi_mr_sensitivity_combos(
    loader: BarLoader,
    base_config: RSIMRBaselineConfig,
    combos: list[dict[str, Any]],
    start: date,
    end: date,
    *,
    dca_baseline_return_pct: Decimal,
    on_row: Callable[[RSIMRSensitivityRow], None] | None = None,
) -> tuple[RSIMRSensitivityRow, ...]:
    """명시적 조합 리스트에 대한 직렬 실행 — resume 경로용 public API.

    ``run_rsi_mr_sensitivity`` 가 grid 전체를 실행한다면 본 함수는 호출자가
    완료 조합을 skip 하고 미완료 조합만 전달하는 incremental flow 를 지원.
    빈 ``combos`` → 빈 튜플 (resume 시 "이미 전부 완료" 분기).
    """
    rows: list[RSIMRSensitivityRow] = []
    for combo in combos:
        config = _apply_combo(base_config, combo)
        logger.debug("rsi_mr.sensitivity.attempt combo={combo}", combo=combo)
        result = compute_rsi_mr_baseline(loader, config, start, end)
        row = _result_to_row(combo, result, dca_baseline_return_pct)
        rows.append(row)
        if on_row is not None:
            on_row(row)
        logger.info(
            "rsi_mr.sensitivity.done combo={combo} ret={r} mdd={m} sharpe={s} "
            "alpha={a} trades={t} all_gates={g}",
            combo=combo,
            r=row.metrics.total_return_pct,
            m=row.metrics.max_drawdown_pct,
            s=row.metrics.sharpe_ratio,
            a=row.dca_alpha_pct,
            t=row.trade_count,
            g=row.all_gates_pass,
        )
    return tuple(rows)


def run_rsi_mr_sensitivity_parallel(
    loader_factory: Callable[[], BarLoader],
    base_config: RSIMRBaselineConfig,
    grid: RSIMRSensitivityGrid,
    start: date,
    end: date,
    *,
    dca_baseline_return_pct: Decimal,
    max_workers: int | None = None,
    mp_context: BaseContext | None = None,
    on_row: Callable[[RSIMRSensitivityRow], None] | None = None,
) -> tuple[RSIMRSensitivityRow, ...]:
    """ProcessPool 병렬 실행 (ADR-0020 분석 도구 예외).

    ``loader_factory`` 는 워커 안에서 호출되어 새 ``BarLoader`` 를 생성한다 —
    KisMinuteBarLoader / sqlite3 connection 등 pickle 불가 자원 회피. 메인
    프로세스는 ``as_completed`` 로 결과를 받아 ``combo_idx`` 기준 재정렬해
    직렬 경로와 동일 순서를 반환.

    ``on_row`` 는 워커 종료 순서 (비결정적) 로 호출. 결과 tuple 자체는
    ``combos`` 순서로 정렬됨.

    Raises:
        RuntimeError: ``max_workers < 1``, ``start > end``.
    """
    if max_workers is not None and max_workers < 1:
        raise RuntimeError(f"max_workers 는 1 이상이어야 합니다 (got={max_workers})")
    if start > end:
        raise RuntimeError(
            f"start({start.isoformat()}) 는 end({end.isoformat()}) 이전이어야 합니다."
        )
    combos = list(grid.iter_combinations())
    return run_rsi_mr_sensitivity_combos_parallel(
        loader_factory=loader_factory,
        base_config=base_config,
        combos=combos,
        start=start,
        end=end,
        dca_baseline_return_pct=dca_baseline_return_pct,
        max_workers=max_workers,
        mp_context=mp_context,
        on_row=on_row,
    )


def run_rsi_mr_sensitivity_combos_parallel(
    loader_factory: Callable[[], BarLoader],
    base_config: RSIMRBaselineConfig,
    combos: list[dict[str, Any]],
    start: date,
    end: date,
    *,
    dca_baseline_return_pct: Decimal,
    max_workers: int | None = None,
    mp_context: BaseContext | None = None,
    on_row: Callable[[RSIMRSensitivityRow], None] | None = None,
) -> tuple[RSIMRSensitivityRow, ...]:
    if not combos:
        return ()
    results: list[RSIMRSensitivityRow | None] = [None] * len(combos)
    logger.info(
        "rsi_mr.sensitivity.parallel.start combos={c} workers={w}",
        c=len(combos),
        w=max_workers,
    )
    with ProcessPoolExecutor(max_workers=max_workers, mp_context=mp_context) as executor:
        futures = {
            executor.submit(
                _run_single_combo,
                idx,
                combo,
                loader_factory,
                base_config,
                start,
                end,
                dca_baseline_return_pct,
            ): idx
            for idx, combo in enumerate(combos)
        }
        try:
            for future in as_completed(futures):
                idx, row = future.result()
                results[idx] = row
                if on_row is not None:
                    on_row(row)
        except BaseException:
            executor.shutdown(wait=False, cancel_futures=True)
            raise
    if any(r is None for r in results):
        raise RuntimeError(
            "run_rsi_mr_sensitivity_parallel 내부 오류: 결과 누락 (combo idx 무결성 위반)"
        )
    return tuple(r for r in results if r is not None)


def render_markdown_table(
    rows: tuple[RSIMRSensitivityRow, ...],
    sort_by: str = "total_return_pct",
    descending: bool = True,
) -> str:
    """결과 행 튜플을 Markdown 표 문자열로 렌더링."""
    if sort_by not in _SORTABLE_KEYS:
        raise RuntimeError(
            f"sort_by 는 {sorted(_SORTABLE_KEYS)} 중 하나여야 합니다 (got={sort_by!r})"
        )
    if not rows:
        return "_결과 행 0개 — 그리드가 비었거나 실행되지 않았습니다._\n"

    param_keys = _consistent_param_keys(rows)
    metric_keys = _metric_columns()
    extra_keys = ("gate1_pass", "gate2_pass", "gate3_pass", "all_gates_pass")

    sorted_rows = sorted(rows, key=lambda r: _get_value(r, sort_by), reverse=descending)

    header = list(param_keys) + list(metric_keys) + list(extra_keys)
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")
    for row in sorted_rows:
        row_params = dict(row.params)
        cells = (
            [_format_value(row_params[k]) for k in param_keys]
            + [_format_value(_get_value(row, k)) for k in metric_keys]
            + [_format_value(getattr(row, k)) for k in extra_keys]
        )
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_csv(rows: tuple[RSIMRSensitivityRow, ...], path: Path) -> None:
    """결과 행 튜플을 CSV 로 저장."""
    metric_keys = _metric_columns()
    extra_keys = ("gate1_pass", "gate2_pass", "gate3_pass", "all_gates_pass")
    param_keys = _consistent_param_keys(rows) if rows else ()

    header = list(param_keys) + list(metric_keys) + list(extra_keys)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(header)
        for row in rows:
            row_params = dict(row.params)
            writer.writerow(
                [_format_value(row_params[k]) for k in param_keys]
                + [_format_value(_get_value(row, k)) for k in metric_keys]
                + [_format_value(getattr(row, k)) for k in extra_keys]
            )


# 축 이름 → 파서 매핑. CSV resume 경로에서 str → 원형 복원.
_AXIS_PARSERS: dict[str, Callable[[str], Any]] = {
    "rsi_period": int,
    "oversold_threshold": Decimal,
    "overbought_threshold": Decimal,
    "stop_loss_pct": Decimal,
    "max_positions": int,
    "position_pct": Decimal,
}

# 메트릭/보조 필드 → int 파싱 대상.
_INT_FIELDS: frozenset[str] = frozenset(
    {
        "net_pnl_krw",
        "trade_count",
    }
)


def load_completed_combos(
    path: Path,
    grid: RSIMRSensitivityGrid,
) -> set[tuple[tuple[str, Any], ...]]:
    """기존 sensitivity CSV 에서 완료 조합 params key set 반환."""
    if not path.exists():
        raise FileNotFoundError(str(path))
    axis_names = tuple(ax.name for ax in grid.axes)
    with path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.reader(fp)
        try:
            header = next(reader)
        except StopIteration:
            return set()
        data_rows = list(reader)
        if not data_rows:
            return set()
        missing = [name for name in axis_names if name not in header]
        if missing:
            raise RuntimeError(f"CSV 헤더에 축 {missing!r} 가 없습니다 (header={header})")
        unknown = [name for name in axis_names if name not in _AXIS_PARSERS]
        if unknown:
            raise RuntimeError(f"파싱 규칙 없는 축: {unknown!r}")
        col_idx = {name: header.index(name) for name in axis_names}
        completed: set[tuple[tuple[str, Any], ...]] = set()
        for row in data_rows:
            key_items: list[tuple[str, Any]] = []
            for name in axis_names:
                raw = row[col_idx[name]]
                key_items.append((name, _AXIS_PARSERS[name](raw)))
            completed.add(tuple(key_items))
        return completed


def filter_remaining_combos(
    grid: RSIMRSensitivityGrid,
    completed: set[tuple[tuple[str, Any], ...]],
) -> list[dict[str, Any]]:
    """grid 에서 completed 에 없는 조합만 dict 리스트로 반환."""
    remaining: list[dict[str, Any]] = []
    for combo in grid.iter_combinations():
        key = tuple(combo.items())
        if key not in completed:
            remaining.append(combo)
    return remaining


def load_sensitivity_rows(
    path: Path,
    grid: RSIMRSensitivityGrid,
) -> tuple[RSIMRSensitivityRow, ...]:
    """기존 CSV 를 파싱해 ``RSIMRSensitivityRow`` 튜플 복원 — resume 렌더용."""
    if not path.exists():
        raise FileNotFoundError(str(path))
    axis_names = tuple(ax.name for ax in grid.axes)
    metric_names = _metric_columns()
    extra_names = ("gate1_pass", "gate2_pass", "gate3_pass", "all_gates_pass")
    with path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.reader(fp)
        try:
            header = next(reader)
        except StopIteration:
            return ()
        data_rows = list(reader)
        if not data_rows:
            return ()
        missing_axes = [name for name in axis_names if name not in header]
        if missing_axes:
            raise RuntimeError(f"CSV 헤더에 축 {missing_axes!r} 가 없습니다 (header={header})")
        missing_metrics = [name for name in metric_names if name not in header]
        if missing_metrics:
            raise RuntimeError(
                f"CSV 헤더에 메트릭 {missing_metrics!r} 가 없습니다 (header={header})"
            )
        missing_extras = [name for name in extra_names if name not in header]
        if missing_extras:
            raise RuntimeError(
                f"CSV 헤더에 보조 컬럼 {missing_extras!r} 가 없습니다 (header={header})"
            )
        unknown = [name for name in axis_names if name not in _AXIS_PARSERS]
        if unknown:
            raise RuntimeError(f"파싱 규칙 없는 축: {unknown!r}")
        axis_col = {name: header.index(name) for name in axis_names}
        metric_col = {name: header.index(name) for name in metric_names}
        extra_col = {name: header.index(name) for name in extra_names}
        rows: list[RSIMRSensitivityRow] = []
        for row in data_rows:
            params = tuple((name, _AXIS_PARSERS[name](row[axis_col[name]])) for name in axis_names)
            metric_values = {
                name: _parse_metric_value(name, row[metric_col[name]]) for name in metric_names
            }
            extra_values = {name: _parse_bool(row[extra_col[name]]) for name in extra_names}
            metrics = BacktestMetrics(
                total_return_pct=metric_values["total_return_pct"],
                max_drawdown_pct=metric_values["max_drawdown_pct"],
                sharpe_ratio=metric_values["sharpe_ratio"],
                win_rate=metric_values["win_rate"],
                avg_pnl_ratio=metric_values["avg_pnl_ratio"],
                trades_per_day=metric_values["trades_per_day"],
                net_pnl_krw=metric_values["net_pnl_krw"],
            )
            rows.append(
                RSIMRSensitivityRow(
                    params=params,
                    metrics=metrics,
                    trade_count=metric_values["trade_count"],
                    dca_alpha_pct=metric_values["dca_alpha_pct"],
                    gate1_pass=extra_values["gate1_pass"],
                    gate2_pass=extra_values["gate2_pass"],
                    gate3_pass=extra_values["gate3_pass"],
                    all_gates_pass=extra_values["all_gates_pass"],
                )
            )
        return tuple(rows)


def merge_sensitivity_rows(
    existing: tuple[RSIMRSensitivityRow, ...],
    new: tuple[RSIMRSensitivityRow, ...],
    grid: RSIMRSensitivityGrid,
) -> tuple[RSIMRSensitivityRow, ...]:
    """existing·new 를 grid 순서로 병합. 동일 params → new 우선."""
    by_key: dict[tuple[tuple[str, Any], ...], RSIMRSensitivityRow] = {}
    for row in existing:
        by_key[row.params] = row
    for row in new:
        by_key[row.params] = row
    result: list[RSIMRSensitivityRow] = []
    missing: list[tuple[tuple[str, Any], ...]] = []
    for combo in grid.iter_combinations():
        key = tuple(combo.items())
        if key in by_key:
            result.append(by_key[key])
        else:
            missing.append(key)
    if missing:
        raise RuntimeError(f"병합 후 누락 조합: {len(missing)} 개 — {missing}")
    return tuple(result)


def append_sensitivity_row(
    row: RSIMRSensitivityRow,
    path: Path,
    grid: RSIMRSensitivityGrid,
) -> None:
    """조합 1개 결과를 CSV 에 atomic append (Issue #82 동일 기조)."""
    existing_rows: tuple[RSIMRSensitivityRow, ...] = ()
    if path.exists():
        existing_rows = load_sensitivity_rows(path, grid)
    merged = existing_rows + (row,)
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        write_csv(merged, tmp_path)
        os.replace(str(tmp_path), str(path))
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------


def _apply_combo(base: RSIMRBaselineConfig, combo: dict[str, Any]) -> RSIMRBaselineConfig:
    """``RSIMRBaselineConfig`` 의 5축 필드를 ``combo`` 로 갱신.

    Raises:
        RuntimeError: ``combo`` 키가 ``_TUNABLE_FIELDS`` 외부.
    """
    updates: dict[str, Any] = {}
    for name, value in combo.items():
        if name not in _TUNABLE_FIELDS:
            raise RuntimeError(
                f"RSI MR 그리드 축 이름은 {sorted(_TUNABLE_FIELDS)} 로 제한됩니다 (got={name!r})"
            )
        updates[name] = value
    return dataclasses.replace(base, **updates)


def _result_to_row(
    combo: dict[str, Any],
    result: BacktestResult,
    dca_baseline_return_pct: Decimal,
) -> RSIMRSensitivityRow:
    """``BacktestResult`` + 게이트 임계값 → ``RSIMRSensitivityRow``."""
    metrics = result.metrics
    dca_alpha = metrics.total_return_pct - dca_baseline_return_pct
    gate1 = metrics.max_drawdown_pct > _GATE1_MDD_THRESHOLD
    gate2 = dca_alpha > _GATE2_ALPHA_THRESHOLD
    gate3 = metrics.sharpe_ratio > _GATE3_SHARPE_THRESHOLD
    return RSIMRSensitivityRow(
        params=tuple(combo.items()),
        metrics=metrics,
        trade_count=len(result.trades),
        dca_alpha_pct=dca_alpha,
        gate1_pass=gate1,
        gate2_pass=gate2,
        gate3_pass=gate3,
        all_gates_pass=gate1 and gate2 and gate3,
    )


def _run_single_combo(
    combo_idx: int,
    combo: dict[str, Any],
    loader_factory: Callable[[], BarLoader],
    base_config: RSIMRBaselineConfig,
    start: date,
    end: date,
    dca_baseline_return_pct: Decimal,
) -> tuple[int, RSIMRSensitivityRow]:
    """ProcessPool 워커 진입점 — 단일 조합 실행 후 ``(idx, row)`` 반환."""
    loader = loader_factory()
    try:
        config = _apply_combo(base_config, combo)
        logger.debug(
            "rsi_mr.sensitivity.parallel.attempt idx={i} combo={c}",
            i=combo_idx,
            c=combo,
        )
        result = compute_rsi_mr_baseline(loader, config, start, end)
        row = _result_to_row(combo, result, dca_baseline_return_pct)
        logger.info(
            "rsi_mr.sensitivity.parallel.combo_done idx={i} combo={c} ret={r} mdd={m} "
            "sharpe={s} alpha={a} trades={t} all_gates={g}",
            i=combo_idx,
            c=combo,
            r=row.metrics.total_return_pct,
            m=row.metrics.max_drawdown_pct,
            s=row.metrics.sharpe_ratio,
            a=row.dca_alpha_pct,
            t=row.trade_count,
            g=row.all_gates_pass,
        )
        return combo_idx, row
    finally:
        close = getattr(loader, "close", None)
        if callable(close):
            close()


def _metric_columns() -> tuple[str, ...]:
    """렌더러 공통 메트릭 컬럼 순서.

    ``BacktestMetrics`` 7 필드 + 보조 2 (``trade_count``, ``dca_alpha_pct``).
    """
    return (
        "total_return_pct",
        "max_drawdown_pct",
        "sharpe_ratio",
        "win_rate",
        "avg_pnl_ratio",
        "trades_per_day",
        "net_pnl_krw",
        "trade_count",
        "dca_alpha_pct",
    )


_METRICS_FIELDS_ON_ROW: frozenset[str] = frozenset(
    {
        "trade_count",
        "dca_alpha_pct",
    }
)


def _get_value(row: RSIMRSensitivityRow, key: str) -> Any:
    """``key`` 가 BacktestMetrics 필드면 ``row.metrics.xxx``, 보조 필드면 ``row.xxx``."""
    if key in _METRICS_FIELDS_ON_ROW:
        return getattr(row, key)
    return getattr(row.metrics, key)


def _parse_metric_value(name: str, raw: str) -> Any:
    """메트릭/보조 셀 파싱."""
    if name in _INT_FIELDS:
        return int(raw)
    return Decimal(raw)


def _parse_bool(raw: str) -> bool:
    """gate*_pass / all_gates_pass 셀 파싱."""
    s = raw.strip().lower()
    if s in {"true", "1"}:
        return True
    if s in {"false", "0"}:
        return False
    raise RuntimeError(f"bool 파싱 실패: {raw!r}")


def _consistent_param_keys(rows: tuple[RSIMRSensitivityRow, ...]) -> tuple[str, ...]:
    """모든 row 의 params 축 이름 집합이 동일한지 확인하고 첫 row 의 순서 반환."""
    if not rows:
        return ()
    first_keys = tuple(name for name, _ in rows[0].params)
    first_set = set(first_keys)
    for row in rows[1:]:
        other = {name for name, _ in row.params}
        if other != first_set:
            raise RuntimeError(
                "RSIMRSensitivityRow 들의 params 축 이름 집합이 일치하지 않습니다 — "
                f"first={sorted(first_set)}, other={sorted(other)}"
            )
    return first_keys


def _format_value(value: Any) -> str:
    """축/메트릭 값 표기 — Decimal·bool·int·기타 통일."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return f"{value:.6f}"
    if isinstance(value, int):
        return f"{value:d}"
    return str(value)


__all__ = [
    "RSIMRParameterAxis",
    "RSIMRSensitivityGrid",
    "RSIMRSensitivityRow",
    "append_sensitivity_row",
    "filter_remaining_combos",
    "load_completed_combos",
    "load_sensitivity_rows",
    "merge_sensitivity_rows",
    "render_markdown_table",
    "run_rsi_mr_sensitivity",
    "run_rsi_mr_sensitivity_combos",
    "run_rsi_mr_sensitivity_combos_parallel",
    "run_rsi_mr_sensitivity_parallel",
    "step_f_rsi_mr_grid",
    "write_csv",
]
