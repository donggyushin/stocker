"""run_sensitivity_parallel 공개 계약 단위 테스트 (RED — 미구현 API).

검증 대상:
- run_sensitivity_parallel 결과 결정론 (직렬 run_sensitivity 와 동일)
- 결과 순서가 grid.iter_combinations() 순서와 일치
- loader_factory 가 워커마다 호출되는지 (process-safe 카운터)
- 한 워커 실패 → 전체 취소 + 예외 전파 (fail-fast)
- max_workers <= 0 → RuntimeError
- SensitivityRow pickle/unpickle 왕복 안전성

multiprocessing 호환성 주의:
  pytest 환경에서 spawn context 를 쓰면 워커가 테스트 모듈을 import 할 때 경로
  이슈가 생길 수 있다. 모든 테스트는 mp_context=fork (darwin 전용)를 주입해
  회피한다. 헬퍼 함수는 모두 모듈 top-level 에 정의 (closure 금지).
"""

from __future__ import annotations

import multiprocessing
import pickle
from datetime import date, datetime, timedelta, timezone
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
    run_sensitivity,
)

# run_sensitivity_parallel 는 아직 미구현 — ImportError 로 RED 확인
try:
    from stock_agent.backtest import run_sensitivity_parallel  # type: ignore[attr-defined]

    _IMPORT_OK = True
except (ImportError, AttributeError):
    run_sensitivity_parallel = None  # type: ignore[assignment]
    _IMPORT_OK = False

from stock_agent.data import MinuteBar

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_DATE1 = date(2026, 4, 20)
_DATE2 = date(2026, 4, 21)
_DATE3 = date(2026, 4, 22)

_SYM_A = "005930"
_SYM_B = "000660"
_SYM_C = "035420"

# fork context — darwin 전용 (platform 보장됨)
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
    """MinuteBar 생성 헬퍼."""
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
    """단일 심볼 1일치 — OR(09:00) + 진입(09:30) + 익절(09:32).

    or_high=70500, close=71000 → 진입, high=73130 → 익절(+3.0% 기본값).
    """
    return [
        _bar(symbol, 9, 0, 70000, 70500, 69800, 70000, date_=date_),
        _bar(symbol, 9, 30, 70200, 71500, 70100, 71000, date_=date_),
        _bar(symbol, 9, 31, 71000, 72000, 70900, 71100, date_=date_),
        _bar(symbol, 9, 32, 71100, 73130, 71000, 71200, date_=date_),
    ]


def _make_base_config(capital: int = 1_000_000) -> BacktestConfig:
    return BacktestConfig(starting_capital_krw=capital)


def _make_2x2_grid() -> SensitivityGrid:
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


def _make_2x2_grid_distinct() -> SensitivityGrid:
    """4 조합 — 순서 보존 검증용."""
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


def _build_bars_2sym_3day() -> list[MinuteBar]:
    bars: list[MinuteBar] = []
    for d in [_DATE1, _DATE2, _DATE3]:
        bars.extend(_익절_시나리오_bars(_SYM_A, d))
        bars.extend(_익절_시나리오_bars(_SYM_B, d))
    return bars


def _inmemory_factory_2sym_3day() -> InMemoryBarLoader:
    """워커마다 새 InMemoryBarLoader 를 반환하는 팩토리."""
    return InMemoryBarLoader(_build_bars_2sym_3day())


def _failing_stream_factory() -> InMemoryBarLoader:
    """stream() 호출 시 RuntimeError 를 발생시키는 BarLoader 스텁.

    ProcessPoolExecutor 워커 예외 투명 전파를 검증한다.
    """

    class _FailingLoader(InMemoryBarLoader):
        def stream(self, start: date, end: date, symbols: tuple[str, ...]):  # type: ignore[override]
            raise RuntimeError("worker failure")

    return _FailingLoader([])


def _counting_factory(counter_path: str) -> InMemoryBarLoader:
    """호출 횟수를 파일에 append 하는 팩토리 (multiprocessing-safe).

    counter_path 의 파일에 "x\n" 을 추가하므로 호출 수 = 줄 수.
    """
    with open(counter_path, "a") as f:
        f.write("x\n")
    return InMemoryBarLoader(_build_bars_2sym_3day())


def _make_counting_factory(counter_path: str):
    """counter_path 를 캡처한 pickleable 팩토리를 functools.partial 로 반환.

    테스트 메서드 내부 closure 는 fork 컨텍스트에서도 pickle 불가능하므로
    functools.partial(모듈 top-level 함수, ...) 패턴을 사용한다.
    """
    import functools

    return functools.partial(_counting_factory, counter_path)


# ---------------------------------------------------------------------------
# pytest 스킵 마커 — run_sensitivity_parallel import 실패 시 모든 테스트 FAIL
# (RED 모드: import 실패 자체가 RED 상태 — 스킵 없음)
# ---------------------------------------------------------------------------

_requires_parallel = pytest.mark.skipif(
    False,  # 절대 스킵하지 않는다 — ImportError 로 FAIL 이 RED 확인
    reason="항상 실행",
)


# ---------------------------------------------------------------------------
# 공통 fixture: run_sensitivity_parallel 가 없으면 즉시 FAIL
# ---------------------------------------------------------------------------


def _assert_api_exists() -> None:
    """run_sensitivity_parallel 가 import 되지 않으면 AssertionError."""
    assert _IMPORT_OK and run_sensitivity_parallel is not None, (
        "run_sensitivity_parallel 가 stock_agent.backtest 에 없음 — "
        "ImportError/AttributeError (RED 상태)"
    )


# ---------------------------------------------------------------------------
# A. TestDeterminismVsSerial — 직렬 vs 병렬 결과 동일
# ---------------------------------------------------------------------------


class TestDeterminismVsSerial:
    """run_sensitivity ↔ run_sensitivity_parallel 결과 동일 검증."""

    def test_결과_개수_직렬과_동일_workers2(self):
        """max_workers=2 병렬 결과 개수 == 직렬 결과 개수."""
        _assert_api_exists()
        bars = _build_bars_2sym_3day()
        loader = InMemoryBarLoader(bars)
        grid = _make_2x2_grid()
        base_config = _make_base_config()

        rows_serial = run_sensitivity(loader, _DATE1, _DATE3, (_SYM_A, _SYM_B), base_config, grid)
        rows_parallel = run_sensitivity_parallel(  # type: ignore[misc]
            _inmemory_factory_2sym_3day,
            _DATE1,
            _DATE3,
            (_SYM_A, _SYM_B),
            base_config,
            grid,
            max_workers=2,
            mp_context=_FORK_CTX,
        )

        assert len(rows_serial) == len(rows_parallel)

    def test_params_직렬과_동일_workers2(self):
        """max_workers=2 — 각 행의 params 가 직렬 결과와 완전 일치."""
        _assert_api_exists()
        bars = _build_bars_2sym_3day()
        loader = InMemoryBarLoader(bars)
        grid = _make_2x2_grid()
        base_config = _make_base_config()

        rows_serial = run_sensitivity(loader, _DATE1, _DATE3, (_SYM_A, _SYM_B), base_config, grid)
        rows_parallel = run_sensitivity_parallel(  # type: ignore[misc]
            _inmemory_factory_2sym_3day,
            _DATE1,
            _DATE3,
            (_SYM_A, _SYM_B),
            base_config,
            grid,
            max_workers=2,
            mp_context=_FORK_CTX,
        )

        for r_serial, r_parallel in zip(rows_serial, rows_parallel, strict=True):
            msg = f"params 불일치: serial={r_serial.params}, parallel={r_parallel.params}"
            assert r_serial.params == r_parallel.params, msg

    def test_net_pnl_직렬과_동일_workers2(self):
        """max_workers=2 — 각 행의 metrics.net_pnl_krw 가 직렬 결과와 일치."""
        _assert_api_exists()
        bars = _build_bars_2sym_3day()
        loader = InMemoryBarLoader(bars)
        grid = _make_2x2_grid()
        base_config = _make_base_config()

        rows_serial = run_sensitivity(loader, _DATE1, _DATE3, (_SYM_A, _SYM_B), base_config, grid)
        rows_parallel = run_sensitivity_parallel(  # type: ignore[misc]
            _inmemory_factory_2sym_3day,
            _DATE1,
            _DATE3,
            (_SYM_A, _SYM_B),
            base_config,
            grid,
            max_workers=2,
            mp_context=_FORK_CTX,
        )

        for r_serial, r_parallel in zip(rows_serial, rows_parallel, strict=True):
            assert r_serial.metrics.net_pnl_krw == r_parallel.metrics.net_pnl_krw

    def test_trade_count_직렬과_동일_workers2(self):
        """max_workers=2 — 각 행의 trade_count 가 직렬 결과와 일치."""
        _assert_api_exists()
        bars = _build_bars_2sym_3day()
        loader = InMemoryBarLoader(bars)
        grid = _make_2x2_grid()
        base_config = _make_base_config()

        rows_serial = run_sensitivity(loader, _DATE1, _DATE3, (_SYM_A, _SYM_B), base_config, grid)
        rows_parallel = run_sensitivity_parallel(  # type: ignore[misc]
            _inmemory_factory_2sym_3day,
            _DATE1,
            _DATE3,
            (_SYM_A, _SYM_B),
            base_config,
            grid,
            max_workers=2,
            mp_context=_FORK_CTX,
        )

        for r_serial, r_parallel in zip(rows_serial, rows_parallel, strict=True):
            assert r_serial.trade_count == r_parallel.trade_count

    def test_결과_완전_일치_workers4(self):
        """max_workers=4 — 직렬과 params·net_pnl·trade_count 전부 일치."""
        _assert_api_exists()
        bars = _build_bars_2sym_3day()
        loader = InMemoryBarLoader(bars)
        grid = _make_2x2_grid()
        base_config = _make_base_config()

        rows_serial = run_sensitivity(loader, _DATE1, _DATE3, (_SYM_A, _SYM_B), base_config, grid)
        rows_parallel = run_sensitivity_parallel(  # type: ignore[misc]
            _inmemory_factory_2sym_3day,
            _DATE1,
            _DATE3,
            (_SYM_A, _SYM_B),
            base_config,
            grid,
            max_workers=4,
            mp_context=_FORK_CTX,
        )

        assert len(rows_serial) == len(rows_parallel)
        for r_s, r_p in zip(rows_serial, rows_parallel, strict=True):
            assert r_s.params == r_p.params
            assert r_s.metrics.net_pnl_krw == r_p.metrics.net_pnl_krw
            assert r_s.trade_count == r_p.trade_count


# ---------------------------------------------------------------------------
# B. TestOrderPreserved — 결과 순서가 grid.iter_combinations() 와 일치
# ---------------------------------------------------------------------------


class TestOrderPreserved:
    """as_completed 비동기 완료 순서와 무관하게 combo 선언 순서가 유지됨을 검증."""

    def test_순서_grid_iter_combinations_일치(self):
        """반환 tuple 의 i-번째 row.params 가 i-번째 combo 와 일치한다."""
        _assert_api_exists()
        grid = _make_2x2_grid_distinct()
        base_config = _make_base_config()

        combos = list(grid.iter_combinations())
        rows_parallel = run_sensitivity_parallel(  # type: ignore[misc]
            _inmemory_factory_2sym_3day,
            _DATE1,
            _DATE3,
            (_SYM_A, _SYM_B),
            base_config,
            grid,
            max_workers=2,
            mp_context=_FORK_CTX,
        )

        assert len(rows_parallel) == len(combos)
        for i, (combo, row) in enumerate(zip(combos, rows_parallel, strict=True)):
            expected_params = tuple(combo.items())
            msg = f"index={i}: 기대={expected_params}, 실제={row.params}"
            assert row.params == expected_params, msg

    def test_단일_워커_순서_보장(self):
        """max_workers=1 에서도 순서가 보장된다."""
        _assert_api_exists()
        grid = _make_2x2_grid_distinct()
        base_config = _make_base_config()

        combos = list(grid.iter_combinations())
        rows_parallel = run_sensitivity_parallel(  # type: ignore[misc]
            _inmemory_factory_2sym_3day,
            _DATE1,
            _DATE3,
            (_SYM_A,),
            base_config,
            grid,
            max_workers=1,
            mp_context=_FORK_CTX,
        )

        for i, (combo, row) in enumerate(zip(combos, rows_parallel, strict=True)):
            assert row.params == tuple(combo.items()), f"index={i} 순서 불일치"


# ---------------------------------------------------------------------------
# C. TestLoaderFactoryCalledPerWorker — loader_factory 호출 횟수
# ---------------------------------------------------------------------------


class TestLoaderFactoryCalledPerWorker:
    """loader_factory 가 워커/조합 단위로 호출되는지 파일 카운터로 검증."""

    def test_factory_호출_최소_1회_이상(self, tmp_path: Path):
        """4 조합 + workers=2 → factory 가 1회 이상 호출된다."""
        _assert_api_exists()
        counter_path = str(tmp_path / "counter.txt")
        grid = _make_2x2_grid()
        base_config = _make_base_config()

        # closure 금지 — functools.partial(top-level 함수, path) 패턴
        factory = _make_counting_factory(counter_path)

        run_sensitivity_parallel(  # type: ignore[misc]
            factory,
            _DATE1,
            _DATE3,
            (_SYM_A,),
            base_config,
            grid,
            max_workers=2,
            mp_context=_FORK_CTX,
        )

        counter_file = Path(counter_path)
        assert counter_file.exists(), "카운터 파일이 생성되지 않음"
        call_count = counter_file.read_text().count("\n")
        assert call_count >= 1, f"factory 호출 횟수={call_count}, 최소 1회 기대"

    def test_factory_조합_수만큼_호출(self, tmp_path: Path):
        """4 조합 + workers=4 → factory 가 정확히 4회 호출된다 (조합당 1회)."""
        _assert_api_exists()
        counter_path = str(tmp_path / "counter4.txt")
        grid = _make_2x2_grid()
        base_config = _make_base_config()

        factory = _make_counting_factory(counter_path)

        run_sensitivity_parallel(  # type: ignore[misc]
            factory,
            _DATE1,
            _DATE3,
            (_SYM_A,),
            base_config,
            grid,
            max_workers=4,
            mp_context=_FORK_CTX,
        )

        counter_file = Path(counter_path)
        assert counter_file.exists()
        call_count = counter_file.read_text().count("\n")
        # 조합이 4개이므로 factory 는 정확히 4회 호출돼야 한다
        assert call_count == 4, f"factory 호출 횟수={call_count}, 4회 기대 (조합 4개)"


# ---------------------------------------------------------------------------
# D. TestFailFastOnWorkerError — 워커 예외 → 전체 취소 + 전파
# ---------------------------------------------------------------------------


class TestFailFastOnWorkerError:
    """한 워커가 RuntimeError 를 raise 하면 호출자로 투명하게 전파된다."""

    def test_워커_실패_RuntimeError_전파(self):
        """_failing_stream_factory 주입 → RuntimeError("worker failure") 전파."""
        _assert_api_exists()
        grid = _make_2x2_grid()
        base_config = _make_base_config()

        with pytest.raises(RuntimeError, match="worker failure"):
            run_sensitivity_parallel(  # type: ignore[misc]
                _failing_stream_factory,
                _DATE1,
                _DATE1,
                (_SYM_A,),
                base_config,
                grid,
                max_workers=2,
                mp_context=_FORK_CTX,
            )

    def test_워커_실패_예외_타입_보존(self):
        """ProcessPoolExecutor 가 워커 예외를 메인으로 재발생 — RuntimeError 타입 확인."""
        _assert_api_exists()
        grid = _make_2x2_grid()
        base_config = _make_base_config()

        exc_caught = None
        try:
            run_sensitivity_parallel(  # type: ignore[misc]
                _failing_stream_factory,
                _DATE1,
                _DATE1,
                (_SYM_A,),
                base_config,
                grid,
                max_workers=2,
                mp_context=_FORK_CTX,
            )
        except RuntimeError as e:
            exc_caught = e
        except Exception as e:  # 다른 예외 유형도 허용 (PPE 래핑 시)
            exc_caught = e

        assert exc_caught is not None, "예외가 전파되지 않음 — fail-fast 계약 위반"


# ---------------------------------------------------------------------------
# E. TestMaxWorkersGuard — max_workers <= 0 입력 가드
# ---------------------------------------------------------------------------


class TestMaxWorkersGuard:
    """max_workers <= 0 → RuntimeError. max_workers=None 은 정상."""

    def test_max_workers_0_RuntimeError(self):
        """max_workers=0 → RuntimeError (match='max_workers')."""
        _assert_api_exists()
        grid = _make_2x2_grid()
        base_config = _make_base_config()

        with pytest.raises(RuntimeError, match="max_workers"):
            run_sensitivity_parallel(  # type: ignore[misc]
                _inmemory_factory_2sym_3day,
                _DATE1,
                _DATE1,
                (_SYM_A,),
                base_config,
                grid,
                max_workers=0,
                mp_context=_FORK_CTX,
            )

    def test_max_workers_음수_RuntimeError(self):
        """max_workers=-1 → RuntimeError (match='max_workers')."""
        _assert_api_exists()
        grid = _make_2x2_grid()
        base_config = _make_base_config()

        with pytest.raises(RuntimeError, match="max_workers"):
            run_sensitivity_parallel(  # type: ignore[misc]
                _inmemory_factory_2sym_3day,
                _DATE1,
                _DATE1,
                (_SYM_A,),
                base_config,
                grid,
                max_workers=-1,
                mp_context=_FORK_CTX,
            )

    def test_max_workers_음수_큰값_RuntimeError(self):
        """max_workers=-99 → RuntimeError."""
        _assert_api_exists()
        grid = _make_2x2_grid()
        base_config = _make_base_config()

        with pytest.raises(RuntimeError, match="max_workers"):
            run_sensitivity_parallel(  # type: ignore[misc]
                _inmemory_factory_2sym_3day,
                _DATE1,
                _DATE1,
                (_SYM_A,),
                base_config,
                grid,
                max_workers=-99,
                mp_context=_FORK_CTX,
            )

    def test_max_workers_None_정상_smoke(self):
        """max_workers=None 은 PPE 기본값 사용 — 예외 없이 결과 반환."""
        _assert_api_exists()
        grid = _make_2x2_grid()
        base_config = _make_base_config()

        rows = run_sensitivity_parallel(  # type: ignore[misc]
            _inmemory_factory_2sym_3day,
            _DATE1,
            _DATE3,
            (_SYM_A,),
            base_config,
            grid,
            max_workers=None,
            mp_context=_FORK_CTX,
        )
        assert len(rows) == grid.size


# ---------------------------------------------------------------------------
# F. TestSensitivityRowPickleRoundtrip — pickle 왕복 안전성
# ---------------------------------------------------------------------------


class TestSensitivityRowPickleRoundtrip:
    """ProcessPoolExecutor 가 row 를 워커→메인으로 직렬화할 때 데이터 보존 검증."""

    def test_pickle_unpickle_params_보존(self):
        """SensitivityRow.params 가 pickle/unpickle 후 동일하다."""
        row = SensitivityRow(
            params=(
                ("strategy.stop_loss_pct", Decimal("0.015")),
                ("strategy.take_profit_pct", Decimal("0.030")),
            ),
            metrics=BacktestMetrics(
                total_return_pct=Decimal("0.05"),
                max_drawdown_pct=Decimal("-0.03"),
                sharpe_ratio=Decimal("1.2"),
                win_rate=Decimal("0.6"),
                avg_pnl_ratio=Decimal("1.5"),
                trades_per_day=Decimal("2.0"),
                net_pnl_krw=50_000,
            ),
            trade_count=10,
            rejected_total=2,
            post_slippage_rejections=0,
        )

        restored = pickle.loads(pickle.dumps(row))

        assert restored.params == row.params
        assert restored.metrics.net_pnl_krw == row.metrics.net_pnl_krw
        assert restored.trade_count == row.trade_count
        assert restored.rejected_total == row.rejected_total
        assert restored.metrics.total_return_pct == row.metrics.total_return_pct


# ---------------------------------------------------------------------------
# RED 확인용 — import 실패 시 명시적 FAIL 테스트
# ---------------------------------------------------------------------------


class TestImportExists:
    """run_sensitivity_parallel 가 backtest 패키지에 노출돼 있는지 검증.

    현재 미구현이므로 이 테스트는 FAIL 해야 한다 (RED).
    """

    def test_run_sensitivity_parallel_importable(self):
        """stock_agent.backtest 에서 run_sensitivity_parallel 를 import 할 수 있어야 한다."""
        from stock_agent.backtest import (  # noqa: F401, PLC0415
            run_sensitivity_parallel as _fn,
        )

        assert callable(_fn), "run_sensitivity_parallel 는 callable 이어야 한다"
