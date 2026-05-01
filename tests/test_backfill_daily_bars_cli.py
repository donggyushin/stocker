"""scripts/backfill_daily_bars.py 공개 계약 단위 테스트 (RED 명세).

_parse_args / _run_pipeline / main(exit code) 를 검증한다.
HistoricalDataStore 와 load_kospi200_universe 는 전부 mock 으로 교체.
실 sqlite·pykrx·네트워크 접촉 없음.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# scripts/backfill_daily_bars.py 로드
# (scripts/ 에 __init__.py 없음 — spec_from_file_location 사용,
#  backfill_minute_bars 와 동일 패턴)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "backfill_daily_bars.py"

_src = str(_PROJECT_ROOT / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

# 스크립트 로드 시도. 파일이 없으면 _LOAD_ERROR 에 예외를 기록하고
# 각 테스트 함수에서 pytest.fail() 로 개별 FAIL 케이스를 생성한다.
_LOAD_ERROR: Exception | None = None
backfill_daily_cli = None  # type: ignore[assignment]

try:
    _spec = importlib.util.spec_from_file_location("backfill_daily_cli", _SCRIPT_PATH)
    if _spec is None or _spec.loader is None:
        raise ImportError(f"spec_from_file_location 이 None 반환: {_SCRIPT_PATH}")
    backfill_daily_cli = importlib.util.module_from_spec(_spec)
    sys.modules["backfill_daily_cli"] = backfill_daily_cli
    _spec.loader.exec_module(backfill_daily_cli)  # type: ignore[union-attr]
except Exception as _e:
    _LOAD_ERROR = _e


def _require_module() -> None:
    """각 테스트 시작 시 호출 — 모듈 로드 실패면 pytest.fail() 로 FAIL."""
    if _LOAD_ERROR is not None:
        pytest.fail(
            f"scripts/backfill_daily_bars.py 로드 실패 (RED 예상): {_LOAD_ERROR}",
            pytrace=False,
        )


# ---------------------------------------------------------------------------
# 검증 대상 심볼 참조 (로드 성공 시에만 유효)
# ---------------------------------------------------------------------------


def _parse_args(argv=None):  # type: ignore[misc]
    _require_module()
    return backfill_daily_cli._parse_args(argv)  # type: ignore[union-attr]


def _run_pipeline(args, *, store_factory=None, universe_loader=None):  # type: ignore[misc]
    _require_module()
    return backfill_daily_cli._run_pipeline(  # type: ignore[union-attr]
        args,
        store_factory=store_factory,
        universe_loader=universe_loader,
    )


def main(argv=None):  # type: ignore[misc]
    _require_module()
    return backfill_daily_cli.main(argv)  # type: ignore[union-attr]


# exit code 상수 — 로드 전 placeholder; 실제 값은 _require_module() 후 사용
_EXIT_OK = 0
_EXIT_PARTIAL_FAILURE = 1
_EXIT_INPUT_ERROR = 2
_EXIT_IO_ERROR = 3

# ---------------------------------------------------------------------------
# stock_agent 공개 심볼 — HistoricalDataError, KospiUniverse
# ---------------------------------------------------------------------------
from stock_agent.data import HistoricalDataError, KospiUniverse  # noqa: E402

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

_DATE_START = date(2025, 4, 1)
_DATE_END = date(2026, 4, 1)


def _make_universe(*tickers: str) -> KospiUniverse:
    """테스트용 KospiUniverse 더미 생성 헬퍼."""
    return KospiUniverse(
        as_of_date=date(2025, 6, 9),
        source="test",
        tickers=tuple(tickers),
    )


def _make_args(
    start: date = _DATE_START,
    end: date = _DATE_END,
    symbols: str = "",
    universe_yaml: Path | None = None,
    db_path: Path = Path("data/stock_agent.db"),
) -> object:
    """_parse_args 없이 직접 argparse.Namespace 생성 헬퍼."""
    import argparse

    ns = argparse.Namespace()
    ns.start = start
    ns.end = end
    ns.symbols = symbols
    ns.universe_yaml = universe_yaml
    ns.db_path = db_path
    return ns


def _make_mock_store(*, raise_on_fetch: Exception | None = None) -> MagicMock:
    """HistoricalDataStore MagicMock 헬퍼.

    raise_on_fetch 가 None 이면 fetch_daily_ohlcv 는 빈 리스트 반환.
    """
    store = MagicMock()
    if raise_on_fetch is not None:
        store.fetch_daily_ohlcv.side_effect = raise_on_fetch
    else:
        store.fetch_daily_ohlcv.return_value = []
    return store


# ===========================================================================
# 1. _parse_args
# ===========================================================================


class TestParseArgs:
    def test_정상_파싱_from_to_symbols_universe_yaml_db_path(self, tmp_path: Path):
        """--from / --to / --symbols / --universe-yaml / --db-path 모두 정상 파싱."""
        yaml_path = tmp_path / "universe.yaml"
        db = tmp_path / "custom.db"
        args = _parse_args(
            [
                "--from=2025-04-01",
                "--to=2026-04-01",
                "--symbols=005930,000660",
                f"--universe-yaml={yaml_path}",
                f"--db-path={db}",
            ]
        )
        assert args.start == date(2025, 4, 1)
        assert args.end == date(2026, 4, 1)
        assert args.symbols == "005930,000660"
        assert args.universe_yaml == yaml_path
        assert args.db_path == db

    def test_from_to_date_객체_타입(self):
        """--from / --to 가 date 객체로 파싱된다."""
        args = _parse_args(["--from=2025-06-01", "--to=2026-03-31"])
        assert isinstance(args.start, date)
        assert isinstance(args.end, date)

    def test_from_gt_to_parse_단계_통과(self):
        """--from > --to 는 argparse 단계를 통과하고 _run_pipeline 단계에서 거부된다."""
        args = _parse_args(["--from=2026-04-01", "--to=2025-04-01"])
        # parse 단계에서는 SystemExit 가 나면 안 됨 — 반환되어야 함
        assert args.start > args.end  # 실제로 역전된 날짜가 담겨 있어야 함

    def test_symbols_미지정_기본값_빈_문자열(self):
        """--symbols 미지정 → 기본값 '' (빈 문자열)."""
        args = _parse_args(["--from=2025-04-01", "--to=2026-04-01"])
        assert args.symbols == ""

    def test_db_path_기본값(self):
        """--db-path 미지정 → 기본값 Path('data/stock_agent.db')."""
        args = _parse_args(["--from=2025-04-01", "--to=2026-04-01"])
        assert args.db_path == Path("data/stock_agent.db")


# ===========================================================================
# 2. _run_pipeline
# ===========================================================================


class TestRunPipeline:
    """_run_pipeline(args, *, store_factory, universe_loader) → exit code 검증."""

    def test_정상_2심볼_모두_성공_exit_0(self):
        """universe 2 심볼 모두 성공 → exit 0 + fetch_daily_ohlcv 2회 호출."""
        store = _make_mock_store()
        store_factory = MagicMock(return_value=store)

        universe = _make_universe("005930", "000660")
        universe_loader = MagicMock(return_value=universe)

        args = _make_args(symbols="", start=_DATE_START, end=_DATE_END)
        result = _run_pipeline(
            args,
            store_factory=store_factory,
            universe_loader=universe_loader,
        )

        assert result == _EXIT_OK
        assert store.fetch_daily_ohlcv.call_count == 2

    def test_symbols_직접_지정_시_universe_loader_미호출(self):
        """--symbols 지정 시 universe_loader 가 호출되지 않는다."""
        store = _make_mock_store()
        store_factory = MagicMock(return_value=store)
        universe_loader = MagicMock()

        args = _make_args(symbols="005930,000660")
        _run_pipeline(
            args,
            store_factory=store_factory,
            universe_loader=universe_loader,
        )

        universe_loader.assert_not_called()

    def test_빈_universe_exit_2(self):
        """universe_loader 가 빈 tickers 반환 → exit 2."""
        store_factory = MagicMock(return_value=_make_mock_store())
        universe = _make_universe()  # tickers=()
        universe_loader = MagicMock(return_value=universe)

        args = _make_args(symbols="")
        result = _run_pipeline(
            args,
            store_factory=store_factory,
            universe_loader=universe_loader,
        )

        assert result == _EXIT_INPUT_ERROR

    def test_start_gt_end_exit_2_fetch_미호출(self):
        """start > end → exit 2 + fetch_daily_ohlcv 미호출."""
        store = _make_mock_store()
        store_factory = MagicMock(return_value=store)
        universe = _make_universe("005930")
        universe_loader = MagicMock(return_value=universe)

        args = _make_args(start=date(2026, 4, 1), end=date(2025, 4, 1), symbols="005930")
        result = _run_pipeline(
            args,
            store_factory=store_factory,
            universe_loader=universe_loader,
        )

        assert result == _EXIT_INPUT_ERROR
        store.fetch_daily_ohlcv.assert_not_called()

    def test_한_심볼_HistoricalDataError_다음_진행_exit_1(self):
        """000660 에서 HistoricalDataError → 다음 심볼 진행 + exit 1 + 둘 다 fetch 호출."""
        call_order: list[str] = []

        def _fetch(symbol, start, end):
            call_order.append(symbol)
            if symbol == "000660":
                raise HistoricalDataError("조회 실패")
            return []

        store = _make_mock_store()
        store.fetch_daily_ohlcv.side_effect = _fetch
        store_factory = MagicMock(return_value=store)

        universe = _make_universe("005930", "000660")
        universe_loader = MagicMock(return_value=universe)

        args = _make_args(symbols="005930,000660")
        result = _run_pipeline(
            args,
            store_factory=store_factory,
            universe_loader=universe_loader,
        )

        assert result == _EXIT_PARTIAL_FAILURE
        assert "005930" in call_order
        assert "000660" in call_order
        assert store.fetch_daily_ohlcv.call_count == 2

    def test_store_생성자_RuntimeError_exit_2(self):
        """HistoricalDataStore.__init__ 에서 RuntimeError → exit 2."""
        store_factory = MagicMock(side_effect=RuntimeError("DB 초기화 실패"))
        universe_loader = MagicMock(return_value=_make_universe("005930"))

        args = _make_args(symbols="005930")
        result = _run_pipeline(
            args,
            store_factory=store_factory,
            universe_loader=universe_loader,
        )

        assert result == _EXIT_INPUT_ERROR

    def test_store_생성자_OSError_exit_3(self):
        """HistoricalDataStore.__init__ 에서 OSError → exit 3."""
        store_factory = MagicMock(side_effect=OSError("디스크 오류"))
        universe_loader = MagicMock(return_value=_make_universe("005930"))

        args = _make_args(symbols="005930")
        result = _run_pipeline(
            args,
            store_factory=store_factory,
            universe_loader=universe_loader,
        )

        assert result == _EXIT_IO_ERROR

    def test_try_finally_store_close_호출_정상경로(self):
        """정상 완료 후에도 store.close() 가 1회 호출된다."""
        store = _make_mock_store()
        store_factory = MagicMock(return_value=store)
        universe = _make_universe("005930")
        universe_loader = MagicMock(return_value=universe)

        args = _make_args(symbols="005930")
        _run_pipeline(
            args,
            store_factory=store_factory,
            universe_loader=universe_loader,
        )

        store.close.assert_called_once()

    def test_try_finally_store_close_호출_예외경로(self):
        """fetch 중 HistoricalDataError 가 발생해도 store.close() 1회 호출 (try/finally)."""
        store = _make_mock_store(raise_on_fetch=HistoricalDataError("실패"))
        store_factory = MagicMock(return_value=store)
        universe = _make_universe("005930")
        universe_loader = MagicMock(return_value=universe)

        args = _make_args(symbols="005930")
        _run_pipeline(
            args,
            store_factory=store_factory,
            universe_loader=universe_loader,
        )

        store.close.assert_called_once()

    def test_symbols_쉼표_파싱_공백제거(self):
        """'005930 , 000660' 에서 공백 제거 후 두 심볼이 각각 fetch 된다."""
        store = _make_mock_store()
        store_factory = MagicMock(return_value=store)
        universe_loader = MagicMock()

        args = _make_args(symbols=" 005930 , 000660 ")
        _run_pipeline(
            args,
            store_factory=store_factory,
            universe_loader=universe_loader,
        )

        called_symbols = [c.args[0] for c in store.fetch_daily_ohlcv.call_args_list]
        assert "005930" in called_symbols
        assert "000660" in called_symbols

    def test_fetch_daily_ohlcv_인자_start_end_전달(self):
        """fetch_daily_ohlcv(symbol, start, end) 인자 순서·값 검증."""
        store = _make_mock_store()
        store_factory = MagicMock(return_value=store)
        universe_loader = MagicMock()

        args = _make_args(symbols="005930", start=_DATE_START, end=_DATE_END)
        _run_pipeline(
            args,
            store_factory=store_factory,
            universe_loader=universe_loader,
        )

        store.fetch_daily_ohlcv.assert_called_once_with("005930", _DATE_START, _DATE_END)

    def test_store_factory_db_path_전달(self, tmp_path: Path):
        """store_factory 가 args.db_path 를 인자로 받아 호출된다."""
        db = tmp_path / "custom.db"
        store = _make_mock_store()
        store_factory = MagicMock(return_value=store)
        universe_loader = MagicMock(return_value=_make_universe("005930"))

        args = _make_args(symbols="005930", db_path=db)
        _run_pipeline(
            args,
            store_factory=store_factory,
            universe_loader=universe_loader,
        )

        store_factory.assert_called_once_with(db)


# ===========================================================================
# 3. main(argv) exit code
# ===========================================================================


class TestMainExitCode:
    """main() 의 exit code 경로 검증 — _run_pipeline 을 mock 으로 대체."""

    _BASE_ARGV = [
        "--from=2025-04-01",
        "--to=2026-04-01",
        "--symbols=005930,000660",
    ]

    def test_정상_exit_0(self, monkeypatch):
        """정상 경로 → exit 0."""
        _require_module()
        monkeypatch.setattr(backfill_daily_cli, "_run_pipeline", lambda *a, **kw: _EXIT_OK)
        assert main(self._BASE_ARGV) == 0

    def test_잘못된_날짜_포맷_SystemExit_2(self):
        """--from 에 잘못된 날짜 포맷 → argparse SystemExit (exit code 2)."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--from=not-a-date", "--to=2026-04-01"])
        assert exc_info.value.code == 2
