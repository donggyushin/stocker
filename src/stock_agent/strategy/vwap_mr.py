"""VWAP mean-reversion 전략.

책임 범위
- 세션 시작(`session_start`) 이후 누적 거래량 가중 평균가(VWAP) 산출.
- 분봉 close 가 `vwap × (1 - threshold_pct)` 이하로 이탈할 때 long 진입 시그널.
- 진입 후 손절(-stop_loss_pct) · 익절(+take_profit_pct) · VWAP 회귀(bar.high ≥ vwap) ·
  강제청산(`force_close_at`) 중 먼저 성립하는 쪽으로 청산. 동일 분봉에서 손절·
  익절·회귀가 동시 성립 시 **손절 우선** (보수적 — 슬리피지 과소평가 방지).
- per-symbol 상태 머신. 세션 경계(`bar.bar_time.date()`) 자동 전환.
- 1일 1심볼 1회 진입 — 청산(`closed`) 이후 당일 재진입 금지.

설계 메모 (ADR-0019 Step E PR2)
- ORB 와 동일한 `Strategy` Protocol 구현체. `BacktestConfig.strategy_factory` 로
  주입해 백테스트·실전 양쪽에서 ORB 와 직교 비교.
- VWAP 누적은 거래량(volume) 이 양수인 bar 만 반영 — 한국 시장 분봉의 일부
  거래 정지·휴식 구간 대비 안전망.
- 청산 가격 우선순위: stop_price (선) → take_price (후) → vwap (회귀). 회귀 청산
  가격은 vwap 자체 — executor 가 실체결가로 덮어쓰는 것을 전제.

에러 정책 (orb.py 와 동일 기조)
- `RuntimeError` 전파 — 잘못된 symbol, naive datetime, 시간 역행, 설정 위반.
- 그 외 `Exception` 은 `StrategyError` 로 래핑 + loguru `exception` 로그.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, DecimalException
from typing import Literal

from loguru import logger

from stock_agent.data import MinuteBar
from stock_agent.strategy.base import EntrySignal, ExitSignal, Signal
from stock_agent.strategy.orb import StrategyError

_SYMBOL_RE = re.compile(r"^\d{6}$")

_DEFAULT_SESSION_START = time(9, 0)
_DEFAULT_FORCE_CLOSE_AT = time(15, 0)
_DEFAULT_THRESHOLD_PCT = Decimal("0.01")
_DEFAULT_TAKE_PROFIT_PCT = Decimal("0.005")
_DEFAULT_STOP_LOSS_PCT = Decimal("0.015")

PositionState = Literal["flat", "long", "closed"]


@dataclass(frozen=True, slots=True)
class VWAPMRConfig:
    """VWAP mean-reversion 파라미터.

    `session_start` / `force_close_at` 은 naive `datetime.time` (KST 기준 암묵
    해석). `MinuteBar.bar_time.time()` 이 naive time 을 반환하므로 일관성.

    Raises:
        RuntimeError: `threshold_pct ≤ 0`, `take_profit_pct ≤ 0`,
            `stop_loss_pct ≤ 0`, `session_start ≥ force_close_at` 일 때.
    """

    session_start: time = _DEFAULT_SESSION_START
    force_close_at: time = _DEFAULT_FORCE_CLOSE_AT
    threshold_pct: Decimal = _DEFAULT_THRESHOLD_PCT
    take_profit_pct: Decimal = _DEFAULT_TAKE_PROFIT_PCT
    stop_loss_pct: Decimal = _DEFAULT_STOP_LOSS_PCT

    def __post_init__(self) -> None:
        if self.threshold_pct <= 0:
            raise RuntimeError(f"threshold_pct 는 양수여야 합니다 (got={self.threshold_pct})")
        if self.take_profit_pct <= 0:
            raise RuntimeError(f"take_profit_pct 는 양수여야 합니다 (got={self.take_profit_pct})")
        if self.stop_loss_pct <= 0:
            raise RuntimeError(f"stop_loss_pct 는 양수여야 합니다 (got={self.stop_loss_pct})")
        if self.session_start >= self.force_close_at:
            raise RuntimeError(
                f"session_start({self.session_start}) 는 "
                f"force_close_at({self.force_close_at}) 보다 이전이어야 합니다."
            )


@dataclass
class _SymbolState:
    """심볼별 상태. 세션 단위로 `reset()` 된다."""

    session_date: date | None = None
    sum_pv: Decimal = Decimal("0")  # ∑(close × volume)
    sum_v: Decimal = Decimal("0")  # ∑volume
    vwap: Decimal | None = None
    position_state: PositionState = "flat"
    entry_price: Decimal | None = None
    stop_price: Decimal | None = None
    take_price: Decimal | None = None
    last_bar_time: datetime | None = None
    last_close: Decimal | None = None

    def reset(self, session_date: date) -> None:
        self.session_date = session_date
        self.sum_pv = Decimal("0")
        self.sum_v = Decimal("0")
        self.vwap = None
        self.position_state = "flat"
        self.entry_price = None
        self.stop_price = None
        self.take_price = None
        self.last_bar_time = None
        self.last_close = None


class VWAPMRStrategy:
    """VWAP mean-reversion 규칙 엔진. `Strategy` Protocol 구현체.

    공개 API: `on_bar`, `on_time`, `config` (프로퍼티), `get_state` (디버깅용).

    동일 호출자 스레드에서 순차 호출 가정 — 동시 호출 미지원.
    """

    def __init__(self, config: VWAPMRConfig | None = None) -> None:
        self._config = config or VWAPMRConfig()
        self._states: dict[str, _SymbolState] = {}

    @property
    def config(self) -> VWAPMRConfig:
        return self._config

    def get_state(self, symbol: str) -> _SymbolState | None:
        return self._states.get(symbol)

    # ---- on_bar --------------------------------------------------------

    def on_bar(self, bar: MinuteBar) -> list[Signal]:
        try:
            self._validate_symbol(bar.symbol)
            self._require_aware(bar.bar_time, "bar.bar_time")

            state = self._states.setdefault(bar.symbol, _SymbolState())
            session = bar.bar_time.date()

            if state.session_date is None or state.session_date != session:
                state.reset(session)

            if state.last_bar_time is not None and bar.bar_time < state.last_bar_time:
                raise RuntimeError(
                    f"bar.bar_time 역행 감지 ({bar.symbol}): "
                    f"last={state.last_bar_time.isoformat()}, "
                    f"now={bar.bar_time.isoformat()}"
                )
            state.last_bar_time = bar.bar_time
            state.last_close = bar.close

            return self._dispatch_bar(state, bar)
        except (RuntimeError, StrategyError):
            raise
        except DecimalException as e:
            logger.exception(f"VWAP MR on_bar Decimal 연산 실패 ({bar.symbol})")
            raise StrategyError(f"VWAP MR on_bar Decimal 연산 실패 ({bar.symbol}): {e}") from e

    def _dispatch_bar(self, state: _SymbolState, bar: MinuteBar) -> list[Signal]:
        cfg = self._config
        bar_t = bar.bar_time.time()

        if bar_t < cfg.session_start:
            return []

        # 진입/청산 판정 — VWAP 갱신 **전** 의 vwap 으로 비교한다. 자기 자신을
        # 누적 평균에 포함시키면 이탈률이 자동 축소되어 mean-reversion 신호가
        # 약해진다 (분봉 종료 시점 기준).
        signals: list[Signal] = []
        if state.position_state == "flat":
            if bar_t >= cfg.force_close_at:
                logger.debug(
                    f"VWAP MR 진입 스킵 (force_close_at 이후): {bar.symbol} "
                    f"@ bar_t={bar_t} (force_close_at={cfg.force_close_at})"
                )
            elif state.vwap is not None:
                entry_threshold = state.vwap * (Decimal("1") - cfg.threshold_pct)
                if bar.close <= entry_threshold:
                    signals.append(self._enter_long(state, bar))
        elif state.position_state == "long":
            exit_signal = self._check_exit(state, bar)
            if exit_signal is not None:
                signals.append(exit_signal)
        else:
            # "closed" — 당일 재진입 금지.
            logger.debug(
                f"VWAP MR 재진입 스킵 ({bar.symbol}): 당일 청산 완료 상태 "
                f"(bar_t={bar_t}, close={bar.close})"
            )

        # VWAP 누적 — 분기 판정 후 갱신해 다음 분봉이 갱신된 vwap 사용.
        # volume 양수 bar 만 반영 (거래 정지·휴식 구간 안전망).
        if bar.volume > 0:
            state.sum_pv += bar.close * Decimal(bar.volume)
            state.sum_v += Decimal(bar.volume)
            state.vwap = state.sum_pv / state.sum_v

        return signals

    def _enter_long(self, state: _SymbolState, bar: MinuteBar) -> EntrySignal:
        cfg = self._config
        entry = bar.close
        stop = entry * (Decimal("1") - cfg.stop_loss_pct)
        take = entry * (Decimal("1") + cfg.take_profit_pct)

        state.position_state = "long"
        state.entry_price = entry
        state.stop_price = stop
        state.take_price = take

        logger.info(
            f"VWAP MR 진입: {bar.symbol} @ {entry} "
            f"(vwap={state.vwap}, stop={stop}, take={take}, "
            f"ts={bar.bar_time.isoformat()})"
        )
        return EntrySignal(
            symbol=bar.symbol,
            price=entry,
            ts=bar.bar_time,
            stop_price=stop,
            take_price=take,
        )

    def _check_exit(self, state: _SymbolState, bar: MinuteBar) -> ExitSignal | None:
        if state.stop_price is None or state.take_price is None:
            raise StrategyError(
                f"long 상태인데 stop_price/take_price 미세팅 ({bar.symbol}) — "
                "상태 머신 무결성 오류 (_enter_long 호출 누락 가능성)"
            )

        if bar.low <= state.stop_price:
            state.position_state = "closed"
            logger.info(
                f"VWAP MR 손절: {bar.symbol} @ {state.stop_price} "
                f"(low={bar.low}, stop={state.stop_price}, "
                f"ts={bar.bar_time.isoformat()})"
            )
            return ExitSignal(
                symbol=bar.symbol,
                price=state.stop_price,
                ts=bar.bar_time,
                reason="stop_loss",
            )

        if bar.high >= state.take_price:
            state.position_state = "closed"
            logger.info(
                f"VWAP MR 익절(target): {bar.symbol} @ {state.take_price} "
                f"(high={bar.high}, take={state.take_price}, "
                f"ts={bar.bar_time.isoformat()})"
            )
            return ExitSignal(
                symbol=bar.symbol,
                price=state.take_price,
                ts=bar.bar_time,
                reason="take_profit",
            )

        # VWAP 회귀 청산 — 진입가 회복 안 했더라도 평균가 통과 시 mean-reversion 종료.
        if state.vwap is not None and bar.high >= state.vwap:
            state.position_state = "closed"
            logger.info(
                f"VWAP MR 익절(회귀): {bar.symbol} @ {state.vwap} "
                f"(high={bar.high}, vwap={state.vwap}, "
                f"ts={bar.bar_time.isoformat()})"
            )
            return ExitSignal(
                symbol=bar.symbol,
                price=state.vwap,
                ts=bar.bar_time,
                reason="take_profit",
            )

        return None

    # ---- on_time -------------------------------------------------------

    def on_time(self, now: datetime) -> list[Signal]:
        """`force_close_at` 이후 long 심볼 강제청산.

        가격: `last_close` 우선, 없으면 `entry_price` 폴백 + warning. 둘 다
        None 이면 `StrategyError` (long 상태에서 도달 불가).
        """
        self._require_aware(now, "now")
        cfg = self._config
        if now.time() < cfg.force_close_at:
            return []

        signals: list[Signal] = []
        for symbol, state in self._states.items():
            if state.position_state != "long":
                continue
            if state.last_close is not None:
                price = state.last_close
            elif state.entry_price is not None:
                logger.warning(
                    f"VWAP MR 강제청산: {symbol} last_close 없음 → entry_price 폴백 "
                    f"(price={state.entry_price}). 데이터 파이프라인 이상 가능성."
                )
                price = state.entry_price
            else:
                raise StrategyError(
                    f"VWAP MR 강제청산 시점에 {symbol} 의 last_close·entry_price 모두 None "
                    "— 상태 머신 무결성 오류 (long 상태에서 발생 불가)"
                )
            state.position_state = "closed"
            logger.info(f"VWAP MR 강제청산: {symbol} @ {price} (ts={now.isoformat()})")
            signals.append(
                ExitSignal(
                    symbol=symbol,
                    price=price,
                    ts=now,
                    reason="force_close",
                )
            )
        return signals

    # ---- 공통 가드 -----------------------------------------------------

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
