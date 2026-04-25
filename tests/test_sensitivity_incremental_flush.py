"""append_sensitivity_row 단독 검증 — 조합 단위 증분 CSV flush.

검증 대상:
- append_sensitivity_row(row, path, grid):
  * path 부재 → 헤더 + 1행 신규 작성 (atomic: .tmp → os.replace)
  * path 존재 → 기존 내용 read → 1행 추가 → .tmp → os.replace
  * 헤더는 write_csv 와 동일 포맷 (축 이름 + 메트릭 10종)
  * load_completed_combos 와 round-trip 가능
  * tmp 파일 누수 없음
  * default_grid() 32 조합 1개씩 append → 32 키 인식

외부 I/O: tmp_path 실 디스크 쓰기만. 네트워크/KIS 접촉 없음.
"""

from __future__ import annotations

import os
from datetime import timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from stock_agent.backtest import (
    BacktestMetrics,
    ParameterAxis,
    SensitivityGrid,
    SensitivityRow,
    append_sensitivity_row,
    default_grid,
    load_completed_combos,
    write_csv,
)

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))


def _make_metrics(net_pnl_krw: int = 0) -> BacktestMetrics:
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
    params: dict[str, Any],
    net_pnl_krw: int = 0,
    trade_count: int = 0,
) -> SensitivityRow:
    return SensitivityRow(
        params=tuple(params.items()),
        metrics=_make_metrics(net_pnl_krw),
        trade_count=trade_count,
        rejected_total=0,
        post_slippage_rejections=0,
    )


def _small_grid() -> SensitivityGrid:
    """strategy.stop_loss_pct × strategy.take_profit_pct — 2×2 = 4 조합."""
    return SensitivityGrid(
        axes=(
            ParameterAxis(
                name="strategy.stop_loss_pct",
                values=(Decimal("0.010"), Decimal("0.015")),
            ),
            ParameterAxis(
                name="strategy.take_profit_pct",
                values=(Decimal("0.020"), Decimal("0.030")),
            ),
        )
    )


def _all_rows_for_grid(grid: SensitivityGrid) -> tuple[SensitivityRow, ...]:
    """grid 의 모든 조합에 대한 SensitivityRow 튜플 (순서 동일)."""
    rows = []
    for combo in grid.iter_combinations():
        rows.append(_make_row(combo, net_pnl_krw=1000, trade_count=1))
    return tuple(rows)


# ---------------------------------------------------------------------------
# A. TestAppendSensitivityRowNewFile — path 부재 시 신규 작성
# ---------------------------------------------------------------------------


class TestAppendSensitivityRowNewFile:
    """path 가 존재하지 않을 때 헤더 + 1행을 신규 작성한다."""

    def test_path_부재_헤더_플러스_1행_작성(self, tmp_path: Path):
        """빈 디렉터리에서 호출 → CSV 파일 생성, write_csv((row,), path) 결과와 동일 컨텐츠."""
        grid = _small_grid()
        combo = next(iter(grid.iter_combinations()))
        row = _make_row(combo, net_pnl_krw=1000, trade_count=1)

        target = tmp_path / "out.csv"
        assert not target.exists()

        append_sensitivity_row(row, target, grid)

        assert target.exists(), "파일이 생성되지 않음"

        # write_csv 로 동일 단일 행 작성 후 내용 비교
        reference = tmp_path / "ref.csv"
        write_csv((row,), reference)

        actual = target.read_text(encoding="utf-8")
        expected = reference.read_text(encoding="utf-8")
        assert actual == expected, f"내용 불일치\n실제:\n{actual}\n기대:\n{expected}"

    def test_path_부재_파일_실제_존재함(self, tmp_path: Path):
        """append 후 파일이 실제로 생성돼 있어야 한다."""
        grid = _small_grid()
        combo = next(iter(grid.iter_combinations()))
        row = _make_row(combo)

        target = tmp_path / "new.csv"
        append_sensitivity_row(row, target, grid)

        assert target.exists()
        assert target.stat().st_size > 0, "파일 크기가 0"

    def test_path_부재_헤더_포함_2행(self, tmp_path: Path):
        """신규 작성 시 헤더 1행 + 데이터 1행 = 2행이어야 한다."""
        grid = _small_grid()
        combo = next(iter(grid.iter_combinations()))
        row = _make_row(combo)

        target = tmp_path / "two_lines.csv"
        append_sensitivity_row(row, target, grid)

        lines = [line for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 2, f"헤더 + 데이터 행 기대 2, 실제 {len(lines)}"


# ---------------------------------------------------------------------------
# B. TestAppendSensitivityRowExistingFile — path 존재 시 1행 추가
# ---------------------------------------------------------------------------


class TestAppendSensitivityRowExistingFile:
    """path 가 존재할 때 기존 내용을 보존하고 1행을 추가한다."""

    def test_path_존재_기존N행_플러스_1행(self, tmp_path: Path):
        """write_csv((r1, r2), path) 후 append_sensitivity_row(r3, path, grid)
        → 파일에 r1, r2, r3 순서로 3 행 + 헤더."""
        grid = _small_grid()
        combos = list(grid.iter_combinations())
        r1 = _make_row(combos[0], net_pnl_krw=100)
        r2 = _make_row(combos[1], net_pnl_krw=200)
        r3 = _make_row(combos[2], net_pnl_krw=300)

        target = tmp_path / "existing.csv"
        write_csv((r1, r2), target)

        append_sensitivity_row(r3, target, grid)

        # 헤더 1 + 데이터 3 = 4 라인
        lines = [line for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 4, f"헤더 + 3 데이터 행 기대, 실제 {len(lines)}"

    def test_기존_row_보존(self, tmp_path: Path):
        """append 후 기존 행의 내용이 손상되지 않는다."""
        grid = _small_grid()
        combos = list(grid.iter_combinations())
        r1 = _make_row(combos[0], net_pnl_krw=111)
        r2 = _make_row(combos[1], net_pnl_krw=222)

        target = tmp_path / "preserved.csv"
        write_csv((r1,), target)

        append_sensitivity_row(r2, target, grid)

        # load_completed_combos 로 r1 의 조합이 여전히 인식되는지 확인
        completed = load_completed_combos(target, grid)
        r1_key = tuple((ax.name, dict(r1.params)[ax.name]) for ax in grid.axes)
        assert r1_key in completed, f"기존 r1 조합이 사라짐. completed={completed}"

    def test_추가된_row_인식됨(self, tmp_path: Path):
        """append 후 새 행이 load_completed_combos 로 인식된다."""
        grid = _small_grid()
        combos = list(grid.iter_combinations())
        r1 = _make_row(combos[0], net_pnl_krw=111)
        r2 = _make_row(combos[1], net_pnl_krw=222)

        target = tmp_path / "new_row.csv"
        write_csv((r1,), target)

        append_sensitivity_row(r2, target, grid)

        completed = load_completed_combos(target, grid)
        r2_key = tuple((ax.name, dict(r2.params)[ax.name]) for ax in grid.axes)
        assert r2_key in completed, f"새 r2 조합이 인식되지 않음. completed={completed}"


# ---------------------------------------------------------------------------
# C. TestAppendRoundTrip — load_completed_combos 와 round-trip
# ---------------------------------------------------------------------------


class TestAppendRoundTrip:
    """append_sensitivity_row 로 쓴 행이 load_completed_combos 로 정확히 복원된다."""

    def test_load_completed_combos_round_trip(self, tmp_path: Path):
        """append 한 행을 load_completed_combos(path, grid) 로 읽으면
        모든 조합 키가 인식됨 (헤더 일관성 검증)."""
        grid = _small_grid()
        combos = list(grid.iter_combinations())

        target = tmp_path / "round_trip.csv"
        for combo in combos:
            row = _make_row(combo, net_pnl_krw=500)
            append_sensitivity_row(row, target, grid)

        completed = load_completed_combos(target, grid)
        _msg = f"grid.size={grid.size}, 인식된 조합 수={len(completed)}"
        assert len(completed) == grid.size, _msg

    def test_Decimal_값_파싱_정확도(self, tmp_path: Path):
        """append 후 Decimal("0.015") 조합이 set 에 포함된다."""
        grid = _small_grid()
        combo = {
            "strategy.stop_loss_pct": Decimal("0.015"),
            "strategy.take_profit_pct": Decimal("0.020"),
        }
        row = _make_row(combo)

        target = tmp_path / "decimal.csv"
        append_sensitivity_row(row, target, grid)

        completed = load_completed_combos(target, grid)
        target_key = (
            ("strategy.stop_loss_pct", Decimal("0.015")),
            ("strategy.take_profit_pct", Decimal("0.020")),
        )
        assert target_key in completed, f"Decimal 파싱 불일치. completed={completed}"


# ---------------------------------------------------------------------------
# D. TestAppendAtomicOsReplace — atomic 쓰기 (os.replace 경유)
# ---------------------------------------------------------------------------


class TestAppendAtomicOsReplace:
    """append_sensitivity_row 가 os.replace 를 경유해 atomic 쓰기를 수행한다."""

    def test_atomic_via_os_replace(self, tmp_path: Path, monkeypatch):
        """os.replace 를 monkeypatch 로 가로채 호출 횟수·인자 검증.
        tmp 경로가 같은 디렉터리 내이고 → final path 인지 확인.
        호출 후 final 파일에 새 row 가 보임."""
        grid = _small_grid()
        combo = next(iter(grid.iter_combinations()))
        row = _make_row(combo, net_pnl_krw=42)

        target = tmp_path / "atomic.csv"

        replace_calls: list[tuple[str, str]] = []
        original_replace = os.replace

        def _fake_replace(src: str, dst: str) -> None:
            replace_calls.append((src, dst))
            original_replace(src, dst)

        monkeypatch.setattr(os, "replace", _fake_replace)

        append_sensitivity_row(row, target, grid)

        # os.replace 가 정확히 1회 호출
        assert len(replace_calls) == 1, f"os.replace 호출 횟수={len(replace_calls)}, 기대 1"

        src_path, dst_path = replace_calls[0]

        # src(tmp)와 dst(final)가 같은 디렉터리에 있어야 한다
        _dir_msg = f"tmp({src_path}) 와 final({dst_path}) 의 디렉터리가 다름"
        assert Path(src_path).parent == Path(dst_path).parent, _dir_msg

        # dst 가 target 과 동일해야 한다
        assert Path(dst_path) == target, f"dst={dst_path}, 기대={target}"

        # 호출 완료 후 final 파일에 새 row 의 조합이 인식돼야 한다
        assert target.exists()
        completed = load_completed_combos(target, grid)
        row_key = tuple((ax.name, dict(row.params)[ax.name]) for ax in grid.axes)
        assert row_key in completed

    def test_atomic_tmp_파일_누수_없음(self, tmp_path: Path):
        """정상 완료 후 디렉터리에 .tmp 파일이 남아있지 않다."""
        grid = _small_grid()
        combos = list(grid.iter_combinations())

        target = tmp_path / "no_tmp_leak.csv"
        for combo in combos:
            row = _make_row(combo)
            append_sensitivity_row(row, target, grid)

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f".tmp 파일 누수: {tmp_files}"

    def test_os_replace_src_tmp_경로_규칙(self, tmp_path: Path, monkeypatch):
        """os.replace 의 src(tmp) 가 .tmp 확장자를 갖거나 같은 디렉터리의 임시 파일이다."""
        grid = _small_grid()
        combo = next(iter(grid.iter_combinations()))
        row = _make_row(combo)

        target = tmp_path / "rule_check.csv"
        replace_calls: list[tuple[str, str]] = []
        original_replace = os.replace

        def _fake_replace(src: str, dst: str) -> None:
            replace_calls.append((src, dst))
            original_replace(src, dst)

        monkeypatch.setattr(os, "replace", _fake_replace)
        append_sensitivity_row(row, target, grid)

        assert replace_calls, "os.replace 가 호출되지 않음"
        src_path = Path(replace_calls[0][0])

        # tmp 파일은 final 파일과 같은 디렉터리
        _dir_msg = f"tmp 디렉터리({src_path.parent}) != final 디렉터리({target.parent})"
        assert src_path.parent == target.parent, _dir_msg
        # tmp 파일은 final 파일과 다른 이름
        assert src_path != target, "tmp 경로가 final 경로와 동일 (atomic 보장 불가)"


# ---------------------------------------------------------------------------
# E. TestAppendDefault32 — default_grid() 32 조합 전체 append
# ---------------------------------------------------------------------------


class TestAppendDefault32:
    """default_grid() 32 조합을 1개씩 32회 append → load_completed_combos 로 32 키 인식."""

    def test_default_grid_조합_appendx32_동일도달(self, tmp_path: Path):
        """default_grid() 32 조합을 1개씩 32회 append →
        load_completed_combos 로 32 키 인식."""
        grid = default_grid()
        assert grid.size == 32

        target = tmp_path / "default32.csv"
        for combo in grid.iter_combinations():
            row = _make_row(combo, net_pnl_krw=100, trade_count=1)
            append_sensitivity_row(row, target, grid)

        completed = load_completed_combos(target, grid)
        assert len(completed) == 32, f"32 조합 기대, 실제 인식={len(completed)}"

    def test_default_grid_append순서무관_동일결과(self, tmp_path: Path):
        """append 순서와 무관하게 최종 파일에 32 조합이 존재한다."""
        grid = default_grid()
        combos = list(grid.iter_combinations())

        target = tmp_path / "order_check.csv"
        # 역순으로 append
        for combo in reversed(combos):
            row = _make_row(combo, net_pnl_krw=50)
            append_sensitivity_row(row, target, grid)

        completed = load_completed_combos(target, grid)
        assert len(completed) == 32
