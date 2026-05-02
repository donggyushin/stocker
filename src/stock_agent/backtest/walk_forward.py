"""Walk-forward validation — DTO + ``generate_windows`` + RSI-MR 평가자 본 구현.

책임 범위
- ``generate_windows(total_from, total_to, *, train_months, test_months, step_months)``
  로 rolling window 를 생성한다 (train 구간 → test 구간, step 만큼 앞으로 이동).
- ``run_rsi_mr_walk_forward(loader, config, windows, *, pass_threshold)`` —
  RSI 평균회귀 baseline (`compute_rsi_mr_baseline`) 을 각 window 의 train·test
  구간에 두 번씩 호출하고 ``degradation_pct = (train_avg - test_avg) / train_avg``
  를 계산해 ``pass_threshold`` 와 비교, ``is_pass`` 판정.
- 최종 산출: ``WalkForwardResult(windows, per_window_metrics, aggregate_metrics)``.

현재 상태 (ADR-0023 C2 도입 — 2026-05-02)
- DTO + ``generate_windows`` + ``run_rsi_mr_walk_forward`` 본 구현 완료.
- ORB 경로의 ``run_walk_forward(loader: BacktestConfig, ...)`` 는 그대로
  ``NotImplementedError`` 유지 — Phase 5 별도 PR 에서 BacktestEngine 호출
  형태로 구현 예정. 본 PR 의 RSI MR 채택 후보 검증 (ADR-0023) 만 우선.

설계 원칙 (backtest 나머지 모듈과 동일 기조)
- 외부 I/O 없음. ``datetime.now()`` 미사용. 결정론.
- generic ``except Exception`` 금지. DTO 계약 위반은 ``RuntimeError`` (ADR-0003).
- ``Decimal`` 정확도 우선.
- 단일 프로세스 전용.

pass_threshold 결정 노트
- ADR-0024 (예정): ``degradation_pct <= 0.3`` (train→test 수익률 악화 30% 이하 PASS).
- 본 함수는 호출자 주입을 받는다 (기본값 하드코딩 없음). 운영 진입 가드는
  스크립트 레벨 또는 ADR-0024 본문에서 명시.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from stock_agent.backtest import rsi_mr as _rsi_mr_mod
from stock_agent.backtest.engine import BacktestConfig, BacktestMetrics
from stock_agent.backtest.loader import BarLoader
from stock_agent.backtest.rsi_mr import RSIMRBaselineConfig


def _add_months(d: date, months: int) -> date:
    """``d`` 에 ``months`` 개월을 가산. day clamp (월말 초과 시 해당 월 말일).

    예: ``_add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)`` (윤년).
    """
    total_index = d.month - 1 + months
    new_year = d.year + total_index // 12
    new_month = total_index % 12 + 1
    last_day = calendar.monthrange(new_year, new_month)[1]
    new_day = min(d.day, last_day)
    return date(new_year, new_month, new_day)


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
    """``[total_from, total_to]`` 구간을 rolling window 로 분할.

    각 i 번째 window:
    - ``train_from = total_from + i * step_months``
    - ``train_to = train_from + train_months - 1day``
    - ``test_from = train_to + 1day``
    - ``test_to = test_from + test_months - 1day``

    ``test_to <= total_to`` 인 동안 window 를 emit 한다.

    Raises:
        RuntimeError: ``total_from > total_to``, ``train_months <= 0``,
            ``test_months <= 0``, ``step_months <= 0``, 또는 첫 window 부터
            ``test_to > total_to`` (구간 부족).
    """
    if total_from > total_to:
        raise RuntimeError(f"total_from({total_from}) 는 total_to({total_to}) 이전이어야 합니다.")
    if train_months <= 0:
        raise RuntimeError(f"train_months 는 양수여야 합니다 (got={train_months})")
    if test_months <= 0:
        raise RuntimeError(f"test_months 는 양수여야 합니다 (got={test_months})")
    if step_months <= 0:
        raise RuntimeError(f"step_months 는 양수여야 합니다 (got={step_months})")

    one_day = timedelta(days=1)
    windows: list[WalkForwardWindow] = []
    i = 0
    while True:
        train_from = _add_months(total_from, i * step_months)
        train_to = _add_months(train_from, train_months) - one_day
        test_from = train_to + one_day
        test_to = _add_months(test_from, test_months) - one_day
        if test_to > total_to:
            break
        windows.append(
            WalkForwardWindow(
                train_from=train_from,
                train_to=train_to,
                test_from=test_from,
                test_to=test_to,
            )
        )
        i += 1

    if not windows:
        raise RuntimeError(
            f"총 구간({total_from} ~ {total_to}) 이 train_months({train_months}) + "
            f"test_months({test_months}) 보다 짧아 window 를 만들 수 없습니다."
        )
    return tuple(windows)


def run_walk_forward(
    loader: BarLoader,
    config: BacktestConfig,
    windows: tuple[WalkForwardWindow, ...],
) -> WalkForwardResult:
    """각 window 에 대해 ``BacktestEngine`` 실행 후 ``WalkForwardResult`` 집계.

    현재 스텁 — Phase 5 구현 대기. ADR-0023 C2 검증은 ORB 가 아닌 RSI 평균회귀
    채택 후보를 대상으로 하므로 본 함수 대신 ``run_rsi_mr_walk_forward`` 를
    사용한다.
    """
    raise NotImplementedError("Phase 5 구현 대기 — Issue #67 skeleton")


def run_rsi_mr_walk_forward(
    loader: BarLoader,
    config: RSIMRBaselineConfig,
    windows: tuple[WalkForwardWindow, ...],
    *,
    pass_threshold: Decimal,
) -> WalkForwardResult:
    """RSI 평균회귀 baseline walk-forward 평가 (ADR-0023 C2).

    각 ``window`` 마다 ``compute_rsi_mr_baseline`` 을 두 번 호출 (train + test)
    하고, train/test 평균 수익률의 degradation 을 ``pass_threshold`` 와 비교.

    Args:
        loader: ``BarLoader`` 구현체. multi-symbol 일봉 스트림 제공.
        config: RSI 평균회귀 평가 파라미터.
        windows: 평가 대상 walk-forward window 튜플. 1개 이상.
        pass_threshold: degradation 허용 임계치 (소수, 0 이상).

    Raises:
        RuntimeError: ``pass_threshold < 0``, ``windows`` 가 빈 tuple,
            또는 ``windows`` 가 ``test_from`` 기준 단조 증가하지 않을 때.

    Returns:
        ``WalkForwardResult`` — ``per_window_metrics`` 는 각 window 의 test
        구간 ``BacktestMetrics``, ``aggregate_metrics`` 는 train/test 평균
        + degradation + PASS 판정.
    """
    if pass_threshold < 0:
        raise RuntimeError(f"pass_threshold 는 0 이상이어야 합니다 (got={pass_threshold})")
    if not windows:
        raise RuntimeError("windows 는 1개 이상이어야 합니다.")
    for prev, cur in zip(windows, windows[1:], strict=False):
        if cur.test_from <= prev.test_from:
            raise RuntimeError(
                f"windows 시간 역행 — windows[i].test_from({prev.test_from}) "
                f"≥ windows[i+1].test_from({cur.test_from})."
            )

    train_returns: list[Decimal] = []
    test_returns: list[Decimal] = []
    per_window_metrics: list[BacktestMetrics] = []

    for window in windows:
        train_result = _rsi_mr_mod.compute_rsi_mr_baseline(
            loader, config, window.train_from, window.train_to
        )
        test_result = _rsi_mr_mod.compute_rsi_mr_baseline(
            loader, config, window.test_from, window.test_to
        )
        train_returns.append(train_result.metrics.total_return_pct)
        test_returns.append(test_result.metrics.total_return_pct)
        per_window_metrics.append(test_result.metrics)

    n = Decimal(len(windows))
    train_avg = sum(train_returns, Decimal("0")) / n
    test_avg = sum(test_returns, Decimal("0")) / n
    degradation = (train_avg - test_avg) / train_avg if train_avg > 0 else Decimal("0")
    is_pass = degradation <= pass_threshold

    aggregate = WalkForwardMetrics(
        train_avg_return_pct=train_avg,
        test_avg_return_pct=test_avg,
        degradation_pct=degradation,
        pass_threshold=pass_threshold,
        is_pass=is_pass,
    )
    return WalkForwardResult(
        windows=windows,
        per_window_metrics=tuple(per_window_metrics),
        aggregate_metrics=aggregate,
    )
