"""strategy 패키지 공개 심볼.

상위 레이어(execution/backtest/main) 는 이 패키지의 공개 심볼만 사용한다.
시그널 DTO(`EntrySignal`/`ExitSignal`) 와 `Strategy` Protocol, 구현체(`ORBStrategy`)
만 노출한다. 내부 상태(`_SymbolState`) 는 패키지 private.
"""

from stock_agent.strategy.base import (
    EntrySignal,
    ExitReason,
    ExitSignal,
    Signal,
    Strategy,
)
from stock_agent.strategy.orb import (
    ORBStrategy,
    StrategyConfig,
    StrategyError,
)
from stock_agent.strategy.vwap_mr import (
    VWAPMRConfig,
    VWAPMRStrategy,
)

__all__ = [
    "EntrySignal",
    "ExitReason",
    "ExitSignal",
    "ORBStrategy",
    "Signal",
    "Strategy",
    "StrategyConfig",
    "StrategyError",
    "VWAPMRConfig",
    "VWAPMRStrategy",
]
