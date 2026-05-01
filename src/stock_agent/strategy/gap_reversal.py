"""Opening gap reversal 전략.

책임 범위
- 새 세션 첫 분봉의 시가(`session_open`) 와 전일 종가(`prev_close`) 차이로
  갭 비율(`gap_pct`) 산출.
- `gap_pct ≤ -gap_threshold_pct` (갭 하락) 시 진입 윈도(`session_start ≤ bar_t
  < entry_window_end`) 안에서 long 매수 시그널 (반대 방향 — 반등 가설).
- long 진입 후 손절(-stop_loss_pct) · 익절(+take_profit_pct) · 강제청산
  (`force_close_at`) 중 먼저 성립하는 쪽으로 청산. 동일 분봉에서 손절·익절
  동시 성립 시 **손절 우선** (보수적 — 슬리피지 과소평가 방지).
- per-symbol 상태 머신. 세션 경계(`bar.bar_time.date()`) 자동 전환.
- 1일 1심볼 1회 진입 — `closed` 이후 당일 재진입 금지.
- 갭 평가 1회 — 첫 평가 후 `gap_evaluated=True` 가드, 같은 세션 내 재평가 없음.

설계 메모 (ADR-0019 Step E PR3)
- ORB · VWAPMR 과 동일한 `Strategy` Protocol 구현체. `BacktestConfig.strategy_factory`
  로 주입.
- **long-only 정책** — KOSPI 200 + KIS API 공매도 미지원으로 갭 상승 후 매도
  (mean-reversion) 평가 불가. 갭 하락 후 매수만 검증.
- 전일 종가 의존 주입 — `prev_close_provider: Callable[[symbol, session_date],
  Decimal | None]`. 백테스트는 `HistoricalDataStore.DailyBar` + `BusinessDayCalendar`
  (ADR-0018) 조합으로 주입. None 반환 시 당일 진입 포기.
- `StrategyError` 는 `orb.py` 의 것을 재사용.

에러 정책 (orb.py / vwap_mr.py 와 동일 기조)
- `RuntimeError` 전파 — 잘못된 symbol, naive datetime, 시간 역행, 설정 위반.
- 그 외 `Exception` 은 `StrategyError` 로 래핑 + loguru `exception` 로그.
"""

from __future__ import annotations

import re
from collections.abc import Callable
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
_DEFAULT_ENTRY_WINDOW_END = time(9, 30)
_DEFAULT_FORCE_CLOSE_AT = time(15, 0)
_DEFAULT_GAP_THRESHOLD_PCT = Decimal("0.02")
_DEFAULT_TAKE_PROFIT_PCT = Decimal("0.015")
_DEFAULT_STOP_LOSS_PCT = Decimal("0.01")

PositionState = Literal["flat", "long", "closed"]

PrevCloseProvider = Callable[[str, date], "Decimal | None"]


@dataclass(frozen=True, slots=True)
class GapReversalConfig:
    """Opening gap reversal 파라미터.

    `session_start` / `entry_window_end` / `force_close_at` 은 naive
    `datetime.time` (KST 기준 암묵 해석). `MinuteBar.bar_time.time()` 이 naive
    time 을 반환하므로 일관성.

    Raises:
        RuntimeError: `gap_threshold_pct ≤ 0`, `take_profit_pct ≤ 0`,
            `stop_loss_pct ≤ 0`, `session_start ≥ entry_window_end`,
            `entry_window_end ≥ force_close_at` 일 때.
    """

    session_start: time = _DEFAULT_SESSION_START
    entry_window_end: time = _DEFAULT_ENTRY_WINDOW_END
    force_close_at: time = _DEFAULT_FORCE_CLOSE_AT
    gap_threshold_pct: Decimal = _DEFAULT_GAP_THRESHOLD_PCT
    take_profit_pct: Decimal = _DEFAULT_TAKE_PROFIT_PCT
    stop_loss_pct: Decimal = _DEFAULT_STOP_LOSS_PCT

    def __post_init__(self) -> None:
        if self.gap_threshold_pct <= 0:
            raise RuntimeError(
                f"gap_threshold_pct 는 양수여야 합니다 (got={self.gap_threshold_pct})"
            )
        if self.take_profit_pct <= 0:
            raise RuntimeError(f"take_profit_pct 는 양수여야 합니다 (got={self.take_profit_pct})")
        if self.stop_loss_pct <= 0:
            raise RuntimeError(f"stop_loss_pct 는 양수여야 합니다 (got={self.stop_loss_pct})")
        if self.session_start >= self.entry_window_end:
            raise RuntimeError(
                f"session_start({self.session_start}) 는 "
                f"entry_window_end({self.entry_window_end}) 보다 이전이어야 합니다."
            )
        if self.entry_window_end >= self.force_close_at:
            raise RuntimeError(
                f"entry_window_end({self.entry_window_end}) 는 "
                f"force_close_at({self.force_close_at}) 보다 이전이어야 합니다."
            )


@dataclass
class _SymbolState:
    """심볼별 상태. 세션 단위로 `reset()` 된다."""

    session_date: date | None = None
    prev_close: Decimal | None = None
    session_open: Decimal | None = None
    gap_pct: Decimal | None = None
    gap_evaluated: bool = False
    position_state: PositionState = "flat"
    entry_price: Decimal | None = None
    stop_price: Decimal | None = None
    take_price: Decimal | None = None
    last_bar_time: datetime | None = None
    last_close: Decimal | None = None

    def reset(self, session_date: date) -> None:
        self.session_date = session_date
        self.prev_close = None
        self.session_open = None
        self.gap_pct = None
        self.gap_evaluated = False
        self.position_state = "flat"
        self.entry_price = None
        self.stop_price = None
        self.take_price = None
        self.last_bar_time = None
        self.last_close = None


class GapReversalStrategy:
    """Opening gap reversal 규칙 엔진. `Strategy` Protocol 구현체.

    공개 API: `on_bar`, `on_time`, `config` (프로퍼티), `get_state` (디버깅용).

    의존 주입: `prev_close_provider` — 새 세션 reset 시 1회 호출하여 전일
    종가를 조회한다. None 반환 시 당일 진입 포기 (gap 평가 자체 불가).

    동일 호출자 스레드에서 순차 호출 가정 — 동시 호출 미지원.
    """

    def __init__(
        self,
        prev_close_provider: PrevCloseProvider,
        config: GapReversalConfig | None = None,
    ) -> None:
        self._prev_close_provider = prev_close_provider
        self._config = config or GapReversalConfig()
        self._states: dict[str, _SymbolState] = {}

    @property
    def config(self) -> GapReversalConfig:
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
                state.prev_close = self._prev_close_provider(bar.symbol, session)

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
            logger.exception(f"GapReversal on_bar Decimal 연산 실패 ({bar.symbol})")
            raise StrategyError(f"GapReversal on_bar Decimal 연산 실패 ({bar.symbol}): {e}") from e

    def _dispatch_bar(self, state: _SymbolState, bar: MinuteBar) -> list[Signal]:
        cfg = self._config
        bar_t = bar.bar_time.time()

        if bar_t < cfg.session_start:
            return []

        if state.position_state == "flat":
            return self._maybe_enter(state, bar, bar_t)

        if state.position_state == "long":
            exit_signal = self._check_exit(state, bar)
            return [exit_signal] if exit_signal is not None else []

        # "closed" — 당일 재진입 금지.
        logger.debug(
            f"GapReversal 재진입 스킵 ({bar.symbol}): 당일 청산 완료 상태 "
            f"(bar_t={bar_t}, close={bar.close})"
        )
        return []

    def _maybe_enter(self, state: _SymbolState, bar: MinuteBar, bar_t: time) -> list[Signal]:
        cfg = self._config
        if bar_t >= cfg.force_close_at:
            return []
        if bar_t >= cfg.entry_window_end:
            logger.debug(
                f"GapReversal 진입 스킵 (entry_window_end 이후): {bar.symbol} "
                f"@ bar_t={bar_t} (entry_window_end={cfg.entry_window_end})"
            )
            return []
        if state.prev_close is None:
            logger.debug(f"GapReversal 진입 스킵 (prev_close 부재): {bar.symbol} @ bar_t={bar_t}")
            return []
        if state.gap_evaluated:
            return []

        # 첫 평가 — session_open 확정, gap_pct 계산.
        state.session_open = bar.open
        state.gap_pct = (bar.open - state.prev_close) / state.prev_close
        state.gap_evaluated = True

        logger.info(
            f"GapReversal 갭 평가: {bar.symbol} session_open={state.session_open} "
            f"prev_close={state.prev_close} gap_pct={state.gap_pct} "
            f"(threshold=-{cfg.gap_threshold_pct})"
        )

        # long-only — 갭 하락 (음수) 만 매수.
        if state.gap_pct <= -cfg.gap_threshold_pct:
            return [self._enter_long(state, bar)]

        logger.debug(
            f"GapReversal 진입 거부 (갭 조건 미달): {bar.symbol} "
            f"gap_pct={state.gap_pct} threshold=-{cfg.gap_threshold_pct}"
        )
        return []

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
            f"GapReversal 진입: {bar.symbol} @ {entry} "
            f"(gap_pct={state.gap_pct}, stop={stop}, take={take}, "
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
                f"GapReversal 손절: {bar.symbol} @ {state.stop_price} "
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
                f"GapReversal 익절: {bar.symbol} @ {state.take_price} "
                f"(high={bar.high}, take={state.take_price}, "
                f"ts={bar.bar_time.isoformat()})"
            )
            return ExitSignal(
                symbol=bar.symbol,
                price=state.take_price,
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
                    f"GapReversal 강제청산: {symbol} last_close 없음 → entry_price "
                    f"폴백 (price={state.entry_price}). 데이터 파이프라인 이상 가능성."
                )
                price = state.entry_price
            else:
                raise StrategyError(
                    f"GapReversal 강제청산 시점에 {symbol} 의 last_close·entry_price "
                    "모두 None — 상태 머신 무결성 오류 (long 상태에서 발생 불가)"
                )
            state.position_state = "closed"
            logger.info(f"GapReversal 강제청산: {symbol} @ {price} (ts={now.isoformat()})")
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
