"""scripts/backfill_minute_bars.py 공개 계약 단위 테스트 (RED 명세).

_parse_args / _resolve_symbols / _run_pipeline / main(exit code) 를 검증한다.
KisMinuteBarLoader 와 get_settings 는 전부 monkeypatch/MagicMock 으로 교체.
실 KIS 네트워크·실 pykis·실 DB 접촉 없음.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# scripts/backfill_minute_bars.py 로드
# (scripts/ 에 __init__.py 없음 — spec_from_file_location 사용, backtest_cli 와 동일 패턴)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "backfill_minute_bars.py"

_src = str(_PROJECT_ROOT / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

# 스크립트 로드 시도. 파일이 없으면 _LOAD_ERROR 에 예외를 기록하고,
# 각 테스트 함수에서 pytest.fail() 로 개별 FAIL 케이스를 생성한다.
# (collection 오류 1건이 아닌 "N tests FAILED" 로 보고되어 RED 케이스 수가 명확해진다.)
_LOAD_ERROR: Exception | None = None
backfill_cli = None  # type: ignore[assignment]

try:
    _spec = importlib.util.spec_from_file_location("backfill_cli", _SCRIPT_PATH)
    if _spec is None or _spec.loader is None:
        raise ImportError(f"spec_from_file_location 이 None 반환: {_SCRIPT_PATH}")
    backfill_cli = importlib.util.module_from_spec(_spec)
    sys.modules["backfill_cli"] = backfill_cli
    _spec.loader.exec_module(backfill_cli)  # type: ignore[union-attr]
except Exception as _e:
    _LOAD_ERROR = _e


def _require_module():
    """각 테스트 시작 시 호출 — 모듈 로드 실패면 pytest.fail() 로 FAIL."""
    if _LOAD_ERROR is not None:
        pytest.fail(
            f"scripts/backfill_minute_bars.py 로드 실패 (RED 예상): {_LOAD_ERROR}",
            pytrace=False,
        )


# ---------------------------------------------------------------------------
# 검증 대상 심볼 참조 (로드 성공 시에만 유효)
# ---------------------------------------------------------------------------


def _parse_args(argv=None):  # type: ignore[misc]
    _require_module()
    return backfill_cli._parse_args(argv)  # type: ignore[union-attr]


def _resolve_symbols(raw):  # type: ignore[misc]
    _require_module()
    return backfill_cli._resolve_symbols(raw)  # type: ignore[union-attr]


def _run_pipeline(args):  # type: ignore[misc]
    _require_module()
    return backfill_cli._run_pipeline(args)  # type: ignore[union-attr]


def main(argv=None):  # type: ignore[misc]
    _require_module()
    return backfill_cli.main(argv)  # type: ignore[union-attr]


def _get_exit_const(name: str) -> int:
    _require_module()
    return getattr(backfill_cli, name)  # type: ignore[union-attr]


_EXIT_OK = 0  # 로드 전 placeholder — 실제 값은 _require_module() 후 사용
_EXIT_PARTIAL_FAILURE = 1
_EXIT_INPUT_ERROR = 2
_EXIT_IO_ERROR = 3

# ---------------------------------------------------------------------------
# stock_agent 공개 심볼
# ---------------------------------------------------------------------------
from stock_agent.data import KisMinuteBarLoadError, MinuteBar  # noqa: E402
from stock_agent.data.realtime import MinuteBar as _MinuteBarImpl  # noqa: E402

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_DATE_START = date(2025, 4, 1)
_DATE_END = date(2026, 4, 1)


def _make_minute_bar(
    symbol: str = "005930",
    h: int = 9,
    m: int = 30,
    d: date = _DATE_START,
) -> MinuteBar:
    """테스트용 MinuteBar 더미 생성 헬퍼."""
    return _MinuteBarImpl(
        symbol=symbol,
        bar_time=datetime(d.year, d.month, d.day, h, m, tzinfo=KST),
        open=Decimal("70000"),
        high=Decimal("71000"),
        low=Decimal("69500"),
        close=Decimal("70500"),
        volume=1000,
    )


def _make_fake_loader(
    *,
    bars_per_symbol: list[MinuteBar] | None = None,
    raise_for_symbols: dict[str, Exception] | None = None,
) -> MagicMock:
    """stream() 이 bars_per_symbol 을 yield 하거나
    특정 심볼에서 예외를 raise 하는 MagicMock loader."""
    loader = MagicMock()
    bars_per_symbol = bars_per_symbol or []
    raise_for_symbols = raise_for_symbols or {}

    def _stream(start, end, symbols):
        for sym in symbols:
            if sym in raise_for_symbols:
                raise raise_for_symbols[sym]
            yield from bars_per_symbol

    loader.stream.side_effect = _stream
    return loader


def _make_fake_settings(has_live_keys: bool = True) -> MagicMock:
    """has_live_keys 만 만족하는 Settings 더블."""
    s = MagicMock()
    s.has_live_keys = has_live_keys
    return s


# ===========================================================================
# 1. _parse_args
# ===========================================================================


class TestParseArgs:
    def test_정상_파싱_from_to_symbols_throttle(self):
        """--from / --to / --symbols / --throttle-s 모두 정상 파싱된다."""
        args = _parse_args(
            [
                "--from=2025-04-01",
                "--to=2026-04-01",
                "--symbols=005930,000660",
                "--throttle-s=0.5",
            ]
        )
        assert args.start == date(2025, 4, 1)
        assert args.end == date(2026, 4, 1)
        assert args.symbols == "005930,000660"
        assert args.throttle_s == pytest.approx(0.5)

    def test_from_누락_SystemExit(self):
        """--from 없으면 argparse SystemExit."""
        with pytest.raises(SystemExit):
            _parse_args(["--to=2026-04-01"])

    def test_to_누락_SystemExit(self):
        """--to 없으면 argparse SystemExit."""
        with pytest.raises(SystemExit):
            _parse_args(["--from=2025-04-01"])

    def test_throttle_s_음수_SystemExit_또는_RuntimeError(self):
        """--throttle-s 음수 → SystemExit 또는 RuntimeError (argparse 단계 또는 검증 단계)."""
        try:
            args = _parse_args(
                [
                    "--from=2025-04-01",
                    "--to=2026-04-01",
                    "--throttle-s=-1.0",
                ]
            )
            # argparse 단계에서 잡지 못했다면 main 에서 RuntimeError 가 나와야 한다.
            # _parse_args 가 반환한 경우, throttle_s 가 음수임을 확인.
            assert args.throttle_s < 0
        except SystemExit:
            pass  # argparse 단계에서 잡은 경우도 OK

    def test_기본값_확인_symbols_빈_문자열(self):
        """--symbols 미지정 → 기본값 '' (빈 문자열)."""
        args = _parse_args(["--from=2025-04-01", "--to=2026-04-01"])
        assert args.symbols == ""

    def test_기본값_throttle_s_0_2(self):
        """--throttle-s 미지정 → 기본값 0.2."""
        args = _parse_args(["--from=2025-04-01", "--to=2026-04-01"])
        assert args.throttle_s == pytest.approx(0.2)

    def test_기본값_cache_db_path_None(self):
        """--cache-db-path 미지정 → 기본값 None."""
        args = _parse_args(["--from=2025-04-01", "--to=2026-04-01"])
        assert args.cache_db_path is None

    def test_cache_db_path_Path_파싱(self, tmp_path: Path):
        """--cache-db-path 지정 시 Path 객체로 파싱된다."""
        db = tmp_path / "my.db"
        args = _parse_args(
            [
                "--from=2025-04-01",
                "--to=2026-04-01",
                f"--cache-db-path={db}",
            ]
        )
        assert args.cache_db_path == db

    def test_from_to_date_객체(self):
        """--from / --to 가 date 객체로 파싱된다."""
        args = _parse_args(["--from=2025-06-01", "--to=2026-03-31"])
        assert isinstance(args.start, date)
        assert isinstance(args.end, date)
        assert args.start == date(2025, 6, 1)
        assert args.end == date(2026, 3, 31)

    def test_symbols_단일_심볼(self):
        """--symbols 단일 심볼도 정상 파싱된다."""
        args = _parse_args(
            [
                "--from=2025-04-01",
                "--to=2026-04-01",
                "--symbols=005930",
            ]
        )
        assert args.symbols == "005930"


# ===========================================================================
# 2. _resolve_symbols
# ===========================================================================


class TestResolveSymbols:
    def test_쉼표_구분_파싱(self):
        """'005930,000660' → 2개 코드 tuple."""
        result = _resolve_symbols("005930,000660")
        assert result == ("005930", "000660")

    def test_공백_포함_쉼표_파싱(self):
        """' 005930 , 000660 ' — 각 항목 strip."""
        result = _resolve_symbols(" 005930 , 000660 ")
        assert result == ("005930", "000660")

    def test_단일_심볼(self):
        """단일 심볼도 tuple 로 반환된다."""
        result = _resolve_symbols("005930")
        assert result == ("005930",)

    def test_빈_문자열_universe_호출(self, monkeypatch):
        """빈 raw → load_kospi200_universe 호출 결과 반환."""
        fake_universe = type("U", (), {"tickers": ("005930", "000660")})()
        monkeypatch.setattr(backfill_cli, "load_kospi200_universe", lambda: fake_universe)
        result = _resolve_symbols("")
        assert result == ("005930", "000660")

    def test_공백만_universe_호출(self, monkeypatch):
        """'   ' (공백만) → load_kospi200_universe 호출."""
        called = []
        fake_universe = type("U", (), {"tickers": ("005930",)})()

        def _fake_load():
            called.append(True)
            return fake_universe

        monkeypatch.setattr(backfill_cli, "load_kospi200_universe", _fake_load)
        result = _resolve_symbols("   ")
        assert len(called) == 1
        assert result == ("005930",)

    def test_universe_비면_RuntimeError(self, monkeypatch):
        """universe.tickers 가 비면 RuntimeError."""
        fake_universe = type("U", (), {"tickers": ()})()
        monkeypatch.setattr(backfill_cli, "load_kospi200_universe", lambda: fake_universe)
        with pytest.raises(RuntimeError):
            _resolve_symbols("")


# ===========================================================================
# 3. _run_pipeline
# ===========================================================================


class TestRunPipeline:
    """_run_pipeline(args) -> (succeeded, failed, total_bars) 검증."""

    def _make_args(
        self,
        symbols: str = "005930,000660",
        throttle_s: float = 0.0,
        cache_db_path=None,
        start: date = _DATE_START,
        end: date = _DATE_END,
    ):
        """_parse_args 없이 직접 args Namespace 생성 헬퍼."""
        import argparse

        ns = argparse.Namespace()
        ns.start = start
        ns.end = end
        ns.symbols = symbols
        ns.throttle_s = throttle_s
        ns.cache_db_path = cache_db_path
        return ns

    def test_모든_심볼_성공_카운터(self, monkeypatch):
        """2 심볼 모두 성공 → succeeded=2, failed=0, total_bars>0."""
        bars = [_make_minute_bar("005930"), _make_minute_bar("000660")]
        fake_loader = _make_fake_loader(bars_per_symbol=bars)
        loader_cls = MagicMock(return_value=fake_loader)
        monkeypatch.setattr(backfill_cli, "KisMinuteBarLoader", loader_cls)
        monkeypatch.setattr(
            backfill_cli,
            "get_settings",
            lambda: _make_fake_settings(has_live_keys=True),
        )

        args = self._make_args(symbols="005930,000660")
        succeeded, failed, total_bars = _run_pipeline(args)

        assert succeeded == 2
        assert failed == 0
        assert total_bars > 0

    def test_모든_심볼_성공_loader_close_호출됨(self, monkeypatch):
        """성공 후 loader.close() 가 반드시 호출된다 (try/finally 보장)."""
        bars = [_make_minute_bar("005930")]
        fake_loader = _make_fake_loader(bars_per_symbol=bars)
        loader_cls = MagicMock(return_value=fake_loader)
        monkeypatch.setattr(backfill_cli, "KisMinuteBarLoader", loader_cls)
        monkeypatch.setattr(
            backfill_cli,
            "get_settings",
            lambda: _make_fake_settings(has_live_keys=True),
        )

        args = self._make_args(symbols="005930")
        _run_pipeline(args)

        fake_loader.close.assert_called_once()

    def test_일부_심볼_KisMinuteBarLoadError_격리(self, monkeypatch):
        """000660 에서 KisMinuteBarLoadError → failed=1, 005930 은 정상 처리."""
        bars_005930 = [_make_minute_bar("005930")]
        fake_loader = _make_fake_loader(
            bars_per_symbol=bars_005930,
            raise_for_symbols={"000660": KisMinuteBarLoadError("API 오류")},
        )
        loader_cls = MagicMock(return_value=fake_loader)
        monkeypatch.setattr(backfill_cli, "KisMinuteBarLoader", loader_cls)
        monkeypatch.setattr(
            backfill_cli,
            "get_settings",
            lambda: _make_fake_settings(has_live_keys=True),
        )

        args = self._make_args(symbols="005930,000660")
        succeeded, failed, total_bars = _run_pipeline(args)

        assert succeeded == 1
        assert failed == 1
        assert total_bars > 0  # 005930 분봉은 카운트됨

    def test_일부_실패_후_loader_close_호출됨(self, monkeypatch):
        """예외 발생 시에도 loader.close() 가 호출된다 (try/finally)."""
        fake_loader = _make_fake_loader(
            bars_per_symbol=[],
            raise_for_symbols={"005930": KisMinuteBarLoadError("실패")},
        )
        loader_cls = MagicMock(return_value=fake_loader)
        monkeypatch.setattr(backfill_cli, "KisMinuteBarLoader", loader_cls)
        monkeypatch.setattr(
            backfill_cli,
            "get_settings",
            lambda: _make_fake_settings(has_live_keys=True),
        )

        args = self._make_args(symbols="005930")
        _run_pipeline(args)

        fake_loader.close.assert_called_once()

    def test_모든_심볼_실패_succeeded_0(self, monkeypatch):
        """모든 심볼 KisMinuteBarLoadError → succeeded=0, failed=2."""
        fake_loader = _make_fake_loader(
            bars_per_symbol=[],
            raise_for_symbols={
                "005930": KisMinuteBarLoadError("오류A"),
                "000660": KisMinuteBarLoadError("오류B"),
            },
        )
        loader_cls = MagicMock(return_value=fake_loader)
        monkeypatch.setattr(backfill_cli, "KisMinuteBarLoader", loader_cls)
        monkeypatch.setattr(
            backfill_cli,
            "get_settings",
            lambda: _make_fake_settings(has_live_keys=True),
        )

        args = self._make_args(symbols="005930,000660")
        succeeded, failed, total_bars = _run_pipeline(args)

        assert succeeded == 0
        assert failed == 2

    def test_stream_인자가_심볼별_1건씩_분리_호출(self, monkeypatch):
        """loader.stream 이 심볼별 1개씩 (start, end, (symbol,)) 형태로 호출된다."""
        bars = [_make_minute_bar("005930")]
        fake_loader = _make_fake_loader(bars_per_symbol=bars)
        loader_cls = MagicMock(return_value=fake_loader)
        monkeypatch.setattr(backfill_cli, "KisMinuteBarLoader", loader_cls)
        monkeypatch.setattr(
            backfill_cli,
            "get_settings",
            lambda: _make_fake_settings(has_live_keys=True),
        )

        args = self._make_args(symbols="005930,000660")
        _run_pipeline(args)

        # stream 이 2회 호출됐는지 확인
        assert fake_loader.stream.call_count == 2
        # 각 호출에 단일 심볼 tuple 이 포함됐는지 확인
        calls = fake_loader.stream.call_args_list
        called_symbols = [c.args[2] if c.args else c.kwargs.get("symbols") for c in calls]
        # 심볼 tuple 이 단원소 tuple 이어야 함
        for sym_tuple in called_symbols:
            assert len(sym_tuple) == 1

    def test_stream_첫번째_인자가_start_end_순서(self, monkeypatch):
        """stream(start, end, (symbol,)) — 첫 두 인자가 start/end 순서인지 검증."""
        bars = [_make_minute_bar("005930")]
        fake_loader = _make_fake_loader(bars_per_symbol=bars)
        loader_cls = MagicMock(return_value=fake_loader)
        monkeypatch.setattr(backfill_cli, "KisMinuteBarLoader", loader_cls)
        monkeypatch.setattr(
            backfill_cli,
            "get_settings",
            lambda: _make_fake_settings(has_live_keys=True),
        )

        args = self._make_args(symbols="005930", start=_DATE_START, end=_DATE_END)
        _run_pipeline(args)

        call_args = fake_loader.stream.call_args
        # positional 첫 번째 인자 = start
        assert call_args.args[0] == _DATE_START
        # positional 두 번째 인자 = end
        assert call_args.args[1] == _DATE_END

    def test_throttle_s가_KisMinuteBarLoader_생성자에_전달됨(self, monkeypatch):
        """throttle_s 가 KisMinuteBarLoader 생성자 kwarg 로 전달된다."""
        fake_loader = _make_fake_loader(bars_per_symbol=[])
        loader_cls = MagicMock(return_value=fake_loader)
        monkeypatch.setattr(backfill_cli, "KisMinuteBarLoader", loader_cls)
        monkeypatch.setattr(
            backfill_cli,
            "get_settings",
            lambda: _make_fake_settings(has_live_keys=True),
        )

        args = self._make_args(symbols="005930", throttle_s=1.5)
        _run_pipeline(args)

        # KisMinuteBarLoader 가 throttle_s=1.5 로 호출됐는지 확인
        loader_cls.assert_called_once()
        kwargs = loader_cls.call_args.kwargs
        assert "throttle_s" in kwargs
        assert kwargs["throttle_s"] == pytest.approx(1.5)

    def test_기본값_경로_throttle_s_0_2_생성자_전달(self, monkeypatch) -> None:
        """--from/--to 만 지정한 기본값 경로에서 KisMinuteBarLoader 생성자에 throttle_s=0.2 가 전달된다.

        _make_args 는 throttle_s default=0.0 으로 CLI default(0.2) 와 달라 직접 사용 불가.
        backfill_cli._parse_args 로 argparse default 를 통과한 Namespace 를 만들어 검증.
        """
        _require_module()
        fake_loader = _make_fake_loader(bars_per_symbol=[])
        loader_cls = MagicMock(return_value=fake_loader)
        monkeypatch.setattr(backfill_cli, "KisMinuteBarLoader", loader_cls)
        monkeypatch.setattr(
            backfill_cli,
            "get_settings",
            lambda: _make_fake_settings(has_live_keys=True),
        )

        # argparse default 를 통과한 Namespace — throttle_s 는 CLI default(0.2) 가 채워짐
        args = _parse_args(["--from=2025-04-01", "--to=2026-04-01", "--symbols=005930"])
        _run_pipeline(args)

        loader_cls.assert_called_once()
        assert "throttle_s" in loader_cls.call_args.kwargs
        assert loader_cls.call_args.kwargs["throttle_s"] == pytest.approx(0.2)

    def test_cache_db_path_None이면_생성자에_None_또는_미전달(self, monkeypatch):
        """cache_db_path=None 이면 KisMinuteBarLoader 생성자에 None 이거나 인자 자체 누락."""
        fake_loader = _make_fake_loader(bars_per_symbol=[])
        loader_cls = MagicMock(return_value=fake_loader)
        monkeypatch.setattr(backfill_cli, "KisMinuteBarLoader", loader_cls)
        monkeypatch.setattr(
            backfill_cli,
            "get_settings",
            lambda: _make_fake_settings(has_live_keys=True),
        )

        args = self._make_args(symbols="005930", cache_db_path=None)
        _run_pipeline(args)

        loader_cls.assert_called_once()
        kwargs = loader_cls.call_args.kwargs
        # cache_db_path 가 없거나 None 이면 OK
        assert kwargs.get("cache_db_path") is None

    def test_cache_db_path_명시_전달(self, monkeypatch, tmp_path: Path):
        """cache_db_path 명시 시 KisMinuteBarLoader 생성자에 정확히 전달된다."""
        db_path = tmp_path / "cache.db"
        fake_loader = _make_fake_loader(bars_per_symbol=[])
        loader_cls = MagicMock(return_value=fake_loader)
        monkeypatch.setattr(backfill_cli, "KisMinuteBarLoader", loader_cls)
        monkeypatch.setattr(
            backfill_cli,
            "get_settings",
            lambda: _make_fake_settings(has_live_keys=True),
        )

        args = self._make_args(symbols="005930", cache_db_path=db_path)
        _run_pipeline(args)

        loader_cls.assert_called_once()
        kwargs = loader_cls.call_args.kwargs
        assert kwargs.get("cache_db_path") == db_path

    def test_total_bars_분봉_카운트_정확(self, monkeypatch):
        """2 심볼 × 3 bars = 총 6 bar 카운트."""
        three_bars = [
            _make_minute_bar("005930", h=9, m=30),
            _make_minute_bar("005930", h=9, m=31),
            _make_minute_bar("005930", h=9, m=32),
        ]
        fake_loader = _make_fake_loader(bars_per_symbol=three_bars)
        loader_cls = MagicMock(return_value=fake_loader)
        monkeypatch.setattr(backfill_cli, "KisMinuteBarLoader", loader_cls)
        monkeypatch.setattr(
            backfill_cli,
            "get_settings",
            lambda: _make_fake_settings(has_live_keys=True),
        )

        args = self._make_args(symbols="005930,000660")
        succeeded, failed, total_bars = _run_pipeline(args)

        assert total_bars == 6  # 2 심볼 × 3 bars


# ===========================================================================
# 4. main(argv) exit code
# ===========================================================================


class TestMainExitCode:
    """_run_pipeline 을 monkeypatch 로 대체해 exit code 경로만 검증."""

    _BASE_ARGV = [
        "--from=2025-04-01",
        "--to=2026-04-01",
        "--symbols=005930,000660",
    ]

    def test_전체_성공_exit_0(self, monkeypatch):
        """모든 심볼 성공 → exit code 0."""

        def _fake_pipeline(args):
            return (2, 0, 100)

        monkeypatch.setattr(backfill_cli, "_run_pipeline", _fake_pipeline)
        assert main(self._BASE_ARGV) == 0

    def test_부분_실패_exit_1(self, monkeypatch):
        """일부 심볼 실패(failed>0) → exit code 1."""

        def _fake_pipeline(args):
            return (1, 1, 50)

        monkeypatch.setattr(backfill_cli, "_run_pipeline", _fake_pipeline)
        assert main(self._BASE_ARGV) == 1

    def test_전체_실패_exit_1(self, monkeypatch):
        """모든 심볼 실패(succeeded=0) → exit code 1."""

        def _fake_pipeline(args):
            return (0, 2, 0)

        monkeypatch.setattr(backfill_cli, "_run_pipeline", _fake_pipeline)
        assert main(self._BASE_ARGV) == 1

    def test_start_after_end_exit_2(self, monkeypatch):
        """--from 이 --to 보다 나중 → exit code 2, _run_pipeline 미호출."""
        called = []
        monkeypatch.setattr(
            backfill_cli, "_run_pipeline", lambda args: called.append(True) or (0, 0, 0)
        )
        result = main(["--from=2026-04-01", "--to=2025-04-01"])
        assert result == 2
        assert called == []

    def test_throttle_s_음수_exit_2(self, monkeypatch):
        """throttle_s < 0 → exit code 2."""
        called = []

        def _fake_pipeline(args):
            called.append(True)
            return (0, 0, 0)

        monkeypatch.setattr(backfill_cli, "_run_pipeline", _fake_pipeline)
        result = main(
            [
                "--from=2025-04-01",
                "--to=2026-04-01",
                "--throttle-s=-1.0",
            ]
        )
        # throttle_s 음수는 _EXIT_INPUT_ERROR(2) 또는 argparse SystemExit
        # main 이 SystemExit 를 잡아 2 로 변환하거나 직접 2 를 반환해야 함
        assert result == 2

    def test_빈_universe_symbols_exit_2(self, monkeypatch):
        """--symbols '' 이고 universe 비어있음 → exit code 2."""

        def _fake_pipeline(args):
            raise RuntimeError("유니버스 비어있음")

        monkeypatch.setattr(backfill_cli, "_run_pipeline", _fake_pipeline)
        fake_universe = type("U", (), {"tickers": ()})()
        monkeypatch.setattr(backfill_cli, "load_kospi200_universe", lambda: fake_universe)
        result = main(["--from=2025-04-01", "--to=2026-04-01", "--symbols="])
        assert result == 2

    def test_생성자_KisMinuteBarLoadError_exit_2(self, monkeypatch):
        """생성자에서 KisMinuteBarLoadError (has_live_keys=False) → exit code 2."""

        def _fake_pipeline(args):
            raise KisMinuteBarLoadError("live 키 없음")

        monkeypatch.setattr(backfill_cli, "_run_pipeline", _fake_pipeline)
        result = main(self._BASE_ARGV)
        assert result == 2

    def test_RuntimeError_exit_2(self, monkeypatch):
        """RuntimeError 발생 → exit code 2."""

        def _fake_pipeline(args):
            raise RuntimeError("설정 오류")

        monkeypatch.setattr(backfill_cli, "_run_pipeline", _fake_pipeline)
        assert main(self._BASE_ARGV) == 2

    def test_OSError_exit_3(self, monkeypatch):
        """OSError 발생 → exit code 3."""

        def _fake_pipeline(args):
            raise OSError("DB 디렉토리 생성 실패")

        monkeypatch.setattr(backfill_cli, "_run_pipeline", _fake_pipeline)
        assert main(self._BASE_ARGV) == 3

    def test_start_eq_end_정상통과(self, monkeypatch):
        """--from 과 --to 가 동일 날짜 → 정상 처리 (exit code 0 또는 1)."""

        def _fake_pipeline(args):
            return (1, 0, 10)

        monkeypatch.setattr(backfill_cli, "_run_pipeline", _fake_pipeline)
        result = main(["--from=2026-01-02", "--to=2026-01-02"])
        assert result in (_EXIT_OK, _EXIT_PARTIAL_FAILURE)
