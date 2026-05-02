"""RSI 평균회귀 전략 (ADR-0019 Step F PR5 — F5).

Multi-symbol 일봉 RSI(14) 평균회귀 전략. RSI < ``oversold_threshold`` (기본 30)
시 진입, RSI > ``overbought_threshold`` (기본 70) 또는 ``stop_loss_pct``
도달 시 청산.

설계 결정
- ``on_bar``: per-symbol close 누적. RSI 계산 후 시그널 emit
  (Entry/Exit/Stop-loss).
- ``on_time``: 항상 빈 리스트 (강제청산 없음, 일봉 전략).

RSI 계산 — simple average gain/loss 방식 (Wilder smoothing 미사용)
- ``gains[i] = max(close[i] - close[i-1], 0)``
- ``losses[i] = max(close[i-1] - close[i], 0)``
- ``avg_gain = sum(gains[-period:]) / period``
- ``avg_loss = sum(losses[-period:]) / period``
- ``avg_loss == 0`` 이면 ``RSI = 100``
- 그 외 ``RS = avg_gain / avg_loss``, ``RSI = 100 - 100 / (1 + RS)``

체결가 계약
- ``EntrySignal.stop_price = close × (1 - stop_loss_pct)`` — bar.low ≤
  stop_price 시 stop_loss 청산.
- ``EntrySignal.take_price = Decimal("0")`` 마커 — 고정 익절 미사용. RSI
  회귀(overbought) 로만 take_profit 청산. 호출자
  (``compute_rsi_mr_baseline``) 가 마커 인지.

청산 우선순위 — 동일 bar 동시 발화 시 stop_loss 우선 (슬리피지 과소평가
방지, ORB · VWAP-MR · GapReversal 와 동일 기조).

세션 경계
- 일봉 전략 — buffer 자동 리셋 없음. RSI 시계열은 연속.

에러 정책 (broker/data/strategy 와 동일 기조)
- ``RuntimeError`` 전파 — config 위반·naive datetime·symbol 정규식 위반·시간 역행.

스레드 모델
- 단일 프로세스 전용.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from loguru import logger

from stock_agent.data import MinuteBar
from stock_agent.strategy.base import EntrySignal, ExitSignal, Signal

_SYMBOL_RE = re.compile(r"^\d{6}$")

_DEFAULT_RSI_PERIOD = 14
_DEFAULT_OVERSOLD = Decimal("30")
_DEFAULT_OVERBOUGHT = Decimal("70")
_DEFAULT_STOP_LOSS_PCT = Decimal("0.03")
_DEFAULT_MAX_POSITIONS = 10
_DEFAULT_POSITION_PCT = Decimal("1.0")


@dataclass(frozen=True, slots=True)
class RSIMRConfig:
    """RSI 평균회귀 파라미터.

    Raises:
        RuntimeError: ``universe`` 빈 tuple / 6자리 정규식 위반 / 중복,
            ``rsi_period <= 0``, ``oversold_threshold`` 가 ``(0, 50)``
            범위 밖, ``overbought_threshold`` 가 ``(50, 100)`` 범위 밖,
            ``oversold >= overbought``, ``stop_loss_pct`` 가 ``(0, 1)``
            범위 밖, ``max_positions < 1``, 사용자 명시 ``max_positions
            > len(universe)``, ``position_pct`` 가 ``(0, 1]`` 범위 밖.
    """

    universe: tuple[str, ...]
    rsi_period: int = _DEFAULT_RSI_PERIOD
    oversold_threshold: Decimal = _DEFAULT_OVERSOLD
    overbought_threshold: Decimal = _DEFAULT_OVERBOUGHT
    stop_loss_pct: Decimal = _DEFAULT_STOP_LOSS_PCT
    max_positions: int = _DEFAULT_MAX_POSITIONS
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
        if self.rsi_period <= 0:
            raise RuntimeError(f"rsi_period 는 양수여야 합니다 (got={self.rsi_period})")
        if self.oversold_threshold <= 0 or self.oversold_threshold >= 50:
            raise RuntimeError(
                f"oversold_threshold 는 (0, 50) 범위여야 합니다 (got={self.oversold_threshold})"
            )
        if self.overbought_threshold <= 50 or self.overbought_threshold >= 100:
            raise RuntimeError(
                f"overbought_threshold 는 (50, 100) 범위여야 합니다 "
                f"(got={self.overbought_threshold})"
            )
        if self.oversold_threshold >= self.overbought_threshold:
            raise RuntimeError(
                f"oversold_threshold({self.oversold_threshold}) 는 "
                f"overbought_threshold({self.overbought_threshold}) 보다 작아야 합니다."
            )
        if self.stop_loss_pct <= 0 or self.stop_loss_pct >= 1:
            raise RuntimeError(
                f"stop_loss_pct 는 (0, 1) 범위여야 합니다 (got={self.stop_loss_pct})"
            )
        if self.max_positions < 1:
            raise RuntimeError(f"max_positions 은 1 이상이어야 합니다 (got={self.max_positions})")
        if self.max_positions != _DEFAULT_MAX_POSITIONS and self.max_positions > len(self.universe):
            raise RuntimeError(
                f"max_positions 은 universe 길이 이하여야 합니다 "
                f"(max_positions={self.max_positions}, universe_size={len(self.universe)})"
            )
        if self.position_pct <= 0 or self.position_pct > 1:
            raise RuntimeError(f"position_pct 는 (0, 1] 범위여야 합니다 (got={self.position_pct})")


@dataclass(slots=True)
class _Holding:
    """per-symbol 보유 상태 — 진입가·손절가 추적."""

    entry_price: Decimal
    stop_price: Decimal


class RSIMRStrategy:
    """RSI 평균회귀 전략. ``Strategy`` Protocol 구현체.

    공개 API: ``on_bar``, ``on_time``, ``config`` (프로퍼티).

    동일 호출자 스레드 순차 호출 가정. 동시 호출 미지원.
    """

    def __init__(self, config: RSIMRConfig | None = None) -> None:
        if config is None:
            raise RuntimeError("RSIMRConfig 는 필수입니다 (universe 지정 필요).")
        self._config = config
        # close 누적 — RSI(period) 계산은 period+1 개 close 필요.
        # 약간 여유로 period+2 maxlen 사용 (이전 값 유지로 diff 계산 안전).
        maxlen = config.rsi_period + 1
        self._closes: dict[str, deque[Decimal]] = {
            sym: deque(maxlen=maxlen) for sym in config.universe
        }
        self._holdings: dict[str, _Holding] = {}
        self._last_bar_time: dict[str, datetime] = {}
        # 동일 세션 (date) 내 청산 후 재진입 방지 — RSI 가 즉시 회복되어
        # 무한 entry/exit 루프를 막는다.
        self._last_exit_date: dict[str, date] = {}

    @property
    def config(self) -> RSIMRConfig:
        return self._config

    def on_bar(self, bar: MinuteBar) -> list[Signal]:
        """분봉 이벤트 — universe 종목 close 누적 + RSI 시그널 emit."""
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

        signals: list[Signal] = []

        holding = self._holdings.get(bar.symbol)
        # 보유 중 stop_loss 우선 판정 (슬리피지 과소평가 방지)
        if holding is not None and bar.low <= holding.stop_price:
            signals.append(
                ExitSignal(
                    symbol=bar.symbol,
                    price=holding.stop_price,
                    ts=bar.bar_time,
                    reason="stop_loss",
                )
            )
            del self._holdings[bar.symbol]
            self._last_exit_date[bar.symbol] = bar.bar_time.date()
            self._closes[bar.symbol].append(bar.close)
            logger.info(
                "RSI-MR stop_loss: {s} stop={sp} low={lo}",
                s=bar.symbol,
                sp=holding.stop_price,
                lo=bar.low,
            )
            return signals

        # close 누적 (stop_loss 분기 전에 누적하면 RSI 가 청산 bar 까지 포함되어
        # 다음 bar 의 진입 판단에 영향. stop_loss 시 위에서 별도 처리 후 return.)
        self._closes[bar.symbol].append(bar.close)
        rsi = self._compute_rsi(bar.symbol)

        if holding is not None:
            # take_profit 판정 — RSI > overbought
            if rsi is not None and rsi > self._config.overbought_threshold:
                signals.append(
                    ExitSignal(
                        symbol=bar.symbol,
                        price=bar.close,
                        ts=bar.bar_time,
                        reason="take_profit",
                    )
                )
                del self._holdings[bar.symbol]
                self._last_exit_date[bar.symbol] = bar.bar_time.date()
                logger.info(
                    "RSI-MR take_profit: {s} rsi={r} close={c}",
                    s=bar.symbol,
                    r=rsi,
                    c=bar.close,
                )
            return signals

        # 미보유 — RSI < oversold AND len(holdings) < max_positions
        if rsi is None:
            return signals
        if rsi >= self._config.oversold_threshold:
            return signals
        if len(self._holdings) >= self._config.max_positions:
            logger.debug(
                "RSI-MR entry skip: max_positions 한도 (sym={s}, held={h})",
                s=bar.symbol,
                h=len(self._holdings),
            )
            return signals
        # 동일 세션 재진입 차단 — 청산 직후 RSI 가 여전히 oversold 인 경우
        # 같은 날 다시 진입하지 않는다.
        if self._last_exit_date.get(bar.symbol) == bar.bar_time.date():
            logger.debug(
                "RSI-MR entry skip: 동일 세션 재진입 차단 (sym={s}, date={d})",
                s=bar.symbol,
                d=bar.bar_time.date().isoformat(),
            )
            return signals

        stop_price = bar.close * (Decimal("1") - self._config.stop_loss_pct)
        signals.append(
            EntrySignal(
                symbol=bar.symbol,
                price=bar.close,
                ts=bar.bar_time,
                stop_price=stop_price,
                take_price=Decimal("0"),
            )
        )
        self._holdings[bar.symbol] = _Holding(entry_price=bar.close, stop_price=stop_price)
        logger.info(
            "RSI-MR entry: {s} rsi={r} close={c} stop={sp}",
            s=bar.symbol,
            r=rsi,
            c=bar.close,
            sp=stop_price,
        )
        return signals

    def on_time(self, now: datetime) -> list[Signal]:
        """시각 이벤트 — RSI 전략은 강제청산 없음. 항상 빈 리스트 + naive 가드."""
        self._require_aware(now, "now")
        return []

    def _compute_rsi(self, symbol: str) -> Decimal | None:
        """현재 buffer 기준 RSI(rsi_period) 계산. lookback 부족 시 ``None``."""
        buf = self._closes[symbol]
        period = self._config.rsi_period
        if len(buf) < period + 1:
            return None
        closes = list(buf)
        gains = Decimal("0")
        losses = Decimal("0")
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            if diff > 0:
                gains += diff
            else:
                losses += -diff
        period_dec = Decimal(period)
        avg_gain = gains / period_dec
        avg_loss = losses / period_dec
        if avg_loss == 0:
            return Decimal("100")
        rs = avg_gain / avg_loss
        return Decimal("100") - Decimal("100") / (Decimal("1") + rs)

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
