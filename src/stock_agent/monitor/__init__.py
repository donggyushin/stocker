"""monitor 패키지 — 텔레그램 알림·일일 요약 라우팅.

상위 진입점(`main.py`) 은 `Notifier` Protocol 만 의존하고 구체 구현
(`TelegramNotifier` / `NullNotifier`) 은 조립 시점에 주입된다. `Executor`
가 반환하는 `StepReport.entry_events` / `exit_events` 와 `RiskManager` 의
공개 프로퍼티를 소비해 진입·청산·에러·일일 요약을 푸시한다.

실전송 실패는 재전파하지 않는다 (콜백 예외 re-raise 금지 원칙, ADR-0011
결정 5 연장). 연속 실패 N회 초과 시 `logger.critical` 1회만 dedupe 발행.
"""

from stock_agent.monitor.notifier import (
    DailySummary,
    ErrorEvent,
    Notifier,
    NullNotifier,
    TelegramNotifier,
)

__all__ = [
    "DailySummary",
    "ErrorEvent",
    "Notifier",
    "NullNotifier",
    "TelegramNotifier",
]
