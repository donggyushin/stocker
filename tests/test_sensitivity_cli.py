"""scripts/sensitivity.py 공개 함수 단위 테스트.

main(argv) exit code 계약을 검증한다.
외부 네트워크 · KIS · pykis · 파일시스템 접촉 없음 — monkeypatch 로 _run_pipeline 만 대체.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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
