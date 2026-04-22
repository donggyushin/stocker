"""SQLite 원장 — EntryEvent / ExitEvent / DailySummary 영속화 + 재기동 상태 복원.

책임 범위
- Executor 가 방출한 `EntryEvent`·`ExitEvent` 를 `orders` 테이블에 1행 1이벤트로
  append.
- `main._on_daily_report` 가 조립한 `DailySummary` 를 `daily_pnl` 테이블에
  session_date PK `INSERT OR REPLACE` 로 기록.
- **재기동 시 당일 상태 복원 (Issue #33, ADR-0014)** — `load_open_positions`·
  `load_daily_pnl` 로 `orders` 를 재생해 오픈 포지션·실현 PnL·진입 횟수·청산
  완료 심볼을 복원. `main._on_session_start` 가 감지 후 `Executor.restore_session`
  을 호출한다.

범위 제외 (의도적 defer)
- 주간 회고 리포트 CLI (`scripts/weekly_report.py` 등) — MVP 는 SQL 직접 쿼리.
- KIS 체결조회 API 통합 — 실체결가 정확도 향상은 별도 PR. 현재는
  `backtest/costs.py` 산식으로 추정한 체결가를 기록.
- PostgreSQL 전환 — plan.md:71 "추후" 영역.
- 부분체결 기록 — Executor 가 즉시 전량 체결 가정이라 모델링 불필요.
- 스키마 마이그레이션 프레임워크 — v1 초기 릴리스이므로 `schema_version`
  테이블 + 분기 훅만.

공개 API
- `TradingRecorder` (Protocol, @runtime_checkable) — notifier 와 동일 기조의
  의존성 역전 경계. `Executor` 는 이 타입을 **모른다** — `main.py` 콜백이
  `StepReport.entry_events`·`exit_events` 를 순회하며 notifier 와 나란히 호출.
- `SqliteTradingRecorder` — 단일 파일(기본 `data/trading.db` — 상대경로 기본값,
  `main.py` 는 프로젝트 루트 기반 절대경로를 주입해 CWD 의존성을 제거), WAL
  저널, autocommit + `BEGIN IMMEDIATE` 는 스키마 init 한정.
- `NullTradingRecorder` — 생성자 실패 폴백 (notifier 의 `NullNotifier` 와 동일
  기조 — 부분 기능 손실 > 세션 전체 실패).
- `StorageError` — 스키마 init 실패·치명적 초기화 실패 래퍼 (`__cause__` 보존).
- `OpenPositionRow` — `load_open_positions` 반환 DTO. 재기동 시 Executor 가
  `_open_lots` / `RiskManager.active_positions` / `ORBStrategy` long 상태 복원
  입력으로 소비.
- `DailyPnlSnapshot` — `load_daily_pnl` 반환 DTO. `realized_pnl_krw`,
  `entries_today`, `closed_symbols` 를 묶어 Executor 복원 + ORB 재진입 차단
  입력으로 소비.

실패 정책 (notifier.py `_record_failure` 패턴 재사용)
- `record_*` 메서드 내부의 `sqlite3.Error` **및 기타 `Exception`** 은 raise
  하지 않고 silent (매매 루프 보호 — 계약의 명분을 DB 외부 예외까지 포괄).
  단 `BaseException` (KeyboardInterrupt 등) 은 전파. 메서드별 **독립** 실패
  카운터(`_consecutive_failures[op]`) 를 증가시키고 매회 `logger.warning`.
  카운터가 `consecutive_failure_threshold` (기본 5) 에 도달하면 해당 메서드의
  `_critical_emitted[op]` 가 False 일 때만 `logger.critical` 1회 방출(dedupe).
  메서드별 성공 1회 시 그 메서드의 카운터·플래그만 리셋 → 다음 연속 실패가
  다시 threshold 에 도달하면 critical 재방출. 공유 카운터는 빈도가 낮은
  `record_daily_summary` 실패가 빈도가 높은 `record_entry` 성공에 묻혀 경보를
  영영 못 띄우는 경로가 있어 분리한다(2026-04-22 리뷰 C4).
- 생성자 내부의 스키마 init 실패는 `StorageError` 로 raise — 폴백
  (`NullTradingRecorder`) 선택은 호출자(main.py `_default_recorder_factory`)
  책임.
- `close()` 이후 `record_*` 호출은 warning 1회 + silent, 카운터 불변 (세션
  종료 내구성).

스키마 v1 (3 테이블 + 2 인덱스 + schema_version)
- `orders`: 모든 매수·매도 체결을 `order_number` PK 로 append.
- `daily_pnl`: `session_date` PK, 재실행 시 `INSERT OR REPLACE`.
- `schema_version`: 현재 버전 v1. 향후 마이그레이션 분기 진입점.

PRAGMA (파일 기반만)
- `journal_mode = WAL` (동시 읽기 + append 쓰기 성능)
- `synchronous = NORMAL`
- `foreign_keys = ON`

스레드 모델
- 단일 프로세스·단일 caller 전용 (broker/strategy/risk/data/execution 와
  동일). 동시 호출 금지.
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, DecimalException
from pathlib import Path
from types import TracebackType
from typing import Protocol, runtime_checkable

from loguru import logger

from stock_agent.execution import EntryEvent, ExitEvent
from stock_agent.monitor import DailySummary

KST = timezone(timedelta(hours=9))

_DEFAULT_DB_PATH = Path("data/trading.db")
_SCHEMA_VERSION = 1
_DEFAULT_FAILURE_THRESHOLD = 5

_SYMBOL_RE = re.compile(r"^\d{6}$")

_OP_RECORD_ENTRY = "record_entry"
_OP_RECORD_EXIT = "record_exit"
_OP_RECORD_DAILY_SUMMARY = "record_daily_summary"
_OP_LOAD_OPEN_POSITIONS = "load_open_positions"
_OP_LOAD_DAILY_PNL = "load_daily_pnl"
_TRACKED_OPS: tuple[str, ...] = (
    _OP_RECORD_ENTRY,
    _OP_RECORD_EXIT,
    _OP_RECORD_DAILY_SUMMARY,
    _OP_LOAD_OPEN_POSITIONS,
    _OP_LOAD_DAILY_PNL,
)


_CREATE_SCHEMA_VERSION_SQL = """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY
    )
"""

_CREATE_ORDERS_SQL = """
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
    )
"""

_CREATE_ORDERS_INDEX_SESSION = (
    "CREATE INDEX IF NOT EXISTS idx_orders_session ON orders(session_date)"
)
_CREATE_ORDERS_INDEX_SYMBOL = "CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol)"

_CREATE_DAILY_PNL_SQL = """
    CREATE TABLE IF NOT EXISTS daily_pnl (
        session_date         TEXT PRIMARY KEY,
        starting_capital_krw INTEGER,
        realized_pnl_krw     INTEGER NOT NULL,
        realized_pnl_pct     REAL,
        entries_today        INTEGER NOT NULL,
        halted               INTEGER NOT NULL CHECK (halted IN (0, 1)),
        mismatch_symbols     TEXT NOT NULL,
        recorded_at          TEXT NOT NULL
    )
"""


class StorageError(Exception):
    """스토리지 초기화·치명 실패 래퍼. 원본은 `__cause__` 에 보존."""


@dataclass(frozen=True, slots=True)
class OpenPositionRow:
    """재기동 시 `load_open_positions` 가 반환하는 오픈 포지션 DTO.

    `orders` 테이블을 `filled_at` 순으로 재생한 뒤 아직 청산되지 않은
    포지션을 나타낸다. `Executor.restore_session` 이 이 DTO 를 받아
    `_open_lots` / `RiskManager.active_positions` / `ORBStrategy` long 상태
    복원에 사용한다 (ADR-0014).

    Attributes:
        symbol: 6자리 종목 코드.
        qty: 보유 수량. 양수.
        entry_price: 체결 단가 (`EntryEvent.fill_price` 그대로 — 슬리피지 반영).
        entry_ts: 체결 시각. KST aware datetime.
        order_number: 브로커 주문번호 (감사 추적용).
    """

    symbol: str
    qty: int
    entry_price: Decimal
    entry_ts: datetime
    order_number: str

    def __post_init__(self) -> None:
        if not _SYMBOL_RE.fullmatch(self.symbol):
            raise RuntimeError(
                f"OpenPositionRow.symbol 은 6자리 숫자여야 합니다 (got={self.symbol!r})."
            )
        if self.qty <= 0:
            raise RuntimeError(f"OpenPositionRow.qty 는 양수여야 합니다 (got={self.qty}).")
        if self.entry_price <= 0:
            raise RuntimeError(
                f"OpenPositionRow.entry_price 는 양수여야 합니다 (got={self.entry_price})."
            )
        if self.entry_ts.tzinfo is None:
            raise RuntimeError("OpenPositionRow.entry_ts 는 tz-aware datetime 이어야 합니다.")
        if not self.order_number:
            raise RuntimeError("OpenPositionRow.order_number 는 비어있을 수 없습니다.")


@dataclass(frozen=True, slots=True)
class DailyPnlSnapshot:
    """재기동 시 `load_daily_pnl` 이 반환하는 당일 집계 DTO.

    `orders` 테이블의 당일 buy/sell 행을 기반으로 RiskManager 가 **카운터·
    실현 PnL·재진입 차단** 을 동일하게 유지할 수 있도록 집계한다.

    Attributes:
        session_date: 집계 기준 세션 날짜.
        realized_pnl_krw: 당일 sell 행의 `net_pnl_krw` 합계 (수익 양수, 손실 음수).
        entries_today: 당일 buy 행 개수 (성공 진입 = 1건 buy = 1건 기록).
        closed_symbols: 당일 sell 이 기록된 심볼 집합 (정렬 tuple). ORB
            `mark_session_closed` 로 재진입 차단 용도.
    """

    session_date: date
    realized_pnl_krw: int
    entries_today: int
    closed_symbols: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.entries_today < 0:
            raise RuntimeError(
                f"DailyPnlSnapshot.entries_today 는 0 이상이어야 합니다 (got={self.entries_today})."
            )

    @property
    def has_state(self) -> bool:
        """당일 상태 존재 여부 — 어느 것이든 0 이 아니면 재기동 복원 분기.

        `entries_today > 0` 이면 이미 매수 이력이 있다는 뜻. `closed_symbols`
        나 `realized_pnl_krw != 0` 은 `entries_today > 0` 일 때만 가능하므로
        전자만 검사해도 충분하지만, 방어적으로 셋 다 확인한다.
        """
        return self.entries_today > 0 or bool(self.closed_symbols) or self.realized_pnl_krw != 0


@runtime_checkable
class TradingRecorder(Protocol):
    """거래 이벤트 영속화 경계 (Protocol).

    `main.py` 콜백이 `StepReport.entry_events`·`exit_events` 와 `DailySummary`
    를 notifier 와 나란히 이 인터페이스로 포워딩한다. Executor 는 이 타입을
    직접 의존하지 않는다 (Protocol 의존성 역전, notifier 와 동일 기조).

    Issue #33 (ADR-0014) — `load_open_positions`·`load_daily_pnl` 은
    `_on_session_start` 재기동 복원 경로가 사용. `NullTradingRecorder` 는
    빈 결과를 반환(기존 `record_*` no-op 기조와 동일).
    """

    def record_entry(self, event: EntryEvent) -> None: ...

    def record_exit(self, event: ExitEvent) -> None: ...

    def record_daily_summary(self, summary: DailySummary) -> None: ...

    def load_open_positions(self, session_date: date) -> tuple[OpenPositionRow, ...]: ...

    def load_daily_pnl(self, session_date: date) -> DailyPnlSnapshot: ...

    def close(self) -> None: ...


class NullTradingRecorder:
    """no-op `TradingRecorder`.

    `_default_recorder_factory` 가 `SqliteTradingRecorder` 조립 실패 시 폴백
    으로 주입한다 — 영속화 부재가 세션 전체 실패보다 덜 위험하다는
    판단(ADR-0013). 로그(loguru sink) 는 여전히 유지되므로 사후 재구성 경로가
    완전히 닫히지 않는다.

    `load_*` 는 "기록 없음" 과 동일한 빈 결과를 반환 — 재기동 복원 경로가
    Null 폴백에서는 자연스럽게 "신규 세션" 분기를 타도록.
    """

    def record_entry(self, event: EntryEvent) -> None:  # noqa: ARG002
        return None

    def record_exit(self, event: ExitEvent) -> None:  # noqa: ARG002
        return None

    def record_daily_summary(self, summary: DailySummary) -> None:  # noqa: ARG002
        return None

    def load_open_positions(
        self,
        session_date: date,  # noqa: ARG002
    ) -> tuple[OpenPositionRow, ...]:
        return ()

    def load_daily_pnl(self, session_date: date) -> DailyPnlSnapshot:
        return DailyPnlSnapshot(
            session_date=session_date,
            realized_pnl_krw=0,
            entries_today=0,
            closed_symbols=(),
        )

    def close(self) -> None:
        return None


class SqliteTradingRecorder:
    """SQLite 기반 `TradingRecorder` — 단일 파일 원장.

    생성 시 스키마 v1 을 적용(IF NOT EXISTS + schema_version 기록) 하고 PRAGMA
    (WAL·NORMAL·foreign_keys) 를 설정한다. `record_*` 는 autocommit 단건
    INSERT 로 기록 — 스키마 init 만 `BEGIN IMMEDIATE` 로 원자성 확보.
    """

    def __init__(
        self,
        *,
        db_path: str | Path = _DEFAULT_DB_PATH,
        consecutive_failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
    ) -> None:
        """
        Args:
            db_path: SQLite 파일 경로. `":memory:"` 도 허용 (테스트용).
                파일 경로이면 부모 디렉토리를 자동 생성한다. 경로가 기존
                디렉토리이면 `StorageError` 로 fail-fast. **상대경로 기본값은
                현재 작업 디렉토리(CWD) 기준으로 해석되므로 운영 진입점
                (`main.py`) 은 절대경로를 주입한다 — 리뷰 C1 기조.**
            consecutive_failure_threshold: 연속 실패 몇 번째에서 `logger.critical`
                1회 경보를 낼지. 기본 5. 1 이상.

        Raises:
            StorageError: 스키마 init 실패·디렉토리 경로 오지정·connect 실패.
                원본 예외는 `__cause__` 로 보존.
            RuntimeError: `consecutive_failure_threshold` 가 1 미만.
        """
        if consecutive_failure_threshold <= 0:
            raise RuntimeError(
                "SqliteTradingRecorder.consecutive_failure_threshold 는 1 이상이어야 "
                f"합니다 (got={consecutive_failure_threshold})."
            )
        self._closed = False
        self._consecutive_failures: dict[str, int] = {op: 0 for op in _TRACKED_OPS}
        self._critical_emitted: dict[str, bool] = {op: False for op in _TRACKED_OPS}
        self._threshold = consecutive_failure_threshold
        self._is_memory = isinstance(db_path, str) and db_path == ":memory:"

        self._conn = self._open_connection(db_path)
        try:
            self._apply_pragmas()
            self._init_schema()
        except sqlite3.Error as e:
            self._safe_close_conn()
            raise StorageError(
                f"SqliteTradingRecorder 스키마 초기화 실패: {e.__class__.__name__}: {e}"
            ) from e
        except StorageError:
            self._safe_close_conn()
            raise

    @staticmethod
    def _open_connection(db_path: str | Path) -> sqlite3.Connection:
        """connect + 디렉토리·권한 가드.

        Raises:
            StorageError: 경로가 디렉토리이거나 connect 실패.
        """
        if isinstance(db_path, str) and db_path == ":memory:":
            try:
                return sqlite3.connect(":memory:", isolation_level=None)
            except sqlite3.Error as e:
                raise StorageError(
                    f"SqliteTradingRecorder: sqlite3.connect(':memory:') 실패: {e}"
                ) from e

        path = Path(db_path)
        if path.exists() and path.is_dir():
            raise StorageError(
                f"SqliteTradingRecorder: db_path={path} 는 디렉토리입니다. 파일 경로를 지정하세요."
            )
        parent = path.parent
        if parent and str(parent) not in ("", "."):
            # mkdir 실패는 아래 connect 에서 sqlite3.Error 로 귀결 →
            # StorageError 로 래핑되며 원본은 __cause__ 에 보존.
            with contextlib.suppress(OSError):
                parent.mkdir(parents=True, exist_ok=True)
        try:
            return sqlite3.connect(str(path), isolation_level=None)
        except sqlite3.Error as e:
            raise StorageError(
                f"SqliteTradingRecorder: sqlite3.connect({path}) 실패: {e.__class__.__name__}: {e}"
            ) from e

    def _apply_pragmas(self) -> None:
        """WAL·NORMAL·foreign_keys. WAL 은 파일 기반에서만 적용."""
        if not self._is_memory:
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _init_schema(self) -> None:
        """스키마 v1 을 IF NOT EXISTS 로 적용.

        모든 CREATE/SELECT/INSERT 를 단일 `BEGIN IMMEDIATE` 내부에서 실행해
        동시 프로세스 경합 상황에서 `schema_version` 만 먼저 생성되고 나머지
        테이블은 미생성된 "부분 스키마" 상태가 남는 것을 막는다(리뷰 C3).
        """
        cur = self._conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            try:
                cur.execute(_CREATE_SCHEMA_VERSION_SQL)
                cur.execute(_CREATE_ORDERS_SQL)
                cur.execute(_CREATE_DAILY_PNL_SQL)
                cur.execute(_CREATE_ORDERS_INDEX_SESSION)
                cur.execute(_CREATE_ORDERS_INDEX_SYMBOL)
                row = cur.execute("SELECT MAX(version) FROM schema_version").fetchone()
                current = row[0] if row and row[0] is not None else 0
                if current < _SCHEMA_VERSION:
                    cur.execute(
                        "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                        (_SCHEMA_VERSION,),
                    )
                cur.execute("COMMIT")
            except Exception:
                with contextlib.suppress(sqlite3.Error):
                    cur.execute("ROLLBACK")
                raise
        finally:
            cur.close()

    def _safe_close_conn(self) -> None:
        """예외 경로용 연결 닫기 — 실패 무시."""
        try:
            self._conn.close()
        except Exception as e:  # noqa: BLE001 — 이미 실패 경로, 덮어쓰기 방지
            logger.warning(
                f"storage._safe_close_conn: connection close 실패 (무시): "
                f"{e.__class__.__name__}: {e}"
            )

    # ---- Protocol impl --------------------------------------------------

    def record_entry(self, event: EntryEvent) -> None:
        """EntryEvent 를 orders 테이블(buy) 에 INSERT.

        close 후 호출은 warning + silent. `sqlite3.Error` 및 기타 `Exception`
        은 silent fail + 연속 실패 dedupe 경보(I2: 매매 루프 보호 계약을
        sqlite3 외부 예외까지 포괄).
        """
        if self._closed:
            logger.warning(
                "storage.record_entry: 이미 close() 된 recorder — 무시 "
                f"(order_number={event.order_number})"
            )
            return
        if event.timestamp.tzinfo is None:
            # DTO __post_init__ 가 이미 tz-aware 를 강제하지만 defensive depth.
            logger.warning(
                "storage.record_entry: naive timestamp 감지 — reject "
                f"(order_number={event.order_number})"
            )
            self._consecutive_failures[_OP_RECORD_ENTRY] += 1
            self._maybe_emit_critical(_OP_RECORD_ENTRY)
            return
        session_date = event.timestamp.date().isoformat()
        try:
            self._conn.execute(
                "INSERT INTO orders "
                "(order_number, session_date, symbol, side, qty, fill_price, "
                " ref_price, exit_reason, net_pnl_krw, filled_at) "
                "VALUES (?, ?, ?, 'buy', ?, ?, ?, NULL, NULL, ?)",
                (
                    event.order_number,
                    session_date,
                    event.symbol,
                    int(event.qty),
                    str(event.fill_price),
                    str(event.ref_price),
                    event.timestamp.isoformat(),
                ),
            )
            self._on_success(_OP_RECORD_ENTRY)
        except Exception as e:  # noqa: BLE001 — I2: silent fail 계약 (매매 루프 보호)
            self._on_failure(_OP_RECORD_ENTRY, e)

    def record_exit(self, event: ExitEvent) -> None:
        """ExitEvent 를 orders 테이블(sell) 에 INSERT. `ref_price` 는 `fill_price` 복사.

        ExitEvent 는 참고가 필드가 없으므로 orders.ref_price 는 fill_price 와
        동일 값으로 기록. 주문 의도·추정 체결가 격차 분석이 필요하면 별도
        컬럼을 v2 에 추가.
        """
        if self._closed:
            logger.warning(
                "storage.record_exit: 이미 close() 된 recorder — 무시 "
                f"(order_number={event.order_number})"
            )
            return
        if event.timestamp.tzinfo is None:
            logger.warning(
                "storage.record_exit: naive timestamp 감지 — reject "
                f"(order_number={event.order_number})"
            )
            self._consecutive_failures[_OP_RECORD_EXIT] += 1
            self._maybe_emit_critical(_OP_RECORD_EXIT)
            return
        session_date = event.timestamp.date().isoformat()
        try:
            self._conn.execute(
                "INSERT INTO orders "
                "(order_number, session_date, symbol, side, qty, fill_price, "
                " ref_price, exit_reason, net_pnl_krw, filled_at) "
                "VALUES (?, ?, ?, 'sell', ?, ?, ?, ?, ?, ?)",
                (
                    event.order_number,
                    session_date,
                    event.symbol,
                    int(event.qty),
                    str(event.fill_price),
                    str(event.fill_price),
                    event.reason,
                    int(event.net_pnl_krw),
                    event.timestamp.isoformat(),
                ),
            )
            self._on_success(_OP_RECORD_EXIT)
        except Exception as e:  # noqa: BLE001 — I2: silent fail 계약
            self._on_failure(_OP_RECORD_EXIT, e)

    def record_daily_summary(self, summary: DailySummary) -> None:
        """DailySummary 를 daily_pnl 테이블에 session_date PK INSERT OR REPLACE.

        `mismatch_symbols` 는 `json.dumps(list)` 로 직렬화. `halted` 는 0/1.
        `recorded_at` 은 호출 시점 KST aware `now()` — 동일 세션의 재실행·재집계
        이력을 구분하기 위해.
        """
        if self._closed:
            logger.warning(
                "storage.record_daily_summary: 이미 close() 된 recorder — 무시 "
                f"(session_date={summary.session_date})"
            )
            return
        recorded_at = datetime.now(KST).isoformat()
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO daily_pnl "
                "(session_date, starting_capital_krw, realized_pnl_krw, "
                " realized_pnl_pct, entries_today, halted, mismatch_symbols, "
                " recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    summary.session_date.isoformat(),
                    summary.starting_capital_krw,
                    int(summary.realized_pnl_krw),
                    summary.realized_pnl_pct,
                    int(summary.entries_today),
                    1 if summary.halted else 0,
                    json.dumps(list(summary.mismatch_symbols)),
                    recorded_at,
                ),
            )
            self._on_success(_OP_RECORD_DAILY_SUMMARY)
        except Exception as e:  # noqa: BLE001 — I2: silent fail 계약
            self._on_failure(_OP_RECORD_DAILY_SUMMARY, e)

    def load_open_positions(self, session_date: date) -> tuple[OpenPositionRow, ...]:
        """Issue #33 — 재기동 시 당일 오픈 포지션 복원.

        `orders` 테이블에서 `session_date` 의 buy·sell 을 `filled_at` 순으로
        재생해 아직 청산되지 않은 포지션을 반환. 1일 1심볼 1회 진입 계약
        (`ORBStrategy._dispatch_bar` 의 closed 재진입 차단) 을 전제로 하므로
        동일 심볼의 buy 가 재등장해도 마지막 buy 를 보존한다(데이터 오염 방어적
        허용 — 일반 경로에서는 발생하지 않음).

        실패 정책 — `record_*` 와 동일 silent fail:
            close 후 호출 → warning 1회 + 빈 tuple 반환 (카운터 불변).
            `sqlite3.Error` 및 기타 `Exception` → silent fail + 연속 실패
            dedupe 경보 + 빈 tuple 반환. `DecimalException` 등 데이터 파싱
            실패도 동일 경로.

        Raises:
            이 메서드는 raise 하지 않는다 — 매매 루프 보호 계약(ADR-0013) 을
            load 경로까지 확장한다. 복원 실패 시 호출자(main) 는 빈 결과를
            "신규 세션" 으로 해석하므로 절전 복구가 데이터 손실로 이어질 수
            있지만, 부분 복원(예: 일부 포지션만 복원) 보다는 전체 스킵이 안전.
        """
        if self._closed:
            logger.warning(
                "storage.load_open_positions: 이미 close() 된 recorder — 빈 결과 반환 "
                f"(session_date={session_date.isoformat()})"
            )
            return ()
        try:
            rows = self._conn.execute(
                "SELECT side, symbol, qty, fill_price, order_number, filled_at "
                "FROM orders WHERE session_date = ? ORDER BY filled_at ASC, rowid ASC",
                (session_date.isoformat(),),
            ).fetchall()
            open_map: dict[str, OpenPositionRow] = {}
            for side, symbol, qty, fill_price, order_number, filled_at in rows:
                if side == "buy":
                    open_map[symbol] = OpenPositionRow(
                        symbol=symbol,
                        qty=int(qty),
                        entry_price=Decimal(fill_price),
                        entry_ts=datetime.fromisoformat(filled_at),
                        order_number=order_number,
                    )
                elif side == "sell":
                    open_map.pop(symbol, None)
                else:  # pragma: no cover — CHECK 제약이 이미 차단
                    logger.warning(
                        "storage.load_open_positions: 알 수 없는 side={side} 무시",
                        side=side,
                    )
            self._on_success(_OP_LOAD_OPEN_POSITIONS)
            return tuple(open_map.values())
        except (sqlite3.Error, DecimalException, ValueError, TypeError) as e:
            self._on_failure(_OP_LOAD_OPEN_POSITIONS, e)
            return ()

    def load_daily_pnl(self, session_date: date) -> DailyPnlSnapshot:
        """Issue #33 — 재기동 시 당일 PnL·진입 횟수·청산 심볼 집계.

        `orders` 테이블에서 `session_date` 행을 훑어:
            - buy 개수 → `entries_today` (성공 진입 = 1건 buy 기록).
            - sell 의 `net_pnl_krw` 합 → `realized_pnl_krw`.
            - sell 의 symbol 집합 (정렬 tuple) → `closed_symbols`.

        실패 정책 — `load_open_positions` 와 동일 silent fail. 실패 시 "빈
        상태" snapshot 을 반환해 호출자(main) 가 신규 세션 분기로 폴백.
        """
        empty = DailyPnlSnapshot(
            session_date=session_date,
            realized_pnl_krw=0,
            entries_today=0,
            closed_symbols=(),
        )
        if self._closed:
            logger.warning(
                "storage.load_daily_pnl: 이미 close() 된 recorder — 빈 결과 반환 "
                f"(session_date={session_date.isoformat()})"
            )
            return empty
        try:
            rows = self._conn.execute(
                "SELECT side, symbol, net_pnl_krw FROM orders WHERE session_date = ?",
                (session_date.isoformat(),),
            ).fetchall()
            entries = 0
            pnl = 0
            sold: set[str] = set()
            for side, symbol, net_pnl in rows:
                if side == "buy":
                    entries += 1
                elif side == "sell":
                    sold.add(symbol)
                    if net_pnl is not None:
                        pnl += int(net_pnl)
            self._on_success(_OP_LOAD_DAILY_PNL)
            return DailyPnlSnapshot(
                session_date=session_date,
                realized_pnl_krw=pnl,
                entries_today=entries,
                closed_symbols=tuple(sorted(sold)),
            )
        except (sqlite3.Error, ValueError, TypeError) as e:
            self._on_failure(_OP_LOAD_DAILY_PNL, e)
            return empty

    def close(self) -> None:
        """멱등 close. 실패 경로에서도 호출 가능."""
        if self._closed:
            return
        self._closed = True
        try:
            self._conn.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"storage.close: connection close 실패 (무시): {e.__class__.__name__}: {e}"
            )

    def __enter__(self) -> SqliteTradingRecorder:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---- 내부 상태 -----------------------------------------------------

    def _on_success(self, op: str) -> None:
        self._consecutive_failures[op] = 0
        self._critical_emitted[op] = False

    def _on_failure(self, op: str, err: Exception) -> None:
        self._consecutive_failures[op] += 1
        logger.warning(
            f"storage.{op} 실패 (silent): {err.__class__.__name__}: {err} "
            f"consecutive={self._consecutive_failures[op]}"
        )
        self._maybe_emit_critical(op)

    def _maybe_emit_critical(self, op: str) -> None:
        if self._consecutive_failures[op] >= self._threshold and not self._critical_emitted[op]:
            logger.critical(
                f"storage.persistent_failure: {op} 연속 "
                f"{self._consecutive_failures[op]}회 실패 (threshold={self._threshold}). "
                "DB 파일 권한·디스크 공간·WAL 잠금 등 운영자 확인 필요."
            )
            self._critical_emitted[op] = True
