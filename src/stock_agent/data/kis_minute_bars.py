"""KIS 과거 분봉 API 어댑터 — `BarLoader` Protocol 구현.

책임 범위
- KIS Developers 주식일별분봉조회(`/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice`,
  TR `FHKST03010230`) 를 실전(live) 키로 호출해 심볼·날짜 범위의 과거 분봉을
  `(bar_time, symbol)` 정렬 스트림으로 공급.
- 120 건 역방향 커서 페이지네이션 + 날짜 단위 SQLite 캐시로 재호출 시 KIS 재요청을
  최소화.
- EGW00201 레이트 리밋 응답은 `rate_limit_wait_s` 대기 후 최대 `rate_limit_max_retries`
  회 재시도.

책임 범위 밖 (의도적 defer)
- 대량 백필 스크립트 (별도 PR).
- 2~3년 실데이터 수집 — **KIS 서버는 분봉을 최대 1년 보관**. 장기 데이터는
  외부 소스 필요 (Issue #5 에 코멘트 별도).
- WebSocket 실시간 분봉 — `data/realtime.py` 가 담당.
- 공식 python-kis API 래핑 — python-kis 2.1.6 은 `day_chart(...)` 로 **당일** 만
  지원하고 과거 일자 분봉 API 를 래핑하지 않아 `kis.fetch()` 로우레벨을 직접 쓴다.

설계 결정 (ADR-0016)
- 캐시 저장소는 별도 파일 `data/minute_bars.db` (기본값) — `data/stock_agent.db`
  (일봉 캐시) · `data/trading.db` (원장) 과 대칭. 스키마 v4 전환 위험 0, 파일 삭제만으로
  캐시 초기화 가능.
- 가격은 `TEXT` 로 저장해 `Decimal` 정밀도 보존 (`historical.py` · `storage/db.py` 와
  동일 기조).
- `bar_time` 계약: **KST aware ISO8601** (`"2026-04-22T09:31:00+09:00"`),
  `second=0, microsecond=0` 강제 (분 경계). `_parse_row` 가 KST 부여 + 초/마이크로초
  절삭을 수행하므로 이 모듈이 직접 쓴 값은 계약을 지킨다. `_date_cached` 의
  `BETWEEN '...T00:00:00+09:00' AND '...T23:59:59+09:00'` 쿼리는 이 계약에
  의존 — 외부 도구가 같은 테이블에 다른 tz 문자열로 쓰면 캐시 판정이 어긋난다.
- 캐시 hit 판정은 `(symbol, 날짜)` 단위. 해당 날짜 bar 가 한 건이라도 있으면
  API 재호출 생략. `date == clock().date()` 인 "오늘" 자는 항상 재조회 (**쓰기·읽기
  모두 skip** — 이전 실행에서 어떤 경로로든 오늘 자 행이 DB 에 있어도 장중
  미확정 데이터가 사용되지 않도록 분기 자체를 분리한다). 실시간 분봉은
  `realtime.py` 경로와 별개로 장중에는 미확정.

동시성 (ADR-0008 단일 프로세스 전용)
- 이 어댑터는 **단일 스레드 전용**. `sqlite3.Connection` 은 `check_same_thread=True`
  기본값을 사용하므로 다른 스레드에서 `stream`/`_fetch_day`/`_write_bars_to_db`/
  `_read_day_from_db`/`_date_cached` 를 호출하면 `sqlite3.ProgrammingError` 로 폭파한다.
- `_lock` 은 `_ensure_kis` 의 지연 초기화 race 만 보호한다 — 단일 스레드 전제
  하에서는 DB 호출을 전역 락으로 감쌀 필요가 없다. 백테스트 엔진 병렬화
  요구가 생기면 별도 ADR 로 전환 재평가.

에러 정책 (`historical.py` / `minute_csv.py` 와 동일 기조)
- `RuntimeError` 는 전파 — 호출자 계약 위반 (`start > end`, 빈 `symbols`,
  심볼 포맷, 생성자 파라미터 범위).
- `has_live_keys == False` · SQLite 초기화 실패는 생성자에서 `KisMinuteBarLoadError` fail-fast.
- KIS 응답 에러 (`rt_cd != "0"`) 는 `KisMinuteBarLoadError` (EGW00201 한해 재시도).
- 그 외 `Exception` (네트워크·파싱·SQLite) 은 `KisMinuteBarLoadError` 로 래핑 +
  `__cause__` 보존 + `loguru.error`.
- 개별 행 파싱 오류는 `logger.warning` 후 skip (한 행 오류로 날짜 전체를 잃지 않도록).

안전 가드
- 실전 키 PyKis 인스턴스 생성 직후 `install_order_block_guard` 설치 —
  `/trading/order*` 경로 도메인 무관 차단 (`realtime.py` 와 동일).

로그 포맷 계약
- 행·페이지 단위 파싱 경보 메시지의 `kind=<value>` 토큰은 **운영 grep 계약**.
  카테고리 이름(`_ParseFailureKind` Literal 5종) 은 고정이며, 변경 시 운영
  grep·대시보드·알림 규칙이 함께 깨진다 — `_ParseSkipError.kind` ↔
  warning 메시지 ↔ `parse_skip_counts` 요약 warning 이 동일 문자열을 공유.
"""

from __future__ import annotations

import contextlib
import heapq
import re
import sqlite3
import threading
import time as _time_mod
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from loguru import logger

from stock_agent.config import Settings
from stock_agent.data.realtime import MinuteBar
from stock_agent.safety import install_order_block_guard

PyKisFactory = Callable[..., Any]
"""`PyKis` 생성자와 호환되는 팩토리 타입. 테스트는 `MagicMock` 반환 팩토리 주입."""

ClockFn = Callable[[], datetime]
"""현재 시각 제공자. 테스트 결정론화를 위해 주입. KST aware datetime 기대."""

SleepFn = Callable[[float], None]
"""슬립 함수. `throttle_s` 및 레이트 리밋 대기에 공통 사용. 테스트는 `MagicMock` 주입."""

KST = timezone(timedelta(hours=9))
_SYMBOL_RE = re.compile(r"^\d{6}$")
_API_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
_TR_ID = "FHKST03010230"
_RATE_LIMIT_MSG_CD = "EGW00201"
_DEFAULT_CACHE_DB_PATH = Path("data/minute_bars.db")
_SCHEMA_VERSION = 1
_PAGE_SIZE = 120
_SESSION_OPEN_HHMMSS = "090000"
_SESSION_CLOSE_HHMMSS = "153000"


class KisMinuteBarLoadError(Exception):
    """KIS 과거 분봉 로드 실패 공통 예외. 원본 예외는 `__cause__` 로 보존."""


_ParseFailureKind = Literal[
    "missing_date_or_time",
    "date_mismatch",
    "invalid_price",
    "invalid_volume",
    "malformed_bar_time",
]


class _ParseSkipError(Exception):
    """`_parse_row` 가 한 행을 skip 할 때 내부 신호로 사용하는 예외.

    Issue #52 회귀 — 원인 카테고리(`kind`) 와 row key 목록(`keys`) 을 담아
    날짜 단위 페치 루프가 `(symbol, day, kind)` 단위 dedupe 후 1회만 warning 을
    방출하도록 한다. 전체 row repr 은 포함하지 않아 로그 용량·가격 유출을 방지한다.
    """

    __slots__ = ("kind", "keys")

    def __init__(self, kind: _ParseFailureKind, keys: tuple[str, ...]) -> None:
        super().__init__(kind)
        self.kind: _ParseFailureKind = kind
        self.keys: tuple[str, ...] = keys


class KisMinuteBarLoader:
    """KIS 과거 분봉 `BarLoader` 어댑터.

    `BarLoader` Protocol (`backtest/loader.py`) 의 `stream(start, end, symbols)` 계약을
    만족하며, 내부적으로 KIS API 를 실전 키로 호출해 SQLite 캐시에 적층한다.

    공개 API
        `stream(start, end, symbols) -> Iterator[MinuteBar]`
        `close()` (멱등) · 컨텍스트 매니저
        `cache_db_path` 프로퍼티

    재호출 안전성: 같은 `(start, end, symbols)` 로 `stream` 을 여러 번 호출하면 매번
    새 이터레이터를 반환한다 (`minute_csv.py` 와 동일 계약).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        pykis_factory: PyKisFactory | None = None,
        clock: ClockFn | None = None,
        cache_db_path: Path | None = None,
        throttle_s: float = 0.0,
        sleep: SleepFn | None = None,
        rate_limit_wait_s: float = 61.0,
        rate_limit_max_retries: int = 3,
    ) -> None:
        """
        Args:
            settings: `.env` 에서 로드된 설정. `has_live_keys == True` 필수.
            pykis_factory: `PyKis` 호환 팩토리. `None` 이면 지연 import.
            clock: KST aware datetime 반환자. `None` 이면 `datetime.now(KST)`.
            cache_db_path: SQLite 파일 경로. `None` 이면 `data/minute_bars.db`.
                `":memory:"` Path 도 허용 (단, `Path(":memory:")` 로 감싸야 함).
            throttle_s: 페이지 호출 사이 추가 sleep 초. 기본 0. 음수 → `RuntimeError`.
            sleep: 슬립 함수. `None` 이면 `time.sleep`. 테스트는 `MagicMock` 주입.
            rate_limit_wait_s: EGW00201 수신 시 대기 시간. 기본 61.
            rate_limit_max_retries: EGW00201 재시도 최대 횟수. 기본 3 (총 4회 호출).

        Raises:
            RuntimeError: `throttle_s < 0` · `rate_limit_wait_s < 0` ·
                `rate_limit_max_retries < 1`.
            KisMinuteBarLoadError: `settings.has_live_keys == False` · SQLite 초기화 실패.
        """
        if throttle_s < 0:
            raise RuntimeError(f"throttle_s 는 0 이상이어야 합니다 (got={throttle_s})")
        if rate_limit_wait_s < 0:
            raise RuntimeError(
                f"rate_limit_wait_s 는 0 이상이어야 합니다 (got={rate_limit_wait_s})"
            )
        if rate_limit_max_retries < 1:
            raise RuntimeError(
                f"rate_limit_max_retries 는 1 이상이어야 합니다 (got={rate_limit_max_retries})"
            )

        if not settings.has_live_keys:
            raise KisMinuteBarLoadError(
                "KisMinuteBarLoader 는 실전 APP_KEY 3종(KIS_LIVE_APP_KEY · "
                "KIS_LIVE_APP_SECRET · KIS_LIVE_ACCOUNT_NO) 이 필요합니다. "
                "paper 도메인에는 시세 API(`/quotations/*`) 가 없어 실전 도메인을 "
                "호출해야 하며, paper 키로는 real 도메인 인증이 거부됩니다 (EGW02004)."
            )

        self._settings = settings
        self._pykis_factory = pykis_factory
        self._clock: ClockFn = clock or (lambda: datetime.now(KST))
        self._throttle_s = throttle_s
        self._sleep: SleepFn = sleep or _time_mod.sleep
        self._rate_limit_wait_s = rate_limit_wait_s
        self._rate_limit_max_retries = rate_limit_max_retries

        self._cache_db_path = cache_db_path if cache_db_path is not None else _DEFAULT_CACHE_DB_PATH
        self._conn: sqlite3.Connection | None = None
        self._kis: Any | None = None
        self._closed = False
        self._lock = threading.Lock()

        try:
            self._init_db()
        except (OSError, sqlite3.Error) as exc:
            raise KisMinuteBarLoadError(f"SQLite 캐시 초기화 실패: {self._cache_db_path}") from exc

    # ---- 공개 API ------------------------------------------------------

    @property
    def cache_db_path(self) -> Path:
        """캐시 DB 경로. 테스트·디버깅용."""
        return self._cache_db_path

    def stream(
        self,
        start: date,
        end: date,
        symbols: tuple[str, ...],
    ) -> Iterator[MinuteBar]:
        """지정 구간·심볼의 분봉을 `(bar_time, symbol)` 순으로 yield.

        Args:
            start: 포함 시작 날짜 (KST trade_date).
            end: 포함 종료 날짜.
            symbols: 6자리 숫자 심볼 튜플 (1개 이상).

        Returns:
            `MinuteBar` 를 yield 하는 이터레이터. 날짜별 캐시 판정 후 API 호출.

        Raises:
            RuntimeError: `start > end` · 빈 `symbols` · 심볼 포맷 위반 · 이미 close.
            KisMinuteBarLoadError: KIS API 에러 · 네트워크 · SQLite 실패.
        """
        if self._closed:
            raise RuntimeError("KisMinuteBarLoader 는 이미 close() 되었습니다.")
        if start > end:
            raise RuntimeError(f"start({start}) 는 end({end}) 이전이어야 합니다.")
        if not symbols:
            raise RuntimeError("symbols 는 1개 이상이어야 합니다.")
        for symbol in symbols:
            if not _SYMBOL_RE.match(symbol):
                raise RuntimeError(f"symbol 은 6자리 숫자여야 합니다: {symbol!r}")

        per_symbol_bars = [
            iter(self._collect_symbol_bars(symbol, start, end)) for symbol in symbols
        ]
        return heapq.merge(*per_symbol_bars, key=lambda b: (b.bar_time, b.symbol))

    def close(self) -> None:
        """SQLite 연결·PyKis 리소스를 정리. 멱등."""
        if self._closed:
            return
        self._closed = True
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error as exc:
                logger.warning(f"SQLite close 중 예외 (무시): {exc!r}")
            self._conn = None

    def __enter__(self) -> KisMinuteBarLoader:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # ---- 내부: SQLite 초기화 -------------------------------------------

    def _init_db(self) -> None:
        """스키마 v1 로 DB 초기화. 테이블이 이미 있으면 그대로 둠."""
        path_str = str(self._cache_db_path)
        if path_str == ":memory:":
            self._conn = sqlite3.connect(":memory:", isolation_level=None)
        else:
            self._cache_db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(path_str, isolation_level=None)
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS minute_bars (
                    symbol TEXT NOT NULL,
                    bar_time TEXT NOT NULL,
                    open TEXT NOT NULL,
                    high TEXT NOT NULL,
                    low TEXT NOT NULL,
                    close TEXT NOT NULL,
                    volume INTEGER NOT NULL,
                    PRIMARY KEY (symbol, bar_time)
                )
                """)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
                (_SCHEMA_VERSION,),
            )
            self._conn.execute("COMMIT")
        except sqlite3.Error:
            self._conn.execute("ROLLBACK")
            raise

    # ---- 내부: 심볼별 수집 ---------------------------------------------

    def _collect_symbol_bars(
        self,
        symbol: str,
        start: date,
        end: date,
    ) -> list[MinuteBar]:
        """한 심볼의 `[start, end]` 구간 bar 를 수집. 시간 오름차순 반환.

        날짜는 `end → start` 역방향 순회. 각 날짜마다:
        - `date == today` → 항상 API 재호출. DB 쓰기 **와 읽기 모두 skip**
          (stale bar 방어 — 이전 실행에서 어떤 경로로든 오늘 자 행이 이미
          DB 에 있어도 장중 미확정 데이터가 사용되지 않도록 분기 자체를 분리).
        - DB 에 해당 날짜 bar 존재 → DB 재사용.
        - 그 외 → API 호출 + DB 쓰기.
        """
        today = self._clock().date()
        collected: list[MinuteBar] = []

        current = end
        while current >= start:
            if current == today:
                day_bars = self._fetch_day(symbol, current)
                collected.extend(day_bars)
            elif self._date_cached(symbol, current):
                collected.extend(self._read_day_from_db(symbol, current))
            else:
                day_bars = self._fetch_day(symbol, current)
                self._write_bars_to_db(day_bars)
                collected.extend(day_bars)
            current -= timedelta(days=1)

        # 날짜별 수집이라 bar_time 오름차순으로 재정렬.
        collected.sort(key=lambda b: b.bar_time)
        return collected

    def _date_cached(self, symbol: str, day: date) -> bool:
        assert self._conn is not None
        start_key = f"{day.isoformat()}T00:00:00+09:00"
        end_key = f"{day.isoformat()}T23:59:59+09:00"
        row = self._conn.execute(
            "SELECT 1 FROM minute_bars WHERE symbol=? AND bar_time BETWEEN ? AND ? LIMIT 1",
            (symbol, start_key, end_key),
        ).fetchone()
        return row is not None

    def _read_day_from_db(self, symbol: str, day: date) -> list[MinuteBar]:
        assert self._conn is not None
        start_key = f"{day.isoformat()}T00:00:00+09:00"
        end_key = f"{day.isoformat()}T23:59:59+09:00"
        cursor = self._conn.execute(
            "SELECT bar_time, open, high, low, close, volume FROM minute_bars "
            "WHERE symbol=? AND bar_time BETWEEN ? AND ? ORDER BY bar_time ASC",
            (symbol, start_key, end_key),
        )
        bars: list[MinuteBar] = []
        for bar_time_s, open_s, high_s, low_s, close_s, volume_i in cursor:
            bars.append(
                MinuteBar(
                    symbol=symbol,
                    bar_time=datetime.fromisoformat(bar_time_s),
                    open=Decimal(open_s),
                    high=Decimal(high_s),
                    low=Decimal(low_s),
                    close=Decimal(close_s),
                    volume=int(volume_i),
                )
            )
        return bars

    def _write_bars_to_db(self, bars: list[MinuteBar]) -> None:
        if not bars:
            return
        assert self._conn is not None
        rows = [
            (
                b.symbol,
                b.bar_time.isoformat(),
                str(b.open),
                str(b.high),
                str(b.low),
                str(b.close),
                b.volume,
            )
            for b in bars
        ]
        # H4: 명시적 트랜잭션으로 감싸 "부분 실패 + _date_cached 오인" 방지.
        # autocommit 모드라도 executemany 중간에 실패하면 먼저 삽입된 row 가
        # 커밋되어 `_date_cached` 가 True 를 반환 → 불완전 날짜 재사용 위험.
        # `_init_db` 의 스키마 초기화와 동일 기조 (`BEGIN IMMEDIATE`).
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.executemany(
                "INSERT OR REPLACE INTO minute_bars "
                "(symbol, bar_time, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
                rows,
            )
            self._conn.execute("COMMIT")
        except sqlite3.Error as exc:
            # ROLLBACK 실패는 원 예외를 가리지 않도록 무시.
            with contextlib.suppress(sqlite3.Error):
                self._conn.execute("ROLLBACK")
            raise KisMinuteBarLoadError(
                f"SQLite 저장 실패: symbol={bars[0].symbol} rows={len(rows)}"
            ) from exc

    # ---- 내부: KIS API 호출 + 페이지네이션 -----------------------------

    def _fetch_day(self, symbol: str, day: date) -> list[MinuteBar]:
        """단일 날짜의 전체 bar 를 120 건 역방향 커서 페이지네이션으로 수집.

        빈 응답은 주말·공휴일 또는 KIS 서버 보관 경계 밖으로 간주해 빈 리스트 반환.

        운영 경보 (M2): `rows` 는 비어있지 않은데 `page_bars` 가 비는 (전원
        파싱 실패) 상황은 KIS 응답 스키마 변경 또는 수신 오염 징후라 빈 응답
        (정상 공휴일) 과 구별되어야 한다. `(symbol, day)` 단위로 최초 1 회만
        `logger.error` 방출해 로그 폭주 방지. 메시지에 첫 행의 정렬된 key 목록을
        동봉해 스키마 변경 진단에 직결 (Issue #52).

        행 단위 parse skip 경보는 `(kind)` 단위 로컬 dedupe — 같은 날짜·같은 원인
        은 로그 1회만. 최대 `_PAGE_SIZE` rows × N 일 × M 종목 로그 폭주 방지 (Issue #52).

        날짜 단위 요약 (Issue #52 C1): return 직전에 `parse_skip_counts` 를
        `logger.warning` 1줄로 방출해 "1건 실패 vs 119건 실패" 구별 불가 문제를
        해소한다. 같은 페이지에서 warning·error 가 동시 방출될 수 있다 — 운영자가
        `kind` 와 `keys=` 를 한눈에 보도록 한 의도적 중첩.
        """
        kis = self._ensure_kis()
        date_str = day.strftime("%Y%m%d")
        cursor = _SESSION_CLOSE_HHMMSS
        collected: list[MinuteBar] = []
        seen: set[datetime] = set()
        is_first_page = True
        malformed_warned = False
        parse_skip_emitted: set[_ParseFailureKind] = set()
        parse_skip_counts: dict[_ParseFailureKind, int] = {}

        while True:
            if not is_first_page and self._throttle_s > 0:
                self._sleep(self._throttle_s)
            is_first_page = False

            response = self._request_with_retry(kis, symbol, date_str, cursor)
            rows = response.get("output2") or []

            if not rows:
                break

            page_bars: list[MinuteBar] = []
            for row in rows:
                try:
                    bar = self._parse_row(symbol, row, day)
                except _ParseSkipError as skip:
                    parse_skip_counts[skip.kind] = parse_skip_counts.get(skip.kind, 0) + 1
                    if skip.kind not in parse_skip_emitted:
                        parse_skip_emitted.add(skip.kind)
                        logger.warning(
                            "KIS 분봉 행 파싱 실패 — symbol={s} date={d} kind={k} keys={ks}",
                            s=symbol,
                            d=date_str,
                            k=skip.kind,
                            ks=",".join(skip.keys),
                        )
                    continue
                if bar.bar_time in seen:
                    continue
                seen.add(bar.bar_time)
                page_bars.append(bar)

            if not page_bars and not malformed_warned:
                first_row = rows[0] if rows else None
                first_row_keys = (
                    ",".join(sorted(str(k) for k in first_row))
                    if isinstance(first_row, dict)
                    else f"<non-dict:{type(first_row).__name__}>"
                )
                logger.error(
                    "KIS 분봉 페이지 전원 파싱 실패 — symbol={s} date={d} rows={n} keys={ks}",
                    s=symbol,
                    d=date_str,
                    n=len(rows),
                    ks=first_row_keys,
                )
                malformed_warned = True

            collected.extend(page_bars)

            if len(rows) < _PAGE_SIZE:
                break

            # H2: 커서 갱신은 **raw rows** 의 `stck_cntg_hour` 기준. page_bars
            # (seen dedupe 통과분) 의 min 을 쓰면 KIS 가 커서 경계에서 중복 bar 를
            # 되돌려줄 때 page_bars 는 비어있지만 rows 에는 신규 앞쪽 bar 가 섞여
            # 진행해야 할 상황에서 break 로 빠져 누락이 발생한다.
            raw_times = [
                str(row.get("stck_cntg_hour", ""))
                for row in rows
                if len(str(row.get("stck_cntg_hour", ""))) == 6
            ]
            if not raw_times:
                break
            min_time = min(raw_times)
            if min_time <= _SESSION_OPEN_HHMMSS:
                break
            cursor = _decrement_hhmmss_by_one_minute(min_time)

        if parse_skip_counts:
            logger.warning(
                "KIS 분봉 skip 요약 — symbol={s} date={d} counts={c} kept={k}",
                s=symbol,
                d=date_str,
                c=dict(sorted(parse_skip_counts.items())),
                k=len(collected),
            )

        return collected

    def _request_with_retry(
        self,
        kis: Any,
        symbol: str,
        date_str: str,
        cursor: str,
    ) -> dict[str, Any]:
        """`kis.fetch` 호출 + EGW00201 레이트 리밋 자동 재시도.

        총 최대 호출 수 = `rate_limit_max_retries + 1` (최초 1회 + 재시도 N회).
        EGW00201 감지 시 `sleep(rate_limit_wait_s)` 후 동일 params 로 재시도.
        다른 에러 코드는 재시도 없이 `KisMinuteBarLoadError` 로 승격.
        """
        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_HOUR_1": cursor,
            "FID_INPUT_DATE_1": date_str,
            "FID_PW_DATA_INCU_YN": "N",
            "FID_FAKE_TICK_INCU_YN": "",
        }

        max_attempts = self._rate_limit_max_retries + 1
        attempts = 0

        while attempts < max_attempts:
            attempts += 1
            try:
                response = kis.fetch(
                    _API_PATH,
                    api=_TR_ID,
                    params=params,
                    domain="real",
                )
            except KisMinuteBarLoadError:
                raise
            except RuntimeError:
                raise
            except Exception as exc:
                raise KisMinuteBarLoadError(
                    f"KIS fetch 호출 실패: symbol={symbol} date={date_str}"
                ) from exc

            # python-kis 의 `KisDynamicDict` 는 `dict` 서브클래스가 아니라 `__data__`
            # 속성에 raw dict 를 보관한다. raw dict · KisDynamicDict 둘 다 허용하되,
            # `__data__` 경유 값은 각 행도 raw dict 이므로 downstream 파싱에 편하다.
            response_data = getattr(response, "__data__", None)
            if isinstance(response_data, dict):
                response_dict: dict[str, Any] = response_data
            elif isinstance(response, dict):
                response_dict = response
            else:
                raise KisMinuteBarLoadError(
                    f"KIS 응답이 dict 가 아닙니다: type={type(response).__name__}"
                )

            rt_cd = response_dict.get("rt_cd", "0")
            msg_cd = response_dict.get("msg_cd", "")

            if rt_cd == "0":
                return response_dict

            if msg_cd == _RATE_LIMIT_MSG_CD:
                if attempts >= max_attempts:
                    break
                logger.warning(
                    f"KIS rate limit (EGW00201) — {self._rate_limit_wait_s}s 대기 후 "
                    f"재시도 ({attempts}/{self._rate_limit_max_retries})"
                )
                self._sleep(self._rate_limit_wait_s)
                continue

            msg = response_dict.get("msg1", "")
            raise KisMinuteBarLoadError(
                f"KIS API 에러 rt_cd={rt_cd} msg_cd={msg_cd} msg={msg!r} "
                f"(symbol={symbol} date={date_str})"
            )

        raise KisMinuteBarLoadError(
            f"KIS rate limit 재시도 한도 초과 "
            f"(max_retries={self._rate_limit_max_retries}, symbol={symbol} date={date_str})"
        )

    def _ensure_kis(self) -> Any:
        """지연 초기화된 실전 키 PyKis 인스턴스 반환. 최초 호출 시 가드 설치."""
        with self._lock:
            if self._kis is not None:
                return self._kis

            factory = self._pykis_factory
            if factory is None:
                from pykis import PyKis  # noqa: PLC0415

                factory = PyKis

            assert self._settings.kis_live_app_key is not None
            assert self._settings.kis_live_app_secret is not None
            assert self._settings.kis_live_account_no is not None

            live_appkey = self._settings.kis_live_app_key.get_secret_value()
            live_secret = self._settings.kis_live_app_secret.get_secret_value()
            try:
                kis = factory(
                    id=self._settings.kis_hts_id,
                    account=self._settings.kis_live_account_no,
                    appkey=live_appkey,
                    secretkey=live_secret,
                    keep_token=True,
                )
            except Exception as exc:
                raise KisMinuteBarLoadError("PyKis 실전 인스턴스 생성 실패") from exc
            install_order_block_guard(kis)
            self._kis = kis
            return kis

    def _parse_row(
        self,
        symbol: str,
        row: dict[str, Any],
        expected_day: date,
    ) -> MinuteBar:
        """`output2` 한 행을 `MinuteBar` 로 변환.

        실패 시 `_ParseSkipError(kind, keys)` 를 raise — `_fetch_day` 가 catch 해 원인
        카테고리별 dedupe 후 1회 warning 을 방출한다 (Issue #52). 카테고리:

        - `missing_date_or_time`: `stck_bsop_date` / `stck_cntg_hour` 길이 위반.
        - `malformed_bar_time`: 날짜·시각 문자열 파싱(`strptime`) 실패.
        - `date_mismatch`: `bar_time.date()` 가 요청 `expected_day` 와 불일치.
        - `invalid_price`: OHLC 중 하나 이상이 `None` · 빈 문자열 · 비-finite 이거나
          `_parse_decimal` 에서 `Decimal` 파싱 실패(`InvalidOperation` · `ValueError`
          · `TypeError` — 비-수치 토큰·구분자 포함 등).
        - `invalid_volume`: `cntg_vol` 이 `None` · 빈 문자열 이거나
          `int(Decimal(...))` 변환 실패(`InvalidOperation` · `ValueError` ·
          `TypeError` — 소수점·음수 포맷 오류·과대값 등).
        """
        row_keys = tuple(sorted(str(k) for k in row)) if isinstance(row, dict) else ()

        date_s = str(row.get("stck_bsop_date", ""))
        time_s = str(row.get("stck_cntg_hour", ""))
        if len(date_s) != 8 or len(time_s) != 6:
            raise _ParseSkipError("missing_date_or_time", row_keys)

        try:
            bar_time = datetime.strptime(date_s + time_s, "%Y%m%d%H%M%S").replace(tzinfo=KST)
        except ValueError as exc:
            raise _ParseSkipError("malformed_bar_time", row_keys) from exc
        bar_time = bar_time.replace(second=0, microsecond=0)

        if bar_time.date() != expected_day:
            raise _ParseSkipError("date_mismatch", row_keys)

        try:
            open_ = _parse_decimal(row.get("stck_oprc"))
            high = _parse_decimal(row.get("stck_hgpr"))
            low = _parse_decimal(row.get("stck_lwpr"))
            close = _parse_decimal(row.get("stck_prpr"))
        except (ValueError, InvalidOperation, TypeError) as exc:
            raise _ParseSkipError("invalid_price", row_keys) from exc

        try:
            volume = _parse_int(row.get("cntg_vol"))
        except (ValueError, InvalidOperation, TypeError) as exc:
            raise _ParseSkipError("invalid_volume", row_keys) from exc

        return MinuteBar(
            symbol=symbol,
            bar_time=bar_time,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )


# ---- 모듈 수준 순수 함수 ---------------------------------------------------


def _parse_decimal(raw: Any) -> Decimal:
    if raw is None:
        raise ValueError("Decimal None")
    text = str(raw).strip()
    if not text:
        raise ValueError("Decimal empty")
    value = Decimal(text)
    if not value.is_finite():
        raise ValueError(f"Decimal non-finite: {raw!r}")
    return value


def _parse_int(raw: Any) -> int:
    if raw is None:
        raise ValueError("int None")
    text = str(raw).strip()
    if not text:
        raise ValueError("int empty")
    return int(Decimal(text))


def _bar_time_to_hhmmss(dt: datetime) -> str:
    return dt.strftime("%H%M%S")


def _decrement_hhmmss_by_one_minute(hhmmss: str) -> str:
    """`"HHMMSS"` 를 1 분 감소. 음수 보정으로 `"000000"` 하한."""
    h = int(hhmmss[0:2])
    m = int(hhmmss[2:4])
    s = int(hhmmss[4:6])
    total = h * 3600 + m * 60 + s - 60
    if total < 0:
        return "000000"
    nh, rem = divmod(total, 3600)
    nm, ns = divmod(rem, 60)
    return f"{nh:02d}{nm:02d}{ns:02d}"
