# monitor — 텔레그램 알림 라우팅

stock-agent 의 알림 경계 모듈. `Notifier` Protocol 을 통해 `main.py` 콜백이
진입·청산·에러·일일 요약 4종 텔레그램 알림을 라우팅한다. `Executor` 는
notifier 의 존재 자체를 모르며, `main.py` 가 `StepReport.entry_events` /
`exit_events` 를 소비해 `runtime.notifier.notify_*` 를 호출하는 구조다.

> 이 파일은 root [CLAUDE.md](../../../CLAUDE.md) 의 하위 문서이다. 프로젝트 전반
> 규약·리스크 고지·승인된 결정 사항은 root 문서를 따른다.

## 공개 심볼 (`monitor/__init__.py`)

`Notifier`, `TelegramNotifier`, `NullNotifier`, `ErrorEvent`, `DailySummary`

## 현재 상태 (2026-04-21 기준)

**Phase 3 세 번째 산출물 — `monitor/notifier.py` (코드·테스트 레벨) 완료**
(2026-04-21).

- pytest 71건 신규 (`tests/test_notifier.py`) + executor 회귀 22건 +
  main notifier 통합 34건 = 전체 780건 green.
- 의존성 추가 없음 (`python-telegram-bot>=21.0` 기존 사용).
- `StepReport` 에 `entry_events`·`exit_events` 필드 추가 —
  `execution/CLAUDE.md` 참조.
- `Executor.last_reconcile` 프로퍼티 추가 — `execution/CLAUDE.md` 참조.

Phase 3 PASS 선언은 모의투자 환경에서 **연속 10영업일 무중단 + 0 unhandled
error + 모든 주문이 SQLite 기록 + 텔레그램 알림 100% 수신** 후. 본 산출물은
알림 경로의 코드·테스트 레벨 계약만 잠근다.

### 의도적으로 미포함 (후속 PR)

| 영역 | 위치 | 비고 |
|---|---|---|
| 로그 설정 일원화 | `main.py._configure_logging` | `monitor/logger.py` 는 현 범위 밖 — 로그 파일 설정은 `main.py` 가 책임 |
| 포지션 추적 | 없음 | `RiskManager.active_positions` 직접 소비로 커버 |
| SQLite 체결 영속화 | `storage/db.py` | Phase 3 네 번째 산출물 (미착수) |

## 핵심 결정 (ADR-0012 요약)

전체 결정 근거: [docs/adr/0012-monitor-notifier-design.md](../../../docs/adr/0012-monitor-notifier-design.md)

1. **Protocol 의존성 역전 유지** — `Executor` 는 notifier 를 모른다. `main.py`
   가 `StepReport` 이벤트를 소비해 `notifier.notify_*` 호출.
2. **`StepReport` 확장** — `entry_events: tuple[EntryEvent, ...] = ()` +
   `exit_events: tuple[ExitEvent, ...] = ()`. 기본값 `()` 로 backward compat.
3. **전송 실패 silent fail** — `TelegramError` / `TimeoutError` / `Exception`
   재전파 금지. `_consecutive_failures` 카운터가 `consecutive_failure_threshold`
   (기본 5) 도달 시 `logger.critical` 1회 dedupe. 성공 시 카운터·dedupe 플래그 리셋.
4. **드라이런도 실전송 + `[DRY-RUN]` 프리픽스** — `TelegramNotifier(dry_run=True)`
   는 제목 맨 앞에 `[DRY-RUN] ` 를 붙여 실전송. 알림 경로 end-to-end 검증 목적.
   `NullNotifier` 는 팩토리 실패 폴백 전용.
5. **plain text 한국어 포맷** — MarkdownV2 미사용. 특수문자 escape 실패로
   전송이 거부되는 경로를 막는다. 이모지 사용 없음.
6. **`Bot` 인스턴스 1회 조립 재사용** — 생성자에서 `bot_factory(token)` 1회 호출.
   각 전송에서 `async with self._bot as bot:` 컨텍스트를 여닫아 리소스 누수 방지.

## 공개 API

### `Notifier` Protocol

```python
@runtime_checkable
class Notifier(Protocol):
    def notify_entry(self, event: EntryEvent) -> None: ...
    def notify_exit(self, event: ExitEvent) -> None: ...
    def notify_error(self, event: ErrorEvent) -> None: ...
    def notify_daily_summary(self, summary: DailySummary) -> None: ...
```

`@runtime_checkable` — `isinstance(obj, Notifier)` 런타임 체크 가능.
`NullNotifier` / `TelegramNotifier` / `MagicMock(spec=Notifier)` 모두 자연 만족.

### `TelegramNotifier` 생성자

```python
TelegramNotifier(
    *,
    bot_token: SecretStr,
    chat_id: int,
    dry_run: bool = False,
    timeout_s: float = 5.0,
    consecutive_failure_threshold: int = 5,
    clock: Callable[[], datetime] | None = None,   # KST aware datetime 반환
    bot_factory: Callable[[str], Bot] | None = None,
)
```

`__post_init__` 검증: `timeout_s <= 0` 또는 `consecutive_failure_threshold <= 0`
→ `RuntimeError`.

### `NullNotifier`

```python
class NullNotifier:
    """no-op 구현 — 팩토리 실패 폴백·알림 비활성 모드."""
    def notify_entry(self, event: EntryEvent) -> None: ...
    def notify_exit(self, event: ExitEvent) -> None: ...
    def notify_error(self, event: ErrorEvent) -> None: ...
    def notify_daily_summary(self, summary: DailySummary) -> None: ...
```

모든 메서드는 `return None`. 예외를 던지지 않는다.

### `ErrorEvent` / `DailySummary` 필드

| DTO | 필드 | 타입 | 설명 |
|---|---|---|---|
| `ErrorEvent` | `stage` | `str` | 에러 발생 단계 (예: `"step"`, `"force_close"`, `"reconcile"`) |
| | `error_class` | `str` | 예외 클래스명 |
| | `message` | `str` | 예외 메시지 |
| | `timestamp` | `datetime` | KST aware datetime |
| | `severity` | `Literal["error", "critical"]` | `"critical"`: 포지션 잔존·halt 위험. `"error"`: 복구 가능성 있는 오류 |
| `DailySummary` | `session_date` | `date` | 세션 날짜 |
| | `starting_capital_krw` | `int \| None` | 세션 시작 자본 (`None` 면 `"n/a"` 출력) |
| | `realized_pnl_krw` | `int` | 당일 실현 손익 (원) |
| | `realized_pnl_pct` | `float \| None` | 손익률 (%) — `None` 이면 `"n/a"` |
| | `entries_today` | `int` | 당일 진입 횟수 |
| | `halted` | `bool` | 서킷브레이커 or Executor halt 여부 |
| | `mismatch_symbols` | `tuple[str, ...]` | reconcile 불일치 종목 목록 |

모두 `@dataclass(frozen=True, slots=True)`.

## 메시지 포맷 규칙

- **plain text 한국어** — MarkdownV2 / HTML 파싱 모드 미사용.
- **이모지 미사용** — 프로젝트 톤 유지.
- **`[DRY-RUN]` 프리픽스** — `dry_run=True` 이면 모든 제목 맨 앞에 삽입.
- **severity 대문자** — `notify_error` 제목: `[stock-agent] ERROR step` (소문자 severity → upper()).
- **메시지 구조**: 제목 줄 + 본문 `\n` 구분. `send_message(chat_id, text)` 단일 호출.

예시:

```text
[stock-agent] 진입 체결
종목=005930 수량=3주 체결가=83500 참고가=83200 시각=09:32:00

[stock-agent] 일일 요약 2026-04-21
실현 PnL=45000원 (1.50%)
진입 횟수=2
서킷브레이커=no
Executor halt=no
Reconcile mismatch=없음
```

## 전송 실패 정책

`TelegramNotifier._send` 동작:

```text
try:
    asyncio.run(wait_for(_inner(), timeout_s))
except TelegramError:    → logger.exception + _record_failure()
except TimeoutError:     → logger.exception + _record_failure()
except Exception:        → logger.exception + _record_failure()
else:
    consecutive_failures = 0
    persistent_alert_emitted = False
```

`_record_failure`:
- `_consecutive_failures += 1`
- `_consecutive_failures >= _threshold` AND `not _persistent_alert_emitted`
  → `logger.critical` (dedupe 1회) + `_persistent_alert_emitted = True`
- 전송 성공 시 카운터·플래그 모두 리셋.

세션 연속성이 알림 전송보다 중요하다 — 전송 실패가 `_on_step`·`_on_force_close`
콜백을 죽이지 않게 한다 (ADR-0011 결정 5 연장).

## 예외 경계 설계

| 예외 | 발생 조건 | 처리 |
|---|---|---|
| `RuntimeError` | `timeout_s <= 0` / `consecutive_failure_threshold <= 0` | 생성자에서 즉시 raise — 프로그래밍 오류 |
| `TelegramError` | Telegram API 오류 (400 Bad Request, 401 등) | `_send` 내에서 silent fail + 카운터 |
| `TimeoutError` | `asyncio.wait_for` 만료 | `_send` 내에서 silent fail + 카운터 |
| 기타 `Exception` | SSL, asyncio, 네트워크 등 | `_send` 내에서 silent fail + 카운터 |

`NullNotifier` 는 예외를 던지지 않는다 (무조건 반환).

## 테스트 정책

- **실제 `telegram.Bot` / 네트워크 접촉 절대 0** — `bot_factory` 주입으로 완전 격리.
- `Bot` 인스턴스는 `MagicMock(spec=telegram.Bot)` + `AsyncMock` 반환으로 대체.
- `clock` 주입으로 `timestamp` 값 결정론 확보.
- `Notifier` Protocol 만족 검증: `isinstance(NullNotifier(), Notifier)` +
  `isinstance(TelegramNotifier(...), Notifier)` assert.
- 연속 실패 카운터·dedupe 플래그 상태 전이 시나리오 커버.
- `dry_run=True` 프리픽스 주입 검증 (메시지 text 에 `[DRY-RUN]` 포함 assert).
- 테스트 파일 작성·수정은 반드시 `unit-test-writer` 서브에이전트 경유 (root
  CLAUDE.md 하드 규칙, `.claude/hooks/tests-writer-guard.sh` fail-closed).
- 관련 테스트 파일: `tests/test_notifier.py` (71건). 절대 케이스 수는 의도적으로
  적지 않는다 — root CLAUDE.md "현재 상태" 의 총합 카운트만 정본으로 유지.

## 소비자 참고 (`main.py` 콜백 통합)

`src/stock_agent/main.py` 의 콜백 4종이 `runtime.notifier` 를 통해 호출한다.

| 콜백 | notifier 호출 | 조건 |
|---|---|---|
| `_on_session_start` | `notify_error(stage="session_start", severity="error")` | 자본 ≤ 0 또는 예외 발생 시 |
| `_on_step` | `notify_entry(event)` / `notify_exit(event)` | `StepReport.entry_events` / `exit_events` 순회 |
| `_on_step` | `notify_error(stage="reconcile", severity="critical")` | mismatch 발견 시 1회 dedupe |
| `_on_step` | `notify_error(stage="step", severity="error")` | except 블록 |
| `_on_force_close` | `notify_exit(event)` | `StepReport.exit_events` 순회 |
| `_on_force_close` | `notify_error(stage="force_close", severity="critical")` | except 블록 (logger.critical 과 이중 경보) |
| `_on_daily_report` | `notify_daily_summary(DailySummary(...))` | 정상 경로 1회 |
| `_on_daily_report` | `notify_error(stage="daily_report", severity="error")` | except 블록 |

`build_runtime` 에 `notifier_factory: Callable[[Settings, bool], Notifier] | None`
파라미터가 있다 — `None` 이면 `_default_notifier_factory` 사용
(`TelegramNotifier` 또는 `NullNotifier` 폴백).

`EntryEvent` / `ExitEvent` DTO 는 `execution/` 패키지에 정의된다 —
[src/stock_agent/execution/CLAUDE.md](../execution/CLAUDE.md) 참조.

## 범위 제외 (의도적 defer)

- **MarkdownV2 / HTML 포맷** — escape 실패로 전송 거부 경로 추가되는 위험.
- **이모지** — 프로젝트 톤 유지.
- **`Application` 장기 실행 모드** — `BlockingScheduler` 와 asyncio event loop
  충돌 가능성. 현재 `asyncio.run` 1회 실행 패턴 유지 (ADR-0011).
- **알림 throttling** — 동일 종목 연속 알림 억제. 현재 범위 밖.
- **수신자 그룹별 라우팅** — 단일 `chat_id` 고정. 향후 필요 시 Protocol 확장.
- **`monitor/logger.py`** — loguru 설정 일원화. 현 범위에서는 `main.py._configure_logging` 이 책임.
