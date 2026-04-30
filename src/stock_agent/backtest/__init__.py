"""backtest 패키지 공개 심볼.

상위 레이어(scripts/main) 는 이 패키지의 공개 심볼만 사용한다. 비용·메트릭 모듈
(`costs`, `metrics`) 은 엔진 내부 구현 디테일이므로 직접 노출하지 않지만,
필요 시 `stock_agent.backtest.costs` / `stock_agent.backtest.metrics` 로 직접
접근 가능 (테스트·민감도 분석 후속 PR 용도).

`RejectReason` 은 `BacktestResult.rejected_counts` 의 키 타입이라 동일 패키지에서
재노출한다 (소비자가 `stock_agent.risk` 를 직접 import 하지 않아도 되도록).
"""

from stock_agent.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestMetrics,
    BacktestResult,
    DailyEquity,
    TradeRecord,
)
from stock_agent.backtest.loader import BarLoader, InMemoryBarLoader
from stock_agent.backtest.sensitivity import (
    ParameterAxis,
    SensitivityGrid,
    SensitivityRow,
    append_sensitivity_row,
    default_grid,
    filter_remaining_combos,
    load_completed_combos,
    load_sensitivity_rows,
    merge_sensitivity_rows,
    render_markdown_table,
    run_sensitivity,
    run_sensitivity_combos,
    run_sensitivity_combos_parallel,
    run_sensitivity_parallel,
    step_d1_grid,
    write_csv,
)
from stock_agent.backtest.walk_forward import (
    WalkForwardMetrics,
    WalkForwardResult,
    WalkForwardWindow,
    generate_windows,
    run_walk_forward,
)
from stock_agent.risk import RejectReason

__all__ = [
    "BacktestConfig",
    "BacktestEngine",
    "BacktestMetrics",
    "BacktestResult",
    "BarLoader",
    "DailyEquity",
    "InMemoryBarLoader",
    "ParameterAxis",
    "RejectReason",
    "SensitivityGrid",
    "SensitivityRow",
    "TradeRecord",
    "WalkForwardMetrics",
    "WalkForwardResult",
    "WalkForwardWindow",
    "append_sensitivity_row",
    "default_grid",
    "filter_remaining_combos",
    "generate_windows",
    "load_completed_combos",
    "load_sensitivity_rows",
    "merge_sensitivity_rows",
    "render_markdown_table",
    "run_sensitivity",
    "run_sensitivity_combos",
    "run_sensitivity_combos_parallel",
    "run_sensitivity_parallel",
    "run_walk_forward",
    "step_d1_grid",
    "write_csv",
]
