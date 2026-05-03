"""ADR-0023 C4 검증 — RSI 평균회귀 sensitivity grid 단위 테스트 (RED).

대상 모듈: src/stock_agent/backtest/rsi_mr_sensitivity.py (미존재 — import 단계 FAIL 예상).
검증 범위:
  - RSIMRParameterAxis 생성·가드
  - RSIMRSensitivityGrid 생성·size·iter_combinations 결정론
  - RSIMRSensitivityRow 생성·ADR-0022 게이트 판정
  - step_f_rsi_mr_grid() 96 조합·5축·현행 파라미터 포함 여부
  - run_rsi_mr_sensitivity() 직렬 실행·콜백·게이트 판정·start>end 가드
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from stock_agent.backtest.engine import BacktestMetrics
from stock_agent.backtest.loader import InMemoryBarLoader
from stock_agent.backtest.rsi_mr import RSIMRBaselineConfig

# --------------------------------------------------------------------------
# 대상 모듈 임포트 — 미존재 시 ImportError/ModuleNotFoundError 로 FAIL (RED 의도)
# --------------------------------------------------------------------------
from stock_agent.backtest.rsi_mr_sensitivity import (  # noqa: E402
    RSIMRParameterAxis,
    RSIMRSensitivityGrid,
    RSIMRSensitivityRow,
    run_rsi_mr_sensitivity,
    step_f_rsi_mr_grid,
)
from stock_agent.data import MinuteBar

# --------------------------------------------------------------------------
# 공통 헬퍼
# --------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_SYM_A = "005930"
_SYM_B = "000660"
_START = date(2025, 1, 2)
_END = date(2025, 1, 31)


def _kst(d: date, h: int = 9, m: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=KST)


def _make_bar(
    symbol: str,
    d: date,
    close: int | str | Decimal,
    *,
    low: int | str | Decimal | None = None,
    high: int | str | Decimal | None = None,
) -> MinuteBar:
    c = Decimal(str(close))
    lo = Decimal(str(low)) if low is not None else c
    hi = Decimal(str(high)) if high is not None else c
    return MinuteBar(
        symbol=symbol,
        bar_time=_kst(d),
        open=c,
        high=hi,
        low=lo,
        close=c,
        volume=1000,
    )


def _make_daily_series(
    symbol: str,
    start_date: date,
    closes: list[int],
) -> list[MinuteBar]:
    bars = []
    for i, c in enumerate(closes):
        d = start_date + timedelta(days=i)
        bars.append(_make_bar(symbol, d, c))
    return bars


def _make_metrics(
    *,
    total_return_pct: Decimal = Decimal("0.10"),
    max_drawdown_pct: Decimal = Decimal("-0.05"),
    sharpe_ratio: Decimal = Decimal("1.5"),
    win_rate: Decimal = Decimal("0.5"),
    avg_pnl_ratio: Decimal = Decimal("2.0"),
    trades_per_day: Decimal = Decimal("0.5"),
    net_pnl_krw: int = 100_000,
) -> BacktestMetrics:
    """합성 BacktestMetrics 생성 헬퍼."""
    return BacktestMetrics(
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe_ratio,
        win_rate=win_rate,
        avg_pnl_ratio=avg_pnl_ratio,
        trades_per_day=trades_per_day,
        net_pnl_krw=net_pnl_krw,
    )


def _make_row(
    *,
    params: tuple[tuple[str, Any], ...] | None = None,
    metrics: BacktestMetrics | None = None,
    trade_count: int = 10,
    dca_alpha_pct: Decimal = Decimal("0.05"),
    gate1_pass: bool = True,
    gate2_pass: bool = True,
    gate3_pass: bool = True,
    all_gates_pass: bool = True,
) -> RSIMRSensitivityRow:
    if params is None:
        params = (("rsi_period", 14), ("stop_loss_pct", Decimal("0.03")))
    if metrics is None:
        metrics = _make_metrics()
    return RSIMRSensitivityRow(
        params=params,
        metrics=metrics,
        trade_count=trade_count,
        dca_alpha_pct=dca_alpha_pct,
        gate1_pass=gate1_pass,
        gate2_pass=gate2_pass,
        gate3_pass=gate3_pass,
        all_gates_pass=all_gates_pass,
    )


def _default_base_config(universe: tuple[str, ...] = (_SYM_A,)) -> RSIMRBaselineConfig:
    """비용 0 최소 설정 빌더."""
    return RSIMRBaselineConfig(
        starting_capital_krw=2_000_000,
        universe=universe,
        rsi_period=5,
        oversold_threshold=Decimal("30"),
        overbought_threshold=Decimal("70"),
        stop_loss_pct=Decimal("0.03"),
        max_positions=10,
        position_pct=Decimal("1.0"),
        slippage_rate=Decimal("0"),
        commission_rate=Decimal("0"),
        sell_tax_rate=Decimal("0"),
    )


# ===========================================================================
# 1. TestRSIMRParameterAxis
# ===========================================================================


class TestRSIMRParameterAxis:
    """RSIMRParameterAxis DTO 생성·가드 검증."""

    def test_정상_생성(self):
        """name, values 정상 → 인스턴스 생성."""
        axis = RSIMRParameterAxis(
            name="rsi_period",
            values=(10, 14, 21),
        )
        assert axis.name == "rsi_period"
        assert axis.values == (10, 14, 21)

    def test_빈_name_RuntimeError(self):
        """name 빈 문자열 → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRParameterAxis(name="", values=(10, 14))

    def test_빈_values_RuntimeError(self):
        """values 빈 tuple → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRParameterAxis(name="rsi_period", values=())

    def test_중복_values_RuntimeError(self):
        """values 중복 포함 → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRParameterAxis(name="rsi_period", values=(14, 14, 21))

    def test_frozen_수정_FrozenInstanceError(self):
        """frozen=True → 수정 시 FrozenInstanceError."""
        axis = RSIMRParameterAxis(name="rsi_period", values=(10, 14))
        with pytest.raises(FrozenInstanceError):
            axis.name = "other"  # type: ignore[misc]


# ===========================================================================
# 2. TestRSIMRSensitivityGrid
# ===========================================================================


class TestRSIMRSensitivityGrid:
    """RSIMRSensitivityGrid 생성·size·iter_combinations 검증."""

    def test_정상_생성_size_정확(self):
        """2축 2×3=6 조합 size 확인."""
        grid = RSIMRSensitivityGrid(
            axes=(
                RSIMRParameterAxis(name="rsi_period", values=(10, 14)),
                RSIMRParameterAxis(
                    name="stop_loss_pct",
                    values=(Decimal("0.02"), Decimal("0.03"), Decimal("0.04")),
                ),
            )
        )
        assert grid.size == 6

    def test_빈_axes_RuntimeError(self):
        """axes 빈 tuple → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRSensitivityGrid(axes=())

    def test_축_이름_중복_RuntimeError(self):
        """동일 이름 축 2개 → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRSensitivityGrid(
                axes=(
                    RSIMRParameterAxis(name="rsi_period", values=(10, 14)),
                    RSIMRParameterAxis(name="rsi_period", values=(21, 28)),
                )
            )

    def test_iter_combinations_결정론_축_선언_순서_보존(self):
        """iter_combinations 의 각 dict 는 축 선언 순서대로 키를 갖는다."""
        grid = RSIMRSensitivityGrid(
            axes=(
                RSIMRParameterAxis(name="rsi_period", values=(10, 14)),
                RSIMRParameterAxis(name="stop_loss_pct", values=(Decimal("0.02"), Decimal("0.03"))),
            )
        )
        combos = list(grid.iter_combinations())
        assert len(combos) == 4
        # 첫 번째 dict 의 키 순서 = 선언 순서
        assert list(combos[0].keys()) == ["rsi_period", "stop_loss_pct"]

    def test_iter_combinations_마지막_축이_가장_빠른_회전(self):
        """마지막 축(stop_loss_pct)이 먼저 변화한다 (inner loop)."""
        grid = RSIMRSensitivityGrid(
            axes=(
                RSIMRParameterAxis(name="rsi_period", values=(10, 14)),
                RSIMRParameterAxis(name="stop_loss_pct", values=(Decimal("0.02"), Decimal("0.03"))),
            )
        )
        combos = list(grid.iter_combinations())
        # 첫 2개: rsi_period=10 고정, stop_loss_pct 변화
        assert combos[0] == {"rsi_period": 10, "stop_loss_pct": Decimal("0.02")}
        assert combos[1] == {"rsi_period": 10, "stop_loss_pct": Decimal("0.03")}
        assert combos[2] == {"rsi_period": 14, "stop_loss_pct": Decimal("0.02")}
        assert combos[3] == {"rsi_period": 14, "stop_loss_pct": Decimal("0.03")}

    def test_size_프로퍼티_cartesian_product(self):
        """3축 3×2×4=24 조합."""
        grid = RSIMRSensitivityGrid(
            axes=(
                RSIMRParameterAxis(name="rsi_period", values=(10, 14, 21)),
                RSIMRParameterAxis(name="max_positions", values=(5, 10)),
                RSIMRParameterAxis(
                    name="stop_loss_pct",
                    values=(Decimal("0.02"), Decimal("0.03"), Decimal("0.04"), Decimal("0.05")),
                ),
            )
        )
        assert grid.size == 24

    def test_단일_축_그리드_동작(self):
        """축 1개짜리 그리드도 정상 동작."""
        grid = RSIMRSensitivityGrid(
            axes=(RSIMRParameterAxis(name="rsi_period", values=(10, 14, 21)),)
        )
        assert grid.size == 3
        combos = list(grid.iter_combinations())
        assert combos == [
            {"rsi_period": 10},
            {"rsi_period": 14},
            {"rsi_period": 21},
        ]


# ===========================================================================
# 3. TestRSIMRSensitivityRow
# ===========================================================================


class TestRSIMRSensitivityRow:
    """RSIMRSensitivityRow 생성·게이트 판정 검증."""

    def test_정상_생성_all_gates_pass(self):
        """게이트 3종 PASS → all_gates_pass=True."""
        metrics = _make_metrics(
            max_drawdown_pct=Decimal("-0.10"),
            sharpe_ratio=Decimal("1.5"),
        )
        row = RSIMRSensitivityRow(
            params=(("rsi_period", 14), ("stop_loss_pct", Decimal("0.03"))),
            metrics=metrics,
            trade_count=10,
            dca_alpha_pct=Decimal("0.05"),
            gate1_pass=True,
            gate2_pass=True,
            gate3_pass=True,
            all_gates_pass=True,
        )
        assert row.all_gates_pass is True
        assert row.gate1_pass is True
        assert row.gate2_pass is True
        assert row.gate3_pass is True

    def test_gate1_FAIL_MDD_깊음(self):
        """MDD = -0.30 < -0.25 → gate1_pass=False."""
        metrics = _make_metrics(max_drawdown_pct=Decimal("-0.30"))
        row = RSIMRSensitivityRow(
            params=(("rsi_period", 14),),
            metrics=metrics,
            trade_count=5,
            dca_alpha_pct=Decimal("0.05"),
            gate1_pass=False,
            gate2_pass=True,
            gate3_pass=True,
            all_gates_pass=False,
        )
        assert row.gate1_pass is False
        assert row.all_gates_pass is False

    def test_gate2_FAIL_dca_alpha_음수(self):
        """dca_alpha_pct = -0.05 ≤ 0 → gate2_pass=False."""
        metrics = _make_metrics()
        row = RSIMRSensitivityRow(
            params=(("rsi_period", 14),),
            metrics=metrics,
            trade_count=5,
            dca_alpha_pct=Decimal("-0.05"),
            gate1_pass=True,
            gate2_pass=False,
            gate3_pass=True,
            all_gates_pass=False,
        )
        assert row.gate2_pass is False
        assert row.all_gates_pass is False

    def test_gate3_FAIL_sharpe_낮음(self):
        """Sharpe = 0.2 ≤ 0.3 → gate3_pass=False."""
        metrics = _make_metrics(sharpe_ratio=Decimal("0.2"))
        row = RSIMRSensitivityRow(
            params=(("rsi_period", 14),),
            metrics=metrics,
            trade_count=5,
            dca_alpha_pct=Decimal("0.05"),
            gate1_pass=True,
            gate2_pass=True,
            gate3_pass=False,
            all_gates_pass=False,
        )
        assert row.gate3_pass is False
        assert row.all_gates_pass is False

    def test_all_gates_pass_1개라도_FAIL이면_False(self):
        """gate1~gate3 중 1개라도 False → all_gates_pass=False."""
        row = RSIMRSensitivityRow(
            params=(("rsi_period", 14),),
            metrics=_make_metrics(),
            trade_count=5,
            dca_alpha_pct=Decimal("0.05"),
            gate1_pass=True,
            gate2_pass=False,
            gate3_pass=True,
            all_gates_pass=False,
        )
        assert not row.all_gates_pass

    def test_params_축_이름_중복_RuntimeError(self):
        """params 에 중복 이름 → RuntimeError."""
        with pytest.raises(RuntimeError):
            RSIMRSensitivityRow(
                params=(("rsi_period", 14), ("rsi_period", 21)),
                metrics=_make_metrics(),
                trade_count=5,
                dca_alpha_pct=Decimal("0.05"),
                gate1_pass=True,
                gate2_pass=True,
                gate3_pass=True,
                all_gates_pass=True,
            )


# ===========================================================================
# 4. TestStepFRSIMRGrid
# ===========================================================================


class TestStepFRSIMRGrid:
    """step_f_rsi_mr_grid() 96 조합·5축·현행 파라미터 포함 검증."""

    def test_size_96(self):
        """5축 Cartesian product = 3×2×2×4×2 = 96."""
        grid = step_f_rsi_mr_grid()
        assert grid.size == 96

    def test_5축_이름_정확(self):
        """axes 이름이 5종 정확히 포함되어야 한다."""
        grid = step_f_rsi_mr_grid()
        axis_names = {ax.name for ax in grid.axes}
        expected_names = {
            "rsi_period",
            "oversold_threshold",
            "overbought_threshold",
            "stop_loss_pct",
            "max_positions",
        }
        assert axis_names == expected_names

    def test_현행_파라미터_조합_포함(self):
        """현행 PR5 파라미터 (rsi_period=14, oversold=30, overbought=70,
        stop_loss=0.03, max_positions=10) 가 iter_combinations 결과에 포함."""
        grid = step_f_rsi_mr_grid()
        combos = list(grid.iter_combinations())
        target = {
            "rsi_period": 14,
            "oversold_threshold": Decimal("30"),
            "overbought_threshold": Decimal("70"),
            "stop_loss_pct": Decimal("0.03"),
            "max_positions": 10,
        }
        assert target in combos, "현행 PR5 파라미터 조합이 그리드에 없습니다"

    def test_각_축_후보값_정확(self):
        """각 축의 후보값이 명세와 일치한다."""
        grid = step_f_rsi_mr_grid()
        axis_by_name = {ax.name: ax for ax in grid.axes}

        # rsi_period: (10, 14, 21)
        assert set(axis_by_name["rsi_period"].values) == {10, 14, 21}

        # oversold_threshold: (25, 30)
        assert set(axis_by_name["oversold_threshold"].values) == {Decimal("25"), Decimal("30")}

        # overbought_threshold: (70, 75)
        assert set(axis_by_name["overbought_threshold"].values) == {Decimal("70"), Decimal("75")}

        # stop_loss_pct: (0.02, 0.03, 0.04, 0.05)
        assert set(axis_by_name["stop_loss_pct"].values) == {
            Decimal("0.02"),
            Decimal("0.03"),
            Decimal("0.04"),
            Decimal("0.05"),
        }

        # max_positions: (5, 10)
        assert set(axis_by_name["max_positions"].values) == {5, 10}

    def test_모든_조합이_RSIMRConfig_가드_통과(self):
        """모든 96 조합이 RSIMRConfig.__post_init__ 검증을 통과해야 한다.

        oversold < overbought 등 필드 제약 포함.
        """
        from stock_agent.strategy.rsi_mr import RSIMRConfig

        grid = step_f_rsi_mr_grid()
        failed: list[dict[str, Any]] = []
        for combo in grid.iter_combinations():
            try:
                RSIMRConfig(
                    universe=(_SYM_A,),
                    rsi_period=combo["rsi_period"],
                    oversold_threshold=combo["oversold_threshold"],
                    overbought_threshold=combo["overbought_threshold"],
                    stop_loss_pct=combo["stop_loss_pct"],
                    max_positions=10,  # universe=1 종목이지만 기본값=10 은 허용
                    position_pct=Decimal("1.0"),
                )
            except RuntimeError as exc:
                failed.append({"combo": combo, "error": str(exc)})

        assert not failed, f"RSIMRConfig 가드 통과 실패 조합: {failed}"


# ===========================================================================
# 5. TestRunRsiMrSensitivity
# ===========================================================================


def _make_rsi_triggering_bars(
    symbol: str,
    start_date: date,
    rsi_period: int = 5,
    n_sessions: int = 30,
) -> list[MinuteBar]:
    """RSI 계산이 가능하도록 하락·상승 패턴 일봉 시리즈 생성.

    처음 rsi_period+1 개 = 하락 (oversold 유도 가능),
    이후 = 상승 (overbought 유도 가능).
    실제 시그널 발생 여부보다 RSI 계산에 필요한 bar 수 충족이 목적.
    """
    bars: list[MinuteBar] = []
    for i in range(n_sessions):
        d = start_date + timedelta(days=i)
        close = (
            1000 - i * 10 if i < rsi_period + 2 else 900 + (i - rsi_period - 2) * 10  # 하락  # 상승
        )
        bars.append(_make_bar(symbol, d, max(close, 100)))
    return bars


class TestRunRsiMrSensitivity:
    """run_rsi_mr_sensitivity() 직렬 실행·결정론·게이트 판정 검증."""

    def _small_grid(self) -> RSIMRSensitivityGrid:
        """2 조합 소형 그리드."""
        return RSIMRSensitivityGrid(
            axes=(
                RSIMRParameterAxis(
                    name="rsi_period",
                    values=(5, 7),
                ),
            )
        )

    def _make_loader(self, rsi_period: int = 5) -> InMemoryBarLoader:
        """RSI 계산 충분한 합성 일봉 InMemoryBarLoader."""
        bars = _make_rsi_triggering_bars(_SYM_A, _START, rsi_period=rsi_period, n_sessions=40)
        return InMemoryBarLoader(bars)

    def test_직렬실행_결과_길이_grid_size와_일치(self):
        """2 조합 그리드 → 결과 tuple 길이=2."""
        grid = self._small_grid()
        base_cfg = _default_base_config(universe=(_SYM_A,))
        loader = self._make_loader()

        rows = run_rsi_mr_sensitivity(
            loader=loader,
            base_config=base_cfg,
            grid=grid,
            start=_START,
            end=_START + timedelta(days=39),
            dca_baseline_return_pct=Decimal("0.05"),
        )
        assert len(rows) == grid.size

    def test_조합_순서_결정론적(self):
        """결과 rows 의 params 순서 = grid.iter_combinations 순서."""
        grid = self._small_grid()
        base_cfg = _default_base_config(universe=(_SYM_A,))
        loader = self._make_loader()

        combos = list(grid.iter_combinations())
        rows = run_rsi_mr_sensitivity(
            loader=loader,
            base_config=base_cfg,
            grid=grid,
            start=_START,
            end=_START + timedelta(days=39),
            dca_baseline_return_pct=Decimal("0.05"),
        )
        # 각 row 의 rsi_period 값이 combo 선언 순서와 동일해야 한다
        for i, (combo, row) in enumerate(zip(combos, rows, strict=True)):
            row_dict = dict(row.params)
            msg = (
                f"rows[{i}].params rsi_period 불일치: "
                f"expected={combo['rsi_period']}, got={row_dict['rsi_period']}"
            )
            assert row_dict["rsi_period"] == combo["rsi_period"], msg

    def test_dca_alpha_계산_정확(self):
        """dca_alpha_pct = metrics.total_return_pct - dca_baseline_return_pct."""
        grid = self._small_grid()
        base_cfg = _default_base_config(universe=(_SYM_A,))
        loader = self._make_loader()
        dca_baseline = Decimal("0.10")

        rows = run_rsi_mr_sensitivity(
            loader=loader,
            base_config=base_cfg,
            grid=grid,
            start=_START,
            end=_START + timedelta(days=39),
            dca_baseline_return_pct=dca_baseline,
        )
        for row in rows:
            expected_alpha = row.metrics.total_return_pct - dca_baseline
            msg = f"dca_alpha_pct 불일치: expected={expected_alpha}, got={row.dca_alpha_pct}"
            assert row.dca_alpha_pct == expected_alpha, msg

    def test_게이트_3종_판정_정확(self):
        """ADR-0022 게이트 3종 자동 판정이 임계값 기준과 일치."""
        grid = self._small_grid()
        base_cfg = _default_base_config(universe=(_SYM_A,))
        loader = self._make_loader()

        rows = run_rsi_mr_sensitivity(
            loader=loader,
            base_config=base_cfg,
            grid=grid,
            start=_START,
            end=_START + timedelta(days=39),
            dca_baseline_return_pct=Decimal("0.10"),
        )
        for row in rows:
            # gate1: max_drawdown_pct > -0.25
            expected_gate1 = row.metrics.max_drawdown_pct > Decimal("-0.25")
            msg1 = (
                f"gate1_pass 불일치: expected={expected_gate1}, "
                f"got={row.gate1_pass}, mdd={row.metrics.max_drawdown_pct}"
            )
            assert row.gate1_pass == expected_gate1, msg1
            # gate2: dca_alpha_pct > 0
            expected_gate2 = row.dca_alpha_pct > Decimal("0")
            msg2 = (
                f"gate2_pass 불일치: expected={expected_gate2}, "
                f"got={row.gate2_pass}, alpha={row.dca_alpha_pct}"
            )
            assert row.gate2_pass == expected_gate2, msg2
            # gate3: sharpe_ratio > 0.3
            expected_gate3 = row.metrics.sharpe_ratio > Decimal("0.3")
            msg3 = (
                f"gate3_pass 불일치: expected={expected_gate3}, "
                f"got={row.gate3_pass}, sharpe={row.metrics.sharpe_ratio}"
            )
            assert row.gate3_pass == expected_gate3, msg3
            # all_gates_pass = gate1 AND gate2 AND gate3
            expected_all = expected_gate1 and expected_gate2 and expected_gate3
            assert row.all_gates_pass == expected_all

    def test_on_row_콜백_호출_횟수_grid_size(self):
        """on_row 콜백 호출 횟수 = grid.size."""
        grid = self._small_grid()
        base_cfg = _default_base_config(universe=(_SYM_A,))
        loader = self._make_loader()

        called_rows: list[RSIMRSensitivityRow] = []

        def on_row(row: RSIMRSensitivityRow) -> None:
            called_rows.append(row)

        rows = run_rsi_mr_sensitivity(
            loader=loader,
            base_config=base_cfg,
            grid=grid,
            start=_START,
            end=_START + timedelta(days=39),
            dca_baseline_return_pct=Decimal("0.05"),
            on_row=on_row,
        )
        assert len(called_rows) == grid.size
        # 콜백 순서 = 결과 순서
        for i, (cb_row, result_row) in enumerate(zip(called_rows, rows, strict=True)):
            assert cb_row is result_row, f"on_row 콜백 rows[{i}] 순서 불일치"

    def test_base_config_universe_조합마다_유지(self):
        """각 조합에서 base_config.universe 가 변경되지 않아야 한다."""
        grid = self._small_grid()
        original_universe = (_SYM_A,)
        base_cfg = _default_base_config(universe=original_universe)
        loader = self._make_loader()

        rows = run_rsi_mr_sensitivity(
            loader=loader,
            base_config=base_cfg,
            grid=grid,
            start=_START,
            end=_START + timedelta(days=39),
            dca_baseline_return_pct=Decimal("0.05"),
        )
        # base_config 가 변경되지 않아야 한다
        assert base_cfg.universe == original_universe
        # 결과 rows 가 반환되어야 한다 (조합마다 동일 universe 사용)
        assert len(rows) == grid.size

    def test_start_after_end_RuntimeError(self):
        """start > end → RuntimeError."""
        grid = self._small_grid()
        base_cfg = _default_base_config(universe=(_SYM_A,))
        loader = self._make_loader()

        with pytest.raises(RuntimeError):
            run_rsi_mr_sensitivity(
                loader=loader,
                base_config=base_cfg,
                grid=grid,
                start=date(2025, 2, 1),  # start > end
                end=date(2025, 1, 1),
                dca_baseline_return_pct=Decimal("0.05"),
            )
