"""pykrx 기반 KOSPI 과거 일봉 수집 + SQLite 캐시.

책임 범위
- KOSPI 200 구성종목 조회 (as-of 날짜 기준)
- 개별 종목의 일봉 OHLCV 조회 (start~end)
- 결과를 단일 SQLite 파일 (기본: `data/stock_agent.db`) 에 캐시

범위 제외 (의도적)
- 분봉/틱 데이터: pykrx 가 공식 미지원. `data/realtime.py` 가 장중 분봉 폴링으로 수집.
- KIS Developers 현재가 조회: `broker/kis_client.py` 범위.
- 백테스트 엔진: Phase 2 `backtest/engine.py`.

에러 정책 (broker/kis_client 와 동일 기조)
- `RuntimeError` 는 래핑하지 않고 그대로 전파 (설정 오류 — 재시도 대상 아님).
- 그 외 `Exception` 은 `HistoricalDataError` 로 래핑 + loguru `exception` 로그.
- 사전 가드: `symbol` 형식(6자리 숫자), `start <= end` 는 pykrx 호출 전에 거부.

캐시 정책 (v2, 단순)
- `daily_bars` 테이블: `(symbol, trade_date)` PRIMARY KEY. OHLC + 거래량.
  (v1 에 있던 `value`(거래대금) 컬럼은 제거. pykrx `get_market_ohlcv` 는 단일 종목
  모드에서 거래대금을 반환하지 않아 조용한 0 이 섞이는 무결성 위험이 있었다.
  유동성 필터가 필요하면 추후 전시장 스냅샷 메서드를 별도 추가.)
- `kospi200_constituents` 테이블: `(as_of_date, symbol)` PRIMARY KEY.
- 재호출 판정: "요청 end 날짜가 DB 에 존재" + "end < today" 이면 캐시 적중.
  당일(T) 데이터는 장 종료 여부를 확정할 수 없어 항상 재조회한다.
- v1 스키마가 감지되면 `daily_bars` 를 DROP 후 v2 로 재생성 (캐시 재구축).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import TracebackType
from typing import Any

from loguru import logger

PykrxFactory = Callable[[], Any]
"""`pykrx.stock` 모듈을 반환하는 팩토리. 테스트는 `lambda: MagicMock()` 주입."""

ClockFn = Callable[[], datetime]
"""현재 시각 제공자. 테스트 결정론화를 위해 주입 가능."""

_DEFAULT_DB_PATH = Path("data/stock_agent.db")
_KOSPI200_INDEX_CODE = "1028"
_SYMBOL_RE = re.compile(r"^\d{6}$")
_SCHEMA_VERSION = 2

_CREATE_DAILY_BARS_SQL = """
    CREATE TABLE IF NOT EXISTS daily_bars (
        symbol      TEXT NOT NULL,
        trade_date  TEXT NOT NULL,
        open        TEXT NOT NULL,
        high        TEXT NOT NULL,
        low         TEXT NOT NULL,
        close       TEXT NOT NULL,
        volume      INTEGER NOT NULL,
        PRIMARY KEY (symbol, trade_date)
    )
"""

_CREATE_CONSTITUENTS_SQL = """
    CREATE TABLE IF NOT EXISTS kospi200_constituents (
        as_of_date  TEXT NOT NULL,
        symbol      TEXT NOT NULL,
        PRIMARY KEY (as_of_date, symbol)
    )
"""

_CREATE_SCHEMA_VERSION_SQL = """
    CREATE TABLE IF NOT EXISTS schema_version (
        version     INTEGER PRIMARY KEY
    )
"""


class HistoricalDataError(Exception):
    """과거 데이터 수집/캐시 실패를 공통 표현.

    pykrx 의 구체 예외 타입이 상위 레이어로 누출되지 않도록 래핑한다.
    원본 예외는 `__cause__` 로 보존된다 (`raise ... from e`).
    """


@dataclass(frozen=True, slots=True)
class DailyBar:
    """일봉 1건. pykrx DataFrame 행을 DTO 로 정규화한 형태.

    거래대금(`value`) 은 포함하지 않는다: pykrx `get_market_ohlcv` 는 단일 종목
    조회에서 거래대금을 돌려주지 않으므로 값을 0 으로 채우면 "데이터 없음" 과
    "실제 0" 을 구별할 수 없게 된다. 유동성 필터가 필요할 때는 별도 전시장
    스냅샷 API 를 추가한다.
    """

    symbol: str
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


class HistoricalDataStore:
    """pykrx 일봉 + KOSPI 200 구성종목 조회를 SQLite 로 캐시한다.

    공개 API: `get_kospi200_constituents`, `fetch_daily_ohlcv`, `close` + 컨텍스트 매니저.
    단일 프로세스 전용 (스레드/프로세스 safe 미제공).
    """

    def __init__(
        self,
        *,
        pykrx_factory: PykrxFactory | None = None,
        db_path: str | Path = _DEFAULT_DB_PATH,
        clock: ClockFn | None = None,
    ) -> None:
        """
        Args:
            pykrx_factory: `pykrx.stock` 모듈을 반환하는 팩토리. 테스트에서
                `lambda: MagicMock()` 을 주입해 네트워크·import 를 차단한다.
                `None` 이면 실제 `pykrx.stock` 을 지연 import.
            db_path: SQLite 파일 경로. `":memory:"` 도 허용 (테스트용).
                파일 경로이면 부모 디렉토리를 자동 생성한다.
            clock: "오늘" 판정용 시각 제공자. `None` 이면 `datetime.now()` 사용.
        """
        self._pykrx_factory = pykrx_factory
        self._clock: ClockFn = clock or datetime.now
        self._closed = False
        self._db_path = db_path
        self._pykrx: Any | None = None  # 지연 초기화
        self._conn = self._open_connection(db_path)
        self._init_schema()

    @staticmethod
    def _open_connection(db_path: str | Path) -> sqlite3.Connection:
        if isinstance(db_path, Path) or (isinstance(db_path, str) and db_path != ":memory:"):
            path = Path(db_path)
            if path.parent and str(path.parent) not in ("", "."):
                path.parent.mkdir(parents=True, exist_ok=True)
            return sqlite3.connect(str(path), isolation_level=None)
        # ":memory:"
        return sqlite3.connect(db_path, isolation_level=None)

    def _init_schema(self) -> None:
        """스키마를 최신 버전(`_SCHEMA_VERSION`) 으로 맞춘다.

        - 빈 DB: 테이블 새로 생성 + 버전 기록.
        - v1 (daily_bars 에 `value` 컬럼 존재): `daily_bars` DROP 후 v2 로 재생성.
          캐시 레코드는 유실되지만 `.gitignore` 된 로컬 캐시이므로 영향 적음.
        - v2 이상: no-op.
        """
        cur = self._conn.cursor()
        try:
            cur.execute(_CREATE_SCHEMA_VERSION_SQL)
            cur.execute(_CREATE_CONSTITUENTS_SQL)

            row = cur.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current = row[0] if row and row[0] is not None else 0

            if current == 0:
                cur.execute(_CREATE_DAILY_BARS_SQL)
            elif current < _SCHEMA_VERSION:
                logger.info(
                    f"historical 스키마 v{current} → v{_SCHEMA_VERSION} 마이그레이션 "
                    "(daily_bars 재생성)"
                )
                cur.execute("DROP TABLE IF EXISTS daily_bars")
                cur.execute(_CREATE_DAILY_BARS_SQL)
            # current >= _SCHEMA_VERSION 인 경우: 최신이거나 미래 버전(다운그레이드 금지)
            else:
                cur.execute(_CREATE_DAILY_BARS_SQL)

            cur.execute(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                (_SCHEMA_VERSION,),
            )
        finally:
            cur.close()

    # ---- 내부 헬퍼 ------------------------------------------------------

    def _get_pykrx(self) -> Any:
        if self._pykrx is not None:
            return self._pykrx
        if self._pykrx_factory is None:
            from pykrx import stock as _stock  # noqa: PLC0415

            self._pykrx = _stock
        else:
            self._pykrx = self._pykrx_factory()
        return self._pykrx

    def _require_open(self) -> None:
        if self._closed:
            raise HistoricalDataError(
                "HistoricalDataStore 는 이미 close() 되었습니다. 새 인스턴스를 생성하세요."
            )

    def _call(self, label: str, fn: Callable[[], Any]) -> Any:
        """공개 메서드 공통 에러 래핑 헬퍼.

        `RuntimeError` 는 전파, 그 외 `Exception` 은 `HistoricalDataError` 로 래핑.
        """
        self._require_open()
        try:
            return fn()
        except RuntimeError:
            raise
        except HistoricalDataError:
            raise
        except Exception as e:
            logger.exception(f"{label} 실패: {e.__class__.__name__}: {e}")
            raise HistoricalDataError(f"{label} 실패: {e.__class__.__name__}: {e}") from e

    def _today(self) -> date:
        return self._clock().date()

    # ---- 공개 API: KOSPI 200 구성종목 ----------------------------------

    def get_kospi200_constituents(self, as_of: date | None = None) -> list[str]:
        """KOSPI 200 구성종목 티커 리스트를 반환한다.

        - `as_of=None` → 오늘 날짜(`clock` 기준) 사용.
        - 동일 `as_of` 에 대해 두 번째 호출부터는 pykrx 재호출 없이 DB 캐시에서 반환.
        - 반환 순서는 티커 오름차순으로 안정화한다.
        """
        target = as_of or self._today()
        return self._call(
            "KOSPI 200 구성종목 조회",
            lambda: self._load_or_fetch_constituents(target),
        )

    def _load_or_fetch_constituents(self, target: date) -> list[str]:
        cached = self._select_constituents(target)
        if cached:
            logger.info(
                f"KOSPI 200 구성종목 캐시 적중 — as_of={target.isoformat()}, n={len(cached)}"
            )
            return cached

        tickers = self._fetch_constituents_from_pykrx(target)
        self._insert_constituents(target, tickers)
        logger.info(
            f"KOSPI 200 구성종목 캐시 미스 — pykrx 조회 후 저장 "
            f"(as_of={target.isoformat()}, n={len(tickers)})"
        )
        return tickers

    def _select_constituents(self, target: date) -> list[str]:
        cur = self._conn.cursor()
        try:
            rows = cur.execute(
                "SELECT symbol FROM kospi200_constituents WHERE as_of_date = ? ORDER BY symbol ASC",
                (target.isoformat(),),
            ).fetchall()
        finally:
            cur.close()
        return [r[0] for r in rows]

    def _fetch_constituents_from_pykrx(self, target: date) -> list[str]:
        stock = self._get_pykrx()
        raw = stock.get_index_portfolio_deposit_file(
            target.strftime("%Y%m%d"),
            _KOSPI200_INDEX_CODE,
        )
        if raw is None:
            raise HistoricalDataError(
                "pykrx 가 KOSPI 200 구성종목으로 None 을 반환 — 데이터 소스 이상. "
                f"as_of={target.isoformat()}"
            )
        tickers = sorted({str(t) for t in raw if str(t)})
        return tickers

    def _insert_constituents(self, target: date, tickers: list[str]) -> None:
        if not tickers:
            return
        rows = [(target.isoformat(), t) for t in tickers]
        cur = self._conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            try:
                cur.executemany(
                    "INSERT OR REPLACE INTO kospi200_constituents (as_of_date, symbol) "
                    "VALUES (?, ?)",
                    rows,
                )
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
        finally:
            cur.close()

    # ---- 공개 API: 일봉 OHLCV -------------------------------------------

    def fetch_daily_ohlcv(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> list[DailyBar]:
        """`symbol` 의 `start`~`end` 구간 일봉을 `DailyBar` 리스트로 반환.

        - 과거 구간(end < today) 이면서 DB 에 `(symbol, end)` 행이 있으면 pykrx 재호출 없이
          구간 내 DB 행만 반환한다.
        - end 가 오늘(T) 이거나 DB 에 해당 end 행이 없으면 pykrx 로 전체 구간을 재조회 후
          `INSERT OR REPLACE` 로 캐시를 갱신한다.
        - pykrx 가 빈 결과를 돌려주면 빈 리스트를 반환 (휴장/신규상장 등).
        """
        self._require_open()
        self._validate_symbol(symbol)
        if start > end:
            raise HistoricalDataError(
                f"start({start.isoformat()}) > end({end.isoformat()}) — 구간이 역전되었습니다."
            )

        return self._call(
            f"일봉 OHLCV 조회 ({symbol} {start.isoformat()}~{end.isoformat()})",
            lambda: self._load_or_fetch_daily(symbol, start, end),
        )

    @staticmethod
    def _validate_symbol(symbol: str) -> None:
        if not symbol or not _SYMBOL_RE.match(symbol):
            raise HistoricalDataError(f"symbol 은 6자리 숫자 문자열이어야 합니다 (got={symbol!r})")

    def _load_or_fetch_daily(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> list[DailyBar]:
        today = self._today()
        if end < today and self._has_daily_row(symbol, end):
            bars = self._select_daily(symbol, start, end)
            logger.info(
                f"일봉 캐시 적중 — symbol={symbol}, "
                f"range={start.isoformat()}~{end.isoformat()}, n={len(bars)}"
            )
            return bars

        bars = self._fetch_daily_from_pykrx(symbol, start, end)
        if bars:
            self._insert_daily(bars)
        logger.info(
            f"일봉 캐시 미스 — pykrx 조회 후 저장 (symbol={symbol}, "
            f"range={start.isoformat()}~{end.isoformat()}, n={len(bars)})"
        )
        # 캐시 미스 경로에서도 저장 후 DB 기반 반환으로 일관성 유지 (휴장일 등 제외).
        return self._select_daily(symbol, start, end) if bars else []

    def _has_daily_row(self, symbol: str, trade_date: date) -> bool:
        cur = self._conn.cursor()
        try:
            row = cur.execute(
                "SELECT 1 FROM daily_bars WHERE symbol = ? AND trade_date = ? LIMIT 1",
                (symbol, trade_date.isoformat()),
            ).fetchone()
        finally:
            cur.close()
        return row is not None

    def _select_daily(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        cur = self._conn.cursor()
        try:
            rows = cur.execute(
                "SELECT symbol, trade_date, open, high, low, close, volume "
                "FROM daily_bars "
                "WHERE symbol = ? AND trade_date BETWEEN ? AND ? "
                "ORDER BY trade_date ASC",
                (symbol, start.isoformat(), end.isoformat()),
            ).fetchall()
        finally:
            cur.close()
        return [_row_to_bar(r) for r in rows]

    def _fetch_daily_from_pykrx(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> list[DailyBar]:
        stock = self._get_pykrx()
        df = stock.get_market_ohlcv(
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            symbol,
        )
        if df is None:
            raise HistoricalDataError(
                "pykrx 가 일봉으로 None 을 반환 — 데이터 소스 이상. "
                f"symbol={symbol}, range={start.isoformat()}~{end.isoformat()}"
            )
        if getattr(df, "empty", False) or len(df) == 0:
            return []
        return [_df_row_to_bar(symbol, idx, row) for idx, row in df.iterrows()]

    def _insert_daily(self, bars: list[DailyBar]) -> None:
        rows = [
            (
                b.symbol,
                b.trade_date.isoformat(),
                str(b.open),
                str(b.high),
                str(b.low),
                str(b.close),
                b.volume,
            )
            for b in bars
        ]
        cur = self._conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            try:
                cur.executemany(
                    "INSERT OR REPLACE INTO daily_bars "
                    "(symbol, trade_date, open, high, low, close, volume) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
        finally:
            cur.close()

    # ---- 수명 주기 ------------------------------------------------------

    def close(self) -> None:
        """SQLite 커넥션을 정리한다. 멱등."""
        if self._closed:
            return
        self._closed = True
        try:
            self._conn.close()
        except Exception as e:  # noqa: BLE001 — close 실패는 부수 정보로만 기록
            logger.warning(f"SQLite close 중 예외 발생 (무시): {e!r}")

    def __enter__(self) -> HistoricalDataStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


# ---- 내부 변환기 --------------------------------------------------------


def _row_to_bar(row: tuple[Any, ...]) -> DailyBar:
    symbol, trade_date, open_, high, low, close, volume = row
    return DailyBar(
        symbol=str(symbol),
        trade_date=date.fromisoformat(str(trade_date)),
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=int(volume),
    )


def _df_row_to_bar(symbol: str, idx: Any, row: Any) -> DailyBar:
    """pykrx DataFrame row → DailyBar.

    pykrx `get_market_ohlcv` 는 단일 종목 조회에서 한국어 컬럼
    ("시가"/"고가"/"저가"/"종가"/"거래량") 을 반환한다. 인덱스는 `pandas.Timestamp`.
    거래대금은 단일 종목 모드에서 미제공이라 수집하지 않는다.
    """
    trade_date = _coerce_date(idx)
    try:
        open_ = Decimal(str(row["시가"]))
        high = Decimal(str(row["고가"]))
        low = Decimal(str(row["저가"]))
        close = Decimal(str(row["종가"]))
        volume = int(row["거래량"])
    except KeyError as e:
        raise HistoricalDataError(
            f"pykrx 일봉 컬럼 접근 실패: {e!r} — 라이브러리 스키마 변동 가능성. symbol={symbol}"
        ) from e
    return DailyBar(
        symbol=symbol,
        trade_date=trade_date,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _coerce_date(idx: Any) -> date:
    """pandas.Timestamp 또는 datetime/date/str 을 `date` 로 정규화."""
    if isinstance(idx, date) and not isinstance(idx, datetime):
        return idx
    if isinstance(idx, datetime):
        return idx.date()
    to_pydatetime = getattr(idx, "to_pydatetime", None)
    if callable(to_pydatetime):
        dt = to_pydatetime()
        if isinstance(dt, datetime):
            return dt.date()
    date_attr = getattr(idx, "date", None)
    if callable(date_attr):
        d = date_attr()
        if isinstance(d, date):
            return d
    if isinstance(idx, str):
        return date.fromisoformat(idx[:10]) if "-" in idx[:10] else _parse_yyyymmdd(idx)
    raise HistoricalDataError(f"일봉 인덱스를 date 로 변환 실패: {idx!r}")


def _parse_yyyymmdd(s: str) -> date:
    if len(s) < 8:
        raise HistoricalDataError(f"날짜 문자열 포맷 이상: {s!r}")
    return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
