"""walk_forward.py 공개 계약 단위 테스트 (RED — 심볼 부재 FAIL 예상).

WalkForwardWindow / WalkForwardMetrics / WalkForwardResult DTO 가드 +
generate_windows / run_walk_forward 스텁 NotImplementedError 를 검증한다.
외부 네트워크 · KIS · 시계 의존 없음 — 합성 InMemoryBarLoader 만 사용.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from stock_agent.backtest import (
    BacktestConfig,
    BacktestMetrics,
    InMemoryBarLoader,
    WalkForwardMetrics,
    WalkForwardResult,
    WalkForwardWindow,
    generate_windows,
    run_walk_forward,
)

# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------


def _make_window(
    train_from: date = date(2024, 1, 2),
    train_to: date = date(2024, 6, 28),
    test_from: date = date(2024, 7, 1),
    test_to: date = date(2024, 8, 30),
) -> WalkForwardWindow:
    """정상 WalkForwardWindow 생성 헬퍼."""
    return WalkForwardWindow(
        train_from=train_from,
        train_to=train_to,
        test_from=test_from,
        test_to=test_to,
    )


def _make_metrics(
    train_avg: str = "0.05",
    test_avg: str = "0.03",
    degradation: str = "0.40",
    pass_threshold: str = "0.50",
    is_pass: bool = True,
) -> WalkForwardMetrics:
    """정상 WalkForwardMetrics 생성 헬퍼."""
    return WalkForwardMetrics(
        train_avg_return_pct=Decimal(train_avg),
        test_avg_return_pct=Decimal(test_avg),
        degradation_pct=Decimal(degradation),
        pass_threshold=Decimal(pass_threshold),
        is_pass=is_pass,
    )


def _make_backtest_metrics() -> BacktestMetrics:
    """테스트용 BacktestMetrics 헬퍼."""
    return BacktestMetrics(
        total_return_pct=Decimal("0.05"),
        max_drawdown_pct=Decimal("-0.02"),
        sharpe_ratio=Decimal("1.2"),
        win_rate=Decimal("0.6"),
        avg_pnl_ratio=Decimal("1.5"),
        trades_per_day=Decimal("0.8"),
        net_pnl_krw=50000,
    )


def _make_result(
    num_windows: int = 2,
) -> WalkForwardResult:
    """정상 WalkForwardResult 생성 헬퍼 (num_windows 개 윈도우)."""
    windows = tuple(
        _make_window(
            train_from=date(2024, i, 2) if i <= 6 else date(2024, 1, 2),
            train_to=date(2024, i + 1, 28) if i + 1 <= 6 else date(2024, 6, 28),
            test_from=date(2024, i + 2, 1) if i + 2 <= 8 else date(2024, 7, 1),
            test_to=date(2024, i + 3, 28) if i + 3 <= 9 else date(2024, 8, 30),
        )
        for i in range(1, num_windows + 1)
    )
    per_window = tuple(_make_backtest_metrics() for _ in range(num_windows))
    aggregate = _make_metrics()
    return WalkForwardResult(
        windows=windows,
        per_window_metrics=per_window,
        aggregate_metrics=aggregate,
    )


# ---------------------------------------------------------------------------
# A. WalkForwardWindow DTO 가드
# ---------------------------------------------------------------------------


class TestWalkForwardWindow:
    def test_정상_생성_happy_path(self):
        """4 필드 모두 채운 정상 케이스 — train_to < test_from."""
        window = _make_window()
        assert window.train_from == date(2024, 1, 2)
        assert window.train_to == date(2024, 6, 28)
        assert window.test_from == date(2024, 7, 1)
        assert window.test_to == date(2024, 8, 30)

    def test_train_from_gt_train_to_RuntimeError(self):
        """train_from > train_to → RuntimeError."""
        with pytest.raises(RuntimeError):
            WalkForwardWindow(
                train_from=date(2024, 7, 1),
                train_to=date(2024, 1, 2),
                test_from=date(2024, 8, 1),
                test_to=date(2024, 9, 30),
            )

    def test_test_from_gt_test_to_RuntimeError(self):
        """test_from > test_to → RuntimeError."""
        with pytest.raises(RuntimeError):
            WalkForwardWindow(
                train_from=date(2024, 1, 2),
                train_to=date(2024, 6, 28),
                test_from=date(2024, 9, 1),
                test_to=date(2024, 7, 31),
            )

    def test_train_to_eq_test_from_RuntimeError(self):
        """train_to == test_from → RuntimeError (경계 — 엄격 less than 강제)."""
        d = date(2024, 7, 1)
        with pytest.raises(RuntimeError):
            WalkForwardWindow(
                train_from=date(2024, 1, 2),
                train_to=d,
                test_from=d,
                test_to=date(2024, 8, 30),
            )

    def test_train_to_gt_test_from_RuntimeError(self):
        """train_to > test_from → RuntimeError (train·test 기간 중첩 금지)."""
        with pytest.raises(RuntimeError):
            WalkForwardWindow(
                train_from=date(2024, 1, 2),
                train_to=date(2024, 8, 1),
                test_from=date(2024, 7, 1),
                test_to=date(2024, 9, 30),
            )

    def test_frozen_필드_대입_FrozenInstanceError(self):
        """frozen dataclass — 필드 대입 시 FrozenInstanceError."""
        from dataclasses import FrozenInstanceError

        window = _make_window()
        with pytest.raises(FrozenInstanceError):
            window.train_from = date(2025, 1, 1)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# B. WalkForwardMetrics DTO 가드
# ---------------------------------------------------------------------------


class TestWalkForwardMetrics:
    def test_정상_생성_is_pass_true(self):
        """degradation_pct <= pass_threshold → is_pass=True 정상 생성."""
        m = _make_metrics(
            train_avg="0.05",
            test_avg="0.03",
            degradation="0.40",
            pass_threshold="0.50",
            is_pass=True,
        )
        assert m.is_pass is True
        assert m.pass_threshold == Decimal("0.50")

    def test_pass_threshold_음수_RuntimeError(self):
        """pass_threshold < 0 → RuntimeError."""
        with pytest.raises(RuntimeError):
            WalkForwardMetrics(
                train_avg_return_pct=Decimal("0.05"),
                test_avg_return_pct=Decimal("0.03"),
                degradation_pct=Decimal("0.40"),
                pass_threshold=Decimal("-0.01"),
                is_pass=True,
            )

    def test_frozen_필드_대입_FrozenInstanceError(self):
        """frozen dataclass — 필드 대입 시 FrozenInstanceError."""
        from dataclasses import FrozenInstanceError

        m = _make_metrics()
        with pytest.raises(FrozenInstanceError):
            m.is_pass = False  # type: ignore[misc]

    def test_pass_threshold_zero_허용(self):
        """pass_threshold == 0 → 허용 (non-negative 경계)."""
        m = WalkForwardMetrics(
            train_avg_return_pct=Decimal("0.05"),
            test_avg_return_pct=Decimal("0.03"),
            degradation_pct=Decimal("0.40"),
            pass_threshold=Decimal("0"),
            is_pass=True,
        )
        assert m.pass_threshold == Decimal("0")


# ---------------------------------------------------------------------------
# C. WalkForwardResult DTO 가드
# ---------------------------------------------------------------------------


class TestWalkForwardResult:
    def test_정상_생성_윈도우_2개(self):
        """windows 2개 + per_window_metrics 2개 + aggregate_metrics 정상 생성."""
        result = _make_result(num_windows=2)
        assert len(result.windows) == 2
        assert len(result.per_window_metrics) == 2
        assert isinstance(result.aggregate_metrics, WalkForwardMetrics)

    def test_빈_windows_RuntimeError(self):
        """windows=() → RuntimeError."""
        with pytest.raises(RuntimeError):
            WalkForwardResult(
                windows=(),
                per_window_metrics=(),
                aggregate_metrics=_make_metrics(),
            )

    def test_windows_per_window_metrics_길이_불일치_RuntimeError(self):
        """len(windows) != len(per_window_metrics) → RuntimeError."""
        window = _make_window()
        with pytest.raises(RuntimeError):
            WalkForwardResult(
                windows=(window,),
                per_window_metrics=(_make_backtest_metrics(), _make_backtest_metrics()),
                aggregate_metrics=_make_metrics(),
            )

    def test_frozen_필드_대입_FrozenInstanceError(self):
        """frozen dataclass — 필드 대입 시 FrozenInstanceError."""
        from dataclasses import FrozenInstanceError

        result = _make_result(num_windows=1)
        with pytest.raises(FrozenInstanceError):
            result.windows = ()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# D. generate_windows 스텁 — NotImplementedError
# ---------------------------------------------------------------------------


class TestGenerateWindowsStub:
    def test_호출_시_NotImplementedError(self):
        """임의 인자로 호출 → NotImplementedError."""
        with pytest.raises(NotImplementedError):
            generate_windows(
                total_from=date(2023, 1, 2),
                total_to=date(2024, 12, 31),
                train_months=6,
                test_months=2,
                step_months=1,
            )

    def test_예외_메시지_Phase5_포함(self):
        """NotImplementedError 메시지에 'Phase 5' 가 포함된다."""
        with pytest.raises(NotImplementedError, match="Phase 5"):
            generate_windows(
                total_from=date(2023, 1, 2),
                total_to=date(2024, 12, 31),
            )


# ---------------------------------------------------------------------------
# E. run_walk_forward 스텁 — NotImplementedError
# ---------------------------------------------------------------------------


class TestRunWalkForwardStub:
    def test_호출_시_NotImplementedError(self):
        """InMemoryBarLoader + 기본 BacktestConfig + 1개 window → NotImplementedError."""
        loader = InMemoryBarLoader([])
        config = BacktestConfig(starting_capital_krw=1_000_000)
        windows = (_make_window(),)
        with pytest.raises(NotImplementedError):
            run_walk_forward(loader, config, windows)

    def test_예외_메시지_Phase5_포함(self):
        """NotImplementedError 메시지에 'Phase 5' 가 포함된다."""
        loader = InMemoryBarLoader([])
        config = BacktestConfig(starting_capital_krw=1_000_000)
        windows = (_make_window(),)
        with pytest.raises(NotImplementedError, match="Phase 5"):
            run_walk_forward(loader, config, windows)
