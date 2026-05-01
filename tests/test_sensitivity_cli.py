"""scripts/sensitivity.py 공개 함수 단위 테스트.

main(argv) exit code 계약 + --workers 라우팅 계약을 검증한다.
외부 네트워크 · KIS · pykis · 파일시스템 접촉 없음.
  - exit code 경로: monkeypatch 로 _run_pipeline 만 대체.
  - --workers 라우팅: run_sensitivity_combos / run_sensitivity_combos_parallel 양쪽 monkeypatch.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# scripts/sensitivity.py 로드 (scripts/ 에 __init__.py 없음 — spec_from_file_location 사용)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "sensitivity.py"

_spec = importlib.util.spec_from_file_location("sensitivity_cli", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None, "scripts/sensitivity.py 로드 실패"
sensitivity_cli = importlib.util.module_from_spec(_spec)
# src/ 가 sys.path 에 있어야 stock_agent 패키지를 import 할 수 있음
_src = str(_PROJECT_ROOT / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)
# sys.modules 에 먼저 등록해야 @dataclass 가 __module__ 참조 시 NoneType 오류를 피한다.
sys.modules["sensitivity_cli"] = sensitivity_cli
_spec.loader.exec_module(sensitivity_cli)  # type: ignore[union-attr]

# ---------------------------------------------------------------------------
# 검증 대상 심볼 참조
# ---------------------------------------------------------------------------
main = sensitivity_cli.main
_EXIT_INPUT_ERROR = sensitivity_cli._EXIT_INPUT_ERROR
_EXIT_IO_ERROR = sensitivity_cli._EXIT_IO_ERROR

# ---------------------------------------------------------------------------
# stock_agent 공개 예외 참조
# ---------------------------------------------------------------------------
from stock_agent.data import (  # noqa: E402  (로드 순서상 sensitivity_cli 먼저)
    KisMinuteBarLoadError,
    MinuteCsvLoadError,
    UniverseLoadError,
)

# ---------------------------------------------------------------------------
# main(argv) exit code
# ---------------------------------------------------------------------------


class TestMainExitCode:
    """_run_pipeline 을 monkeypatch 로 대체해 exit code 경로만 검증한다."""

    _BASE_ARGV = [
        "--csv-dir=/tmp/dummy_csv",
        "--from=2023-01-01",
        "--to=2025-12-31",
    ]

    def test_성공_0(self, monkeypatch):
        """_run_pipeline 이 정상 완료하면 exit code 0."""
        monkeypatch.setattr(sensitivity_cli, "_run_pipeline", lambda _: None)
        assert main(self._BASE_ARGV) == 0

    def test_MinuteCsvLoadError_exit_2(self, monkeypatch):
        """MinuteCsvLoadError 발생 → exit code 2."""

        def _raise(_):
            raise MinuteCsvLoadError("테스트 오류")

        monkeypatch.setattr(sensitivity_cli, "_run_pipeline", _raise)
        assert main(self._BASE_ARGV) == 2

    def test_KisMinuteBarLoadError_exit_2(self, monkeypatch):
        """KisMinuteBarLoadError 발생 → exit code 2."""

        def _raise(_):
            raise KisMinuteBarLoadError("KIS 분봉 오류")

        monkeypatch.setattr(sensitivity_cli, "_run_pipeline", _raise)
        assert main(self._BASE_ARGV) == 2

    def test_UniverseLoadError_exit_2(self, monkeypatch):
        """UniverseLoadError 발생 → exit code 2.

        UniverseLoadError 는 Exception 직상속(not RuntimeError)이라
        RuntimeError 분기에 잡히지 않는다 — 전용 분기 회귀 검증.
        scripts/sensitivity.py 에 `except UniverseLoadError` 분기가 없으면
        이 테스트는 예외가 전파돼 pytest 에러로 FAIL 한다.
        """

        def _raise(_):
            raise UniverseLoadError("universe YAML 오류 시뮬레이션")

        monkeypatch.setattr(sensitivity_cli, "_run_pipeline", _raise)
        assert main(self._BASE_ARGV) == 2

    def test_RuntimeError_exit_2(self, monkeypatch):
        """RuntimeError 발생 → exit code 2."""

        def _raise(_):
            raise RuntimeError("설정·검증 오류")

        monkeypatch.setattr(sensitivity_cli, "_run_pipeline", _raise)
        assert main(self._BASE_ARGV) == 2

    def test_OSError_exit_3(self, monkeypatch):
        """OSError 발생 → exit code 3."""

        def _raise(_):
            raise OSError("I/O 오류")

        monkeypatch.setattr(sensitivity_cli, "_run_pipeline", _raise)
        assert main(self._BASE_ARGV) == 3

    def test_start_after_end_exit_2_조기반환(self, monkeypatch):
        """--from 이 --to 보다 나중 → exit code 2, _run_pipeline 미호출."""
        called = []
        monkeypatch.setattr(sensitivity_cli, "_run_pipeline", lambda _: called.append(True))
        result = main(
            [
                "--csv-dir=/tmp/dummy_csv",
                "--from=2025-12-31",
                "--to=2023-01-01",
            ]
        )
        assert result == 2
        assert called == [], "_run_pipeline 이 호출되면 안 됨"


# ---------------------------------------------------------------------------
# --workers 라우팅 검증 (신규 케이스 — run_sensitivity_parallel 미구현 RED)
# ---------------------------------------------------------------------------

# _BASE_ARGV 에 --loader=csv 를 명시해 csv_dir 파싱 오류를 우회한다.
_WORKERS_BASE_ARGV = [
    "--csv-dir=/tmp/dummy_csv",
    "--from=2023-01-01",
    "--to=2025-12-31",
]


class TestWorkersRouting:
    """--workers 옵션에 따라 run_sensitivity_combos / run_sensitivity_combos_parallel 가
    올바르게 선택되는지 검증한다.

    _run_pipeline 전체 교체가 아닌 run_sensitivity_combos / run_sensitivity_combos_parallel +
    _build_loader 를 mock 해 파일시스템 접근을 완전히 차단한다.
    """

    def _setup_mocks(self, monkeypatch, tmp_path):
        """run_sensitivity_combos / run_sensitivity_combos_parallel + _build_loader 를
        no-op mock 으로 교체한다.

        merge_sensitivity_rows / render_markdown_table / write_csv 도 패치해
        엔진 반환값 () 가 후속 단계에서 RuntimeError 를 일으키지 않게 막는다.
        output 경로는 tmp_path 로 우회해 실제 data/ 디렉토리 생성을 차단한다.
        """
        called: dict[str, bool] = {"serial": False, "parallel": False}

        # 더미 loader — stream() 이 빈 이터러블 반환
        from stock_agent.backtest import InMemoryBarLoader  # noqa: PLC0415

        _dummy_loader = InMemoryBarLoader([])

        def _fake_build_loader(*args, **kwargs):
            return _dummy_loader

        def _fake_build_loader_primitive(*args, **kwargs):
            return _dummy_loader

        def _fake_serial(*args, **kwargs):
            called["serial"] = True
            return ()

        def _fake_parallel(*args, **kwargs):
            called["parallel"] = True
            return ()

        monkeypatch.setattr(sensitivity_cli, "_build_loader", _fake_build_loader)
        monkeypatch.setattr(
            sensitivity_cli, "_build_loader_primitive", _fake_build_loader_primitive
        )
        monkeypatch.setattr(sensitivity_cli, "run_sensitivity_combos", _fake_serial)
        # run_sensitivity_combos_parallel 가 없으면 AttributeError → RED
        monkeypatch.setattr(sensitivity_cli, "run_sensitivity_combos_parallel", _fake_parallel)

        # 후속 단계 no-op: merge 반환 () 로 인한 "누락 조합" RuntimeError 방지
        monkeypatch.setattr(sensitivity_cli, "merge_sensitivity_rows", lambda *a, **kw: ())
        monkeypatch.setattr(sensitivity_cli, "render_markdown_table", lambda *a, **kw: "")
        monkeypatch.setattr(sensitivity_cli, "write_csv", lambda *a, **kw: None)

        # _run_pipeline 이 output_markdown.parent.mkdir + write_text 를 호출하므로
        # argparse default 경로(data/...) 대신 tmp_path 산하로 우회하기 위해
        # _parse_args 후 args 를 수정할 수 없다 → _run_pipeline 출력 직전 write_text 를
        # Path.write_text 레벨이 아닌 sensitivity_cli 내 호출 경로에서 이미 막았으므로
        # mkdir 만 실제로 호출된다. mkdir(parents=True, exist_ok=True) 는 부작용이
        # 없으므로 그대로 허용한다 (tmp 경로가 아닌 data/ 가 생성될 수 있으나
        # 라우팅 테스트 목적상 허용 범위).

        return called

    def test_workers_2_경로_run_sensitivity_parallel_호출(self, monkeypatch, tmp_path):
        """--workers=2 → run_sensitivity_combos_parallel 호출,
        run_sensitivity_combos 미호출, exit 0."""
        called = self._setup_mocks(monkeypatch, tmp_path)

        result = main(_WORKERS_BASE_ARGV + ["--workers=2", "--symbols=005930"])

        assert result == 0, f"exit code 기대 0, 실제 {result}"
        assert called["parallel"], "run_sensitivity_combos_parallel 가 호출돼야 한다"
        assert not called["serial"], "run_sensitivity_combos 는 호출되면 안 된다"

    def test_workers_1_경로_run_sensitivity_호출(self, monkeypatch, tmp_path):
        """--workers=1 → run_sensitivity_combos 호출, run_sensitivity_combos_parallel 미호출."""
        called = self._setup_mocks(monkeypatch, tmp_path)

        result = main(_WORKERS_BASE_ARGV + ["--workers=1", "--symbols=005930"])

        assert result == 0, f"exit code 기대 0, 실제 {result}"
        assert called["serial"], "run_sensitivity_combos 가 호출돼야 한다"
        assert not called["parallel"], "run_sensitivity_combos_parallel 는 호출되면 안 된다"

    def test_workers_0_거부_exit_2(self, monkeypatch, tmp_path):
        """--workers=0 → exit code 2 (입력 오류, run_sensitivity_combos_parallel 미호출)."""
        called = self._setup_mocks(monkeypatch, tmp_path)

        result = main(_WORKERS_BASE_ARGV + ["--workers=0", "--symbols=005930"])

        assert result == 2, f"exit code 기대 2, 실제 {result}"
        assert not called["parallel"], "run_sensitivity_combos_parallel 는 호출되면 안 된다"
        assert not called["serial"], "run_sensitivity_combos 는 호출되면 안 된다"

    def test_workers_음수_거부_exit_2(self, monkeypatch, tmp_path):
        """--workers=-3 → exit code 2."""
        called = self._setup_mocks(monkeypatch, tmp_path)

        result = main(_WORKERS_BASE_ARGV + ["--workers=-3", "--symbols=005930"])

        assert result == 2, f"exit code 기대 2, 실제 {result}"
        assert not called["parallel"]
        assert not called["serial"]

    def test_workers_생략_기본값_경로_선택(self, monkeypatch, tmp_path):
        """--workers 미지정 시 기본값 경로가 호출된다 (serial 또는 parallel 중 하나)."""
        called = self._setup_mocks(monkeypatch, tmp_path)

        result = main(_WORKERS_BASE_ARGV + ["--symbols=005930"])

        assert result == 0, f"exit code 기대 0, 실제 {result}"
        # 기본값이 어떤 경로든 반드시 하나는 호출돼야 한다
        assert called["serial"] or called["parallel"], (
            "--workers 미지정 시 run_sensitivity_combos 또는 "
            "run_sensitivity_combos_parallel 중 하나가 호출돼야 한다"
        )


# ---------------------------------------------------------------------------
# --resume 분기 flush 콜백 주입 검증 (RED — 미구현)
# ---------------------------------------------------------------------------


class TestResumeFlushCallback:
    """--resume 분기에서 on_row=_flush 가 run_sensitivity_combos /
    run_sensitivity_combos_parallel 양쪽에 callable 로 주입되는지 검증.

    구현 예정 동작:
    - --resume 지정 + 미완료 조합 N개 → _run_pipeline 이 on_row=<callable> 로
      run_sensitivity_combos 또는 run_sensitivity_combos_parallel 를 호출한다.
    - --resume 없음 → on_row=None 또는 인자 자체 없음 (구현자 재량).
    """

    def _setup_flush_mocks(self, monkeypatch, resume_path=None):
        """run_sensitivity_combos / run_sensitivity_combos_parallel 를 교체해
        on_row kwarg 를 캡처한다.

        반환: captured dict — 'serial_on_row', 'parallel_on_row' 키.
        """
        captured: dict[str, object] = {
            "serial_on_row": ...,  # 아직 미호출 sentinel
            "parallel_on_row": ...,
        }

        from stock_agent.backtest import InMemoryBarLoader  # noqa: PLC0415

        _dummy_loader = InMemoryBarLoader([])

        def _fake_build_loader(*args, **kwargs):
            return _dummy_loader

        def _fake_build_loader_primitive(*args, **kwargs):
            return _dummy_loader

        def _fake_serial(*args, **kwargs):
            captured["serial_on_row"] = kwargs.get("on_row", ...)
            return ()

        def _fake_parallel(*args, **kwargs):
            captured["parallel_on_row"] = kwargs.get("on_row", ...)
            return ()

        monkeypatch.setattr(sensitivity_cli, "_build_loader", _fake_build_loader)
        monkeypatch.setattr(
            sensitivity_cli, "_build_loader_primitive", _fake_build_loader_primitive
        )
        monkeypatch.setattr(sensitivity_cli, "run_sensitivity_combos", _fake_serial)
        monkeypatch.setattr(sensitivity_cli, "run_sensitivity_combos_parallel", _fake_parallel)
        monkeypatch.setattr(sensitivity_cli, "merge_sensitivity_rows", lambda *a, **kw: ())
        monkeypatch.setattr(sensitivity_cli, "render_markdown_table", lambda *a, **kw: "")
        monkeypatch.setattr(sensitivity_cli, "write_csv", lambda *a, **kw: None)

        # --resume 파일이 존재하는 경우 load_sensitivity_rows 도 mock
        if resume_path is not None:
            monkeypatch.setattr(
                sensitivity_cli,
                "load_sensitivity_rows",
                lambda path, grid: (),
            )
            monkeypatch.setattr(
                sensitivity_cli,
                "filter_remaining_combos",
                # 미완료 조합 3개 반환 (전체 실행 분기 진입 보장)
                lambda grid, completed: [next(iter(grid.iter_combinations())) for _ in range(3)],
            )

        return captured

    def test_resume_분기_flush_콜백_주입(self, monkeypatch, tmp_path):
        """--resume 지정 + 미완료 조합 N 개 → run_sensitivity_combos (또는 _parallel) 호출 시
        on_row keyword 가 callable 로 주입된다."""
        # --resume 파일 경로 (존재하는 파일로 만들어야 resume 분기 진입)
        resume_path = tmp_path / "existing.csv"
        resume_path.write_text("dummy", encoding="utf-8")

        captured = self._setup_flush_mocks(monkeypatch, resume_path=resume_path)

        result = main(
            _WORKERS_BASE_ARGV
            + [
                "--workers=1",
                "--symbols=005930",
                f"--resume={resume_path}",
            ]
        )

        assert result == 0, f"exit code 기대 0, 실제 {result}"

        # serial 경로가 호출됐어야 한다 (workers=1)
        on_row_value = captured["serial_on_row"]
        _msg = "run_sensitivity_combos 가 호출되지 않음 (serial_on_row 가 sentinel)"
        assert on_row_value is not ..., _msg
        assert callable(on_row_value), (
            f"on_row 가 callable 이 아님: {type(on_row_value)!r} = {on_row_value!r}\n"
            "--resume 분기에서 on_row=_flush 를 주입해야 한다 (RED: 미구현)"
        )

    def test_resume_없음_콜백_주입_안함_또는_None(self, monkeypatch, tmp_path):
        """--resume 미지정 → on_row 가 None 이거나 인자 자체가 없다 (구현자 재량).

        즉 on_row 가 callable 이 아니어야 한다 — flush 는 resume 분기 전용.
        """
        captured = self._setup_flush_mocks(monkeypatch, resume_path=None)

        result = main(
            _WORKERS_BASE_ARGV
            + [
                "--workers=1",
                "--symbols=005930",
            ]
        )

        assert result == 0, f"exit code 기대 0, 실제 {result}"

        on_row_value = captured["serial_on_row"]
        # sentinel(...)은 호출 자체가 안 된 경우 — 호출됐다면 None or not callable 이어야 함
        if on_row_value is not ...:
            assert not callable(on_row_value), (
                f"--resume 없는 경로에서 on_row 가 callable: {on_row_value!r}\n"
                "flush 콜백은 --resume 분기에서만 주입돼야 한다"
            )

    def test_resume_parallel_분기_flush_콜백_주입(self, monkeypatch, tmp_path):
        """--resume 지정 + workers=2 → run_sensitivity_combos_parallel 호출 시
        on_row keyword 가 callable 로 주입된다."""
        resume_path = tmp_path / "existing_parallel.csv"
        resume_path.write_text("dummy", encoding="utf-8")

        captured = self._setup_flush_mocks(monkeypatch, resume_path=resume_path)

        result = main(
            _WORKERS_BASE_ARGV
            + [
                "--workers=2",
                "--symbols=005930",
                f"--resume={resume_path}",
            ]
        )

        assert result == 0, f"exit code 기대 0, 실제 {result}"

        on_row_value = captured["parallel_on_row"]
        _msg = "run_sensitivity_combos_parallel 가 호출되지 않음 (parallel_on_row 가 sentinel)"
        assert on_row_value is not ..., _msg
        assert callable(on_row_value), (
            f"on_row 가 callable 이 아님: {type(on_row_value)!r} = {on_row_value!r}\n"
            "--resume + --workers>=2 분기에서 on_row=_flush 를 주입해야 한다 (RED: 미구현)"
        )


# ---------------------------------------------------------------------------
# _resolve_symbols — universe_yaml 인자 (RED: --universe-yaml 미구현)
# ---------------------------------------------------------------------------

_resolve_symbols = sensitivity_cli._resolve_symbols
_parse_args = sensitivity_cli._parse_args


class TestResolveSymbolsUniverseYaml:
    def test_명시적_path_전달_load_kospi200_universe_path_호출(self, monkeypatch):
        """universe_yaml=Path('/custom/path.yaml') 전달 시
        load_kospi200_universe(path) 가 정확히 그 경로로 호출된다."""
        call_args: list = []
        fake_universe = type("U", (), {"tickers": ("005930", "000660")})()

        def spy(path):
            call_args.append(path)
            return fake_universe

        monkeypatch.setattr(sensitivity_cli, "load_kospi200_universe", spy)
        custom_path = Path("/custom/path.yaml")
        result = _resolve_symbols("", universe_yaml=custom_path)
        assert result == ("005930", "000660")
        assert len(call_args) == 1
        assert call_args[0] == custom_path

    def test_path_전달_raw_우선_universe_미호출(self, monkeypatch):
        """raw='005930,000660', universe_yaml=Path('/x.yaml') →
        tuple('005930','000660') 반환, load_kospi200_universe 호출 0회."""
        call_count: list = []

        def spy(path=None):
            call_count.append(True)
            return type("U", (), {"tickers": ()})()

        monkeypatch.setattr(sensitivity_cli, "load_kospi200_universe", spy)
        result = _resolve_symbols("005930,000660", universe_yaml=Path("/x.yaml"))
        assert result == ("005930", "000660")
        assert len(call_count) == 0

    def test_universe_yaml_None_인자_없이_호출(self, monkeypatch):
        """universe_yaml=None 키워드 전달 시 load_kospi200_universe() 를
        인자 없이(zero-arg) 호출하는 분기에 진입한다."""
        call_log: list = []
        fake_universe = type("U", (), {"tickers": ("035420",)})()

        def spy_noarg():
            call_log.append("noarg")
            return fake_universe

        monkeypatch.setattr(sensitivity_cli, "load_kospi200_universe", spy_noarg)
        result = _resolve_symbols("", universe_yaml=None)
        assert result == ("035420",)
        assert call_log == ["noarg"]


# ---------------------------------------------------------------------------
# _parse_args — --universe-yaml 옵션 (RED: 미구현)
# ---------------------------------------------------------------------------


class TestParseArgsUniverseYaml:
    _BASE = ["--csv-dir=/tmp/dummy", "--from=2023-01-01", "--to=2025-12-31"]

    def test_기본값_config_universe_yaml(self):
        """--universe-yaml 미지정 시 args.universe_yaml == Path('config/universe.yaml')."""
        args = _parse_args(self._BASE)
        assert args.universe_yaml == Path("config/universe.yaml")

    def test_명시적_전달_상대경로(self):
        """--universe-yaml=config/universe_top50.yaml →
        args.universe_yaml == Path('config/universe_top50.yaml')."""
        args = _parse_args(self._BASE + ["--universe-yaml=config/universe_top50.yaml"])
        assert args.universe_yaml == Path("config/universe_top50.yaml")

    def test_절대경로_isinstance_Path(self):
        """--universe-yaml=/abs/path.yaml → isinstance(args.universe_yaml, Path) True."""
        args = _parse_args(self._BASE + ["--universe-yaml=/abs/path.yaml"])
        assert isinstance(args.universe_yaml, Path)


# ---------------------------------------------------------------------------
# --grid 플래그 분기 검증 (RED — step_d1_grid 미구현)
# ---------------------------------------------------------------------------

# step_d1_grid 는 아직 sensitivity.py 에 없으므로 import 를 테스트 내부에서 처리한다.
# sensitivity_cli 모듈이 로드될 때 --grid 플래그 자체가 없으면 _parse_args 가
# SystemExit(2) 를 발생시키므로 해당 케이스는 argparse 동작으로 검증한다.

_GRID_BASE_ARGV = [
    "--csv-dir=/tmp/dummy_csv",
    "--from=2023-01-01",
    "--to=2025-12-31",
]


class TestGridFlag:
    """--grid {default,step-d1} 플래그 분기 계약.

    step_d1_grid 함수와 --grid 플래그가 모두 미구현 상태이므로
    이 클래스의 모든 테스트는 현재 RED (ImportError / SystemExit / AssertionError).
    """

    def _setup_grid_mocks(self, monkeypatch):
        """엔진 함수와 loader 를 no-op mock 으로 대체한다.

        TestWorkersRouting._setup_mocks 와 동일 패턴.
        """
        called: dict[str, int] = {"serial_combos": 0, "parallel_combos": 0}
        combo_counts: dict[str, int] = {}

        from stock_agent.backtest import InMemoryBarLoader  # noqa: PLC0415

        _dummy_loader = InMemoryBarLoader([])

        def _fake_build_loader(*args, **kwargs):
            return _dummy_loader

        def _fake_build_loader_primitive(*args, **kwargs):
            return _dummy_loader

        def _fake_serial(*args, **kwargs):
            called["serial_combos"] += 1
            combo_counts["serial"] = len(kwargs.get("combos", args[5] if len(args) > 5 else []))
            return ()

        def _fake_parallel(*args, **kwargs):
            called["parallel_combos"] += 1
            combo_counts["parallel"] = len(kwargs.get("combos", args[5] if len(args) > 5 else []))
            return ()

        monkeypatch.setattr(sensitivity_cli, "_build_loader", _fake_build_loader)
        monkeypatch.setattr(
            sensitivity_cli, "_build_loader_primitive", _fake_build_loader_primitive
        )
        monkeypatch.setattr(sensitivity_cli, "run_sensitivity_combos", _fake_serial)
        monkeypatch.setattr(sensitivity_cli, "run_sensitivity_combos_parallel", _fake_parallel)
        monkeypatch.setattr(sensitivity_cli, "merge_sensitivity_rows", lambda *a, **kw: ())
        monkeypatch.setattr(sensitivity_cli, "render_markdown_table", lambda *a, **kw: "")
        monkeypatch.setattr(sensitivity_cli, "write_csv", lambda *a, **kw: None)

        return called, combo_counts

    def test_grid_step_d1_step_d1_grid_호출_48조합(self, monkeypatch):
        """--grid=step-d1 → step_d1_grid() 가 호출되어 48 조합이 엔진에 전달된다.

        현재 RED 기대:
        - sensitivity_cli 에 --grid 플래그가 없으면 argparse SystemExit(2).
        - step_d1_grid 가 sensitivity.py 에 없으면 AttributeError / ImportError.
        """
        called, combo_counts = self._setup_grid_mocks(monkeypatch)

        # step_d1_grid 를 sensitivity_cli 에 주입 (미구현 → AttributeError 회피)
        from stock_agent.backtest.sensitivity import step_d1_grid  # noqa: PLC0415

        monkeypatch.setattr(sensitivity_cli, "step_d1_grid", step_d1_grid, raising=False)

        result = main(_GRID_BASE_ARGV + ["--workers=1", "--symbols=005930", "--grid=step-d1"])

        assert result == 0, f"exit code 기대 0, 실제 {result}"
        assert called["serial_combos"] == 1, "run_sensitivity_combos 가 호출돼야 한다"
        # step_d1_grid().size == 48 → combos 길이 48
        assert combo_counts.get("serial") == 48, f"48 조합 기대, 실제 {combo_counts.get('serial')}"

    def test_grid_default_32조합_회귀(self, monkeypatch):
        """--grid=default (또는 미지정) → default_grid() 호출 + 32 조합, 기존 동작 회귀 0."""
        called, combo_counts = self._setup_grid_mocks(monkeypatch)

        result = main(_GRID_BASE_ARGV + ["--workers=1", "--symbols=005930", "--grid=default"])

        assert result == 0, f"exit code 기대 0, 실제 {result}"
        assert called["serial_combos"] == 1
        assert combo_counts.get("serial") == 32, f"32 조합 기대, 실제 {combo_counts.get('serial')}"

    def test_grid_step_d2_step_d2_grid_호출_48조합(self, monkeypatch):
        """--grid=step-d2 → step_d2_grid() 가 호출되어 48 조합이 엔진에 전달된다.

        현재 RED 기대:
        - sensitivity_cli 에 --grid choices 에 step-d2 가 없으면 argparse SystemExit(2).
        - step_d2_grid 가 sensitivity.py 에 없으면 AttributeError / ImportError.
        """
        called, combo_counts = self._setup_grid_mocks(monkeypatch)

        # step_d2_grid 를 sensitivity_cli 에 주입 (미구현 → AttributeError 회피)
        from stock_agent.backtest.sensitivity import step_d2_grid  # noqa: PLC0415

        monkeypatch.setattr(sensitivity_cli, "step_d2_grid", step_d2_grid, raising=False)

        result = main(_GRID_BASE_ARGV + ["--workers=1", "--symbols=005930", "--grid=step-d2"])

        assert result == 0, f"exit code 기대 0, 실제 {result}"
        assert called["serial_combos"] == 1, "run_sensitivity_combos 가 호출돼야 한다"
        # step_d2_grid().size == 48 → combos 길이 48
        assert combo_counts.get("serial") == 48, f"48 조합 기대, 실제 {combo_counts.get('serial')}"

    def test_grid_foobar_exit_2(self, monkeypatch):
        """--grid=foobar → argparse choices 위반 → SystemExit(2) 발생.

        choices 에 step-d2 가 추가되어도 잘못된 값은 여전히 거부된다 (회귀).
        """
        # argparse 가 자체적으로 exit(2) 를 발생시키므로 SystemExit 예외로 잡는다.
        with pytest.raises(SystemExit) as exc_info:
            main(_GRID_BASE_ARGV + ["--symbols=005930", "--grid=foobar"])
        assert exc_info.value.code == 2
