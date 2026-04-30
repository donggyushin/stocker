"""sensitivity.py 공개 계약 단위 테스트.

ParameterAxis / SensitivityGrid / SensitivityRow / run_sensitivity /
render_markdown_table / write_csv / default_grid 를 검증한다.
외부 네트워크 · KIS · 시계 의존 없음 — 합성 분봉 InMemoryBarLoader 만 사용.
"""

from __future__ import annotations

import csv as csv_mod
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from stock_agent.backtest import (
    BacktestConfig,
    BacktestMetrics,
    InMemoryBarLoader,
    ParameterAxis,
    SensitivityGrid,
    SensitivityRow,
    default_grid,
    render_markdown_table,
    run_sensitivity,
    write_csv,
)
from stock_agent.data import MinuteBar

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_DATE1 = date(2026, 4, 20)
_DATE2 = date(2026, 4, 21)
_DATE3 = date(2026, 4, 22)

_SYM_A = "005930"
_SYM_B = "000660"


def _bar(
    symbol: str,
    h: int,
    m: int,
    open_: int | str | Decimal,
    high: int | str | Decimal,
    low: int | str | Decimal,
    close: int | str | Decimal,
    *,
    date_: date = _DATE1,
    volume: int = 0,
) -> MinuteBar:
    """MinuteBar 생성 헬퍼. h/m 은 KST 시·분."""
    return MinuteBar(
        symbol=symbol,
        bar_time=datetime(date_.year, date_.month, date_.day, h, m, tzinfo=KST),
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=volume,
    )


def _make_base_config(capital: int = 1_000_000) -> BacktestConfig:
    """기본 BacktestConfig 헬퍼."""
    return BacktestConfig(starting_capital_krw=capital)


def _make_metrics(net_pnl_krw: int = 0) -> BacktestMetrics:
    """테스트용 BacktestMetrics 헬퍼."""
    total_return = Decimal(net_pnl_krw) / Decimal(1_000_000)
    return BacktestMetrics(
        total_return_pct=total_return,
        max_drawdown_pct=Decimal("0"),
        sharpe_ratio=Decimal("0"),
        win_rate=Decimal("1") if net_pnl_krw > 0 else Decimal("0"),
        avg_pnl_ratio=Decimal("0"),
        trades_per_day=Decimal("0"),
        net_pnl_krw=net_pnl_krw,
    )


def _make_row(
    params: dict,
    net_pnl_krw: int = 0,
    trade_count: int = 0,
) -> SensitivityRow:
    """테스트용 SensitivityRow 헬퍼.

    params: {축이름: 값} dict → tuple[tuple[str, Any], ...] 로 변환.
    trade_count: 호출자가 명시 전달 (비즈니스 로직 혼입 회피).
    """
    return SensitivityRow(
        params=tuple(params.items()),
        metrics=_make_metrics(net_pnl_krw),
        trade_count=trade_count,
        rejected_total=0,
        post_slippage_rejections=0,
    )


def _익절_시나리오_bars(symbol: str, date_: date) -> list[MinuteBar]:
    """단일 심볼 1일치 — OR + 진입(09:30) + 익절(09:32).

    or_high=70500, close=71000 → 진입, high=73130 → 익절(+3.0% 기본값).
    """
    return [
        _bar(symbol, 9, 0, 70000, 70500, 69800, 70000, date_=date_),
        _bar(symbol, 9, 30, 70200, 71500, 70100, 71000, date_=date_),
        _bar(symbol, 9, 31, 71000, 72000, 70900, 71100, date_=date_),
        _bar(symbol, 9, 32, 71100, 73130, 71000, 71200, date_=date_),
    ]


# ---------------------------------------------------------------------------
# A. ParameterAxis 검증
# ---------------------------------------------------------------------------


class TestParameterAxis:
    def test_정상_생성_strategy_prefix(self):
        """strategy.stop_loss_pct — 정상 생성."""
        axis = ParameterAxis(
            name="strategy.stop_loss_pct",
            values=(Decimal("0.010"), Decimal("0.015")),
        )
        assert axis.name == "strategy.stop_loss_pct"
        assert len(axis.values) == 2

    def test_정상_생성_risk_prefix(self):
        """risk.max_positions — 정상 생성."""
        axis = ParameterAxis(name="risk.max_positions", values=(2, 3))
        assert axis.name == "risk.max_positions"

    def test_정상_생성_engine_prefix(self):
        """engine.slippage_rate — 정상 생성."""
        axis = ParameterAxis(
            name="engine.slippage_rate",
            values=(Decimal("0.001"), Decimal("0.002")),
        )
        assert axis.name == "engine.slippage_rate"

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "strategy",
            "no_dot_at_all",
        ],
        ids=["빈_문자열", "점_없음_strategy", "점_없음_general"],
    )
    def test_점_없는_name_RuntimeError(self, name: str):
        """'.' 없는 name → RuntimeError."""
        with pytest.raises(RuntimeError):
            ParameterAxis(name=name, values=(1,))

    def test_잘못된_prefix_RuntimeError(self):
        """알 수 없는 prefix → RuntimeError."""
        with pytest.raises(RuntimeError, match="prefix"):
            ParameterAxis(name="unknown.field", values=(1,))

    @pytest.mark.parametrize(
        "name",
        ["execution.field", "broker.field", "data.field"],
        ids=["execution", "broker", "data"],
    )
    def test_허용되지않는_prefix_RuntimeError(self, name: str):
        """strategy/risk/engine 이외 prefix → RuntimeError."""
        with pytest.raises(RuntimeError):
            ParameterAxis(name=name, values=(1,))

    def test_field_공란_RuntimeError(self):
        """prefix.field 에서 field 가 빈 문자열 → RuntimeError."""
        with pytest.raises(RuntimeError, match="field"):
            ParameterAxis(name="strategy.", values=(1,))

    def test_빈_values_RuntimeError(self):
        """values=() → RuntimeError."""
        with pytest.raises(RuntimeError, match="values"):
            ParameterAxis(name="strategy.stop_loss_pct", values=())

    def test_중복_values_RuntimeError(self):
        """values 에 동일 값 중복 → RuntimeError."""
        with pytest.raises(RuntimeError, match="중복"):
            ParameterAxis(
                name="strategy.stop_loss_pct",
                values=(Decimal("0.015"), Decimal("0.015")),
            )

    def test_중복_values_정수_RuntimeError(self):
        """정수 중복도 감지된다."""
        with pytest.raises(RuntimeError, match="중복"):
            ParameterAxis(name="risk.max_positions", values=(3, 3))

    def test_단일_value_허용(self):
        """values 길이 1 은 허용된다."""
        axis = ParameterAxis(name="strategy.stop_loss_pct", values=(Decimal("0.015"),))
        assert len(axis.values) == 1

    def test_frozen_변경_불가(self):
        """frozen dataclass — 필드 변경 시도 AttributeError."""
        axis = ParameterAxis(name="strategy.stop_loss_pct", values=(Decimal("0.015"),))
        with pytest.raises(AttributeError):
            axis.name = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# B. SensitivityGrid 조합
# ---------------------------------------------------------------------------


class TestSensitivityGrid:
    def _axis_a(self) -> ParameterAxis:
        return ParameterAxis(
            name="strategy.stop_loss_pct",
            values=(Decimal("0.010"), Decimal("0.015")),
        )

    def _axis_b(self) -> ParameterAxis:
        return ParameterAxis(
            name="strategy.take_profit_pct",
            values=(Decimal("0.020"), Decimal("0.030"), Decimal("0.040")),
        )

    def test_size_cartesian_product(self):
        """2 × 3 = 6 조합."""
        grid = SensitivityGrid(axes=(self._axis_a(), self._axis_b()))
        assert grid.size == 6

    def test_size_단일_축(self):
        """단일 축 — size == len(values)."""
        grid = SensitivityGrid(axes=(self._axis_a(),))
        assert grid.size == 2

    def test_iter_combinations_개수_정확(self):
        """2 × 3 그리드 → 조합 6개."""
        grid = SensitivityGrid(axes=(self._axis_a(), self._axis_b()))
        combos = list(grid.iter_combinations())
        assert len(combos) == 6

    def test_iter_combinations_모든_키_포함(self):
        """각 조합 dict 에 두 축 이름이 모두 포함된다."""
        grid = SensitivityGrid(axes=(self._axis_a(), self._axis_b()))
        for combo in grid.iter_combinations():
            assert "strategy.stop_loss_pct" in combo
            assert "strategy.take_profit_pct" in combo

    def test_iter_combinations_순서_결정론(self):
        """동일 입력 → 동일 순서 (결정론)."""
        grid = SensitivityGrid(axes=(self._axis_a(), self._axis_b()))
        run1 = list(grid.iter_combinations())
        run2 = list(grid.iter_combinations())
        assert run1 == run2

    def test_iter_combinations_마지막_축_가장_빠름(self):
        """마지막 축이 가장 빠르게 회전한다 (axis_b 가 먼저 변화)."""
        grid = SensitivityGrid(axes=(self._axis_a(), self._axis_b()))
        combos = list(grid.iter_combinations())
        # 첫 len(axis_b.values)=3 개는 axis_a 첫 번째 값 고정
        first_stop = combos[0]["strategy.stop_loss_pct"]
        for combo in combos[:3]:
            assert combo["strategy.stop_loss_pct"] == first_stop

    def test_iter_combinations_전체_커버리지(self):
        """모든 값 조합이 정확히 1회씩 등장한다."""
        grid = SensitivityGrid(axes=(self._axis_a(), self._axis_b()))
        combos = list(grid.iter_combinations())
        seen = set()
        for combo in combos:
            key = (combo["strategy.stop_loss_pct"], combo["strategy.take_profit_pct"])
            assert key not in seen, f"중복 조합 발견: {key}"
            seen.add(key)
        assert len(seen) == 6

    def test_빈_axes_RuntimeError(self):
        """axes=() → __post_init__ 에서 RuntimeError (빈 그리드 조기 실패)."""
        with pytest.raises(RuntimeError, match="axes"):
            SensitivityGrid(axes=())

    def test_중복_축_이름_RuntimeError(self):
        """동일 name 의 축 2개 → RuntimeError."""
        axis = self._axis_a()
        with pytest.raises(RuntimeError, match="중복"):
            SensitivityGrid(axes=(axis, axis))

    def test_3축_size(self):
        """2 × 4 × 4 = 32 — default_grid 와 동일 구조."""
        axis_or = ParameterAxis(
            name="strategy.or_end",
            values=(time(9, 15), time(9, 30)),
        )
        axis_stop = ParameterAxis(
            name="strategy.stop_loss_pct",
            values=(
                Decimal("0.010"),
                Decimal("0.015"),
                Decimal("0.020"),
                Decimal("0.025"),
            ),
        )
        axis_take = ParameterAxis(
            name="strategy.take_profit_pct",
            values=(
                Decimal("0.020"),
                Decimal("0.030"),
                Decimal("0.040"),
                Decimal("0.050"),
            ),
        )
        grid = SensitivityGrid(axes=(axis_or, axis_stop, axis_take))
        assert grid.size == 32


# ---------------------------------------------------------------------------
# C. SensitivityRow 구조 계약
# ---------------------------------------------------------------------------


class TestSensitivityRow:
    """SensitivityRow 신규 구조 계약 — params:tuple, metrics:BacktestMetrics."""

    def test_params_타입이_tuple이다(self):
        """SensitivityRow.params 는 tuple[tuple[str, Any], ...] 타입이다."""
        row = _make_row({"strategy.stop_loss_pct": Decimal("0.015")}, net_pnl_krw=1000)
        assert isinstance(row.params, tuple)

    def test_metrics_타입이_BacktestMetrics이다(self):
        """SensitivityRow.metrics 는 BacktestMetrics 인스턴스이다."""
        row = _make_row({"strategy.stop_loss_pct": Decimal("0.015")}, net_pnl_krw=1000)
        assert isinstance(row.metrics, BacktestMetrics)

    def test_trade_count_타입이_int이다(self):
        """SensitivityRow.trade_count 는 int 이다."""
        row = _make_row({}, net_pnl_krw=0, trade_count=3)
        assert isinstance(row.trade_count, int)
        assert row.trade_count == 3

    def test_rejected_total_타입이_int이다(self):
        """SensitivityRow.rejected_total 는 int 이다."""
        row = _make_row({})
        assert isinstance(row.rejected_total, int)

    def test_post_slippage_rejections_타입이_int이다(self):
        """SensitivityRow.post_slippage_rejections 는 int 이다."""
        row = _make_row({})
        assert isinstance(row.post_slippage_rejections, int)

    def test_metrics_필드_접근(self):
        """row.metrics.net_pnl_krw 로 메트릭에 접근한다."""
        row = _make_row({"strategy.stop_loss_pct": Decimal("0.015")}, net_pnl_krw=5000)
        assert row.metrics.net_pnl_krw == 5000
        assert isinstance(row.metrics.total_return_pct, Decimal)
        assert isinstance(row.metrics.max_drawdown_pct, Decimal)
        assert isinstance(row.metrics.sharpe_ratio, Decimal)
        assert isinstance(row.metrics.win_rate, Decimal)
        assert isinstance(row.metrics.avg_pnl_ratio, Decimal)
        assert isinstance(row.metrics.trades_per_day, Decimal)

    def test_params_dict_복사본_동작(self):
        """params_dict() 는 dict 복사본을 반환하고, 원본 params 튜플은 불변이다."""
        row = _make_row({"strategy.stop_loss_pct": Decimal("0.015")}, net_pnl_krw=0)
        d = row.params_dict()
        assert isinstance(d, dict)
        assert d["strategy.stop_loss_pct"] == Decimal("0.015")
        # 반환된 dict 를 변경해도 원본 row.params 에 영향이 없다
        d["strategy.stop_loss_pct"] = Decimal("0.999")
        assert row.params_dict()["strategy.stop_loss_pct"] == Decimal("0.015")

    def test_중복_축이름_RuntimeError(self):
        """params 에 중복된 축 이름이 있으면 RuntimeError 를 발생시킨다."""
        with pytest.raises(RuntimeError, match="중복"):
            SensitivityRow(
                params=(("x", 1), ("x", 2)),
                metrics=_make_metrics(0),
                trade_count=0,
                rejected_total=0,
                post_slippage_rejections=0,
            )

    def test_중복_축이름_세_개_RuntimeError(self):
        """세 항목 중 하나만 중복이어도 RuntimeError 발생."""
        with pytest.raises(RuntimeError, match="중복"):
            SensitivityRow(
                params=(("a", 1), ("b", 2), ("a", 3)),
                metrics=_make_metrics(0),
                trade_count=0,
                rejected_total=0,
                post_slippage_rejections=0,
            )

    def test_frozen_불변성(self):
        """frozen dataclass — 필드 직접 변경 시도 AttributeError."""
        row = _make_row({"strategy.stop_loss_pct": Decimal("0.015")})
        with pytest.raises(AttributeError):
            row.trade_count = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# D. run_sensitivity 회귀 / prefix 라우팅
# ---------------------------------------------------------------------------


class TestRunSensitivity:
    """합성 분봉 2 심볼 × 3일 + 2×2 그리드 = 4 조합."""

    def _loader(self) -> InMemoryBarLoader:
        """2 심볼 × 3일치 합성 분봉 — OR(09:00) + 진입(09:30) + 익절(09:32)."""
        bars: list[MinuteBar] = []
        for d in [_DATE1, _DATE2, _DATE3]:
            bars.extend(_익절_시나리오_bars(_SYM_A, d))
            bars.extend(_익절_시나리오_bars(_SYM_B, d))
        return InMemoryBarLoader(bars)

    def _2x2_grid(self) -> SensitivityGrid:
        """strategy.stop_loss_pct × strategy.take_profit_pct 2×2 = 4 조합."""
        return SensitivityGrid(
            axes=(
                ParameterAxis(
                    name="strategy.stop_loss_pct",
                    values=(Decimal("0.010"), Decimal("0.020")),
                ),
                ParameterAxis(
                    name="strategy.take_profit_pct",
                    values=(Decimal("0.030"), Decimal("0.050")),
                ),
            )
        )

    def test_결과_개수_그리드_size_일치(self):
        """run_sensitivity 결과 tuple 길이 == grid.size."""
        loader = self._loader()
        grid = self._2x2_grid()
        rows = run_sensitivity(
            loader,
            _DATE1,
            _DATE3,
            (_SYM_A, _SYM_B),
            _make_base_config(),
            grid,
        )
        assert len(rows) == 4

    def test_결과_타입_SensitivityRow(self):
        """결과는 SensitivityRow 인스턴스 튜플."""
        loader = self._loader()
        grid = self._2x2_grid()
        rows = run_sensitivity(loader, _DATE1, _DATE3, (_SYM_A,), _make_base_config(), grid)
        for row in rows:
            assert isinstance(row, SensitivityRow)

    def test_결과_params_키_축_이름_일치(self):
        """각 row.params_dict() 키가 축 이름과 일치한다."""
        loader = self._loader()
        grid = self._2x2_grid()
        rows = run_sensitivity(loader, _DATE1, _DATE3, (_SYM_A,), _make_base_config(), grid)
        for row in rows:
            assert set(row.params_dict().keys()) == {
                "strategy.stop_loss_pct",
                "strategy.take_profit_pct",
            }

    def test_익절_시나리오_trade_count_양수(self):
        """익절 분봉이 있는 시나리오 → trade_count > 0."""
        loader = self._loader()
        grid = SensitivityGrid(
            axes=(
                ParameterAxis(
                    name="strategy.stop_loss_pct",
                    values=(Decimal("0.015"),),
                ),
            )
        )
        rows = run_sensitivity(loader, _DATE1, _DATE3, (_SYM_A,), _make_base_config(), grid)
        assert rows[0].trade_count > 0

    def test_결정론_동일_입력_동일_출력(self):
        """동일 loader · grid 로 두 번 실행 → 동일 결과."""
        grid = self._2x2_grid()

        bars: list[MinuteBar] = []
        for d in [_DATE1, _DATE2]:
            bars.extend(_익절_시나리오_bars(_SYM_A, d))
        loader = InMemoryBarLoader(bars)

        rows1 = run_sensitivity(loader, _DATE1, _DATE2, (_SYM_A,), _make_base_config(), grid)
        rows2 = run_sensitivity(loader, _DATE1, _DATE2, (_SYM_A,), _make_base_config(), grid)

        assert len(rows1) == len(rows2)
        for r1, r2 in zip(rows1, rows2, strict=True):
            assert r1.params == r2.params
            assert r1.metrics.net_pnl_krw == r2.metrics.net_pnl_krw
            assert r1.trade_count == r2.trade_count

    def test_strategy_prefix_적용(self):
        """strategy.take_profit_pct 변경 → 적용된 파라미터가 row.params_dict() 에 반영."""
        bars = _익절_시나리오_bars(_SYM_A, _DATE1)
        loader = InMemoryBarLoader(bars)
        grid = SensitivityGrid(
            axes=(
                ParameterAxis(
                    name="strategy.take_profit_pct",
                    values=(Decimal("0.030"), Decimal("0.050")),
                ),
            )
        )
        rows = run_sensitivity(loader, _DATE1, _DATE1, (_SYM_A,), _make_base_config(), grid)
        assert rows[0].params_dict()["strategy.take_profit_pct"] == Decimal("0.030")
        assert rows[1].params_dict()["strategy.take_profit_pct"] == Decimal("0.050")

    def test_risk_prefix_적용(self):
        """risk.max_positions 변경 → params_dict() 에 기록된다."""
        bars = _익절_시나리오_bars(_SYM_A, _DATE1)
        loader = InMemoryBarLoader(bars)
        grid = SensitivityGrid(
            axes=(
                ParameterAxis(
                    name="risk.max_positions",
                    values=(1, 3),
                ),
            )
        )
        rows = run_sensitivity(loader, _DATE1, _DATE1, (_SYM_A,), _make_base_config(), grid)
        assert rows[0].params_dict()["risk.max_positions"] == 1
        assert rows[1].params_dict()["risk.max_positions"] == 3

    def test_engine_prefix_slippage_rate_적용(self):
        """engine.slippage_rate 변경 → params_dict() 에 기록된다."""
        bars = _익절_시나리오_bars(_SYM_A, _DATE1)
        loader = InMemoryBarLoader(bars)
        grid = SensitivityGrid(
            axes=(
                ParameterAxis(
                    name="engine.slippage_rate",
                    values=(Decimal("0.001"), Decimal("0.002")),
                ),
            )
        )
        rows = run_sensitivity(loader, _DATE1, _DATE1, (_SYM_A,), _make_base_config(), grid)
        assert rows[0].params_dict()["engine.slippage_rate"] == Decimal("0.001")
        assert rows[1].params_dict()["engine.slippage_rate"] == Decimal("0.002")

    def test_engine_starting_capital_krw_RuntimeError(self):
        """engine.starting_capital_krw 는 그리드 대상 아님 → RuntimeError."""
        bars = _익절_시나리오_bars(_SYM_A, _DATE1)
        loader = InMemoryBarLoader(bars)
        grid = SensitivityGrid(
            axes=(
                ParameterAxis(
                    name="engine.starting_capital_krw",
                    values=(500_000, 1_000_000),
                ),
            )
        )
        with pytest.raises(RuntimeError):
            run_sensitivity(loader, _DATE1, _DATE1, (_SYM_A,), _make_base_config(), grid)

    def test_알수없는_strategy_필드_RuntimeError(self):
        """strategy.nonexistent_field → RuntimeError."""
        bars = _익절_시나리오_bars(_SYM_A, _DATE1)
        loader = InMemoryBarLoader(bars)
        grid = SensitivityGrid(
            axes=(
                ParameterAxis(
                    name="strategy.nonexistent_field",
                    values=(1, 2),
                ),
            )
        )
        with pytest.raises(RuntimeError):
            run_sensitivity(loader, _DATE1, _DATE1, (_SYM_A,), _make_base_config(), grid)

    def test_알수없는_risk_필드_RuntimeError(self):
        """risk.nonexistent_field → RuntimeError."""
        bars = _익절_시나리오_bars(_SYM_A, _DATE1)
        loader = InMemoryBarLoader(bars)
        grid = SensitivityGrid(
            axes=(
                ParameterAxis(
                    name="risk.nonexistent_field",
                    values=(1, 2),
                ),
            )
        )
        with pytest.raises(RuntimeError):
            run_sensitivity(loader, _DATE1, _DATE1, (_SYM_A,), _make_base_config(), grid)

    def test_rejected_total_집계(self):
        """RiskManager 거부 카운트 합산이 rejected_total 에 반영된다.

        max_positions=1 로 제한하고 2 심볼 동시 돌파 → 1건 거부 발생.
        """
        bars = _익절_시나리오_bars(_SYM_A, _DATE1) + _익절_시나리오_bars(_SYM_B, _DATE1)
        loader = InMemoryBarLoader(bars)
        grid = SensitivityGrid(
            axes=(
                ParameterAxis(
                    name="risk.max_positions",
                    values=(1,),
                ),
            )
        )
        rows = run_sensitivity(
            loader,
            _DATE1,
            _DATE1,
            (_SYM_A, _SYM_B),
            BacktestConfig(starting_capital_krw=4_000_000),
            grid,
        )
        assert rows[0].rejected_total >= 1, "max_positions=1 → 최소 1건 거부 기대"

    def test_SensitivityRow_필드_타입_정합성(self):
        """SensitivityRow 필드 타입이 계약과 일치한다 (신규 구조)."""
        bars = _익절_시나리오_bars(_SYM_A, _DATE1)
        loader = InMemoryBarLoader(bars)
        grid = SensitivityGrid(
            axes=(
                ParameterAxis(
                    name="strategy.stop_loss_pct",
                    values=(Decimal("0.015"),),
                ),
            )
        )
        rows = run_sensitivity(loader, _DATE1, _DATE1, (_SYM_A,), _make_base_config(), grid)
        r = rows[0]
        # params 는 tuple (dict 아님)
        assert isinstance(r.params, tuple)
        # metrics 는 BacktestMetrics
        assert isinstance(r.metrics, BacktestMetrics)
        # metrics 내 필드 타입
        assert isinstance(r.metrics.total_return_pct, Decimal)
        assert isinstance(r.metrics.max_drawdown_pct, Decimal)
        assert isinstance(r.metrics.sharpe_ratio, Decimal)
        assert isinstance(r.metrics.win_rate, Decimal)
        assert isinstance(r.metrics.avg_pnl_ratio, Decimal)
        assert isinstance(r.metrics.trades_per_day, Decimal)
        assert isinstance(r.metrics.net_pnl_krw, int)
        # 보조 필드
        assert isinstance(r.trade_count, int)
        assert isinstance(r.rejected_total, int)
        assert isinstance(r.post_slippage_rejections, int)

    # -------------------------------------------------------------------
    # I4. post_slippage_rejections 집계 경로 회귀
    # -------------------------------------------------------------------

    def test_post_slippage_rejections_집계_end_to_end(self):
        """사후 슬리피지 거부가 SensitivityRow.post_slippage_rejections 에 전파된다.

        재현 조건:
        - starting_capital=1_050 원 (극소 자본)
        - 참고가 1_000 원대 OR 돌파 시나리오
        - RiskManager 는 qty=1 승인 (position_pct=1.0, min_notional=1_000)
        - 슬리피지 0.1% 반영 후 notional+commission > cash 가 되어 사후 거부
        """
        from stock_agent.risk import RiskConfig

        # OR 분봉(09:00) high=1010 → or_high=1010
        # 09:30 close=1015 → or_high(1010) 돌파 → 진입 시도
        # entry_fill = 1015 * 1.001 = 1016.015 → int = 1016
        # notional = 1 * 1016 = 1016, commission = int(1016 * 0.00015) = 0
        # 1016 + 0 = 1016 > 1_050 ? → 1016 <= 1050 → 통과할 수 있으므로
        # 더 확실하게: starting_capital=1_010, position_pct=1.0 → target=1010
        # qty = int(1010 / 1015) = 0 → below_min_notional 으로 RiskManager 거부될 수 있음
        # → min_notional=0 으로 낮추고 position_pct=1.0, capital=1_010
        # qty = int(Decimal(1010)/Decimal("1015")) = int(0.99...) = 0
        # qty=0 이면 filled_notional=0 → below_min_notional(min=0 이면 통과)
        # qty=0 이면 진입 자체가 의미 없으므로 다른 접근 필요
        #
        # 올바른 재현:
        # capital=1_100, position_pct=Decimal("1.00"), min_notional=0
        # 참고가=1_000 → qty = int(1100 / 1000) = 1
        # entry_fill = 1000 * 1.001 = 1001 → notional = 1001
        # commission(buy) = int(1001 * 0.00015) = 0
        # 1001 + 0 = 1001 <= 1100 → 통과 (사후 거부 안 됨)
        #
        # 사후 거부를 유발하려면 notional+commission > capital 이어야 함
        # slippage=0.1% → fill = 1000*1.001=1001, commission=int(1001*0.015%)=0
        # capital=1_001 이면 1001+0 = 1001 <= 1001 → 통과
        # capital=1_000 이면 1001+0 = 1001 > 1000 → 사후 거부!
        #
        # capital=1_000, qty=1 이 RiskManager 를 통과하려면:
        # filled_notional = qty * ref_price = 1 * 1000 = 1000 >= min_notional
        # filled_notional(1000) <= available_cash(1000) → insufficient_cash 로 거부됨!
        # → RiskManager 의 insufficient_cash(6번) 가 먼저 거부
        #
        # RiskManager insufficient_cash 를 피하고 엔진 사후 거부만 유발하는 조건:
        # evaluate_entry 의 available_cash 판정은 ref_price 기준(슬리피지 전),
        # 엔진의 사후 거부는 fill_price 기준(슬리피지 후).
        # ref_price=1000, capital=1000 → filled_notional=1000 <= 1000 → RiskManager 통과
        # fill_price = 1000*1.001 = 1001 → notional=1001 > 1000 → 사후 거부!
        # 수치 계산 (or_end=09:30 기본값 기준, OR 구간=09:00):
        # OR high = 1010 (09:00 bar)
        # 09:30 close=1011 > or_high(1010) → 진입 시도, signal.price=1011
        # capital=1_011, position_pct=1.0
        # target_notional = int(1011 * 1.0) = 1011
        # qty = int(Decimal(1011) / Decimal("1011")) = 1
        # filled_notional = 1 * 1011 = 1011
        # min_notional(1) 통과
        # insufficient_cash: filled_notional(1011) <= available_cash(1011) → 통과
        # ∴ RiskManager 승인
        # 엔진 사후 검사:
        # entry_fill = buy_fill_price(1011, 0.001) = 1011 * 1.001 = 1012.011
        # notional_int = int(1 * 1012.011) = 1012
        # commission = int(1012 * 0.00015) = 0
        # 1012 + 0 = 1012 > cash(1011) → 사후 거부!
        bars = [
            _bar(_SYM_A, 9, 0, 1000, 1010, 990, 1000, date_=_DATE1),
            _bar(_SYM_A, 9, 30, 1005, 1015, 1000, 1011, date_=_DATE1),
            _bar(_SYM_A, 9, 31, 1011, 1050, 1005, 1030, date_=_DATE1),
        ]
        loader = InMemoryBarLoader(bars)
        risk_cfg = RiskConfig(
            position_pct=Decimal("1.00"),
            max_positions=3,
            min_notional_krw=1,  # 양수 최솟값 — filled_notional(1011) >= 1 통과
            daily_max_entries=10,
            daily_loss_limit_pct=Decimal("0.02"),
        )
        grid = SensitivityGrid(
            axes=(
                ParameterAxis(
                    name="strategy.stop_loss_pct",
                    values=(Decimal("0.015"),),
                ),
            )
        )
        base_cfg = BacktestConfig(
            starting_capital_krw=1_011,  # capital=signal.price → RiskManager 통과, 사후 거부
            slippage_rate=Decimal("0.001"),
            commission_rate=Decimal("0.00015"),
            sell_tax_rate=Decimal("0.0018"),
            risk_config=risk_cfg,
        )
        rows = run_sensitivity(loader, _DATE1, _DATE1, (_SYM_A,), base_cfg, grid)
        assert len(rows) == 1
        assert rows[0].post_slippage_rejections >= 1

    # -------------------------------------------------------------------
    # I5-a. engine.commission_rate prefix 라우팅 회귀
    # -------------------------------------------------------------------

    def test_engine_commission_rate_메트릭_차이(self):
        """engine.commission_rate 높을수록 net_pnl_krw 가 낮아진다.

        동일 분봉, commission_rate=0.0001 vs 0.001 두 후보.
        매도가 일어나는 익절 시나리오 → 수수료 차이 → net_pnl 차이.
        """
        bars = _익절_시나리오_bars(_SYM_A, _DATE1)
        loader = InMemoryBarLoader(bars)
        grid = SensitivityGrid(
            axes=(
                ParameterAxis(
                    name="engine.commission_rate",
                    values=(Decimal("0.0001"), Decimal("0.001")),
                ),
            )
        )
        rows = run_sensitivity(
            loader,
            _DATE1,
            _DATE1,
            (_SYM_A,),
            BacktestConfig(starting_capital_krw=1_000_000),
            grid,
        )
        assert len(rows) == 2
        net_low_commission = rows[0].metrics.net_pnl_krw
        net_high_commission = rows[1].metrics.net_pnl_krw
        # 낮은 수수료(0.0001) 쪽이 높은 수수료(0.001) 보다 net_pnl 이 크거나 같아야 함
        assert net_low_commission >= net_high_commission
        # 체결이 일어난 경우엔 반드시 차이가 있어야 함
        if rows[0].trade_count > 0:
            assert net_low_commission > net_high_commission

    # -------------------------------------------------------------------
    # I5-b. engine.sell_tax_rate prefix 라우팅 회귀
    # -------------------------------------------------------------------

    def test_engine_sell_tax_rate_메트릭_차이(self):
        """engine.sell_tax_rate 높을수록 net_pnl_krw 가 낮아진다.

        sell_tax_rate=0 vs 0.005 두 후보.
        매도가 일어나는 익절 시나리오 → 거래세 차이 → net_pnl 차이.
        """
        bars = _익절_시나리오_bars(_SYM_A, _DATE1)
        loader = InMemoryBarLoader(bars)
        grid = SensitivityGrid(
            axes=(
                ParameterAxis(
                    name="engine.sell_tax_rate",
                    values=(Decimal("0"), Decimal("0.005")),
                ),
            )
        )
        rows = run_sensitivity(
            loader,
            _DATE1,
            _DATE1,
            (_SYM_A,),
            BacktestConfig(starting_capital_krw=1_000_000),
            grid,
        )
        assert len(rows) == 2
        net_no_tax = rows[0].metrics.net_pnl_krw
        net_high_tax = rows[1].metrics.net_pnl_krw
        # 거래세 0 쪽이 거래세 0.5% 보다 net_pnl 이 크거나 같아야 함
        assert net_no_tax >= net_high_tax
        # 체결이 일어난 경우엔 반드시 차이가 있어야 함
        if rows[0].trade_count > 0:
            assert net_no_tax > net_high_tax


# ---------------------------------------------------------------------------
# E. render_markdown_table
# ---------------------------------------------------------------------------


class TestRenderMarkdownTable:
    def _two_rows(self) -> tuple[SensitivityRow, ...]:
        """비교 가능한 rows 2개 — params 키 동일."""
        p1 = {"strategy.stop_loss_pct": Decimal("0.010")}
        p2 = {"strategy.stop_loss_pct": Decimal("0.020")}
        return (
            _make_row(p1, net_pnl_krw=3_000, trade_count=1),
            _make_row(p2, net_pnl_krw=1_000, trade_count=1),
        )

    def test_잘못된_sort_by_RuntimeError(self):
        """허용되지 않는 sort_by → RuntimeError."""
        rows = self._two_rows()
        with pytest.raises(RuntimeError, match="sort_by"):
            render_markdown_table(rows, sort_by="invalid_key")

    def test_빈_rows_placeholder_반환(self):
        """rows=() → 빈 결과 placeholder 문자열 반환 (오류 아님)."""
        result = render_markdown_table(())
        assert isinstance(result, str)
        assert len(result) > 0  # 빈 문자열 아님

    def test_헤더_포함(self):
        """Markdown 표에 헤더 행이 있어야 한다 (| 구분자)."""
        rows = self._two_rows()
        result = render_markdown_table(rows)
        lines = result.strip().splitlines()
        assert lines[0].startswith("|")
        assert "strategy.stop_loss_pct" in lines[0]

    def test_구분자_행_포함(self):
        """헤더 다음 행은 --- 구분자 행이어야 한다."""
        rows = self._two_rows()
        result = render_markdown_table(rows)
        lines = result.strip().splitlines()
        assert "---" in lines[1]

    def test_데이터_행_개수_일치(self):
        """rows 개수만큼 데이터 행이 있어야 한다 (헤더·구분자 제외)."""
        rows = self._two_rows()
        result = render_markdown_table(rows)
        lines = [ln for ln in result.strip().splitlines() if ln.startswith("|")]
        # 헤더 1 + 구분자 1 + 데이터 2 = 4
        assert len(lines) == 4

    def test_descending_정렬(self):
        """descending=True → net_pnl_krw 큰 row 가 먼저 나온다."""
        rows = self._two_rows()
        result = render_markdown_table(rows, sort_by="net_pnl_krw", descending=True)
        lines = result.strip().splitlines()
        data_lines = lines[2:]
        # 첫 데이터 행에 3000 이 있어야 한다
        assert "3000" in data_lines[0]

    def test_ascending_정렬(self):
        """descending=False → net_pnl_krw 작은 row 가 먼저 나온다."""
        rows = self._two_rows()
        result = render_markdown_table(rows, sort_by="net_pnl_krw", descending=False)
        lines = result.strip().splitlines()
        data_lines = lines[2:]
        # 첫 데이터 행에 1000 이 있어야 한다
        assert "1000" in data_lines[0]

    def test_params_키_불일치_RuntimeError(self):
        """rows 간 params 키 집합이 다르면 → RuntimeError."""
        r1 = _make_row({"strategy.stop_loss_pct": Decimal("0.010")}, net_pnl_krw=100)
        r2 = _make_row({"strategy.take_profit_pct": Decimal("0.030")}, net_pnl_krw=200)
        with pytest.raises(RuntimeError):
            render_markdown_table((r1, r2))

    @pytest.mark.parametrize(
        "sort_by",
        [
            "total_return_pct",
            "max_drawdown_pct",
            "sharpe_ratio",
            "win_rate",
            "avg_pnl_ratio",
            "trades_per_day",
            "net_pnl_krw",
            "trade_count",
            "rejected_total",
            "post_slippage_rejections",
        ],
    )
    def test_허용된_sort_by_정상_반환(self, sort_by: str):
        """_SORTABLE_METRIC_KEYS 의 10 종류 키는 모두 정상 처리된다."""
        rows = self._two_rows()
        result = render_markdown_table(rows, sort_by=sort_by)
        assert isinstance(result, str)
        assert "|" in result

    def test_메트릭_컬럼_전체_헤더_포함(self):
        """헤더에 10종 메트릭 컬럼명이 모두 포함된다."""
        rows = self._two_rows()
        result = render_markdown_table(rows)
        header = result.splitlines()[0]
        for col in [
            "total_return_pct",
            "max_drawdown_pct",
            "sharpe_ratio",
            "win_rate",
            "avg_pnl_ratio",
            "trades_per_day",
            "net_pnl_krw",
            "trade_count",
            "rejected_total",
            "post_slippage_rejections",
        ]:
            assert col in header, f"컬럼 '{col}' 이 헤더에 없음"

    def test_결과_문자열_newline_종결(self):
        """렌더 결과 마지막 문자는 newline."""
        rows = self._two_rows()
        result = render_markdown_table(rows)
        assert result.endswith("\n")


# ---------------------------------------------------------------------------
# F. write_csv
# ---------------------------------------------------------------------------


class TestWriteCsv:
    def _two_rows(self) -> tuple[SensitivityRow, ...]:
        p1 = {"strategy.stop_loss_pct": Decimal("0.010")}
        p2 = {"strategy.stop_loss_pct": Decimal("0.020")}
        return (
            _make_row(p1, net_pnl_krw=3_000, trade_count=1),
            _make_row(p2, net_pnl_krw=-500, trade_count=1),
        )

    def test_파일_생성(self, tmp_path: Path):
        """write_csv 호출 후 파일이 생성된다."""
        path = tmp_path / "out.csv"
        write_csv(self._two_rows(), path)
        assert path.exists()

    def test_헤더_행_포함(self, tmp_path: Path):
        """첫 행이 헤더 (params 키 + 메트릭 컬럼)."""
        path = tmp_path / "out.csv"
        write_csv(self._two_rows(), path)
        with path.open(encoding="utf-8") as f:
            reader = csv_mod.reader(f)
            header = next(reader)
        assert "strategy.stop_loss_pct" in header
        assert "net_pnl_krw" in header

    def test_데이터_행_개수(self, tmp_path: Path):
        """헤더 포함 총 행 수 == 1 + len(rows)."""
        path = tmp_path / "out.csv"
        rows = self._two_rows()
        write_csv(rows, path)
        with path.open(encoding="utf-8") as f:
            all_lines = list(csv_mod.reader(f))
        # 헤더 1행 + 데이터 2행
        assert len(all_lines) == 3

    def test_빈_rows_헤더만(self, tmp_path: Path):
        """rows=() → 헤더만 쓴다 (데이터 행 0)."""
        path = tmp_path / "out.csv"
        write_csv((), path)
        with path.open(encoding="utf-8") as f:
            all_lines = list(csv_mod.reader(f))
        # 헤더 1행만 (빈 CSV 도 유효)
        assert len(all_lines) == 1

    def test_빈_rows_헤더_메트릭_컬럼_포함(self, tmp_path: Path):
        """빈 rows 일 때 헤더는 메트릭 컬럼 10종을 포함한다."""
        path = tmp_path / "out.csv"
        write_csv((), path)
        with path.open(encoding="utf-8") as f:
            header = next(csv_mod.reader(f))
        for col in ["total_return_pct", "net_pnl_krw", "trade_count"]:
            assert col in header

    def test_net_pnl_값_저장(self, tmp_path: Path):
        """net_pnl_krw 값이 CSV 에 정확히 기록된다."""
        path = tmp_path / "out.csv"
        write_csv(self._two_rows(), path)
        with path.open(encoding="utf-8") as f:
            rows_read = list(csv_mod.DictReader(f))
        values = {int(r["net_pnl_krw"]) for r in rows_read}
        assert 3000 in values
        assert -500 in values

    def test_params_키_불일치_RuntimeError(self, tmp_path: Path):
        """rows 간 params 키 집합이 다르면 → RuntimeError."""
        r1 = _make_row({"strategy.stop_loss_pct": Decimal("0.010")}, net_pnl_krw=100)
        r2 = _make_row({"strategy.take_profit_pct": Decimal("0.030")}, net_pnl_krw=200)
        path = tmp_path / "out.csv"
        with pytest.raises(RuntimeError):
            write_csv((r1, r2), path)

    def test_utf8_인코딩(self, tmp_path: Path):
        """CSV 는 UTF-8 으로 인코딩된다."""
        path = tmp_path / "out.csv"
        write_csv(self._two_rows(), path)
        # UTF-8 디코딩이 예외 없이 성공해야 함
        content = path.read_bytes().decode("utf-8")
        assert "net_pnl_krw" in content


# ---------------------------------------------------------------------------
# G. default_grid
# ---------------------------------------------------------------------------


class TestDefaultGrid:
    def test_size_32(self):
        """default_grid() → 2 × 4 × 4 = 32 조합."""
        grid = default_grid()
        assert grid.size == 32

    def test_축_개수_3(self):
        """축이 3개여야 한다."""
        grid = default_grid()
        assert len(grid.axes) == 3

    def test_축_이름_확인(self):
        """3개 축 이름이 기대한 파라미터다."""
        grid = default_grid()
        names = {axis.name for axis in grid.axes}
        assert "strategy.or_end" in names
        assert "strategy.stop_loss_pct" in names
        assert "strategy.take_profit_pct" in names

    def test_현재_기본값_조합_포함_or_end(self):
        """현재 운영 기본값 or_end=09:30 이 그리드에 포함된다."""
        grid = default_grid()
        combos = list(grid.iter_combinations())
        or_end_values = {c["strategy.or_end"] for c in combos}
        assert time(9, 30) in or_end_values

    def test_현재_기본값_조합_포함_stop(self):
        """현재 운영 기본값 stop=0.015 이 그리드에 포함된다."""
        grid = default_grid()
        combos = list(grid.iter_combinations())
        stop_values = {c["strategy.stop_loss_pct"] for c in combos}
        assert Decimal("0.015") in stop_values

    def test_현재_기본값_조합_포함_take(self):
        """현재 운영 기본값 take=0.030 이 그리드에 포함된다."""
        grid = default_grid()
        combos = list(grid.iter_combinations())
        take_values = {c["strategy.take_profit_pct"] for c in combos}
        assert Decimal("0.030") in take_values

    def test_현재_기본값_조합_정확히_한번_등장(self):
        """or_end=09:30 / stop=0.015 / take=0.030 조합이 정확히 1회 등장한다."""
        grid = default_grid()
        combos = list(grid.iter_combinations())
        target = {
            "strategy.or_end": time(9, 30),
            "strategy.stop_loss_pct": Decimal("0.015"),
            "strategy.take_profit_pct": Decimal("0.030"),
        }
        count = sum(1 for c in combos if c == target)
        assert count == 1, f"기본값 조합이 {count}회 등장 (기대: 1)"

    def test_iter_combinations_결정론(self):
        """동일 default_grid() 두 번 순회 → 동일 순서."""
        grid = default_grid()
        run1 = list(grid.iter_combinations())
        run2 = list(grid.iter_combinations())
        assert run1 == run2

    def test_stop_loss_값_범위(self):
        """stop_loss_pct 후보값 4종: 0.010, 0.015, 0.020, 0.025."""
        grid = default_grid()
        stop_axis = next(a for a in grid.axes if a.name == "strategy.stop_loss_pct")
        expected = {
            Decimal("0.010"),
            Decimal("0.015"),
            Decimal("0.020"),
            Decimal("0.025"),
        }
        assert set(stop_axis.values) == expected

    def test_take_profit_값_범위(self):
        """take_profit_pct 후보값 4종: 0.020, 0.030, 0.040, 0.050."""
        grid = default_grid()
        take_axis = next(a for a in grid.axes if a.name == "strategy.take_profit_pct")
        expected = {
            Decimal("0.020"),
            Decimal("0.030"),
            Decimal("0.040"),
            Decimal("0.050"),
        }
        assert set(take_axis.values) == expected

    def test_or_end_값_범위(self):
        """or_end 후보값 2종: 09:15, 09:30."""
        grid = default_grid()
        or_axis = next(a for a in grid.axes if a.name == "strategy.or_end")
        expected = {time(9, 15), time(9, 30)}
        assert set(or_axis.values) == expected


# ---------------------------------------------------------------------------
# H. step_d1_grid — Step D1 OR 윈도 길이 스터디 전용 그리드
# ---------------------------------------------------------------------------


class TestStepD1Grid:
    """step_d1_grid() 공개 계약 — 3×4×4 = 48 조합.

    현재 RED: step_d1_grid 가 stock_agent.backtest 에 미구현.
    각 테스트 내부에서 import 해 ImportError 를 AssertionError 형태로 FAIL 유도.
    """

    @staticmethod
    def _get_func():
        """step_d1_grid 를 지연 import — ImportError 를 테스트 실패로 전환."""
        from stock_agent.backtest import step_d1_grid  # noqa: PLC0415

        return step_d1_grid

    def test_반환타입_SensitivityGrid(self):
        """step_d1_grid() 반환값은 SensitivityGrid 타입이다."""
        step_d1_grid = self._get_func()
        grid = step_d1_grid()
        assert isinstance(grid, SensitivityGrid)

    def test_size_48(self):
        """3 × 4 × 4 = 48 조합."""
        step_d1_grid = self._get_func()
        grid = step_d1_grid()
        assert grid.size == 48

    def test_축_이름_순서(self):
        """축 이름 순서 = (strategy.or_end, strategy.stop_loss_pct, strategy.take_profit_pct)."""
        step_d1_grid = self._get_func()
        grid = step_d1_grid()
        names = tuple(a.name for a in grid.axes)
        assert names == (
            "strategy.or_end",
            "strategy.stop_loss_pct",
            "strategy.take_profit_pct",
        )

    def test_or_end_후보값_순서(self):
        """or_end 축 후보값 = (time(9,15), time(9,30), time(10,0)) — 정확히 이 순서."""
        step_d1_grid = self._get_func()
        grid = step_d1_grid()
        or_axis = next(a for a in grid.axes if a.name == "strategy.or_end")
        assert or_axis.values == (time(9, 15), time(9, 30), time(10, 0))

    def test_stop_loss_pct_후보값_default_grid_일치(self):
        """stop_loss_pct 후보값 집합이 default_grid() 의 동일 축과 정확히 일치한다."""
        step_d1_grid = self._get_func()
        d1_grid = step_d1_grid()
        ref_grid = default_grid()
        d1_stop = next(a for a in d1_grid.axes if a.name == "strategy.stop_loss_pct")
        ref_stop = next(a for a in ref_grid.axes if a.name == "strategy.stop_loss_pct")
        assert d1_stop.values == ref_stop.values

    def test_take_profit_pct_후보값_default_grid_일치(self):
        """take_profit_pct 후보값 집합이 default_grid() 의 동일 축과 정확히 일치한다."""
        step_d1_grid = self._get_func()
        d1_grid = step_d1_grid()
        ref_grid = default_grid()
        d1_take = next(a for a in d1_grid.axes if a.name == "strategy.take_profit_pct")
        ref_take = next(a for a in ref_grid.axes if a.name == "strategy.take_profit_pct")
        assert d1_take.values == ref_take.values

    def test_default_grid_size_32_회귀(self):
        """step_d1_grid() 추가가 default_grid() 동작을 변경하지 않는다."""
        assert default_grid().size == 32

    def test_iter_combinations_첫_조합(self):
        """iter_combinations() 첫 조합 = 축·후보 선언 순서 0번 값들의 조합."""
        step_d1_grid = self._get_func()
        grid = step_d1_grid()
        first = next(iter(grid.iter_combinations()))
        assert first == {
            "strategy.or_end": time(9, 15),
            "strategy.stop_loss_pct": Decimal("0.010"),
            "strategy.take_profit_pct": Decimal("0.020"),
        }
