"""KisClient 단위 테스트. PyKis/네트워크 호출은 전부 mocker.MagicMock 으로 대체한다."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from loguru import logger
from pytest_mock import MockerFixture

from stock_agent.broker.kis_client import (
    BalanceSnapshot,
    Holding,
    KisClient,
    KisClientError,
    OrderTicket,
    PendingOrder,
)
from stock_agent.broker.rate_limiter import OrderRateLimiter
from stock_agent.config import Settings, reset_settings_cache

# ---------------------------------------------------------------------------
# 공통 환경변수 기반값
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# autouse: .env 자동 로드 무력화 + Settings 캐시 리셋
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """매 테스트 시작·종료 시 .env 영향 제거 및 lru_cache 초기화."""
    monkeypatch.setenv("PYDANTIC_SETTINGS_DOTENV_DISABLED", "1")
    for k in _VALID_BASE_ENV:
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


# ---------------------------------------------------------------------------
# 공통 픽스처: fake_kis / pykis_factory / guard_patch
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_kis(mocker: MockerFixture):
    """호출 인자를 기록하는 MagicMock PyKis 인스턴스."""
    return mocker.MagicMock()


@pytest.fixture
def pykis_factory(fake_kis, mocker: MockerFixture):
    """fake_kis 를 반환하는 팩토리 MagicMock."""
    factory = mocker.MagicMock(return_value=fake_kis)
    return factory


@pytest.fixture
def guard_patch(mocker: MockerFixture):
    """install_paper_mode_guard 를 mocker 로 패치. 사용하는 테스트에서 명시적으로 주입."""
    return mocker.patch("stock_agent.broker.kis_client.install_paper_mode_guard")


def _make_pending_order_mock(
    mocker: MockerFixture,
    *,
    number: str = "PO-000",
    symbol: str = "005930",
    side: str | None = "buy",
    qty: int = 1,
    qty_remaining: int = 1,
    price: int | None = 70_000,
    qty_filled: int | None = None,
    use_pykis_fields: bool = False,
):
    """PendingOrder 변환 대상이 될 PyKis order mock 생성. time/created_at 은 None 고정.

    qty_filled: None 이면 헬퍼 내부에서 max(0, qty - qty_remaining) 으로 계산.
    use_pykis_fields: True 면 PyKis 정식 필드(executed_quantity/pending_quantity)를 세팅.
                      False(기본값)면 기존 호환 방식(qty_remaining 직접 세팅)으로 동작.
    """
    order = mocker.MagicMock()
    order.number = number
    order.symbol = symbol
    order.side = side
    order.qty = qty
    order.price = price
    order.time = None
    order.created_at = None

    # qty_filled 기본값 계산
    resolved_filled = qty_filled if qty_filled is not None else max(0, qty - qty_remaining)

    if use_pykis_fields:
        # PyKis 정식 필드 세팅 — _to_pending_order 가 이쪽을 우선 읽어야 한다
        order.executed_quantity = resolved_filled
        order.pending_quantity = qty_remaining
        # qty_remaining 속성은 MagicMock 이 자동 생성하나, 정식 필드 우선 테스트이므로
        # getattr fallback 경로를 확인하기 위해 명시적으로 지우지 않는다
        # (MagicMock 특성상 존재하지 않는 속성 접근 시 새 MagicMock 반환 — int 변환 시 오류)
        del order.qty_remaining  # fallback 경로 비활성화
    else:
        # 기존 호환 방식
        order.qty_remaining = qty_remaining
        # executed_quantity / pending_quantity 는 MagicMock 이 자동 생성하므로
        # AttributeError 가 발생하지 않는다 → getattr(order, "executed_quantity", None)
        # 가 MagicMock 을 반환해 정수 변환 시 문제가 생길 수 있음.
        # 기존 코드는 이 필드를 보지 않으므로 del 로 명시적 제거.
        del order.executed_quantity
        del order.pending_quantity

    return order


# ---------------------------------------------------------------------------
# 테스트 1: paper 모드에서 install_paper_mode_guard 가 호출된다
# ---------------------------------------------------------------------------


def test_paper_모드에서_install_paper_mode_guard가_호출된다(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    settings = _make_settings(monkeypatch)
    KisClient(settings, pykis_factory=pykis_factory)
    guard_patch.assert_called_once_with(fake_kis)


# ---------------------------------------------------------------------------
# 테스트 2: live 모드는 NotImplementedError 를 발생시킨다
# ---------------------------------------------------------------------------


def test_live_모드는_NotImplementedError를_발생시킨다(
    monkeypatch: pytest.MonkeyPatch,
    pykis_factory,
    guard_patch,
) -> None:
    # live 환경에서는 kis_key_origin 도 live 여야 Settings 통과
    settings = _make_settings(monkeypatch, KIS_ENV="live", KIS_KEY_ORIGIN="live")
    with pytest.raises(NotImplementedError):
        KisClient(settings, pykis_factory=pykis_factory)
    # factory 는 호출되지 않아야 한다 (live 분기 즉시 NotImplementedError)
    pykis_factory.assert_not_called()


# ---------------------------------------------------------------------------
# 테스트 3: paper 모드에서 PyKis 생성자 양쪽 슬롯에 동일키가 주입된다
# ---------------------------------------------------------------------------


def test_paper_모드에서_PyKis_생성자_양쪽_슬롯에_동일키가_주입된다(
    monkeypatch: pytest.MonkeyPatch,
    pykis_factory,
    guard_patch,
) -> None:
    settings = _make_settings(monkeypatch)
    KisClient(settings, pykis_factory=pykis_factory)

    assert pykis_factory.call_count == 1
    _, kwargs = pykis_factory.call_args

    expected_key = settings.kis_app_key.get_secret_value()
    expected_secret = settings.kis_app_secret.get_secret_value()

    assert kwargs["appkey"] == expected_key
    assert kwargs["virtual_appkey"] == expected_key
    assert kwargs["secretkey"] == expected_secret
    assert kwargs["virtual_secretkey"] == expected_secret
    assert kwargs["id"] == settings.kis_hts_id
    assert kwargs["virtual_id"] == settings.kis_hts_id
    assert kwargs["keep_token"] is True
    assert kwargs["use_websocket"] is False


# ---------------------------------------------------------------------------
# 테스트 4: get_balance 가 KisBalance 를 BalanceSnapshot 으로 정규화한다
# ---------------------------------------------------------------------------


def test_get_balance가_KisBalance를_BalanceSnapshot으로_정규화한다(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    settings = _make_settings(monkeypatch)

    # mock balance: stocks 없는 케이스
    mock_balance = mocker.MagicMock()
    mock_balance.withdrawable_amount = 10_000_000
    mock_balance.total = 10_000_000
    mock_balance.stocks = []
    fake_kis.account.return_value.balance.return_value = mock_balance

    kc = KisClient(settings, pykis_factory=pykis_factory)
    snapshot = kc.get_balance()

    assert isinstance(snapshot, BalanceSnapshot)
    assert snapshot.withdrawable == 10_000_000
    assert snapshot.total == 10_000_000
    assert snapshot.holdings_count == 0
    assert snapshot.holdings == ()
    assert snapshot.fetched_at.tzinfo is not None  # timezone-aware

    # stocks 에 1건 있는 케이스
    mock_stock = mocker.MagicMock()
    mock_stock.symbol = "005930"
    mock_stock.qty = 10
    mock_stock.price = Decimal("70000")
    mock_stock.current_price = Decimal("72000")
    mock_balance.stocks = [mock_stock]
    fake_kis.account.return_value.balance.return_value = mock_balance

    snapshot2 = kc.get_balance()
    assert snapshot2.holdings_count == 1
    assert len(snapshot2.holdings) == 1
    h = snapshot2.holdings[0]
    assert isinstance(h, Holding)
    assert h.symbol == "005930"
    assert h.qty == 10
    assert h.avg_price == pytest.approx(Decimal("70000"))
    assert h.current_price == pytest.approx(Decimal("72000"))


# ---------------------------------------------------------------------------
# 테스트 5: place_buy 는 market=KRX 와 symbol/qty/price 를 account.buy 에 전달,
#            price=None 이면 그대로 None 이 전달된다
# ---------------------------------------------------------------------------


def test_place_buy는_price_None을_account_buy에_그대로_전달한다(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    settings = _make_settings(monkeypatch)

    mock_order = mocker.MagicMock()
    mock_order.number = "ORD-001"
    fake_kis.account.return_value.buy.return_value = mock_order

    kc = KisClient(settings, pykis_factory=pykis_factory)
    ticket = kc.place_buy("005930", qty=5, price=None)

    fake_kis.account.return_value.buy.assert_called_once_with(
        market="KRX", symbol="005930", qty=5, price=None
    )
    assert isinstance(ticket, OrderTicket)
    assert ticket.side == "buy"
    assert ticket.qty == 5
    assert ticket.price is None
    assert ticket.order_number == "ORD-001"
    assert ticket.symbol == "005930"


# ---------------------------------------------------------------------------
# 테스트 6: place_buy price 지정 시 price 가 account.buy 에 전달된다
# ---------------------------------------------------------------------------


def test_place_buy_price_지정시_price가_account_buy에_전달된다(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    settings = _make_settings(monkeypatch)

    mock_order = mocker.MagicMock()
    mock_order.number = "ORD-002"
    fake_kis.account.return_value.buy.return_value = mock_order

    kc = KisClient(settings, pykis_factory=pykis_factory)
    ticket = kc.place_buy("005930", qty=3, price=70_000)

    fake_kis.account.return_value.buy.assert_called_once_with(
        market="KRX", symbol="005930", qty=3, price=70_000
    )
    assert ticket.price == 70_000


# ---------------------------------------------------------------------------
# 테스트 7: place_sell 도 동일한 계약을 account.sell 에 적용한다
# ---------------------------------------------------------------------------


def test_place_sell도_동일한_계약을_account_sell에_적용한다(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    settings = _make_settings(monkeypatch)

    mock_order = mocker.MagicMock()
    mock_order.number = "ORD-003"
    fake_kis.account.return_value.sell.return_value = mock_order

    kc = KisClient(settings, pykis_factory=pykis_factory)
    ticket = kc.place_sell("000660", qty=3, price=None)

    fake_kis.account.return_value.sell.assert_called_once_with(
        market="KRX", symbol="000660", qty=3, price=None
    )
    assert ticket.side == "sell"
    assert ticket.symbol == "000660"
    assert ticket.qty == 3
    assert ticket.order_number == "ORD-003"


# ---------------------------------------------------------------------------
# 테스트 8: get_pending_orders 는 KisOrder iterable 을 list[PendingOrder] 로 변환한다
# ---------------------------------------------------------------------------


def test_get_pending_orders는_KisOrder_iterable을_list_PendingOrder로_변환한다(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    settings = _make_settings(monkeypatch)

    buy_order = _make_pending_order_mock(
        mocker,
        number="PO-001",
        symbol="005930",
        side="buy",
        qty=10,
        qty_remaining=10,
        price=70_000,
    )
    sell_order = _make_pending_order_mock(
        mocker,
        number="PO-002",
        symbol="000660",
        side="sell",
        qty=5,
        qty_remaining=3,
        price=150_000,
    )

    fake_kis.account.return_value.pending_orders.return_value = [buy_order, sell_order]

    kc = KisClient(settings, pykis_factory=pykis_factory)
    result = kc.get_pending_orders()

    assert len(result) == 2
    assert all(isinstance(o, PendingOrder) for o in result)

    po_buy = result[0]
    assert po_buy.order_number == "PO-001"
    assert po_buy.symbol == "005930"
    assert po_buy.side == "buy"
    assert po_buy.qty_ordered == 10
    assert po_buy.qty_remaining == 10
    assert po_buy.price == 70_000

    po_sell = result[1]
    assert po_sell.order_number == "PO-002"
    assert po_sell.symbol == "000660"
    assert po_sell.side == "sell"
    assert po_sell.qty_ordered == 5
    assert po_sell.qty_remaining == 3
    assert po_sell.price == 150_000

    # 빈 iterable 케이스
    fake_kis.account.return_value.pending_orders.return_value = []
    assert kc.get_pending_orders() == []


# ---------------------------------------------------------------------------
# 테스트 9: 라이브러리 예외는 KisClientError 로 래핑되며 원본은 cause 로 보존된다
# ---------------------------------------------------------------------------


def test_라이브러리_예외는_KisClientError로_래핑되며_원본은_cause로_보존된다(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    settings = _make_settings(monkeypatch)
    original = ValueError("boom")
    fake_kis.account.return_value.balance.side_effect = original

    kc = KisClient(settings, pykis_factory=pykis_factory)
    with pytest.raises(KisClientError) as excinfo:
        kc.get_balance()

    assert excinfo.value.__cause__ is original


# ---------------------------------------------------------------------------
# 테스트 10: paper guard RuntimeError 는 래핑되지 않고 그대로 전파된다
# ---------------------------------------------------------------------------


def test_paper_guard_RuntimeError는_래핑되지_않고_그대로_전파된다(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    settings = _make_settings(monkeypatch)
    fake_kis.account.return_value.balance.side_effect = RuntimeError(
        "paper 모드에서 실전 도메인 호출 차단됨: /uapi/domestic-stock/v1/trading/order-cash"
    )

    kc = KisClient(settings, pykis_factory=pykis_factory)
    with pytest.raises(RuntimeError) as excinfo:
        kc.get_balance()

    assert not isinstance(excinfo.value, KisClientError)


# ---------------------------------------------------------------------------
# 테스트 11: close 후 재사용 시 KisClientError 가 발생한다
# ---------------------------------------------------------------------------


def test_close_후_재사용시_KisClientError가_발생한다(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    settings = _make_settings(monkeypatch)
    kc = KisClient(settings, pykis_factory=pykis_factory)

    # 정상 close
    kc.close()

    # close 후 재사용은 KisClientError
    with pytest.raises(KisClientError):
        kc.get_balance()

    with pytest.raises(KisClientError):
        kc.ensure_token()

    with pytest.raises(KisClientError):
        kc.place_buy("005930", qty=1)

    # close 는 멱등 — 두 번 호출해도 예외 없음
    kc.close()
    kc.close()


# ---------------------------------------------------------------------------
# 테스트 12: 컨텍스트 매니저가 close 를 호출하고 원본 예외가 전파된다
# ---------------------------------------------------------------------------


def test_컨텍스트_매니저가_close를_호출하고_원본_예외가_전파된다(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    settings = _make_settings(monkeypatch)

    # with 블록 내 예외 발생 케이스: ValueError 가 외부로 전파되어야 한다
    with (
        pytest.raises(ValueError, match="x"),
        KisClient(settings, pykis_factory=pykis_factory),
    ):
        raise ValueError("x")

    # ValueError 가 전파되는 과정에서 fake_kis.close 가 정확히 한 번 호출
    fake_kis.close.assert_called_once()

    # 정상 종료 케이스: with 블록 완료 후에도 close 호출 확인
    fake_kis_2 = mocker.MagicMock()
    factory_2 = mocker.MagicMock(return_value=fake_kis_2)
    with KisClient(settings, pykis_factory=factory_2):
        pass  # 정상 종료
    fake_kis_2.close.assert_called_once()


# ---------------------------------------------------------------------------
# 테스트 13: _place_order 의 qty<=0 사전 가드 (C-3)
# ---------------------------------------------------------------------------


def test_place_buy_qty_0이하는_KisClientError를_raise하고_account_호출되지_않는다(
    monkeypatch: pytest.MonkeyPatch,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    settings = _make_settings(monkeypatch)
    kc = KisClient(settings, pykis_factory=pykis_factory)

    with pytest.raises(KisClientError, match="주문 수량"):
        kc.place_buy("005930", qty=0)
    with pytest.raises(KisClientError, match="주문 수량"):
        kc.place_buy("005930", qty=-1)
    with pytest.raises(KisClientError, match="주문 수량"):
        kc.place_sell("005930", qty=0)
    with pytest.raises(KisClientError, match="주문 수량"):
        kc.place_sell("005930", qty=-5)

    # 사전 가드라 account.buy / account.sell 까지 전파되지 않아야 한다
    fake_kis.account.return_value.buy.assert_not_called()
    fake_kis.account.return_value.sell.assert_not_called()


# ---------------------------------------------------------------------------
# 테스트 14: _to_pending_order 는 side 판별 실패 시 KisClientError 로 실패한다 (C-1)
# ---------------------------------------------------------------------------


def test_get_pending_orders는_side_미상이면_KisClientError를_raise한다(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    settings = _make_settings(monkeypatch)

    bad_order = _make_pending_order_mock(
        mocker,
        number="PO-999",
        symbol="005930",
        side="unknown",  # "buy"/"sell" 어디에도 해당하지 않음
    )
    fake_kis.account.return_value.pending_orders.return_value = [bad_order]

    kc = KisClient(settings, pykis_factory=pykis_factory)
    with pytest.raises(KisClientError, match="side"):
        kc.get_pending_orders()

    # side 가 None 인 케이스도 동일하게 실패해야 한다
    bad_order.side = None
    with pytest.raises(KisClientError, match="side"):
        kc.get_pending_orders()


# ---------------------------------------------------------------------------
# 테스트 15: _place_order 는 주문번호가 비면 KisClientError 로 실패한다 (C-2)
# ---------------------------------------------------------------------------


def test_place_buy는_주문번호가_비면_KisClientError를_raise한다(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    settings = _make_settings(monkeypatch)

    # 빈 문자열 케이스
    mock_order = mocker.MagicMock()
    mock_order.number = ""
    fake_kis.account.return_value.buy.return_value = mock_order

    kc = KisClient(settings, pykis_factory=pykis_factory)
    with pytest.raises(KisClientError, match="주문번호"):
        kc.place_buy("005930", qty=1)

    # None 케이스
    mock_order_none = mocker.MagicMock()
    mock_order_none.number = None
    fake_kis.account.return_value.buy.return_value = mock_order_none
    with pytest.raises(KisClientError, match="주문번호"):
        kc.place_buy("005930", qty=1)

    # place_sell 도 동일 계약
    fake_kis.account.return_value.sell.return_value = mock_order
    with pytest.raises(KisClientError, match="주문번호"):
        kc.place_sell("005930", qty=1)


# ---------------------------------------------------------------------------
# 테스트 16: acquire 가 account().buy/sell 보다 먼저 1 회 호출된다
# ---------------------------------------------------------------------------


def test_주문_경로에서_acquire가_account_buy보다_먼저_호출된다(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    fake_kis,
    pykis_factory,
    guard_patch,
) -> None:
    """place_buy / place_sell 에서 acquire 가 account().buy/sell 보다 선행하는지 검증."""
    settings = _make_settings(monkeypatch)

    mock_limiter = mocker.MagicMock(spec=OrderRateLimiter)

    mock_buy_order = mocker.MagicMock()
    mock_buy_order.number = "ORD-B01"
    mock_sell_order = mocker.MagicMock()
    mock_sell_order.number = "ORD-S01"
    fake_kis.account.return_value.buy.return_value = mock_buy_order
    fake_kis.account.return_value.sell.return_value = mock_sell_order

    kc = KisClient(settings, pykis_factory=pykis_factory, order_rate_limiter=mock_limiter)

    # --- place_buy 검증 ---
    # attach_mock 으로 acquire 와 account().buy 호출 순서를 하나의 manager 로 추적한다.
    manager = mocker.MagicMock()
    manager.attach_mock(mock_limiter.acquire, "acquire")
    manager.attach_mock(fake_kis.account.return_value.buy, "buy")

    kc.place_buy("005930", qty=5, price=None)

    # acquire 가 정확히 1 회, label 에 "buy" 와 "005930" 포함
    mock_limiter.acquire.assert_called_once()
    buy_label: str = mock_limiter.acquire.call_args[0][0]
    assert "buy" in buy_label
    assert "005930" in buy_label

    # 호출 순서: acquire → buy
    call_names = [c[0] for c in manager.mock_calls]
    acquire_idx = next(i for i, n in enumerate(call_names) if n == "acquire")
    buy_idx = next(i for i, n in enumerate(call_names) if n == "buy")
    assert acquire_idx < buy_idx, "acquire 가 account().buy 보다 먼저 호출되어야 한다"

    # --- place_sell 검증 ---
    mock_limiter.reset_mock()
    manager2 = mocker.MagicMock()
    manager2.attach_mock(mock_limiter.acquire, "acquire")
    manager2.attach_mock(fake_kis.account.return_value.sell, "sell")

    kc.place_sell("000660", qty=3, price=None)

    mock_limiter.acquire.assert_called_once()
    sell_label: str = mock_limiter.acquire.call_args[0][0]
    assert "sell" in sell_label
    assert "000660" in sell_label

    call_names2 = [c[0] for c in manager2.mock_calls]
    acquire_idx2 = next(i for i, n in enumerate(call_names2) if n == "acquire")
    sell_idx2 = next(i for i, n in enumerate(call_names2) if n == "sell")
    assert acquire_idx2 < sell_idx2, "acquire 가 account().sell 보다 먼저 호출되어야 한다"


# ---------------------------------------------------------------------------
# 테스트 17: qty<=0 가드가 rate limiter 보다 먼저 작동한다
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "qty",
    [0, -1],
    ids=["qty=0", "qty=-1"],
)
def test_qty_0이하_가드가_rate_limiter보다_먼저_작동한다(
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    fake_kis,
    pykis_factory,
    guard_patch,
    qty: int,
) -> None:
    """qty <= 0 인 경우 KisClientError 가 발생하고,
    rate limiter.acquire 와 account().buy 는 모두 호출되지 않아야 한다."""
    settings = _make_settings(monkeypatch)

    mock_limiter = mocker.MagicMock(spec=OrderRateLimiter)
    kc = KisClient(settings, pykis_factory=pykis_factory, order_rate_limiter=mock_limiter)

    with pytest.raises(KisClientError, match="주문 수량"):
        kc.place_buy("005930", qty=qty, price=None)

    mock_limiter.acquire.assert_not_called()
    fake_kis.account.return_value.buy.assert_not_called()

    # place_sell 도 동일하게 검증
    with pytest.raises(KisClientError, match="주문 수량"):
        kc.place_sell("005930", qty=qty, price=None)

    mock_limiter.acquire.assert_not_called()
    fake_kis.account.return_value.sell.assert_not_called()


# ---------------------------------------------------------------------------
# 신규 테스트 18: PendingOrder.qty_filled 필드 매핑 — ADR-0015 결정 1
# ---------------------------------------------------------------------------


class TestPendingOrderQtyFilledMapping:
    """PendingOrder.qty_filled 필드 매핑 시나리오 검증 (ADR-0015 결정 1).

    _to_pending_order 가 PyKis 정식 필드(executed_quantity/pending_quantity)를
    우선 사용하고, 없을 때만 기존 fallback(qty_remaining) 으로 qty_filled 를 추론함을 확인.
    """

    def test_executed_quantity_있으면_qty_filled에_매핑된다(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        fake_kis,
        pykis_factory,
        guard_patch,
    ) -> None:
        """executed_quantity=3, pending_quantity=7, qty=10 → qty_filled=3, qty_remaining=7."""
        settings = _make_settings(monkeypatch)

        order_mock = _make_pending_order_mock(
            mocker,
            number="PO-100",
            symbol="005930",
            side="buy",
            qty=10,
            qty_remaining=7,  # use_pykis_fields=True 면 pending_quantity 로 매핑됨
            qty_filled=3,
            use_pykis_fields=True,
        )
        fake_kis.account.return_value.pending_orders.return_value = [order_mock]

        kc = KisClient(settings, pykis_factory=pykis_factory)
        result = kc.get_pending_orders()

        assert len(result) == 1
        po = result[0]
        assert po.qty_ordered == 10
        assert po.qty_filled == 3  # executed_quantity 를 읽어야 한다
        assert po.qty_remaining == 7  # pending_quantity 를 읽어야 한다

    def test_executed_quantity_없으면_qty_remaining으로_qty_filled를_추론한다(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        fake_kis,
        pykis_factory,
        guard_patch,
    ) -> None:
        """executed_quantity 없이 qty_remaining=4, qty=10 → qty_filled=6 (=10-4) fallback."""
        settings = _make_settings(monkeypatch)

        order_mock = _make_pending_order_mock(
            mocker,
            number="PO-101",
            symbol="005930",
            side="buy",
            qty=10,
            qty_remaining=4,
            use_pykis_fields=False,  # executed_quantity 없는 기존 mock 방식
        )
        fake_kis.account.return_value.pending_orders.return_value = [order_mock]

        kc = KisClient(settings, pykis_factory=pykis_factory)
        result = kc.get_pending_orders()

        assert len(result) == 1
        po = result[0]
        assert po.qty_ordered == 10
        assert po.qty_remaining == 4
        assert po.qty_filled == 6  # qty - qty_remaining = 10 - 4 = 6

    def test_executed_quantity도_pending_quantity도_없으면_qty_filled는_0이다(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        fake_kis,
        pykis_factory,
        guard_patch,
    ) -> None:
        """executed_quantity/pending_quantity 둘 다 없고 qty=10 만 있을 때.

        qty_filled=0, qty_remaining=10 으로 정규화되어야 한다.
        """
        settings = _make_settings(monkeypatch)

        # qty=10, qty_remaining=10 → qty_filled = max(0, 10-10) = 0 (미체결 전량)
        order_mock = _make_pending_order_mock(
            mocker,
            number="PO-102",
            symbol="005930",
            side="buy",
            qty=10,
            qty_remaining=10,
            use_pykis_fields=False,
        )
        fake_kis.account.return_value.pending_orders.return_value = [order_mock]

        kc = KisClient(settings, pykis_factory=pykis_factory)
        result = kc.get_pending_orders()

        assert len(result) == 1
        po = result[0]
        assert po.qty_ordered == 10
        assert po.qty_remaining == 10
        assert po.qty_filled == 0  # 체결 수량 없음

    def test_기존_get_pending_orders_테스트와_호환된다(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        fake_kis,
        pykis_factory,
        guard_patch,
    ) -> None:
        """기존 테스트 8 케이스에 qty_filled 가 추가돼도 다른 필드 계약은 깨지지 않는다."""
        settings = _make_settings(monkeypatch)

        buy_order = _make_pending_order_mock(
            mocker,
            number="PO-001",
            symbol="005930",
            side="buy",
            qty=10,
            qty_remaining=10,
            price=70_000,
        )
        sell_order = _make_pending_order_mock(
            mocker,
            number="PO-002",
            symbol="000660",
            side="sell",
            qty=5,
            qty_remaining=3,
            price=150_000,
        )

        fake_kis.account.return_value.pending_orders.return_value = [buy_order, sell_order]

        kc = KisClient(settings, pykis_factory=pykis_factory)
        result = kc.get_pending_orders()

        assert len(result) == 2
        po_buy = result[0]
        assert po_buy.order_number == "PO-001"
        assert po_buy.qty_ordered == 10
        assert po_buy.qty_remaining == 10
        assert po_buy.qty_filled == 0  # 10 - 10 = 0

        po_sell = result[1]
        assert po_sell.order_number == "PO-002"
        assert po_sell.qty_ordered == 5
        assert po_sell.qty_remaining == 3
        assert po_sell.qty_filled == 2  # 5 - 3 = 2


# ---------------------------------------------------------------------------
# loguru 로그 캡처 픽스처 (test_rate_limiter.py 와 동일 패턴)
# ---------------------------------------------------------------------------


@pytest.fixture
def _loguru_messages() -> Iterator[list[dict[str, Any]]]:
    """loguru 로그 메시지 capture. pytest caplog 는 stdlib logging 만 잡아서 별도 sink."""
    captured: list[dict[str, Any]] = []

    def _sink(message: Any) -> None:
        record = message.record
        captured.append({"level": record["level"].name, "message": record["message"]})

    handler_id = logger.add(_sink, level="INFO")
    try:
        yield captured
    finally:
        logger.remove(handler_id)


# ---------------------------------------------------------------------------
# 신규 테스트 19: KisClient.cancel_order — ADR-0015 결정 2
# ---------------------------------------------------------------------------


class TestCancelOrder:
    """KisClient.cancel_order(order_number) 계약 검증 (ADR-0015 결정 2).

    계약:
    - _require_open 경유 (close 후 KisClientError)
    - OrderRateLimiter.acquire 1회 호출 (라벨에 "cancel" + order_number 포함)
    - 매칭 pending 엔트리의 cancel() 호출
    - 매칭 실패 시 no-op + logger.info
    - 빈 order_number 는 KisClientError fail-fast
    - 라이브러리 예외(RuntimeError 제외)는 KisClientError 래핑
    - 멱등 — 두 번 호출해도 두 번째는 no-op
    """

    def _make_kc(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        *,
        limiter=None,
    ) -> KisClient:
        settings = _make_settings(monkeypatch)
        return KisClient(settings, pykis_factory=pykis_factory, order_rate_limiter=limiter)

    def _make_pending_entry(self, mocker: MockerFixture, number: str) -> object:
        """pending_orders() 에 포함될 엔트리 mock. PyKis KisPendingOrder 구조를 따른다.

        PyKis KisPendingOrder 는 중간 .order 래퍼 없이 entry.number(str) 와
        entry.cancel()(KisCancelableOrderMixin 상속 메서드) 를 직접 노출한다.
        """
        entry = mocker.MagicMock()
        entry.number = number  # str 고정 — MagicMock auto-attr 이 아닌 실제 문자열
        entry.cancel = mocker.MagicMock()  # 명시 세팅 — assert_called_once() 대응
        return entry

    def test_매칭_엔트리가_있을때_cancel이_호출된다(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        fake_kis,
        pykis_factory,
        guard_patch,
    ) -> None:
        """pending_orders 목록에 order_number 매칭 엔트리가 있으면 cancel() 이 호출된다."""
        kc = self._make_kc(monkeypatch, fake_kis, pykis_factory)

        entry = self._make_pending_entry(mocker, "ORD-CANCEL-001")
        fake_kis.account.return_value.pending_orders.return_value = [entry]

        kc.cancel_order("ORD-CANCEL-001")

        entry.cancel.assert_called_once()

    def test_매칭_실패시_no_op이고_로그가_남는다(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        fake_kis,
        pykis_factory,
        guard_patch,
        _loguru_messages: list[dict[str, Any]],
    ) -> None:
        """pending_orders 에 order_number 가 없으면 cancel() 호출 없이 info 로그만 남긴다."""
        kc = self._make_kc(monkeypatch, fake_kis, pykis_factory)

        other_entry = self._make_pending_entry(mocker, "ORD-OTHER-999")
        fake_kis.account.return_value.pending_orders.return_value = [other_entry]

        kc.cancel_order("ORD-NOT-FOUND-001")

        other_entry.cancel.assert_not_called()
        # loguru 로그에 "not_pending" 또는 order_number 가 포함돼야 한다
        assert any(
            "ORD-NOT-FOUND-001" in m["message"] or "not_pending" in m["message"]
            for m in _loguru_messages
        ), f"기대한 로그가 없음. captured={[m['message'] for m in _loguru_messages]}"

    def test_빈_order_number는_KisClientError를_raise한다(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        fake_kis,
        pykis_factory,
        guard_patch,
    ) -> None:
        """order_number 가 빈 문자열이면 pending_orders 조회 없이 KisClientError fail-fast."""
        kc = self._make_kc(monkeypatch, fake_kis, pykis_factory)

        with pytest.raises(KisClientError):
            kc.cancel_order("")

        # 빈 문자열 가드라 account 는 호출되지 않아야 한다
        fake_kis.account.return_value.pending_orders.assert_not_called()

    def test_close_후_호출시_KisClientError를_raise한다(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        fake_kis,
        pykis_factory,
        guard_patch,
    ) -> None:
        """close() 후 cancel_order 호출 시 _require_open 이 KisClientError 를 올린다."""
        kc = self._make_kc(monkeypatch, fake_kis, pykis_factory)
        kc.close()

        with pytest.raises(KisClientError):
            kc.cancel_order("ORD-AFTER-CLOSE")

    def test_rate_limiter_acquire가_1회_호출된다(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        fake_kis,
        pykis_factory,
        guard_patch,
    ) -> None:
        """cancel_order 는 OrderRateLimiter.acquire 를 "cancel {order_number}" 라벨로 1회 호출한다.

        acquire 호출 라벨이 올바른지, 호출 횟수가 정확히 1회인지 검증한다.
        """
        mock_limiter = mocker.MagicMock(spec=OrderRateLimiter)
        kc = self._make_kc(monkeypatch, fake_kis, pykis_factory, limiter=mock_limiter)

        # pending_orders 는 빈 목록 — no-op 경로지만 acquire 는 호출돼야 한다
        fake_kis.account.return_value.pending_orders.return_value = []

        kc.cancel_order("ORD-RATE-001")

        mock_limiter.acquire.assert_called_once()
        label: str = mock_limiter.acquire.call_args[0][0]
        assert "cancel" in label
        assert "ORD-RATE-001" in label

    def test_라이브러리_예외는_KisClientError로_래핑된다(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        fake_kis,
        pykis_factory,
        guard_patch,
    ) -> None:
        """pending_orders() 에서 RuntimeError 외 예외 발생 시 KisClientError 로 래핑된다."""
        kc = self._make_kc(monkeypatch, fake_kis, pykis_factory)

        original = ValueError("KIS 서버 오류")
        fake_kis.account.return_value.pending_orders.side_effect = original

        with pytest.raises(KisClientError) as excinfo:
            kc.cancel_order("ORD-ERR-001")

        assert excinfo.value.__cause__ is original

    def test_RuntimeError는_래핑되지_않고_전파된다(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        fake_kis,
        pykis_factory,
        guard_patch,
    ) -> None:
        """pending_orders() 에서 RuntimeError 발생 시 KisClientError 로 감싸지 않고 그대로 전파."""
        kc = self._make_kc(monkeypatch, fake_kis, pykis_factory)

        rt_err = RuntimeError("paper guard 차단")
        fake_kis.account.return_value.pending_orders.side_effect = rt_err

        with pytest.raises(RuntimeError) as excinfo:
            kc.cancel_order("ORD-RT-001")

        assert not isinstance(excinfo.value, KisClientError)
        assert excinfo.value is rt_err

    def test_멱등성_두번_호출시_두번째는_no_op이다(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        fake_kis,
        pykis_factory,
        guard_patch,
    ) -> None:
        """첫 호출에서 cancel() 후 두 번째 호출에서는 pending 목록이 비어 no-op 경로를 탄다."""
        kc = self._make_kc(monkeypatch, fake_kis, pykis_factory)

        entry = self._make_pending_entry(mocker, "ORD-IDEM-001")

        # 첫 호출: 매칭 엔트리 있음 → cancel() 호출
        fake_kis.account.return_value.pending_orders.return_value = [entry]
        kc.cancel_order("ORD-IDEM-001")
        entry.cancel.assert_called_once()

        # 두 번째 호출: pending 목록이 비어 있음 (이미 취소됨) → no-op
        fake_kis.account.return_value.pending_orders.return_value = []
        kc.cancel_order("ORD-IDEM-001")  # 예외 없이 통과해야 한다

        # cancel 은 첫 호출에서만 1회 호출됐어야 한다
        entry.cancel.assert_called_once()

    def test_매칭_실패시_로그_레벨이_WARNING이다(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mocker: MockerFixture,
        fake_kis,
        pykis_factory,
        guard_patch,
        _loguru_messages: list[dict[str, Any]],
    ) -> None:
        """매칭 실패 시 broker.cancel.not_pending 메시지가 WARNING 레벨로 방출된다.

        silent-failure-hunter C2: 운영 로그 grep 가시성 확보를 위해
        info → warning 으로 승격된 것을 회귀 방지.
        """
        kc = self._make_kc(monkeypatch, fake_kis, pykis_factory)

        other_entry = self._make_pending_entry(mocker, "ORD-OTHER-999")
        fake_kis.account.return_value.pending_orders.return_value = [other_entry]

        kc.cancel_order("ORD-NOT-FOUND-999")

        # WARNING 레벨이면서 not_pending 또는 order_number 가 포함된 로그가 있어야 한다
        warning_msgs = [
            m
            for m in _loguru_messages
            if m["level"] == "WARNING"
            and ("not_pending" in m["message"] or "ORD-NOT-FOUND-999" in m["message"])
        ]
        assert len(warning_msgs) >= 1, (
            f"broker.cancel.not_pending 은 WARNING 레벨이어야 한다 "
            f"(got levels={[m['level'] for m in _loguru_messages]})"
        )


# ---------------------------------------------------------------------------
# 신규 테스트 20: PendingOrder.__post_init__ 가드 4종 — PR #39 리뷰 반영
# ---------------------------------------------------------------------------


class TestPendingOrderPostInitGuards:
    """PendingOrder.__post_init__ 가드 4종 회귀 방지 (PR #39 silent-failure-hunter).

    qty_ordered ≤ 0, qty_filled < 0, qty_remaining < 0,
    qty_filled + qty_remaining != qty_ordered 각각이
    RuntimeError 를 발생시키는지 검증.
    """

    _BASE = dict(
        order_number="PO-GUARD",
        symbol="005930",
        side="buy",
        price=70_000,
        submitted_at=datetime(2026, 4, 21, 9, 30, tzinfo=timezone(timedelta(hours=9))),
    )

    def _make(self, **overrides: Any) -> PendingOrder:
        return PendingOrder(**{**self._BASE, **overrides})  # type: ignore[arg-type]

    def test_정상_생성_가드_통과(self) -> None:
        """qty_ordered=10, qty_filled=3, qty_remaining=7 → 정상 생성."""
        po = self._make(qty_ordered=10, qty_filled=3, qty_remaining=7)
        assert po.qty_ordered == 10
        assert po.qty_filled == 3
        assert po.qty_remaining == 7

    def test_qty_ordered_0이면_RuntimeError(self) -> None:
        """qty_ordered=0 → RuntimeError (양수 위반)."""
        with pytest.raises(RuntimeError, match="qty_ordered"):
            self._make(qty_ordered=0, qty_filled=0, qty_remaining=0)

    def test_qty_ordered_음수이면_RuntimeError(self) -> None:
        """qty_ordered=-1 → RuntimeError."""
        with pytest.raises(RuntimeError, match="qty_ordered"):
            self._make(qty_ordered=-1, qty_filled=0, qty_remaining=0)

    def test_qty_filled_음수이면_RuntimeError(self) -> None:
        """qty_filled=-1 → RuntimeError (0 이상 위반)."""
        with pytest.raises(RuntimeError, match="qty_filled"):
            self._make(qty_ordered=10, qty_filled=-1, qty_remaining=11)

    def test_qty_remaining_음수이면_RuntimeError(self) -> None:
        """qty_remaining=-1 → RuntimeError (0 이상 위반)."""
        with pytest.raises(RuntimeError, match="qty_remaining"):
            self._make(qty_ordered=10, qty_filled=11, qty_remaining=-1)

    def test_sum_불일치이면_RuntimeError(self) -> None:
        """qty_filled(3) + qty_remaining(5) != qty_ordered(10) → RuntimeError."""
        with pytest.raises(RuntimeError, match="qty_ordered"):
            self._make(qty_ordered=10, qty_filled=3, qty_remaining=5)

    def test_전량_미체결은_정상(self) -> None:
        """qty_filled=0, qty_remaining=10, qty_ordered=10 → 미체결 전량, 정상."""
        po = self._make(qty_ordered=10, qty_filled=0, qty_remaining=10)
        assert po.qty_filled == 0
        assert po.qty_remaining == 10

    def test_전량_체결은_정상(self) -> None:
        """qty_filled=10, qty_remaining=0, qty_ordered=10 → 전량 체결, 정상."""
        po = self._make(qty_ordered=10, qty_filled=10, qty_remaining=0)
        assert po.qty_filled == 10
        assert po.qty_remaining == 0
