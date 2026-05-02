"""Low Volatility 전략 (ADR-0019 Step F PR4 — F4).

KOSPI 200 종목 중 직전 ``lookback_days`` 영업일 일별 수익률 표준편차 하위
``top_n`` 종목을 보유하는 multi-symbol 전략. 분기 (또는
``rebalance_month_interval`` 개월) 단위 리밸런싱.

설계 결정
- ``on_bar``: universe 종목 close 를 per-symbol rolling deque 에 누적. 시그널 X.
- ``on_time(now)``: 직전 리밸런싱 시점과 다른 주기 (``period_index`` 변경) 일 때
  리밸런싱 트리거. lookback 충족 후보 수가 ``top_n`` 미만이면 보류
  (``last_rebalance_period`` 미갱신 — 다음 호출에서 재시도). 그 외엔
  ``ExitSignal`` · ``EntrySignal`` 다중 emit 후 holdings 갱신 +
  ``last_rebalance_period`` 갱신.

Ranking metric: ``pstdev(daily_returns)`` 오름차순 (저변동성 → 상위).

``Strategy`` Protocol 호환. force_close 가정 없음 — ``on_time`` 은 리밸런싱 전용.

체결가 계약
- ``EntrySignal.stop_price = take_price = Decimal("0")`` 마커 — 손익절 미사용.
  호출자 (``compute_low_volatility_baseline``) 가 마커를 인지해 손익절 판정 건너뜀.

세션 경계 정책
- 일봉 전략 — buffer 자동 리셋 없음 (Momentum 과 동일 기조).

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
from statistics import pstdev

from loguru import logger

from stock_agent.data import MinuteBar
from stock_agent.strategy.base import EntrySignal, ExitSignal, Signal

_SYMBOL_RE = re.compile(r"^\d{6}$")

_DEFAULT_LOOKBACK_DAYS = 60
_DEFAULT_TOP_N = 20
_DEFAULT_REBALANCE_MONTH_INTERVAL = 3
_DEFAULT_POSITION_PCT = Decimal("1.0")


@dataclass(frozen=True, slots=True)
class LowVolConfig:
    """Low Volatility 파라미터.

    Raises:
        RuntimeError: ``universe`` 빈 tuple / 6자리 정규식 위반 / 중복,
            ``lookback_days <= 0``, ``top_n`` 가 ``[1, len(universe)]`` 범위 밖,
            ``rebalance_month_interval`` 가 ``[1, 12]`` 범위 밖,
            ``position_pct`` 가 ``(0, 1]`` 범위 밖.
    """

    universe: tuple[str, ...]
    lookback_days: int = _DEFAULT_LOOKBACK_DAYS
    top_n: int = _DEFAULT_TOP_N
    rebalance_month_interval: int = _DEFAULT_REBALANCE_MONTH_INTERVAL
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
        if self.lookback_days <= 0:
            raise RuntimeError(f"lookback_days 는 양수여야 합니다 (got={self.lookback_days})")
        if self.top_n < 1:
            raise RuntimeError(f"top_n 은 1 이상이어야 합니다 (got={self.top_n})")
        # 사용자 명시 값일 때만 universe 길이 검증. 기본값은 작은 universe 테스트 편의로 허용.
        if self.top_n != _DEFAULT_TOP_N and self.top_n > len(self.universe):
            raise RuntimeError(
                f"top_n 은 universe 길이 이하여야 합니다 "
                f"(top_n={self.top_n}, universe_size={len(self.universe)})"
            )
        if self.rebalance_month_interval < 1 or self.rebalance_month_interval > 12:
            raise RuntimeError(
                f"rebalance_month_interval 는 1~12 범위여야 합니다 "
                f"(got={self.rebalance_month_interval})"
            )
        if self.position_pct <= 0 or self.position_pct > 1:
            raise RuntimeError(f"position_pct 는 (0, 1] 범위여야 합니다 (got={self.position_pct})")


class LowVolStrategy:
    """Low Volatility 전략. ``Strategy`` Protocol 구현체.

    공개 API: ``on_bar``, ``on_time``, ``config`` (프로퍼티).

    동일 호출자 스레드 순차 호출 가정. 동시 호출 미지원.
    """

    def __init__(self, config: LowVolConfig | None = None) -> None:
        if config is None:
            raise RuntimeError("LowVolConfig 는 필수입니다 (universe 지정 필요).")
        self._config = config
        self._lookback_days = config.lookback_days
        self._closes: dict[str, deque[Decimal]] = {
            sym: deque(maxlen=self._lookback_days) for sym in config.universe
        }
        self._holdings: set[str] = set()
        self._last_rebalance_period: int | None = None
        self._last_bar_time: dict[str, datetime] = {}

    @property
    def config(self) -> LowVolConfig:
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
        """시각 이벤트 — 분기 (또는 ``rebalance_month_interval`` 개월) 변경 시
        리밸런싱 시그널 다중 emit."""
        self._require_aware(now, "now")
        current_period = self._period_index(now)
        if self._last_rebalance_period == current_period:
            return []

        candidates: list[tuple[str, Decimal, Decimal]] = []
        for sym in self._config.universe:
            buf = self._closes[sym]
            if len(buf) < self._lookback_days:
                continue
            stdev = self._daily_return_stdev(buf)
            if stdev is None:
                continue
            last = buf[-1]
            candidates.append((sym, stdev, last))

        if len(candidates) < self._config.top_n:
            return []

        candidates.sort(key=lambda x: (x[1], x[0]))  # stdev asc, symbol asc tiebreaker
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
        self._last_rebalance_period = current_period
        if signals:
            logger.info(
                "LowVol rebalance: exits={e} entries={n} ts={t}",
                e=sorted(to_exit),
                n=sorted(to_enter),
                t=now.isoformat(),
            )
        return signals

    def _period_index(self, now: datetime) -> int:
        """``rebalance_month_interval`` 개월 단위 주기 인덱스 — 동일 인덱스면 같은 분기."""
        absolute_month = now.year * 12 + now.month - 1
        return absolute_month // self._config.rebalance_month_interval

    @staticmethod
    def _daily_return_stdev(buf: deque[Decimal]) -> Decimal | None:
        """버퍼의 일별 수익률 모집단 표준편차 (Decimal)."""
        if len(buf) < 2:
            return None
        returns: list[Decimal] = []
        prev = buf[0]
        for cur in list(buf)[1:]:
            if prev <= 0:
                prev = cur
                continue
            returns.append((cur - prev) / prev)
            prev = cur
        if len(returns) < 2:
            return None
        return Decimal(str(pstdev(returns)))

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
