"""Walk-forward validation — Phase 5 구현 대기 스켈레톤.

책임 범위 (Phase 5 본 구현)
- `generate_windows(total_from, total_to, *, train_months, test_months, step_months)`
  로 rolling window 를 생성한다 (train 구간 → test 구간, step 만큼 앞으로 이동).
- 각 window 에 대해 `BacktestEngine` 을 두 번 실행 (train·test) 하고
  `WalkForwardMetrics.degradation_pct = (train_avg - test_avg) / train_avg` 를
  계산해 `pass_threshold` 와 비교해 `is_pass` 판정.
- 최종 산출: `WalkForwardResult(windows, per_window_metrics, aggregate_metrics)`.

현재 상태 (Issue #67)
- **스켈레톤 + DTO + Protocol 만 제공**. 실제 검증 로직은 Phase 5 에서 구현.
- `generate_windows` / `run_walk_forward` 는 `NotImplementedError` 를 던진다.
- DTO `__post_init__` 가드는 스텁이라도 계약을 강제 — 후속 PR 이 DTO 계약을
  바꾸지 않고 순수 구현 레이어만 덮어쓰도록 한다.

설계 원칙 (backtest 나머지 모듈과 동일 기조)
- 외부 I/O 없음. `datetime.now()` 미사용. 결정론.
- generic `except Exception` 금지. DTO 계약 위반은 `RuntimeError` (ADR-0003).
- `Decimal` 정확도 우선.
- 단일 프로세스 전용.

pass_threshold 기본값 결정 노트
- Issue #67 제안: `degradation_pct <= 0.3` (train→test 수익률 악화 30% 이하 PASS).
- 현재 스텁은 호출자가 값을 주입한다 — 기본값 하드코딩 없음. Phase 5 본
  구현에서 ADR `docs/adr/NNNN-walk-forward-pass-threshold.md` 로 결정 기록.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from stock_agent.backtest.engine import BacktestConfig, BacktestMetrics
from stock_agent.backtest.loader import BarLoader


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    """단일 walk-forward window — train 구간 + test 구간.

    계약:
    - `train_from <= train_to` (단일일 허용).
    - `test_from <= test_to` (단일일 허용).
    - `train_to < test_from` (중첩 금지 — train/test 누설 방지).

    Raises:
        RuntimeError: 위 계약 중 하나라도 위반할 때.
    """

    train_from: date
    train_to: date
    test_from: date
    test_to: date

    def __post_init__(self) -> None:
        if self.train_from > self.train_to:
            raise RuntimeError(
                f"train_from({self.train_from}) 는 train_to({self.train_to}) 이전이어야 합니다."
            )
        if self.test_from > self.test_to:
            raise RuntimeError(
                f"test_from({self.test_from}) 는 test_to({self.test_to}) 이전이어야 합니다."
            )
        if self.train_to >= self.test_from:
            raise RuntimeError(
                f"train_to({self.train_to}) 는 test_from({self.test_from}) 보다 "
                "엄격히 이전이어야 합니다 (train/test 중첩 금지)."
            )


@dataclass(frozen=True, slots=True)
class WalkForwardMetrics:
    """walk-forward 집계 메트릭.

    필드:
    - `train_avg_return_pct`: 모든 train window 의 `total_return_pct` 평균 (소수).
    - `test_avg_return_pct`: 모든 test window 의 `total_return_pct` 평균 (소수).
    - `degradation_pct`: `(train_avg - test_avg) / train_avg`. train_avg == 0 이면
      Phase 5 구현에서 0 으로 폴백 (호출자가 주입하는 현재 스텁은 값 검증만).
    - `pass_threshold`: 허용 degradation 임계치 (0 이상, 소수 — 0.3 = 30%).
    - `is_pass`: 최종 PASS 판정.

    Raises:
        RuntimeError: `pass_threshold < 0`.
    """

    train_avg_return_pct: Decimal
    test_avg_return_pct: Decimal
    degradation_pct: Decimal
    pass_threshold: Decimal
    is_pass: bool

    def __post_init__(self) -> None:
        if self.pass_threshold < 0:
            raise RuntimeError(f"pass_threshold 는 0 이상이어야 합니다 (got={self.pass_threshold})")


@dataclass(frozen=True, slots=True)
class WalkForwardResult:
    """walk-forward 실행 결과 스냅샷.

    필드:
    - `windows`: 실행한 window 목록.
    - `per_window_metrics`: 각 window 의 test 구간 `BacktestMetrics` (`windows` 와
      동일 길이·동일 순서).
    - `aggregate_metrics`: 집계 메트릭 + PASS 판정.

    Raises:
        RuntimeError: `windows` 가 비어있거나, `windows` 와
            `per_window_metrics` 길이가 다를 때.
    """

    windows: tuple[WalkForwardWindow, ...]
    per_window_metrics: tuple[BacktestMetrics, ...]
    aggregate_metrics: WalkForwardMetrics

    def __post_init__(self) -> None:
        if len(self.windows) == 0:
            raise RuntimeError("windows 는 1개 이상이어야 합니다.")
        if len(self.windows) != len(self.per_window_metrics):
            raise RuntimeError(
                f"windows 길이({len(self.windows)}) 와 per_window_metrics "
                f"길이({len(self.per_window_metrics)}) 가 일치해야 합니다."
            )


def generate_windows(
    total_from: date,
    total_to: date,
    *,
    train_months: int = 6,
    test_months: int = 2,
    step_months: int = 1,
) -> tuple[WalkForwardWindow, ...]:
    """`[total_from, total_to]` 구간을 rolling window 로 분할.

    현재 스텁 — Phase 5 구현 대기.
    """
    raise NotImplementedError("Phase 5 구현 대기 — Issue #67 skeleton")


def run_walk_forward(
    loader: BarLoader,
    config: BacktestConfig,
    windows: tuple[WalkForwardWindow, ...],
) -> WalkForwardResult:
    """각 window 에 대해 `BacktestEngine` 실행 후 `WalkForwardResult` 집계.

    현재 스텁 — Phase 5 구현 대기.
    """
    raise NotImplementedError("Phase 5 구현 대기 — Issue #67 skeleton")
