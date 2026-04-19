"""OrderRateLimiter 단위 테스트.

wall-clock 소비를 피하기 위해 `time_fn` / `sleep_fn` 을 `_FakeClock` 으로
주입한다. sleep 요청이 들어오면 가상 시계를 그만큼 전진시켜, 실제
`time.sleep` 과 동일한 인과(sleep 후 time_fn 이 증가) 를 재현한다.

검증 범위: 슬라이딩 윈도우 상한, 최소 간격, 만료 purge, 로그 관측, 입력 검증,
label 인자 로그 반영, 긴 시간 경과 후 min_interval 재적용.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from loguru import logger

from stock_agent.broker.rate_limiter import OrderRateLimiter


class _FakeClock:
    """결정론적 가짜 클럭. sleep 이 호출되면 now 를 전진시킨다."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleep_calls: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        # rate_limiter 내부가 wait=0 일 땐 sleep 을 아예 호출하지 않아야 하므로
        # 여기서는 호출되었을 때만 기록·전진한다.
        self.sleep_calls.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        """테스트에서 시간 경과를 흉내낼 때 사용. sleep 기록은 남기지 않는다."""
        self.now += seconds


def _make_limiter(
    clock: _FakeClock,
    *,
    max_calls: int = 2,
    period_s: float = 1.0,
    min_interval_s: float = 0.35,
) -> OrderRateLimiter:
    return OrderRateLimiter(
        max_calls=max_calls,
        period_s=period_s,
        min_interval_s=min_interval_s,
        time_fn=clock.time,
        sleep_fn=clock.sleep,
    )


# ---------------------------------------------------------------------------
# 정상 경로
# ---------------------------------------------------------------------------


def test_acquire_first_call_does_not_sleep() -> None:
    clock = _FakeClock()
    limiter = _make_limiter(clock)

    limiter.acquire("buy 005930")

    assert clock.sleep_calls == []


def test_acquire_respects_min_interval() -> None:
    """연속 두 호출의 간격이 min_interval_s 보다 짧으면 그만큼 대기해야 한다."""
    clock = _FakeClock()
    limiter = _make_limiter(clock, max_calls=10, period_s=10.0, min_interval_s=0.35)

    limiter.acquire("buy")
    limiter.acquire("sell")  # 시간 경과 0 → 350ms 대기 발생

    assert clock.sleep_calls == pytest.approx([0.35])


def test_acquire_respects_window_cap() -> None:
    """max_calls=2, period_s=1.0 상황에서 3번째 호출은 window 가 풀릴 때까지 대기."""
    clock = _FakeClock()
    limiter = _make_limiter(clock, max_calls=2, period_s=1.0, min_interval_s=0.0)

    limiter.acquire("buy #1")  # t=1000.000
    clock.advance(0.4)
    limiter.acquire("buy #2")  # t=1000.400
    clock.advance(0.2)
    # t=1000.600, 첫 호출로부터 0.6s 경과 → 남은 window = 0.4s 대기
    limiter.acquire("buy #3")

    assert clock.sleep_calls == pytest.approx([0.4])


def test_acquire_takes_max_of_window_and_interval() -> None:
    """두 제약 중 더 큰 대기 시간을 택해야 한다."""
    clock = _FakeClock()
    limiter = _make_limiter(clock, max_calls=2, period_s=1.0, min_interval_s=0.8)

    limiter.acquire("a")  # t=1000.000
    clock.advance(0.1)
    limiter.acquire("b")  # t=1000.100, min_interval=0.8 → sleep 0.7
    # 위 호출 후 시각은 1000.100 + 0.7 = 1000.800
    assert clock.sleep_calls == pytest.approx([0.7])

    clock.advance(0.1)  # t=1000.900
    # window: 첫 호출이 t=1000, 남은 window=0.1
    # interval: 직전이 t=1000.800, 남은 interval=0.7
    # 둘 중 큰 0.7 이 채택되어야 한다
    limiter.acquire("c")
    assert clock.sleep_calls == pytest.approx([0.7, 0.7])


def test_acquire_purges_expired_timestamps() -> None:
    """period 를 초과해 경과하면 과거 타임스탬프는 정리되고 sleep 미발생."""
    clock = _FakeClock()
    limiter = _make_limiter(clock, max_calls=2, period_s=1.0, min_interval_s=0.0)

    limiter.acquire("first")
    limiter.acquire("second")  # sleep 없음 (min_interval=0)
    clock.sleep_calls.clear()  # 여기까지의 sleep 은 아직 0 이지만 명시적 리셋

    clock.advance(1.5)  # period 경과
    limiter.acquire("third")

    assert clock.sleep_calls == []


def test_acquire_with_zero_min_interval_only_window_applies() -> None:
    clock = _FakeClock()
    limiter = _make_limiter(clock, max_calls=3, period_s=1.0, min_interval_s=0.0)

    limiter.acquire("a")
    limiter.acquire("b")
    limiter.acquire("c")

    assert clock.sleep_calls == []


# ---------------------------------------------------------------------------
# 관측 (로그)
# ---------------------------------------------------------------------------


@pytest.fixture
def _loguru_messages() -> Iterator[list[dict[str, Any]]]:
    """loguru 로그 메시지 captura. pytest caplog 는 stdlib logging 만 잡아서 별도 sink."""
    captured: list[dict[str, Any]] = []

    def _sink(message: Any) -> None:
        record = message.record
        captured.append({"level": record["level"].name, "message": record["message"]})

    handler_id = logger.add(_sink, level="DEBUG")
    try:
        yield captured
    finally:
        logger.remove(handler_id)


def test_acquire_logs_info_when_waiting(_loguru_messages: list[dict[str, Any]]) -> None:
    clock = _FakeClock()
    limiter = _make_limiter(clock, max_calls=10, period_s=10.0, min_interval_s=0.35)

    limiter.acquire("buy 005930")
    limiter.acquire("sell 000660")  # 대기 발생

    infos = [m for m in _loguru_messages if m["level"] == "INFO"]
    assert len(infos) == 1
    assert "rate_limiter" in infos[0]["message"]
    assert "sell 000660" in infos[0]["message"]


def test_acquire_does_not_log_info_without_wait(
    _loguru_messages: list[dict[str, Any]],
) -> None:
    clock = _FakeClock()
    limiter = _make_limiter(clock, max_calls=5, period_s=1.0, min_interval_s=0.0)

    limiter.acquire("buy")

    infos = [m for m in _loguru_messages if m["level"] == "INFO"]
    assert infos == []


# ---------------------------------------------------------------------------
# 입력 검증
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("max_calls", "period_s", "min_interval_s"),
    [
        (0, 1.0, 0.1),
        (-1, 1.0, 0.1),
        (1, 0.0, 0.1),
        (1, -0.5, 0.1),
        (1, 1.0, -0.01),
    ],
    ids=[
        "max_calls=0",
        "max_calls=-1",
        "period_s=0",
        "period_s=-0.5",
        "min_interval_s=-0.01",
    ],
)
def test_constructor_rejects_invalid_params(
    max_calls: int, period_s: float, min_interval_s: float
) -> None:
    with pytest.raises(ValueError):
        OrderRateLimiter(
            max_calls=max_calls,
            period_s=period_s,
            min_interval_s=min_interval_s,
        )


# ---------------------------------------------------------------------------
# 추가 케이스
# ---------------------------------------------------------------------------


def test_acquire_label이_info_로그_문자열에_반영된다(
    _loguru_messages: list[dict[str, Any]],
) -> None:
    """label 인자가 대기 발생 시 INFO 로그 문자열에 포함되는지 검증."""
    clock = _FakeClock()
    limiter = _make_limiter(clock, max_calls=10, period_s=10.0, min_interval_s=0.35)

    limiter.acquire("buy 005930")
    limiter.acquire("sell 000660")  # 대기 발생 → INFO 로그

    infos = [m for m in _loguru_messages if m["level"] == "INFO"]
    assert len(infos) == 1
    assert "sell 000660" in infos[0]["message"]
    # 처음 호출 label 은 INFO 에 없어야 한다 (대기 없음)
    assert "buy 005930" not in infos[0]["message"]


def test_acquire_period_경과후_재호출_min_interval_재적용() -> None:
    """period 가 완전히 경과해 window 제약이 풀렸더라도, 그 직후 연속 호출은
    min_interval_s 제약을 다시 받아야 한다.

    purge 로 _timestamps 가 완전히 비면 _wait_for_interval 이 0 을 반환해
    min_interval 이 무효화된다. 이는 의도된 동작이다:
    - period 경과 후 첫 호출은 sleep 없이 즉시 허용된다.
    - 그러나 그 첫 호출 *이후* 곧바로 이어지는 다음 호출은 min_interval 을 받는다.
    """
    clock = _FakeClock()
    limiter = _make_limiter(clock, max_calls=2, period_s=1.0, min_interval_s=0.35)

    limiter.acquire("first")
    limiter.acquire("second")  # min_interval 대기 발생
    clock.sleep_calls.clear()

    clock.advance(2.0)  # period 를 완전히 초과 — _timestamps 전부 purge 대상

    limiter.acquire("third")  # purge 후 첫 호출 — sleep 없음
    assert clock.sleep_calls == [], "period 경과 후 첫 호출은 sleep 없어야 한다"

    # third 이후 즉시 호출 → min_interval=0.35 대기 발생
    limiter.acquire("fourth")
    assert clock.sleep_calls == pytest.approx([0.35]), "첫 호출 직후엔 min_interval 이 적용된다"
