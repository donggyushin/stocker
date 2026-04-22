"""TelegramNotifier / NullNotifier / DTO 공개 계약 단위 테스트 (RED 모드).

stock_agent.monitor 패키지와 stock_agent.execution 의 EntryEvent/ExitEvent 가
아직 미작성 상태이므로 모든 케이스가 ImportError 로 실패한다.
구현 완료 후 GREEN 전환을 목표로 한다.

가드레일: 실제 telegram.Bot 생성·네트워크 접촉 0.
         bot_factory 주입으로 AsyncMock bot 을 사용한다.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr
from pytest_mock import MockerFixture

# ---------------------------------------------------------------------------
# import — 이 블록이 ImportError / ModuleNotFoundError 로 실패하는 것이
# RED 모드의 목표. 구현 완료 후 GREEN 으로 전환된다.
# ---------------------------------------------------------------------------
from stock_agent.execution import EntryEvent, ExitEvent  # noqa: F401
from stock_agent.monitor import (
    DailySummary,
    ErrorEvent,
    Notifier,
    NullNotifier,
    TelegramNotifier,
)
from stock_agent.strategy import ExitReason

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))
_FIXED_DT = datetime(2026, 4, 21, 14, 30, 0, tzinfo=KST)
_FIXED_DATE = date(2026, 4, 21)
_TOKEN = SecretStr("dummy-bot-token")
_CHAT_ID = 123456789
_SYMBOL = "005930"


def _fixed_clock() -> datetime:
    return _FIXED_DT


def _make_bot_mock() -> MagicMock:
    """AsyncMock send_message + async context manager 를 갖춘 Bot 더블 생성."""
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=None)
    # async with bot: 패턴 지원
    bot.__aenter__ = AsyncMock(return_value=bot)
    bot.__aexit__ = AsyncMock(return_value=False)
    return bot


def _make_bot_factory(bot: MagicMock) -> MagicMock:
    """주어진 bot 인스턴스를 반환하는 MagicMock 팩토리.

    Callable 로 호출 가능하면서 assert_called_once/call_args 접근 가능.
    """
    return MagicMock(return_value=bot)


def _make_notifier(
    *,
    bot: MagicMock | None = None,
    dry_run: bool = False,
    timeout_s: float = 5.0,
    consecutive_failure_threshold: int = 5,
) -> TelegramNotifier:
    """TelegramNotifier 생성 헬퍼 — bot_factory 주입으로 네트워크 접촉 0."""
    if bot is None:
        bot = _make_bot_mock()
    factory = _make_bot_factory(bot)
    return TelegramNotifier(
        bot_token=_TOKEN,
        chat_id=_CHAT_ID,
        dry_run=dry_run,
        timeout_s=timeout_s,
        consecutive_failure_threshold=consecutive_failure_threshold,
        clock=_fixed_clock,
        bot_factory=factory,
    )


def _make_entry_event(
    *,
    symbol: str = _SYMBOL,
    qty: int = 10,
    fill_price: Decimal = Decimal("50000"),
    ref_price: Decimal = Decimal("49500"),
    order_number: str = "ORD-ENTRY-001",
) -> EntryEvent:
    """EntryEvent 더블 — order_number 포함 (EntryEvent.order_number 추가 계약)."""
    return EntryEvent(  # type: ignore[call-arg]
        symbol=symbol,
        qty=qty,
        fill_price=fill_price,
        ref_price=ref_price,
        timestamp=_FIXED_DT,
        order_number=order_number,
    )


def _make_exit_event(
    *,
    symbol: str = _SYMBOL,
    qty: int = 10,
    fill_price: Decimal = Decimal("51500"),
    reason: ExitReason = "take_profit",
    net_pnl_krw: int = 14_000,
    order_number: str = "ORD-EXIT-001",
) -> ExitEvent:
    """ExitEvent 더블 — order_number 포함 (ExitEvent.order_number 추가 계약)."""
    return ExitEvent(  # type: ignore[call-arg]
        symbol=symbol,
        qty=qty,
        fill_price=fill_price,
        reason=reason,
        net_pnl_krw=net_pnl_krw,
        timestamp=_FIXED_DT,
        order_number=order_number,
    )


def _make_error_event(
    *,
    stage: str = "executor.step",
    error_class: str = "ExecutorError",
    message: str = "체결 타임아웃",
    severity: str = "error",
) -> ErrorEvent:
    return ErrorEvent(
        stage=stage,
        error_class=error_class,
        message=message,
        timestamp=_FIXED_DT,
        severity=severity,  # type: ignore[arg-type]
    )


def _make_daily_summary(
    *,
    session_date: date = _FIXED_DATE,
    starting_capital_krw: int | None = 1_000_000,
    realized_pnl_krw: int = 15_000,
    realized_pnl_pct: float | None = 1.5,
    entries_today: int = 2,
    halted: bool = False,
    mismatch_symbols: tuple[str, ...] = (),
) -> DailySummary:
    return DailySummary(
        session_date=session_date,
        starting_capital_krw=starting_capital_krw,
        realized_pnl_krw=realized_pnl_krw,
        realized_pnl_pct=realized_pnl_pct,
        entries_today=entries_today,
        halted=halted,
        mismatch_symbols=mismatch_symbols,
    )


# ---------------------------------------------------------------------------
# 1. 공개 심볼 노출 검증
# ---------------------------------------------------------------------------


class TestPublicSymbolExposure:
    """stock_agent.monitor 에서 필요한 심볼이 import 가능한지 검증."""

    def test_notifier_protocol_importable(self) -> None:
        assert Notifier is not None

    def test_telegram_notifier_importable(self) -> None:
        assert TelegramNotifier is not None

    def test_null_notifier_importable(self) -> None:
        assert NullNotifier is not None

    def test_error_event_importable(self) -> None:
        assert ErrorEvent is not None

    def test_daily_summary_importable(self) -> None:
        assert DailySummary is not None

    def test_entry_event_from_execution(self) -> None:
        """EntryEvent 는 stock_agent.execution 에서 노출 — 미구현 시 ImportError."""
        assert EntryEvent is not None

    def test_exit_event_from_execution(self) -> None:
        """ExitEvent 는 stock_agent.execution 에서 노출 — 미구현 시 ImportError."""
        assert ExitEvent is not None


# ---------------------------------------------------------------------------
# 2. NullNotifier — no-op 4종 메서드
# ---------------------------------------------------------------------------


class TestNullNotifier:
    """NullNotifier 는 모든 메서드를 호출 가능하고 예외를 발생시키지 않는다."""

    @pytest.fixture
    def null(self) -> NullNotifier:
        return NullNotifier()

    def test_notify_entry_no_op(self, null: NullNotifier) -> None:
        null.notify_entry(_make_entry_event())

    def test_notify_exit_no_op(self, null: NullNotifier) -> None:
        null.notify_exit(_make_exit_event())

    def test_notify_error_no_op(self, null: NullNotifier) -> None:
        null.notify_error(_make_error_event())

    def test_notify_daily_summary_no_op(self, null: NullNotifier) -> None:
        null.notify_daily_summary(_make_daily_summary())

    def test_null_notifier_satisfies_notifier_protocol(self, null: NullNotifier) -> None:
        """NullNotifier 가 Notifier Protocol 을 충족하는지 isinstance 로 확인."""
        assert isinstance(null, Notifier)


# ---------------------------------------------------------------------------
# 3. TelegramNotifier 생성자 가드
# ---------------------------------------------------------------------------


class TestTelegramNotifierInit:
    """timeout_s / consecutive_failure_threshold 경계값 → RuntimeError."""

    def test_timeout_zero_raises(self) -> None:
        bot = _make_bot_mock()
        with pytest.raises(RuntimeError):
            TelegramNotifier(
                bot_token=_TOKEN,
                chat_id=_CHAT_ID,
                timeout_s=0,
                bot_factory=_make_bot_factory(bot),
            )

    def test_timeout_negative_raises(self) -> None:
        bot = _make_bot_mock()
        with pytest.raises(RuntimeError):
            TelegramNotifier(
                bot_token=_TOKEN,
                chat_id=_CHAT_ID,
                timeout_s=-1.0,
                bot_factory=_make_bot_factory(bot),
            )

    def test_consecutive_failure_threshold_zero_raises(self) -> None:
        bot = _make_bot_mock()
        with pytest.raises(RuntimeError):
            TelegramNotifier(
                bot_token=_TOKEN,
                chat_id=_CHAT_ID,
                consecutive_failure_threshold=0,
                bot_factory=_make_bot_factory(bot),
            )

    def test_valid_params_no_raise(self) -> None:
        """정상 파라미터는 RuntimeError 없이 생성된다."""
        _make_notifier()  # 예외 없이 통과하면 pass

    def test_bot_factory_raising_exception_propagates_from_init(self) -> None:
        """bot_factory 가 예외를 던지면 __init__ 은 예외를 삼키지 말고 그대로 전파해야 한다.

        근거: main.py._default_notifier_factory 의 `except Exception → NullNotifier` 폴백은
        생성자가 예외를 전파한다는 하위 계약에 의존한다. 만약 __init__ 이 내부적으로
        예외를 삼키고 self 를 반환하면, 잘못된 토큰으로 기동해도 NullNotifier 로 폴백되지
        않고 silent 하게 "정상" 으로 오인된다 (이슈 #26).
        """
        boom = RuntimeError("bot factory failure")
        factory = MagicMock(side_effect=boom)
        with pytest.raises(RuntimeError) as exc_info:
            TelegramNotifier(
                bot_token=_TOKEN,
                chat_id=_CHAT_ID,
                bot_factory=factory,
            )
        assert exc_info.value is boom


# ---------------------------------------------------------------------------
# 4. bot_factory 주입 계약 — 생성자에서 1회 호출, 각 notify 에서 재사용
# ---------------------------------------------------------------------------


class TestBotFactoryContract:
    """bot_factory 는 생성자에서 정확히 1회 호출되어 bot 인스턴스를 캐시한다."""

    def test_factory_called_once_at_init(self) -> None:
        bot = _make_bot_mock()
        factory = _make_bot_factory(bot)
        TelegramNotifier(
            bot_token=_TOKEN,
            chat_id=_CHAT_ID,
            bot_factory=factory,
            clock=_fixed_clock,
        )
        factory.assert_called_once()

    def test_factory_receives_token_string(self) -> None:
        """bot_factory 는 token 문자열(str)을 인자로 받아야 한다."""
        bot = _make_bot_mock()
        factory = _make_bot_factory(bot)
        TelegramNotifier(
            bot_token=_TOKEN,
            chat_id=_CHAT_ID,
            bot_factory=factory,
            clock=_fixed_clock,
        )
        args, _ = factory.call_args
        assert args[0] == _TOKEN.get_secret_value()

    def test_same_bot_reused_across_notify_calls(self) -> None:
        """여러 notify 호출에서 bot.send_message 가 동일 인스턴스로 호출됨을 검증."""
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event())
        n.notify_exit(_make_exit_event())
        # send_message 2회 — 새 bot 인스턴스가 매번 생성됐다면 합산 1회씩
        assert bot.send_message.await_count == 2


# ---------------------------------------------------------------------------
# 5. notify_entry — 메시지 포맷 검증
# ---------------------------------------------------------------------------


class TestNotifyEntry:
    """notify_entry 는 Bot.send_message 를 올바른 chat_id + text 로 호출한다."""

    def test_send_message_called_once(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event())
        assert bot.send_message.await_count == 1

    def test_correct_chat_id(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event())
        _, kwargs = bot.send_message.call_args
        assert kwargs["chat_id"] == _CHAT_ID

    def test_text_contains_진입체결(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event(symbol="005930"))
        _, kwargs = bot.send_message.call_args
        assert "진입 체결" in kwargs["text"]

    def test_text_contains_symbol(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event(symbol="005930"))
        _, kwargs = bot.send_message.call_args
        assert "005930" in kwargs["text"]

    def test_text_contains_qty(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event(qty=7))
        _, kwargs = bot.send_message.call_args
        assert "7" in kwargs["text"]

    def test_text_contains_fill_price(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event(fill_price=Decimal("52000")))
        _, kwargs = bot.send_message.call_args
        assert "52000" in kwargs["text"] or "52,000" in kwargs["text"]

    def test_text_contains_ref_price(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event(ref_price=Decimal("51800")))
        _, kwargs = bot.send_message.call_args
        assert "51800" in kwargs["text"] or "51,800" in kwargs["text"]

    def test_text_contains_hhmmss(self) -> None:
        """timestamp 가 HH:MM:SS 형태로 포함되어야 한다 (14:30:00)."""
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event())
        _, kwargs = bot.send_message.call_args
        assert "14:30:00" in kwargs["text"]

    def test_prefix_stock_agent(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event())
        _, kwargs = bot.send_message.call_args
        assert kwargs["text"].startswith("[stock-agent]")


# ---------------------------------------------------------------------------
# 6. notify_exit — reason 별 + PnL 포맷
# ---------------------------------------------------------------------------


class TestNotifyExit:
    """notify_exit 는 reason·PnL 을 메시지에 포함한다."""

    @pytest.mark.parametrize(
        "reason",
        ["stop_loss", "take_profit", "force_close"],
        ids=["stop_loss", "take_profit", "force_close"],
    )
    def test_text_contains_reason(self, reason: ExitReason) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_exit(_make_exit_event(reason=reason))
        _, kwargs = bot.send_message.call_args
        assert reason in kwargs["text"]

    def test_text_contains_positive_pnl(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_exit(_make_exit_event(net_pnl_krw=14_000))
        _, kwargs = bot.send_message.call_args
        assert "14000" in kwargs["text"] or "14,000" in kwargs["text"]

    def test_text_contains_negative_pnl(self) -> None:
        """음수 PnL 이 텍스트에 포함되어야 한다."""
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_exit(_make_exit_event(net_pnl_krw=-7_500))
        _, kwargs = bot.send_message.call_args
        text = kwargs["text"]
        assert "-7500" in text or "-7,500" in text

    def test_text_contains_청산체결(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_exit(_make_exit_event())
        _, kwargs = bot.send_message.call_args
        assert "청산 체결" in kwargs["text"]

    def test_text_contains_hhmmss(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_exit(_make_exit_event())
        _, kwargs = bot.send_message.call_args
        assert "14:30:00" in kwargs["text"]


# ---------------------------------------------------------------------------
# 7. notify_error — severity 대문자, stage 포함
# ---------------------------------------------------------------------------


class TestNotifyError:
    """notify_error 는 severity 를 대문자로, stage 를 텍스트에 포함한다."""

    def test_text_contains_stage(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_error(_make_error_event(stage="executor.step"))
        _, kwargs = bot.send_message.call_args
        assert "executor.step" in kwargs["text"]

    def test_severity_error_uppercase(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_error(_make_error_event(severity="error"))
        _, kwargs = bot.send_message.call_args
        assert "ERROR" in kwargs["text"]

    def test_severity_critical_uppercase(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_error(_make_error_event(severity="critical"))
        _, kwargs = bot.send_message.call_args
        assert "CRITICAL" in kwargs["text"]

    def test_text_contains_error_class(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_error(_make_error_event(error_class="ExecutorError"))
        _, kwargs = bot.send_message.call_args
        assert "ExecutorError" in kwargs["text"]

    def test_text_contains_message(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_error(_make_error_event(message="체결 타임아웃"))
        _, kwargs = bot.send_message.call_args
        assert "체결 타임아웃" in kwargs["text"]

    def test_text_contains_hhmmss(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_error(_make_error_event())
        _, kwargs = bot.send_message.call_args
        assert "14:30:00" in kwargs["text"]


# ---------------------------------------------------------------------------
# 8. notify_daily_summary — 각종 필드 포맷
# ---------------------------------------------------------------------------


class TestNotifyDailySummary:
    """notify_daily_summary 메시지 포맷 검증."""

    def test_text_contains_date(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_daily_summary(_make_daily_summary(session_date=date(2026, 4, 21)))
        _, kwargs = bot.send_message.call_args
        assert "2026-04-21" in kwargs["text"]

    def test_realized_pnl_pct_none_shows_na(self) -> None:
        """pct=None 이면 텍스트에 'n/a' 가 포함된다."""
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_daily_summary(_make_daily_summary(realized_pnl_pct=None))
        _, kwargs = bot.send_message.call_args
        assert "n/a" in kwargs["text"]

    def test_halted_true_shows_yes(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_daily_summary(_make_daily_summary(halted=True))
        _, kwargs = bot.send_message.call_args
        assert "yes" in kwargs["text"]

    def test_halted_false_shows_no(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_daily_summary(_make_daily_summary(halted=False))
        _, kwargs = bot.send_message.call_args
        assert "no" in kwargs["text"]

    def test_empty_mismatch_shows_없음(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_daily_summary(_make_daily_summary(mismatch_symbols=()))
        _, kwargs = bot.send_message.call_args
        assert "없음" in kwargs["text"]

    def test_mismatch_symbols_comma_joined(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_daily_summary(_make_daily_summary(mismatch_symbols=("005930", "000660")))
        _, kwargs = bot.send_message.call_args
        assert "005930" in kwargs["text"] and "000660" in kwargs["text"]

    def test_entries_today_in_text(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_daily_summary(_make_daily_summary(entries_today=3))
        _, kwargs = bot.send_message.call_args
        assert "3" in kwargs["text"]

    def test_realized_pnl_krw_in_text(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot)
        n.notify_daily_summary(_make_daily_summary(realized_pnl_krw=25_000))
        _, kwargs = bot.send_message.call_args
        text = kwargs["text"]
        assert "25000" in text or "25,000" in text


# ---------------------------------------------------------------------------
# 9. dry_run=True — 4종 메서드 모두 제목 맨 앞에 [DRY-RUN] 붙음
# ---------------------------------------------------------------------------


class TestDryRunPrefix:
    """dry_run=True 이면 각 메시지 제목 맨 앞에 '[DRY-RUN] ' 가 붙는다."""

    def test_entry_has_dry_run_prefix(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot, dry_run=True)
        n.notify_entry(_make_entry_event())
        _, kwargs = bot.send_message.call_args
        assert kwargs["text"].startswith("[DRY-RUN] [stock-agent]")

    def test_exit_has_dry_run_prefix(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot, dry_run=True)
        n.notify_exit(_make_exit_event())
        _, kwargs = bot.send_message.call_args
        assert kwargs["text"].startswith("[DRY-RUN] [stock-agent]")

    def test_error_has_dry_run_prefix(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot, dry_run=True)
        n.notify_error(_make_error_event())
        _, kwargs = bot.send_message.call_args
        assert kwargs["text"].startswith("[DRY-RUN] [stock-agent]")

    def test_daily_summary_has_dry_run_prefix(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot, dry_run=True)
        n.notify_daily_summary(_make_daily_summary())
        _, kwargs = bot.send_message.call_args
        assert kwargs["text"].startswith("[DRY-RUN] [stock-agent]")

    def test_no_dry_run_no_prefix(self) -> None:
        bot = _make_bot_mock()
        n = _make_notifier(bot=bot, dry_run=False)
        n.notify_entry(_make_entry_event())
        _, kwargs = bot.send_message.call_args
        assert not kwargs["text"].startswith("[DRY-RUN]")


# ---------------------------------------------------------------------------
# 10. 실패 처리 — TelegramError / asyncio.TimeoutError / 일반 Exception
# ---------------------------------------------------------------------------


class TestFailureHandling:
    """send_message 가 예외를 던져도 notify_* 는 재전파하지 않는다."""

    def _bot_raising(self, exc: type[BaseException] | BaseException) -> MagicMock:
        bot = _make_bot_mock()
        bot.send_message = AsyncMock(side_effect=exc)
        return bot

    def test_telegram_error_silent(self) -> None:
        """telegram.TelegramError 는 재전파되지 않는다."""
        try:
            from telegram.error import TelegramError
        except ImportError:
            pytest.skip("python-telegram-bot 미설치")
        bot = self._bot_raising(TelegramError("network fail"))
        n = _make_notifier(bot=bot)
        # 예외 없이 반환되어야 한다
        n.notify_entry(_make_entry_event())

    def test_asyncio_timeout_silent(self) -> None:
        """asyncio.TimeoutError 는 재전파되지 않는다."""
        bot = self._bot_raising(TimeoutError())
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event())

    def test_generic_exception_silent(self) -> None:
        """일반 Exception (ConnectionError 등) 은 재전파되지 않는다."""
        bot = self._bot_raising(ConnectionError("socket reset"))
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event())

    def test_failure_increments_consecutive_failures(self) -> None:
        """전송 실패 1회 → _consecutive_failures == 1."""
        bot = self._bot_raising(TimeoutError())
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event())
        assert n._consecutive_failures == 1

    def test_two_failures_increments_to_two(self) -> None:
        bot = self._bot_raising(TimeoutError())
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event())
        n.notify_entry(_make_entry_event())
        assert n._consecutive_failures == 2


# ---------------------------------------------------------------------------
# 11. 연속 실패 임계값 도달 — logger.critical 1회 dedupe
# ---------------------------------------------------------------------------


class TestConsecutiveFailureAlert:
    """threshold 도달 시 logger.critical 1회만 방출 (dedupe)."""

    def _notifier_always_failing(self, threshold: int = 3) -> tuple[TelegramNotifier, MagicMock]:
        bot = _make_bot_mock()
        bot.send_message = AsyncMock(side_effect=TimeoutError())
        n = _make_notifier(bot=bot, consecutive_failure_threshold=threshold)
        return n, bot

    def test_critical_emitted_at_threshold(self, mocker: MockerFixture) -> None:
        """threshold=3 이면 3번째 실패 시 logger.critical 가 호출된다."""
        mock_logger = mocker.patch("stock_agent.monitor.notifier.logger")
        n, _ = self._notifier_always_failing(threshold=3)
        for _ in range(3):
            n.notify_entry(_make_entry_event())
        mock_logger.critical.assert_called_once()

    def test_critical_not_emitted_before_threshold(self, mocker: MockerFixture) -> None:
        """threshold=3 이면 2번째 실패까지는 critical 미방출."""
        mock_logger = mocker.patch("stock_agent.monitor.notifier.logger")
        n, _ = self._notifier_always_failing(threshold=3)
        for _ in range(2):
            n.notify_entry(_make_entry_event())
        mock_logger.critical.assert_not_called()

    def test_critical_not_emitted_again_after_threshold(self, mocker: MockerFixture) -> None:
        """4번째 실패에서 critical 재방출 없음 (dedupe)."""
        mock_logger = mocker.patch("stock_agent.monitor.notifier.logger")
        n, _ = self._notifier_always_failing(threshold=3)
        for _ in range(4):
            n.notify_entry(_make_entry_event())
        # 총 1회만 호출되어야 한다
        mock_logger.critical.assert_called_once()


# ---------------------------------------------------------------------------
# 12. 성공 시 카운터·플래그 리셋
# ---------------------------------------------------------------------------


class TestSuccessResetsCounter:
    """전송 성공 시 _consecutive_failures 가 0 으로 리셋된다."""

    def test_success_after_failures_resets_counter(self) -> None:
        """2회 실패 후 1회 성공 → _consecutive_failures == 0."""
        bot = _make_bot_mock()
        # 처음 2회는 실패, 3회째는 성공
        bot.send_message = AsyncMock(side_effect=[TimeoutError(), TimeoutError(), None])
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event())  # fail 1
        n.notify_entry(_make_entry_event())  # fail 2
        assert n._consecutive_failures == 2
        n.notify_entry(_make_entry_event())  # success
        assert n._consecutive_failures == 0

    def test_counter_restarts_from_one_after_reset(self, mocker: MockerFixture) -> None:
        """리셋 후 다시 실패 시 카운터는 1부터 다시 시작 (threshold 재도달 시 critical 재방출)."""
        mock_logger = mocker.patch("stock_agent.monitor.notifier.logger")
        bot = _make_bot_mock()
        # fail, fail, success, fail, fail, fail → 두 번째 임계 도달
        bot.send_message = AsyncMock(
            side_effect=[
                TimeoutError(),
                TimeoutError(),
                None,  # reset
                TimeoutError(),
                TimeoutError(),
                TimeoutError(),  # 두 번째 threshold 도달
            ]
        )
        n = _make_notifier(bot=bot, consecutive_failure_threshold=3)
        for _ in range(2):
            n.notify_entry(_make_entry_event())
        n.notify_entry(_make_entry_event())  # success
        assert n._consecutive_failures == 0
        for _ in range(3):
            n.notify_entry(_make_entry_event())
        # 두 번째 임계 도달에서 critical 1회 방출
        assert mock_logger.critical.call_count == 1


# ---------------------------------------------------------------------------
# 13. logger.exception 호출 검증 (실패 시)
# ---------------------------------------------------------------------------


class TestLoggerExceptionOnFailure:
    """전송 실패 시 logger.exception 이 호출된다."""

    def test_logger_exception_called_on_telegram_error(self, mocker: MockerFixture) -> None:
        try:
            from telegram.error import TelegramError
        except ImportError:
            pytest.skip("python-telegram-bot 미설치")
        mock_logger = mocker.patch("stock_agent.monitor.notifier.logger")
        bot = _make_bot_mock()
        bot.send_message = AsyncMock(side_effect=TelegramError("fail"))
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event())
        mock_logger.exception.assert_called_once()

    def test_logger_exception_called_on_timeout(self, mocker: MockerFixture) -> None:
        mock_logger = mocker.patch("stock_agent.monitor.notifier.logger")
        bot = _make_bot_mock()
        bot.send_message = AsyncMock(side_effect=TimeoutError())
        n = _make_notifier(bot=bot)
        n.notify_entry(_make_entry_event())
        mock_logger.exception.assert_called_once()


# ---------------------------------------------------------------------------
# 14. ErrorEvent / DailySummary frozen dataclass 검증
# ---------------------------------------------------------------------------


class TestFrozenDataclasses:
    """ErrorEvent / DailySummary 는 frozen dataclass — 필드 변경 시 FrozenInstanceError."""

    def test_error_event_frozen(self) -> None:
        event = _make_error_event()
        with pytest.raises(dataclasses.FrozenInstanceError):
            event.stage = "other.stage"  # type: ignore[misc]  # 일반 setattr — frozen 가드 발동

    def test_daily_summary_frozen(self) -> None:
        summary = _make_daily_summary()
        with pytest.raises(dataclasses.FrozenInstanceError):
            summary.entries_today = 99  # type: ignore[misc]  # 일반 setattr — frozen 가드 발동

    def test_error_event_fields_exist(self) -> None:
        event = _make_error_event(stage="s", error_class="E", message="m", severity="critical")
        assert event.stage == "s"
        assert event.error_class == "E"
        assert event.message == "m"
        assert event.severity == "critical"
        assert isinstance(event.timestamp, datetime)

    def test_daily_summary_fields_exist(self) -> None:
        s = _make_daily_summary(
            session_date=_FIXED_DATE,
            starting_capital_krw=1_000_000,
            realized_pnl_krw=5_000,
            realized_pnl_pct=0.5,
            entries_today=1,
            halted=False,
            mismatch_symbols=("005930",),
        )
        assert s.session_date == _FIXED_DATE
        assert s.starting_capital_krw == 1_000_000
        assert s.realized_pnl_krw == 5_000
        assert s.realized_pnl_pct == pytest.approx(0.5)
        assert s.entries_today == 1
        assert s.halted is False
        assert s.mismatch_symbols == ("005930",)

    def test_daily_summary_pct_none_allowed(self) -> None:
        s = _make_daily_summary(realized_pnl_pct=None)
        assert s.realized_pnl_pct is None


# ---------------------------------------------------------------------------
# 15. _record_failure → stderr 2차 경보 (I1)
# ---------------------------------------------------------------------------


class _FailingStderr:
    """print(..., file=sys.stderr) 호출 시 예외를 던지는 페이크 stderr."""

    def write(self, _data: str) -> None:
        raise OSError("stderr broken")

    def flush(self) -> None:
        raise OSError("stderr broken")


class TestStderrSecondaryAlert:
    """_record_failure 임계값 도달 시 sys.stderr 에도 [CRITICAL] 한 줄 출력 (I1).

    현재 구현에는 stderr write 가 없으므로 모든 케이스가 FAIL 이어야 한다.
    """

    def _notifier_always_failing(self, threshold: int = 3) -> TelegramNotifier:
        bot = _make_bot_mock()
        bot.send_message = AsyncMock(side_effect=TimeoutError())
        return _make_notifier(bot=bot, consecutive_failure_threshold=threshold)

    def test_stderr_write_on_threshold_reached(self, capsys: pytest.CaptureFixture[str]) -> None:
        """threshold=3 에서 3번째 실패 시 stderr 에 [CRITICAL] 과 consecutive=3 이 포함된다."""
        n = self._notifier_always_failing(threshold=3)
        for _ in range(3):
            n._record_failure()
        captured = capsys.readouterr()
        assert "[CRITICAL]" in captured.err
        assert "telegram.notifier.persistent_failure" in captured.err
        assert "consecutive=3" in captured.err

    def test_stderr_not_written_before_threshold(self, capsys: pytest.CaptureFixture[str]) -> None:
        """threshold=3 에서 2회 실패까지는 stderr 에 경보 미출력."""
        n = self._notifier_always_failing(threshold=3)
        for _ in range(2):
            n._record_failure()
        captured = capsys.readouterr()
        assert "telegram.notifier.persistent_failure" not in captured.err

    def test_stderr_not_written_again_after_dedupe(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """4회 실패해도 stderr 경보는 정확히 1회만 출력된다 (dedupe 플래그)."""
        n = self._notifier_always_failing(threshold=3)
        for _ in range(4):
            n._record_failure()
        captured = capsys.readouterr()
        assert captured.err.count("telegram.notifier.persistent_failure") == 1

    def test_stderr_failure_silently_swallowed(
        self, mocker: MockerFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """sys.stderr 가 OSError 를 던져도 _record_failure 는 예외를 전파하지 않는다.

        logger.critical 은 여전히 호출된다.
        capsys 는 사용하지 않음 — monkeypatch 로 sys.stderr 자체를 교체.
        """
        import sys

        mock_logger = mocker.patch("stock_agent.monitor.notifier.logger")
        monkeypatch.setattr(sys, "stderr", _FailingStderr())
        n = self._notifier_always_failing(threshold=3)
        # 예외 전파 없이 완료되어야 한다
        for _ in range(3):
            n._record_failure()
        mock_logger.critical.assert_called_once()

    def test_stderr_write_resets_with_counter(self, capsys: pytest.CaptureFixture[str]) -> None:
        """threshold=2 에서 2회 실패 → 성공(리셋) → 다시 2회 실패 → stderr 재방출."""
        bot = _make_bot_mock()
        # 처음 2회 실패, 3회째 성공, 4~5회 실패
        bot.send_message = AsyncMock(
            side_effect=[TimeoutError(), TimeoutError(), None, TimeoutError(), TimeoutError()]
        )
        n = _make_notifier(bot=bot, consecutive_failure_threshold=2)
        # 1차 실패 사이클 (notify_entry 경유, _record_failure 2회)
        n.notify_entry(_make_entry_event())
        n.notify_entry(_make_entry_event())
        _ = capsys.readouterr()  # 1차 캡처 소비
        # 성공으로 리셋
        n.notify_entry(_make_entry_event())
        # 2차 실패 사이클
        n.notify_entry(_make_entry_event())
        n.notify_entry(_make_entry_event())
        captured_second = capsys.readouterr()
        assert "telegram.notifier.persistent_failure" in captured_second.err


# ---------------------------------------------------------------------------
# 16. _fmt_time tz-naive / non-KST 가드 (I2)
# ---------------------------------------------------------------------------


class TestFmtTimeGuards:
    """_fmt_time 이 naive datetime 경고·KST 변환을 처리하는지 검증 (I2).

    현재 구현에는 이 동작이 없으므로 대부분의 케이스가 FAIL 이어야 한다.
    """

    def test_naive_datetime_emits_warning_and_tz_suffix(self, mocker: MockerFixture) -> None:
        """naive datetime 을 전달하면 logger.warning 1회 + 반환값에 '(tz?)' 꼬리표."""
        mock_logger = mocker.patch("stock_agent.monitor.notifier.logger")
        n = _make_notifier()
        naive_dt = datetime(2026, 4, 21, 14, 30, 0)  # tzinfo=None
        result = n._fmt_time(naive_dt)
        mock_logger.warning.assert_called_once()
        assert result == "14:30:00 (tz?)"

    def test_naive_warning_deduped_within_instance(self, mocker: MockerFixture) -> None:
        """같은 notifier 에서 naive datetime 으로 _fmt_time 2회 호출 → warning 은 1회만."""
        mock_logger = mocker.patch("stock_agent.monitor.notifier.logger")
        n = _make_notifier()
        naive_dt = datetime(2026, 4, 21, 14, 30, 0)
        n._fmt_time(naive_dt)
        n._fmt_time(naive_dt)
        assert mock_logger.warning.call_count == 1

    def test_utc_aware_converted_to_kst(self, mocker: MockerFixture) -> None:
        """UTC+0 14:30 이 아닌 5:30 입력 → KST+9 = 14:30 으로 변환해 출력.

        현재 구현은 .astimezone(KST) 없이 strftime 만 하므로 '05:30:00' 반환 → FAIL.
        """
        mocker.patch("stock_agent.monitor.notifier.logger")
        n = _make_notifier()
        utc_dt = datetime(2026, 4, 21, 5, 30, 0, tzinfo=UTC)
        result = n._fmt_time(utc_dt)
        assert result == "14:30:00"
        assert "(tz?)" not in result

    def test_kst_aware_unchanged(self, mocker: MockerFixture) -> None:
        """KST aware datetime 은 그대로 HH:MM:SS 반환 — (tz?) 꼬리표 없음, warning 미호출."""
        mock_logger = mocker.patch("stock_agent.monitor.notifier.logger")
        n = _make_notifier()
        result = n._fmt_time(_FIXED_DT)  # _FIXED_DT = datetime(2026,4,21,14,30,0, tzinfo=KST)
        assert result == "14:30:00"
        assert "(tz?)" not in result
        mock_logger.warning.assert_not_called()

    def test_non_kst_non_utc_aware_converted_to_kst(self, mocker: MockerFixture) -> None:
        """EST(UTC-5) 0:30 → KST +9 = 14:30 으로 변환해 출력.

        현재 구현은 변환 없이 strftime 이므로 '00:30:00' 반환 → FAIL.
        """
        mocker.patch("stock_agent.monitor.notifier.logger")
        n = _make_notifier()
        est = timezone(timedelta(hours=-5))
        est_dt = datetime(2026, 4, 21, 0, 30, 0, tzinfo=est)
        result = n._fmt_time(est_dt)
        assert result == "14:30:00"
        assert "(tz?)" not in result
