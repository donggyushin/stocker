"""KIS Developers API 래퍼 (`python-kis 2.x`).

상위 레이어에 정규화된 DTO(`BalanceSnapshot`/`OrderTicket`/`PendingOrder`) 만
노출하고, 라이브러리 반환 객체는 감춘다. 테스트에서 네트워크/실주문을
원천 차단하기 위해 `pykis_factory` 의존성 주입을 지원한다.

환경 분기
- `kis_env == "paper"`: PyKis 실전/모의 슬롯 양쪽에 모의 키를 동일 주입하고
  `install_paper_mode_guard` 를 설치한다. paper-only 라이브러리 초기화를
  우회하는 현행 정책(`scripts/healthcheck.py` 참조)을 그대로 따른다.
- `kis_env == "live"`: 현 시점에는 `Settings` 에 실전 전용 키 필드가 없으므로
  즉시 `NotImplementedError`. Phase 4 실전 전환 시 Settings 확장과 함께 구현.

에러 정책
- 공개 메서드는 라이브러리 예외를 `KisClientError` 로 래핑하여 `raise ... from e`
  (loguru 로 원본 트레이스 로그).
- `RuntimeError` 는 래핑하지 않고 그대로 전파한다. `install_paper_mode_guard`
  가 던지는 "paper 모드에서 실전 도메인 호출 차단" 같은 설정 오류는 재시도
  대상이 아니라 상위에서 즉시 실패해야 하기 때문.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import TracebackType
from typing import Any, Literal

from loguru import logger

from stock_agent.config import Settings
from stock_agent.safety import install_paper_mode_guard

PyKisFactory = Callable[..., Any]
"""`PyKis` 생성자와 호환되는 팩토리 타입. 테스트는 `MagicMock` 반환 팩토리를 주입."""


class KisClientError(Exception):
    """KIS API 호출 실패를 공통적으로 표현하는 에러.

    python-kis 의 구체 예외 타입이 상위 레이어로 누출되지 않도록 래핑한다.
    원본 예외는 `__cause__` 로 보존된다 (`raise ... from e`).
    """


@dataclass(frozen=True, slots=True)
class Holding:
    """보유 종목 1건."""

    symbol: str
    qty: int
    avg_price: Decimal
    current_price: Decimal | None


@dataclass(frozen=True, slots=True)
class BalanceSnapshot:
    """계좌 잔고 스냅샷."""

    withdrawable: int
    total: int
    holdings_count: int
    holdings: tuple[Holding, ...]
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class OrderTicket:
    """주문 제출 결과 티켓. 체결 확정 여부와 무관한 접수 기록."""

    order_number: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    price: int | None  # None = 시장가
    submitted_at: datetime


@dataclass(frozen=True, slots=True)
class PendingOrder:
    """미체결(또는 부분체결 잔량) 주문 1건."""

    order_number: str
    symbol: str
    side: Literal["buy", "sell"]
    qty_ordered: int
    qty_remaining: int
    price: int | None
    submitted_at: datetime


class KisClient:
    """KIS Developers API 얇은 래퍼.

    공개 메서드는 `ensure_token`, `get_balance`, `place_buy`, `place_sell`,
    `get_pending_orders`, `close` + 컨텍스트 매니저. 주문 취소와 rate limiter 는
    후속 PR 범위.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        pykis_factory: PyKisFactory | None = None,
    ) -> None:
        """
        Args:
            settings: `.env` 에서 읽어온 검증된 설정.
            pykis_factory: `PyKis` 와 동일한 키워드 인자 계약을 만족하는 팩토리.
                테스트에서 `lambda **kw: MagicMock()` 을 주입. `None` 이면 실제
                `pykis.PyKis` 를 지연 import 해 사용.
        """
        self._settings = settings
        self._closed = False
        self._kis = self._build_pykis(pykis_factory)

    def _build_pykis(self, factory: PyKisFactory | None) -> Any:
        if self._settings.kis_env == "live":
            raise NotImplementedError(
                "live 분기는 Phase 4 에서 Settings 에 kis_live_app_key/secret 필드 "
                "추가 후 구현 예정. 현재는 paper 모드만 지원한다."
            )

        if factory is None:
            # 지연 import: 테스트가 pykis_factory 를 주입하면 실제 pykis 를 import 하지 않는다.
            from pykis import PyKis  # noqa: PLC0415

            factory = PyKis

        appkey = self._settings.kis_app_key.get_secret_value()
        secret = self._settings.kis_app_secret.get_secret_value()
        kis = factory(
            id=self._settings.kis_hts_id,
            account=self._settings.kis_account_no,
            appkey=appkey,
            secretkey=secret,
            virtual_id=self._settings.kis_hts_id,
            virtual_appkey=appkey,
            virtual_secretkey=secret,
            keep_token=True,
            use_websocket=False,
        )
        install_paper_mode_guard(kis)
        return kis

    def _require_open(self) -> None:
        if self._closed:
            raise KisClientError("KisClient 는 이미 close() 되었습니다. 새 인스턴스를 생성하세요.")

    def _call(self, label: str, fn: Callable[[], Any]) -> Any:
        """공개 메서드 공통 에러 래핑 헬퍼.

        `RuntimeError` 는 전파(paper guard 등 설정 오류), 그 외 `Exception` 은
        `KisClientError` 로 래핑하고 loguru 로 원본 트레이스를 남긴다.
        """
        self._require_open()
        try:
            return fn()
        except RuntimeError:
            raise
        except Exception as e:
            logger.exception(f"{label} 실패: {e.__class__.__name__}: {e}")
            raise KisClientError(f"{label} 실패: {e.__class__.__name__}: {e}") from e

    # ---- 공개 API -------------------------------------------------------

    def ensure_token(self) -> None:
        """토큰 상태 점검/갱신.

        현재는 `keep_token=True` 로 python-kis 가 `~/.pykis/` 아래 자동 캐시·
        갱신하므로 no-op. 라이브러리가 명시적 refresh API 를 노출하면 여기서
        호출하도록 인터페이스만 확보해둔다.
        """
        self._require_open()

    def get_balance(self) -> BalanceSnapshot:
        """잔고 조회 후 `BalanceSnapshot` 으로 정규화."""
        balance = self._call("잔고 조회", lambda: self._kis.account().balance())
        stocks_raw = getattr(balance, "stocks", ()) or ()
        holdings = tuple(_to_holding(s) for s in stocks_raw)
        return BalanceSnapshot(
            withdrawable=int(balance.withdrawable_amount),
            total=int(balance.total),
            holdings_count=len(holdings),
            holdings=holdings,
            fetched_at=datetime.now(UTC),
        )

    def place_buy(self, symbol: str, qty: int, price: int | None = None) -> OrderTicket:
        """매수 주문. `price=None` 이면 시장가."""
        return self._place_order("buy", symbol, qty, price)

    def place_sell(self, symbol: str, qty: int, price: int | None = None) -> OrderTicket:
        """매도 주문. `price=None` 이면 시장가."""
        return self._place_order("sell", symbol, qty, price)

    def _place_order(
        self,
        side: Literal["buy", "sell"],
        symbol: str,
        qty: int,
        price: int | None,
    ) -> OrderTicket:
        if qty <= 0:
            raise KisClientError(f"주문 수량은 양의 정수여야 합니다 (qty={qty})")

        def _do() -> Any:
            account = self._kis.account()
            method = account.buy if side == "buy" else account.sell
            # python-kis 2.x: price=None 이면 시장가 분기로 매핑됨.
            return method(market="KRX", symbol=symbol, qty=qty, price=price)

        order = self._call(f"{side} 주문 제출", _do)
        raw_number = getattr(order, "number", None)
        order_number = str(raw_number) if raw_number else ""
        if not order_number:
            # 주문번호가 없으면 체결·취소·재조회 경로가 끊겨 유령 포지션이 된다.
            # 상위 레이어가 "주문 성공" 으로 오인하지 않도록 명시적 실패 처리.
            raise KisClientError(
                f"{side} 주문 제출은 성공했으나 주문번호가 비어 있음 — 체결/취소 추적 불가. "
                f"symbol={symbol}, qty={qty}, price={price}"
            )
        return OrderTicket(
            order_number=order_number,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            submitted_at=datetime.now(UTC),
        )

    def get_pending_orders(self) -> list[PendingOrder]:
        """미체결 주문 조회 후 `PendingOrder` 리스트로 정규화."""
        result = self._call("미체결 주문 조회", lambda: self._kis.account().pending_orders())
        if result is None:
            return []
        return [_to_pending_order(o) for o in result]

    def close(self) -> None:
        """리소스 정리. 멱등. 라이브러리 close 실패는 warning 로그만."""
        if self._closed:
            return
        self._closed = True
        close_method = getattr(self._kis, "close", None)
        if not callable(close_method):
            return
        try:
            close_method()
        except Exception as e:  # noqa: BLE001 — close 실패는 부수 정보로만 기록
            logger.warning(f"PyKis close 중 예외 발생 (무시): {e!r}")

    def __enter__(self) -> KisClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # close 실패가 원본 예외를 가리지 않도록 close 내부에서 swallow.
        self.close()


# ---- 내부 변환기 --------------------------------------------------------


def _to_holding(stock: Any) -> Holding:
    """python-kis `KisBalanceStock` 유사 객체 → `Holding` 변환."""
    avg_raw = getattr(stock, "price", None)
    avg_price = Decimal(str(avg_raw)) if avg_raw is not None else Decimal("0")
    cur_raw = getattr(stock, "current_price", None)
    current_price = Decimal(str(cur_raw)) if cur_raw is not None else None
    return Holding(
        symbol=str(getattr(stock, "symbol", "")),
        qty=int(getattr(stock, "qty", 0)),
        avg_price=avg_price,
        current_price=current_price,
    )


def _to_pending_order(order: Any) -> PendingOrder:
    """python-kis `KisOrder` 유사 객체 → `PendingOrder` 변환.

    python-kis 의 미체결 표현은 속성명이 증권사 응답 필드에 따라 변동 가능성이
    있어 `getattr` 로 방어적으로 접근한다. 단 `side` 판별 실패는 **절대**
    조용히 넘기지 않는다 — 매도 미체결을 매수로 오인하면 상위 리스크·청산
    로직이 포지션을 잘못 집계해 자금 손실로 직결되기 때문. `KisClientError`
    로 실패시켜 상위에서 재조회·중단을 강제한다.
    """
    side_raw = getattr(order, "side", None)
    if side_raw not in ("buy", "sell"):
        raise KisClientError(
            "미체결 주문 side 판별 불가 — 매수/매도 오인 방지를 위해 실패 처리. "
            f"order_number={getattr(order, 'number', '?')!r}, side_raw={side_raw!r}"
        )
    side: Literal["buy", "sell"] = side_raw

    price_raw = getattr(order, "price", None)
    price: int | None = None if price_raw in (None, 0) else int(price_raw)

    submitted_raw = getattr(order, "time", None) or getattr(order, "created_at", None)
    submitted_at = submitted_raw if isinstance(submitted_raw, datetime) else datetime.now(UTC)

    qty = int(getattr(order, "qty", 0))
    remaining = int(getattr(order, "qty_remaining", qty))

    return PendingOrder(
        order_number=str(getattr(order, "number", "") or ""),
        symbol=str(getattr(order, "symbol", "")),
        side=side,
        qty_ordered=qty,
        qty_remaining=remaining,
        price=price,
        submitted_at=submitted_at,
    )
