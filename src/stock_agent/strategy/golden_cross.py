"""Golden Cross (200d SMA) 추세추종 전략.

ADR-0019 Step F PR2 — F2 Golden Cross. KOSPI 200 ETF (069500 KODEX 200) 단일
종목에 대해 200 일 단순이동평균 (SMA) 의 close cross 를 신호로 사용하는 추세
추종 전략. ADR-0022 게이트 (DCA baseline 대비 알파 + MDD > -25% + Sharpe > 0.3)
평가 대상.

책임 범위
- 매 분봉 수신 시 close history 누적 → SMA(`sma_period`) 계산.
- close > SMA cross-up 시 EntrySignal (long 진입), close < SMA cross-down 시
  ExitSignal (force_close 사유) 발행.
- `on_time(now)` 항상 빈 리스트 — DCA 와 동일하게 force_close 시각 가정 없음.

설계 메모
- DCA 와 동일하게 `EntrySignal.stop_price/take_price=Decimal("0")` 마커.
  손익절 미사용 — 청산은 SMA cross-down 단일 경로. 호출자
  (`compute_golden_cross_baseline`) 가 0 마커를 인지해 손익절 판정 건너뜀.
- `target_symbol` 만 처리. 비타겟 분봉은 buffer 누적 안 함 + 시그널 없음.
- 세션 경계 reset 없음 — SMA 누적이 핵심이라 day 변경 무관 buffer 유지
  (DCA 와의 핵심 차이).
- per-symbol 시간 역행 가드는 target_symbol 기준으로만 적용.

에러 정책 (broker/data/strategy 와 동일 기조)
- `RuntimeError` 전파 — 잘못된 symbol·naive datetime·시간 역행·설정 위반.

스레드 모델
- 단일 프로세스 전용.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

from loguru import logger

from stock_agent.data import MinuteBar
from stock_agent.strategy.base import EntrySignal, ExitSignal, Signal

_SYMBOL_RE = re.compile(r"^\d{6}$")

_DEFAULT_TARGET_SYMBOL = "069500"
_DEFAULT_SMA_PERIOD = 200
_DEFAULT_POSITION_PCT = Decimal("1.0")

_PositionState = Literal["flat", "long"]


@dataclass(frozen=True, slots=True)
class GoldenCrossConfig:
    """Golden Cross 파라미터.

    Raises:
        RuntimeError: `target_symbol` 6자리 숫자 정규식 위반,
            `sma_period <= 0`, `position_pct <= 0` 또는 `position_pct > 1`.
    """

    target_symbol: str = _DEFAULT_TARGET_SYMBOL
    sma_period: int = _DEFAULT_SMA_PERIOD
    position_pct: Decimal = _DEFAULT_POSITION_PCT

    def __post_init__(self) -> None:
        if not _SYMBOL_RE.match(self.target_symbol):
            raise RuntimeError(
                f"target_symbol 은 6자리 숫자 문자열이어야 합니다 (got={self.target_symbol!r})"
            )
        if self.sma_period <= 0:
            raise RuntimeError(f"sma_period 는 양수여야 합니다 (got={self.sma_period})")
        if self.position_pct <= 0 or self.position_pct > 1:
            raise RuntimeError(f"position_pct 는 (0, 1] 범위여야 합니다 (got={self.position_pct})")


@dataclass
class _GoldenCrossState:
    """내부 상태 — close rolling buffer + position state + 시간 역행 가드용 last_bar_time."""

    closes: deque[Decimal] = field(default_factory=deque)
    position_state: _PositionState = "flat"
    last_bar_time: datetime | None = None


class GoldenCrossStrategy:
    """200 일 SMA cross 추세추종. `Strategy` Protocol 구현체.

    공개 API: `on_bar`, `on_time`, `config` (프로퍼티).

    동일 호출자 스레드 순차 호출 가정. 동시 호출 미지원.
    """

    def __init__(self, config: GoldenCrossConfig | None = None) -> None:
        self._config = config if config is not None else GoldenCrossConfig()
        self._state = _GoldenCrossState(closes=deque(maxlen=self._config.sma_period))

    @property
    def config(self) -> GoldenCrossConfig:
        return self._config

    def on_bar(self, bar: MinuteBar) -> list[Signal]:
        """분봉 이벤트 진입점. target_symbol 의 close > / < SMA cross 에서 시그널 발행."""
        self._validate_symbol(bar.symbol)
        self._require_aware(bar.bar_time, "bar.bar_time")

        if bar.symbol != self._config.target_symbol:
            return []

        # 시간 역행 가드는 target_symbol 기준으로만.
        if self._state.last_bar_time is not None and bar.bar_time < self._state.last_bar_time:
            raise RuntimeError(
                f"bar.bar_time 역행 감지: last={self._state.last_bar_time.isoformat()}, "
                f"now={bar.bar_time.isoformat()}"
            )
        self._state.last_bar_time = bar.bar_time

        self._state.closes.append(bar.close)

        if len(self._state.closes) < self._config.sma_period:
            return []

        sma = sum(self._state.closes, Decimal("0")) / Decimal(self._config.sma_period)
        close = bar.close

        if self._state.position_state == "flat" and close > sma:
            self._state.position_state = "long"
            signal = EntrySignal(
                symbol=bar.symbol,
                price=close,
                ts=bar.bar_time,
                stop_price=Decimal("0"),
                take_price=Decimal("0"),
            )
            logger.info(
                "GoldenCross 진입: {s} @ {p} (sma={sma}, ts={t})",
                s=bar.symbol,
                p=close,
                sma=sma,
                t=bar.bar_time.isoformat(),
            )
            return [signal]

        if self._state.position_state == "long" and close < sma:
            self._state.position_state = "flat"
            signal = ExitSignal(
                symbol=bar.symbol,
                price=close,
                ts=bar.bar_time,
                reason="force_close",
            )
            logger.info(
                "GoldenCross 청산: {s} @ {p} (sma={sma}, ts={t})",
                s=bar.symbol,
                p=close,
                sma=sma,
                t=bar.bar_time.isoformat(),
            )
            return [signal]

        return []

    def on_time(self, now: datetime) -> list[Signal]:
        """시각 이벤트 — Golden Cross 는 force_close 가정 없음, 항상 빈 리스트."""
        self._require_aware(now, "now")
        return []

    @staticmethod
    def _validate_symbol(symbol: str) -> None:
        if not symbol or not _SYMBOL_RE.match(symbol):
            raise RuntimeError(f"symbol 은 6자리 숫자 문자열이어야 합니다 (got={symbol!r})")

    @staticmethod
    def _require_aware(ts: datetime, name: str) -> None:
        if ts.tzinfo is None:
            raise RuntimeError(
                f"{name} 은 tz-aware datetime 이어야 합니다 (got naive {ts.isoformat()})"
            )
