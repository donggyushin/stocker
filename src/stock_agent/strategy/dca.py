"""Buy-and-hold DCA (Dollar-Cost Averaging) 전략 구현.

ADR-0019 Step F PR1 — F1 Buy & Hold baseline. KOSPI 200 ETF (069500 KODEX 200)
단일 종목에 대해 매월 정해진 영업일에 정액 시장가 매수, 청산 X 영구 보유.
ADR-0022 게이트 2 (DCA baseline 대비 알파) 의 비교 기준 산출 목적.

책임 범위
- 매월 N번째 target_symbol 분봉 수신 시 `EntrySignal` 1건 방출.
- 청산 시그널 절대 미생성 — DCA 는 영구 보유.
- `on_time(now)` 항상 빈 리스트 — force_close 없음.

설계 메모
- 영업일 캘린더 의존 X. '받은 분봉 = 영업일' 가정 (`DailyBarLoader` 가 영업일만
  공급) → 휴일은 분봉 미수신으로 자연스럽게 스킵된다.
- 단일 `target_symbol` 카운팅. 비타겟 심볼 분봉은 조용히 무시 — 멀티 심볼
  스트림에 안전.
- `EntrySignal.stop_price` / `take_price` = `Decimal("0")` 마커. DCA 손익절
  미사용을 호출자(`compute_dca_baseline`) 가 0 으로 인지.

에러 정책 (broker/data/strategy 와 동일 기조)
- `RuntimeError` 는 전파 — 잘못된 symbol·naive datetime·시간 역행·설정 위반.

스레드 모델
- 단일 프로세스 전용 (ORB·VWAPMR·GapReversal 와 동일).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from loguru import logger

from stock_agent.data import MinuteBar
from stock_agent.strategy.base import EntrySignal, Signal

_SYMBOL_RE = re.compile(r"^\d{6}$")

_DEFAULT_MONTHLY_INVESTMENT_KRW = 100_000
_DEFAULT_TARGET_SYMBOL = "069500"
_DEFAULT_PURCHASE_DAY = 1
_PURCHASE_DAY_MAX = 28  # 모든 달에 N 번째 영업일 존재 보장 한계.


@dataclass(frozen=True, slots=True)
class DCAConfig:
    """DCA 파라미터.

    Raises:
        RuntimeError: `monthly_investment_krw <= 0`,
            `target_symbol` 이 6 자리 숫자 정규식 위반,
            `purchase_day` 가 [1, 28] 범위를 벗어날 때.
    """

    monthly_investment_krw: int = _DEFAULT_MONTHLY_INVESTMENT_KRW
    target_symbol: str = _DEFAULT_TARGET_SYMBOL
    purchase_day: int = _DEFAULT_PURCHASE_DAY

    def __post_init__(self) -> None:
        if self.monthly_investment_krw <= 0:
            raise RuntimeError(
                f"monthly_investment_krw 는 양수여야 합니다 (got={self.monthly_investment_krw})"
            )
        if not _SYMBOL_RE.match(self.target_symbol):
            raise RuntimeError(
                f"target_symbol 은 6자리 숫자 문자열이어야 합니다 (got={self.target_symbol!r})"
            )
        if self.purchase_day < 1 or self.purchase_day > _PURCHASE_DAY_MAX:
            raise RuntimeError(
                f"purchase_day 는 [1, {_PURCHASE_DAY_MAX}] 범위여야 합니다 "
                f"(got={self.purchase_day})"
            )


@dataclass
class _DCAState:
    """DCA 내부 상태. 월 경계마다 카운터·진입 플래그 리셋."""

    last_bar_time: datetime | None = None
    current_year_month: tuple[int, int] | None = None
    counter: int = 0
    entered_this_month: bool = False


class DCAStrategy:
    """매월 N 번째 영업일 정액 매수 + 영구 보유. `Strategy` Protocol 구현체.

    공개 API: `on_bar`, `on_time`, `config` (프로퍼티).

    동일 호출자 스레드 순차 호출 가정. 동시 호출 미지원.
    """

    def __init__(self, config: DCAConfig | None = None) -> None:
        self._config = config if config is not None else DCAConfig()
        self._state = _DCAState()

    @property
    def config(self) -> DCAConfig:
        return self._config

    def on_bar(self, bar: MinuteBar) -> list[Signal]:
        """분봉 이벤트 진입점. target_symbol 의 N 번째 분봉에서 EntrySignal 1건."""
        self._validate_symbol(bar.symbol)
        self._require_aware(bar.bar_time, "bar.bar_time")

        if self._state.last_bar_time is not None and bar.bar_time < self._state.last_bar_time:
            raise RuntimeError(
                f"bar.bar_time 역행 감지: last={self._state.last_bar_time.isoformat()}, "
                f"now={bar.bar_time.isoformat()}"
            )
        self._state.last_bar_time = bar.bar_time

        if bar.symbol != self._config.target_symbol:
            return []

        ym = (bar.bar_time.year, bar.bar_time.month)
        if self._state.current_year_month != ym:
            self._state.current_year_month = ym
            self._state.counter = 0
            self._state.entered_this_month = False

        self._state.counter += 1

        if self._state.entered_this_month:
            return []
        if self._state.counter < self._config.purchase_day:
            return []

        self._state.entered_this_month = True
        signal = EntrySignal(
            symbol=bar.symbol,
            price=bar.close,
            ts=bar.bar_time,
            stop_price=Decimal("0"),
            take_price=Decimal("0"),
        )
        logger.info(
            "DCA 진입: {s} @ {p} (ts={t}, monthly_krw={k}, day_n={d})",
            s=bar.symbol,
            p=bar.close,
            t=bar.bar_time.isoformat(),
            k=self._config.monthly_investment_krw,
            d=self._state.counter,
        )
        return [signal]

    def on_time(self, now: datetime) -> list[Signal]:
        """시각 이벤트 — DCA 는 force_close 없음, 항상 빈 리스트."""
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
