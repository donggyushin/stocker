"""sensitivity.py 공개 계약 단위 테스트.

ParameterAxis / SensitivityGrid / SensitivityRow / run_sensitivity /
render_markdown_table / write_csv / default_grid 를 검증한다.
외부 네트워크 · KIS · 시계 의존 없음 — 합성 분봉 InMemoryBarLoader 만 사용.
"""

from __future__ import annotations

import csv as csv_mod
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from stock_agent.backtest import (
    BacktestConfig,
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


def _make_row(params: dict, net_pnl_krw: int = 0) -> SensitivityRow:
    """테스트용 SensitivityRow 헬퍼."""
    return SensitivityRow(
        params=params,
        total_return_pct=Decimal(net_pnl_krw) / Decimal(1_000_000),
        max_drawdown_pct=Decimal("0"),
        sharpe_ratio=Decimal("0"),
        win_rate=Decimal("1") if net_pnl_krw > 0 else Decimal("0"),
        avg_pnl_ratio=Decimal("0"),
        trades_per_day=Decimal("0"),
        net_pnl_krw=net_pnl_krw,
        trade_count=1 if net_pnl_krw != 0 else 0,
        rejected_total=0,
        post_slippage_rejections=0,
    )


def _취_익절_시나리오_bars(symbol: str, date_: date) -> list[MinuteBar]:
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

    def test_size_빈_axes(self):
        """axes=() → size == 0."""
        grid = SensitivityGrid(axes=())
        assert grid.size == 0

    def test_iter_combinations_빈_axes_yield_없음(self):
        """빈 axes → iter_combinations 에서 yield 없음."""
        grid = SensitivityGrid(axes=())
        combos = list(grid.iter_combinations())
        assert combos == []

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

    def test_중복_축_이름_RuntimeError(self):
        """동일 name 의 축 2개 → RuntimeError."""
        axis = self._axis_a()
        with pytest.raises(RuntimeError, match="중복"):
            SensitivityGrid(axes=(axis, axis))

    def test_3축_size(self):
        """2 × 4 × 4 = 32 — default_grid 와 동일 구조."""
        axis_or = ParameterAxis(
            name="strategy.or_end",
            values=(__import__("datetime").time(9, 15), __import__("datetime").time(9, 30)),
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
# C. run_sensitivity 회귀 / prefix 라우팅
# ---------------------------------------------------------------------------


class TestRunSensitivity:
    """합성 분봉 2 심볼 × 3일 + 2×2 그리드 = 4 조합."""

    def _loader(self) -> InMemoryBarLoader:
        """2 심볼 × 3일치 합성 분봉 — OR(09:00) + 진입(09:30) + 익절(09:32)."""
        bars: list[MinuteBar] = []
        for d in [_DATE1, _DATE2, _DATE3]:
            bars.extend(_취_익절_시나리오_bars(_SYM_A, d))
            bars.extend(_취_익절_시나리오_bars(_SYM_B, d))
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
        """각 row.params 키가 축 이름과 일치한다."""
        loader = self._loader()
        grid = self._2x2_grid()
        rows = run_sensitivity(loader, _DATE1, _DATE3, (_SYM_A,), _make_base_config(), grid)
        for row in rows:
            assert set(row.params.keys()) == {
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
            bars.extend(_취_익절_시나리오_bars(_SYM_A, d))
        loader = InMemoryBarLoader(bars)

        rows1 = run_sensitivity(loader, _DATE1, _DATE2, (_SYM_A,), _make_base_config(), grid)
        rows2 = run_sensitivity(loader, _DATE1, _DATE2, (_SYM_A,), _make_base_config(), grid)

        assert len(rows1) == len(rows2)
        for r1, r2 in zip(rows1, rows2, strict=True):
            assert r1.params == r2.params
            assert r1.net_pnl_krw == r2.net_pnl_krw
            assert r1.trade_count == r2.trade_count

    def test_그리드_size_0_빈_튜플_반환(self):
        """빈 grid (axes=()) → 빈 tuple 반환."""
        loader = InMemoryBarLoader([])
        grid = SensitivityGrid(axes=())
        rows = run_sensitivity(loader, _DATE1, _DATE1, (_SYM_A,), _make_base_config(), grid)
        assert rows == ()

    def test_strategy_prefix_적용(self):
        """strategy.take_profit_pct 변경 → 적용된 파라미터가 row.params 에 반영."""
        bars = _취_익절_시나리오_bars(_SYM_A, _DATE1)
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
        assert rows[0].params["strategy.take_profit_pct"] == Decimal("0.030")
        assert rows[1].params["strategy.take_profit_pct"] == Decimal("0.050")

    def test_risk_prefix_적용(self):
        """risk.max_positions 변경 → params 에 기록된다."""
        bars = _취_익절_시나리오_bars(_SYM_A, _DATE1)
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
        assert rows[0].params["risk.max_positions"] == 1
        assert rows[1].params["risk.max_positions"] == 3

    def test_engine_prefix_slippage_rate_적용(self):
        """engine.slippage_rate 변경 → params 에 기록된다."""
        bars = _취_익절_시나리오_bars(_SYM_A, _DATE1)
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
        assert rows[0].params["engine.slippage_rate"] == Decimal("0.001")
        assert rows[1].params["engine.slippage_rate"] == Decimal("0.002")

    def test_engine_starting_capital_krw_RuntimeError(self):
        """engine.starting_capital_krw 는 그리드 대상 아님 → RuntimeError."""
        bars = _취_익절_시나리오_bars(_SYM_A, _DATE1)
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
        bars = _취_익절_시나리오_bars(_SYM_A, _DATE1)
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
        bars = _취_익절_시나리오_bars(_SYM_A, _DATE1)
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
        bars = _취_익절_시나리오_bars(_SYM_A, _DATE1) + _취_익절_시나리오_bars(_SYM_B, _DATE1)
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
        """SensitivityRow 필드 타입이 계약과 일치한다."""
        bars = _취_익절_시나리오_bars(_SYM_A, _DATE1)
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
        assert isinstance(r.params, dict)
        assert isinstance(r.total_return_pct, Decimal)
        assert isinstance(r.max_drawdown_pct, Decimal)
        assert isinstance(r.sharpe_ratio, Decimal)
        assert isinstance(r.win_rate, Decimal)
        assert isinstance(r.avg_pnl_ratio, Decimal)
        assert isinstance(r.trades_per_day, Decimal)
        assert isinstance(r.net_pnl_krw, int)
        assert isinstance(r.trade_count, int)
        assert isinstance(r.rejected_total, int)
        assert isinstance(r.post_slippage_rejections, int)


# ---------------------------------------------------------------------------
# D. render_markdown_table
# ---------------------------------------------------------------------------


class TestRenderMarkdownTable:
    def _two_rows(self) -> tuple[SensitivityRow, ...]:
        """비교 가능한 rows 2개 — params 키 동일."""
        p1 = {"strategy.stop_loss_pct": Decimal("0.010")}
        p2 = {"strategy.stop_loss_pct": Decimal("0.020")}
        return (
            _make_row(p1, net_pnl_krw=3_000),
            _make_row(p2, net_pnl_krw=1_000),
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
# E. write_csv
# ---------------------------------------------------------------------------


class TestWriteCsv:
    def _two_rows(self) -> tuple[SensitivityRow, ...]:
        p1 = {"strategy.stop_loss_pct": Decimal("0.010")}
        p2 = {"strategy.stop_loss_pct": Decimal("0.020")}
        return (
            _make_row(p1, net_pnl_krw=3_000),
            _make_row(p2, net_pnl_krw=-500),
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
# F. default_grid
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
        import datetime as dt

        grid = default_grid()
        combos = list(grid.iter_combinations())
        or_end_values = {c["strategy.or_end"] for c in combos}
        assert dt.time(9, 30) in or_end_values

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
        import datetime as dt

        grid = default_grid()
        combos = list(grid.iter_combinations())
        target = {
            "strategy.or_end": dt.time(9, 30),
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
        import datetime as dt

        grid = default_grid()
        or_axis = next(a for a in grid.axes if a.name == "strategy.or_end")
        expected = {dt.time(9, 15), dt.time(9, 30)}
        assert set(or_axis.values) == expected
