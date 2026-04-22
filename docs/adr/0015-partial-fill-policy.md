---
date: 2026-04-22
status: 승인됨
deciders: donggyu
related: [0013-sqlite-trading-ledger.md, 0012-monitor-notifier-design.md, 0003-runtime-error-propagation.md, 0008-single-process-only.md]
---

# ADR-0015: 체결조회 API 통합 + 부분체결 정책 — 잔량 취소 + 체결 수량만 원장 기록

## 상태

승인됨 — 2026-04-22.

## 맥락

Phase 3 Executor 는 `OrderSubmitter` Protocol 뒤에서 `LiveOrderSubmitter` 로 주문을 내지만, **체결조회 API 가 정식 통합되지 않은 상태** 였다. `_wait_fill` 은 `get_pending_orders()` 에서 `order_number` 가 사라지면 "전량 체결" 로 간주한다 — 이 단순 가정이 모의투자 운영 중 다음 시나리오에서 원장·포지션 정합을 깨뜨릴 수 있다.

1. **부분체결 + 잔량 자동 취소**: KIS 시장가 주문이라도 종목·시각·유동성에 따라 일부만 체결되고 나머지가 미체결 상태로 남을 수 있다 — 장중 일시적 체결 공백.
2. **사용자 수동 취소**: 운영자가 MTS 앱에서 직접 취소 → 해당 주문 번호가 pending 목록에서 사라짐 → Executor 는 "전량 체결" 로 오인 → RiskManager 에 full qty 로 `record_entry` → 잔고와 불일치 → reconcile 시점에 halt (유령 포지션).
3. **데이터 필드 미매핑 버그**: `broker/kis_client.py::_to_pending_order` 의 `qty_remaining` 매핑이 `getattr(order, "qty_remaining", qty)` 로 되어 있어 PyKis 실제 필드(`pending_quantity` / `pending_qty`) 와 일치하지 않는다. 실운영에서는 항상 `qty` 로 fallback 되어 **부분체결 정보가 조용히 사라지고** 있었다(테스트 headless 에서는 mock 객체에 `qty_remaining` 속성을 직접 세팅해서 통과).

구현 경계에서 아래 네 가지 설계 결정이 필요했다.

1. **체결조회 API 범위**: 별도 체결내역(`daily_orders`) API 를 도입할지 vs 기존 `get_pending_orders()` 의 필드 정상 매핑(`executed_quantity`/`pending_quantity`) 으로 충분할지.
2. **취소 API 위치**: `broker/` 에 취소 경로 추가 후 `OrderSubmitter` Protocol 로 드러낼지 vs Executor 가 `KisClient` 를 직접 의존하도록 타협할지.
3. **부분체결 시 잔량 처리 정책**: 잔량 재주문(추가 주문 제출) vs 즉시 취소. 데이트레이딩 맥락(분봉 기반 ORB 전략, 15:00 강제청산) 과의 정합.
4. **`_handle_entry` / `_handle_exit` 의 부분체결 계약**: 진입·청산 경로에서 체결 수량이 요청 수량보다 작을 때 각각 어떻게 다룰지 — 특히 ORB 전략의 "1일 1회 진입" 제약과의 상호작용.

검토한 대안:

- **별도 `daily_orders()` API 호출로 체결 정확도 향상**: 체결가(체결단가) 를 실측해서 비용 모델의 슬리피지 가정(`slippage_rate = 0.001`) 을 교정하는 방향. 장점: 백테스트 대비 실전 괴리 정량화. 단점: (a) KIS API 호출 1 건 추가 (주문당 체결 확정 시점마다), (b) 비용 모델 변경은 ADR-0006 재협상 범위, (c) 현재는 단순 슬리피지 가정으로도 충분히 검증 가능. → **범위 밖**. V1 은 `get_pending_orders()` 의 `executed_quantity`/`pending_quantity` 만 이용해 "얼마나 체결되었는지" 를 판별한다. 체결가 정확도 개선은 모의투자 10영업일 운영 결과 슬리피지 이탈이 관찰될 때 후속 ADR 로 확장.

- **잔량 재주문** (남은 수량만큼 시장가 재제출): 유동성이 일시적으로 끊긴 상황에서 진입 기회를 놓치지 않을 수 있음. 단점: (a) 같은 분봉 내에서 두 번 주문하면 ORB 전략의 "1일 1회 진입" 계약을 우회하게 된다 — 엄밀한 의미의 "분봉 close 돌파 1회" 시그널 계약이 희석됨. (b) 부분체결 사건 자체가 유동성 이상 신호일 수 있어 무지성 재주문은 악화시킬 가능성. (c) Executor 가 체결 수량을 추적하며 재주문 루프를 돌려야 해 상태 복잡도가 증가. → **거부**. 데이트레이딩 맥락에서는 "진입 실패 = 그 종목은 오늘 포기" 가 안전한 기본선.

- **Executor 가 `KisClient.cancel_order` 를 직접 호출**: Protocol 의존성 역전을 깨뜨림 (ADR-0012 와 충돌). 드라이런 경로가 분기 없이 유지되지 못함. → **거부**.

- **체결 수량 원장 보정 없이 ExecutorError 일률 승격**: 부분체결 시 전부 실패로 처리. 장점: 단순. 단점: (a) 잔고에는 실제로 체결된 수량이 존재하므로 다음 reconcile 에서 mismatch → halt 가 되어 운영 중단 빈도가 증가, (b) 실체결분을 RiskManager·storage 에 기록하지 않으면 일일 PnL 집계도 빗나감. → **거부**. 진입 경로는 체결된 만큼 기록하는 방향이 합리적. 청산 경로는 별도 취급(아래 결정 6).

추가 고려 사항 — ORB 전략의 `_entered_today` 플래그가 이미 "1일 1회 진입" 을 강제하므로, `_handle_entry` 부분체결 시 `_open_lots[symbol]` 키를 세팅하면 다음 분봉에서 재진입 시그널이 생성되지 않는다. 즉 "잔량 취소 + 다음 분봉 재진입 금지" 는 Executor 에 추가 게이트를 두지 않아도 전략·내부 상태만으로 성립. `RiskManager.record_exit` 는 qty 인자를 받지 않고 단순히 포지션을 전량 제거 + 실현 PnL 만 누적하는 구조 → 부분 청산을 정합하게 기록하려면 RiskManager API 확장이 필요하지만, 청산 부분체결은 극히 드문 사건이므로 별도 대응(결정 6) 으로 간접 처리.

## 결정

1. **`PendingOrder` DTO 에 `qty_filled: int` 필드 추가** + `_to_pending_order` 매핑을 PyKis 정식 필드(`executed_quantity` / `pending_quantity`) 로 정정. 기존 `qty_remaining` 계산은 `executed_quantity` 가 없을 때만 fallback 으로 유지(테스트 mock 호환). `qty_filled + qty_remaining == qty_ordered` 불변은 체결 비율에 따라 근사 성립 — PyKis 가 실전에서 재집계한 값을 그대로 신뢰. 정확도 검증은 모의투자 1주 운영 후 재평가.

2. **`KisClient.cancel_order(order_number: str) -> None`** 신설. 내부적으로 `account().pending_orders()` 를 재조회해 주문번호 매칭 객체의 `.cancel()` (PyKis `KisCancelableOrderMixin`) 을 호출. 매칭 실패(이미 체결·취소) 시 no-op + `logger.info` — **멱등**. `OrderRateLimiter` 를 경유해 계좌 단위 상한 준수. 에러 래핑은 기존 `_call` 경로 재사용 (`KisClientError` 로 통일).

3. **`OrderSubmitter` Protocol 에 `cancel_order(order_number: str) -> None`** 추가. `LiveOrderSubmitter.cancel_order` 는 `KisClient.cancel_order` 위임. `DryRunOrderSubmitter.cancel_order` 는 `logger.info` + no-op. 단위 테스트용 `FakeOrderSubmitter` 도 동일 시그니처 구현.

4. **`Executor._wait_fill` → `_resolve_fill(ticket) -> _FillOutcome`**. 내부 `@dataclass(frozen=True, slots=True)` DTO 로 `_FillOutcome(filled_qty: int, status: Literal["full","partial","none"])` 를 반환. 폴링 중 `ticket.order_number` 가 pending 에서 사라지면 `status="full"` + `filled_qty=ticket.qty`. 타임아웃 도달 시 마지막 pending 레코드의 `qty_filled` 값으로 `cancel_order` 호출 후 `filled_qty > 0 → "partial"`, `filled_qty == 0 → "none"`. `ExecutorError` 를 더 이상 타임아웃 자체로 raise 하지 않는다 — 잔량 취소까지가 정상 수습 경로.

5. **`_handle_entry` 부분체결 정책**:
   - `status == "full"` → 기존 동일 (요청 qty 전량 기록).
   - `status == "partial"` → `RiskManager.record_entry(symbol, entry_fill_price, filled_qty, now)` + `_open_lots[symbol] = _OpenLot(entry_fill_price, filled_qty)` + `EntryEvent.qty = filled_qty` + `logger.warning` (부분체결 사실 로그). ORB 전략의 `_entered_today[symbol]` 이 이미 True 로 세팅되어 있어 다음 분봉 재진입은 자동 차단 — 추가 게이트 없음.
   - `status == "none"` → 아무 것도 기록하지 않고 `return False` + `logger.info`. ORB 전략은 `_entered_today` 세팅까지는 진행되므로 같은 날 재진입 안 됨 (전략 상태 보존). 운영자가 재진입을 원하면 수동 개입 + `start_session` 재호출.

6. **`_handle_exit` 부분체결 정책**: `status != "full"` → `ExecutorError` 승격 (원인 포함 메시지). RiskManager 의 `record_exit` 가 qty 분할을 지원하지 않고, 청산 부분체결은 운영 상 예외 사건 — 잔고 실체가 줄어든 상태에서 원장과 불일치가 발생하므로 다음 `reconcile()` 이 자동 halt 를 트리거한다. 운영자가 수동 정리(MTS 에서 잔량 청산 또는 취소 확인) 후 다음 세션 `start_session` 으로 리셋. 자동 재시도 금지(ADR-0003 기조 — 운영자 개입 경로).

7. **ORB 전략 재진입 금지는 전략 계약에 위임** — Executor 는 `_entered_today` 를 다시 체크하지 않는다. 본 ADR 은 `ORBStrategy._entered_today` 플래그에 의존하며 중복 게이트를 추가하지 않는다. 미래에 다른 전략이 도입되면 해당 전략 자체에서 "같은 분봉·같은 종목 재진입 금지" 계약을 보장해야 한다.

8. **`EntryEvent.qty` / `ExitEvent.qty` 는 "실제 체결 수량"** — 주문 요청 수량이 아니라 `_FillOutcome.filled_qty` 를 주입. storage 원장(ADR-0013) 의 `orders.qty` 도 실체결 수량 기준으로 기록. 이 결정으로 storage 와 RiskManager, _open_lots 의 모든 qty 가 동일 소스(실체결) 로 정렬된다.

9. **`cancel_order` 는 `_with_backoff` 경로 통과** — `KisClientError` 일시 장애 시 기본 100→200→400 ms 재시도. 한계 초과 시 `ExecutorError` 로 승격되지만 호출 시점은 이미 `_resolve_fill` 의 수습 경로이므로 상위로 전파되어도 `step` 만 실패 (다음 step 에서 reconcile 이 실상을 재포착).

## 결과

**긍정**
- `get_pending_orders()` 의 필드 매핑 버그(qty_remaining 미매핑) 가 교정되어 운영 데이터 정확도 회복. 모의투자 중 부분체결이 실제로 발생하면 로그·storage 양쪽에서 가시화됨.
- Executor 의 Protocol 의존성 역전 유지 — 드라이런·단위 테스트 경로가 추가 분기 없이 계속 통과.
- 시장가 타임아웃이 더 이상 `ExecutorError` 로 세션 중단을 강요하지 않고, 체결된 만큼은 원장에 반영되는 수습 경로가 확보됨. 잔량 취소가 멱등이라 네트워크 flake 재시도도 안전.
- 청산 부분체결 시 `reconcile` mismatch → halt 로 자동 전이되어 운영자 개입 경로가 명시적. silent 진행으로 인한 포지션 오염 없음.
- `EntryEvent.qty` 가 실체결 수량이 되어 storage 원장(ADR-0013), 텔레그램 알림(ADR-0012), RiskManager 활성 포지션 3군데가 한 소스 기준으로 정렬됨.

**부정**
- 부분체결 시 `_open_lots[symbol].qty` 가 요청보다 작아진다 — 청산 시점의 PnL 계산은 `lot.qty` 기준이므로 자동으로 정합하지만, ORB 전략이 `entry_qty` 를 내부에서 추적하지 않으므로 strategy 쪽에서 "내가 낸 signal qty 와 실체결 qty 의 괴리" 를 감지할 수 없다. 전략 진화 시 이 경로를 어떻게 피드백할지 재평가 필요.
- `cancel_order` 가 멱등이지만 네트워크 장애 중 "취소 응답 유실 + 실제로는 취소됨" 과 "취소 실패 + 실제로 미취소" 를 구분하지 못한다. `_with_backoff` 한계 초과 시 `ExecutorError` 승격 → 다음 step 의 reconcile 이 잔고 실상과 비교 → 필요 시 halt. 양방향 안전 벨트로 감수.
- 청산 부분체결 시 자동 잔량 청산을 하지 않는다(결정 6). 운영자 개입 대기 중 추가 손절 기회를 놓칠 수 있으나, 자동 재청산은 (a) RiskManager API 확장, (b) "청산 중 다시 청산" 동시성 문제 두 가지를 도입해야 해 현재 범위 밖.

**중립**
- `_FillOutcome` 을 Executor 내부 DTO 로 숨김 — 공개 심볼 증가 억제. 외부 테스트는 `EntryEvent.qty` 와 `StepReport.orders_submitted` 로 결과 확인.
- 체결가 정확도(실체결단가 vs 슬리피지 추정) 개선은 본 ADR 범위 밖 — 모의투자 10영업일 운영에서 백테스트 대비 괴리가 관찰되면 후속 ADR 로 `daily_orders()` 통합 검토.

## 추적

- 코드: `src/stock_agent/broker/kis_client.py` (PendingOrder.qty_filled, cancel_order), `src/stock_agent/broker/__init__.py`, `src/stock_agent/execution/executor.py` (_FillOutcome, _resolve_fill, cancel_order Protocol, _handle_entry/_handle_exit 부분체결 분기), `src/stock_agent/execution/__init__.py`.
- 테스트: `tests/test_kis_client.py` (PendingOrder 필드 매핑·cancel_order 경로·멱등성), `tests/test_executor.py` (_resolve_fill 상태별·부분체결 진입·청산 에러·OrderSubmitter Protocol 확장).
- 관련 ADR: [ADR-0003](./0003-runtime-error-propagation.md) (RuntimeError 전파 기조), [ADR-0006](./0006-cost-model-rates.md) (슬리피지 0.1% 가정 — 본 ADR 범위 밖 미조정), [ADR-0012](./0012-monitor-notifier-design.md) (Protocol 의존성 역전 원칙), [ADR-0013](./0013-sqlite-trading-ledger.md) (order_number PK + EntryEvent/ExitEvent DTO).
- 문서: [broker/CLAUDE.md](../../src/stock_agent/broker/CLAUDE.md), [execution/CLAUDE.md](../../src/stock_agent/execution/CLAUDE.md), [plan.md](../../plan.md), root [CLAUDE.md](../../CLAUDE.md).
- 도입 PR: TBD (이슈 #32).
