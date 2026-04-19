"""Phase 0 healthcheck — KIS 모의 잔고 조회 + 텔레그램 hello 알림.

실주문 API(매수/매도/취소) 는 호출하지 않는다. paper 모드 전용이며, `KisClient`
생성 시 내부적으로 `install_paper_mode_guard` 가 설치되어 `request(domain="real")`
호출도 차단된다. live 모드에서는 `KisClient.__init__` 이 `NotImplementedError` 를
즉시 발생시키므로, 여기서도 명시적으로 paper 전용 스크립트임을 방어적으로 체크한다.
"""

from __future__ import annotations

import asyncio
import sys

from loguru import logger
from telegram import Bot
from telegram.error import TelegramError

from stock_agent.broker import KisClient
from stock_agent.config import Settings, get_settings


def check_kis_balance(settings: Settings) -> str:
    """모의 계좌 잔고를 조회해 요약 문자열로 반환.

    `KisClient` 가 PyKis 초기화·paper guard 설치·close 까지 책임지므로,
    여기서는 컨텍스트 매니저로 잔고 DTO(`BalanceSnapshot`) 만 꺼낸다.
    """
    if settings.kis_env != "paper":
        raise RuntimeError(f"healthcheck 는 paper 모드 전용. 현재 KIS_ENV={settings.kis_env}")
    with KisClient(settings) as kc:
        snapshot = kc.get_balance()
    return (
        f"예수금 {snapshot.withdrawable:,}원 / "
        f"평가총액 {snapshot.total:,}원 / "
        f"보유종목 {snapshot.holdings_count}건"
    )


async def _send_telegram(token: str, chat_id: int, text: str) -> None:
    bot = Bot(token=token)
    async with bot:
        await bot.send_message(chat_id=chat_id, text=text)


def send_telegram_hello(settings: Settings, body: str) -> None:
    text = f"[stock-agent] healthcheck OK\n{body}"
    asyncio.run(
        _send_telegram(
            token=settings.telegram_bot_token.get_secret_value(),
            chat_id=settings.telegram_chat_id,
            text=text,
        )
    )


def main() -> int:
    try:
        settings = get_settings()
    except Exception as e:
        logger.exception(f".env 로드 실패: {e}")
        return 1

    try:
        summary = check_kis_balance(settings)
        logger.info(f"KIS 잔고 조회 OK — {summary}")
    except Exception as e:
        logger.exception(f"KIS 잔고 조회 단계 실패: {e}")
        return 1

    try:
        send_telegram_hello(settings, summary)
        logger.info("Telegram 메시지 전송 OK")
    except TelegramError as e:
        logger.exception(f"Telegram 전송 실패 (Telegram API 오류): {e}")
        return 1
    except Exception as e:
        # 네트워크 단절 / asyncio 타임아웃 / SSL 등 비-TelegramError 도 모두 1로.
        logger.exception(f"Telegram 전송 실패 (네트워크/런타임 오류): {e}")
        return 1

    logger.info("healthcheck 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
