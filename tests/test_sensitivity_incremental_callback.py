"""run_sensitivity_combos / run_sensitivity_combos_parallel 의 on_row 콜백 계약 검증 (RED).

검증 대상:
- run_sensitivity_combos(..., on_row=callable): keyword-only 콜백 추가 (미구현)
  * 조합 수만큼 호출, 각 인자가 SensitivityRow 인스턴스
  * 반환 tuple 의 row 들과 동일 (set 비교)
  * on_row=None (기본) 이면 기존 동작 동일
- run_sensitivity_combos_parallel(..., on_row=callable): 동일 계약
  * as_completed 시점에 메인 프로세스에서 호출
  * 호출 순서는 비결정적 — 호출 횟수와 row 집합만 검증
  * on_row=None 기본값 회귀

외부 I/O: 없음 (InMemoryBarLoader 사용). 네트워크/KIS 접촉 없음.
multiprocessing: mp_context=fork (darwin 전용).
"""

from __future__ import annotations

import multiprocessing
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from stock_agent.backtest import (
    BacktestConfig,
    InMemoryBarLoader,
    ParameterAxis,
    SensitivityGrid,
    SensitivityRow,
    run_sensitivity_combos,
    run_sensitivity_combos_parallel,
)
from stock_agent.data import MinuteBar

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_DATE1 = date(2026, 4, 20)
_DATE2 = date(2026, 4, 21)

_SYM_A = "005930"
_SYM_B = "000660"

# fork context — darwin 전용
_FORK_CTX = multiprocessing.get_context("fork")


# ---------------------------------------------------------------------------
# 모듈 top-level 헬퍼 (closure 금지 — fork 워커 pickle 안전)
# ---------------------------------------------------------------------------


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
    return MinuteBar(
        symbol=symbol,
        bar_time=datetime(date_.year, date_.month, date_.day, h, m, tzinfo=KST),
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=volume,
    )


def _익절_시나리오_bars(symbol: str, date_: date) -> list[MinuteBar]:
    """단일 심볼 1일치 — OR(09:00) + 진입(09:30) + 익절(09:32)."""
    return [
        _bar(symbol, 9, 0, 70000, 70500, 69800, 70000, date_=date_),
        _bar(symbol, 9, 30, 70200, 71500, 70100, 71000, date_=date_),
        _bar(symbol, 9, 31, 71000, 72000, 70900, 71100, date_=date_),
        _bar(symbol, 9, 32, 71100, 73130, 71000, 71200, date_=date_),
    ]


def _build_bars_1sym_2day() -> list[MinuteBar]:
    bars: list[MinuteBar] = []
    for d in [_DATE1, _DATE2]:
        bars.extend(_익절_시나리오_bars(_SYM_A, d))
    return bars


def _build_bars_2sym_2day() -> list[MinuteBar]:
    bars: list[MinuteBar] = []
    for d in [_DATE1, _DATE2]:
        bars.extend(_익절_시나리오_bars(_SYM_A, d))
        bars.extend(_익절_시나리오_bars(_SYM_B, d))
    return bars


def _inmemory_factory_1sym() -> InMemoryBarLoader:
    return InMemoryBarLoader(_build_bars_1sym_2day())


def _inmemory_factory_2sym() -> InMemoryBarLoader:
    return InMemoryBarLoader(_build_bars_2sym_2day())


def _make_base_config(capital: int = 1_000_000) -> BacktestConfig:
    return BacktestConfig(starting_capital_krw=capital)


def _make_n_combo_grid(n: int) -> SensitivityGrid:
    """n 개 조합을 가진 그리드 생성 — stop_loss_pct 단일 축 n 값."""
    values = tuple(Decimal("0.010") + Decimal("0.005") * i for i in range(n))
    return SensitivityGrid(
        axes=(
            ParameterAxis(
                name="strategy.stop_loss_pct",
                values=values,
            ),
        )
    )


def _make_2x2_grid() -> SensitivityGrid:
    """strategy.stop_loss_pct × strategy.take_profit_pct — 2×2 = 4 조합."""
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


# ---------------------------------------------------------------------------
# A. TestRunSensitivityCombosOnRow — 직렬 경로 on_row 계약
# ---------------------------------------------------------------------------


class TestRunSensitivityCombosOnRow:
    """run_sensitivity_combos(..., on_row=...) keyword-only 콜백 계약."""

    def test_run_sensitivity_combos_on_row_호출_횟수(self):
        """5 조합 입력 → on_row 가 정확히 5회 호출, 각 인자가 SensitivityRow."""
        grid = _make_n_combo_grid(5)
        combos = list(grid.iter_combinations())
        loader = InMemoryBarLoader(_build_bars_1sym_2day())
        base_config = _make_base_config()

        called_with: list[Any] = []

        def _on_row(row: SensitivityRow) -> None:
            called_with.append(row)

        result = run_sensitivity_combos(
            loader=loader,
            start=_DATE1,
            end=_DATE2,
            symbols=(_SYM_A,),
            base_config=base_config,
            combos=combos,
            on_row=_on_row,
        )

        assert len(called_with) == 5, f"on_row 호출 횟수={len(called_with)}, 기대 5"
        for row in called_with:
            _row_msg = f"on_row 인자가 SensitivityRow 가 아님: {type(row)}"
            assert isinstance(row, SensitivityRow), _row_msg

        # 반환 tuple 의 row 집합과 콜백 row 집합이 동일 (params 기준)
        result_params = {r.params for r in result}
        callback_params = {r.params for r in called_with}
        _set_msg = (
            f"반환값 set과 콜백 set 불일치\nresult={result_params}\ncallback={callback_params}"
        )
        assert result_params == callback_params, _set_msg

    def test_run_sensitivity_combos_on_row_None_기본_회귀(self):
        """on_row 미지정 → 기존 동작 동일 (반환값만 검증, 예외 없음)."""
        grid = _make_n_combo_grid(3)
        combos = list(grid.iter_combinations())
        loader = InMemoryBarLoader(_build_bars_1sym_2day())
        base_config = _make_base_config()

        # on_row 인자 없이 호출 — TypeError 없이 동작해야 함
        result = run_sensitivity_combos(
            loader=loader,
            start=_DATE1,
            end=_DATE2,
            symbols=(_SYM_A,),
            base_config=base_config,
            combos=combos,
        )

        assert len(result) == 3, f"결과 개수={len(result)}, 기대 3"
        assert all(isinstance(r, SensitivityRow) for r in result)

    def test_run_sensitivity_combos_on_row_명시적_None_회귀(self):
        """on_row=None 명시 → 기존 동작과 동일, 예외 없음."""
        grid = _make_n_combo_grid(2)
        combos = list(grid.iter_combinations())
        loader = InMemoryBarLoader(_build_bars_1sym_2day())
        base_config = _make_base_config()

        result = run_sensitivity_combos(
            loader=loader,
            start=_DATE1,
            end=_DATE2,
            symbols=(_SYM_A,),
            base_config=base_config,
            combos=combos,
            on_row=None,
        )

        assert len(result) == 2

    def test_run_sensitivity_combos_on_row_인자_순서_결정론(self):
        """on_row 가 조합 순서대로 호출된다 (직렬 경로는 결정론적)."""
        grid = _make_n_combo_grid(4)
        combos = list(grid.iter_combinations())
        loader = InMemoryBarLoader(_build_bars_1sym_2day())
        base_config = _make_base_config()

        called_params: list[tuple] = []

        def _on_row(row: SensitivityRow) -> None:
            called_params.append(row.params)

        result = run_sensitivity_combos(
            loader=loader,
            start=_DATE1,
            end=_DATE2,
            symbols=(_SYM_A,),
            base_config=base_config,
            combos=combos,
            on_row=_on_row,
        )

        # 직렬 경로는 combos 순서와 on_row 호출 순서가 일치해야 한다
        for i, (r, p) in enumerate(zip(result, called_params, strict=True)):
            assert r.params == p, f"index={i}: 반환값 params와 콜백 params 불일치"

    def test_run_sensitivity_combos_빈_combos_on_row_미호출(self):
        """combos=[] 이면 on_row 가 호출되지 않고 빈 튜플 반환."""
        loader = InMemoryBarLoader([])
        base_config = _make_base_config()

        called: list[SensitivityRow] = []

        result = run_sensitivity_combos(
            loader=loader,
            start=_DATE1,
            end=_DATE2,
            symbols=(_SYM_A,),
            base_config=base_config,
            combos=[],
            on_row=lambda r: called.append(r),
        )

        assert result == ()
        assert called == [], "빈 combos 에서 on_row 가 호출되면 안 됨"


# ---------------------------------------------------------------------------
# B. TestRunSensitivityCombosParallelOnRow — 병렬 경로 on_row 계약
# ---------------------------------------------------------------------------


class TestRunSensitivityCombosParallelOnRow:
    """run_sensitivity_combos_parallel(..., on_row=...) — 메인 프로세스에서 호출."""

    def test_run_sensitivity_combos_parallel_on_row_호출_횟수(self):
        """4 조합 + workers=2 (fork) → on_row 가 메인 프로세스에서 4회 호출.
        as_completed 순서이므로 호출 순서는 비결정적 — 호출 횟수와 row 집합만 검증."""
        grid = _make_2x2_grid()
        combos = list(grid.iter_combinations())
        base_config = _make_base_config()

        called_with: list[SensitivityRow] = []

        def _on_row(row: SensitivityRow) -> None:
            called_with.append(row)

        result = run_sensitivity_combos_parallel(
            loader_factory=_inmemory_factory_1sym,
            start=_DATE1,
            end=_DATE2,
            symbols=(_SYM_A,),
            base_config=base_config,
            combos=combos,
            max_workers=2,
            mp_context=_FORK_CTX,
            on_row=_on_row,
        )

        assert len(called_with) == 4, f"on_row 호출 횟수={len(called_with)}, 기대 4"
        for row in called_with:
            _row_msg = f"on_row 인자가 SensitivityRow 가 아님: {type(row)}"
            assert isinstance(row, SensitivityRow), _row_msg

        # 반환 tuple 과 콜백 row 집합이 동일 (params 기준)
        result_params = {r.params for r in result}
        callback_params = {r.params for r in called_with}
        assert result_params == callback_params

    def test_run_sensitivity_combos_parallel_on_row_None_회귀(self):
        """on_row 미지정 → 기존 동작, 예외 없음, 결과 개수 = 조합 수."""
        grid = _make_2x2_grid()
        combos = list(grid.iter_combinations())
        base_config = _make_base_config()

        result = run_sensitivity_combos_parallel(
            loader_factory=_inmemory_factory_1sym,
            start=_DATE1,
            end=_DATE2,
            symbols=(_SYM_A,),
            base_config=base_config,
            combos=combos,
            max_workers=2,
            mp_context=_FORK_CTX,
        )

        assert len(result) == 4
        assert all(isinstance(r, SensitivityRow) for r in result)

    def test_run_sensitivity_combos_parallel_on_row_명시적_None_회귀(self):
        """on_row=None 명시 → 기존 동작과 동일."""
        grid = _make_2x2_grid()
        combos = list(grid.iter_combinations())
        base_config = _make_base_config()

        result = run_sensitivity_combos_parallel(
            loader_factory=_inmemory_factory_1sym,
            start=_DATE1,
            end=_DATE2,
            symbols=(_SYM_A,),
            base_config=base_config,
            combos=combos,
            max_workers=2,
            mp_context=_FORK_CTX,
            on_row=None,
        )

        assert len(result) == 4

    def test_run_sensitivity_combos_parallel_on_row_메인_프로세스_호출(self):
        """on_row 가 메인 프로세스(pid) 에서 호출됨을 확인한다."""
        import os

        grid = _make_2x2_grid()
        combos = list(grid.iter_combinations())
        base_config = _make_base_config()

        main_pid = os.getpid()
        callback_pids: list[int] = []

        def _on_row(row: SensitivityRow) -> None:
            callback_pids.append(os.getpid())

        run_sensitivity_combos_parallel(
            loader_factory=_inmemory_factory_1sym,
            start=_DATE1,
            end=_DATE2,
            symbols=(_SYM_A,),
            base_config=base_config,
            combos=combos,
            max_workers=2,
            mp_context=_FORK_CTX,
            on_row=_on_row,
        )

        assert len(callback_pids) == 4
        for pid in callback_pids:
            _pid_msg = f"on_row 가 워커 프로세스(pid={pid})에서 호출됨 — 메인(pid={main_pid}) 기대"
            assert pid == main_pid, _pid_msg

    def test_run_sensitivity_combos_parallel_빈_combos_on_row_미호출(self):
        """combos=[] → ProcessPool 생성 안 함, on_row 미호출, 빈 튜플 반환."""
        base_config = _make_base_config()

        called: list[SensitivityRow] = []

        result = run_sensitivity_combos_parallel(
            loader_factory=_inmemory_factory_1sym,
            start=_DATE1,
            end=_DATE2,
            symbols=(_SYM_A,),
            base_config=base_config,
            combos=[],
            max_workers=2,
            mp_context=_FORK_CTX,
            on_row=lambda r: called.append(r),
        )

        assert result == ()
        assert called == [], "빈 combos 에서 on_row 가 호출되면 안 됨"
