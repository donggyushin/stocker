"""Opening Range Breakout 전략 구현.

책임 범위
- OR 구간(09:00~09:30 KST) 분봉에서 고가(OR-High)·저가(OR-Low) 확정.
- OR 확정 이후 분봉 close 가 OR-High 상향 돌파 시 long 진입 시그널 생성.
- 진입 이후 손절(-1.5%) · 익절(+3.0%) · 강제청산(15:00) 중 먼저 성립하는 쪽으로
  청산 시그널 생성. 동일 분봉에서 손절·익절이 함께 성립하면 **손절 우선**
  (보수적 — 슬리피지 과소평가 방지).
- per-symbol 상태 머신. 세션 경계(`bar.bar_time.date()`) 는 자동 전환.
- 1일 1심볼 최대 1회 진입 — 청산(`closed`) 이후 당일 재진입 금지.

범위 제외 (의도적)
- 포지션 사이징·자금 관리 — `risk/manager.py` 책임.
- 주문 실행·체결 추적 — `execution/executor.py` (Phase 3).
- 거래대금/유동성 필터 — `MinuteBar.volume=0 고정` 제약으로 본 모듈 범위 밖.
- 틱 기반 진입(`on_tick`) — 필요 시 Phase 3 에서 `Strategy` Protocol 확장.

에러 정책 (broker/data 와 동일 기조)
- `RuntimeError` 는 전파 — 잘못된 symbol, naive datetime, 시간 역행, 설정 위반.
- 그 외 `Exception` 은 `StrategyError` 로 래핑 + loguru `exception` 로그.
  원본 예외는 `__cause__` 로 보존.

스레드 모델
- 단일 프로세스 전용. `on_bar`/`on_time` 은 동일 호출자 스레드에서 순차 호출을
  가정. 동시 호출이 필요해지면 Phase 5 재설계 범위.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, DecimalException
from typing import Literal

from loguru import logger

from stock_agent.data import MinuteBar
from stock_agent.strategy.base import EntrySignal, ExitSignal, Signal

_SYMBOL_RE = re.compile(r"^\d{6}$")

_DEFAULT_OR_START = time(9, 0)
_DEFAULT_OR_END = time(9, 30)
_DEFAULT_FORCE_CLOSE_AT = time(15, 0)
_DEFAULT_STOP_LOSS_PCT = Decimal("0.015")
_DEFAULT_TAKE_PROFIT_PCT = Decimal("0.030")

PositionState = Literal["flat", "long", "closed"]


class StrategyError(Exception):
    """ORB 상태 머신 처리 중 발생한 예기치 못한 오류.

    사용자 수정이 필요한 입력 오류(`RuntimeError`) 와 구분. 원본 예외는
    `__cause__` 로 보존된다 (`raise ... from e`).
    """


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    """ORB 파라미터. 모든 값은 생성자 인자로 재정의 가능.

    `or_start`/`or_end`/`force_close_at` 은 naive `datetime.time` 이다
    (KST 기준 암묵적 해석). `MinuteBar.bar_time` 은 KST aware datetime 이지만
    `.time()` 은 tzinfo 미포함 naive time 을 반환하므로 naive 끼리 비교가
    안전·단순하다.

    Raises:
        RuntimeError: `stop_loss_pct ≤ 0`, `take_profit_pct ≤ 0`,
            `or_start ≥ or_end`, `or_end ≥ force_close_at` 일 때.
    """

    or_start: time = _DEFAULT_OR_START
    or_end: time = _DEFAULT_OR_END
    force_close_at: time = _DEFAULT_FORCE_CLOSE_AT
    stop_loss_pct: Decimal = _DEFAULT_STOP_LOSS_PCT
    take_profit_pct: Decimal = _DEFAULT_TAKE_PROFIT_PCT

    def __post_init__(self) -> None:
        if self.stop_loss_pct <= 0:
            raise RuntimeError(f"stop_loss_pct 는 양수여야 합니다 (got={self.stop_loss_pct})")
        if self.take_profit_pct <= 0:
            raise RuntimeError(f"take_profit_pct 는 양수여야 합니다 (got={self.take_profit_pct})")
        if self.or_start >= self.or_end:
            raise RuntimeError(
                f"or_start({self.or_start}) 는 or_end({self.or_end}) 보다 이전이어야 합니다."
            )
        if self.or_end >= self.force_close_at:
            raise RuntimeError(
                f"or_end({self.or_end}) 는 force_close_at({self.force_close_at}) "
                "보다 이전이어야 합니다."
            )


@dataclass
class _SymbolState:
    """심볼별 상태. 세션 단위로 `reset()` 된다.

    `or_missing_warned` 는 "OR 구간에 bar 가 단 하나도 없어 당일 포기" 경고가
    같은 세션에서 반복 로그를 남기지 않도록 한 플래그 (동일 심볼에 매 분봉마다
    warning 이 찍히는 스팸 방지).
    """

    session_date: date | None = None
    or_high: Decimal | None = None
    or_low: Decimal | None = None
    or_confirmed: bool = False
    position_state: PositionState = "flat"
    entry_price: Decimal | None = None
    stop_price: Decimal | None = None
    take_price: Decimal | None = None
    last_bar_time: datetime | None = None
    last_close: Decimal | None = None
    or_missing_warned: bool = False

    def reset(self, session_date: date) -> None:
        self.session_date = session_date
        self.or_high = None
        self.or_low = None
        self.or_confirmed = False
        self.position_state = "flat"
        self.entry_price = None
        self.stop_price = None
        self.take_price = None
        self.last_bar_time = None
        self.last_close = None
        self.or_missing_warned = False


class ORBStrategy:
    """Opening Range Breakout 규칙 엔진. `Strategy` Protocol 구현체.

    공개 API: `on_bar`, `on_time`, `config` (프로퍼티), `get_state` (디버깅용).

    동일 호출자 스레드에서 `on_bar` → `on_bar` → `on_time` 형태로 순차 호출하는
    것을 가정한다. 동시 호출은 지원하지 않는다.
    """

    def __init__(self, config: StrategyConfig | None = None) -> None:
        self._config = config or StrategyConfig()
        self._states: dict[str, _SymbolState] = {}

    @property
    def config(self) -> StrategyConfig:
        return self._config

    def get_state(self, symbol: str) -> _SymbolState | None:
        """테스트·디버깅용 상태 스냅샷. 반환 객체는 내부 상태와 공유되므로
        호출자는 수정하지 않는다."""
        return self._states.get(symbol)

    # ---- on_bar --------------------------------------------------------

    def on_bar(self, bar: MinuteBar) -> list[Signal]:
        """분봉 이벤트를 소비하고 발생한 시그널 리스트를 반환."""
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
            # 사용자 수정 필요 입력 오류(RuntimeError)와 상태 머신 무결성 오류
            # (StrategyError — _check_exit 의 명시적 raise) 는 그대로 전파.
            raise
        except DecimalException as e:
            # Decimal 연산 실패만 좁혀 StrategyError 로 래핑. AttributeError 같은
            # 코드 버그는 의도적으로 propagate 시켜 디버깅 단서(스택트레이스/예외
            # 타입)를 보존한다.
            logger.exception(f"ORB on_bar Decimal 연산 실패 ({bar.symbol})")
            raise StrategyError(f"ORB on_bar Decimal 연산 실패 ({bar.symbol}): {e}") from e

    def _dispatch_bar(self, state: _SymbolState, bar: MinuteBar) -> list[Signal]:
        cfg = self._config
        bar_t = bar.bar_time.time()

        if bar_t < cfg.or_start:
            # 장 시작 전 데이터 — 무시 (로그 생략, 정상 케이스).
            return []

        if bar_t < cfg.or_end:
            self._accumulate_or(state, bar)
            return []

        # OR 확정 이후.
        if not state.or_confirmed:
            state.or_confirmed = True

        if state.position_state == "flat":
            if bar_t >= cfg.force_close_at:
                # 장 마감 30분 이내에는 신규 진입 금지. 디버그 레벨로 흔적만.
                logger.debug(
                    f"ORB 진입 스킵 (force_close_at 이후): {bar.symbol} "
                    f"@ bar_t={bar_t} (force_close_at={cfg.force_close_at})"
                )
                return []
            if state.or_high is None:
                # OR 구간에 bar 가 단 하나도 없었던 극단 케이스 — 당일 포기.
                # 같은 세션에서 1회만 warning (중복 스팸 방지).
                if not state.or_missing_warned:
                    logger.warning(
                        f"ORB 당일 포기 ({bar.symbol}): OR 구간 bar 수집 없음 "
                        f"(or_start={cfg.or_start}, or_end={cfg.or_end})"
                    )
                    state.or_missing_warned = True
                return []
            if bar.close > state.or_high:
                return [self._enter_long(state, bar)]
            return []

        if state.position_state == "long":
            exit_signal = self._check_exit(state, bar)
            return [exit_signal] if exit_signal is not None else []

        # "closed" — 당일 재진입 금지. 돌파 재발생 등 누락 추적용 디버그 흔적.
        logger.debug(
            f"ORB 재진입 스킵 ({bar.symbol}): 당일 청산 완료 상태 "
            f"(bar_t={bar_t}, close={bar.close})"
        )
        return []

    def _accumulate_or(self, state: _SymbolState, bar: MinuteBar) -> None:
        state.or_high = bar.high if state.or_high is None else max(state.or_high, bar.high)
        state.or_low = bar.low if state.or_low is None else min(state.or_low, bar.low)

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
            f"ORB 진입: {bar.symbol} @ {entry} "
            f"(or_high={state.or_high}, stop={stop}, take={take}, "
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
        # long 상태에서는 stop/take 가 _enter_long 에서 세팅된 상태여야 한다.
        # assert 가 `-O` 최적화 플래그에서 사라지면 `bar.low <= None` 이 TypeError
        # 를 뱉고 generic except 가 silent 하게 삼킬 위험이 있어 명시적 raise 로
        # 교체. 실제로 이 분기에 도달했다면 상태 머신 무결성 오류이므로
        # StrategyError 가 올바른 표현.
        if state.stop_price is None or state.take_price is None:
            raise StrategyError(
                f"long 상태인데 stop_price/take_price 미세팅 ({bar.symbol}) — "
                "상태 머신 무결성 오류 (_enter_long 호출 누락 가능성)"
            )

        if bar.low <= state.stop_price:
            state.position_state = "closed"
            logger.info(
                f"ORB 손절: {bar.symbol} @ {state.stop_price} "
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
                f"ORB 익절: {bar.symbol} @ {state.take_price} "
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
        """시각 이벤트 진입점. 현재는 `force_close_at` 이후 강제청산만 발생.

        `long` 상태인 모든 심볼에 대해 `ExitSignal(reason="force_close")` 를
        생성하고 상태를 `closed` 로 전이한다. 가격은 마지막 관찰 분봉 close
        (`state.last_close`) — 없으면 `entry_price` 로 폴백하되 이 경우
        데이터 파이프라인 이상 신호이므로 `logger.warning` 으로 흔적을 남긴다.
        **executor 가 실제 체결가로 덮어쓰는 것을 전제로 한다.** 둘 다 None 이면
        `StrategyError` — long 상태에서는 도달 불가능한 상태 머신 무결성 오류.
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
                    f"ORB 강제청산: {symbol} last_close 없음 → entry_price 로 폴백 "
                    f"(price={state.entry_price}). 데이터 파이프라인 이상 가능성."
                )
                price = state.entry_price
            else:
                # long 상태면 _enter_long 에서 entry_price 가 세팅되어야 하므로 도달 불가.
                raise StrategyError(
                    f"ORB 강제청산 시점에 {symbol} 의 last_close·entry_price 모두 None "
                    "— 상태 머신 무결성 오류 (long 상태에서 발생 불가)"
                )
            state.position_state = "closed"
            logger.info(f"ORB 강제청산: {symbol} @ {price} (ts={now.isoformat()})")
            signals.append(
                ExitSignal(
                    symbol=symbol,
                    price=price,
                    ts=now,
                    reason="force_close",
                )
            )
        return signals

    # ---- 재기동 복원 (Issue #33) ---------------------------------------

    def restore_long_position(
        self,
        symbol: str,
        entry_price: Decimal,
        entry_ts: datetime,
    ) -> None:
        """재기동 시 open position 의 ORB 상태를 `long` 으로 복원.

        `stop_price` / `take_price` 는 현재 `StrategyConfig` 로 재계산한다 —
        재기동 전 실행이 다른 config 를 썼다면 드리프트가 있지만, 본 프로젝트
        는 `config/strategy.yaml` 미도입으로 코드 상수 변경이 드물어 허용
        범위. `or_high` / `or_low` 는 복원하지 않는다 — `position_state = 'long'`
        이면 `_dispatch_bar` 의 flat 분기를 타지 않아 재진입 계산이 필요없다.

        이 메서드는 `Executor.restore_session` 이 각 `OpenPositionRow` 에
        대해 1회 호출한다. 직접 호출은 운영 경로 밖.

        Raises:
            RuntimeError: symbol 포맷 오류 / entry_ts naive / entry_price ≤ 0.
        """
        self._validate_symbol(symbol)
        self._require_aware(entry_ts, "entry_ts")
        if entry_price <= 0:
            raise RuntimeError(f"entry_price 는 양수여야 합니다 (got={entry_price})")

        state = self._states.setdefault(symbol, _SymbolState())
        session = entry_ts.date()
        if state.session_date is None or state.session_date != session:
            state.reset(session)

        cfg = self._config
        stop = entry_price * (Decimal("1") - cfg.stop_loss_pct)
        take = entry_price * (Decimal("1") + cfg.take_profit_pct)
        state.position_state = "long"
        state.entry_price = entry_price
        state.stop_price = stop
        state.take_price = take
        # OR 구간은 이미 지났다는 가정 (재기동 시점이 OR 이후) —
        # `or_confirmed=True` 로 표시해 이후 `_dispatch_bar` 가 OR 미확정
        # 경로를 타지 않도록. `or_high` / `or_low` 는 None 유지 — long 상태
        # 에서는 참조되지 않는다.
        state.or_confirmed = True
        # last_bar_time 은 복원하지 않는다 — 재기동 후 들어오는 첫 bar 가
        # 정상 진행 경로 (`last_bar_time is None` 조건). last_close 도 None
        # 유지 (on_time 강제청산은 entry_price 폴백으로 동작).
        logger.warning(
            "ORB restore_long_position: {s} entry={p} stop={sp} take={tp} session={d}",
            s=symbol,
            p=entry_price,
            sp=stop,
            tp=take,
            d=session,
        )

    def reset_session(self, symbols: Sequence[str] = ()) -> None:
        """재기동 복원 롤백용 — 지정된 심볼들의 `_SymbolState` 를 제거한다.

        `Executor.restore_session` 이 ORB 복원 루프 중간에 실패했을 때 부분
        상태를 제거해 재호출 시 fresh `_SymbolState` 가 재생성되도록 보장한다
        (Issue #33 후속 보강 — 부분 복원 일관성 사고 방지).

        Args:
            symbols: 초기화할 심볼 목록. 빈 Sequence 이면 모든 `_states` 를
                제거한다. 동일 심볼이 중복돼도 무방.

        Raises:
            이 메서드는 raise 하지 않는다 — 알 수 없는 심볼은 조용히 무시.
            호출자(Executor.restore_session) 가 이미 예외 경로에 있으므로
            추가 실패는 매매 루프 보호 계약에 역행한다.
        """
        if not symbols:
            self._states.clear()
            return
        for sym in symbols:
            self._states.pop(sym, None)

    def mark_session_closed(
        self,
        symbol: str,
        session_date: date,
    ) -> None:
        """재기동 시 당일 이미 청산된 심볼을 `closed` 로 표시 — 당일 재진입 차단.

        `_dispatch_bar` 가 `position_state == 'closed'` 분기에서 재진입을
        debug 로그만 남기고 스킵하므로, DB 에 당일 buy→sell 쌍이 기록된
        심볼을 이 메서드로 표시해 재기동 후 재돌파에도 재진입이 되지 않도록
        보장한다.

        Raises:
            RuntimeError: symbol 포맷 오류.
        """
        self._validate_symbol(symbol)
        state = self._states.setdefault(symbol, _SymbolState())
        if state.session_date is None or state.session_date != session_date:
            state.reset(session_date)
        state.position_state = "closed"
        state.or_confirmed = True
        logger.warning(
            "ORB mark_session_closed: {s} session={d}",
            s=symbol,
            d=session_date,
        )

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
