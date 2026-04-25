"""백테스트 파라미터 민감도 그리드 실행.

책임 범위
- `StrategyConfig`/`RiskConfig`/`BacktestConfig` 의 일부 필드를 축으로 삼아
  Cartesian product 조합을 생성하고, 각 조합으로 `BacktestEngine.run()` 을
  반복 실행해 메트릭을 수집한다.
- 결과를 Markdown 표 / CSV 로 렌더링해 운영자가 파라미터 선정 근거를 얻게 한다.
- 민감도 리포트는 **sanity check** 용도 — "현재 기본값이 로버스트한지" 를 보는
  도구이지 과적합 허가가 아니다. 최종 파라미터 교체는 Walk-forward 검증 후에만
  (plan.md 위험 테이블 "백테스트 과적합" 기조).

실행 경로 2종
- `run_sensitivity` — 단일 프로세스 직렬 실행. 회귀 안전망·소형 그리드용.
- `run_sensitivity_parallel` — `ProcessPoolExecutor` 기반 병렬 (ADR-0020).
  ADR-0008 단일 프로세스 정책의 명시적 예외 (분석 도구 범위). 결과 순서·
  fail-fast 계약은 직렬 경로와 동일.

범위 제외 (의도적 defer — 후속 PR / Phase 5)
- HTML/Jupyter 노트북 렌더러 (Phase 5 후보 — backtest/CLAUDE.md 참조).
- Walk-forward 검증 (Phase 5).
- YAML 기반 축 외부화 — 코드 상수 기조 (YAGNI).

설계 원칙 (`backtest/engine.py` 와 동일 기조)
- 외부 I/O = CSV 쓰기 경로 1개만. Markdown 은 문자열 반환.
- 결정론 — 그리드 순회는 축 선언 순서 · 각 축 후보값 선언 순서 고정.
  병렬 경로도 `combo_idx` 기반 재정렬로 동일 순서 유지.
- generic `except Exception` 금지. 사용자 입력 오류는 `RuntimeError` 전파.
- `@dataclass(frozen=True, slots=True)` 로 DTO 불변화.

파라미터 이름 공간
- `strategy.<field>` — `StrategyConfig` 필드 (`stop_loss_pct`, `take_profit_pct`,
  `or_start`, `or_end`, `force_close_at`).
- `risk.<field>` — `RiskConfig` 필드 (`position_pct`, `max_positions`,
  `daily_max_entries`, `min_notional_krw`, `daily_loss_limit_pct`).
- `engine.<field>` — `BacktestConfig` 필드 (`slippage_rate`, `commission_rate`,
  `sell_tax_rate`). `starting_capital_krw` 은 그리드 대상 아님 (비교 의미 없음).

알 수 없는 prefix 또는 필드명은 `RuntimeError`.
"""

from __future__ import annotations

import contextlib
import csv
import dataclasses
import os
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, time
from decimal import Decimal
from multiprocessing.context import BaseContext
from pathlib import Path
from typing import Any

from loguru import logger

from stock_agent.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestMetrics,
    BacktestResult,
)
from stock_agent.backtest.loader import BarLoader
from stock_agent.risk import RiskConfig
from stock_agent.strategy import StrategyConfig

# 그리드 대상 config 3종 — prefix 로 라우팅.
_STRATEGY_PREFIX = "strategy"
_RISK_PREFIX = "risk"
_ENGINE_PREFIX = "engine"
_ALLOWED_PREFIXES = frozenset({_STRATEGY_PREFIX, _RISK_PREFIX, _ENGINE_PREFIX})

# engine.starting_capital_krw 는 비교 의미가 없어 그리드에서 제외.
_ENGINE_TUNABLE_FIELDS = frozenset({"commission_rate", "sell_tax_rate", "slippage_rate"})

# render_markdown_table / write_csv 의 sort_by 허용 키 — BacktestMetrics 필드명.
_SORTABLE_METRIC_KEYS = frozenset(
    {
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
    }
)


@dataclass(frozen=True, slots=True)
class ParameterAxis:
    """민감도 그리드의 단일 축 — `name` 과 `values` 후보 리스트.

    `name` 은 `prefix.field` 형태 (예: `"strategy.stop_loss_pct"`). prefix 는
    `strategy`/`risk`/`engine` 중 하나. field 는 해당 config 의 실제 필드명.

    `values` 는 비어있을 수 없고 `==` 기준 중복도 허용하지 않는다 (그리드 크기가
    예측 가능해야 함). 값의 **타입** 은 호출자 책임 — `dataclasses.replace` 는
    런타임 타입 체크를 하지 않는다. 대신 범위·순서 검증(예:
    `stop_loss_pct > 0`, `or_start < or_end`) 은 `run_sensitivity` 실행 시점에
    `StrategyConfig.__post_init__` / `RiskConfig.__post_init__` /
    `BacktestConfig.__post_init__` 이 수행한다.

    Raises:
        RuntimeError: `name` 이 빈 문자열 · prefix 불명 · field 공란,
            또는 `values` 가 비어있거나 중복 포함.
    """

    name: str
    values: tuple[Any, ...]

    def __post_init__(self) -> None:
        if not self.name or "." not in self.name:
            raise RuntimeError(
                f"ParameterAxis.name 은 'prefix.field' 형태여야 합니다 (got={self.name!r})"
            )
        prefix, _, field_name = self.name.partition(".")
        if prefix not in _ALLOWED_PREFIXES:
            raise RuntimeError(
                f"ParameterAxis.name 의 prefix 는 {sorted(_ALLOWED_PREFIXES)} 중 하나여야 "
                f"합니다 (got={prefix!r})"
            )
        if not field_name:
            raise RuntimeError(f"ParameterAxis.name 의 field 가 비어있습니다 (got={self.name!r})")
        if not self.values:
            raise RuntimeError(f"ParameterAxis.values 는 1개 이상이어야 합니다 (name={self.name})")
        # 중복 검출 — 축 후보값은 hashable 이어야 한다 (현 그리드는 Decimal·int·time 만 사용).
        # unhashable 이 섞이면 TypeError 가 호출자에게 즉시 전파된다 (silent fallback 없음).
        if len(frozenset(self.values)) != len(self.values):
            seen: set[Any] = set()
            for v in self.values:
                if v in seen:
                    raise RuntimeError(
                        "ParameterAxis.values 에 중복 값이 있습니다 "
                        f"(name={self.name}, value={v!r})"
                    )
                seen.add(v)


@dataclass(frozen=True, slots=True)
class SensitivityGrid:
    """축 목록을 Cartesian product 로 조합하는 그리드.

    축 순서 = 조합 dict 의 키 삽입 순서 → 결정론적 순회.

    Raises:
        RuntimeError: `axes` 가 빈 튜플이거나, 축 이름이 중복될 때 (같은 필드를
            두 축에서 변주하는 설계 실수).
    """

    axes: tuple[ParameterAxis, ...]

    def __post_init__(self) -> None:
        if not self.axes:
            raise RuntimeError("SensitivityGrid.axes 는 1개 이상이어야 합니다")
        names = [axis.name for axis in self.axes]
        if len(set(names)) != len(names):
            dupes = sorted({n for n in names if names.count(n) > 1})
            raise RuntimeError(f"SensitivityGrid.axes 에 중복된 이름이 있습니다: {dupes}")

    def iter_combinations(self) -> Iterator[dict[str, Any]]:
        """각 조합을 `{name: value}` dict 로 yield. 축 선언 순서 고정."""
        # 재귀 대신 스택 기반 — 축 개수에 상관없이 결정론적.
        indices = [0] * len(self.axes)
        sizes = [len(axis.values) for axis in self.axes]
        while True:
            pairs = zip(self.axes, indices, strict=True)
            yield {axis.name: axis.values[idx] for axis, idx in pairs}
            # 마지막 축부터 증가 (가장 안쪽 루프가 가장 빠르게 회전).
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
        """조합 총 개수."""
        total = 1
        for axis in self.axes:
            total *= len(axis.values)
        return total


@dataclass(frozen=True, slots=True)
class SensitivityRow:
    """민감도 그리드 1 조합의 실행 결과 스냅샷.

    `params` 는 `(축 이름, 적용된 값)` 의 순서 있는 튜플. 축 선언 순서가
    `iter_combinations` 의 yield 순서와 일치한다. **튜플을 쓰는 이유**: frozen
    dataclass 에서 `dict` 필드는 내부 변이가 가능해 실질적 불변성이 깨진다
    (`BacktestResult.rejected_counts` 는 이 한계를 docstring 으로 고지). 여기선
    외부가 `row.params` 를 변이시켜 renderer 의 두 번째 소비 결과를 바꾸는
    실수를 원천 차단한다.

    `metrics` 는 `BacktestMetrics` 를 그대로 재사용 (엔진 진화 자동 추종). 조합
    비교에 유용한 보조 지표 3종은 별도 필드:

    - `trade_count`: `BacktestResult.trades` 길이.
    - `rejected_total`: `BacktestResult.rejected_counts` 값의 합 (RiskManager
      사전 거부 6종 합산).
    - `post_slippage_rejections`: 엔진 사후 슬리피지 거부 횟수.

    소비자는 `row.metrics.total_return_pct` 처럼 접근. 평면 키(`sort_by`) 는
    렌더러가 `metrics` + 보조 3종을 합쳐 동일하게 노출한다.

    Raises:
        RuntimeError: `params` 튜플에 중복된 축 이름이 포함될 때 (그리드
            상태 무결성 위반).
    """

    params: tuple[tuple[str, Any], ...]
    metrics: BacktestMetrics
    trade_count: int
    rejected_total: int
    post_slippage_rejections: int

    def __post_init__(self) -> None:
        names = [name for name, _ in self.params]
        if len(set(names)) != len(names):
            raise RuntimeError(
                f"SensitivityRow.params 에 중복된 축 이름이 있습니다: {sorted(set(names))}"
            )

    def params_dict(self) -> dict[str, Any]:
        """소비자 편의용 dict 복사본. 원본 튜플은 불변 유지."""
        return dict(self.params)


def run_sensitivity(
    loader: BarLoader,
    start: date,
    end: date,
    symbols: tuple[str, ...],
    base_config: BacktestConfig,
    grid: SensitivityGrid,
) -> tuple[SensitivityRow, ...]:
    """그리드의 각 조합으로 `BacktestEngine.run()` 을 반복 실행해 결과 수집.

    동일 `loader` 를 조합마다 재호출 (`loader.stream(start, end, symbols)`).
    `BarLoader` Protocol 이 "호출마다 새 Iterable 을 반환" 하는 재호출 안전성을
    명시 계약으로 요구한다 (`loader.py` docstring 참조) — 이 계약을 어기는
    구현을 주입하면 두 번째 조합부터 빈 스트림을 받게 되어 silent 하게 모든
    메트릭이 0 이 나온다.

    **Fail-fast 정책**: 조합 N 에서 `BacktestEngine.run()` 예외가 발생하면
    이전 N-1 성공 결과도 함께 버려지고 예외가 호출자로 전파된다. 중간 실패
    지점을 추적하기 쉽도록 `engine.run()` 호출 직전에 `logger.debug` 로 combo
    를 남긴다 — traceback 과 함께 실패 조합을 즉시 식별할 수 있다.

    Args:
        loader: 분봉 스트림 소스 — 조합마다 `stream` 을 새로 호출한다.
        start: 구간 시작 (경계 포함).
        end: 구간 종료 (경계 포함).
        symbols: 대상 종목 코드 튜플 (1개 이상).
        base_config: 그리드로 덮어쓰지 않은 필드의 기본값을 담은 `BacktestConfig`.
            `starting_capital_krw` · 나머지 비용/전략/리스크 설정이 여기서 출발점.
        grid: 축 조합 — `SensitivityGrid` 가 축 1개 이상을 강제하므로 빈 결과는
            나오지 않는다 (`axes=()` 이면 `SensitivityGrid.__post_init__` 에서
            차단).

    Returns:
        조합 순서대로 생성된 `SensitivityRow` 튜플 (결정론).

    Raises:
        RuntimeError: 알 수 없는 prefix/필드명 등 config 생성 실패. 또는 config
            `__post_init__` 의 범위·순서 검증 위반.
    """
    return run_sensitivity_combos(
        loader=loader,
        start=start,
        end=end,
        symbols=symbols,
        base_config=base_config,
        combos=list(grid.iter_combinations()),
    )


def run_sensitivity_combos(
    loader: BarLoader,
    start: date,
    end: date,
    symbols: tuple[str, ...],
    base_config: BacktestConfig,
    combos: list[dict[str, Any]],
    *,
    on_row: Callable[[SensitivityRow], None] | None = None,
) -> tuple[SensitivityRow, ...]:
    """명시적 조합 리스트에 대한 직렬 실행 — resume 경로용.

    `run_sensitivity` 와 동일하지만 `grid.iter_combinations()` 대신 주어진
    `combos` 를 그대로 순회한다. resume 플로우에서 미완료 조합만 실행하는
    CLI 경로가 이 API 를 쓴다.

    빈 `combos` 는 빈 튜플을 반환한다 (resume 플로우 상 "이미 전부 완료"
    상태를 나타내는 유효 입력).

    Args / Raises 계약은 `run_sensitivity` 와 동일. combos 순서가 반환 rows
    순서와 1:1 대응한다.

    `on_row` (keyword-only): 조합 1개 완료 시점마다 메인 프로세스에서 호출되는
    콜백. 디스크 incremental flush 등 부수효과 주입용 (Issue #82). 콜백 예외는
    그대로 호출자에게 전파 — fail-fast.
    """
    rows: list[SensitivityRow] = []
    for combo in combos:
        config = _apply_combo(base_config, combo)
        engine = BacktestEngine(config)
        logger.debug("sensitivity.run.attempt combo={combo}", combo=combo)
        result = engine.run(loader.stream(start, end, symbols))
        row = _result_to_row(combo, result)
        rows.append(row)
        if on_row is not None:
            on_row(row)
        logger.info(
            "sensitivity.run combo={combo} net_pnl={p} trades={t}",
            combo=combo,
            p=result.metrics.net_pnl_krw,
            t=len(result.trades),
        )
    return tuple(rows)


def run_sensitivity_parallel(
    loader_factory: Callable[[], BarLoader],
    start: date,
    end: date,
    symbols: tuple[str, ...],
    base_config: BacktestConfig,
    grid: SensitivityGrid,
    *,
    max_workers: int | None = None,
    mp_context: BaseContext | None = None,
) -> tuple[SensitivityRow, ...]:
    """`run_sensitivity` 의 ProcessPool 병렬 실행 경로 (ADR-0020).

    각 조합을 별도 워커 프로세스에서 `BacktestEngine.run()` 으로 실행한 뒤
    결과를 `combo_idx` 로 재정렬해 직렬 경로와 동일 순서를 보장한다. 한
    워커가 예외를 발생시키면 즉시 잔여 future 를 취소하고 호출자로 예외를
    전파한다 (직렬 fail-fast 계약 동일).

    `loader` 인스턴스 대신 `loader_factory` 를 받는 이유: `KisMinuteBarLoader`
    의 PyKis 세션 / `requests.Session` / `sqlite3.Connection` 은 pickle 불가
    능하다. 워커는 자기 프로세스 안에서 팩토리를 호출해 새 loader 를 만든다.
    팩토리는 pickleable 해야 한다 (모듈 top-level 함수 또는
    `functools.partial`).

    Args:
        loader_factory: 워커 프로세스 안에서 호출해 새 `BarLoader` 를 생성하
            는 callable. pickle 가능해야 한다.
        start: 구간 시작 (경계 포함).
        end: 구간 종료 (경계 포함).
        symbols: 대상 종목 코드 튜플 (1개 이상).
        base_config: 그리드로 덮어쓰지 않은 필드의 기본값을 담은 `BacktestConfig`.
        grid: 축 조합. `SensitivityGrid` 가 축 1개 이상을 강제하므로 빈 결과는
            발생하지 않는다.
        max_workers: 동시 워커 수. `None` 이면 `ProcessPoolExecutor` 기본값.
            `< 1` → `RuntimeError`.
        mp_context: `multiprocessing` 컨텍스트. `None` 이면 PPE 기본값(macOS·
            Linux Python 3.12 → `spawn`). 테스트에서 `fork` 를 주입하면 워커
            가 부모 모듈 상태를 그대로 상속해 spawn 의 import 비용을 회피.

    Returns:
        조합 순서대로 생성된 `SensitivityRow` 튜플 (`grid.iter_combinations()`
        순서와 동일 — 결정론).

    Raises:
        RuntimeError: `max_workers < 1`.
        Exception: 워커 안에서 발생한 첫 예외 (`BacktestEngine.run()` 의
            `RuntimeError` 또는 `loader_factory` / `loader.stream` 의 예외).
            발생 시 잔여 future 는 `cancel_futures=True` 로 즉시 취소된다.
    """
    if max_workers is not None and max_workers < 1:
        raise RuntimeError(f"max_workers 는 1 이상이어야 합니다 (got={max_workers})")
    return run_sensitivity_combos_parallel(
        loader_factory=loader_factory,
        start=start,
        end=end,
        symbols=symbols,
        base_config=base_config,
        combos=list(grid.iter_combinations()),
        max_workers=max_workers,
        mp_context=mp_context,
    )


def run_sensitivity_combos_parallel(
    loader_factory: Callable[[], BarLoader],
    start: date,
    end: date,
    symbols: tuple[str, ...],
    base_config: BacktestConfig,
    combos: list[dict[str, Any]],
    *,
    max_workers: int | None = None,
    mp_context: BaseContext | None = None,
    on_row: Callable[[SensitivityRow], None] | None = None,
) -> tuple[SensitivityRow, ...]:
    """명시적 조합 리스트에 대한 병렬 실행 — resume 경로용.

    `run_sensitivity_parallel` 와 동일하지만 `grid.iter_combinations()` 대신
    주어진 `combos` 를 그대로 워커에 분산한다. 결과는 `combo_idx` 기준으로
    정렬되어 입력 `combos` 순서와 1:1 대응한다.

    빈 `combos` 는 빈 튜플을 반환한다 — ProcessPool 은 생성하지 않는다
    (resume 플로우 상 "이미 전부 완료" 상태 단락).

    `on_row` (keyword-only): `as_completed` 시점에 메인 프로세스에서 호출되는
    콜백 (Issue #82). 호출 순서는 워커 종료 순서이므로 비결정적. 워커 자체
    는 단순 결과 반환만 — 콜백을 워커로 보내지 않으므로 pickle 제약 없음.
    콜백 예외는 그대로 전파되며 잔여 future 는 `cancel_futures=True` 로 취소.

    Raises:
        RuntimeError: `max_workers < 1`.
        Exception: 워커 안에서 발생한 첫 예외 또는 `on_row` 콜백 예외. 잔여
            future 는 즉시 취소.
    """
    if max_workers is not None and max_workers < 1:
        raise RuntimeError(f"max_workers 는 1 이상이어야 합니다 (got={max_workers})")
    if not combos:
        return ()
    results: list[SensitivityRow | None] = [None] * len(combos)
    logger.info(
        "sensitivity.parallel.start combos={c} workers={w}",
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
                start,
                end,
                symbols,
                base_config,
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
            "run_sensitivity_parallel 내부 오류: 결과 누락 (combo idx 순서 무결성 위반)"
        )
    return tuple(r for r in results if r is not None)


def render_markdown_table(
    rows: tuple[SensitivityRow, ...],
    sort_by: str = "total_return_pct",
    descending: bool = True,
) -> str:
    """결과 행 튜플을 Markdown 표 문자열로 렌더링.

    컬럼: 축 이름 (입력 순서) + 메트릭 10종 (`BacktestMetrics` 7 + 보조 3).

    Args:
        rows: `run_sensitivity` 반환값.
        sort_by: 정렬 기준 메트릭 키. `_SORTABLE_METRIC_KEYS` 중 하나.
        descending: True 면 내림차순, False 면 오름차순.

    Raises:
        RuntimeError: `sort_by` 가 허용 키가 아니거나, `rows` 간 `params` 키
            집합이 일치하지 않을 때.
    """
    if sort_by not in _SORTABLE_METRIC_KEYS:
        raise RuntimeError(
            f"sort_by 는 {sorted(_SORTABLE_METRIC_KEYS)} 중 하나여야 합니다 (got={sort_by!r})"
        )
    if not rows:
        return "_결과 행 0개 — 그리드가 비었거나 실행되지 않았습니다._\n"

    param_keys = _consistent_param_keys(rows)
    metric_keys = _metric_columns()

    sorted_rows = sorted(rows, key=lambda r: _get_metric_value(r, sort_by), reverse=descending)

    header = list(param_keys) + list(metric_keys)
    lines = ["| " + " | ".join(header) + " |"]
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")
    for row in sorted_rows:
        row_params = dict(row.params)
        cells = [_format_param(row_params[k]) for k in param_keys] + [
            _format_metric(k, _get_metric_value(row, k)) for k in metric_keys
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_csv(rows: tuple[SensitivityRow, ...], path: Path) -> None:
    """결과 행 튜플을 CSV 로 저장. stdlib `csv.writer` 만 사용.

    축 이름 · 메트릭 키 10종을 플랫 컬럼으로 펼친다. `rows` 가 비어있으면
    헤더만 쓴다 (빈 CSV 도 스크립트 처리 흐름에서 유효한 산출물).

    Raises:
        RuntimeError: `rows` 간 `params` 키 집합이 일치하지 않을 때.
        OSError: 파일 쓰기 실패.
    """
    metric_keys = _metric_columns()
    param_keys = _consistent_param_keys(rows) if rows else ()

    header = list(param_keys) + list(metric_keys)
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.writer(fp)
        writer.writerow(header)
        for row in rows:
            row_params = dict(row.params)
            writer.writerow(
                [_format_param(row_params[k]) for k in param_keys]
                + [_format_metric(k, _get_metric_value(row, k)) for k in metric_keys]
            )


# ---------------------------------------------------------------------------
# Resume 지원 — 기존 CSV 읽어 완료 조합 skip + 병합
# ---------------------------------------------------------------------------


# 축 이름 → 파서 매핑. `write_csv` 의 `_format_param` 은 `str(value)` 로 통일
# 하므로 역방향 파싱은 축별로 명시 타입 복원. 새 축을 `default_grid` 또는
# 외부 그리드에 추가할 때 여기에도 파싱 규칙을 추가해야 한다 (미등록 축은
# `RuntimeError` fail-fast — silent str fallback 금지).
_AXIS_PARSERS: dict[str, Callable[[str], Any]] = {
    "strategy.or_start": time.fromisoformat,
    "strategy.or_end": time.fromisoformat,
    "strategy.force_close_at": time.fromisoformat,
    "strategy.stop_loss_pct": Decimal,
    "strategy.take_profit_pct": Decimal,
    "risk.position_pct": Decimal,
    "risk.daily_loss_limit_pct": Decimal,
    "risk.max_positions": int,
    "risk.daily_max_entries": int,
    "risk.min_notional_krw": int,
    "engine.slippage_rate": Decimal,
    "engine.commission_rate": Decimal,
    "engine.sell_tax_rate": Decimal,
}


def load_sensitivity_rows(path: Path, grid: SensitivityGrid) -> tuple[SensitivityRow, ...]:
    """기존 sensitivity CSV 를 파싱해 `SensitivityRow` 튜플로 복원.

    `load_completed_combos` 가 params key 만 반환하는 것과 달리 이 함수는
    메트릭 10종까지 복원해 `merge_sensitivity_rows` 입력으로 쓸 수 있다.
    resume 플로우에서 기존 row 를 다시 `render_markdown_table` · `write_csv`
    로 렌더링하기 위해 필수.

    파싱 규칙:
    - 축 값 — `_AXIS_PARSERS` (load_completed_combos 와 동일)
    - 메트릭 — `BacktestMetrics` 7 필드 + 보조 3 필드. Decimal vs int 구분은
      `_parse_metric_value` 가 필드명 기반으로 결정.

    빈 CSV (데이터 0행) 는 빈 튜플. 파일 자체가 없으면 `FileNotFoundError`.

    Raises:
        FileNotFoundError: `path` 가 존재하지 않을 때.
        RuntimeError: 헤더에 축/메트릭 이름 누락, 파싱 규칙 없는 축, 또는
            행 길이가 헤더와 다를 때.
    """
    if not path.exists():
        raise FileNotFoundError(str(path))
    axis_names = tuple(ax.name for ax in grid.axes)
    metric_names = _metric_columns()
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
        unknown_axes = [name for name in axis_names if name not in _AXIS_PARSERS]
        if unknown_axes:
            raise RuntimeError(f"파싱 규칙 없는 축: {unknown_axes!r}")
        axis_col = {name: header.index(name) for name in axis_names}
        metric_col = {name: header.index(name) for name in metric_names}
        rows: list[SensitivityRow] = []
        for row in data_rows:
            if len(row) < len(header):
                raise RuntimeError(f"CSV 행 길이가 헤더보다 짧습니다 (row={row}, header={header})")
            params = tuple((name, _AXIS_PARSERS[name](row[axis_col[name]])) for name in axis_names)
            metric_values = {
                name: _parse_metric_value(name, row[metric_col[name]]) for name in metric_names
            }
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
                SensitivityRow(
                    params=params,
                    metrics=metrics,
                    trade_count=metric_values["trade_count"],
                    rejected_total=metric_values["rejected_total"],
                    post_slippage_rejections=metric_values["post_slippage_rejections"],
                )
            )
        return tuple(rows)


# 메트릭 필드 → int 로 파싱할 이름 집합. 나머지는 Decimal.
_INT_METRIC_FIELDS: frozenset[str] = frozenset(
    {
        "net_pnl_krw",
        "trade_count",
        "rejected_total",
        "post_slippage_rejections",
    }
)


def _parse_metric_value(name: str, raw: str) -> Any:
    """메트릭 필드 1개의 CSV 셀 파싱. 필드명 기반 int vs Decimal 분기."""
    if name in _INT_METRIC_FIELDS:
        return int(raw)
    return Decimal(raw)


def load_completed_combos(path: Path, grid: SensitivityGrid) -> set[tuple[tuple[str, Any], ...]]:
    """기존 sensitivity CSV 를 읽어 완료된 조합의 params key set 반환.

    `write_csv` 가 생성한 포맷 (헤더: 축 이름 + 메트릭 10종) 을 가정한다. 각
    데이터 행의 축 값을 `_AXIS_PARSERS` 로 원형 복원해 `(name, value)` tuple
    순서대로 (grid 축 순서) 묶어 set 으로 반환한다.

    빈 CSV (헤더만 있고 데이터 0행) 는 빈 set 을 반환한다 — `write_csv((), path)`
    의 산출물도 헤더만 있고 축 컬럼이 없을 수 있어 (rows 가 비었을 때
    `_consistent_param_keys` 가 빈 튜플을 반환하므로), 데이터 0행이면 헤더
    검증을 건너뛴다. 데이터 1행 이상이면 grid.axes 의 모든 이름이 헤더에
    있어야 하고 모든 축 이름이 `_AXIS_PARSERS` 에 등록되어 있어야 한다.

    Args:
        path: 기존 sensitivity CSV 파일 경로.
        grid: 현재 실행 중인 그리드 (축 이름·순서 기준).

    Returns:
        완료 조합 params key 의 set. 각 key 는 `tuple((name, value), ...)` 로
        grid 축 순서와 일치한다.

    Raises:
        FileNotFoundError: `path` 가 존재하지 않을 때.
        RuntimeError: 데이터 행이 있는데 헤더에 축 이름이 누락됐거나, 축 이름이
            `_AXIS_PARSERS` 에 등록되지 않은 경우.
    """
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
        missing_cols = [name for name in axis_names if name not in header]
        if missing_cols:
            raise RuntimeError(f"CSV 헤더에 축 {missing_cols!r} 가 없습니다 (header={header})")
        unknown = [name for name in axis_names if name not in _AXIS_PARSERS]
        if unknown:
            raise RuntimeError(f"파싱 규칙 없는 축: {unknown!r}")
        col_idx = {name: header.index(name) for name in axis_names}
        completed: set[tuple[tuple[str, Any], ...]] = set()
        for row in data_rows:
            key_items: list[tuple[str, Any]] = []
            for name in axis_names:
                raw = row[col_idx[name]]
                parser = _AXIS_PARSERS[name]
                key_items.append((name, parser(raw)))
            completed.add(tuple(key_items))
        return completed


def filter_remaining_combos(
    grid: SensitivityGrid,
    completed: set[tuple[tuple[str, Any], ...]],
) -> list[dict[str, Any]]:
    """grid 에서 completed 에 없는 조합만 dict 리스트로 반환.

    `grid.iter_combinations()` 순서를 유지해 결정론. completed 에 grid 에
    없는 orphan 키가 섞여 있어도 단순 차집합 시맨틱으로 무시 (추가 검증·
    경고 없음 — grid 가 사후 좁혀졌을 때 orphan 이 자연스럽게 생김).

    Args:
        grid: 현재 실행 중인 그리드.
        completed: `load_completed_combos` 반환값.

    Returns:
        미완료 조합 dict 리스트. grid 축 순서가 각 dict 의 키 순서로 보존.
    """
    remaining: list[dict[str, Any]] = []
    for combo in grid.iter_combinations():
        key = tuple(combo.items())
        if key not in completed:
            remaining.append(combo)
    return remaining


def merge_sensitivity_rows(
    existing: tuple[SensitivityRow, ...],
    new: tuple[SensitivityRow, ...],
    grid: SensitivityGrid,
) -> tuple[SensitivityRow, ...]:
    """existing·new SensitivityRow 를 grid 순서로 병합해 반환.

    병합 정책:
    - 동일 params 가 existing·new 양쪽에 있으면 **new 우선** (재실행된 조합
      은 최신 결과 채택).
    - grid 에 없는 조합이 existing/new 에 섞여 있으면 무시 (orphan).
    - grid 의 조합이 existing·new 합집합에 없으면 `RuntimeError` (resume
      flow 불완전 — 호출자가 미완료 조합을 실행해야 함).

    Args:
        existing: 기존 CSV 에서 로드한 row (또는 이전 실행 결과).
        new: 이번 실행에서 추가된 row.
        grid: 현재 실행 중인 그리드 (순서·포함 여부 기준).

    Returns:
        grid 순서로 정렬된 `SensitivityRow` 튜플. 길이 = `grid.size`.

    Raises:
        RuntimeError: 병합 후 grid 의 일부 조합이 누락된 경우.
    """
    by_key: dict[tuple[tuple[str, Any], ...], SensitivityRow] = {}
    for row in existing:
        by_key[row.params] = row
    for row in new:
        by_key[row.params] = row
    result: list[SensitivityRow] = []
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
    row: SensitivityRow,
    path: Path,
    grid: SensitivityGrid,
) -> None:
    """조합 1개 결과를 sensitivity CSV 에 atomic append (Issue #82).

    `path` 부재 → 헤더 + row 1행 신규 작성. `path` 존재 → `load_sensitivity_rows`
    로 기존 rows 복원 후 row 추가, 같은 디렉터리의 `.tmp` 파일에 `write_csv` 로
    전체 작성, 마지막에 `os.replace(tmp, path)` 로 atomic rename.

    헤더 포맷은 `write_csv` 와 동일 (축 이름 + 메트릭 10종) 이므로
    `load_completed_combos` 와 round-trip 가능하다.

    Atomic 보장:
    - tmp 파일은 `path` 와 같은 디렉터리 (POSIX `os.replace` 가 동일 디렉터리
      내에서만 atomic).
    - tmp 파일은 `os.replace` 로 final 로 이동되므로 정상 종료 후 누수 없음.
    - 작성 도중 실패 시 tmp 가 남을 수 있으나 final 은 손상되지 않음 — 다음
      실행에서 새로 append.

    동시성:
    - 단일 writer 전제. `--workers >= 2` 의 경우 `as_completed` 직렬 소비
      시점에서만 메인 프로세스가 `on_row` 콜백으로 이 함수를 호출 → 동시
      쓰기 발생하지 않음.

    Args:
        row: append 할 단일 결과 행.
        path: 최종 CSV 경로 (final). 디렉터리는 미리 존재해야 한다 (호출자 책임).
        grid: 헤더 축 이름 검증 + 기존 파일 파싱용 그리드.

    Raises:
        OSError: 파일 쓰기·rename 실패.
        RuntimeError: 기존 파일 헤더에 grid 축 이름이 없거나 파싱 규칙이 없는 축.
    """
    existing_rows: tuple[SensitivityRow, ...] = ()
    if path.exists():
        existing_rows = load_sensitivity_rows(path, grid)

    merged = existing_rows + (row,)
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        write_csv(merged, tmp_path)
        os.replace(str(tmp_path), str(path))
    except BaseException:
        # 실패 시 tmp 정리 — final 은 그대로 보존 (atomic 보장).
        with contextlib.suppress(OSError):
            tmp_path.unlink(missing_ok=True)
        raise


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------


def _apply_combo(base: BacktestConfig, combo: dict[str, Any]) -> BacktestConfig:
    """base config 에 combo 적용 — `dataclasses.replace` 로 파생 config 생성.

    3 개의 config (engine/strategy/risk) 를 각각 부분 업데이트한 뒤, 최종
    `BacktestConfig` 를 조합해 반환.
    """
    strategy_base = base.strategy_config or StrategyConfig()
    risk_base = base.risk_config or RiskConfig()

    engine_updates: dict[str, Any] = {}
    strategy_updates: dict[str, Any] = {}
    risk_updates: dict[str, Any] = {}

    for name, value in combo.items():
        prefix, _, field_name = name.partition(".")
        if prefix == _STRATEGY_PREFIX:
            _require_dataclass_field(strategy_base, field_name, name)
            strategy_updates[field_name] = value
        elif prefix == _RISK_PREFIX:
            _require_dataclass_field(risk_base, field_name, name)
            risk_updates[field_name] = value
        elif prefix == _ENGINE_PREFIX:
            if field_name not in _ENGINE_TUNABLE_FIELDS:
                raise RuntimeError(
                    f"engine.* 그리드 대상 필드는 {sorted(_ENGINE_TUNABLE_FIELDS)} 로 제한됩니다 "
                    f"(got={name!r})"
                )
            engine_updates[field_name] = value
        else:
            # ParameterAxis 가 이미 검증하지만 방어 depth.
            raise RuntimeError(f"알 수 없는 prefix: {name!r}")

    strategy_new = (
        dataclasses.replace(strategy_base, **strategy_updates)
        if strategy_updates
        else strategy_base
    )
    risk_new = dataclasses.replace(risk_base, **risk_updates) if risk_updates else risk_base
    return dataclasses.replace(
        base,
        strategy_config=strategy_new,
        risk_config=risk_new,
        **engine_updates,
    )


def _require_dataclass_field(instance: Any, field_name: str, axis_name: str) -> None:
    """dataclasses.fields 로 field 존재 확인. 없으면 `RuntimeError`."""
    names = {f.name for f in dataclasses.fields(instance)}
    if field_name not in names:
        raise RuntimeError(
            f"{type(instance).__name__} 에 필드 {field_name!r} 가 없습니다 "
            f"(axis={axis_name}, available={sorted(names)})"
        )


def _run_single_combo(
    combo_idx: int,
    combo: dict[str, Any],
    loader_factory: Callable[[], BarLoader],
    start: date,
    end: date,
    symbols: tuple[str, ...],
    base_config: BacktestConfig,
) -> tuple[int, SensitivityRow]:
    """ProcessPool 워커 진입점 — 단일 조합 실행 후 `(idx, row)` 반환.

    모듈 top-level 함수로 유지해 `ProcessPoolExecutor.submit` 가 pickle 가능
    하게 한다 (closure 사용 금지). loader 는 워커 안에서 생성·`close` 한다 —
    KisMinuteBarLoader 처럼 SQLite·PyKis 세션을 잡고 있는 구현이 워커 종료
    경로에서 누수되지 않도록.
    """
    loader = loader_factory()
    try:
        config = _apply_combo(base_config, combo)
        engine = BacktestEngine(config)
        logger.debug(
            "sensitivity.parallel.attempt idx={i} combo={c}",
            i=combo_idx,
            c=combo,
        )
        result = engine.run(loader.stream(start, end, symbols))
        row = _result_to_row(combo, result)
        logger.info(
            "sensitivity.parallel.combo_done idx={i} combo={c} net_pnl={p} trades={t}",
            i=combo_idx,
            c=combo,
            p=result.metrics.net_pnl_krw,
            t=len(result.trades),
        )
        return combo_idx, row
    finally:
        close = getattr(loader, "close", None)
        if callable(close):
            close()


def _result_to_row(combo: dict[str, Any], result: BacktestResult) -> SensitivityRow:
    rejected_total = sum(result.rejected_counts.values())
    return SensitivityRow(
        params=tuple(combo.items()),
        metrics=result.metrics,
        trade_count=len(result.trades),
        rejected_total=rejected_total,
        post_slippage_rejections=result.post_slippage_rejections,
    )


# BacktestMetrics 안에 있는 7 필드 vs 보조 3 필드 분리. `sort_by`·렌더러가
# 평면 키로 접근할 수 있게 헬퍼로 추상화.
_METRIC_FIELDS_ON_ROW: frozenset[str] = frozenset(
    {"trade_count", "rejected_total", "post_slippage_rejections"}
)


def _get_metric_value(row: SensitivityRow, key: str) -> Any:
    """`key` 가 BacktestMetrics 필드면 `row.metrics.xxx`, 보조 필드면 `row.xxx`."""
    if key in _METRIC_FIELDS_ON_ROW:
        return getattr(row, key)
    return getattr(row.metrics, key)


def _metric_columns() -> tuple[str, ...]:
    """렌더러가 공통으로 사용하는 메트릭 컬럼 순서."""
    return (
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


def _consistent_param_keys(rows: tuple[SensitivityRow, ...]) -> tuple[str, ...]:
    """모든 row 의 params 축 이름 집합이 동일한지 확인하고 첫 row 의 순서를 반환.

    `SensitivityRow.params` 는 축 선언 순서가 보존된 튜플이므로 첫 row 의 순서를
    그대로 컬럼 순서로 쓴다.
    """
    if not rows:
        return ()
    first_keys = tuple(name for name, _ in rows[0].params)
    first_set = set(first_keys)
    for row in rows[1:]:
        other_keys = {name for name, _ in row.params}
        if other_keys != first_set:
            raise RuntimeError(
                "SensitivityRow 들의 params 축 이름 집합이 일치하지 않습니다 — "
                f"동일 그리드에서 생성된 결과인지 확인 필요 (first={sorted(first_set)}, "
                f"other={sorted(other_keys)})"
            )
    return first_keys


def _format_param(value: Any) -> str:
    """파라미터 값 표기 — Decimal·time·기타 모두 `str()` 로 통일."""
    return str(value)


def _format_metric(key: str, value: Any) -> str:
    """메트릭 값 표기 — 비율은 소수 4자리, KRW·카운트는 정수."""
    if isinstance(value, Decimal):
        # 비율(소수). 4자리면 0.0001 단위 — 승률·수익률 관찰에 충분.
        return f"{value:.4f}"
    if isinstance(value, int):
        return f"{value:d}"
    return str(value)


# ---------------------------------------------------------------------------
# 기본 그리드 — plan.md line 149 의 "OR 구간(15/30분), 손절/익절 레벨 비교".
# scripts/sensitivity.py 가 이 상수를 그대로 소비한다. 외부 YAML 로의 이관은
# YAGNI (코드 상수 기조 — broker/data 와 동일).
# ---------------------------------------------------------------------------


def default_grid() -> SensitivityGrid:
    """plan.md Phase 2 기본 그리드 — 2×4×4 = 32 조합.

    - `strategy.or_end`: 09:15, 09:30 (OR 15분 vs 30분)
    - `strategy.stop_loss_pct`: 1.0%, 1.5%, 2.0%, 2.5%
    - `strategy.take_profit_pct`: 2.0%, 3.0%, 4.0%, 5.0%

    현재 운영 기본값(or_end=09:30, stop=0.015, take=0.030) 은 반드시 그리드
    안에 포함된다 — "현재 기본값 vs 그리드 최상위" 비교가 자동으로 나온다.
    """
    return SensitivityGrid(
        axes=(
            ParameterAxis(
                name="strategy.or_end",
                values=(time(9, 15), time(9, 30)),
            ),
            ParameterAxis(
                name="strategy.stop_loss_pct",
                values=(
                    Decimal("0.010"),
                    Decimal("0.015"),
                    Decimal("0.020"),
                    Decimal("0.025"),
                ),
            ),
            ParameterAxis(
                name="strategy.take_profit_pct",
                values=(
                    Decimal("0.020"),
                    Decimal("0.030"),
                    Decimal("0.040"),
                    Decimal("0.050"),
                ),
            ),
        ),
    )


__all__ = [
    "ParameterAxis",
    "SensitivityGrid",
    "SensitivityRow",
    "append_sensitivity_row",
    "default_grid",
    "filter_remaining_combos",
    "load_completed_combos",
    "load_sensitivity_rows",
    "merge_sensitivity_rows",
    "render_markdown_table",
    "run_sensitivity",
    "run_sensitivity_combos",
    "run_sensitivity_combos_parallel",
    "run_sensitivity_parallel",
    "write_csv",
]
