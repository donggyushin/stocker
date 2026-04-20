"""백테스트 입력 분봉 로더 — `BarLoader` Protocol + `InMemoryBarLoader`.

책임 범위
- 과거 분봉 스트림을 시간순으로 `BacktestEngine.run()` 에 공급하는 경계.
- 날짜·심볼 필터링, 정렬, (symbol, bar_time) 중복 제거.

설계 원칙
- **실제 데이터 소스(KIS 과거 분봉 API, 외부 CSV 등)는 이 PR 범위 밖**. Protocol
  만 정의해 엔진과 소스를 분리하고, 인메모리 구현 하나만 제공해 단위 테스트에
  집중한다. 실데이터 어댑터는 후속 PR.
- `BarLoader.stream` 반환은 **시간 단조증가** 를 계약. 동일 시각의 서로 다른
  심볼 bar 는 입력 순서에 따라 배치.
- 중복 `(symbol, bar_time)` 은 나중 값 우선으로 dedupe (단위 테스트 편의).
- 외부 I/O 없음. `MinuteBar` 는 `data` 패키지 공개 DTO 를 그대로 소비.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date
from typing import Protocol

from stock_agent.data import MinuteBar


class BarLoader(Protocol):
    """백테스트 분봉 스트림 공급자 Protocol.

    `stream(start, end, symbols)` 은 다음을 보장해야 한다:
    - `start <= bar.bar_time.date() <= end` (경계 포함).
    - `bar.symbol in symbols`.
    - 시간 단조증가 (동일 시각은 허용).
    - `(symbol, bar_time)` 중복 없음.

    호출자 계약:
    - `start <= end` 이어야 함. 위반 시 구현은 `RuntimeError` 를 던진다.
    - `symbols` 는 1개 이상이어야 함. 빈 튜플은 호출자 오류로 간주하며
      구현은 `RuntimeError` 를 던진다 ("필터 미적용 = 전체 로드" 같은
      암묵 확장 금지 — 각 구현의 전수 스캔 비용과 의미가 서로 다르다).

    구현체는 I/O 여부·스트리밍 방식에 자유. 엔진은 순방향 1회 소비만 한다.
    """

    def stream(
        self,
        start: date,
        end: date,
        symbols: tuple[str, ...],
    ) -> Iterable[MinuteBar]: ...


class InMemoryBarLoader:
    """메모리 상주 분봉 컬렉션을 정렬·필터링해 제공하는 로더.

    초기화 시점에 한 번 정렬·dedupe 한다. 이후 `stream` 호출은 조건 필터링만.

    Raises:
        RuntimeError: `start > end` 또는 `symbols` 가 빈 튜플일 때.
    """

    def __init__(self, bars: Iterable[MinuteBar]) -> None:
        unique: dict[tuple[str, object], MinuteBar] = {}
        for bar in bars:
            unique[(bar.symbol, bar.bar_time)] = bar
        self._bars: tuple[MinuteBar, ...] = tuple(
            sorted(unique.values(), key=lambda b: (b.bar_time, b.symbol))
        )

    @property
    def bars(self) -> tuple[MinuteBar, ...]:
        """저장된 분봉 스냅샷 (정렬·dedup 완료). 테스트·디버깅용."""
        return self._bars

    def stream(
        self,
        start: date,
        end: date,
        symbols: tuple[str, ...],
    ) -> Iterator[MinuteBar]:
        if start > end:
            raise RuntimeError(f"start({start}) 는 end({end}) 이전이어야 합니다.")
        if not symbols:
            raise RuntimeError("symbols 는 1개 이상이어야 합니다.")
        symbol_set = frozenset(symbols)
        for bar in self._bars:
            d = bar.bar_time.date()
            if d < start or d > end:
                continue
            if bar.symbol not in symbol_set:
                continue
            yield bar
