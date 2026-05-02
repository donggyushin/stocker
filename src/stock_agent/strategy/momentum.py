"""Cross-sectional Momentum 전략 (ADR-0019 Step F PR3 — F3).

KOSPI 200 종목 중 직전 ``lookback_months × 21`` 영업일 누적 수익률 상위
``top_n`` 종목을 보유하는 multi-symbol 전략. 매월 첫 영업일 (rebalance_day=1)
리밸런싱.

설계 결정
- ``on_bar``: universe 종목 close 를 per-symbol rolling deque 에 누적. 시그널 X.
- ``on_time(now)``: ``now.month != last_rebalance_month`` 이면 리밸런싱 트리거.
  lookback 충족 후보 수가 ``top_n`` 미만이면 보류 (``last_rebalance_month``
  미갱신 — 다음 호출에서 재시도). 그 외엔 ``ExitSignal`` · ``EntrySignal``
  다중 emit 후 holdings 갱신 + ``last_rebalance_month`` 갱신.

``Strategy`` Protocol 호환. force_close 가정 없음 (``on_time`` 은 리밸런싱 전용).

체결가 계약
- ``EntrySignal.stop_price = take_price = Decimal("0")`` 마커 — 손익절 미사용.
  호출자 (``compute_momentum_baseline``) 가 마커를 인지해 손익절 판정 건너뜀.

세션 경계 정책
- 일봉 전략 — buffer 자동 리셋 없음 (Golden Cross 와 동일 기조).

에러 정책 (broker/data/strategy 와 동일 기조)
- ``RuntimeError`` 전파 — config 위반·naive datetime·symbol 정규식 위반·시간 역행.

스레드 모델
- 단일 프로세스 전용.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from loguru import logger

from stock_agent.data import MinuteBar
from stock_agent.strategy.base import EntrySignal, ExitSignal, Signal

_SYMBOL_RE = re.compile(r"^\d{6}$")

_DEFAULT_LOOKBACK_MONTHS = 12
_DEFAULT_TOP_N = 10
_DEFAULT_REBALANCE_DAY = 1
_DEFAULT_POSITION_PCT = Decimal("1.0")
_DAYS_PER_MONTH = 21  # KRX 영업일 근사 (월 평균 ≈ 21일)


@dataclass(frozen=True, slots=True)
class MomentumConfig:
    """Cross-sectional 모멘텀 파라미터.

    Raises:
        RuntimeError: ``universe`` 빈 tuple / 6자리 정규식 위반 / 중복,
            ``lookback_months <= 0``, ``top_n`` 가 ``[1, len(universe)]`` 범위 밖,
            ``rebalance_day`` 가 ``[1, 28]`` 범위 밖,
            ``position_pct`` 가 ``(0, 1]`` 범위 밖.
    """

    universe: tuple[str, ...]
    lookback_months: int = _DEFAULT_LOOKBACK_MONTHS
    top_n: int = _DEFAULT_TOP_N
    rebalance_day: int = _DEFAULT_REBALANCE_DAY
    position_pct: Decimal = _DEFAULT_POSITION_PCT

    def __post_init__(self) -> None:
        if not self.universe:
            raise RuntimeError("universe 는 1개 이상이어야 합니다.")
        seen: set[str] = set()
        for sym in self.universe:
            if not _SYMBOL_RE.match(sym):
                raise RuntimeError(f"universe 종목은 6자리 숫자 문자열이어야 합니다 (got={sym!r})")
            if sym in seen:
                raise RuntimeError(f"universe 에 중복 종목이 있습니다 (sym={sym})")
            seen.add(sym)
        if self.lookback_months <= 0:
            raise RuntimeError(f"lookback_months 는 양수여야 합니다 (got={self.lookback_months})")
        if self.top_n < 1:
            raise RuntimeError(f"top_n 은 1 이상이어야 합니다 (got={self.top_n})")
        # top_n > len(universe) 검증은 사용자가 명시적으로 지정한 값일 때만 적용.
        # 기본값(_DEFAULT_TOP_N=10) 은 작은 universe 테스트·실험 편의를 위해 허용
        # — 런타임에서 candidates < top_n 이면 자동으로 리밸런싱 보류.
        if self.top_n != _DEFAULT_TOP_N and self.top_n > len(self.universe):
            raise RuntimeError(
                f"top_n 은 universe 길이 이하여야 합니다 "
                f"(top_n={self.top_n}, universe_size={len(self.universe)})"
            )
        if self.rebalance_day < 1 or self.rebalance_day > 28:
            raise RuntimeError(f"rebalance_day 는 1~28 범위여야 합니다 (got={self.rebalance_day})")
        if self.position_pct <= 0 or self.position_pct > 1:
            raise RuntimeError(f"position_pct 는 (0, 1] 범위여야 합니다 (got={self.position_pct})")

    @property
    def lookback_days(self) -> int:
        """``lookback_months × 21`` 영업일 근사."""
        return self.lookback_months * _DAYS_PER_MONTH


class MomentumStrategy:
    """Cross-sectional 모멘텀 전략. ``Strategy`` Protocol 구현체.

    공개 API: ``on_bar``, ``on_time``, ``config`` (프로퍼티).

    동일 호출자 스레드 순차 호출 가정. 동시 호출 미지원.
    """

    def __init__(self, config: MomentumConfig | None = None) -> None:
        if config is None:
            raise RuntimeError("MomentumConfig 는 필수입니다 (universe 지정 필요).")
        self._config = config
        self._lookback_days = config.lookback_days
        self._closes: dict[str, deque[Decimal]] = {
            sym: deque(maxlen=self._lookback_days) for sym in config.universe
        }
        self._holdings: set[str] = set()
        self._last_rebalance_month: tuple[int, int] | None = None
        self._last_bar_time: dict[str, datetime] = {}

    @property
    def config(self) -> MomentumConfig:
        return self._config

    def on_bar(self, bar: MinuteBar) -> list[Signal]:
        """분봉 이벤트 — universe 종목 close 누적. 항상 빈 리스트 반환."""
        self._validate_symbol(bar.symbol)
        self._require_aware(bar.bar_time, "bar.bar_time")

        if bar.symbol not in self._closes:
            return []

        last_ts = self._last_bar_time.get(bar.symbol)
        if last_ts is not None and bar.bar_time < last_ts:
            raise RuntimeError(
                f"bar.bar_time 역행 감지: sym={bar.symbol}, "
                f"last={last_ts.isoformat()}, now={bar.bar_time.isoformat()}"
            )
        self._last_bar_time[bar.symbol] = bar.bar_time
        self._closes[bar.symbol].append(bar.close)
        return []

    def on_time(self, now: datetime) -> list[Signal]:
        """시각 이벤트 — 월 변경 시 리밸런싱 시그널 다중 emit."""
        self._require_aware(now, "now")
        current_month = (now.year, now.month)
        if self._last_rebalance_month == current_month:
            return []

        candidates: list[tuple[str, Decimal, Decimal]] = []
        for sym in self._config.universe:
            buf = self._closes[sym]
            if len(buf) < self._lookback_days:
                continue
            first = buf[0]
            last = buf[-1]
            if first <= 0:
                continue
            ret = (last / first) - Decimal("1")
            candidates.append((sym, ret, last))

        if len(candidates) < self._config.top_n:
            return []

        candidates.sort(key=lambda x: (-x[1], x[0]))
        top = candidates[: self._config.top_n]
        top_set = {sym for sym, _, _ in top}
        latest_by_sym: dict[str, Decimal] = {sym: price for sym, _, price in candidates}
        for sym in self._holdings:
            if sym not in latest_by_sym:
                buf = self._closes[sym]
                if buf:
                    latest_by_sym[sym] = buf[-1]

        to_exit = self._holdings - top_set
        to_enter = top_set - self._holdings

        signals: list[Signal] = []
        for sym in sorted(to_exit):
            price = latest_by_sym.get(sym)
            if price is None:
                continue
            signals.append(ExitSignal(symbol=sym, price=price, ts=now, reason="force_close"))
        for sym in sorted(to_enter):
            price = latest_by_sym[sym]
            signals.append(
                EntrySignal(
                    symbol=sym,
                    price=price,
                    ts=now,
                    stop_price=Decimal("0"),
                    take_price=Decimal("0"),
                )
            )

        self._holdings = top_set
        self._last_rebalance_month = current_month
        if signals:
            logger.info(
                "Momentum rebalance: exits={e} entries={n} ts={t}",
                e=sorted(to_exit),
                n=sorted(to_enter),
                t=now.isoformat(),
            )
        return signals

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
