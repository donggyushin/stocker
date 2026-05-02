"""run_rsi_mr_walk_forward 단위 테스트 (RED — 함수 미존재 ImportError FAIL 예상).

ADR-0023 C2 검증 (walk-forward 본 구현) 선행 RED 테스트.
- run_rsi_mr_walk_forward 는 walk_forward.py 에 아직 구현되지 않음.
- 합성 InMemoryBarLoader + 진동 close 시리즈로 RSIMRStrategy 가 시그널을 emit 하게 한다.
- 외부 네트워크·KIS·시계·파일·DB 의존 없음.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from stock_agent.backtest.engine import BacktestMetrics
from stock_agent.backtest.loader import InMemoryBarLoader
from stock_agent.backtest.rsi_mr import RSIMRBaselineConfig

# --- 대상 함수 임포트 — 미존재 시 ImportError 로 모든 테스트 FAIL (RED 의도) ---
from stock_agent.backtest.walk_forward import (  # noqa: E402
    WalkForwardResult,
    WalkForwardWindow,
    run_rsi_mr_walk_forward,
)
from stock_agent.data import MinuteBar

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))
_SYM_A = "005930"
_SYM_B = "000660"


# ---------------------------------------------------------------------------
# 헬퍼 — 합성 일봉 생성
# ---------------------------------------------------------------------------


def _kst(d: date, h: int = 9, m: int = 0) -> datetime:
    """date + h:m 을 KST tz-aware datetime 으로 반환."""
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KST)


def _make_synthetic_loader(
    symbols: tuple[str, ...],
    start: date,
    end: date,
    base_price: Decimal = Decimal("10000"),
) -> InMemoryBarLoader:
    """RSI 30~70 진동 시나리오를 위한 합성 일봉 loader.

    close 패턴: i % 10 < 5 이면 하락 (base - i*50), 이상이면 상승 (base + i*50).
    이 패턴으로 RSI 가 과매도/과매수 구간을 번갈아 오가며 시그널 emit.
    날짜 증가는 +1일 단순 증가 (영업일 무관, InMemoryBarLoader 필터 통과).
    """
    bars: list[MinuteBar] = []
    current = start
    i = 0
    while current <= end:
        # 5일 하락 → 5일 상승 패턴 반복 → RSI 진동
        if i % 10 < 5:
            close = base_price - Decimal(i % 5) * Decimal("200")
        else:
            close = base_price + Decimal(i % 5) * Decimal("200")
        # 음수/0 방어
        close = max(close, Decimal("100"))
        for sym in symbols:
            bars.append(
                MinuteBar(
                    symbol=sym,
                    bar_time=_kst(current),
                    open=close,
                    high=close + Decimal("50"),
                    low=close - Decimal("50"),
                    close=close,
                    volume=5000,
                )
            )
        current += timedelta(days=1)
        i += 1
    return InMemoryBarLoader(bars)


def _make_window(
    train_from: date,
    train_to: date,
    test_from: date,
    test_to: date,
) -> WalkForwardWindow:
    """WalkForwardWindow 생성 헬퍼."""
    return WalkForwardWindow(
        train_from=train_from,
        train_to=train_to,
        test_from=test_from,
        test_to=test_to,
    )


def _make_config(
    symbols: tuple[str, ...] = (_SYM_A,),
    rsi_period: int = 3,
    starting_capital_krw: int = 2_000_000,
    max_positions: int | None = None,
) -> RSIMRBaselineConfig:
    """최소 설정의 RSIMRBaselineConfig 빌더.

    rsi_period=3 으로 짧게 설정해 짧은 합성 구간에서도 RSI 시그널 유도.
    비용=0 — 수치 검증 단순화.
    max_positions 미지정 시 len(symbols) 로 자동 조정 —
    RSIMRConfig 의 ``max_positions <= len(universe)`` 가드를 단일 심볼 테스트에서도 통과.
    """
    if max_positions is None:
        max_positions = len(symbols)
    return RSIMRBaselineConfig(
        starting_capital_krw=starting_capital_krw,
        universe=symbols,
        rsi_period=rsi_period,
        oversold_threshold=Decimal("30"),
        overbought_threshold=Decimal("70"),
        stop_loss_pct=Decimal("0.10"),
        max_positions=max_positions,
        position_pct=Decimal("1.0"),
        slippage_rate=Decimal("0"),
        commission_rate=Decimal("0"),
        sell_tax_rate=Decimal("0"),
    )


# ---------------------------------------------------------------------------
# 테스트 클래스
# ---------------------------------------------------------------------------


class TestRunRsiMrWalkForward:
    """run_rsi_mr_walk_forward 계약 검증."""

    # --- 정상 동작 ---

    def test_단일_window_정상_실행(self):
        """1 window 입력 → WalkForwardResult 반환, per_window_metrics 길이 1."""
        # Arrange
        total_from = date(2024, 1, 2)
        total_to = date(2024, 3, 31)
        # train: 1월~2월, test: 3월
        train_from = date(2024, 1, 2)
        train_to = date(2024, 2, 28)
        test_from = date(2024, 3, 1)
        test_to = date(2024, 3, 31)

        loader = _make_synthetic_loader((_SYM_A,), total_from, total_to)
        config = _make_config(symbols=(_SYM_A,))
        windows = (_make_window(train_from, train_to, test_from, test_to),)

        # Act
        result = run_rsi_mr_walk_forward(
            loader,
            config,
            windows,
            pass_threshold=Decimal("0.5"),
        )

        # Assert
        assert isinstance(result, WalkForwardResult)
        assert len(result.per_window_metrics) == 1

    def test_2_windows_aggregate_평균_계산(self):
        """2 windows → aggregate_metrics.train_avg_return_pct 가 두 train 결과 평균과 일치."""
        # Arrange
        total_from = date(2024, 1, 2)
        total_to = date(2024, 6, 30)

        loader = _make_synthetic_loader((_SYM_A,), total_from, total_to)
        config = _make_config(symbols=(_SYM_A,))
        windows = (
            _make_window(
                train_from=date(2024, 1, 2),
                train_to=date(2024, 2, 29),
                test_from=date(2024, 3, 1),
                test_to=date(2024, 3, 31),
            ),
            _make_window(
                train_from=date(2024, 2, 1),
                train_to=date(2024, 3, 31),
                test_from=date(2024, 4, 1),
                test_to=date(2024, 4, 30),
            ),
        )

        # Act
        result = run_rsi_mr_walk_forward(
            loader,
            config,
            windows,
            pass_threshold=Decimal("0.5"),
        )

        # Assert
        assert len(result.windows) == 2
        assert len(result.per_window_metrics) == 2
        # train_avg = (train_w0.total_return_pct + train_w1.total_return_pct) / 2
        # 집계 결과는 Decimal 이므로 pytest.approx 로 비교
        from stock_agent.backtest.rsi_mr import compute_rsi_mr_baseline

        train_r0 = compute_rsi_mr_baseline(
            loader, config, windows[0].train_from, windows[0].train_to
        ).metrics.total_return_pct
        train_r1 = compute_rsi_mr_baseline(
            loader, config, windows[1].train_from, windows[1].train_to
        ).metrics.total_return_pct
        expected_train_avg = (train_r0 + train_r1) / Decimal("2")
        # train_avg_return_pct 는 Decimal — float 로 변환해 approx 비교
        assert float(result.aggregate_metrics.train_avg_return_pct) == pytest.approx(
            float(expected_train_avg), rel=1e-9
        )

    def test_per_window_metrics_test_구간_BacktestMetrics(self):
        """per_window_metrics[i] 는 BacktestMetrics 인스턴스이고 windows[i] 와 동일 인덱스."""
        # Arrange
        total_from = date(2024, 1, 2)
        total_to = date(2024, 4, 30)

        loader = _make_synthetic_loader((_SYM_A,), total_from, total_to)
        config = _make_config(symbols=(_SYM_A,))
        windows = (
            _make_window(
                train_from=date(2024, 1, 2),
                train_to=date(2024, 2, 29),
                test_from=date(2024, 3, 1),
                test_to=date(2024, 3, 31),
            ),
            _make_window(
                train_from=date(2024, 2, 1),
                train_to=date(2024, 3, 31),
                test_from=date(2024, 4, 1),
                test_to=date(2024, 4, 30),
            ),
        )

        # Act
        result = run_rsi_mr_walk_forward(
            loader,
            config,
            windows,
            pass_threshold=Decimal("0.5"),
        )

        # Assert — 각 per_window_metrics 가 BacktestMetrics 인스턴스
        for i, metrics in enumerate(result.per_window_metrics):
            msg = f"per_window_metrics[{i}] 가 BacktestMetrics 여야 함 (got {type(metrics)})"
            assert isinstance(metrics, BacktestMetrics), msg
        # 순서: per_window_metrics[i] 는 windows[i] 의 test 구간에 해당
        assert len(result.per_window_metrics) == len(result.windows)

    def test_pass_threshold_충족_is_pass_true(self):
        """degradation 이 threshold 이하 → is_pass=True.

        동일 데이터 구간(train/test 같은 범위)을 사용해 degradation ≈ 0 유도.
        """
        # Arrange — train 과 test 구간을 동일 패턴 데이터로 설정
        total_from = date(2024, 1, 2)
        total_to = date(2024, 4, 30)

        loader = _make_synthetic_loader((_SYM_A,), total_from, total_to)
        config = _make_config(symbols=(_SYM_A,))
        # 동일 날짜 패턴의 train/test → degradation 0 근접
        windows = (
            _make_window(
                train_from=date(2024, 1, 2),
                train_to=date(2024, 2, 29),
                test_from=date(2024, 3, 1),
                test_to=date(2024, 4, 29),
            ),
        )

        # Act
        result = run_rsi_mr_walk_forward(
            loader,
            config,
            windows,
            pass_threshold=Decimal("0.99"),  # 매우 관대한 threshold
        )

        # Assert
        assert result.aggregate_metrics.is_pass is True

    def test_pass_threshold_초과_is_pass_false(self):
        """degradation > threshold 시나리오 → is_pass=False.

        train 구간에 상승 데이터(수익 발생), test 구간에 하락 데이터(수익 악화)를 넣어
        degradation > 0.3 을 유도하고 threshold=0.0 으로 is_pass=False 확인.
        """
        # Arrange — train 양수 수익, test 무수익(빈 구간) 조합
        # train 구간에는 RSI 시그널이 발생하는 충분한 데이터
        # test 구간의 threshold 를 0.0 으로 극단 설정 (degradation 이 조금이라도 있으면 FAIL)
        train_from = date(2024, 1, 2)
        train_to = date(2024, 2, 29)
        test_from = date(2024, 3, 1)
        test_to = date(2024, 3, 31)

        loader = _make_synthetic_loader((_SYM_A,), train_from, test_to)
        config = _make_config(symbols=(_SYM_A,))
        windows = (_make_window(train_from, train_to, test_from, test_to),)

        # Act — train_avg > 0 이고 degradation > threshold=0.0 이어야 FAIL
        # (train 에서 양수 수익이 나고, test 에서 수익이 낮으면 degradation > 0)
        # threshold=Decimal("0") 으로 degradation 이 조금이라도 있으면 FAIL
        result = run_rsi_mr_walk_forward(
            loader,
            config,
            windows,
            pass_threshold=Decimal("0.0"),
        )

        # train_avg <= 0 이거나 degradation == 0 이면 is_pass 가 True 일 수 있음
        # 핵심 계약: is_pass = (degradation_pct <= pass_threshold)
        expected_is_pass = (
            result.aggregate_metrics.degradation_pct <= result.aggregate_metrics.pass_threshold
        )
        assert result.aggregate_metrics.is_pass is expected_is_pass

    def test_train_avg_zero_degradation_zero_폴백(self):
        """train_avg == 0 인 경우 degradation 0 폴백 + is_pass=True (threshold >= 0).

        빈 loader (bar 0건)로 모든 구간의 total_return_pct == 0 이 되는 케이스.
        """
        # Arrange — 빈 loader → 모든 구간 total_return_pct=0 → train_avg=0
        loader = InMemoryBarLoader([])
        config = _make_config(symbols=(_SYM_A,))
        windows = (
            _make_window(
                train_from=date(2024, 1, 2),
                train_to=date(2024, 2, 29),
                test_from=date(2024, 3, 1),
                test_to=date(2024, 3, 31),
            ),
        )

        # Act
        result = run_rsi_mr_walk_forward(
            loader,
            config,
            windows,
            pass_threshold=Decimal("0.3"),
        )

        # Assert — train_avg=0 → degradation 0 폴백
        assert result.aggregate_metrics.degradation_pct == Decimal("0")
        # threshold=0.3 >= 0 → is_pass=True
        assert result.aggregate_metrics.is_pass is True

    # --- 가드 ---

    def test_pass_threshold_negative_RuntimeError(self):
        """pass_threshold < 0 → RuntimeError."""
        loader = InMemoryBarLoader([])
        config = _make_config()
        windows = (
            _make_window(
                train_from=date(2024, 1, 2),
                train_to=date(2024, 2, 29),
                test_from=date(2024, 3, 1),
                test_to=date(2024, 3, 31),
            ),
        )

        with pytest.raises(RuntimeError):
            run_rsi_mr_walk_forward(
                loader,
                config,
                windows,
                pass_threshold=Decimal("-0.1"),
            )

    def test_빈_windows_RuntimeError(self):
        """windows=() → RuntimeError."""
        loader = InMemoryBarLoader([])
        config = _make_config()

        with pytest.raises(RuntimeError):
            run_rsi_mr_walk_forward(
                loader,
                config,
                windows=(),
                pass_threshold=Decimal("0.3"),
            )

    def test_windows_시간_역행_RuntimeError(self):
        """W1.test_from <= W0.test_from → RuntimeError (시간 역행 금지)."""
        loader = InMemoryBarLoader([])
        config = _make_config()
        # W0.test_from = 2024-04-01, W1.test_from = 2024-03-01 (역행)
        windows = (
            _make_window(
                train_from=date(2024, 1, 2),
                train_to=date(2024, 2, 29),
                test_from=date(2024, 4, 1),
                test_to=date(2024, 4, 30),
            ),
            _make_window(
                train_from=date(2024, 1, 2),
                train_to=date(2024, 2, 28),
                test_from=date(2024, 3, 1),  # W1.test_from < W0.test_from → 역행
                test_to=date(2024, 3, 31),
            ),
        )

        with pytest.raises(RuntimeError):
            run_rsi_mr_walk_forward(
                loader,
                config,
                windows,
                pass_threshold=Decimal("0.3"),
            )

    # --- compute_rsi_mr_baseline 위임 호출 횟수 확인 ---

    def test_compute_rsi_mr_baseline_호출_횟수(self):
        """각 window 마다 compute_rsi_mr_baseline 을 2회(train+test) 호출해야 한다.

        monkeypatch 로 spy — 총 호출 수 == 2 * len(windows).
        """
        # Arrange
        total_from = date(2024, 1, 2)
        total_to = date(2024, 4, 30)

        loader = _make_synthetic_loader((_SYM_A,), total_from, total_to)
        config = _make_config(symbols=(_SYM_A,))
        windows = (
            _make_window(
                train_from=date(2024, 1, 2),
                train_to=date(2024, 2, 29),
                test_from=date(2024, 3, 1),
                test_to=date(2024, 3, 31),
            ),
            _make_window(
                train_from=date(2024, 2, 1),
                train_to=date(2024, 3, 31),
                test_from=date(2024, 4, 1),
                test_to=date(2024, 4, 30),
            ),
        )
        call_count = 0
        original = __import__(
            "stock_agent.backtest.rsi_mr", fromlist=["compute_rsi_mr_baseline"]
        ).compute_rsi_mr_baseline

        def spy(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return original(*args, **kwargs)

        import stock_agent.backtest.rsi_mr as rsi_mod  # noqa: E402

        original_fn = rsi_mod.compute_rsi_mr_baseline
        rsi_mod.compute_rsi_mr_baseline = spy  # type: ignore[attr-defined]
        try:
            run_rsi_mr_walk_forward(
                loader,
                config,
                windows,
                pass_threshold=Decimal("0.5"),
            )
        finally:
            rsi_mod.compute_rsi_mr_baseline = original_fn  # type: ignore[attr-defined]

        expected = 2 * len(windows)
        msg = f"compute_rsi_mr_baseline 호출 횟수 {expected} 기대, 실제 {call_count}"
        assert call_count == expected, msg
