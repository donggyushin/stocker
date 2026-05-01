"""전략 팩토리 — `--strategy-type` CLI 분기 단일 진실원.

ADR-0019 Step E 후속 (Stage 1). `scripts/backtest.py` / `scripts/sensitivity.py`
운영자 인자 `--strategy-type {orb,vwap-mr,gap-reversal}` 에 따라 적절한 전략
인스턴스를 매번 새로 생성하는 팩토리 클로저를 반환한다. 반환 타입은
`Callable[[], Strategy]` — `BacktestConfig.strategy_factory` 에 그대로 주입.

설계 메모
- 매 호출마다 새 인스턴스 — 매 조합·매 세션마다 상태 누적이 격리되어야 한다
  (`BacktestEngine.run` 1회 소비 계약, sensitivity 그리드의 조합별 격리).
- `gap-reversal` 분기는 stub `prev_close_provider` 를 폴백으로 사용한다.
  Stage 2 에서 `HistoricalDataStore.DailyBar` + `BusinessDayCalendar` (ADR-0018)
  조합 기반 실제 provider 를 주입할 때까지 stub 은 항상 None 을 반환해
  GapReversalStrategy 가 진입을 거부하는 상태를 유지한다.
- 알 수 없는 `strategy_type` 은 `RuntimeError` (사용자 입력 오류, 다른 모듈
  기조와 일관 — broker/data/strategy/risk).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Literal

from stock_agent.strategy.base import Strategy
from stock_agent.strategy.gap_reversal import (
    GapReversalConfig,
    GapReversalStrategy,
    PrevCloseProvider,
)
from stock_agent.strategy.orb import ORBStrategy, StrategyConfig
from stock_agent.strategy.vwap_mr import VWAPMRConfig, VWAPMRStrategy

StrategyType = Literal["orb", "vwap-mr", "gap-reversal"]
STRATEGY_CHOICES: tuple[StrategyType, ...] = ("orb", "vwap-mr", "gap-reversal")


def _stub_prev_close_provider(symbol: str, session_date: date) -> Decimal | None:
    """Stage 2 통합 전까지 사용하는 stub. 항상 None 반환.

    `GapReversalStrategy` 는 prev_close 가 None 이면 갭 평가를 포기하고 당일
    진입을 거부한다. Stage 1 단계에서 `--strategy-type=gap-reversal` 로 백테스트를
    돌려도 진입 0 인 상태로 회귀 안전망 — Stage 2 에서 실제 provider 로 교체.
    """
    del symbol, session_date  # 사용 안 함 (stub) — 인자 시그니처 계약 보존
    return None


def build_strategy_factory(
    strategy_type: str,
    *,
    strategy_config: StrategyConfig | None = None,
    vwap_mr_config: VWAPMRConfig | None = None,
    gap_reversal_config: GapReversalConfig | None = None,
    prev_close_provider: PrevCloseProvider | None = None,
) -> Callable[[], Strategy]:
    """`strategy_type` 별 전략 인스턴스 생성 클로저 반환.

    Args:
        strategy_type: `"orb" | "vwap-mr" | "gap-reversal"` 중 하나. `str` 로
            받아 런타임 가드 (`STRATEGY_CHOICES`) 로 검증 — 호출자가 argparse
            choices 외 값을 넘길 가능성을 차단하기 위해 Literal 로 좁히지 않는다.
        strategy_config: ORB 설정. `strategy_type != "orb"` 면 무시.
        vwap_mr_config: VWAP MR 설정. `strategy_type != "vwap-mr"` 면 무시.
        gap_reversal_config: Gap Reversal 설정. `strategy_type != "gap-reversal"`
            면 무시.
        prev_close_provider: Gap Reversal 전용 의존 주입.
            `strategy_type == "gap-reversal"` 이면서 None 이면 stub
            (`_stub_prev_close_provider`) 로 폴백한다 — Stage 2 통합 전 회귀
            안전망. 다른 strategy_type 에서는 무시.

    Returns:
        매 호출마다 새 전략 인스턴스를 생성하는 클로저.

    Raises:
        RuntimeError: `strategy_type` 이 `STRATEGY_CHOICES` 에 없을 때.
    """
    if strategy_type == "orb":
        orb_cfg = strategy_config
        return lambda: ORBStrategy(orb_cfg)
    if strategy_type == "vwap-mr":
        vwap_cfg = vwap_mr_config
        return lambda: VWAPMRStrategy(vwap_cfg)
    if strategy_type == "gap-reversal":
        provider: PrevCloseProvider = (
            prev_close_provider if prev_close_provider is not None else _stub_prev_close_provider
        )
        gap_cfg = gap_reversal_config
        return lambda: GapReversalStrategy(prev_close_provider=provider, config=gap_cfg)
    raise RuntimeError(f"알 수 없는 strategy_type: {strategy_type!r}. 허용 값: {STRATEGY_CHOICES}")
