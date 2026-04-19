"""pykrx 기반 KOSPI 개별 종목 과거 일봉 수집 + SQLite 캐시.

책임 범위
- 개별 종목의 일봉 OHLCV 조회 (start~end)
- 결과를 단일 SQLite 파일 (기본: `data/stock_agent.db`) 에 캐시

범위 제외 (의도적)
- KOSPI 200 구성종목 조회: `data/universe.py` 가 YAML(`config/universe.yaml`) 에서
  로드한다. pykrx 지수 API 는 KRX 서버 변경으로 호환이 깨졌고 KIS Developers 는
  해당 API 를 제공하지 않아, 수동 관리 YAML 이 유일한 실용 경로.
- 분봉/틱 데이터: pykrx 가 공식 미지원. `data/realtime.py` 가 장중 분봉 폴링으로 수집.
- KIS Developers 현재가 조회: `broker/kis_client.py` 범위.
- 백테스트 엔진: Phase 2 `backtest/engine.py`.

에러 정책 (broker/kis_client 와 동일 기조)
- `RuntimeError` 는 래핑하지 않고 그대로 전파 (설정 오류 — 재시도 대상 아님).
- 그 외 `Exception` 은 `HistoricalDataError` 로 래핑 + loguru `exception` 로그.
- 사전 가드: `symbol` 형식(6자리 숫자), `start <= end` 는 pykrx 호출 전에 거부.

캐시 정책 (v3, 단순)
- `daily_bars` 테이블: `(symbol, trade_date)` PRIMARY KEY. OHLC + 거래량.
  (v1 에 있던 `value`(거래대금) 컬럼은 제거. pykrx `get_market_ohlcv` 는 단일 종목
  모드에서 거래대금을 반환하지 않아 조용한 0 이 섞이는 무결성 위험이 있었다.
  유동성 필터가 필요하면 추후 전시장 스냅샷 메서드를 별도 추가.)
- 재호출 판정: "요청 end 날짜가 DB 에 존재" + "end < today" 이면 캐시 적중.
  당일(T) 데이터는 장 종료 여부를 확정할 수 없어 항상 재조회한다.
- 마이그레이션
  - v1 → v3: `daily_bars` DROP+재생성, `kospi200_constituents` DROP.
  - v2 → v3: `kospi200_constituents` DROP (daily_bars 는 유지).
  - `kospi200_constituents` 테이블은 v3 에서 완전히 제거되었다 (유니버스 조회 책임이
    `data/universe.py` 로 이전됨).
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
_SYMBOL_RE = re.compile(r"^\d{6}$")
_SCHEMA_VERSION = 3

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
    """pykrx 개별 종목 일봉 조회를 SQLite 로 캐시한다.

    공개 API: `fetch_daily_ohlcv`, `close` + 컨텍스트 매니저.
    단일 프로세스 전용 (스레드/프로세스 safe 미제공).

    KOSPI 200 구성종목 조회는 이 클래스의 책임이 아니다. 유니버스는
    `stock_agent.data.universe.load_kospi200_universe()` 로 YAML 에서 로드한다.
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

        분기
        - 빈 DB: `daily_bars` 생성 + 버전 기록.
        - v1 → v3: `daily_bars` DROP+재생성(`value` 컬럼 제거), `kospi200_constituents` DROP.
        - v2 → v3: `kospi200_constituents` DROP. `daily_bars` 는 스키마 동일하므로 DDL
          은 `CREATE ... IF NOT EXISTS` 로 사실상 no-op 이고 기존 행이 보존된다.
        - v3 이상: 테이블 확인용 DDL(IF NOT EXISTS) 만 실행, 상태 변경 없음.

        트랜잭션
        - `BEGIN IMMEDIATE` 로 감싸 DROP + CREATE 사이 실패 시 "스키마 찢김" 을 방지.
        - SQLite 는 DDL 을 트랜잭션 안에 포함할 수 있으므로 atomicity 가 유지된다.
        - 실패 시 ROLLBACK 후 `HistoricalDataError` 로 래핑해 상위가 `HistoricalDataError`
          단일 계층으로 "데이터 계층 실패" 를 잡을 수 있게 한다.

        `.gitignore` 된 로컬 캐시라 v1→v3 재생성 시 레코드 유실 영향은 작다.
        """
        cur = self._conn.cursor()
        try:
            cur.execute(_CREATE_SCHEMA_VERSION_SQL)
            row = cur.execute("SELECT MAX(version) FROM schema_version").fetchone()
            current = row[0] if row and row[0] is not None else 0

            cur.execute("BEGIN IMMEDIATE")
            try:
                if current == 0:
                    cur.execute(_CREATE_DAILY_BARS_SQL)
                elif current < _SCHEMA_VERSION:
                    logger.info(f"historical 스키마 v{current} → v{_SCHEMA_VERSION} 마이그레이션")
                    if current < 2:
                        # v1 의 daily_bars 는 `value` 컬럼을 포함하므로 재생성 필요.
                        cur.execute("DROP TABLE IF EXISTS daily_bars")
                    cur.execute(_CREATE_DAILY_BARS_SQL)
                    # v3 에서 `kospi200_constituents` 테이블은 완전 제거.
                    cur.execute("DROP TABLE IF EXISTS kospi200_constituents")
                else:
                    # current >= _SCHEMA_VERSION: 최신 또는 미래 버전 — 다운그레이드 금지.
                    # IF NOT EXISTS 로 사실상 상태 변경 없음(미존재 시에만 생성).
                    cur.execute(_CREATE_DAILY_BARS_SQL)

                cur.execute(
                    "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                    (_SCHEMA_VERSION,),
                )
                cur.execute("COMMIT")
            except Exception as e:
                cur.execute("ROLLBACK")
                logger.exception(
                    f"historical 스키마 초기화 실패 — 롤백 "
                    f"(current={current}, target={_SCHEMA_VERSION}): "
                    f"{e.__class__.__name__}: {e}"
                )
                raise HistoricalDataError(
                    f"HistoricalDataStore 스키마 초기화 실패 "
                    f"(current=v{current}, target=v{_SCHEMA_VERSION}): "
                    f"{e.__class__.__name__}: {e}"
                ) from e
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
