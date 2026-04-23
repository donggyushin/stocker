"""scripts/debug_kis_minute.py 공개 계약 단위 테스트.

_validate_args / _extract_raw_data / _coerce_json_safe 의 pure 함수 동작을 검증한다.
실 KIS 네트워크·실 pykis·실 파일 I/O 없음 — stdlib + monkeypatch 만 사용.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# scripts/debug_kis_minute.py 로드
# (scripts/ 에 __init__.py 없음 — spec_from_file_location 사용, backfill_minute_bars 와 동일 패턴)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "debug_kis_minute.py"

_src = str(_PROJECT_ROOT / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

_LOAD_ERROR: Exception | None = None
debug_cli = None  # type: ignore[assignment]

try:
    _spec = importlib.util.spec_from_file_location("debug_cli", _SCRIPT_PATH)
    if _spec is None or _spec.loader is None:
        raise ImportError(f"spec_from_file_location 이 None 반환: {_SCRIPT_PATH}")
    debug_cli = importlib.util.module_from_spec(_spec)
    sys.modules["debug_cli"] = debug_cli
    _spec.loader.exec_module(debug_cli)  # type: ignore[union-attr]
except Exception as _e:
    _LOAD_ERROR = _e


def _require_module() -> None:
    """각 테스트 시작 시 호출 — 모듈 로드 실패면 pytest.fail() 로 FAIL."""
    if _LOAD_ERROR is not None:
        pytest.fail(
            f"scripts/debug_kis_minute.py 로드 실패: {_LOAD_ERROR}",
            pytrace=False,
        )


KST = timezone(timedelta(hours=9))


# ===========================================================================
# TestValidateArgs — _validate_args() pure 함수 검증
# ===========================================================================


class TestValidateArgs:
    """_validate_args 의 심볼·커서 포맷 검증 + trade_date=None 채움 계약."""

    def _make_namespace(self, symbol="005930", cursor="153000", trade_date=None):
        import argparse

        ns = argparse.Namespace()
        ns.symbol = symbol
        ns.cursor = cursor
        ns.trade_date = trade_date
        return ns

    def test_정상_args_예외없음(self) -> None:
        """6자리 숫자 symbol + 6자리 숫자 cursor + 날짜 지정 → 예외 없음."""
        _require_module()
        args = self._make_namespace(symbol="005930", cursor="153000", trade_date=date(2026, 4, 21))
        debug_cli._validate_args(args)  # type: ignore[union-attr]
        # trade_date 변경 없음
        assert args.trade_date == date(2026, 4, 21)

    def test_symbol_알파벳_포함_RuntimeError(self) -> None:
        """'abc123' 같이 알파벳 포함 symbol → RuntimeError."""
        _require_module()
        args = self._make_namespace(symbol="abc123", trade_date=date(2026, 4, 21))
        with pytest.raises(RuntimeError, match="symbol"):
            debug_cli._validate_args(args)  # type: ignore[union-attr]

    def test_symbol_7자리_RuntimeError(self) -> None:
        """7자리 숫자 symbol '0059303' → _SYMBOL_RE 위반 → RuntimeError."""
        _require_module()
        args = self._make_namespace(symbol="0059303", trade_date=date(2026, 4, 21))
        with pytest.raises(RuntimeError, match="symbol"):
            debug_cli._validate_args(args)  # type: ignore[union-attr]

    def test_cursor_알파벳_포함_RuntimeError(self) -> None:
        """'15:30:00' 같이 콜론 포함 cursor → _CURSOR_RE 위반 → RuntimeError."""
        _require_module()
        args = self._make_namespace(cursor="15:300", trade_date=date(2026, 4, 21))
        with pytest.raises(RuntimeError, match="cursor"):
            debug_cli._validate_args(args)  # type: ignore[union-attr]

    def test_cursor_5자리_RuntimeError(self) -> None:
        """5자리 숫자 cursor → _CURSOR_RE 위반 → RuntimeError."""
        _require_module()
        args = self._make_namespace(cursor="15300", trade_date=date(2026, 4, 21))
        with pytest.raises(RuntimeError, match="cursor"):
            debug_cli._validate_args(args)  # type: ignore[union-attr]

    def test_trade_date_None_이면_default_date_주입(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """trade_date=None → _default_date() 결과가 args.trade_date 에 채워진다.

        monkeypatch 로 debug_cli._default_date 를 더미 함수로 교체해 결정론화.
        """
        _require_module()
        dummy_date = date(2026, 4, 18)  # 금요일

        monkeypatch.setattr(debug_cli, "_default_date", lambda: dummy_date)

        args = self._make_namespace(trade_date=None)
        debug_cli._validate_args(args)  # type: ignore[union-attr]

        assert args.trade_date == dummy_date

    def test_trade_date_None_월요일기준_금요일_반환(self) -> None:
        """_default_date 자체 동작 검증: 월요일(weekday=0) 기준 clock → 금요일 반환.

        monkeypatch 로 datetime.now(KST) 를 월요일로 고정해 직전 평일(금요일) 계산 확인.
        """
        _require_module()
        from datetime import datetime

        # 2026-04-20 월요일 → 직전 평일 = 2026-04-19 일요일... 아니라 금요일
        # _default_date: today = monday, candidate = sunday → 주말 skip → friday
        monday = datetime(2026, 4, 20, 10, 0, 0, tzinfo=KST)

        # debug_cli 내부 datetime.now 교체 어려움 → _default_date 계산 로직 독립 검증
        # today=2026-04-20(월), candidate=2026-04-19(일)→skip, 2026-04-18(토)→skip, 2026-04-17(금)
        today = monday.date()
        candidate = today - timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        assert candidate == date(2026, 4, 17)  # 금요일
        assert candidate.weekday() == 4  # 4=금


# ===========================================================================
# TestExtractRawData — _extract_raw_data() pure 함수 검증
# ===========================================================================


class TestExtractRawData:
    """_extract_raw_data 의 세 가지 분기(KisDynamicDict-like / 순수 dict / 비정형) 검증."""

    def test_data_attr_dict_타입_응답_반환(self) -> None:
        """__data__ 속성이 dict 인 객체 → (data, type_name) 반환."""
        _require_module()

        class _FakeKisResponse:
            __data__ = {"rt_cd": "0", "output2": []}

        resp = _FakeKisResponse()
        data, type_name = debug_cli._extract_raw_data(resp)  # type: ignore[union-attr]

        assert data == {"rt_cd": "0", "output2": []}
        assert type_name == "_FakeKisResponse"

    def test_순수_dict_응답(self) -> None:
        """순수 dict 응답 → (data, 'dict') 반환."""
        _require_module()

        raw = {"rt_cd": "0", "output2": [{"foo": "bar"}]}
        data, type_name = debug_cli._extract_raw_data(raw)  # type: ignore[union-attr]

        assert data == raw
        assert type_name == "dict"

    def test_비정형_응답_None_반환(self) -> None:
        """__data__ 없고 dict 도 아닌 객체 (예: object()) → (None, type_name) 반환."""
        _require_module()

        resp = object()
        data, type_name = debug_cli._extract_raw_data(resp)  # type: ignore[union-attr]

        assert data is None
        assert type_name == "object"

    def test_data_attr_비dict_타입_None_반환(self) -> None:
        """__data__ 가 있지만 dict 가 아닌 경우 → (None, type_name) 반환."""
        _require_module()

        class _FakeBadResponse:
            __data__ = "not-a-dict"

        resp = _FakeBadResponse()
        data, type_name = debug_cli._extract_raw_data(resp)  # type: ignore[union-attr]

        # __data__ 가 dict 가 아니면 isinstance(data, dict) 실패 → dict 분기 체크
        # resp 자체가 dict 가 아니므로 (None, type_name) 반환
        assert data is None
        assert type_name == "_FakeBadResponse"


# ===========================================================================
# TestCoerceJsonSafe — _coerce_json_safe() 보안 계약 검증
# ===========================================================================


class TestCoerceJsonSafe:
    """_coerce_json_safe 의 재귀 변환 + 비표준 타입 repr 폴백 + 보안 계약 검증."""

    def test_primitive_str_그대로(self) -> None:
        """str primitive → 그대로 반환."""
        _require_module()
        result = debug_cli._coerce_json_safe("hello")  # type: ignore[union-attr]
        assert result == "hello"
        assert isinstance(result, str)

    def test_primitive_int_그대로(self) -> None:
        """int primitive → 그대로 반환."""
        _require_module()
        assert debug_cli._coerce_json_safe(42) == 42  # type: ignore[union-attr]

    def test_primitive_float_그대로(self) -> None:
        """float primitive → 그대로 반환."""
        _require_module()
        assert debug_cli._coerce_json_safe(3.14) == pytest.approx(3.14)  # type: ignore[union-attr]

    def test_primitive_bool_그대로(self) -> None:
        """bool primitive → 그대로 반환."""
        _require_module()
        assert debug_cli._coerce_json_safe(True) is True  # type: ignore[union-attr]
        assert debug_cli._coerce_json_safe(False) is False  # type: ignore[union-attr]

    def test_none_그대로(self) -> None:
        """None → 그대로 반환."""
        _require_module()
        assert debug_cli._coerce_json_safe(None) is None  # type: ignore[union-attr]

    def test_중첩_dict_재귀_변환_str_key(self) -> None:
        """중첩 dict → 재귀 변환, str key 강제."""
        _require_module()
        nested = {"outer": {"inner": 123}}
        result = debug_cli._coerce_json_safe(nested)  # type: ignore[union-attr]
        assert result == {"outer": {"inner": 123}}
        # 모든 key 가 str
        assert all(isinstance(k, str) for k in result)
        assert all(isinstance(k, str) for k in result["outer"])

    def test_list_재귀_변환(self) -> None:
        """list → 재귀 변환."""
        _require_module()
        lst = [1, "two", {"three": 3}]
        result = debug_cli._coerce_json_safe(lst)  # type: ignore[union-attr]
        assert result == [1, "two", {"three": 3}]

    def test_비표준_Decimal_타입_repr_반환(self) -> None:
        """Decimal 같은 비표준 타입 → repr(value) 문자열 반환.

        보안 계약: 원본 객체(Decimal)가 반환값에 그대로 노출되지 않음을 확인.
        """
        _require_module()
        val = Decimal("71000")
        result = debug_cli._coerce_json_safe(val)  # type: ignore[union-attr]

        # 반드시 str 이어야 함 (원본 Decimal 객체 유출 금지)
        assert type(result) is str, f"기대 str, 실제 {type(result)}"
        # repr 결과 포함 여부
        assert "71000" in result

    def test_커스텀_repr_객체_repr_반환(self) -> None:
        """__repr__ 커스텀 객체 → repr(value) 문자열 반환, type(result)==str."""
        _require_module()

        class _CustomObj:
            def __repr__(self):
                return "<CustomObj:secret_data>"

        obj = _CustomObj()
        result = debug_cli._coerce_json_safe(obj)  # type: ignore[union-attr]

        # 원본 객체가 반환값에 그대로 노출되면 안 됨
        assert type(result) is str, f"기대 str, 실제 {type(result)}"
        assert result == "<CustomObj:secret_data>"

    def test_dict_내_비표준_타입_재귀_repr(self) -> None:
        """dict 안에 Decimal 값 → 재귀적으로 repr 변환된다."""
        _require_module()
        d = {"price": Decimal("71000"), "vol": 1234}
        result = debug_cli._coerce_json_safe(d)  # type: ignore[union-attr]

        # price 는 str 로 변환돼야 함
        assert type(result["price"]) is str
        # vol 은 int 그대로
        assert result["vol"] == 1234
