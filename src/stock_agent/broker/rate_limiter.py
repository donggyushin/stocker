"""주문 경로 전용 rate limiter.

python-kis 2.x 는 `PyKis.request()` 내부에서 도메인(real/virtual) 단위로
RPS 상한(real 19/s, virtual 2/s)을 이미 지키고 있다. 이 모듈은 그 위에
**주문 경로**(`KisClient.place_buy` / `place_sell`) 전용으로 **애플리케이션
레벨 보수적 상한**을 얹는다. 목적은 세 가지다.

- 장 시작 직후 동시 브레이크아웃 burst 에 대비해 주문 제출 간격을 강제.
- KIS 계좌·TR 단위로 실제 적용되는 더 낮은 제한(도메인 RPS 보다 엄격)에
  선제 대응.
- python-kis 내부 wall-clock 대기와 별개로 상위 레이어에서 관측 가능한
  제어점을 확보 (loguru 로 대기 시간을 남긴다).

설계 전제
- 단일 프로세스 · 단일 이벤트 루프 가정. 스레드·프로세스 safe 를 제공하지
  않는다. 멀티프로세스 확장(Phase 5 VPS 이전 등)은 재설계 범위.
- 시계는 `time.monotonic` 기본. 테스트는 `time_fn` / `sleep_fn` 주입으로
  wall-clock 을 건드리지 않고 결정론적으로 검증한다.
- 조회 경로(`get_balance` 등)에는 적용하지 않는다. 라이브러리 내장 리미터로
  충분하다고 판단.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from loguru import logger

__all__ = ["OrderRateLimiter"]


class OrderRateLimiter:
    """주문 제출 경로에 얹는 동기 리미터.

    두 제약을 동시에 만족시킨다.
    1. 슬라이딩 윈도우 — 직전 `period_s` 초 동안 호출 수가 `max_calls` 를
       넘지 않는다.
    2. 최소 간격 — 바로 이전 호출과의 간격이 `min_interval_s` 이상.

    `acquire()` 는 두 제약 중 더 큰 대기 시간만큼 `sleep_fn` 을 호출한 뒤
    타임스탬프를 적재하고 반환한다. 타임스탬프는 내부 `deque` 에 쌓이며
    만료된 항목은 호출 시점에 purge 된다.
    """

    __slots__ = (
        "_max_calls",
        "_period_s",
        "_min_interval_s",
        "_time",
        "_sleep",
        "_timestamps",
    )

    def __init__(
        self,
        *,
        max_calls: int = 2,
        period_s: float = 1.0,
        min_interval_s: float = 0.35,
        time_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_calls < 1:
            raise ValueError(f"max_calls 는 1 이상이어야 합니다 (입력={max_calls})")
        if period_s <= 0:
            raise ValueError(f"period_s 는 양수여야 합니다 (입력={period_s})")
        if min_interval_s < 0:
            raise ValueError(f"min_interval_s 는 0 이상이어야 합니다 (입력={min_interval_s})")

        self._max_calls = max_calls
        self._period_s = period_s
        self._min_interval_s = min_interval_s
        self._time = time_fn
        self._sleep = sleep_fn
        self._timestamps: deque[float] = deque()

    @property
    def max_calls(self) -> int:
        return self._max_calls

    @property
    def period_s(self) -> float:
        return self._period_s

    @property
    def min_interval_s(self) -> float:
        return self._min_interval_s

    def acquire(self, label: str = "order") -> None:
        """슬롯 확보. 필요 시 `sleep_fn` 으로 대기 후 타임스탬프 적재.

        Args:
            label: 로그 구분자. 보통 `f"{side} {symbol}"` 형태.
        """
        now = self._time()
        self._purge(now)
        wait = max(self._wait_for_window(now), self._wait_for_interval(now))

        if wait > 0:
            logger.info(
                f"rate_limiter [{label}] 대기 {wait:.3f}s "
                f"(window_cap={self._max_calls}/{self._period_s:.2f}s, "
                f"min_interval={self._min_interval_s:.3f}s)"
            )
            self._sleep(wait)
            now = self._time()
            self._purge(now)

        self._timestamps.append(now)
        logger.debug(f"rate_limiter [{label}] acquired (inflight={len(self._timestamps)})")

    def _purge(self, now: float) -> None:
        while self._timestamps and now - self._timestamps[0] >= self._period_s:
            self._timestamps.popleft()

    def _wait_for_window(self, now: float) -> float:
        if len(self._timestamps) < self._max_calls:
            return 0.0
        oldest = self._timestamps[0]
        return max(0.0, self._period_s - (now - oldest))

    def _wait_for_interval(self, now: float) -> float:
        if not self._timestamps:
            return 0.0
        last = self._timestamps[-1]
        return max(0.0, self._min_interval_s - (now - last))
