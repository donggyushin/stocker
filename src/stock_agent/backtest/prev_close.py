"""백테스트용 `PrevCloseProvider` 구현 — `HistoricalDataStore` + `BusinessDayCalendar` 기반.

ADR-0019 Step E 후속 (Stage 2). `GapReversalStrategy` 의 `prev_close_provider`
의존을 운영 데이터 (pykrx 일봉 캐시) 로 채운다. Stage 1 의 stub
(`_stub_prev_close_provider` — 항상 None) 을 실제 구현으로 교체.

알고리즘
- 세션 시작 시 `(symbol, session_date)` 호출.
- `session_date - 1` 부터 1 일씩 역행하며 `BusinessDayCalendar.is_business_day` 가
  True 가 되는 첫 영업일을 찾는다 (최대 `max_lookback_days` 일).
- 찾은 영업일로 `HistoricalDataStore.fetch_daily_ohlcv(symbol, prev_day, prev_day)`
  호출. 일봉 1 건이 있으면 `close` 반환, 없으면 None.
- max_lookback 내 영업일을 못 찾으면 None + `logger.warning` (운영 가시성 — 캘린더
  구멍 신호).

설계 메모
- `Callable[[str, date], Decimal | None]` 시그니처를 `__call__` 로 만족 →
  `GapReversalStrategy(prev_close_provider=instance)` 직접 주입 가능.
- `HistoricalDataStore` 의 `close()` 를 위임 — `with` 컨텍스트 매니저 지원.
- 입력 가드는 `RuntimeError` (broker/data/strategy 와 동일 기조). 잘못된 symbol·
  잘못된 max_lookback 은 사용자 입력 오류.
- 단일 프로세스 전용 (HistoricalDataStore 와 동일).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal
from types import TracebackType
from typing import Self

from loguru import logger

from stock_agent.data import BusinessDayCalendar, HistoricalDataStore

_SYMBOL_RE = re.compile(r"^\d{6}$")


class DailyBarPrevCloseProvider:
    """`(symbol, session_date) → 직전 영업일 종가` callable.

    `Callable[[str, date], Decimal | None]` 시그니처 (`PrevCloseProvider`) 를
    `__call__` 로 만족한다.

    Args:
        daily_store: `HistoricalDataStore` 인스턴스. 일봉 캐시 + pykrx 폴백.
        calendar: `BusinessDayCalendar` Protocol 구현체 (`YamlBusinessDayCalendar`
            등). 직전 영업일 판정에만 사용.
        max_lookback_days: 직전 영업일을 찾기 위해 역행할 최대 일수. 기본 14.
            한국 시장 최장 연휴 (추석·설날) 도 9 일 이내이므로 14 일이면 충분.
            0 또는 음수 → `RuntimeError`.

    Raises:
        RuntimeError: `max_lookback_days <= 0` 일 때 (생성자).
    """

    def __init__(
        self,
        daily_store: HistoricalDataStore,
        calendar: BusinessDayCalendar,
        *,
        max_lookback_days: int = 14,
    ) -> None:
        if max_lookback_days <= 0:
            raise RuntimeError(f"max_lookback_days 는 양수여야 합니다 (got={max_lookback_days})")
        self._daily_store = daily_store
        self._calendar = calendar
        self._max_lookback_days = max_lookback_days

    def __call__(self, symbol: str, session_date: date) -> Decimal | None:
        """`session_date` 직전 영업일 종가를 반환. 없거나 캐시 미스면 None."""
        self._validate_symbol(symbol)

        prev_day = self._find_prev_business_day(session_date)
        if prev_day is None:
            logger.warning(
                f"prev_close 직전 영업일 미발견 — symbol={symbol}, "
                f"session_date={session_date.isoformat()}, "
                f"max_lookback_days={self._max_lookback_days}. "
                "캘린더 구멍 또는 휴장 연쇄 가능성."
            )
            return None

        bars = self._daily_store.fetch_daily_ohlcv(symbol, prev_day, prev_day)
        if not bars:
            logger.debug(
                f"prev_close 일봉 캐시 미스 — symbol={symbol}, "
                f"prev_day={prev_day.isoformat()} (휴장·신규상장 등). None 반환."
            )
            return None
        return bars[0].close

    def _find_prev_business_day(self, session_date: date) -> date | None:
        """`session_date - 1` 부터 1 일씩 역행하며 첫 영업일을 반환. 못 찾으면 None."""
        for offset in range(1, self._max_lookback_days + 1):
            candidate = session_date - timedelta(days=offset)
            if self._calendar.is_business_day(candidate):
                return candidate
        return None

    def close(self) -> None:
        """`HistoricalDataStore.close()` 위임. 호출자 멱등 책임은 store 가 진다."""
        self._daily_store.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    @staticmethod
    def _validate_symbol(symbol: str) -> None:
        if not symbol or not _SYMBOL_RE.match(symbol):
            raise RuntimeError(f"symbol 은 6자리 숫자 문자열이어야 합니다 (got={symbol!r})")
