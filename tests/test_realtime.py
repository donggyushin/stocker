"""RealtimeDataStore 단위 테스트.

pykis/네트워크 호출은 전부 pykis_factory + mocker.MagicMock 으로 대체하고,
시계는 clock 주입으로 결정론적으로 고정한다.

live 키 정책 변경(paper+live 하이브리드) 이후 기준:
- RealtimeDataStore 는 실전 키 3종(KIS_LIVE_*) 이 없으면 fail-fast.
- guard 는 install_order_block_guard (도메인 무관 주문 경로 차단).
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import ANY, MagicMock

import pytest
from pytest_mock import MockerFixture

from stock_agent.config import Settings, reset_settings_cache
from stock_agent.data.realtime import (
    RealtimeDataError,
    RealtimeDataStore,
    TickQuote,
)

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_VALID_BASE_ENV: dict[str, str] = {
    "KIS_HTS_ID": "test-user",
    "KIS_APP_KEY": "T" * 36,
    "KIS_APP_SECRET": "S" * 180,
    "KIS_ACCOUNT_NO": "12345678-01",
    "TELEGRAM_BOT_TOKEN": "dummy-tg-token",
    "TELEGRAM_CHAT_ID": "9999",
    "KIS_ENV": "paper",
    "KIS_KEY_ORIGIN": "paper",
}

# RealtimeDataStore 는 실전 키 3종이 필요하다. 테스트용 더미값.
_LIVE_KEY_ENV: dict[str, str] = {
    "KIS_LIVE_APP_KEY": "X" * 36,
    "KIS_LIVE_APP_SECRET": "Y" * 180,
    "KIS_LIVE_ACCOUNT_NO": "12345678-01",
}

_SYMBOL = "005930"


def _kst(hour: int, minute: int, second: int = 0) -> datetime:
    """지정 시각의 KST aware datetime 반환 (고정 날짜 2026-04-19)."""
    return datetime(2026, 4, 19, hour, minute, second, tzinfo=KST)


def _fixed_clock(dt: datetime):
    """datetime 을 반환하는 단순 clock 팩토리."""
    return lambda: dt


# ---------------------------------------------------------------------------
# autouse: .env 자동 로드 무력화 + Settings 캐시 리셋
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """매 테스트 시작·종료 시 .env 영향 제거 및 lru_cache 초기화.

    pydantic-settings 는 env_file 을 직접 파일에서 읽으므로,
    model_config 의 env_file 을 None 으로 교체해 .env 로드를 차단한다.
    """
    from stock_agent.config import Settings as _Settings

    monkeypatch.setattr(_Settings, "model_config", {**_Settings.model_config, "env_file": None})
    for k in (*_VALID_BASE_ENV, *_LIVE_KEY_ENV):
        monkeypatch.delenv(k, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


# ---------------------------------------------------------------------------
# Settings 생성 헬퍼
# ---------------------------------------------------------------------------


def _make_settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    """유효한 기반 환경변수 위에 overrides 를 올려 Settings 인스턴스를 반환."""
    for k, v in {**_VALID_BASE_ENV, **overrides}.items():
        monkeypatch.setenv(k, v)
    reset_settings_cache()
    return Settings()  # type: ignore[call-arg]


def _make_settings_with_live_keys(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    """기반 환경변수에 live 키 3종을 포함한 Settings 인스턴스를 반환.

    RealtimeDataStore 는 has_live_keys=True 가 아니면 fail-fast 하므로
    대부분의 realtime 테스트는 이 헬퍼를 사용한다.
    """
    merged = {**_VALID_BASE_ENV, **_LIVE_KEY_ENV, **overrides}
    for k, v in merged.items():
        monkeypatch.setenv(k, v)
    reset_settings_cache()
    return Settings()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 공통 픽스처
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_kis(mocker: MockerFixture):
    """호출 인자를 기록하는 MagicMock PyKis 인스턴스."""
    return mocker.MagicMock()


@pytest.fixture
def pykis_factory(fake_kis, mocker: MockerFixture):
    """fake_kis 를 반환하는 팩토리 MagicMock."""
    return mocker.MagicMock(return_value=fake_kis)


@pytest.fixture
def guard_patch(mocker: MockerFixture):
    """install_order_block_guard 를 목으로 교체. WebSocket/폴링 경로 모두에 적용."""
    return mocker.patch("stock_agent.data.realtime.install_order_block_guard")


# ---------------------------------------------------------------------------
# 생성/생명 주기 테스트
# ---------------------------------------------------------------------------


def test_정상_생성_후_mode가_idle(
    monkeypatch: pytest.MonkeyPatch,
    pykis_factory,
    guard_patch,
) -> None:
    """start() 호출 전에는 mode 가 "idle" 이어야 한다."""
    settings = _make_settings_with_live_keys(monkeypatch)
    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
    )
    assert rt.mode == "idle"


def test_polling_interval_0이하_생성시_RealtimeDataError(
    monkeypatch: pytest.MonkeyPatch,
    pykis_factory,
    guard_patch,
) -> None:
    """polling_interval_s <= 0 은 생성 시점에 RealtimeDataError."""
    settings = _make_settings_with_live_keys(monkeypatch)
    with pytest.raises(RealtimeDataError, match="polling_interval_s"):
        RealtimeDataStore(
            settings,
            pykis_factory=pykis_factory,
            polling_interval_s=0,
        )
    with pytest.raises(RealtimeDataError, match="polling_interval_s"):
        RealtimeDataStore(
            settings,
            pykis_factory=pykis_factory,
            polling_interval_s=-1.0,
        )


def test_ws_connect_timeout_0이하_생성시_RealtimeDataError(
    monkeypatch: pytest.MonkeyPatch,
    pykis_factory,
    guard_patch,
) -> None:
    """ws_connect_timeout_s <= 0 은 생성 시점에 RealtimeDataError."""
    settings = _make_settings_with_live_keys(monkeypatch)
    with pytest.raises(RealtimeDataError, match="ws_connect_timeout_s"):
        RealtimeDataStore(
            settings,
            pykis_factory=pykis_factory,
            ws_connect_timeout_s=0,
        )


def test_start_두번_호출시_RealtimeDataError(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    """start() 를 두 번 호출하면 두 번째에서 RealtimeDataError."""
    settings = _make_settings_with_live_keys(monkeypatch)
    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
        polling_interval_s=0.01,
    )
    # WebSocket 연결 성공으로 설정
    fake_kis.websocket.ensure_connected.return_value = None
    rt.start()
    assert rt.mode == "websocket"

    with pytest.raises(RealtimeDataError, match="1회"):
        rt.start()

    rt.close()


def test_close_멱등성_두번_호출해도_예외없음(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    """close() 는 멱등 — 여러 번 호출해도 예외가 없어야 한다."""
    settings = _make_settings_with_live_keys(monkeypatch)
    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
    )
    rt.close()
    rt.close()  # 예외 없음


def test_컨텍스트_매니저_종료시_closed_상태(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    """with 블록 종료 후 close() 가 호출되어 closed 상태가 된다."""
    settings = _make_settings_with_live_keys(monkeypatch)
    fake_kis.websocket.ensure_connected.return_value = None

    with RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
    ) as rt:
        rt.start()
        assert rt.mode == "websocket"

    # with 블록 종료 후 — get_current_price 호출 시 RealtimeDataError 로 closed 확인
    with pytest.raises(RealtimeDataError, match="close"):
        rt.get_current_price(_SYMBOL)


# ---------------------------------------------------------------------------
# WebSocket 경로 테스트
# ---------------------------------------------------------------------------


def test_start_websocket_연결_성공시_mode가_websocket(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    """start() 에서 ensure_connected 가 호출되고 mode 가 "websocket" 으로 확정된다."""
    settings = _make_settings_with_live_keys(monkeypatch)
    fake_kis.websocket.ensure_connected.return_value = None

    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
    )
    rt.start()

    fake_kis.websocket.ensure_connected.assert_called_once_with(timeout=rt._ws_connect_timeout_s)
    assert rt.mode == "websocket"

    rt.close()


def test_websocket_모드에서_subscribe시_stock_on이_호출된다(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    """WebSocket 모드에서 subscribe() 호출 시 kis.stock(sym).on("price", cb) 가 호출된다."""
    settings = _make_settings_with_live_keys(monkeypatch)
    fake_kis.websocket.ensure_connected.return_value = None
    mock_stock = MagicMock()
    fake_kis.stock.return_value = mock_stock

    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
    )
    rt.start()
    rt.subscribe(_SYMBOL)

    fake_kis.stock.assert_called_with(_SYMBOL)
    mock_stock.on.assert_called_once_with("price", ANY)

    rt.close()


def test_websocket_연결_실패시_폴링_모드로_폴백(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    """ensure_connected 가 TimeoutError 를 던지면 mode 가 "polling" 으로 폴백된다."""
    settings = _make_settings_with_live_keys(monkeypatch)
    fake_kis.websocket.ensure_connected.side_effect = TimeoutError("연결 시간 초과")

    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
        polling_interval_s=0.01,
    )
    rt.start()

    assert rt.mode == "polling"

    rt.close()
    if rt._polling_thread is not None:
        rt._polling_thread.join(timeout=1.0)
        assert not rt._polling_thread.is_alive()


# ---------------------------------------------------------------------------
# 폴링 경로 테스트
# ---------------------------------------------------------------------------


def test_폴링_모드에서_subscribe_후_get_current_price_업데이트됨(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    """폴링 모드에서 subscribe 후 실제로 quote() 가 호출되고 get_current_price 가 갱신된다."""
    settings = _make_settings_with_live_keys(monkeypatch)
    # WebSocket 연결 실패 → 폴링 폴백
    fake_kis.websocket.ensure_connected.side_effect = TimeoutError("ws 없음")

    mock_quote = MagicMock()
    mock_quote.price = Decimal("70000")
    fake_kis.stock.return_value.quote.return_value = mock_quote

    clock_dt = _kst(9, 30, 0)
    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(clock_dt),
        polling_interval_s=0.01,
    )
    rt.start()
    rt.subscribe(_SYMBOL)

    # 폴링 루프가 최소 1회 돌 수 있도록 짧게 대기
    time.sleep(0.1)

    tick = rt.get_current_price(_SYMBOL)
    assert tick is not None
    assert tick.price == Decimal("70000")
    assert tick.symbol == _SYMBOL

    rt.close()
    if rt._polling_thread is not None:
        rt._polling_thread.join(timeout=1.0)
        assert not rt._polling_thread.is_alive()


# ---------------------------------------------------------------------------
# 틱 집계 / 분봉 테스트
# ---------------------------------------------------------------------------


def test_동일_분_4틱_분봉_OHLC_집계(
    monkeypatch: pytest.MonkeyPatch,
    pykis_factory,
    guard_patch,
) -> None:
    """동일 분의 4틱 → open/high/low/close 가 올바르게 집계된다."""
    settings = _make_settings_with_live_keys(monkeypatch)
    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
    )
    rt.subscribe(_SYMBOL)

    prices = [
        Decimal("70000"),
        Decimal("70500"),
        Decimal("69800"),
        Decimal("70200"),
    ]
    base_ts = _kst(9, 30, 0)
    for i, price in enumerate(prices):
        tick = TickQuote(
            symbol=_SYMBOL,
            price=price,
            ts=base_ts.replace(second=i * 10),
        )
        rt._on_tick(tick)

    bar = rt.get_current_bar(_SYMBOL)
    assert bar is not None
    assert bar.open == Decimal("70000")
    assert bar.high == Decimal("70500")
    assert bar.low == Decimal("69800")
    assert bar.close == Decimal("70200")

    rt.close()


def test_분_경계_전환시_완성_분봉_누적(
    monkeypatch: pytest.MonkeyPatch,
    pykis_factory,
    guard_patch,
) -> None:
    """09:30 틱 2건 → 09:31 틱 1건 주입 시 완성 분봉 1건, 진행 중 분봉 bar_time=09:31."""
    settings = _make_settings_with_live_keys(monkeypatch)
    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
    )
    rt.subscribe(_SYMBOL)

    ticks = [
        TickQuote(symbol=_SYMBOL, price=Decimal("70000"), ts=_kst(9, 30, 15)),
        TickQuote(symbol=_SYMBOL, price=Decimal("70500"), ts=_kst(9, 30, 45)),
        TickQuote(symbol=_SYMBOL, price=Decimal("70300"), ts=_kst(9, 31, 5)),
    ]
    for tick in ticks:
        rt._on_tick(tick)

    closed = rt.get_minute_bars(_SYMBOL)
    assert len(closed) == 1
    assert closed[0].bar_time == _kst(9, 30, 0)
    assert closed[0].open == Decimal("70000")
    assert closed[0].close == Decimal("70500")

    current = rt.get_current_bar(_SYMBOL)
    assert current is not None
    assert current.bar_time == _kst(9, 31, 0)
    assert current.close == Decimal("70300")

    rt.close()


# ---------------------------------------------------------------------------
# 가드 / 엣지 케이스 테스트
# ---------------------------------------------------------------------------


def test_미구독_symbol_get_current_price는_None(
    monkeypatch: pytest.MonkeyPatch,
    pykis_factory,
    guard_patch,
) -> None:
    """subscribe 하지 않은 symbol 의 get_current_price 는 None."""
    settings = _make_settings_with_live_keys(monkeypatch)
    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
    )
    assert rt.get_current_price("000660") is None
    rt.close()


def test_미구독_symbol_get_minute_bars는_빈_리스트(
    monkeypatch: pytest.MonkeyPatch,
    pykis_factory,
    guard_patch,
) -> None:
    """subscribe 하지 않은 symbol 의 get_minute_bars 는 빈 리스트."""
    settings = _make_settings_with_live_keys(monkeypatch)
    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
    )
    assert rt.get_minute_bars("000660") == []
    rt.close()


@pytest.mark.parametrize(
    "symbol",
    ["abc", "", "12345", "ABCDEF", "1234567"],
    ids=["소문자영문", "빈문자열", "5자리숫자", "대문자영문", "7자리숫자"],
)
def test_symbol_형식_오류_subscribe시_RealtimeDataError(
    monkeypatch: pytest.MonkeyPatch,
    pykis_factory,
    guard_patch,
    symbol: str,
) -> None:
    """6자리 숫자가 아닌 symbol 로 subscribe 하면 RealtimeDataError."""
    settings = _make_settings_with_live_keys(monkeypatch)
    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
    )
    with pytest.raises(RealtimeDataError, match="6자리"):
        rt.subscribe(symbol)
    rt.close()


def test_get_minute_bars_반환_리스트_수정해도_내부_상태_불변(
    monkeypatch: pytest.MonkeyPatch,
    pykis_factory,
    guard_patch,
) -> None:
    """get_minute_bars 반환값은 복사본 — 외부 수정이 내부 상태에 영향 없다."""
    settings = _make_settings_with_live_keys(monkeypatch)
    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
    )
    rt.subscribe(_SYMBOL)

    # 09:30 분봉 완성 후 09:31 분봉 시작
    rt._on_tick(TickQuote(symbol=_SYMBOL, price=Decimal("70000"), ts=_kst(9, 30, 0)))
    rt._on_tick(TickQuote(symbol=_SYMBOL, price=Decimal("70500"), ts=_kst(9, 31, 0)))

    bars = rt.get_minute_bars(_SYMBOL)
    assert len(bars) == 1

    # 외부에서 반환된 리스트를 clear 해도 내부 상태에 영향 없음
    bars.clear()

    bars_again = rt.get_minute_bars(_SYMBOL)
    assert len(bars_again) == 1

    rt.close()


# ---------------------------------------------------------------------------
# order block guard 연동 테스트
# ---------------------------------------------------------------------------


def test_start시_install_order_block_guard가_호출된다(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    """start() 에서 PyKis 인스턴스 생성 후 install_order_block_guard 가 호출된다."""
    settings = _make_settings_with_live_keys(monkeypatch)
    fake_kis.websocket.ensure_connected.return_value = None

    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
    )
    rt.start()

    guard_patch.assert_called_once_with(fake_kis)

    rt.close()


# ---------------------------------------------------------------------------
# live 키 미설정 fail-fast 테스트
# ---------------------------------------------------------------------------


def test_live_키_미설정_start시_RealtimeDataError(
    monkeypatch: pytest.MonkeyPatch,
    pykis_factory,
    guard_patch,
) -> None:
    """has_live_keys=False 인 Settings 주입 시 start() 에서 RealtimeDataError 발생."""
    # live 키 없이 기본 환경변수만 설정
    settings = _make_settings(monkeypatch)
    assert settings.has_live_keys is False

    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
    )
    with pytest.raises(RealtimeDataError, match="KIS_LIVE_"):
        rt.start()


# ---------------------------------------------------------------------------
# _build_pykis live 키 factory 인자 검증
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 폴링 연속 실패 카운터 테스트
# ---------------------------------------------------------------------------


def test_polling_consecutive_failures_초기값은_0(
    monkeypatch: pytest.MonkeyPatch,
    pykis_factory,
    guard_patch,
) -> None:
    """생성 직후, start() 호출 전에 polling_consecutive_failures == 0."""
    settings = _make_settings_with_live_keys(monkeypatch)
    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
    )
    assert rt.polling_consecutive_failures == 0
    rt.close()


def test_polling_연속_실패시_카운터_증가(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    """폴링 모드에서 quote() 가 계속 None 을 반환하면 카운터가 2 이상으로 올라간다."""
    settings = _make_settings_with_live_keys(monkeypatch)
    # WebSocket 연결 실패 → 폴링 폴백
    fake_kis.websocket.ensure_connected.side_effect = TimeoutError("ws 없음")
    # quote 가 항상 None-price 를 반환 → _poll_once 가 None 반환 → sweep 실패
    mock_quote = MagicMock()
    mock_quote.price = None
    fake_kis.stock.return_value.quote.return_value = mock_quote

    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
        polling_interval_s=0.01,
    )
    rt.start()
    rt.subscribe(_SYMBOL)

    # 폴링 루프가 최소 2회 돌 수 있도록 대기
    deadline = time.monotonic() + 2.0
    while rt.polling_consecutive_failures < 2 and time.monotonic() < deadline:
        time.sleep(0.02)

    assert rt.polling_consecutive_failures >= 2

    rt.close()
    if rt._polling_thread is not None:
        rt._polling_thread.join(timeout=1.0)
        assert not rt._polling_thread.is_alive()


def test_polling_sweep_중_한_종목이라도_성공하면_카운터_리셋(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    """두 종목 중 하나가 성공하면 sweep 성공으로 간주해 카운터가 0 을 유지한다."""
    settings = _make_settings_with_live_keys(monkeypatch)
    fake_kis.websocket.ensure_connected.side_effect = TimeoutError("ws 없음")

    symbol2 = "000660"

    # quote() 호출마다 번갈아: 정상 → 예외 → 정상 → 예외 ... (무한 순환)
    mock_good = MagicMock()
    mock_good.price = Decimal("70000")
    fake_kis.stock.return_value.quote.side_effect = itertools.cycle(
        [mock_good, Exception("일시 오류")]
    )

    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
        polling_interval_s=0.01,
    )
    rt.start()
    rt.subscribe(_SYMBOL)
    rt.subscribe(symbol2)

    # 폴링 루프가 최소 2 sweep 돌 수 있도록 대기
    time.sleep(0.1)

    # 적어도 한 종목이 매 sweep 마다 성공하므로 카운터는 0 이거나 매우 낮아야 한다
    assert rt.polling_consecutive_failures == 0

    rt.close()
    if rt._polling_thread is not None:
        rt._polling_thread.join(timeout=1.0)
        assert not rt._polling_thread.is_alive()


def test_polling_연속_실패_임계_도달시_critical_로그(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    """연속 실패가 임계(_POLLING_FAILURE_ALERT_THRESHOLD)에 도달하면 CRITICAL 로그가 남는다.

    monkeypatch 로 임계를 2 로 낮춰 테스트 대기 시간을 최소화한다.
    """
    # 임계를 2로 낮춰 빠르게 도달
    monkeypatch.setattr("stock_agent.data.realtime._POLLING_FAILURE_ALERT_THRESHOLD", 2)

    settings = _make_settings_with_live_keys(monkeypatch)
    fake_kis.websocket.ensure_connected.side_effect = TimeoutError("ws 없음")

    # quote 가 항상 예외를 던져 sweep 실패 유도
    fake_kis.stock.return_value.quote.side_effect = RuntimeError("조회 불가")

    # loguru 캡처용 싱크
    captured: list[str] = []

    def _sink(message) -> None:  # type: ignore[no-untyped-def]
        captured.append(message)

    from loguru import logger as _logger

    sink_id = _logger.add(_sink, level="CRITICAL", format="{message}")

    try:
        rt = RealtimeDataStore(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(9, 30)),
            polling_interval_s=0.01,
        )
        rt.start()
        rt.subscribe(_SYMBOL)

        # 임계(2) 도달까지 대기
        deadline = time.monotonic() + 3.0
        while rt.polling_consecutive_failures < 2 and time.monotonic() < deadline:
            time.sleep(0.02)

        rt.close()
        if rt._polling_thread is not None:
            rt._polling_thread.join(timeout=1.0)
    finally:
        _logger.remove(sink_id)

    # CRITICAL 레벨 메시지가 캡처되어야 한다
    assert len(captured) >= 1, "CRITICAL 로그가 최소 1건 이상 기록되어야 한다"
    combined = " ".join(captured)
    assert "연속" in combined or "실패" in combined


# ---------------------------------------------------------------------------
# _build_pykis live 키 factory 인자 검증
# ---------------------------------------------------------------------------


def test_build_pykis_live_키로_factory_호출됨(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    """start() 후 pykis_factory 가 live 키 3종으로 호출되고 virtual_* 인자는 없어야 한다."""
    settings = _make_settings_with_live_keys(monkeypatch)
    fake_kis.websocket.ensure_connected.return_value = None

    rt = RealtimeDataStore(
        settings,
        pykis_factory=pykis_factory,
        clock=_fixed_clock(_kst(9, 30)),
    )
    rt.start()

    pykis_factory.assert_called_once()
    call_kwargs = pykis_factory.call_args.kwargs

    # id 는 공유 필드 kis_hts_id (HTS_ID 는 paper/실전 동일)
    assert call_kwargs["id"] == settings.kis_hts_id
    # account 는 실전 계좌번호
    assert call_kwargs["account"] == _LIVE_KEY_ENV["KIS_LIVE_ACCOUNT_NO"]
    assert settings.kis_live_app_key is not None
    assert settings.kis_live_app_secret is not None
    assert call_kwargs["appkey"] == settings.kis_live_app_key.get_secret_value()
    assert call_kwargs["secretkey"] == settings.kis_live_app_secret.get_secret_value()

    # virtual_* 슬롯이 없어야 한다 (paper 키 라우팅 방지)
    assert "virtual_id" not in call_kwargs
    assert "virtual_appkey" not in call_kwargs
    assert "virtual_secretkey" not in call_kwargs

    rt.close()
