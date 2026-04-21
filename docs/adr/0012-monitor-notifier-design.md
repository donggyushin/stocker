---
date: 2026-04-21
status: 승인됨
deciders: donggyu
related: [0011-apscheduler-adoption-single-process.md, 0003-runtime-error-propagation.md]
---

# ADR-0012: monitor/notifier 모듈 설계 — Protocol 분리·StepReport 이벤트 확장·silent fail 정책

## 상태

승인됨 — 2026-04-21.

## 맥락

Phase 3 세 번째 산출물 — 진입·청산·에러·일일 요약 4종 텔레그램 알림. ADR-0011 결정 5("콜백 4종 예외 re-raise 금지") 의 연장선에서 "운영자가 실시간으로 상태를 감지할 경로" 가 필요했다. `main.py` 의 loguru sink 만으로는 사용자가 모의투자 10영업일을 감시하기 위해 항상 단말 로그를 지켜보게 되므로 실용적이지 못하다.

구현 경계에서 세 가지 설계 결정이 필요했다.

1. **이벤트 전파 경로**: Executor 가 직접 notifier 를 호출할지(책임 결합), 아니면 `StepReport` 에 이벤트 tuple 을 실어 main.py 콜백에서 notifier 를 호출할지(Protocol 의존성 역전 유지).
2. **전송 실패 정책**: 텔레그램 봇·네트워크 장애 시 예외를 호출자에게 던질지, silent fail 할지. 콜백 재진입 금지 원칙과의 관계.
3. **드라이런 모드 처리**: `--dry-run` 시 실제 전송을 할지(end-to-end 검증 목적), 아니면 NullNotifier 주입으로 건너뛸지.

추가 고려 사항 — `scripts/healthcheck.py:84-98` 에 이미 검증된 `Bot + async with + asyncio.run` 패턴이 있어 재사용 가능, `python-telegram-bot>=21.0` 의존성은 Phase 0 부터 확보돼 있어 추가 비용 없음.

검토한 대안:
- **Executor 에 optional `notifier: Notifier | None` 주입**: Executor 가 체결 확정 시점에서 직접 호출. 책임 집중도는 높지만 `Executor` 의 Protocol 의존성 역전 원칙(`OrderSubmitter` / `BalanceProvider` / `BarSource`) 과 어긋난다. 텔레그램 전송 실패가 Executor 내부 상태를 오염시킬 위험도 존재.
- **logger sink 기반 라우팅**: loguru 의 커스텀 sink 에 notifier 를 연결. 메시지 파싱이 문자열 기반이라 타입 안전성 손실, 포맷 변경 시 무음 실패 경로 추가.
- **MarkdownV2 포맷**: 굵은 글씨·코드 블록 사용 가능. 그러나 특수문자 escape 실패 시 Telegram API 가 400 을 반환해 전송 자체가 거부됨 — 포맷 오류가 알림 누락으로 전환되는 경로가 생긴다.

## 결정

1. **모듈 경계 신설** — `src/stock_agent/monitor/` 패키지. 공개 심볼: `Notifier` Protocol, `TelegramNotifier`, `NullNotifier`, `ErrorEvent`, `DailySummary`. `Notifier` 는 `@runtime_checkable` Protocol 로 선언해 `NullNotifier` / `TelegramNotifier` / `MagicMock(spec=Notifier)` 모두 자연 만족.

2. **StepReport 확장** — `execution/executor.py` 의 `StepReport` 에 `entry_events: tuple[EntryEvent, ...] = ()` + `exit_events: tuple[ExitEvent, ...] = ()` 두 필드 추가. `EntryEvent` / `ExitEvent` DTO 는 `executor.py` 정본, `execution/__init__` 재노출. Executor 는 sweep 시작 시 list 를 초기화하고 `_handle_entry` / `_handle_exit` 성공 경로에서 append, 반환 직전 tuple 로 고정. 기존 로그(`executor.entry.filled` / `executor.exit.filled`) 는 감사 추적 목적으로 유지 — 알림 채널과 로그 채널은 독립.

3. **Protocol 의존성 역전 유지** — Executor 는 notifier 존재 자체를 모른다. `main.py` 콜백 4종이 `report.entry_events` / `exit_events` 를 순회해 `runtime.notifier.notify_*` 호출. `build_runtime` 에 `notifier_factory: Callable[[Settings, bool], Notifier] | None` 파라미터 주입으로 단위 테스트에서 실 `TelegramNotifier` / 네트워크 접촉 0.

4. **`Executor.last_reconcile: ReconcileReport | None` 프로퍼티 신설** — `reconcile()` 호출 시마다 캐시 갱신. `_on_daily_report` 콜백이 추가 네트워크 호출 없이 최신 mismatch 상태를 `DailySummary.mismatch_symbols` 에 담도록.

5. **전송 실패 silent fail + 연속 실패 dedupe 경보** — `TelegramNotifier._send` 는 `TelegramError` / `asyncio.TimeoutError` / 기타 `Exception` 각각 `logger.exception` 만 남기고 재전파하지 않는다. ADR-0011 결정 5(콜백 예외 re-raise 금지) 의 연장 — 알림 전송 실패가 세션 연속성을 죽이면 안 된다. `_consecutive_failures` 카운터가 `consecutive_failure_threshold`(기본 5) 에 도달하면 `logger.critical` 1회만 방출(`_persistent_alert_emitted` 로 dedupe). 전송 성공 시 카운터·dedupe 플래그 리셋. `data/realtime.py` 의 `polling_consecutive_failures` 패턴과 동일 설계.

6. **드라이런도 실전송 + `[DRY-RUN]` 프리픽스** — `--dry-run` 이어도 `TelegramNotifier(dry_run=True)` 가 모든 메시지 제목 맨 앞에 `[DRY-RUN] ` 프리픽스를 붙여 실전송한다. 알림 경로 자체를 end-to-end 검증할 수 있게 하려는 결정 — 모의투자 운영 중 채팅방이 비어 있으면 "알림이 작동 안 하는지" vs "실제로 트리거가 없는지" 구분이 안 된다. NullNotifier 주입 옵션은 팩토리 실패 폴백 전용으로 보존.

7. **메시지 포맷 plain text 한국어** — MarkdownV2 미사용. 특수문자 escape 실패로 전송 자체가 거부되는 경로를 막는다. 이모지 사용 안 함(프로젝트 톤 유지).

8. **`Bot` 인스턴스 1회 조립 재사용** — 생성자에서 `bot_factory(token)` 을 1회 호출해 `_bot` 에 저장. 각 `notify_*` 호출은 `async with self._bot as bot:` 컨텍스트로 감싸 `send_message` 실행. 생성 비용(1회) + 컨텍스트 여닫기(매 호출) 를 분리.

## 결과

**긍정**
- Executor 의 Protocol 의존성 역전(ADR 명시 없음, `execution/CLAUDE.md` 의 "핵심 결정" 섹션에 기록) 이 보존되어 드라이런·단위 테스트 경로가 추가 분기 없이 유지됨.
- `StepReport` 확장이 backward compat (기본값 `()`) — 기존 `test_executor.py` 63건 중 회귀 0건.
- `monitor/notifier.py` 71건 + `test_main.py` +35건 + `test_executor.py` +22건 테스트 보강. 전체 780 green (기존 681 + 99).
- 의존성 추가 없음 — `python-telegram-bot>=21.0` 기존 사용.
- 연속 실패 경보가 "봇 토큰 만료·네트워크 단절" 같은 지속적 장애를 운영자에게 1회 알려줌 (logger.critical). 이후 복구 시 자동 재설정.

**부정**
- `_send` 가 매 호출마다 `asyncio.run(asyncio.wait_for(...))` 을 돌려서 오버헤드가 있다. 장중 진입·청산 빈도(~10회/일) 에서는 무시 가능하지만, 초당 알림 수백 건 시나리오에서는 병목이 될 수 있음 — 현재 범위 밖.
- 전송 실패 silent fail 이 운영자 진단 난이도를 높이는 부작용. 연속 실패 경보가 완화하지만 "일시적 timeout 1건" 같은 드문 누락은 로그 sink 확인 필요.
- notifier 는 동기 API 만 노출 — APScheduler 콜백이 동기 스레드에서 호출되므로 자연스럽지만, 향후 `AsyncIOScheduler` 전환 시 인터페이스 재평가 필요 (현재 ADR-0011 에 따라 `BlockingScheduler` 고정).

**중립**
- `[DRY-RUN]` 프리픽스 정책은 모의투자 ~실전 전환 시 채팅방에 "drill" 메시지가 남는 UX — 운영 절차서에 명시.
- `Executor.last_reconcile` 프로퍼티 추가는 읽기 전용 캐시로 반환 값 mutation 은 없음. 다음 `reconcile()` 호출에서 덮어씀.

## 추적

- 코드: `src/stock_agent/monitor/notifier.py`, `src/stock_agent/monitor/__init__.py`, `src/stock_agent/execution/executor.py` (EntryEvent/ExitEvent/StepReport 확장·last_reconcile 프로퍼티), `src/stock_agent/execution/__init__.py`, `src/stock_agent/main.py` (Runtime.notifier·`_default_notifier_factory`·콜백 4종 notifier 호출)
- 테스트: `tests/test_notifier.py` (71건), `tests/test_executor.py` 확장분, `tests/test_main.py` 확장분
- 관련 ADR: [ADR-0011](./0011-apscheduler-adoption-single-process.md) (콜백 예외 re-raise 금지 원칙), [ADR-0003](./0003-runtime-error-propagation.md) (RuntimeError 전파 기조)
- 문서: [monitor/CLAUDE.md](../../src/stock_agent/monitor/CLAUDE.md), [execution/CLAUDE.md](../../src/stock_agent/execution/CLAUDE.md), [architecture.md](../architecture.md), [plan.md](../../plan.md)
- 도입 PR: #18
