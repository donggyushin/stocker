"""healthcheck — paper 잔고 + (선택) 실시간 시세 + 텔레그램 알림 점검.

체크 4종
1. `.env` 로드 + KIS paper 토큰 발급 + 모의 계좌 잔고 조회
2. (실전 키 주입 시) RealtimeDataStore 로 삼성전자(005930) 현재가 수신
3. 텔레그램 "hello" 메시지 전송

실주문 API(매수/매도/취소)는 호출하지 않는다. `KisClient` 는 paper 키로 paper
도메인만 사용하고, `RealtimeDataStore` 는 실전 키로 read-only 경로만 사용하며
`install_order_block_guard` 가 `/trading/order*` 를 도메인 무관 차단한다.

실전 키 미설정 시
- 실시간 시세 체크는 SKIP 으로 기록하고 나머지는 계속 진행한다. Phase 1 단계에서
  실전 키 발급 전이라도 잔고/텔레그램 회귀는 검증되어야 하기 때문.

IP 화이트리스트
- KIS 실전 앱은 사전 등록한 IP 에서만 인증 허용. 권한 에러(예: `EGW00123`)를
  감지하면 힌트 로그를 남긴다. 실제 해결은 KIS Developers 포털에서 현재 공인
  IP 를 앱 설정에 추가하는 것.
"""

from __future__ import annotations

import asyncio
import sys
import time

from loguru import logger
from telegram import Bot
from telegram.error import TelegramError

from stock_agent.broker import KisClient
from stock_agent.config import Settings, get_settings
from stock_agent.data import RealtimeDataStore

_PRICE_PROBE_SYMBOL = "005930"  # 삼성전자 — Phase 1 PASS 기준 종목
_PRICE_PROBE_WAIT_S = 2.0
_IP_WHITELIST_ERROR_HINTS = ("EGW00123", "IP", "접근이 허용되지 않")


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


def check_realtime_price(settings: Settings) -> str:
    """삼성전자 현재가를 실시간 경로로 조회해 요약 문자열로 반환.

    `RealtimeDataStore` 가 실전 키로 real 도메인 PyKis 를 생성해 WebSocket
    연결을 시도하고, 실패 시 REST 폴링으로 폴백한다. 어느 경로든 최초 틱 수신
    까지 약간의 지연이 있어 `wait` 동안 `get_current_price` 를 폴링한다.
    """
    with RealtimeDataStore(settings) as rt:
        rt.start()
        rt.subscribe(_PRICE_PROBE_SYMBOL)
        logger.info(f"RealtimeDataStore 가동 — mode={rt.mode}")
        deadline = time.monotonic() + _PRICE_PROBE_WAIT_S
        tick = None
        while time.monotonic() < deadline:
            tick = rt.get_current_price(_PRICE_PROBE_SYMBOL)
            if tick is not None:
                break
            time.sleep(0.1)
        if tick is None:
            raise RuntimeError(
                f"{_PRICE_PROBE_SYMBOL} 현재가를 {_PRICE_PROBE_WAIT_S}s 내에 받지 못했습니다. "
                f"(mode={rt.mode})"
            )
        return f"{_PRICE_PROBE_SYMBOL} 현재가 {tick.price:,}원 (mode={rt.mode})"


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


def _maybe_log_ip_whitelist_hint(error_text: str) -> None:
    """에러 메시지에 IP 화이트리스트 관련 힌트가 보이면 가이드를 로그한다."""
    if any(hint in error_text for hint in _IP_WHITELIST_ERROR_HINTS):
        logger.error(
            "힌트: KIS 실전 앱 IP 화이트리스트 불일치 가능성. "
            "KIS Developers 포털 → 앱 관리 → 허용 IP 목록에 현재 공인 IP 를 추가 후 재시도."
        )


def main() -> int:
    try:
        settings = get_settings()
    except Exception as e:
        logger.exception(f".env 로드 실패: {e}")
        return 1

    try:
        balance_summary = check_kis_balance(settings)
        logger.info(f"KIS 잔고 조회 OK — {balance_summary}")
    except Exception as e:
        logger.exception(f"KIS 잔고 조회 단계 실패: {e}")
        return 1

    # 실시간 시세 체크는 실전 키 주입 시에만 수행 — 미설정은 skip.
    if settings.has_live_keys:
        try:
            price_summary = check_realtime_price(settings)
            logger.info(f"실시간 현재가 조회 OK — {price_summary}")
        except Exception as e:
            logger.exception(f"실시간 현재가 조회 단계 실패: {e}")
            _maybe_log_ip_whitelist_hint(str(e))
            return 1
    else:
        price_summary = "실시간 현재가 조회 SKIP (KIS_LIVE_* 미설정)"
        logger.warning(price_summary)

    summary = f"{balance_summary} / {price_summary}"

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
