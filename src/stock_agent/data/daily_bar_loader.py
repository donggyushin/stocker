"""일봉 → MinuteBar(09:00 KST) 어댑터 — `BarLoader` Protocol 구현체.

ADR-0019 Step F PR1 — F1 DCA baseline 의 일봉 입력 경로. `HistoricalDataStore`
의 `fetch_daily_ohlcv` 결과 `DailyBar` 를 09:00 KST `MinuteBar` 로 래핑해
`backtest/loader.py::BarLoader` 계약을 만족시킨다.

설계 메모
- 일봉을 MinuteBar 로 형변환하는 이유: 백테스트 엔진·전략 인터페이스가 모두
  `MinuteBar` 를 일급 시계열 단위로 다루기 때문. 일/월 단위 가설 평가 (PR1
  DCA · PR2 Golden Cross · PR3 모멘텀 등) 에서 분봉 어댑터 (`MinuteCsvBarLoader`,
  `KisMinuteBarLoader`) 와 동일 시그니처로 plugin 가능.
- bar_time = 09:00 KST 고정. 09:00 은 KRX 정규장 시작 시각으로, "이 영업일의
  대표 시각" 으로 사용. 분봉 단위 분석을 하지 않으므로 다른 시각도 무방하나
  09:00 이 가독성·외부 grep 면에서 가장 자연스럽다.
- `daily_store` 는 호출자가 라이프사이클 관리. `close()` 는 위임만 한다.

에러 정책 (broker/data 와 동일 기조)
- `RuntimeError` 는 그대로 전파. 사전 가드: `start > end`, `symbols=()`.
- store 에서 던진 예외는 래핑하지 않고 전파.
- generic `except Exception` 미사용.

스레드 모델
- 단일 프로세스 전용 (`HistoricalDataStore` 와 동일).
"""

from __future__ import annotations

import heapq
from collections.abc import Iterator
from datetime import date, datetime, time, timedelta, timezone
from types import TracebackType
from typing import Protocol, Self

from stock_agent.data.historical import DailyBar
from stock_agent.data.realtime import MinuteBar

KST = timezone(timedelta(hours=9))
"""한국 표준시 (UTC+09:00). `strategy/base.py`·`data/realtime.py` 와 값 동일."""

_BAR_TIME_OF_DAY = time(9, 0)


class DailyBarSource(Protocol):
    """일봉 소스 구조적 의존 — `HistoricalDataStore` 가 만족.

    `DailyBarLoader` 는 SQLite·pykrx 에 직접 결합하지 않고 본 Protocol 만 의존.
    테스트는 in-memory fake double 로 주입 가능 (`tests/test_daily_bar_loader.py`
    의 `_FakeStore`). 운영 경로에서는 `HistoricalDataStore` 인스턴스를 그대로 주입.
    """

    def fetch_daily_ohlcv(self, symbol: str, start: date, end: date) -> list[DailyBar]: ...
    def close(self) -> None: ...


class DailyBarLoader:
    """`DailyBarSource` 일봉을 `MinuteBar` 09:00 KST 로 래핑한 BarLoader.

    `BarLoader` Protocol (`backtest/loader.py`) 충족. 매 영업일 1 건의 MinuteBar 를
    `(bar_time, symbol)` 정렬 순서로 emit.

    Args:
        daily_store: `DailyBarSource` Protocol 을 만족하는 객체 — 운영에서는
            `HistoricalDataStore`. 라이프사이클 관리는 호출자가 책임진다
            (`close()` 는 위임만).
    """

    def __init__(self, daily_store: DailyBarSource) -> None:
        self._daily_store = daily_store

    def stream(
        self,
        start: date,
        end: date,
        symbols: tuple[str, ...],
    ) -> Iterator[MinuteBar]:
        """일봉을 MinuteBar 로 래핑해 시간순 yield.

        Raises:
            RuntimeError: `start > end` 또는 `symbols=()`. store 에서 던진
                예외는 그대로 전파.
        """
        if start > end:
            raise RuntimeError(
                f"start({start.isoformat()}) 는 end({end.isoformat()}) 이전이어야 합니다."
            )
        if not symbols:
            raise RuntimeError("symbols 는 1개 이상이어야 합니다.")

        per_symbol_streams: list[Iterator[MinuteBar]] = []
        for symbol in symbols:
            daily_bars = self._daily_store.fetch_daily_ohlcv(symbol, start, end)
            per_symbol_streams.append(self._wrap(symbol, daily_bars))

        # heapq.merge 로 (bar_time, symbol) 단조증가 보장.
        yield from heapq.merge(*per_symbol_streams, key=lambda b: (b.bar_time, b.symbol))

    @staticmethod
    def _wrap(symbol: str, daily_bars: list[DailyBar]) -> Iterator[MinuteBar]:
        """단일 심볼의 DailyBar 리스트를 MinuteBar Iterator 로 변환.

        호출자(`stream`) 가 이미 `fetch_daily_ohlcv` 결과를 받아왔으므로 입력은
        이미 trade_date 정렬되어 있다 (`historical.py` 가 ORDER BY 보장). 여기서는
        형변환만 수행.
        """
        for db in daily_bars:
            yield MinuteBar(
                symbol=symbol,
                bar_time=datetime.combine(db.trade_date, _BAR_TIME_OF_DAY, tzinfo=KST),
                open=db.open,
                high=db.high,
                low=db.low,
                close=db.close,
                volume=db.volume,
            )

    def close(self) -> None:
        """`daily_store.close()` 위임. 멱등성은 store 책임."""
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
