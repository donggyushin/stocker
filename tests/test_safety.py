"""install_paper_mode_guard / install_order_block_guard 단위 테스트.

paper_mode_guard 동작 기준:
- domain="real" + 주문 경로(/trading/order 포함) → RuntimeError 차단
- domain="real" + 조회 경로(/trading/inquire-*, /quotations/*) → 통과
- domain="virtual" 또는 domain 미지정 → 항상 통과

order_block_guard 동작 기준 (RealtimeDataStore live-key 인스턴스용):
- 도메인 무관 + 주문 경로(/trading/order 포함) → RuntimeError 차단
- 그 외 모든 경로 → 통과
"""

from __future__ import annotations

from typing import Any

import pytest

from stock_agent.safety import install_order_block_guard, install_paper_mode_guard


class _FakeKis:
    """PyKis 의 request 메서드만 흉내내는 최소 더블."""

    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def request(self, *args: Any, **kwargs: Any) -> str:
        self.calls.append((args, kwargs))
        return "ok"


# ---------------------------------------------------------------------------
# 차단 케이스 (real + 주문 경로)
# ---------------------------------------------------------------------------


def test_real_국내주문_order_cash_차단() -> None:
    """domain=real + /trading/order-cash → RuntimeError, 원본 미호출."""
    kis = _FakeKis()
    install_paper_mode_guard(kis)

    with pytest.raises(RuntimeError, match="paper 모드에서 실전 주문 경로"):
        kis.request(
            "/uapi/domestic-stock/v1/trading/order-cash",
            method="POST",
            domain="real",
        )

    assert kis.calls == [], "차단된 호출은 원본 request 에 도달하지 않아야 한다"


def test_real_국내주문_정정취소_order_rvsecncl_차단() -> None:
    """domain=real + /trading/order-rvsecncl → 차단."""
    kis = _FakeKis()
    install_paper_mode_guard(kis)

    with pytest.raises(RuntimeError, match="paper 모드에서 실전 주문 경로"):
        kis.request(
            "/uapi/domestic-stock/v1/trading/order-rvsecncl",
            method="POST",
            domain="real",
        )

    assert kis.calls == []


def test_real_해외주문_order_차단() -> None:
    """domain=real + /uapi/overseas-stock/v1/trading/order → 차단."""
    kis = _FakeKis()
    install_paper_mode_guard(kis)

    with pytest.raises(RuntimeError, match="paper 모드에서 실전 주문 경로"):
        kis.request(
            "/uapi/overseas-stock/v1/trading/order",
            method="POST",
            domain="real",
        )

    assert kis.calls == []


# ---------------------------------------------------------------------------
# 통과 케이스 (real + 조회 경로)
# ---------------------------------------------------------------------------


def test_real_시세조회_quotations_통과() -> None:
    """domain=real + /quotations/inquire-price → 원본 호출·결과 반환."""
    kis = _FakeKis()
    install_paper_mode_guard(kis)

    result = kis.request(
        "/uapi/domestic-stock/v1/quotations/inquire-price",
        method="GET",
        domain="real",
    )

    assert result == "ok"
    assert len(kis.calls) == 1


def test_real_매수가능조회_inquire_psbl_order_통과() -> None:
    """domain=real + /trading/inquire-psbl-order → 통과.

    path 에 'order' 문자열이 있어도 '/trading/order' 부분 문자열이 없으면 차단하지 않는다.
    """
    kis = _FakeKis()
    install_paper_mode_guard(kis)

    result = kis.request(
        "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
        method="GET",
        domain="real",
    )

    assert result == "ok"
    assert len(kis.calls) == 1


def test_real_잔고조회_inquire_balance_통과() -> None:
    """domain=real + /trading/inquire-balance → 통과."""
    kis = _FakeKis()
    install_paper_mode_guard(kis)

    result = kis.request(
        "/uapi/domestic-stock/v1/trading/inquire-balance",
        method="GET",
        domain="real",
    )

    assert result == "ok"
    assert len(kis.calls) == 1


# ---------------------------------------------------------------------------
# 통과 케이스 (virtual / 미지정 도메인)
# ---------------------------------------------------------------------------


def test_virtual_도메인_호출은_원본에_그대로_위임된다() -> None:
    """domain=virtual → 주문 경로라도 가드를 통과해 원본 호출."""
    kis = _FakeKis()
    install_paper_mode_guard(kis)

    result = kis.request("/uapi/foo", method="GET", domain="virtual")

    assert result == "ok"
    assert kis.calls == [(("/uapi/foo",), {"method": "GET", "domain": "virtual"})]


def test_domain_미지정_호출은_원본에_그대로_위임된다() -> None:
    """domain kwarg 없음 → 통과. PyKis 기본 라우팅(None → virtual)은 건드리지 않는다."""
    kis = _FakeKis()
    install_paper_mode_guard(kis)

    result = kis.request("/uapi/bar", method="GET")

    assert result == "ok"
    assert kis.calls == [(("/uapi/bar",), {"method": "GET"})]


# ---------------------------------------------------------------------------
# kwarg 전달 방식 일관성
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, domain, should_block",
    [
        (
            "/uapi/domestic-stock/v1/trading/order-cash",
            "real",
            True,
        ),
        (
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "real",
            False,
        ),
    ],
    ids=["kwarg-path-주문-차단", "kwarg-path-시세-통과"],
)
def test_path를_kwarg로_전달해도_동일_판정(path: str, domain: str, should_block: bool) -> None:
    """positional 대신 path= kwarg 로 전달해도 차단/통과 판정이 동일해야 한다."""
    kis = _FakeKis()
    install_paper_mode_guard(kis)

    if should_block:
        with pytest.raises(RuntimeError, match="paper 모드에서 실전 주문 경로"):
            kis.request(path=path, domain=domain)
        assert kis.calls == []
    else:
        result = kis.request(path=path, domain=domain)
        assert result == "ok"
        assert len(kis.calls) == 1


# ---------------------------------------------------------------------------
# 에러 메시지 품질
# ---------------------------------------------------------------------------


def test_에러_메시지에_차단된_path_포함() -> None:
    """차단 시 에러 문자열에 실제 path 가 담겨 디버깅이 용이해야 한다."""
    path = "/uapi/domestic-stock/v1/trading/order-cash"
    kis = _FakeKis()
    install_paper_mode_guard(kis)

    with pytest.raises(RuntimeError, match=path):
        kis.request(path, domain="real")


# ===========================================================================
# install_order_block_guard 전용 테스트
# ===========================================================================

# ---------------------------------------------------------------------------
# 차단 케이스 (도메인 무관 + 주문 경로)
# ---------------------------------------------------------------------------


def test_order_block_guard_real_도메인_주문경로_차단() -> None:
    """domain="real" + /trading/order-cash → RuntimeError 차단."""
    kis = _FakeKis()
    install_order_block_guard(kis)

    with pytest.raises(RuntimeError):
        kis.request(
            "/uapi/domestic-stock/v1/trading/order-cash",
            method="POST",
            domain="real",
        )

    assert kis.calls == [], "차단된 호출은 원본 request 에 도달하지 않아야 한다"


def test_order_block_guard_virtual_도메인_주문경로도_차단() -> None:
    """domain="virtual" + /trading/order-cash → 차단.

    install_paper_mode_guard 와 달리 virtual 도메인도 막는다.
    """
    kis = _FakeKis()
    install_order_block_guard(kis)

    with pytest.raises(RuntimeError):
        kis.request(
            "/uapi/domestic-stock/v1/trading/order-cash",
            method="POST",
            domain="virtual",
        )

    assert kis.calls == []


def test_order_block_guard_domain_미지정_주문경로_차단() -> None:
    """domain kwarg 없어도 /trading/order-cash → 차단."""
    kis = _FakeKis()
    install_order_block_guard(kis)

    with pytest.raises(RuntimeError):
        kis.request(
            "/uapi/domestic-stock/v1/trading/order-cash",
            method="POST",
        )

    assert kis.calls == []


# ---------------------------------------------------------------------------
# 통과 케이스 (조회 경로)
# ---------------------------------------------------------------------------


def test_order_block_guard_real_도메인_시세조회_통과() -> None:
    """domain="real" + /quotations/inquire-price → 통과."""
    kis = _FakeKis()
    install_order_block_guard(kis)

    result = kis.request(
        "/uapi/domestic-stock/v1/quotations/inquire-price",
        method="GET",
        domain="real",
    )

    assert result == "ok"
    assert len(kis.calls) == 1


def test_order_block_guard_virtual_도메인_잔고조회_통과() -> None:
    """domain="virtual" + /trading/inquire-balance → 통과."""
    kis = _FakeKis()
    install_order_block_guard(kis)

    result = kis.request(
        "/uapi/domestic-stock/v1/trading/inquire-balance",
        method="GET",
        domain="virtual",
    )

    assert result == "ok"
    assert len(kis.calls) == 1


# ---------------------------------------------------------------------------
# kwarg 전달 방식 및 에러 메시지 품질
# ---------------------------------------------------------------------------


def test_order_block_guard_path를_kwarg로_전달해도_차단() -> None:
    """positional 대신 path= kwarg 로 주문 경로를 전달해도 차단된다."""
    kis = _FakeKis()
    install_order_block_guard(kis)

    with pytest.raises(RuntimeError):
        kis.request(path="/uapi/domestic-stock/v1/trading/order-cash", method="POST")

    assert kis.calls == []


def test_order_block_guard_에러_메시지에_read_only_및_path_포함() -> None:
    """차단 시 에러 메시지에 "read-only" 또는 "주문 경로" 뉘앙스와 실제 path 가 담겨야 한다."""
    path = "/uapi/domestic-stock/v1/trading/order-cash"
    kis = _FakeKis()
    install_order_block_guard(kis)

    with pytest.raises(RuntimeError) as exc_info:
        kis.request(path, domain="real")

    error_msg = str(exc_info.value)
    # 에러 메시지에 차단된 path 포함 확인
    assert path in error_msg
    # "read-only" 또는 "주문 경로" 중 하나 이상 포함
    assert "read-only" in error_msg or "주문 경로" in error_msg
