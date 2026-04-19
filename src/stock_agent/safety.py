"""런타임 안전 가드 모음.

본 모듈은 PyKis 인스턴스에 `request` 래퍼를 설치해 **주문 경로** 호출을
조건부로 차단한다. 두 종류의 가드를 제공한다.

1. `install_paper_mode_guard(kis)` — paper 키를 주입한 PyKis 용.
   - paper 키가 실수로 real 도메인 주문 경로로 흘러가는 것을 방어.
   - 차단: `kwargs["domain"] == "real"` **AND** path 에 `/trading/order` 포함.
   - 통과: real 도메인 read-only 조회(`/quotations/*`, `/trading/inquire-*`) —
     KIS paper 도메인에 해당 API 가 없어 python-kis 가 불가피하게 real 로
     송신하기 때문. 전면 차단하면 `KisClient` 현재가 참조 등 정당한 조회가
     막혀 실행 불가.

2. `install_order_block_guard(kis)` — live 키를 주입한 read-only PyKis 용.
   - `RealtimeDataStore` 는 시세 전용 경로이므로 주문은 절대 나가면 안 된다.
   - 차단: **도메인 무관** path 에 `/trading/order` 포함 시 즉시 `RuntimeError`.
   - 통과: 그 외 모든 호출 (시세·종목정보·잔고 조회 등).
   - 설계 의도: live 키가 있으면 real 주문이 실제로 체결될 수 있으므로,
     `KisClient` 와 분리된 별도 PyKis 인스턴스에 "주문 금지" 벨트를 채운다.

공통 패턴
- `/trading/order` — 국내/해외 주문·정정·취소 경로가 이 접두에 집중됨
  (`order-cash`, `order-credit`, `order-rvsecncl`, 해외 `order`·`order-rvsecncl`).
- `/trading/inquire-*`, `/quotations/*` 는 접두가 달라 매칭되지 않아 통과.

한계
- python-kis 가 새 주문 경로를 도입하면 `_BLOCKED_REAL_PATH_PATTERNS` 를 갱신.
  실전 전환(Phase 4) 때 재검토.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

_BLOCKED_REAL_PATH_PATTERNS: tuple[str, ...] = ("/trading/order",)
"""paper 모드에서 real 도메인으로 호출되면 차단할 path 부분 문자열.

현재는 KIS 국내·해외 주식 주문 경로 공통 접두(`/trading/order`) 하나만으로 충분하다.
- 국내: `/uapi/domestic-stock/v1/trading/order-cash` · `order-credit` · `order-rvsecncl`
- 해외: `/uapi/overseas-stock/v1/trading/order` · `order-rvsecncl`
조회 경로(`/trading/inquire-*`, `/quotations/*`) 는 접두가 달라 매칭되지 않는다.
"""


class _RequestableKis(Protocol):
    """가드 설치에 필요한 최소 인터페이스. PyKis 와 테스트용 더블 모두 만족."""

    def request(self, *args: Any, **kwargs: Any) -> Any: ...


def install_paper_mode_guard(kis: _RequestableKis) -> None:
    """`request(domain="real")` 중 주문 경로만 `RuntimeError` 로 차단.

    PyKis 인스턴스의 `request` 메서드를 같은 시그니처의 가드로 교체한다.
    가드는 다음 조건을 모두 만족하는 호출만 거부한다.

    1. `kwargs.get("domain") == "real"`
    2. 호출 path 가 `_BLOCKED_REAL_PATH_PATTERNS` 중 하나를 부분 문자열로 포함

    그 외 (`domain` 이 `"virtual"`·미지정이거나, 조회 경로 등) 는 원본 `request`
    메서드로 그대로 위임한다. paper 도메인에 API 가 없어 python-kis 가 real 로
    보내는 read-only 조회(시세·종목정보 등)가 막히지 않도록 하기 위함이다.

    Args:
        kis: `request` 를 가진 PyKis (또는 테스트 더블) 인스턴스.

    Raises:
        설치 자체는 예외를 던지지 않음. 실제 차단은 추후 가드된 `request`
        호출이 일어났을 때 `RuntimeError` 로 발생.
    """
    original: Callable[..., Any] = kis.request

    def guarded(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("domain") == "real":
            raw_path = args[0] if args else kwargs.get("path", "<unknown>")
            path_str = str(raw_path)
            if any(pat in path_str for pat in _BLOCKED_REAL_PATH_PATTERNS):
                raise RuntimeError(
                    f"paper 모드에서 실전 주문 경로 호출 차단됨: path={path_str!r}. "
                    "Phase 4 실전 전환 전에는 주문 경로의 domain='real' 호출을 허용하지 않는다."
                )
        return original(*args, **kwargs)

    kis.request = guarded  # type: ignore[method-assign]


def install_order_block_guard(kis: _RequestableKis) -> None:
    """주문 경로 호출을 **도메인 무관** 전면 차단하는 가드.

    read-only PyKis 인스턴스(예: `RealtimeDataStore` 용 live-key 인스턴스) 에
    설치해 해당 인스턴스로는 주문이 절대 나가지 못하게 한다. 차단 조건은
    `install_paper_mode_guard` 와 동일한 `/trading/order` 패턴이지만, 도메인
    조건을 제거해 virtual·real·미지정 전부 막는다.

    Args:
        kis: `request` 를 가진 PyKis (또는 테스트 더블) 인스턴스.
    """
    original: Callable[..., Any] = kis.request

    def guarded(*args: Any, **kwargs: Any) -> Any:
        raw_path = args[0] if args else kwargs.get("path", "<unknown>")
        path_str = str(raw_path)
        if any(pat in path_str for pat in _BLOCKED_REAL_PATH_PATTERNS):
            raise RuntimeError(
                f"read-only PyKis 에서 주문 경로 호출 차단됨: path={path_str!r}. "
                "이 인스턴스는 시세 전용이며 주문은 KisClient(paper 키) 경로로만 허용된다."
            )
        return original(*args, **kwargs)

    kis.request = guarded  # type: ignore[method-assign]
