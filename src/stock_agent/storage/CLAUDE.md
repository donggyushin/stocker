# storage — SQLite 원장 (주문·체결·일일 PnL)

stock-agent 의 영속화 경계 모듈. `Executor` 가 방출한 `EntryEvent`·`ExitEvent` 와
`main._on_daily_report` 가 조립한 `DailySummary` 를 단일 SQLite 파일에 append-only
로 기록한다. `Executor` 는 이 모듈을 직접 의존하지 않는다 — `main.py` 콜백이
`StepReport.entry_events`·`exit_events` 를 순회하며 notifier 와 나란히 호출한다
(Protocol 의존성 역전, ADR-0012 notifier 와 동일 기조).

> 이 파일은 root [CLAUDE.md](../../../CLAUDE.md) 의 하위 문서이다. 프로젝트 전반
> 규약·리스크 고지·승인된 결정 사항은 root 문서를 따른다.

## 공개 심볼 (`storage/__init__.py`)

`TradingRecorder`, `SqliteTradingRecorder`, `NullTradingRecorder`, `StorageError`,
`OpenPositionRow`, `DailyPnlSnapshot`
(총 6종)

## 현재 상태

**Phase 3 네 번째 산출물 — `storage/db.py` (코드·테스트 레벨) 완료** (2026-04-22).
**Phase 3 다섯 번째 산출물 — 세션 재기동 복원 경로 (코드·테스트 레벨) 완료** (2026-04-22, Issue #33): `load_open_positions` · `load_daily_pnl` 2 메서드 + `OpenPositionRow` · `DailyPnlSnapshot` DTO 추가.
**2026-04-22 후속 — `load_*` 행 단위 예외 격리 (Issue #40) 완료**: 쿼리 자체 실패는 빈 결과 + 카운터 +1, 개별 행 파싱 실패는 행 단위 skip + `logger.error`, 파싱 실패 1건 이상이면 메서드 카운터 +1 경로로 묶음.

의도적으로 미포함(defer):
- 주간 회고 리포트 CLI (`scripts/weekly_report.py` 등) — MVP 는 SQL 직접 쿼리
- KIS 체결조회 API 통합 — 실체결가 정확도 향상은 별도 PR
- PostgreSQL 전환 — plan.md "추후" 영역
- 부분체결 기록 — `Executor` 즉시 전량 체결 가정이라 현재 모델링 불필요
- 스키마 마이그레이션 프레임워크 — v1 초기 릴리스이므로 `schema_version` 테이블 + 분기 훅만

## 핵심 결정 (ADR-0013)

1. **Protocol 의존성 역전** — `TradingRecorder` Protocol 경계로 `Executor` 와
   완전 분리. `main.py` 콜백만 이 타입을 알고 있다. notifier 와 동일 기조
   (ADR-0012).

2. **DB 파일 분리** — `data/trading.db` 는 `data/stock_agent.db`(historical
   일봉 캐시) 와 별개 파일. 생명주기 독립 + 스키마 버전 공간 독립. 캐시 DB 를
   날려도 원장 DB 가 보존되고, 원장 스키마 마이그레이션이 캐시 DB 에 영향을
   주지 않는다.

3. **스키마 v1** — 3 테이블(`orders`, `daily_pnl`, `schema_version`) + 2 인덱스
   (`idx_orders_session`, `idx_orders_symbol`). `order_number` TEXT PK 로
   `EntryEvent`·`ExitEvent` 를 1:1 매핑.

4. **가격 TEXT 저장** — `fill_price`·`ref_price` 는 `Decimal` 을 `str()` 변환 후
   저장. REAL 의 부동소수점 오차 없이 원본 값 보존.

5. **PRAGMA** — WAL(파일 전용)/NORMAL/foreign_keys ON. `isolation_level=None`
   autocommit + 스키마 init 한정 `BEGIN IMMEDIATE`. `:memory:` 에서는 WAL 미적용.

6. **silent fail** — `record_*` 내부의 `sqlite3.Error` 는 raise 안 함 (매매 루프
   보호). 연속 실패 카운터 + `logger.warning`. threshold 도달 시 `logger.critical`
   1회 dedupe 경보 (`monitor/notifier.py` `_record_failure` 패턴 재사용).

7. **NullTradingRecorder 폴백** — `SqliteTradingRecorder` 생성자 실패 시
   `_default_recorder_factory` 가 `NullTradingRecorder` 로 대체. 영속화 부재가
   세션 전체 실패보다 덜 위험하다는 판단. loguru sink 는 유지되므로 사후
   재구성 경로가 완전히 닫히지 않는다.

   **가시성 보강 (Issue #41, 2026-04-22)**: `_on_session_start` 가 매 세션 시작
   시 `isinstance(runtime.recorder, NullTradingRecorder)` 검사를 수행해, 폴백
   상태일 때 `logger.critical` + `runtime.notifier.notify_error(stage=
   "session_start.recorder_null", error_class="NullTradingRecorder",
   severity="critical")` 를 1회 방출한다. 이후 정상 세션 시작 경로
   (`get_balance`/`start_session` 또는 `restore_session`) 는 그대로 진행한다.
   `_default_recorder_factory` 의 `logger.warning` 만으로는 재기동 복원 경로
   (ADR-0014) 가 Null 폴백에서 신규 세션 분기로 조용히 빠져버리는 silent-failure
   경로를 충분히 알릴 수 없어 경보 수준을 올렸다.

8. **order_number PK + DTO 확장** — `EntryEvent`·`ExitEvent` 에 `order_number: str`
   필드 추가. `__post_init__` 가드: 빈 문자열·naive timestamp·qty≤0·price≤0 →
   `RuntimeError` (ADR-0003 기조).

9. **드라이런 구분 없음** — `--dry-run` 모드에서도 `SqliteTradingRecorder` 를
   주입해 실기록. `DryRunOrderSubmitter` 가 주문을 차단하므로 실 체결 이벤트는
   발생하지 않아 DB 가 오염되지 않는다.

10. **close 멱등** — `close()` 는 재호출 안전. `_graceful_shutdown` + `finally`
    블록 양쪽에서 호출해도 두 번째 호출은 no-op.

### ADR-0014 확장 — 세션 재기동 복원 (Issue #33)

`TradingRecorder` Protocol 에 읽기 전용 메서드 2종을 추가해 세션 중간 재기동 시
DB 에서 상태를 복원하는 경로를 제공한다 (ADR-0014).

**신규 DTO**:

```python
@dataclass(frozen=True, slots=True)
class OpenPositionRow:
    symbol: str
    qty: int
    entry_price: Decimal
    entry_ts: datetime      # KST aware
    order_number: str

@dataclass(frozen=True, slots=True)
class DailyPnlSnapshot:
    realized: int           # KRW, 당일 실현 손익 누계
    entries: int            # 당일 진입 횟수

    @property
    def has_state(self) -> bool:
        """realized != 0 또는 entries > 0 이면 이미 세션이 진행된 것."""
        ...
```

**신규 Protocol 메서드**:

```python
@dataclass(frozen=True, slots=True)
class OpenPositionRow:
    symbol: str              # 6자리 숫자
    qty: int                 # > 0
    entry_price: Decimal     # > 0
    entry_ts: datetime       # KST aware
    order_number: str        # 비어있지 않음

@dataclass(frozen=True, slots=True)
class DailyPnlSnapshot:
    session_date: date
    realized_pnl_krw: int
    entries_today: int                    # >= 0
    closed_symbols: tuple[str, ...]       # 정렬 tuple

    @property
    def has_state(self) -> bool: ...      # 재기동 감지용

def load_open_positions(self, session_date: date) -> tuple[OpenPositionRow, ...]:
    """orders 테이블을 filled_at ASC, rowid ASC 순으로 재생해
    buy/sell 페어를 상쇄한 결과(미청산 포지션)를 반환한다."""

def load_daily_pnl(self, session_date: date) -> DailyPnlSnapshot:
    """orders 에서 session_date 의 buy/sell 을 집계해 DailyPnlSnapshot 반환.
    buy 개수 → entries_today, sell.net_pnl_krw 합 → realized_pnl_krw,
    sell.symbol 정렬 집합 → closed_symbols."""
```

**`has_state` 공식** — `entries_today > 0 or bool(closed_symbols) or realized_pnl_krw != 0` (3항 OR). `_on_session_start` 가 이 값으로 "신규 세션 / 재기동 복원" 을 분기.

**실패 정책 (silent fail 확장)**: `load_*` 의 실패는 두 계층으로 구분된다. ① 쿼리 자체 실패(`sqlite3.Error`·`ValueError`·`TypeError`) → `logger.warning` + 연속 실패 카운터 +1, 빈 결과 반환. ② 개별 행 파싱 실패(`Decimal` 변환·`datetime.fromisoformat`·`OpenPositionRow.__post_init__` RuntimeError 등) → 해당 행 `logger.error` + skip, 나머지 행은 정상 반환. 파싱 실패 1건 이상이면 카운터 +1 경보 경로를 탄다 (Issue #40). `_TRACKED_OPS` 에 `load_open_positions` · `load_daily_pnl` 가 추가되어 메서드별 독립 실패 카운터를 유지한다. 읽기 실패 시 빈 결과(`tuple()` / `DailyPnlSnapshot(session_date, 0, 0, ())`)를 반환해 호출자가 신규 세션으로 폴백할 수 있도록 한다.

**NullTradingRecorder 대칭**: `load_open_positions` → `()`, `load_daily_pnl` → `DailyPnlSnapshot(session_date=입력, realized_pnl_krw=0, entries_today=0, closed_symbols=())` 반환 (no-op).

## 공개 API

### `TradingRecorder` Protocol

```python
@runtime_checkable
class TradingRecorder(Protocol):
    def record_entry(self, event: EntryEvent) -> None: ...
    def record_exit(self, event: ExitEvent) -> None: ...
    def record_daily_summary(self, summary: DailySummary) -> None: ...
    def close(self) -> None: ...
    def load_open_positions(self, session_date: date) -> tuple[OpenPositionRow, ...]: ...
    def load_daily_pnl(self, session_date: date) -> DailyPnlSnapshot: ...
```

`isinstance(obj, TradingRecorder)` 런타임 체크 가능.

### `SqliteTradingRecorder`

```python
SqliteTradingRecorder(
    *,
    db_path: str | Path = "data/trading.db",   # ":memory:" 허용 (테스트용)
    consecutive_failure_threshold: int = 5,    # 1 이상, 위반 시 RuntimeError
)
```

컨텍스트 매니저(`__enter__`/`__exit__`) 지원. `close()` 멱등.

- `record_entry(event: EntryEvent) -> None`
- `record_exit(event: ExitEvent) -> None`
- `record_daily_summary(summary: DailySummary) -> None`
- `close() -> None`

**Raises (생성자)**:
- `StorageError` — 스키마 init 실패·디렉토리 경로 오지정·connect 실패. `__cause__` 보존.
- `RuntimeError` — `consecutive_failure_threshold < 1`.

**`record_*` 실패 정책**: `sqlite3.Error` 는 raise 안 함 — warning 로그 + 연속
실패 카운터. threshold 도달 시 `logger.critical` 1회.

### `NullTradingRecorder`

```python
NullTradingRecorder()   # 인자 없음
```

모든 메서드가 no-op. `close()` 도 no-op.

### `StorageError`

```python
class StorageError(Exception): ...
```

생성자 실패·치명 초기화 실패 래퍼. 원본 예외는 `__cause__` 에 보존.

## 스키마 v1

```sql
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS orders (
    order_number TEXT PRIMARY KEY,
    session_date TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    side         TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    qty          INTEGER NOT NULL CHECK (qty > 0),
    fill_price   TEXT NOT NULL,
    ref_price    TEXT NOT NULL,
    exit_reason  TEXT CHECK (
        exit_reason IN ('stop_loss', 'take_profit', 'force_close')
        OR exit_reason IS NULL
    ),
    net_pnl_krw  INTEGER,
    filled_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_session ON orders(session_date);
CREATE INDEX IF NOT EXISTS idx_orders_symbol  ON orders(symbol);

CREATE TABLE IF NOT EXISTS daily_pnl (
    session_date         TEXT PRIMARY KEY,
    starting_capital_krw INTEGER,
    realized_pnl_krw     INTEGER NOT NULL,
    realized_pnl_pct     REAL,
    entries_today        INTEGER NOT NULL,
    halted               INTEGER NOT NULL CHECK (halted IN (0, 1)),
    mismatch_symbols     TEXT NOT NULL,   -- JSON 배열 (json.dumps)
    recorded_at          TEXT NOT NULL
);
```

- `orders.side = 'buy'` ↔ `EntryEvent`, `side = 'sell'` ↔ `ExitEvent`.
- `orders.exit_reason` / `net_pnl_krw` 는 buy 행에서 NULL.
- `orders.ref_price` 는 sell 행에서 `fill_price` 와 동일 값 — `ExitEvent` 에 참고가
  필드가 없어 복사. v2 에서 컬럼 추가 예정.
- `daily_pnl.mismatch_symbols` 는 `json.dumps(list[str])` 직렬화.
- `daily_pnl.halted` 는 0(False) / 1(True).
- `daily_pnl.recorded_at` 은 호출 시점 KST aware ISO 문자열 — 동일 세션 재실행 이력 구분용.

## 실패 정책 상세

`consecutive_failure_threshold` (기본 5, 생성자 인자로 변경 가능):

1. `record_*` 에서 `sqlite3.Error` 발생 → `logger.warning` + 카운터 +1.
2. 카운터 ≥ threshold 이고 `_critical_emitted = False` → `logger.critical` 1회 방출 + `_critical_emitted = True`.
3. 성공 1회 → 카운터 0 리셋 + `_critical_emitted = False`.
4. `close()` 이후 `record_*` 호출 → `logger.warning` 1회 + silent 반환. 카운터 불변.
5. naive timestamp 입력 → `logger.warning` + 카운터 +1 + silent 반환 (DTO `__post_init__` 가 이미 차단하지만 defensive depth).
6. **`load_*` 행 단위 격리 (Issue #40)** — 쿼리 자체 실패(`sqlite3.Error`·`ValueError`·`TypeError`) → 빈 결과 + `_on_failure` + 카운터 +1. 개별 행 파싱 실패(`Decimal` 변환·`datetime.fromisoformat`·`OpenPositionRow.__post_init__` RuntimeError 등) → 해당 행만 `logger.error` 로 skip, 나머지 행 정상 반환. 파싱 실패 1건 이상이면 `_on_failure` 를 1회 호출해 메서드별 카운터 +1 + dedupe 경보 경로를 탄다. 모든 행이 실패해도 크래시 없이 빈 결과(`()` / `DailyPnlSnapshot(session_date, 0, 0, ())`). `load_daily_pnl` 내부에서 `sold.add(symbol)` 은 `int(net_pnl)` 성공 이후에 실행해, 파싱 실패 시 해당 심볼이 `closed_symbols` 에 잘못 포함되지 않도록 한다.

## 테스트 정책

- 실제 파일 I/O: `":memory:"` (단위 테스트) 또는 `tmp_path` fixture (경로 테스트). 외부 의존 0.
- KIS 네트워크·텔레그램·시계·실파일 절대 접촉 금지.
- 관련 테스트 파일: `tests/test_storage_db.py`. 카테고리 — 공개 심볼 노출, `SqliteTradingRecorder` 생성·스키마·PRAGMA, `record_entry`/`record_exit`/`record_daily_summary` 정상·가드·DB 행 검증, silent fail + 연속 실패 dedupe, close 후 호출 내구성, `NullTradingRecorder` no-op, `StorageError` 계약, 컨텍스트 매니저, close 멱등. Issue #40 대응으로 `TestLoadOpenPositionsRowIsolation` · `TestLoadDailyPnlRowIsolation` 클래스 추가 (행 단위 파싱 실패 격리 검증).
- SKIP 케이스 3건: naive timestamp 는 DTO `__post_init__` 가드가 선점하므로 `record_*` 진입 전 `RuntimeError` — SKIP 처리. 자세한 사유는 테스트 파일 내 주석 참조.
- 테스트 파일 작성·수정은 반드시 `unit-test-writer` 서브에이전트 경유 (root CLAUDE.md 하드 규칙).

## 소비자 참고

- **`main.py`** 콜백 4종 — **전 경로 `record_*` → `notify_*` 순서로 통일**
  (리뷰 I1, 2026-04-22). 이유: notifier 가 (silent fail 계약에도 불구하고
  장래 확장·외부 예외로) 전파하면 같은 이벤트의 DB 기록이 누락될 수 있어,
  `_on_daily_report` 의 기존 record 선행 기조를 콜백 전체로 확장.
  - `_on_step`: `record_entry` → `notify_entry`, `record_exit` → `notify_exit`.
  - `_on_force_close`: `record_exit` → `notify_exit`. 예외 분기에서는
    `runtime.executor.last_sweep_exit_events` 스냅샷을 읽어 이미 체결된
    부분 청산도 기록(리뷰 I3).
  - `_on_daily_report`: `record_daily_summary(summary)` → `notify_daily_summary(summary)`.
  - `_on_session_start`: recorder 호출 없음.
- **`Executor`** 는 recorder 를 모른다 — `main.py` 콜백이 유일한 호출 경로.
  `last_sweep_entry_events` / `last_sweep_exit_events` 프로퍼티는 sweep 중
  예외 발생 시 부분 결과를 외부에서 읽기 위한 스냅샷 경로이며 recorder 와는
  단방향(main.py 콜백 → recorder).
- `_graceful_shutdown` 과 `main()` `finally` 양쪽에서 `runtime.recorder.close()` 멱등 호출.

## 범위 제외 (의도적 defer)

- **주간 회고 리포트 CLI** — MVP 는 `sqlite3` CLI 직접 쿼리.
- **KIS 체결조회 API 통합** — 실체결가 정확도 향상. broker 확장 별도 PR.
- **PostgreSQL 전환** — plan.md "추후" 영역.
- **부분체결 기록** — `Executor` 즉시 전량 체결 가정.
- **스키마 마이그레이션 프레임워크** — `schema_version` 테이블 + 분기 훅만 준비. 실제 마이그레이션 로직은 v2 도입 시.
- **멀티프로세스·스레드 safe** — 단일 프로세스 전용 (broker/strategy/risk/data/execution 와 동일).

## ADR

- [ADR-0013](../../../docs/adr/0013-sqlite-trading-ledger.md) — storage/db.py 모듈 설계 (DB 분리·Protocol 분리·스키마 v1·가격 TEXT·PRAGMA·silent fail·NullTradingRecorder 폴백·order_number PK·드라이런 구분 없음·close 멱등)
- [ADR-0014](../../../docs/adr/0014-runtime-state-recovery.md) — 세션 재기동 시 DB 기반 상태 복원 경로 설계 (load_open_positions·load_daily_pnl·OpenPositionInput Protocol·restore_session 흐름)
