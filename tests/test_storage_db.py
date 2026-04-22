"""SqliteTradingRecorder / NullTradingRecorder / StorageError 공개 계약 단위 테스트 (RED 모드).

stock_agent.storage 패키지가 아직 미작성 상태이므로 모든 케이스가 ImportError 로
실패한다. 구현 완료 후 GREEN 전환을 목표로 한다.

가드레일: 실 KIS·텔레그램·외부 HTTP 접촉 0.
         SQLite 는 :memory: 또는 tmp_path 기반 파일만 사용.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from stock_agent.execution import EntryEvent, ExitEvent
from stock_agent.monitor import DailySummary

# ---------------------------------------------------------------------------
# import — 이 블록이 ImportError 로 실패하는 것이 RED 모드의 목표.
# 구현 완료 후 GREEN 으로 전환된다.
# ---------------------------------------------------------------------------
from stock_agent.storage import (  # noqa: F401
    NullTradingRecorder,
    SqliteTradingRecorder,
    StorageError,
    TradingRecorder,
)

# ---------------------------------------------------------------------------
# 공통 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))
_DATE = date(2026, 4, 21)
_FIXED_DT = datetime(2026, 4, 21, 9, 30, 0, tzinfo=KST)
_SYMBOL = "005930"
_ORDER_BUY = "ORD-BUY-001"
_ORDER_SELL = "ORD-SELL-001"


def _kst(h: int, m: int, s: int = 0) -> datetime:
    """KST aware datetime 생성 헬퍼."""
    return datetime(_DATE.year, _DATE.month, _DATE.day, h, m, s, tzinfo=KST)


def _make_entry_event(
    *,
    symbol: str = _SYMBOL,
    qty: int = 10,
    fill_price: Decimal = Decimal("50500"),
    ref_price: Decimal = Decimal("50000"),
    timestamp: datetime = _FIXED_DT,
    order_number: str = _ORDER_BUY,
) -> EntryEvent:
    """EntryEvent 생성 헬퍼 — order_number 는 Task #3 에서 DTO 에 추가 예정."""
    # order_number 인자는 현재 EntryEvent DTO 에 없을 수 있다.
    # RED 단계: TypeError('unexpected keyword argument order_number') 도 RED 로 간주.
    return EntryEvent(
        symbol=symbol,
        qty=qty,
        fill_price=fill_price,
        ref_price=ref_price,
        timestamp=timestamp,
        order_number=order_number,
    )


def _make_exit_event(
    *,
    symbol: str = _SYMBOL,
    qty: int = 10,
    fill_price: Decimal = Decimal("51500"),
    reason: str = "take_profit",
    net_pnl_krw: int = 8_500,
    timestamp: datetime | None = None,
    order_number: str = _ORDER_SELL,
) -> ExitEvent:
    """ExitEvent 생성 헬퍼 — order_number 는 Task #3 에서 DTO 에 추가 예정."""
    ts = timestamp or _kst(14, 0)
    return ExitEvent(
        symbol=symbol,
        qty=qty,
        fill_price=fill_price,
        reason=reason,  # type: ignore[arg-type]
        net_pnl_krw=net_pnl_krw,
        timestamp=ts,
        order_number=order_number,
    )


def _make_daily_summary(
    *,
    session_date: date = _DATE,
    starting_capital_krw: int | None = 1_000_000,
    realized_pnl_krw: int = 8_500,
    realized_pnl_pct: float | None = 0.0085,
    entries_today: int = 1,
    halted: bool = False,
    mismatch_symbols: tuple[str, ...] = (),
) -> DailySummary:
    """DailySummary 생성 헬퍼."""
    return DailySummary(
        session_date=session_date,
        starting_capital_krw=starting_capital_krw,
        realized_pnl_krw=realized_pnl_krw,
        realized_pnl_pct=realized_pnl_pct,
        entries_today=entries_today,
        halted=halted,
        mismatch_symbols=mismatch_symbols,
    )


def _make_recorder(db_path: str | Path = ":memory:") -> SqliteTradingRecorder:
    """SqliteTradingRecorder 생성 헬퍼."""
    return SqliteTradingRecorder(db_path=db_path)


def _get_conn(recorder: SqliteTradingRecorder) -> sqlite3.Connection:
    """내부 연결 객체 추출 — 테스트 전용 (private 접근 허용)."""
    # 구현 시 _conn 또는 _connection 사용을 가정
    conn = getattr(recorder, "_conn", None) or getattr(recorder, "_connection", None)
    assert conn is not None, "SqliteTradingRecorder 내부 연결 속성을 찾을 수 없음"
    return conn  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 1. 공개 심볼 export
# ---------------------------------------------------------------------------


class TestPublicExports:
    """stock_agent.storage 에서 4 종 심볼이 모두 임포트 가능하다."""

    def test_trading_recorder_protocol_importable(self) -> None:
        from stock_agent.storage import TradingRecorder  # noqa: F401

    def test_sqlite_trading_recorder_importable(self) -> None:
        from stock_agent.storage import SqliteTradingRecorder  # noqa: F401

    def test_null_trading_recorder_importable(self) -> None:
        from stock_agent.storage import NullTradingRecorder  # noqa: F401

    def test_storage_error_importable(self) -> None:
        from stock_agent.storage import StorageError  # noqa: F401

    def test_storage_error_is_exception(self) -> None:
        assert issubclass(StorageError, Exception)


# ---------------------------------------------------------------------------
# 2. 생성자 · 스키마 초기화 (:memory:)
# ---------------------------------------------------------------------------


class TestSchemaInit:
    """생성자 호출 시 3개 테이블과 schema_version 행이 만들어진다."""

    def test_schema_version_table_exists(self) -> None:
        r = _make_recorder()
        conn = _get_conn(r)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        assert row is not None, "schema_version 테이블이 없음"

    def test_orders_table_exists(self) -> None:
        r = _make_recorder()
        conn = _get_conn(r)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='orders'"
        ).fetchone()
        assert row is not None, "orders 테이블이 없음"

    def test_daily_pnl_table_exists(self) -> None:
        r = _make_recorder()
        conn = _get_conn(r)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='daily_pnl'"
        ).fetchone()
        assert row is not None, "daily_pnl 테이블이 없음"

    def test_schema_version_row_inserted(self) -> None:
        r = _make_recorder()
        conn = _get_conn(r)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row is not None, "schema_version 에 버전 행이 없음"
        assert row[0] == 1

    def test_orders_index_session_exists(self) -> None:
        r = _make_recorder()
        conn = _get_conn(r)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_orders_session'"
        ).fetchone()
        assert row is not None, "idx_orders_session 인덱스가 없음"

    def test_orders_index_symbol_exists(self) -> None:
        r = _make_recorder()
        conn = _get_conn(r)
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_orders_symbol'"
        ).fetchone()
        assert row is not None, "idx_orders_symbol 인덱스가 없음"


# ---------------------------------------------------------------------------
# 3. PRAGMA 적용 검증 (파일 기반, tmp_path)
# ---------------------------------------------------------------------------


class TestPragma:
    """WAL / NORMAL / foreign_keys PRAGMA 가 올바르게 적용된다."""

    def test_journal_mode_wal(self, tmp_path: Path) -> None:
        db_file = tmp_path / "trading.db"
        r = _make_recorder(db_path=db_file)
        conn = _get_conn(r)
        row = conn.execute("PRAGMA journal_mode").fetchone()
        assert row[0].lower() == "wal"

    def test_synchronous_normal(self, tmp_path: Path) -> None:
        db_file = tmp_path / "trading.db"
        r = _make_recorder(db_path=db_file)
        conn = _get_conn(r)
        row = conn.execute("PRAGMA synchronous").fetchone()
        # NORMAL = 1
        assert row[0] == 1

    def test_foreign_keys_on(self, tmp_path: Path) -> None:
        db_file = tmp_path / "trading.db"
        r = _make_recorder(db_path=db_file)
        conn = _get_conn(r)
        row = conn.execute("PRAGMA foreign_keys").fetchone()
        assert row[0] == 1


# ---------------------------------------------------------------------------
# 4. record_entry 정상 — 라운드트립
# ---------------------------------------------------------------------------


class TestRecordEntry:
    """record_entry 가 orders 테이블에 올바르게 저장된다."""

    def test_entry_row_exists_after_record(self) -> None:
        r = _make_recorder()
        r.record_entry(_make_entry_event())
        conn = _get_conn(r)
        row = conn.execute("SELECT * FROM orders WHERE side='buy'").fetchone()
        assert row is not None

    def test_entry_fill_price_decimal_roundtrip(self) -> None:
        r = _make_recorder()
        original = Decimal("50500.50")
        r.record_entry(_make_entry_event(fill_price=original))
        conn = _get_conn(r)
        row = conn.execute("SELECT fill_price FROM orders WHERE side='buy'").fetchone()
        assert Decimal(row[0]) == original

    def test_entry_ref_price_decimal_roundtrip(self) -> None:
        r = _make_recorder()
        original = Decimal("50000.00")
        r.record_entry(_make_entry_event(ref_price=original))
        conn = _get_conn(r)
        row = conn.execute("SELECT ref_price FROM orders WHERE side='buy'").fetchone()
        assert Decimal(row[0]) == original

    def test_entry_filled_at_isoformat_roundtrip(self) -> None:
        r = _make_recorder()
        ts = _kst(9, 30, 5)
        r.record_entry(_make_entry_event(timestamp=ts))
        conn = _get_conn(r)
        row = conn.execute("SELECT filled_at FROM orders WHERE side='buy'").fetchone()
        restored = datetime.fromisoformat(row[0])
        assert restored == ts

    def test_entry_exit_reason_is_null(self) -> None:
        r = _make_recorder()
        r.record_entry(_make_entry_event())
        conn = _get_conn(r)
        row = conn.execute("SELECT exit_reason FROM orders WHERE side='buy'").fetchone()
        assert row[0] is None

    def test_entry_net_pnl_is_null(self) -> None:
        r = _make_recorder()
        r.record_entry(_make_entry_event())
        conn = _get_conn(r)
        row = conn.execute("SELECT net_pnl_krw FROM orders WHERE side='buy'").fetchone()
        assert row[0] is None

    def test_entry_symbol_stored(self) -> None:
        r = _make_recorder()
        r.record_entry(_make_entry_event(symbol="000660"))
        conn = _get_conn(r)
        row = conn.execute("SELECT symbol FROM orders WHERE side='buy'").fetchone()
        assert row[0] == "000660"

    def test_entry_qty_stored(self) -> None:
        r = _make_recorder()
        r.record_entry(_make_entry_event(qty=7))
        conn = _get_conn(r)
        row = conn.execute("SELECT qty FROM orders WHERE side='buy'").fetchone()
        assert row[0] == 7

    def test_entry_order_number_stored(self) -> None:
        r = _make_recorder()
        r.record_entry(_make_entry_event(order_number="ORD-X-999"))
        conn = _get_conn(r)
        row = conn.execute("SELECT order_number FROM orders WHERE side='buy'").fetchone()
        assert row[0] == "ORD-X-999"


# ---------------------------------------------------------------------------
# 5. record_exit 정상 — 라운드트립
# ---------------------------------------------------------------------------


class TestRecordExit:
    """record_exit 가 orders 테이블에 올바르게 저장된다."""

    def test_exit_side_is_sell(self) -> None:
        r = _make_recorder()
        r.record_exit(_make_exit_event())
        conn = _get_conn(r)
        row = conn.execute("SELECT side FROM orders WHERE side='sell'").fetchone()
        assert row is not None
        assert row[0] == "sell"

    def test_exit_reason_stored(self) -> None:
        r = _make_recorder()
        r.record_exit(_make_exit_event(reason="stop_loss"))
        conn = _get_conn(r)
        row = conn.execute("SELECT exit_reason FROM orders WHERE side='sell'").fetchone()
        assert row[0] == "stop_loss"

    @pytest.mark.parametrize(
        "reason",
        ["stop_loss", "take_profit", "force_close"],
        ids=["stop_loss", "take_profit", "force_close"],
    )
    def test_exit_reason_all_variants(self, reason: str) -> None:
        r = _make_recorder()
        r.record_exit(_make_exit_event(reason=reason, order_number=f"ORD-{reason}"))
        conn = _get_conn(r)
        row = conn.execute(
            "SELECT exit_reason FROM orders WHERE order_number=?", (f"ORD-{reason}",)
        ).fetchone()
        assert row[0] == reason

    def test_exit_net_pnl_krw_stored(self) -> None:
        r = _make_recorder()
        r.record_exit(_make_exit_event(net_pnl_krw=-1500))
        conn = _get_conn(r)
        row = conn.execute("SELECT net_pnl_krw FROM orders WHERE side='sell'").fetchone()
        assert row[0] == -1500

    def test_exit_independent_of_entry(self) -> None:
        """EntryEvent 선행 없이도 ExitEvent 를 단독으로 저장할 수 있다 (외래키 없음)."""
        r = _make_recorder()
        r.record_exit(_make_exit_event())
        conn = _get_conn(r)
        count = conn.execute("SELECT COUNT(*) FROM orders WHERE side='sell'").fetchone()[0]
        assert count == 1

    def test_exit_fill_price_decimal_roundtrip(self) -> None:
        r = _make_recorder()
        original = Decimal("51500.75")
        r.record_exit(_make_exit_event(fill_price=original))
        conn = _get_conn(r)
        row = conn.execute("SELECT fill_price FROM orders WHERE side='sell'").fetchone()
        assert Decimal(row[0]) == original


# ---------------------------------------------------------------------------
# 6. record_daily_summary 정상 — 라운드트립
# ---------------------------------------------------------------------------


class TestRecordDailySummary:
    """record_daily_summary 가 daily_pnl 테이블에 올바르게 저장된다."""

    def test_summary_row_exists(self) -> None:
        r = _make_recorder()
        r.record_daily_summary(_make_daily_summary())
        conn = _get_conn(r)
        row = conn.execute("SELECT * FROM daily_pnl").fetchone()
        assert row is not None

    def test_summary_session_date_stored(self) -> None:
        r = _make_recorder()
        r.record_daily_summary(_make_daily_summary(session_date=_DATE))
        conn = _get_conn(r)
        row = conn.execute("SELECT session_date FROM daily_pnl").fetchone()
        assert row[0] == _DATE.isoformat()

    def test_summary_mismatch_symbols_json_roundtrip(self) -> None:
        r = _make_recorder()
        mismatches = ("005930", "000660")
        r.record_daily_summary(_make_daily_summary(mismatch_symbols=mismatches))
        conn = _get_conn(r)
        row = conn.execute("SELECT mismatch_symbols FROM daily_pnl").fetchone()
        restored = tuple(json.loads(row[0]))
        assert restored == mismatches

    def test_summary_empty_mismatch_symbols(self) -> None:
        r = _make_recorder()
        r.record_daily_summary(_make_daily_summary(mismatch_symbols=()))
        conn = _get_conn(r)
        row = conn.execute("SELECT mismatch_symbols FROM daily_pnl").fetchone()
        restored = json.loads(row[0])
        assert restored == []

    def test_summary_halted_true_stored_as_one(self) -> None:
        r = _make_recorder()
        r.record_daily_summary(_make_daily_summary(halted=True))
        conn = _get_conn(r)
        row = conn.execute("SELECT halted FROM daily_pnl").fetchone()
        assert row[0] == 1

    def test_summary_halted_false_stored_as_zero(self) -> None:
        r = _make_recorder()
        r.record_daily_summary(_make_daily_summary(halted=False))
        conn = _get_conn(r)
        row = conn.execute("SELECT halted FROM daily_pnl").fetchone()
        assert row[0] == 0

    def test_summary_starting_capital_none_allowed(self) -> None:
        r = _make_recorder()
        r.record_daily_summary(_make_daily_summary(starting_capital_krw=None))
        conn = _get_conn(r)
        row = conn.execute("SELECT starting_capital_krw FROM daily_pnl").fetchone()
        assert row[0] is None

    def test_summary_realized_pnl_pct_none_allowed(self) -> None:
        r = _make_recorder()
        r.record_daily_summary(
            _make_daily_summary(starting_capital_krw=None, realized_pnl_pct=None)
        )
        conn = _get_conn(r)
        row = conn.execute("SELECT realized_pnl_pct FROM daily_pnl").fetchone()
        assert row[0] is None

    def test_summary_realized_pnl_krw_stored(self) -> None:
        r = _make_recorder()
        r.record_daily_summary(_make_daily_summary(realized_pnl_krw=-2_000))
        conn = _get_conn(r)
        row = conn.execute("SELECT realized_pnl_krw FROM daily_pnl").fetchone()
        assert row[0] == -2_000


# ---------------------------------------------------------------------------
# 7. daily_summary INSERT OR REPLACE — 같은 날 덮어쓰기
# ---------------------------------------------------------------------------


class TestDailySummaryReplace:
    """같은 session_date 로 두 번 기록 시 마지막 값만 남는다."""

    def test_second_write_overwrites_first(self) -> None:
        r = _make_recorder()
        r.record_daily_summary(_make_daily_summary(realized_pnl_krw=1_000))
        r.record_daily_summary(_make_daily_summary(realized_pnl_krw=2_000))
        conn = _get_conn(r)
        count = conn.execute("SELECT COUNT(*) FROM daily_pnl").fetchone()[0]
        row = conn.execute("SELECT realized_pnl_krw FROM daily_pnl").fetchone()
        assert count == 1
        assert row[0] == 2_000


# ---------------------------------------------------------------------------
# 8. order_number PK 충돌 — silent fail
# ---------------------------------------------------------------------------


class TestPrimaryKeyConflict:
    """같은 order_number 로 중복 삽입 시 raise 없이 silent fail."""

    def test_duplicate_entry_does_not_raise(self) -> None:
        r = _make_recorder()
        r.record_entry(_make_entry_event(order_number="ORD-DUP"))
        # 두 번째 삽입 — PK 충돌이지만 raise 되면 안 된다
        r.record_entry(_make_entry_event(order_number="ORD-DUP"))
        # 예외가 없으면 통과

    def test_duplicate_entry_increments_counter(self) -> None:
        r = _make_recorder()
        r.record_entry(_make_entry_event(order_number="ORD-DUP2"))
        r.record_entry(_make_entry_event(order_number="ORD-DUP2"))
        assert r._consecutive_failures["record_entry"] == 1


# ---------------------------------------------------------------------------
# 9. silent fail + dedupe critical 로그
# ---------------------------------------------------------------------------


class TestSilentFailAndDedupe:
    """연속 실패 시 threshold 도달 시 critical 1회만 방출, 성공 후 리셋."""

    def _replace_with_failing_conn(
        self, mocker: MockerFixture, recorder: SqliteTradingRecorder
    ) -> MagicMock:
        """내부 연결을 MagicMock 으로 교체해 execute 를 OperationalError 로 강제.

        sqlite3.Connection.execute 는 C 확장의 read-only 속성이라 mocker.patch.object
        가 불가. _conn 자체를 MagicMock 으로 교체한다.
        """
        fake_conn = mocker.MagicMock()
        fake_conn.execute.side_effect = sqlite3.OperationalError("forced failure")
        recorder._conn = fake_conn
        return fake_conn

    def test_five_failures_emit_critical_once(self, mocker: MockerFixture) -> None:
        r = _make_recorder()
        mock_logger = mocker.patch("stock_agent.storage.db.logger")
        self._replace_with_failing_conn(mocker, r)
        for _ in range(5):
            r.record_entry(_make_entry_event())
        mock_logger.critical.assert_called_once()

    def test_critical_not_emitted_before_threshold(self, mocker: MockerFixture) -> None:
        r = _make_recorder()
        mock_logger = mocker.patch("stock_agent.storage.db.logger")
        self._replace_with_failing_conn(mocker, r)
        for _ in range(4):
            r.record_entry(_make_entry_event())
        mock_logger.critical.assert_not_called()

    def test_critical_not_emitted_again_after_threshold(self, mocker: MockerFixture) -> None:
        r = _make_recorder()
        mock_logger = mocker.patch("stock_agent.storage.db.logger")
        self._replace_with_failing_conn(mocker, r)
        for _ in range(7):
            r.record_entry(_make_entry_event())
        mock_logger.critical.assert_called_once()

    def test_warning_emitted_on_each_failure(self, mocker: MockerFixture) -> None:
        r = _make_recorder()
        mock_logger = mocker.patch("stock_agent.storage.db.logger")
        self._replace_with_failing_conn(mocker, r)
        for _ in range(3):
            r.record_entry(_make_entry_event())
        assert mock_logger.warning.call_count == 3

    def test_counter_resets_after_success(self, mocker: MockerFixture) -> None:
        """성공 1회 후 카운터가 0으로 리셋된다."""
        r = _make_recorder()
        original_conn = r._conn  # 보존

        call_count = {"n": 0}
        fake_conn = mocker.MagicMock()

        def patched_execute(sql: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            call_count["n"] += 1
            if call_count["n"] <= 3:
                raise sqlite3.OperationalError("forced")
            return original_conn.execute(sql, *args, **kwargs)

        fake_conn.execute.side_effect = patched_execute
        r._conn = fake_conn

        for _ in range(3):
            r.record_entry(_make_entry_event())
        assert r._consecutive_failures["record_entry"] == 3

        # 성공 1회 — 카운터 리셋
        r.record_entry(_make_entry_event(order_number="ORD-SUCCESS"))
        assert r._consecutive_failures["record_entry"] == 0

    def test_critical_reemitted_after_reset_and_new_threshold(self, mocker: MockerFixture) -> None:
        """리셋 후 다시 threshold 에 도달하면 critical 이 다시 1회 방출된다."""
        r = SqliteTradingRecorder(db_path=":memory:", consecutive_failure_threshold=3)
        mock_logger = mocker.patch("stock_agent.storage.db.logger")
        original_conn = r._conn  # 보존

        call_count = {"n": 0}
        fake_conn = mocker.MagicMock()

        def patched_execute(sql: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            call_count["n"] += 1
            # 1~3회: 실패, 4회: 성공, 5~7회: 실패
            if call_count["n"] in (1, 2, 3, 5, 6, 7):
                raise sqlite3.OperationalError("forced")
            return original_conn.execute(sql, *args, **kwargs)

        fake_conn.execute.side_effect = patched_execute
        r._conn = fake_conn

        for _ in range(3):  # 1~3: 실패, critical 1회
            r.record_entry(_make_entry_event())
        r.record_entry(_make_entry_event(order_number="ORD-OK"))  # 4: 성공, 리셋
        for _ in range(3):  # 5~7: 실패, critical 다시 1회
            r.record_entry(_make_entry_event())

        assert mock_logger.critical.call_count == 2


# ---------------------------------------------------------------------------
# 10. close 멱등 + 컨텍스트 매니저
# ---------------------------------------------------------------------------


class TestCloseAndContextManager:
    """close() 중복 호출 예외 없음, 컨텍스트 매니저 지원."""

    def test_close_idempotent(self) -> None:
        r = _make_recorder()
        r.close()
        r.close()  # 두 번째 호출도 예외 없음

    def test_context_manager_exits_cleanly(self) -> None:
        with _make_recorder() as r:
            r.record_entry(_make_entry_event())
        # with 블록 탈출 후 예외 없음

    def test_context_manager_returns_recorder(self) -> None:
        with _make_recorder() as r:
            assert isinstance(r, SqliteTradingRecorder)

    def test_context_manager_close_on_exception(self) -> None:
        """예외 발생 시에도 __exit__ 가 연결을 닫는다."""
        r = _make_recorder()
        try:
            with r:
                raise ValueError("test exception")
        except ValueError:
            pass
        # close 후 재호출도 예외 없음
        r.close()


# ---------------------------------------------------------------------------
# 11. close 후 record_* — silent warning
# ---------------------------------------------------------------------------


class TestRecordAfterClose:
    """close 후 record_* 호출은 warning 로그 + silent (예외 없음)."""

    def test_record_entry_after_close_does_not_raise(self, mocker: MockerFixture) -> None:
        r = _make_recorder()
        r.close()
        r.record_entry(_make_entry_event())  # 예외 없어야 함

    def test_record_exit_after_close_does_not_raise(self, mocker: MockerFixture) -> None:
        r = _make_recorder()
        r.close()
        r.record_exit(_make_exit_event())

    def test_record_daily_summary_after_close_does_not_raise(self, mocker: MockerFixture) -> None:
        r = _make_recorder()
        r.close()
        r.record_daily_summary(_make_daily_summary())

    def test_record_after_close_emits_warning(self, mocker: MockerFixture) -> None:
        mock_logger = mocker.patch("stock_agent.storage.db.logger")
        r = _make_recorder()
        r.close()
        r.record_entry(_make_entry_event())
        mock_logger.warning.assert_called()

    def test_counter_not_incremented_after_close(self, mocker: MockerFixture) -> None:
        """close 후 record_* 는 _consecutive_failures["record_entry"] 를 올리지 않는다."""
        r = _make_recorder()
        r.close()
        r.record_entry(_make_entry_event())
        assert r._consecutive_failures["record_entry"] == 0


# ---------------------------------------------------------------------------
# 12. NullTradingRecorder — no-op + 멱등
# ---------------------------------------------------------------------------


class TestNullTradingRecorder:
    """NullTradingRecorder 는 모든 메서드가 no-op 이며 예외를 발생시키지 않는다."""

    def test_record_entry_no_op(self) -> None:
        n = NullTradingRecorder()
        n.record_entry(_make_entry_event())

    def test_record_exit_no_op(self) -> None:
        n = NullTradingRecorder()
        n.record_exit(_make_exit_event())

    def test_record_daily_summary_no_op(self) -> None:
        n = NullTradingRecorder()
        n.record_daily_summary(_make_daily_summary())

    def test_close_idempotent(self) -> None:
        n = NullTradingRecorder()
        n.close()
        n.close()

    def test_null_satisfies_protocol(self) -> None:
        """NullTradingRecorder 는 TradingRecorder Protocol 을 만족한다."""

        # Protocol 이 runtime_checkable 일 때만 isinstance 검사 가능
        try:
            result = isinstance(NullTradingRecorder(), TradingRecorder)
            assert result
        except TypeError:
            # runtime_checkable 미적용 시 스킵 (정적 검사로 대체)
            pytest.skip("TradingRecorder is not @runtime_checkable")


# ---------------------------------------------------------------------------
# 13. 생성자 실패 → StorageError
# ---------------------------------------------------------------------------


class TestConstructorFailure:
    """잘못된 경로·sqlite3 오류 시 StorageError 가 raise 된다."""

    def test_directory_as_path_raises_storage_error(self, tmp_path: Path) -> None:
        """db_path 가 디렉토리이면 StorageError."""
        with pytest.raises(StorageError):
            SqliteTradingRecorder(db_path=tmp_path)  # tmp_path 자체는 디렉토리

    def test_storage_error_has_cause(self, mocker: MockerFixture) -> None:
        """StorageError.__cause__ 에 원본 sqlite3 예외가 보존된다."""
        mocker.patch(
            "sqlite3.connect",
            side_effect=sqlite3.OperationalError("unable to open"),
        )
        with pytest.raises(StorageError) as exc_info:
            SqliteTradingRecorder(db_path="/nonexistent/dir/trading.db")
        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, sqlite3.OperationalError)

    def test_storage_error_is_subclass_of_exception(self) -> None:
        assert issubclass(StorageError, Exception)


# ---------------------------------------------------------------------------
# 14. naive datetime → silent warning + 카운터 증가
#
# 주의: EntryEvent 자체의 __post_init__ 가드가 naive datetime 을 reject 할 경우
# 이 테스트는 Task #3 이후 삭제 가능. 현재는 storage 방어 레이어를 기대한다.
# EntryEvent 가드가 먼저 ValueError 를 내면 SKIP 처리.
# ---------------------------------------------------------------------------


class TestNaiveDatetimeWarning:
    """naive datetime 입력 시 storage 레이어가 warning 을 내고 카운터를 증가시킨다."""

    def test_naive_timestamp_does_not_raise(self, mocker: MockerFixture) -> None:
        """naive datetime 이 storage 에 전달돼도 예외가 전파되지 않는다."""
        naive_dt = datetime(2026, 4, 21, 9, 30)
        try:
            event = EntryEvent(
                symbol=_SYMBOL,
                qty=10,
                fill_price=Decimal("50000"),
                ref_price=Decimal("50000"),
                timestamp=naive_dt,
                order_number="ORD-NAIVE",
            )
        except (TypeError, ValueError, RuntimeError):
            pytest.skip("EntryEvent 자체가 naive datetime 을 거부함 — storage 방어 불필요")

        r = _make_recorder()
        r.record_entry(event)  # 예외 전파 없음

    def test_naive_timestamp_emits_warning(self, mocker: MockerFixture) -> None:
        naive_dt = datetime(2026, 4, 21, 9, 30)
        try:
            event = EntryEvent(
                symbol=_SYMBOL,
                qty=10,
                fill_price=Decimal("50000"),
                ref_price=Decimal("50000"),
                timestamp=naive_dt,
                order_number="ORD-NAIVE2",
            )
        except (TypeError, ValueError, RuntimeError):
            pytest.skip("EntryEvent 자체가 naive datetime 을 거부함")

        mock_logger = mocker.patch("stock_agent.storage.db.logger")
        r = _make_recorder()
        r.record_entry(event)
        mock_logger.warning.assert_called()

    def test_naive_timestamp_increments_counter(self, mocker: MockerFixture) -> None:
        naive_dt = datetime(2026, 4, 21, 9, 30)
        try:
            event = EntryEvent(
                symbol=_SYMBOL,
                qty=10,
                fill_price=Decimal("50000"),
                ref_price=Decimal("50000"),
                timestamp=naive_dt,
                order_number="ORD-NAIVE3",
            )
        except (TypeError, ValueError, RuntimeError):
            pytest.skip("EntryEvent 자체가 naive datetime 을 거부함")

        r = _make_recorder()
        r.record_entry(event)
        assert r._consecutive_failures["record_entry"] == 1


# ---------------------------------------------------------------------------
# 15. per-op 카운터 독립 (C4) + 비-sqlite3 예외 흡수 (I2) + ExitEvent ref_price (C5)
# ---------------------------------------------------------------------------


class TestPerOpCounterIndependence:
    """C4 — op 별 카운터가 독립적으로 동작한다."""

    def _make_partially_failing_conn(
        self,
        mocker: MockerFixture,
        recorder: SqliteTradingRecorder,
        *,
        fail_op_sql_prefix: str,
    ) -> MagicMock:
        """`fail_op_sql_prefix` 로 시작하는 SQL 만 OperationalError 로 강제.

        나머지는 원본 연결로 위임한다.
        """
        original_conn = recorder._conn
        fake_conn = mocker.MagicMock()

        def selective_execute(sql: str, *args, **kwargs):  # type: ignore[no-untyped-def]
            if sql.strip().startswith(fail_op_sql_prefix):
                raise sqlite3.OperationalError("forced selective failure")
            return original_conn.execute(sql, *args, **kwargs)

        fake_conn.execute.side_effect = selective_execute
        recorder._conn = fake_conn
        return fake_conn

    def test_daily_summary_실패가_entry_카운터를_오염시키지_않는다(
        self, mocker: MockerFixture
    ) -> None:
        """record_daily_summary 3회 연속 실패 중 record_entry 성공 시 카운터 독립 유지."""
        r = _make_recorder()
        self._make_partially_failing_conn(
            mocker, r, fail_op_sql_prefix="INSERT OR REPLACE INTO daily_pnl"
        )
        mocker.patch("stock_agent.storage.db.logger")

        for _ in range(3):
            r.record_daily_summary(_make_daily_summary())

        # record_entry 는 orders INSERT 이므로 다른 SQL prefix → 성공
        r.record_entry(_make_entry_event(order_number="ORD-CROSS-1"))

        assert r._consecutive_failures["record_daily_summary"] == 3
        assert r._consecutive_failures["record_entry"] == 0

    def test_daily_summary_만_threshold_도달시_critical_방출(self, mocker: MockerFixture) -> None:
        """threshold=3 에서 record_daily_summary 만 3회 실패 → critical 1회 방출."""
        r = SqliteTradingRecorder(db_path=":memory:", consecutive_failure_threshold=3)
        self._make_partially_failing_conn(
            mocker, r, fail_op_sql_prefix="INSERT OR REPLACE INTO daily_pnl"
        )
        mock_logger = mocker.patch("stock_agent.storage.db.logger")

        # record_entry 성공 (daily_summary threshold 달성에 영향 없어야 함)
        r.record_entry(_make_entry_event(order_number="ORD-OK-CROSS"))

        for _ in range(3):
            r.record_daily_summary(_make_daily_summary())

        mock_logger.critical.assert_called_once()
        critical_msg: str = mock_logger.critical.call_args[0][0]
        assert "record_daily_summary" in critical_msg


class TestNonSqliteExceptionSilentFail:
    """I2 — sqlite3 외부 예외도 silent fail 로 흡수된다."""

    def test_type_error_도_전파하지_않음(self, mocker: MockerFixture) -> None:
        """execute 에서 TypeError 가 발생해도 record_entry 가 예외를 전파하지 않는다."""
        r = _make_recorder()
        fake_conn = mocker.MagicMock()
        fake_conn.execute.side_effect = TypeError("forced non-sqlite error")
        r._conn = fake_conn
        mocker.patch("stock_agent.storage.db.logger")

        # 예외 전파 없음
        r.record_entry(_make_entry_event())

    def test_type_error_카운터_증가(self, mocker: MockerFixture) -> None:
        """execute 에서 TypeError 발생 시 record_entry 카운터가 1 증가한다."""
        r = _make_recorder()
        fake_conn = mocker.MagicMock()
        fake_conn.execute.side_effect = TypeError("forced non-sqlite error")
        r._conn = fake_conn
        mocker.patch("stock_agent.storage.db.logger")

        r.record_entry(_make_entry_event())

        assert r._consecutive_failures["record_entry"] == 1

    def test_type_error_warning_방출(self, mocker: MockerFixture) -> None:
        """execute 에서 TypeError 발생 시 logger.warning 이 1회 호출된다."""
        r = _make_recorder()
        fake_conn = mocker.MagicMock()
        fake_conn.execute.side_effect = TypeError("forced non-sqlite error")
        r._conn = fake_conn
        mock_logger = mocker.patch("stock_agent.storage.db.logger")

        r.record_entry(_make_entry_event())

        mock_logger.warning.assert_called_once()


class TestRecordExitRefPriceCopy:
    """C5 — ExitEvent.ref_price 는 fill_price 복사 계약."""

    def test_exit_ref_price_equals_fill_price(self) -> None:
        """orders 테이블의 ref_price 와 fill_price 가 동일 값으로 저장된다."""
        r = _make_recorder()
        fill = Decimal("49350")
        r.record_exit(_make_exit_event(fill_price=fill, order_number="ORD-EXIT-REF"))
        conn = _get_conn(r)
        row = conn.execute(
            "SELECT ref_price, fill_price FROM orders WHERE order_number = ?",
            ("ORD-EXIT-REF",),
        ).fetchone()
        assert row is not None, "ORD-EXIT-REF 행이 orders 에 없음"
        assert row[0] == row[1], f"ref_price({row[0]}) != fill_price({row[1]})"
        assert row[0] == str(fill)


# ---------------------------------------------------------------------------
# Issue #33 — OpenPositionRow DTO
# ---------------------------------------------------------------------------


class TestOpenPositionRowDTO:
    """OpenPositionRow frozen dataclass 계약 검증."""

    def test_정상_생성(self) -> None:
        from stock_agent.storage import OpenPositionRow

        row = OpenPositionRow(
            symbol="005930",
            qty=10,
            entry_price=Decimal("70000"),
            entry_ts=_kst(9, 31),
            order_number="ORD-001",
        )
        assert row.symbol == "005930"
        assert row.qty == 10
        assert row.entry_price == Decimal("70000")
        assert row.order_number == "ORD-001"

    def test_frozen_변경_불가(self) -> None:
        import dataclasses

        from stock_agent.storage import OpenPositionRow

        row = OpenPositionRow(
            symbol="005930",
            qty=10,
            entry_price=Decimal("70000"),
            entry_ts=_kst(9, 31),
            order_number="ORD-001",
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            row.qty = 99  # type: ignore[misc]

    def test_symbol_포맷_위반_RuntimeError(self) -> None:
        from stock_agent.storage import OpenPositionRow

        with pytest.raises(RuntimeError, match="symbol"):
            OpenPositionRow(
                symbol="ABC",  # 6자리 숫자 아님
                qty=10,
                entry_price=Decimal("70000"),
                entry_ts=_kst(9, 31),
                order_number="ORD-001",
            )

    def test_qty_0이하_RuntimeError(self) -> None:
        from stock_agent.storage import OpenPositionRow

        with pytest.raises(RuntimeError, match="qty"):
            OpenPositionRow(
                symbol="005930",
                qty=0,
                entry_price=Decimal("70000"),
                entry_ts=_kst(9, 31),
                order_number="ORD-001",
            )

    def test_qty_음수_RuntimeError(self) -> None:
        from stock_agent.storage import OpenPositionRow

        with pytest.raises(RuntimeError, match="qty"):
            OpenPositionRow(
                symbol="005930",
                qty=-1,
                entry_price=Decimal("70000"),
                entry_ts=_kst(9, 31),
                order_number="ORD-001",
            )

    def test_entry_price_0이하_RuntimeError(self) -> None:
        from stock_agent.storage import OpenPositionRow

        with pytest.raises(RuntimeError, match="entry_price"):
            OpenPositionRow(
                symbol="005930",
                qty=10,
                entry_price=Decimal("0"),
                entry_ts=_kst(9, 31),
                order_number="ORD-001",
            )

    def test_entry_ts_naive_RuntimeError(self) -> None:
        from stock_agent.storage import OpenPositionRow

        naive_ts = datetime(_DATE.year, _DATE.month, _DATE.day, 9, 31)  # tzinfo=None
        with pytest.raises(RuntimeError, match="tz-aware"):
            OpenPositionRow(
                symbol="005930",
                qty=10,
                entry_price=Decimal("70000"),
                entry_ts=naive_ts,
                order_number="ORD-001",
            )

    def test_order_number_빈문자열_RuntimeError(self) -> None:
        from stock_agent.storage import OpenPositionRow

        with pytest.raises(RuntimeError, match="order_number"):
            OpenPositionRow(
                symbol="005930",
                qty=10,
                entry_price=Decimal("70000"),
                entry_ts=_kst(9, 31),
                order_number="",
            )


# ---------------------------------------------------------------------------
# Issue #33 — DailyPnlSnapshot DTO
# ---------------------------------------------------------------------------


class TestDailyPnlSnapshotDTO:
    """DailyPnlSnapshot frozen dataclass + has_state 계약 검증."""

    def test_정상_생성(self) -> None:
        from stock_agent.storage import DailyPnlSnapshot

        snap = DailyPnlSnapshot(
            session_date=_DATE,
            realized_pnl_krw=0,
            entries_today=0,
            closed_symbols=(),
        )
        assert snap.session_date == _DATE
        assert snap.realized_pnl_krw == 0
        assert snap.entries_today == 0
        assert snap.closed_symbols == ()

    def test_frozen_변경_불가(self) -> None:
        import dataclasses

        from stock_agent.storage import DailyPnlSnapshot

        snap = DailyPnlSnapshot(
            session_date=_DATE,
            realized_pnl_krw=0,
            entries_today=0,
            closed_symbols=(),
        )
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            snap.entries_today = 5  # type: ignore[misc]

    def test_entries_today_음수_RuntimeError(self) -> None:
        from stock_agent.storage import DailyPnlSnapshot

        with pytest.raises(RuntimeError, match="entries_today"):
            DailyPnlSnapshot(
                session_date=_DATE,
                realized_pnl_krw=0,
                entries_today=-1,
                closed_symbols=(),
            )

    def test_has_state_모든_필드_0이면_False(self) -> None:
        from stock_agent.storage import DailyPnlSnapshot

        snap = DailyPnlSnapshot(
            session_date=_DATE,
            realized_pnl_krw=0,
            entries_today=0,
            closed_symbols=(),
        )
        assert snap.has_state is False

    def test_has_state_entries_today_양수이면_True(self) -> None:
        from stock_agent.storage import DailyPnlSnapshot

        snap = DailyPnlSnapshot(
            session_date=_DATE,
            realized_pnl_krw=0,
            entries_today=1,
            closed_symbols=(),
        )
        assert snap.has_state is True

    def test_has_state_closed_symbols_비어있지_않으면_True(self) -> None:
        from stock_agent.storage import DailyPnlSnapshot

        snap = DailyPnlSnapshot(
            session_date=_DATE,
            realized_pnl_krw=0,
            entries_today=0,
            closed_symbols=("005930",),
        )
        assert snap.has_state is True

    def test_has_state_realized_pnl_nonzero이면_True(self) -> None:
        from stock_agent.storage import DailyPnlSnapshot

        snap = DailyPnlSnapshot(
            session_date=_DATE,
            realized_pnl_krw=-1000,
            entries_today=0,
            closed_symbols=(),
        )
        assert snap.has_state is True


# ---------------------------------------------------------------------------
# Issue #33 — NullTradingRecorder load 메서드
# ---------------------------------------------------------------------------


class TestNullTradingRecorderLoadMethods:
    """NullTradingRecorder.load_open_positions / load_daily_pnl 계약 검증."""

    def test_load_open_positions_빈_tuple_반환(self) -> None:
        from stock_agent.storage import NullTradingRecorder

        r = NullTradingRecorder()
        result = r.load_open_positions(_DATE)
        assert result == ()

    def test_load_open_positions_다른_날짜도_빈_tuple(self) -> None:
        from stock_agent.storage import NullTradingRecorder

        r = NullTradingRecorder()
        assert r.load_open_positions(date(2025, 1, 1)) == ()

    def test_load_daily_pnl_빈_snapshot_반환(self) -> None:
        from stock_agent.storage import DailyPnlSnapshot, NullTradingRecorder

        r = NullTradingRecorder()
        result = r.load_daily_pnl(_DATE)

        assert isinstance(result, DailyPnlSnapshot)
        assert result.session_date == _DATE
        assert result.realized_pnl_krw == 0
        assert result.entries_today == 0
        assert result.closed_symbols == ()

    def test_load_daily_pnl_has_state_False(self) -> None:
        from stock_agent.storage import NullTradingRecorder

        r = NullTradingRecorder()
        assert r.load_daily_pnl(_DATE).has_state is False

    def test_load_daily_pnl_다른_날짜_session_date_일치(self) -> None:
        from stock_agent.storage import NullTradingRecorder

        target = date(2025, 6, 15)
        r = NullTradingRecorder()
        result = r.load_daily_pnl(target)
        assert result.session_date == target


# ---------------------------------------------------------------------------
# Issue #33 — load_open_positions (SqliteTradingRecorder)
# ---------------------------------------------------------------------------


class TestLoadOpenPositions:
    """load_open_positions 의 재기동 복원 로직 검증."""

    def _insert_buy(
        self,
        conn: sqlite3.Connection,
        *,
        symbol: str = _SYMBOL,
        qty: int = 10,
        fill_price: str = "70000",
        order_number: str = _ORDER_BUY,
        session_date: str | None = None,
        filled_at: str | None = None,
    ) -> None:
        sd = session_date or _DATE.isoformat()
        fa = filled_at or _kst(9, 31).isoformat()
        conn.execute(
            "INSERT INTO orders "
            "(order_number, session_date, symbol, side, qty, fill_price, ref_price, "
            " exit_reason, net_pnl_krw, filled_at) "
            "VALUES (?, ?, ?, 'buy', ?, ?, ?, NULL, NULL, ?)",
            (order_number, sd, symbol, qty, fill_price, fill_price, fa),
        )

    def _insert_sell(
        self,
        conn: sqlite3.Connection,
        *,
        symbol: str = _SYMBOL,
        qty: int = 10,
        fill_price: str = "71050",
        order_number: str = _ORDER_SELL,
        net_pnl_krw: int = 8500,
        session_date: str | None = None,
        filled_at: str | None = None,
    ) -> None:
        sd = session_date or _DATE.isoformat()
        fa = filled_at or _kst(14, 0).isoformat()
        conn.execute(
            "INSERT INTO orders "
            "(order_number, session_date, symbol, side, qty, fill_price, ref_price, "
            " exit_reason, net_pnl_krw, filled_at) "
            "VALUES (?, ?, ?, 'sell', ?, ?, ?, 'take_profit', ?, ?)",
            (order_number, sd, symbol, qty, fill_price, fill_price, net_pnl_krw, fa),
        )

    def test_buy만_있으면_1건_반환(self) -> None:
        """buy 만 있을 때 open position 1건 반환 — 모든 필드 검증."""
        r = _make_recorder()
        conn = _get_conn(r)
        self._insert_buy(conn)

        result = r.load_open_positions(_DATE)

        assert len(result) == 1
        pos = result[0]
        assert pos.symbol == _SYMBOL
        assert pos.qty == 10
        assert pos.entry_price == Decimal("70000")
        assert pos.entry_ts.tzinfo is not None  # tz-aware
        assert pos.order_number == _ORDER_BUY

    def test_buy_sell_쌍이면_빈_tuple(self) -> None:
        """buy → sell 쌍이 완성되면 오픈 포지션 없음."""
        r = _make_recorder()
        conn = _get_conn(r)
        self._insert_buy(conn)
        self._insert_sell(conn)

        result = r.load_open_positions(_DATE)
        assert result == ()

    def test_여러_심볼_중_하나만_sell_나머지_open(self) -> None:
        """3종목 중 1종목만 sell → 나머지 2종목 반환."""
        r = _make_recorder()
        conn = _get_conn(r)
        self._insert_buy(conn, symbol="005930", order_number="ORD-B-1")
        self._insert_buy(conn, symbol="000660", order_number="ORD-B-2")
        self._insert_buy(conn, symbol="035420", order_number="ORD-B-3")
        # 000660 만 청산
        self._insert_sell(conn, symbol="000660", order_number="ORD-S-2")

        result = r.load_open_positions(_DATE)
        symbols = {p.symbol for p in result}
        assert "005930" in symbols
        assert "035420" in symbols
        assert "000660" not in symbols

    def test_다른_날짜_buy는_반환_안함(self) -> None:
        """어제 buy 는 오늘 session_date 쿼리에서 제외된다."""
        r = _make_recorder()
        conn = _get_conn(r)
        yesterday = date(_DATE.year, _DATE.month, _DATE.day - 1)
        yesterday_filled_at = datetime(
            yesterday.year, yesterday.month, yesterday.day, 9, 31, tzinfo=KST
        ).isoformat()
        self._insert_buy(
            conn,
            session_date=yesterday.isoformat(),
            filled_at=yesterday_filled_at,
        )

        result = r.load_open_positions(_DATE)
        assert result == ()

    def test_filled_at_역순_insert_해도_ORDER_BY_정렬(self) -> None:
        """나중에 INSERT 된 심볼이라도 filled_at ASC 순으로 재생된다 (buy→sell 순서 보장)."""
        r = _make_recorder()
        conn = _get_conn(r)
        # 늦은 시각 insert 먼저, 이른 시각 insert 나중
        self._insert_buy(
            conn,
            symbol="000660",
            order_number="ORD-B-LATE",
            filled_at=_kst(10, 0).isoformat(),
        )
        self._insert_buy(
            conn,
            symbol="005930",
            order_number="ORD-B-EARLY",
            filled_at=_kst(9, 31).isoformat(),
        )
        # 005930 청산 — 정렬이 맞으면 buy→sell 순서 처리, 잘못되면 open 으로 남음
        self._insert_sell(
            conn,
            symbol="005930",
            order_number="ORD-S-1",
            filled_at=_kst(11, 0).isoformat(),
        )

        result = r.load_open_positions(_DATE)
        symbols = {p.symbol for p in result}
        assert "005930" not in symbols
        assert "000660" in symbols

    def test_close_후_호출_빈_tuple_카운터_불변(self) -> None:
        """close() 이후 load_open_positions → warning + 빈 tuple, 카운터 불변."""
        r = _make_recorder()
        r.close()
        result = r.load_open_positions(_DATE)

        assert result == ()
        assert r._consecutive_failures["load_open_positions"] == 0

    def test_sqlite_오류_silent_fail_빈_tuple_카운터_증가(self, mocker: MockerFixture) -> None:
        """sqlite3.Error 주입 시 silent fail — 빈 tuple 반환 + 카운터 1 증가."""
        r = _make_recorder()
        fake_conn = mocker.MagicMock()
        fake_conn.execute.side_effect = sqlite3.OperationalError("forced db error")
        r._conn = fake_conn
        mocker.patch("stock_agent.storage.db.logger")

        result = r.load_open_positions(_DATE)

        assert result == ()
        assert r._consecutive_failures["load_open_positions"] == 1

    def test_연속_5회_실패_critical_1회_dedupe(self, mocker: MockerFixture) -> None:
        """연속 5회 실패 → logger.critical 정확히 1회 방출(dedupe)."""
        r = SqliteTradingRecorder(db_path=":memory:", consecutive_failure_threshold=5)
        fake_conn = mocker.MagicMock()
        fake_conn.execute.side_effect = sqlite3.OperationalError("forced")
        r._conn = fake_conn
        mock_logger = mocker.patch("stock_agent.storage.db.logger")

        for _ in range(5):
            r.load_open_positions(_DATE)

        mock_logger.critical.assert_called_once()

        # 6번째 실패 시 critical 추가 방출 없음
        r.load_open_positions(_DATE)
        mock_logger.critical.assert_called_once()  # 여전히 1회

    def test_성공_1회_후_카운터_리셋(self, mocker: MockerFixture) -> None:
        """성공 1회 → 연속 실패 카운터·dedupe 플래그 리셋."""
        r = _make_recorder()
        fake_conn = mocker.MagicMock()
        fake_conn.execute.side_effect = sqlite3.OperationalError("forced")
        r._conn = fake_conn
        mocker.patch("stock_agent.storage.db.logger")

        # 실패 2회
        r.load_open_positions(_DATE)
        r.load_open_positions(_DATE)
        assert r._consecutive_failures["load_open_positions"] == 2

        # 실제 연결 복원 후 성공
        r._conn = _get_conn(_make_recorder())
        r.load_open_positions(_DATE)

        assert r._consecutive_failures["load_open_positions"] == 0
        assert r._critical_emitted["load_open_positions"] is False


# ---------------------------------------------------------------------------
# Issue #33 — load_daily_pnl (SqliteTradingRecorder)
# ---------------------------------------------------------------------------


class TestLoadDailyPnl:
    """load_daily_pnl 의 집계 로직 검증."""

    def _insert_buy(
        self,
        conn: sqlite3.Connection,
        *,
        symbol: str = _SYMBOL,
        order_number: str = "ORD-B",
        session_date: str | None = None,
        filled_at: str | None = None,
    ) -> None:
        sd = session_date or _DATE.isoformat()
        fa = filled_at or _kst(9, 31).isoformat()
        conn.execute(
            "INSERT INTO orders "
            "(order_number, session_date, symbol, side, qty, fill_price, ref_price, "
            " exit_reason, net_pnl_krw, filled_at) "
            "VALUES (?, ?, ?, 'buy', 10, '70000', '70000', NULL, NULL, ?)",
            (order_number, sd, symbol, fa),
        )

    def _insert_sell(
        self,
        conn: sqlite3.Connection,
        *,
        symbol: str = _SYMBOL,
        order_number: str = "ORD-S",
        net_pnl_krw: int | None = 8500,
        session_date: str | None = None,
        filled_at: str | None = None,
    ) -> None:
        sd = session_date or _DATE.isoformat()
        fa = filled_at or _kst(14, 0).isoformat()
        conn.execute(
            "INSERT INTO orders "
            "(order_number, session_date, symbol, side, qty, fill_price, ref_price, "
            " exit_reason, net_pnl_krw, filled_at) "
            "VALUES (?, ?, ?, 'sell', 10, '71050', '71050', 'take_profit', ?, ?)",
            (order_number, sd, symbol, net_pnl_krw, fa),
        )

    def test_빈_DB_빈_snapshot(self) -> None:
        """orders 가 비어있으면 realized=0, entries=0, closed=()."""
        from stock_agent.storage import DailyPnlSnapshot

        r = _make_recorder()
        result = r.load_daily_pnl(_DATE)

        assert isinstance(result, DailyPnlSnapshot)
        assert result.realized_pnl_krw == 0
        assert result.entries_today == 0
        assert result.closed_symbols == ()

    def test_buy_3건_entries_today_3(self) -> None:
        """buy 3건 → entries_today=3."""
        r = _make_recorder()
        conn = _get_conn(r)
        for i, sym in enumerate(["005930", "000660", "035420"]):
            self._insert_buy(conn, symbol=sym, order_number=f"ORD-B-{i}")

        result = r.load_daily_pnl(_DATE)
        assert result.entries_today == 3

    def test_buy_2건_sell_1건_집계(self) -> None:
        """buy 2건 + sell 1건 → entries=2, closed=(symbol,), realized=net_pnl."""
        r = _make_recorder()
        conn = _get_conn(r)
        self._insert_buy(conn, symbol="005930", order_number="ORD-B-1")
        self._insert_buy(conn, symbol="000660", order_number="ORD-B-2")
        self._insert_sell(conn, symbol="005930", order_number="ORD-S-1", net_pnl_krw=8500)

        result = r.load_daily_pnl(_DATE)
        assert result.entries_today == 2
        assert result.realized_pnl_krw == 8500
        assert "005930" in result.closed_symbols

    def test_여러_sell_합계_음수_포함(self) -> None:
        """sell 여러 건의 net_pnl_krw 합계를 정확히 계산한다."""
        r = _make_recorder()
        conn = _get_conn(r)
        self._insert_buy(conn, symbol="005930", order_number="ORD-B-1")
        self._insert_buy(conn, symbol="000660", order_number="ORD-B-2")
        self._insert_buy(conn, symbol="035420", order_number="ORD-B-3")
        self._insert_sell(conn, symbol="005930", order_number="ORD-S-1", net_pnl_krw=5000)
        self._insert_sell(conn, symbol="000660", order_number="ORD-S-2", net_pnl_krw=-8000)

        result = r.load_daily_pnl(_DATE)
        assert result.realized_pnl_krw == 5000 + (-8000)

    def test_sell_net_pnl_None_무시(self) -> None:
        """sell 행 net_pnl_krw=NULL 은 pnl 합계에서 무시된다 (buy 행 정상)."""
        r = _make_recorder()
        conn = _get_conn(r)
        self._insert_buy(conn, symbol="005930", order_number="ORD-B-1")
        self._insert_sell(conn, symbol="005930", order_number="ORD-S-1", net_pnl_krw=None)

        result = r.load_daily_pnl(_DATE)
        assert result.realized_pnl_krw == 0  # NULL 은 무시

    def test_다른_날짜_session_date_집계_제외(self) -> None:
        """어제 날짜 행은 오늘 session_date 집계에서 제외된다."""
        r = _make_recorder()
        conn = _get_conn(r)
        yesterday = date(_DATE.year, _DATE.month, _DATE.day - 1)
        yesterday_filled_at = datetime(
            yesterday.year, yesterday.month, yesterday.day, 9, 31, tzinfo=KST
        ).isoformat()
        self._insert_buy(
            conn,
            symbol="005930",
            order_number="ORD-B-Y",
            session_date=yesterday.isoformat(),
            filled_at=yesterday_filled_at,
        )

        result = r.load_daily_pnl(_DATE)
        assert result.entries_today == 0
        assert result.realized_pnl_krw == 0

    def test_closed_symbols_정렬_tuple(self) -> None:
        """closed_symbols 는 정렬된 tuple 이어야 한다."""
        r = _make_recorder()
        conn = _get_conn(r)
        for sym in ["035420", "005930", "000660"]:
            self._insert_buy(conn, symbol=sym, order_number=f"ORD-B-{sym}")
            self._insert_sell(conn, symbol=sym, order_number=f"ORD-S-{sym}")

        result = r.load_daily_pnl(_DATE)
        assert list(result.closed_symbols) == sorted(result.closed_symbols)

    def test_close_후_호출_빈_snapshot(self) -> None:
        """close() 이후 load_daily_pnl → warning + 빈 snapshot."""
        from stock_agent.storage import DailyPnlSnapshot

        r = _make_recorder()
        r.close()
        result = r.load_daily_pnl(_DATE)

        assert isinstance(result, DailyPnlSnapshot)
        assert result.entries_today == 0
        assert result.realized_pnl_krw == 0

    def test_sqlite_오류_silent_fail_빈_snapshot_카운터_증가(self, mocker: MockerFixture) -> None:
        """sqlite3.Error 주입 시 silent fail — 빈 snapshot 반환 + 카운터 1 증가."""
        r = _make_recorder()
        fake_conn = mocker.MagicMock()
        fake_conn.execute.side_effect = sqlite3.OperationalError("forced")
        r._conn = fake_conn
        mocker.patch("stock_agent.storage.db.logger")

        result = r.load_daily_pnl(_DATE)

        assert result.entries_today == 0
        assert result.realized_pnl_krw == 0
        assert r._consecutive_failures["load_daily_pnl"] == 1
