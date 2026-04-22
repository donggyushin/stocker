"""storage — SQLite 원장 (주문·체결·일일 PnL) + 재기동 상태 복원 조회.

공개 심볼:
- `TradingRecorder` (Protocol, @runtime_checkable)
- `SqliteTradingRecorder` — 기본 구현
- `NullTradingRecorder` — no-op 폴백
- `StorageError` — 초기화 실패 래퍼
- `OpenPositionRow` — `load_open_positions` 반환 DTO (Issue #33)
- `DailyPnlSnapshot` — `load_daily_pnl` 반환 DTO (Issue #33)

모듈 세부는 [CLAUDE.md](./CLAUDE.md) 참조.
"""

from stock_agent.storage.db import (
    DailyPnlSnapshot,
    NullTradingRecorder,
    OpenPositionRow,
    SqliteTradingRecorder,
    StorageError,
    TradingRecorder,
)

__all__ = [
    "DailyPnlSnapshot",
    "NullTradingRecorder",
    "OpenPositionRow",
    "SqliteTradingRecorder",
    "StorageError",
    "TradingRecorder",
]
