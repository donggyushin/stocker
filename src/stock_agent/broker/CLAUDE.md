# broker — KIS Developers API 래퍼

stock-agent 의 KIS Developers REST/WebSocket 경계 모듈. `python-kis 2.x` 를 감싸
정규화된 DTO 만 상위 레이어(execution/risk/strategy)에 노출한다.

> 이 파일은 root [CLAUDE.md](../../../CLAUDE.md) 의 하위 문서이다. 프로젝트 전반
> 규약·리스크 고지·승인된 결정 사항은 root 문서를 따른다.

## 공개 심볼 (`broker/__init__.py`)

`KisClient`, `KisClientError`, `BalanceSnapshot`, `OrderTicket`, `PendingOrder`, `Holding`

## 현재 상태 (2026-04-19 기준)

- **`kis_client.py`** — 완료 (Phase 1 첫 산출물)
  - 공개 API 4종: `get_balance()`, `place_buy()`, `place_sell()`, `get_pending_orders()`, 보조로 `ensure_token()` 과 `close()`
  - 정규화 DTO: `BalanceSnapshot`, `OrderTicket`, `PendingOrder`, `Holding` (`@dataclass(frozen=True, slots=True)`)
  - 컨텍스트 매니저(`__enter__`/`__exit__`) 지원. `close()` 는 멱등.
  - paper 완전 지원. `kis_env == "live"` 시 `NotImplementedError` 즉시 — Phase 4 에서 Settings 에 `kis_live_app_key/secret` 필드 추가 후 구현.
  - 예외 정책: `RuntimeError`(설정 오류)는 래핑 없이 전파, 그 외 `Exception` 은 `KisClientError(... from e)` + loguru `exception` 로그.
  - 의존성 주입(`pykis_factory`) 으로 테스트에서 실제 `pykis.PyKis` import·네트워크·파일 I/O 를 원천 차단.
  - 데이터 무결성 가드:
    - 미체결 주문 `side` 판별 실패 시 `KisClientError` (매도→매수 오인 차단)
    - 주문 제출 응답의 `order_number` 가 비면 `KisClientError` (유령 포지션 차단)
    - `qty <= 0` 은 라이브러리 호출 전 사전 거부
  - pytest 15 케이스로 공개 계약 잠금 (paper/live 분기, DTO 정규화, 에러 래핑, 컨텍스트 매니저, 가드 3종).

- **`rate_limiter.py`** — 미착수 (Phase 1 잔여 작업).

## 설계 원칙

- **라이브러리 타입 누출 금지**. python-kis 의 `KisBalance`/`KisOrder`/`KisPendingOrders` 는 내부에서만 소비하고, 상위 레이어는 broker 공개 심볼만 의존한다.
- **얇은 래퍼**. 토큰 캐시·레이트 제한은 python-kis 내부(`keep_token=True`, 내장 RateLimiter)에 1차 위임. 후속 `rate_limiter.py` 가 주문 경로를 감쌀 여지만 남긴다.
- **시장가 표현**: `price=None` (python-kis 2.x 의 시장가 분기로 매핑).
- **시장 고정**: `market="KRX"` (KOSPI/KOSDAQ 모두 KRX 산하).
- **안전 가드**: 생성자에서 `install_paper_mode_guard`(`stock_agent.safety`) 를 paper 모드 한정으로 자동 설치한다. paper 모드에서 `request(domain="real")` 호출이 섞이면 즉시 `RuntimeError`.

## 테스트 정책

- 실제 KIS 네트워크·주문·파일 I/O 는 절대 발생시키지 않는다.
- `pykis_factory` 에 `pytest-mock` 의 `MagicMock` 반환 팩토리를 주입.
- `install_paper_mode_guard` 는 `stock_agent.broker.kis_client` 네임스페이스에서 patch (원본 `stock_agent.safety` 를 patch 하면 이미 바인딩된 참조는 바뀌지 않음).
- 관련 테스트 파일: `tests/test_kis_client.py`.

## 소비자 참고

- `scripts/healthcheck.py` 는 `with KisClient(settings) as kc: kc.get_balance()` 패턴으로 이 모듈을 사용한다. 로그 포맷·exit code 규약은 Phase 0 호환을 유지한다.
- Phase 4 실전 전환 시 수정 범위: `Settings` 에 `kis_live_app_key/secret` 추가 → `KisClient._build_pykis` 의 live 분기 구현 → `install_paper_mode_guard` 는 live 에서는 설치하지 않음.

## 범위 제외 (의도적 defer)

- `cancel_order` / 주문 정정 — 후속 PR.
- `broker/rate_limiter.py` — 별도 산출물. `KisClient` 공개 메서드 경계를 침범하지 않도록 설계.
- WebSocket 실시간 시세 — `data/realtime.py` 범위.
