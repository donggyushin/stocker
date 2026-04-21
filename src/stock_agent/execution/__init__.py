"""execution 패키지 — 신호 → 주문 → 체결 추적 → 상태 동기화 루프.

상위 진입점(`main.py`, Phase 3 후속) 은 본 패키지의 공개 심볼만 사용한다.
KisClient·RealtimeDataStore·ORBStrategy·RiskManager 의 구체 의존은
`Executor` 내부에서 Protocol 로 추상화되어 단위 테스트에서 KIS 접촉 없이
검증된다.
"""

from stock_agent.execution.executor import (
    BalanceProvider,
    BarSource,
    DryRunOrderSubmitter,
    EntryEvent,
    Executor,
    ExecutorConfig,
    ExecutorError,
    ExitEvent,
    LiveBalanceProvider,
    LiveOrderSubmitter,
    OrderSubmitter,
    ReconcileReport,
    StepReport,
)

__all__ = [
    "BalanceProvider",
    "BarSource",
    "DryRunOrderSubmitter",
    "EntryEvent",
    "Executor",
    "ExecutorConfig",
    "ExecutorError",
    "ExitEvent",
    "LiveBalanceProvider",
    "LiveOrderSubmitter",
    "OrderSubmitter",
    "ReconcileReport",
    "StepReport",
]
