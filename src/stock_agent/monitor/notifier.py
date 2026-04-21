"""텔레그램 알림 notifier — `Notifier` Protocol + 구현체.

책임 범위
- `Executor` 가 반환한 `StepReport.entry_events` / `exit_events` 와
  `RiskManager` 공개 프로퍼티를 소비해 텔레그램 봇으로 푸시한다.
- 전송 실패는 호출자에 재전파하지 않는다 (ADR-0011 결정 5 연장). 로그만
  남기고 실패 카운터를 증가시킨다 — 임계값 도달 시 `logger.critical`
  1회 dedupe 방출 (`data/realtime.py` 폴링 경보 패턴과 동일).

설계 결정
- `bot_factory` 주입 — 단위 테스트에서 실 `telegram.Bot` / 네트워크 접촉 0.
- `Bot` 인스턴스는 생성자에서 1회 만들어 재사용 — `async with bot:` 컨텍스트는
  각 전송마다 새로 열지만 Bot 객체 생성 비용은 한 번으로 고정.
- 포맷 plain text 한국어 고정 — MarkdownV2 특수문자 escape 실패로 전송이
  거부되는 경로를 막는다.
- `dry_run=True` 면 모든 제목 맨 앞에 `[DRY-RUN] ` 프리픽스. 알림 경로
  자체를 end-to-end 검증할 수 있도록 실전송은 유지한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from loguru import logger
from pydantic import SecretStr
from telegram import Bot
from telegram.error import TelegramError

if TYPE_CHECKING:
    from datetime import date

    from stock_agent.execution import EntryEvent, ExitEvent

KST = timezone(timedelta(hours=9))

ClockFn = Callable[[], datetime]
"""KST aware datetime 을 반환하는 시계 — 테스트에서 고정 datetime 주입."""

BotFactory = Callable[[str], Bot]
"""token 문자열을 받아 `telegram.Bot` 을 생성하는 팩토리."""


# ---- DTO -----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    """에러 알림 이벤트.

    `severity="error"` 는 복구 가능성 있는 일시 오류(예: step 콜백 예외).
    `severity="critical"` 는 운영 리스크 심각 (예: force_close 실패로 포지션
    잔존, reconcile mismatch).
    """

    stage: str
    error_class: str
    message: str
    timestamp: datetime
    severity: Literal["error", "critical"]


@dataclass(frozen=True, slots=True)
class DailySummary:
    """15:30 일일 요약 DTO.

    `realized_pnl_pct` 는 `starting_capital_krw` 가 None/0 일 때 None —
    notifier 가 "n/a" 로 출력한다. `mismatch_symbols` 가 비어있지 않으면
    reconcile 불일치가 발생한 세션 — 운영자 수동 정리 필요 상태.
    """

    session_date: date
    starting_capital_krw: int | None
    realized_pnl_krw: int
    realized_pnl_pct: float | None
    entries_today: int
    halted: bool
    mismatch_symbols: tuple[str, ...]


# ---- Protocol ------------------------------------------------------------


@runtime_checkable
class Notifier(Protocol):
    """알림 라우팅 경계 — main.py 콜백이 의존.

    실구현(`TelegramNotifier`) 과 no-op(`NullNotifier`) 모두 본 Protocol 을
    만족한다. 테스트에서는 `MagicMock(spec=Notifier)` 주입으로 호출 횟수·인자를
    검증.
    """

    def notify_entry(self, event: EntryEvent) -> None: ...

    def notify_exit(self, event: ExitEvent) -> None: ...

    def notify_error(self, event: ErrorEvent) -> None: ...

    def notify_daily_summary(self, summary: DailySummary) -> None: ...


# ---- NullNotifier --------------------------------------------------------


class NullNotifier:
    """no-op 구현 — 팩토리 실패 폴백·테스트·알림 비활성 모드.

    모든 메서드는 아무 동작도 하지 않고 반환한다. 예외를 던지지 않는다.
    `Notifier` Protocol 을 자연 만족한다.
    """

    def notify_entry(self, event: EntryEvent) -> None:
        return None

    def notify_exit(self, event: ExitEvent) -> None:
        return None

    def notify_error(self, event: ErrorEvent) -> None:
        return None

    def notify_daily_summary(self, summary: DailySummary) -> None:
        return None


# ---- TelegramNotifier ----------------------------------------------------


class TelegramNotifier:
    """python-telegram-bot 기반 실구현.

    각 `notify_*` 는 내부에서 `asyncio.run(asyncio.wait_for(..., timeout_s))`
    로 동기 래핑된다 — 호출자(main.py 콜백) 는 동기 API 만 본다. 전송 실패는
    silent fail + `logger.exception`, 연속 실패 카운터가 임계값에 도달하면
    `logger.critical` 1회만 방출한다(dedupe).

    Raises:
        RuntimeError: `timeout_s <= 0` 또는 `consecutive_failure_threshold <= 0`.
    """

    def __init__(
        self,
        *,
        bot_token: SecretStr,
        chat_id: int,
        dry_run: bool = False,
        timeout_s: float = 5.0,
        consecutive_failure_threshold: int = 5,
        clock: ClockFn | None = None,
        bot_factory: BotFactory | None = None,
    ) -> None:
        if timeout_s <= 0:
            raise RuntimeError(f"timeout_s 는 양수여야 합니다 (got={timeout_s})")
        if consecutive_failure_threshold <= 0:
            raise RuntimeError(
                f"consecutive_failure_threshold 는 양의 정수여야 합니다 "
                f"(got={consecutive_failure_threshold})"
            )
        self._chat_id = chat_id
        self._dry_run = dry_run
        self._timeout_s = timeout_s
        self._threshold = consecutive_failure_threshold
        self._clock: ClockFn = clock or (lambda: datetime.now(KST))
        factory: BotFactory = bot_factory or (lambda token: Bot(token=token))
        self._bot: Bot = factory(bot_token.get_secret_value())
        self._consecutive_failures: int = 0
        self._persistent_alert_emitted: bool = False

    # ---- 공개 메서드 ---------------------------------------------------

    def notify_entry(self, event: EntryEvent) -> None:
        body = (
            f"종목={event.symbol} 수량={event.qty}주 "
            f"체결가={event.fill_price} 참고가={event.ref_price} "
            f"시각={self._fmt_time(event.timestamp)}"
        )
        self._send("[stock-agent] 진입 체결", body)

    def notify_exit(self, event: ExitEvent) -> None:
        body = (
            f"종목={event.symbol} 수량={event.qty}주 "
            f"체결가={event.fill_price} 사유={event.reason} "
            f"PnL={event.net_pnl_krw}원 "
            f"시각={self._fmt_time(event.timestamp)}"
        )
        self._send("[stock-agent] 청산 체결", body)

    def notify_error(self, event: ErrorEvent) -> None:
        title = f"[stock-agent] {event.severity.upper()} {event.stage}"
        body = f"에러={event.error_class}: {event.message}\n시각={self._fmt_time(event.timestamp)}"
        self._send(title, body)

    def notify_daily_summary(self, summary: DailySummary) -> None:
        pct = "n/a" if summary.realized_pnl_pct is None else f"{summary.realized_pnl_pct:.2f}%"
        halted = "yes" if summary.halted else "no"
        mismatch = ",".join(summary.mismatch_symbols) if summary.mismatch_symbols else "없음"
        title = f"[stock-agent] 일일 요약 {summary.session_date.isoformat()}"
        body = (
            f"실현 PnL={summary.realized_pnl_krw}원 ({pct})\n"
            f"진입 횟수={summary.entries_today}\n"
            f"서킷브레이커={halted}\n"
            f"Executor halt={halted}\n"
            f"Reconcile mismatch={mismatch}"
        )
        self._send(title, body)

    # ---- 내부 ---------------------------------------------------------

    def _send(self, title: str, body: str) -> None:
        """실전송 경로 — silent fail + 연속 실패 dedupe 경보.

        호출자에 예외를 재전파하지 않는다. 세션 연속성보다 알림 전송 실패의
        영향이 작다 — 운영자는 로그 sink 로도 동일 정보를 얻을 수 있다.
        """
        full_title = f"[DRY-RUN] {title}" if self._dry_run else title
        text = f"{full_title}\n{body}"
        try:
            asyncio.run(self._async_send(text))
        except TelegramError as e:
            logger.exception(f"telegram.notifier.telegram_error: {e.__class__.__name__}: {e}")
            self._record_failure()
        except TimeoutError as e:
            logger.exception(f"telegram.notifier.timeout timeout_s={self._timeout_s} err={e!r}")
            self._record_failure()
        except Exception as e:  # noqa: BLE001 — 네트워크·asyncio·SSL 등 silent fail 정책
            logger.exception(f"telegram.notifier.generic_error: {e.__class__.__name__}: {e}")
            self._record_failure()
        else:
            self._consecutive_failures = 0
            self._persistent_alert_emitted = False

    async def _async_send(self, text: str) -> None:
        """`asyncio.wait_for` 로 타임아웃을 강제한 실전송 코루틴.

        `async with bot:` 패턴은 `scripts/healthcheck.py:84-98` 과 동일 —
        Application 장기 실행 대신 매 호출마다 컨텍스트를 여닫아 리소스
        누수를 방지한다.
        """

        async def _inner() -> None:
            async with self._bot as bot:
                await bot.send_message(chat_id=self._chat_id, text=text)

        await asyncio.wait_for(_inner(), timeout=self._timeout_s)

    def _record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._threshold and not self._persistent_alert_emitted:
            logger.critical(
                "telegram.notifier.persistent_failure consecutive={n} threshold={t} "
                "— 텔레그램 알림 경로 점검 필요 (봇 토큰·네트워크·KIS Developers 아님, "
                "Telegram API 연결).",
                n=self._consecutive_failures,
                t=self._threshold,
            )
            self._persistent_alert_emitted = True

    def _fmt_time(self, ts: datetime) -> str:
        """KST aware datetime 을 `HH:MM:SS` 로 포맷 (tz-naive 도 그대로 포맷)."""
        return ts.strftime("%H:%M:%S")
