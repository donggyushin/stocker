"""장중 실시간 체결가 공급 (WebSocket 우선 + REST 폴링 fallback).

책임 범위
- 구독 종목의 최신 체결가(`TickQuote`) 스냅샷 제공
- 틱을 분 경계로 집계한 `MinuteBar` 시퀀스 제공 (완성분 + 진행중분)

범위 제외 (의도적)
- 자동 재접속/재폴백: Phase 3 스케줄러 책임
- 과거 분봉 백필: pykrx 미지원 (일봉은 `historical.py`)
- 호가(bid/ask)·잔량: 체결가 중심, Phase 5 범위
- 거래량 델타 정규화: WebSocket 의 누적 거래량 해석은 Phase 3 에서 실사 후 확정

에러 정책 (broker/historical 과 동일 기조)
- `RuntimeError` 는 전파 (paper guard 등 설정 오류 — 재시도 대상 아님)
- 그 외 `Exception` 은 `RealtimeDataError` 로 래핑 + loguru `exception` 로그
- 백그라운드 스레드(폴링 루프·WebSocket 콜백) 내부 예외는 상위로 전파하지 않고
  loguru 에 기록한다 — 단일 종목 에러로 다른 종목 구독이 끊기지 않게.

모드 전환
- `start()` 에서 WebSocket `ensure_connected` 시도 → 실패 시 폴링 확정.
- 성공/실패 후 mode 는 고정. 장중 재협상은 defer (Phase 1 산출물 범위 밖).

스레드 모델
- 단일 프로세스 전용. 공유 상태는 `threading.Lock` 으로 보호.
- WebSocket 콜백은 python-kis 가 백그라운드 스레드에서 호출.
- 폴링은 자체 데몬 스레드 1개가 전 구독 종목을 `polling_interval_s` 주기로 훑는다.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import TracebackType
from typing import Any, Literal

from loguru import logger

from stock_agent.config import Settings
from stock_agent.safety import install_order_block_guard

PyKisFactory = Callable[..., Any]
"""`PyKis` 생성자와 호환되는 팩토리 타입. 테스트는 `MagicMock` 반환 팩토리를 주입."""

ClockFn = Callable[[], datetime]
"""현재 시각 제공자. 테스트 결정론화를 위해 주입 가능. KST aware datetime 기대."""

KST = timezone(timedelta(hours=9))
_SYMBOL_RE = re.compile(r"^\d{6}$")
_DEFAULT_POLLING_INTERVAL_S = 1.0
_DEFAULT_WS_CONNECT_TIMEOUT_S = 5.0

Mode = Literal["idle", "websocket", "polling"]


class RealtimeDataError(Exception):
    """실시간 시세 수집 실패를 공통 표현.

    python-kis 의 구체 예외 타입이 상위 레이어로 누출되지 않도록 래핑한다.
    원본 예외는 `__cause__` 로 보존된다 (`raise ... from e`).
    """


@dataclass(frozen=True, slots=True)
class TickQuote:
    """종목의 최신 체결가 스냅샷. 공개 getter 반환용."""

    symbol: str
    price: Decimal
    ts: datetime  # KST aware


@dataclass(frozen=True, slots=True)
class MinuteBar:
    """분봉 1건. 틱을 분 경계로 집계한 형태.

    `bar_time` 은 분의 시작 시각(KST, aware). `volume` 은 현재 Phase 1 에서 0 고정
    (python-kis `KisRealtimePrice.volume` 의 누적/델타 의미를 Phase 3 에서 실사 후 확정).
    """

    symbol: str
    bar_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass
class _BarAccumulator:
    """종목별 틱 누적 버퍼. 락 보호 하에 갱신된다."""

    last_tick: TickQuote | None = None
    current_bar: MinuteBar | None = None
    closed_bars: list[MinuteBar] = field(default_factory=list)
    subscription_handle: Any | None = None  # KisEventTicket 류. 폴링 모드에서는 None.


class RealtimeDataStore:
    """실시간 체결가 공급 스토어.

    공개 API: `start`, `subscribe`, `unsubscribe`, `get_current_price`,
    `get_minute_bars`, `get_current_bar`, `close`, `mode` + 컨텍스트 매니저.

    단일 프로세스 전용. `KisClient` 와 별도의 `PyKis` 인스턴스를 생성하며
    (`use_websocket=True`), paper 모드에서는 `install_paper_mode_guard` 를 동일
    설치해 실전 도메인 접촉을 차단한다.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        pykis_factory: PyKisFactory | None = None,
        clock: ClockFn | None = None,
        polling_interval_s: float = _DEFAULT_POLLING_INTERVAL_S,
        ws_connect_timeout_s: float = _DEFAULT_WS_CONNECT_TIMEOUT_S,
    ) -> None:
        """
        Args:
            settings: `.env` 에서 읽어온 검증된 설정. `KisClient` 와 동일 규약.
            pykis_factory: `PyKis` 와 호환되는 팩토리. `None` 이면 지연 import.
            clock: KST aware `datetime` 반환자. `None` 이면 `datetime.now(KST)`.
            polling_interval_s: 폴백 모드 폴링 주기 (초). 기본 1.0.
            ws_connect_timeout_s: WebSocket 연결 대기 타임아웃 (초). 기본 5.0.
        """
        if polling_interval_s <= 0:
            raise RealtimeDataError(
                f"polling_interval_s 는 양수여야 합니다 (got={polling_interval_s})"
            )
        if ws_connect_timeout_s <= 0:
            raise RealtimeDataError(
                f"ws_connect_timeout_s 는 양수여야 합니다 (got={ws_connect_timeout_s})"
            )

        self._settings = settings
        self._pykis_factory = pykis_factory
        self._clock: ClockFn = clock or (lambda: datetime.now(KST))
        self._polling_interval_s = polling_interval_s
        self._ws_connect_timeout_s = ws_connect_timeout_s

        self._lock = threading.Lock()
        self._accumulators: dict[str, _BarAccumulator] = {}
        self._mode: Mode = "idle"
        self._started = False
        self._closed = False

        self._kis: Any | None = None
        self._stop_event = threading.Event()
        self._polling_thread: threading.Thread | None = None

    # ---- 수명 주기 ------------------------------------------------------

    @property
    def mode(self) -> Mode:
        """현재 구동 모드. `start()` 전에는 `"idle"`."""
        return self._mode

    def start(self) -> None:
        """PyKis 인스턴스 생성 + WebSocket 연결 시도. 실패 시 폴링 모드로 확정.

        멱등하지 않다 — 두 번째 호출은 `RealtimeDataError`. 모드는 확정 후 고정.
        """
        self._require_open()
        if self._started:
            raise RealtimeDataError("RealtimeDataStore.start() 는 1회만 호출 가능합니다.")
        self._started = True

        self._kis = self._build_pykis()

        # WebSocket 먼저 시도. 실패하면 폴링 스레드 기동.
        if self._try_connect_websocket():
            self._mode = "websocket"
            logger.info("RealtimeDataStore: WebSocket 모드로 시작")
        else:
            self._mode = "polling"
            self._polling_thread = threading.Thread(
                target=self._polling_loop,
                name="stock-agent-realtime-polling",
                daemon=True,
            )
            self._polling_thread.start()
            logger.warning(
                f"RealtimeDataStore: 폴링 모드로 시작 (interval={self._polling_interval_s}s)"
            )

    def close(self) -> None:
        """백그라운드 스레드·WebSocket·PyKis 리소스를 순서대로 정리. 멱등."""
        if self._closed:
            return
        self._closed = True

        self._stop_event.set()

        # 구독 취소 (WebSocket 경로에만 유효).
        with self._lock:
            accumulators = list(self._accumulators.values())
        for acc in accumulators:
            handle = acc.subscription_handle
            if handle is None:
                continue
            unsubscribe = getattr(handle, "unsubscribe", None)
            if callable(unsubscribe):
                try:
                    unsubscribe()
                except Exception as e:  # noqa: BLE001 — close 경로 부수 정보
                    logger.warning(f"WebSocket 구독 해제 중 예외 (무시): {e!r}")

        # WebSocket 연결 해제.
        if self._kis is not None:
            ws = getattr(self._kis, "websocket", None)
            disconnect = getattr(ws, "disconnect", None) if ws is not None else None
            if callable(disconnect):
                try:
                    disconnect()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"WebSocket disconnect 중 예외 (무시): {e!r}")

            close_kis = getattr(self._kis, "close", None)
            if callable(close_kis):
                try:
                    close_kis()
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"PyKis close 중 예외 (무시): {e!r}")

        # 폴링 스레드 종료 대기 (최대 polling_interval + 약간의 여유).
        if self._polling_thread is not None and self._polling_thread.is_alive():
            self._polling_thread.join(timeout=self._polling_interval_s + 1.0)

    def __enter__(self) -> RealtimeDataStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---- 공개 API: 구독 -------------------------------------------------

    def subscribe(self, symbol: str) -> None:
        """`symbol` 을 구독 목록에 추가. 이미 구독 중이면 no-op.

        WebSocket 모드면 즉시 이벤트 핸들러를 등록한다. 폴링 모드면 다음 틱에
        반영된다. `start()` 전 호출은 허용되며, `start()` 호출 시 WebSocket
        경로가 확정되면 사후 등록된다.
        """
        self._require_open()
        self._validate_symbol(symbol)

        with self._lock:
            if symbol in self._accumulators:
                return
            self._accumulators[symbol] = _BarAccumulator()

        if self._mode == "websocket":
            self._attach_ws_subscription(symbol)

    def unsubscribe(self, symbol: str) -> None:
        """`symbol` 을 구독 해제. 미구독이면 no-op."""
        self._require_open()
        with self._lock:
            acc = self._accumulators.pop(symbol, None)
        if acc is None:
            return
        handle = acc.subscription_handle
        if handle is None:
            return
        unsubscribe = getattr(handle, "unsubscribe", None)
        if callable(unsubscribe):
            try:
                unsubscribe()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"unsubscribe({symbol}) 중 예외 (무시): {e!r}")

    # ---- 공개 API: 조회 -------------------------------------------------

    def get_current_price(self, symbol: str) -> TickQuote | None:
        """`symbol` 의 최신 체결가 스냅샷. 구독 전·틱 없음 → `None`."""
        self._require_open()
        with self._lock:
            acc = self._accumulators.get(symbol)
            return None if acc is None else acc.last_tick

    def get_current_bar(self, symbol: str) -> MinuteBar | None:
        """`symbol` 의 진행 중 분봉. 구독 전·틱 없음 → `None`."""
        self._require_open()
        with self._lock:
            acc = self._accumulators.get(symbol)
            return None if acc is None else acc.current_bar

    def get_minute_bars(self, symbol: str) -> list[MinuteBar]:
        """`symbol` 의 완성된 분봉 리스트(시간순). 구독 전 → 빈 리스트.

        반환값은 복사본이므로 호출자가 자유롭게 수정해도 내부 상태에 영향 없다.
        """
        self._require_open()
        with self._lock:
            acc = self._accumulators.get(symbol)
            if acc is None:
                return []
            return list(acc.closed_bars)

    # ---- 내부: PyKis 생성/WebSocket 시도 --------------------------------

    def _build_pykis(self) -> Any:
        """실전(live) 키 3종으로 read-only PyKis 를 생성한다.

        paper 키로는 real 도메인 시세 API(`/quotations/*`) 를 호출할 수 없다
        (KIS 서버가 EGW02004 로 거부). 따라서 `RealtimeDataStore` 는 **실전 키
        전용**으로 동작하고, `KisClient`(주문/잔고) 와 완전히 분리된 인스턴스를
        사용한다. 실전 키가 주입되지 않았다면 `RealtimeDataError` 로 fail-fast.

        안전 벨트
        - `install_order_block_guard` 로 주문 경로(`/trading/order*`) 는 도메인
          무관 차단. 이 PyKis 인스턴스로는 시세·종목정보 조회만 가능.
        - `virtual_*` 슬롯은 비워 PyKis 가 paper 로 오라우팅할 여지를 제거.
        """
        if not self._settings.has_live_keys:
            raise RealtimeDataError(
                "RealtimeDataStore 는 실전 APP_KEY 가 필요합니다. "
                ".env 에 KIS_LIVE_APP_KEY · KIS_LIVE_APP_SECRET · KIS_LIVE_ACCOUNT_NO 3종을 "
                "설정하세요. paper 도메인에는 시세 API 가 없어 실전 키로 real 도메인을 "
                "호출해야 하며, paper 키로는 real 도메인 인증이 거부됩니다(EGW02004)."
            )

        factory = self._pykis_factory
        if factory is None:
            from pykis import PyKis  # noqa: PLC0415

            factory = PyKis

        # 3종 일괄 None 검증은 Settings `has_live_keys` 에서 통과했으므로 안전.
        # HTS_ID 는 paper/실전 동일이라 `kis_hts_id` 공유.
        assert self._settings.kis_live_app_key is not None
        assert self._settings.kis_live_app_secret is not None
        assert self._settings.kis_live_account_no is not None

        live_appkey = self._settings.kis_live_app_key.get_secret_value()
        live_secret = self._settings.kis_live_app_secret.get_secret_value()
        kis = factory(
            id=self._settings.kis_hts_id,
            account=self._settings.kis_live_account_no,
            appkey=live_appkey,
            secretkey=live_secret,
            keep_token=True,
            use_websocket=True,
        )
        install_order_block_guard(kis)
        return kis

    def _try_connect_websocket(self) -> bool:
        """WebSocket 연결을 시도한다. 성공 시 True, 실패 시 False + 경고 로그."""
        assert self._kis is not None
        ws = getattr(self._kis, "websocket", None)
        if ws is None:
            logger.warning("PyKis 인스턴스에 websocket 속성이 없어 폴링 모드로 폴백")
            return False
        ensure_connected = getattr(ws, "ensure_connected", None)
        if not callable(ensure_connected):
            logger.warning("PyKis.websocket.ensure_connected 가 callable 아니어서 폴링 모드로 폴백")
            return False
        try:
            ensure_connected(timeout=self._ws_connect_timeout_s)
        except Exception as e:  # noqa: BLE001 — 원인과 무관하게 폴백이 정상 경로
            logger.warning(f"WebSocket 연결 실패 → 폴링 모드로 폴백: {e.__class__.__name__}: {e}")
            return False

        # 구독 전 `subscribe()` 된 심볼이 있으면 사후 등록.
        with self._lock:
            symbols = list(self._accumulators.keys())
        for sym in symbols:
            self._attach_ws_subscription(sym)
        return True

    def _attach_ws_subscription(self, symbol: str) -> None:
        """WebSocket 경로에서 `symbol` 에 `on("price", cb)` 핸들러를 등록."""
        assert self._kis is not None
        try:
            stock = self._kis.stock(symbol)
            handle = stock.on("price", lambda *args, **kwargs: self._ws_callback(symbol, args))
        except Exception as e:
            logger.exception(f"WebSocket 구독 등록 실패 ({symbol}): {e.__class__.__name__}: {e}")
            # 단일 종목 실패로 전체를 폴백시키지 않는다 — 상위 레이어가
            # `get_current_price` 가 None 인 것으로 유효성 판정.
            return
        with self._lock:
            acc = self._accumulators.get(symbol)
            if acc is not None:
                acc.subscription_handle = handle

    # ---- 내부: 콜백 --------------------------------------------------

    def _ws_callback(self, symbol: str, args: tuple[Any, ...]) -> None:
        """python-kis WebSocket 콜백 진입점.

        콜백 인자 형식은 `(client, event_args)` 가 표준이나 라이브러리 버전에
        따라 유동적일 수 있어 `_extract_ws_payload` 에서 방어적으로 파싱한다.
        """
        try:
            payload = _extract_ws_payload(args)
            if payload is None:
                return
            tick = self._normalize_ws_payload(symbol, payload)
            if tick is None:
                return
            self._on_tick(tick)
        except Exception as e:  # noqa: BLE001 — 콜백 예외가 스레드를 죽이지 않게
            logger.exception(f"WebSocket 콜백 처리 실패 ({symbol}): {e.__class__.__name__}: {e}")

    def _normalize_ws_payload(self, symbol: str, payload: Any) -> TickQuote | None:
        price_raw = getattr(payload, "price", None)
        if price_raw is None:
            return None
        ts_raw = getattr(payload, "time_kst", None) or getattr(payload, "time", None)
        ts = ts_raw if isinstance(ts_raw, datetime) else self._clock()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=KST)
        return TickQuote(symbol=symbol, price=Decimal(str(price_raw)), ts=ts)

    # ---- 내부: 폴링 루프 -----------------------------------------------

    def _polling_loop(self) -> None:
        """폴링 모드 전용 백그라운드 루프.

        `_stop_event` 가 세팅될 때까지 `polling_interval_s` 주기로 현재 구독 중인
        전 종목의 현재가를 REST 로 조회한다. 단일 종목 실패는 로그만 남기고
        다음 주기로 넘어간다.
        """
        while not self._stop_event.is_set():
            with self._lock:
                symbols = list(self._accumulators.keys())
            for sym in symbols:
                if self._stop_event.is_set():
                    break
                try:
                    tick = self._poll_once(sym)
                except Exception as e:  # noqa: BLE001
                    logger.exception(f"폴링 조회 실패 ({sym}): {e.__class__.__name__}: {e}")
                    continue
                if tick is not None:
                    self._on_tick(tick)
            self._stop_event.wait(self._polling_interval_s)

    def _poll_once(self, symbol: str) -> TickQuote | None:
        assert self._kis is not None
        stock = self._kis.stock(symbol)
        quote = stock.quote()
        price_raw = getattr(quote, "price", None)
        if price_raw is None:
            return None
        return TickQuote(symbol=symbol, price=Decimal(str(price_raw)), ts=self._clock())

    # ---- 내부: 틱 → 분봉 집계 ------------------------------------------

    def _on_tick(self, tick: TickQuote) -> None:
        """WebSocket/폴링 공통 진입점. 틱을 최신 스냅샷으로 기록하고 분봉에 반영."""
        bar_time = _floor_to_minute(tick.ts)
        with self._lock:
            acc = self._accumulators.get(tick.symbol)
            if acc is None:
                # unsubscribe 이후 큐잉된 콜백일 수 있다 — 조용히 버림.
                return
            acc.last_tick = tick
            current = acc.current_bar
            if current is None:
                acc.current_bar = _new_bar(tick, bar_time)
            elif bar_time == current.bar_time:
                acc.current_bar = _extend_bar(current, tick)
            else:
                # 분 경계 진입 — 이전 분봉 완성, 새 분봉 시작.
                acc.closed_bars.append(current)
                acc.current_bar = _new_bar(tick, bar_time)

    # ---- 내부: 공통 가드 -------------------------------------------------

    def _require_open(self) -> None:
        if self._closed:
            raise RealtimeDataError(
                "RealtimeDataStore 는 이미 close() 되었습니다. 새 인스턴스를 생성하세요."
            )

    @staticmethod
    def _validate_symbol(symbol: str) -> None:
        if not symbol or not _SYMBOL_RE.match(symbol):
            raise RealtimeDataError(f"symbol 은 6자리 숫자 문자열이어야 합니다 (got={symbol!r})")


# ---- 모듈 수준 순수 함수 -------------------------------------------------


def _floor_to_minute(ts: datetime) -> datetime:
    """분 경계 시작 시각(KST) 으로 절사."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=KST)
    return ts.replace(second=0, microsecond=0)


def _new_bar(tick: TickQuote, bar_time: datetime) -> MinuteBar:
    return MinuteBar(
        symbol=tick.symbol,
        bar_time=bar_time,
        open=tick.price,
        high=tick.price,
        low=tick.price,
        close=tick.price,
        volume=0,
    )


def _extend_bar(bar: MinuteBar, tick: TickQuote) -> MinuteBar:
    """진행 중 분봉에 틱 1건을 반영해 새 `MinuteBar` 를 만든다 (frozen 이므로 재생성)."""
    high = bar.high if bar.high >= tick.price else tick.price
    low = bar.low if bar.low <= tick.price else tick.price
    return MinuteBar(
        symbol=bar.symbol,
        bar_time=bar.bar_time,
        open=bar.open,
        high=high,
        low=low,
        close=tick.price,
        volume=bar.volume,
    )


def _extract_ws_payload(args: tuple[Any, ...]) -> Any | None:
    """python-kis WebSocket 콜백 인자에서 실시간 가격 payload 를 뽑아낸다.

    버전별로 `(client, event_args)` 가 표준이나, `event_args` 는 `.response` 또는
    `.data` 속성으로 실제 `KisRealtimePrice` 객체를 노출한다. 둘 중 하나를
    방어적으로 선택하고, 실패하면 `None` 반환 (콜백은 조용히 무시).
    """
    if not args:
        return None
    # 2개 인자(client, event_args) 표준
    candidate = args[-1]
    payload = getattr(candidate, "response", None)
    if payload is not None:
        return payload
    payload = getattr(candidate, "data", None)
    if payload is not None:
        return payload
    # 폴백: event_args 자체가 price-like 인 경우
    if getattr(candidate, "price", None) is not None:
        return candidate
    return None
