"""Risk Manager — 포지션 사이징·진입 게이팅·서킷브레이커.

책임 범위
- 종목당 포지션 사이징 (세션 시작 자본 × 비중 / 참고가 → floor 주수).
- 진입 게이팅 — 동시 보유 상한, 일일 진입 횟수 상한, 중복 심볼, 최소 명목,
  잔액 부족.
- 서킷브레이커 — 당일 **실현** 손익이 `-starting_capital_krw *
  daily_loss_limit_pct` 이하이면 halt 로 전환. 다음 `evaluate_entry` 부터
  `halted_daily_loss` 거부.
- 세션 단위 상태 관리 — `start_session(session_date, starting_capital_krw)`
  로 카운터·PnL·활성 포지션을 리셋한다.

범위 제외 (의도적 defer)
- PnL 계산 — executor 가 실제 체결가·수수료·세금 반영해 계산 후
  `record_exit(symbol, realized_pnl_krw)` 로 통지. 본 모듈은 통지받은 값을
  그대로 신뢰한다 (부호: 손실 음수, 수익 양수).
- 주문 실행·체결 추적 — `execution/executor.py` (Phase 3).
- SQLite 영속화 — Phase 3 executor + storage.
- 미실현 손익 기반 kill-switch — Phase 5.
- `config/strategy.yaml` YAML 로더 — Phase 3 `main.py` 착수 시 도입. 현재는
  `RiskConfig` 생성자 주입.

에러 정책 (strategy/broker 와 동일 기조)
- `RuntimeError` 는 전파 — 사용자 수정 필요 입력 오류 (세션 미시작 상태에서
  `evaluate_entry`, naive datetime, 잘못된 symbol 포맷, 음수 qty·price 등).
- `RiskManagerError` 는 raise — 상태 머신 무결성 오류 (미보유 심볼 청산,
  중복 체결 기록 등 호출 순서 위반).
- generic `except Exception` 을 쓰지 않는다. `assert` 대신 명시적 예외.

스레드 모델
- 단일 프로세스 전용. 동시 호출 금지. strategy/broker 와 동일 기조.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from loguru import logger

from stock_agent.strategy import EntrySignal

_SYMBOL_RE = re.compile(r"^\d{6}$")

_DEFAULT_POSITION_PCT = Decimal("0.20")
_DEFAULT_MAX_POSITIONS = 3
_DEFAULT_DAILY_LOSS_LIMIT_PCT = Decimal("0.02")
_DEFAULT_DAILY_MAX_ENTRIES = 10
_DEFAULT_MIN_NOTIONAL_KRW = 100_000


RejectReason = Literal[
    "halted_daily_loss",
    "max_positions_reached",
    "daily_entry_cap",
    "duplicate_symbol",
    "insufficient_cash",
    "below_min_notional",
]


class RiskManagerError(Exception):
    """RiskManager 상태 머신 처리 중 발생한 예기치 못한 오류.

    사용자 수정 필요 입력 오류(`RuntimeError`) 와 구분한다. 예: 미보유 심볼
    청산, 중복 체결 기록 등 호출 순서 위반.
    """


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """리스크 한도 파라미터. 모든 값은 생성자 인자로 재정의 가능.

    기본값은 `plan.md` Phase 2 운영 기본값 및 root `CLAUDE.md` 에 고정된
    리스크 한도와 일치한다 (종목당 20%, 동시 3종목, 일일 -2% 서킷브레이커,
    일일 10회 진입, 최소 10만원/종목).

    Raises:
        RuntimeError: 어느 한 필드라도 0 이하일 때.
    """

    position_pct: Decimal = _DEFAULT_POSITION_PCT
    max_positions: int = _DEFAULT_MAX_POSITIONS
    daily_loss_limit_pct: Decimal = _DEFAULT_DAILY_LOSS_LIMIT_PCT
    daily_max_entries: int = _DEFAULT_DAILY_MAX_ENTRIES
    min_notional_krw: int = _DEFAULT_MIN_NOTIONAL_KRW

    def __post_init__(self) -> None:
        if self.position_pct <= 0:
            raise RuntimeError(f"position_pct 는 양수여야 합니다 (got={self.position_pct})")
        if self.daily_loss_limit_pct <= 0:
            raise RuntimeError(
                f"daily_loss_limit_pct 는 양수여야 합니다 (got={self.daily_loss_limit_pct})"
            )
        if self.max_positions <= 0:
            raise RuntimeError(f"max_positions 는 양수여야 합니다 (got={self.max_positions})")
        if self.daily_max_entries <= 0:
            raise RuntimeError(
                f"daily_max_entries 는 양수여야 합니다 (got={self.daily_max_entries})"
            )
        if self.min_notional_krw <= 0:
            raise RuntimeError(f"min_notional_krw 는 양수여야 합니다 (got={self.min_notional_krw})")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """진입 승인/거부 결과.

    Attributes:
        approved: 승인 여부. True 이면 `qty > 0` 이고 `reason is None`.
        qty: 승인 수량 (정수 주수). 거부 시 0.
        target_notional_krw: 계산된 목표 명목 (= starting_capital_krw ×
            position_pct). 거부되어도 참고용으로 채워진다.
        reason: 거부 사유. 승인 시 `None`.
    """

    approved: bool
    qty: int
    target_notional_krw: int
    reason: RejectReason | None


@dataclass(frozen=True, slots=True)
class PositionRecord:
    """활성 보유 포지션 (세션 인메모리).

    Attributes:
        symbol: 6자리 종목 코드.
        entry_price: 실제 체결가 (executor 계산 VWAP 또는 단일 체결가). Decimal.
        qty: 체결 수량. 양의 정수.
        entry_ts: 진입 시각. KST aware datetime.
    """

    symbol: str
    entry_price: Decimal
    qty: int
    entry_ts: datetime


class RiskManager:
    """결정론적 리스크 게이팅·포지션 사이징 레이어.

    책임
    - `start_session(session_date, starting_capital_krw)` — 일일 리셋.
    - `evaluate_entry(signal, available_cash_krw)` — 순수 판정 (상태 변경 없음).
    - `record_entry(symbol, entry_price, qty, entry_ts)` — 체결 확정 통지.
    - `record_exit(symbol, realized_pnl_krw)` — 청산 확정 통지 (PnL 누적).

    단일 프로세스 전용. 멀티스레드 호출 금지.
    """

    def __init__(self, config: RiskConfig) -> None:
        self._config = config
        self._session_date: date | None = None
        self._starting_capital_krw: int | None = None
        self._daily_realized_pnl_krw: int = 0
        self._entries_today: int = 0
        self._active_positions: dict[str, PositionRecord] = {}
        self._halt_logged: bool = False

    # ---- public API ----

    def start_session(self, session_date: date, starting_capital_krw: int) -> None:
        """일일 세션 시작 — 카운터·PnL·활성 포지션 리셋, 기준 자본 확정.

        Raises:
            RuntimeError: `starting_capital_krw <= 0` 일 때.
        """
        if starting_capital_krw <= 0:
            raise RuntimeError(
                f"starting_capital_krw 는 양수여야 합니다 (got={starting_capital_krw})"
            )
        if self._active_positions:
            logger.warning(
                "risk_manager.start_session: 이전 세션 잔여 포지션 감지 — "
                "리셋 전 {count}종목 ({symbols}). executor 가 실계좌 재동기화 책임.",
                count=len(self._active_positions),
                symbols=list(self._active_positions.keys()),
            )
        self._session_date = session_date
        self._starting_capital_krw = starting_capital_krw
        self._daily_realized_pnl_krw = 0
        self._entries_today = 0
        self._active_positions = {}
        self._halt_logged = False

    def evaluate_entry(self, signal: EntrySignal, available_cash_krw: int) -> RiskDecision:
        """진입 시그널 승인/거부 판정.

        상태 변경 없음 (순수 판정). 실제 체결 기록은 `record_entry` 로만.

        Args:
            signal: 전략이 생성한 진입 시그널.
            available_cash_krw: 주문 가능 현금. executor 가 수수료·세금 버퍼
                를 뺀 보수적 값을 주입하는 것을 전제로 한다.

        Returns:
            `RiskDecision`. `approved=True` 이면 `qty > 0` 이고 `reason=None`.

        Raises:
            RuntimeError: 세션 미시작, signal 입력 오류, `available_cash_krw
                < 0` 등.
        """
        if self._session_date is None or self._starting_capital_krw is None:
            raise RuntimeError(
                "RiskManager.evaluate_entry: 세션이 시작되지 않았습니다. "
                "start_session(session_date, starting_capital_krw) 를 먼저 호출하세요."
            )
        if available_cash_krw < 0:
            raise RuntimeError(
                f"available_cash_krw 는 0 이상이어야 합니다 (got={available_cash_krw})"
            )
        self._validate_signal(signal)

        target_notional_krw = int(Decimal(self._starting_capital_krw) * self._config.position_pct)

        if self.is_halted:
            return self._reject("halted_daily_loss", signal, target_notional_krw)
        if self._entries_today >= self._config.daily_max_entries:
            return self._reject("daily_entry_cap", signal, target_notional_krw)
        if len(self._active_positions) >= self._config.max_positions:
            return self._reject("max_positions_reached", signal, target_notional_krw)
        if signal.symbol in self._active_positions:
            return self._reject("duplicate_symbol", signal, target_notional_krw)

        qty = int(Decimal(target_notional_krw) / signal.price)
        filled_notional = Decimal(qty) * signal.price
        if filled_notional < self._config.min_notional_krw:
            return self._reject("below_min_notional", signal, target_notional_krw)
        if filled_notional > available_cash_krw:
            return self._reject("insufficient_cash", signal, target_notional_krw)

        logger.info(
            "risk_manager.approve symbol={symbol} qty={qty} "
            "target_notional={target} ref_price={price}",
            symbol=signal.symbol,
            qty=qty,
            target=target_notional_krw,
            price=signal.price,
        )
        return RiskDecision(
            approved=True,
            qty=qty,
            target_notional_krw=target_notional_krw,
            reason=None,
        )

    def record_entry(
        self,
        symbol: str,
        entry_price: Decimal,
        qty: int,
        entry_ts: datetime,
    ) -> None:
        """체결 확정 통지 — 활성 포지션 등록 + `entries_today += 1`.

        `evaluate_entry` 가 승인한 시그널에 대해 실제 체결이 완료된 뒤
        executor 가 호출한다.

        Raises:
            RuntimeError: 입력 오류 (symbol 포맷, qty/price ≤ 0, naive ts).
            RiskManagerError: 세션 미시작, 동일 symbol 중복 체결.
        """
        if self._session_date is None:
            raise RiskManagerError("record_entry: 세션 미시작. start_session 을 먼저 호출하세요.")
        if not _SYMBOL_RE.fullmatch(symbol):
            raise RuntimeError(f"symbol 형식이 올바르지 않습니다 (got={symbol!r})")
        if qty <= 0:
            raise RuntimeError(f"qty 는 양수여야 합니다 (got={qty})")
        if entry_price <= 0:
            raise RuntimeError(f"entry_price 는 양수여야 합니다 (got={entry_price})")
        if entry_ts.tzinfo is None:
            raise RuntimeError("entry_ts 는 tz-aware 여야 합니다 (KST 권장).")
        if symbol in self._active_positions:
            raise RiskManagerError(
                f"record_entry: 중복 체결 — 이미 활성 포지션에 존재 (symbol={symbol})"
            )

        self._active_positions[symbol] = PositionRecord(
            symbol=symbol,
            entry_price=entry_price,
            qty=qty,
            entry_ts=entry_ts,
        )
        self._entries_today += 1
        logger.info(
            "risk_manager.record_entry symbol={symbol} qty={qty} price={price} "
            "entries_today={count}",
            symbol=symbol,
            qty=qty,
            price=entry_price,
            count=self._entries_today,
        )

    def record_exit(self, symbol: str, realized_pnl_krw: int) -> None:
        """청산 체결 통지 — 포지션 제거 + `daily_realized_pnl_krw` 누적.

        `realized_pnl_krw` 부호 계약: 손실은 음수, 수익은 양수. executor 가
        수수료·세금 반영해 계산한 최종값.

        Raises:
            RuntimeError: symbol 포맷 오류.
            RiskManagerError: 세션 미시작, 미보유 심볼 청산 시도.
        """
        if self._session_date is None or self._starting_capital_krw is None:
            raise RiskManagerError("record_exit: 세션 미시작. start_session 을 먼저 호출하세요.")
        if not _SYMBOL_RE.fullmatch(symbol):
            raise RuntimeError(f"symbol 형식이 올바르지 않습니다 (got={symbol!r})")
        if symbol not in self._active_positions:
            raise RiskManagerError(f"record_exit: 미보유 심볼 청산 시도 (symbol={symbol})")

        del self._active_positions[symbol]
        self._daily_realized_pnl_krw += realized_pnl_krw
        logger.info(
            "risk_manager.record_exit symbol={symbol} realized_pnl={pnl} daily_pnl={daily}",
            symbol=symbol,
            pnl=realized_pnl_krw,
            daily=self._daily_realized_pnl_krw,
        )

        threshold = self._halt_threshold_krw()
        if self._daily_realized_pnl_krw <= threshold and not self._halt_logged:
            self._halt_logged = True
            logger.warning(
                "risk_manager.daily_loss_breaker_triggered daily_pnl={pnl} "
                "threshold={threshold} (자본 {starting}원 × -{pct})",
                pnl=self._daily_realized_pnl_krw,
                threshold=threshold,
                starting=self._starting_capital_krw,
                pct=self._config.daily_loss_limit_pct,
            )

    # ---- read-only properties ----

    @property
    def config(self) -> RiskConfig:
        return self._config

    @property
    def session_date(self) -> date | None:
        return self._session_date

    @property
    def starting_capital_krw(self) -> int | None:
        return self._starting_capital_krw

    @property
    def daily_realized_pnl_krw(self) -> int:
        return self._daily_realized_pnl_krw

    @property
    def entries_today(self) -> int:
        return self._entries_today

    @property
    def active_positions(self) -> tuple[PositionRecord, ...]:
        """활성 포지션 스냅샷. 내부 dict 를 변경해도 반환값에 영향 없음."""
        return tuple(self._active_positions.values())

    @property
    def is_halted(self) -> bool:
        """서킷브레이커 활성 여부. 세션 미시작 상태면 False."""
        if self._session_date is None or self._starting_capital_krw is None:
            return False
        return self._daily_realized_pnl_krw <= self._halt_threshold_krw()

    # ---- internal helpers ----

    def _halt_threshold_krw(self) -> int:
        """서킷브레이커 임계치 — `is_halted` 와 halt 전환 로그 양쪽에서 재사용.

        호출자 불변식: `self._starting_capital_krw is not None`. 이 메서드는
        타입 체커 만족을 위해 assert 를 두지 않고 호출자 보장을 신뢰한다
        (현재 호출부 2곳 모두 상위에서 None 체크 이후에만 진입).
        """
        return -int(Decimal(self._starting_capital_krw or 0) * self._config.daily_loss_limit_pct)

    def _validate_signal(self, signal: EntrySignal) -> None:
        if not _SYMBOL_RE.fullmatch(signal.symbol):
            raise RuntimeError(f"signal.symbol 형식이 올바르지 않습니다 (got={signal.symbol!r})")
        if signal.ts.tzinfo is None:
            raise RuntimeError("signal.ts 는 tz-aware 여야 합니다 (KST 권장).")
        if signal.price <= 0:
            raise RuntimeError(f"signal.price 는 양수여야 합니다 (got={signal.price})")

    def _reject(
        self,
        reason: RejectReason,
        signal: EntrySignal,
        target_notional_krw: int,
    ) -> RiskDecision:
        logger.info(
            "risk_manager.reject symbol={symbol} reason={reason} target_notional={target}",
            symbol=signal.symbol,
            reason=reason,
            target=target_notional_krw,
        )
        return RiskDecision(
            approved=False,
            qty=0,
            target_notional_krw=target_notional_krw,
            reason=reason,
        )
