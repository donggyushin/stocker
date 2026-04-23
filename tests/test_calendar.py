"""한국 증시 공휴일 캘린더 단위 테스트 (RED 명세).

Issue #63 — KisMinuteBarLoader 공휴일 캘린더 가드. 모듈
src/stock_agent/data/calendar.py 가 아직 없어 import 가 ImportError 로 FAIL 한다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# 지연 import 가드 — 모듈이 없으면 ImportError 로 RED 상태
# ---------------------------------------------------------------------------


def _import_calendar():
    from stock_agent.data.calendar import (
        BusinessDayCalendar,
        HolidayCalendar,
        HolidayCalendarError,
        YamlBusinessDayCalendar,
        load_kospi_holidays,
    )

    return (
        BusinessDayCalendar,
        HolidayCalendar,
        HolidayCalendarError,
        YamlBusinessDayCalendar,
        load_kospi_holidays,
    )


# ---------------------------------------------------------------------------
# 헬퍼: tmp_path 에 YAML 파일 작성
# ---------------------------------------------------------------------------


def _write_yaml(tmp_path: Path, content: Any, filename: str = "holidays.yaml") -> Path:
    """`content` 를 YAML 직렬화해 tmp_path 에 저장하고 경로를 반환한다."""
    path = tmp_path / filename
    path.write_text(yaml.dump(content, allow_unicode=True), encoding="utf-8")
    return path


def _write_raw(tmp_path: Path, text: str, filename: str = "holidays.yaml") -> Path:
    """raw 문자열을 그대로 파일에 저장한다 (파싱 오류 유발 등에 사용)."""
    path = tmp_path / filename
    path.write_text(text, encoding="utf-8")
    return path


def _make_valid_yaml_content(
    *,
    as_of_date: str = "2026-04-23",
    source: str = "KRX 정보데이터시스템 [12001] 휴장일 정보",
    holidays: list | None = None,
) -> dict:
    """유효한 holidays YAML 내용을 dict 로 반환."""
    if holidays is None:
        holidays = ["2025-01-01", "2026-01-01"]
    return {
        "as_of_date": as_of_date,
        "source": source,
        "holidays": holidays,
    }


# ===========================================================================
# TestLoadKospiHolidays — 정상 로드
# ===========================================================================


class TestLoadKospiHolidays:
    """load_kospi_holidays 정상 케이스 — HolidayCalendar 반환 및 필드 검증."""

    def test_정상_로드_필드_정확(self, tmp_path: Path) -> None:
        """올바른 YAML → HolidayCalendar 반환 + 모든 필드 정확."""
        _, HolidayCalendar, _, _, load_kospi_holidays = _import_calendar()

        path = _write_yaml(
            tmp_path,
            _make_valid_yaml_content(
                as_of_date="2026-04-23",
                source="KRX 정보데이터시스템 [12001] 휴장일 정보",
                holidays=["2025-01-01", "2026-01-01"],
            ),
        )

        result = load_kospi_holidays(path)

        assert isinstance(result, HolidayCalendar)
        assert result.as_of_date == date(2026, 4, 23)
        assert result.source == "KRX 정보데이터시스템 [12001] 휴장일 정보"
        assert date(2025, 1, 1) in result.holidays
        assert date(2026, 1, 1) in result.holidays
        assert isinstance(result.holidays, frozenset)

    def test_as_of_date_pyyaml_자동파싱_date_타입(self, tmp_path: Path) -> None:
        """PyYAML 이 as_of_date 를 date 로 자동 파싱한 경우에도 정상 처리."""
        _, HolidayCalendar, _, _, load_kospi_holidays = _import_calendar()

        # PyYAML 은 따옴표 없이 YYYY-MM-DD 형식이면 date 로 자동 파싱한다
        raw = "as_of_date: 2026-04-23\nsource: KRX\nholidays:\n  - 2025-01-01\n"
        path = _write_raw(tmp_path, raw)

        result = load_kospi_holidays(path)

        assert isinstance(result, HolidayCalendar)
        assert result.as_of_date == date(2026, 4, 23)

    def test_holidays_원소_pyyaml_자동파싱_date_타입(self, tmp_path: Path) -> None:
        """holidays 원소가 PyYAML 에 의해 date 로 자동 파싱된 경우에도
        frozenset[date] 로 정상 처리.
        """
        _, HolidayCalendar, _, _, load_kospi_holidays = _import_calendar()

        # PyYAML 은 따옴표 없는 YYYY-MM-DD 원소를 date 로 자동 파싱한다
        raw = "as_of_date: '2026-04-23'\nsource: KRX\nholidays:\n  - 2025-01-01\n  - 2026-01-01\n"
        path = _write_raw(tmp_path, raw)

        result = load_kospi_holidays(path)

        assert date(2025, 1, 1) in result.holidays
        assert date(2026, 1, 1) in result.holidays

    def test_빈_holidays_리스트_허용_경고_발생(self, tmp_path: Path, mocker: Any) -> None:
        """holidays: [] 빈 리스트 → 예외 없이 빈 frozenset 반환 + logger.warning 1회."""
        _, HolidayCalendar, _, _, load_kospi_holidays = _import_calendar()

        path = _write_yaml(
            tmp_path,
            _make_valid_yaml_content(holidays=[]),
        )

        mock_warning = mocker.patch("stock_agent.data.calendar.logger.warning")

        result = load_kospi_holidays(path)

        assert isinstance(result, HolidayCalendar)
        assert result.holidays == frozenset()
        mock_warning.assert_called_once()


# ===========================================================================
# TestLoadKospiHolidaysErrors — 오류 케이스
# ===========================================================================


class TestLoadKospiHolidaysErrors:
    """load_kospi_holidays 오류 케이스 — HolidayCalendarError 발생 검증."""

    def test_파일_없음_HolidayCalendarError(self, tmp_path: Path) -> None:
        """존재하지 않는 경로 → HolidayCalendarError (메시지에 path 포함)."""
        _, _, HolidayCalendarError, _, load_kospi_holidays = _import_calendar()

        missing = tmp_path / "nonexistent" / "holidays.yaml"

        with pytest.raises(HolidayCalendarError) as excinfo:
            load_kospi_holidays(missing)

        assert str(missing) in str(excinfo.value)

    def test_YAML_파싱_실패_HolidayCalendarError(self, tmp_path: Path) -> None:
        """손상된 YAML → HolidayCalendarError."""
        _, _, HolidayCalendarError, _, load_kospi_holidays = _import_calendar()

        broken_yaml = "holidays: [\n  - '2025-01-01\n"
        path = _write_raw(tmp_path, broken_yaml)

        with pytest.raises(HolidayCalendarError):
            load_kospi_holidays(path)

    def test_최상위가_dict_아님_HolidayCalendarError(self, tmp_path: Path) -> None:
        """YAML 최상위가 dict 가 아닌 경우 (예: 리스트) → HolidayCalendarError."""
        _, _, HolidayCalendarError, _, load_kospi_holidays = _import_calendar()

        path = _write_yaml(tmp_path, ["2025-01-01", "2026-01-01"])

        with pytest.raises(HolidayCalendarError):
            load_kospi_holidays(path)

    @pytest.mark.parametrize(
        "missing_key",
        ["as_of_date", "source", "holidays"],
        ids=["누락_as_of_date", "누락_source", "누락_holidays"],
    )
    def test_필수_키_누락_HolidayCalendarError(self, tmp_path: Path, missing_key: str) -> None:
        """as_of_date / source / holidays 중 하나라도 누락 → HolidayCalendarError."""
        _, _, HolidayCalendarError, _, load_kospi_holidays = _import_calendar()

        content = _make_valid_yaml_content()
        del content[missing_key]
        path = _write_yaml(tmp_path, content)

        with pytest.raises(HolidayCalendarError):
            load_kospi_holidays(path)

    def test_as_of_date_잘못된_포맷_HolidayCalendarError(self, tmp_path: Path) -> None:
        """as_of_date 가 YYYY-MM-DD 포맷이 아닌 문자열 → HolidayCalendarError."""
        _, _, HolidayCalendarError, _, load_kospi_holidays = _import_calendar()

        content = _make_valid_yaml_content(as_of_date="not-a-date")
        path = _write_yaml(tmp_path, content)

        with pytest.raises(HolidayCalendarError):
            load_kospi_holidays(path)

    def test_source_빈문자열_HolidayCalendarError(self, tmp_path: Path) -> None:
        """source 가 빈 문자열 → HolidayCalendarError."""
        _, _, HolidayCalendarError, _, load_kospi_holidays = _import_calendar()

        content = _make_valid_yaml_content(source="")
        path = _write_yaml(tmp_path, content)

        with pytest.raises(HolidayCalendarError):
            load_kospi_holidays(path)

    def test_holidays_list_아님_HolidayCalendarError(self, tmp_path: Path) -> None:
        """holidays 값이 list 가 아닌 경우 → HolidayCalendarError."""
        _, _, HolidayCalendarError, _, load_kospi_holidays = _import_calendar()

        content = _make_valid_yaml_content()
        content["holidays"] = "2025-01-01"  # list 가 아닌 str
        path = _write_yaml(tmp_path, content)

        with pytest.raises(HolidayCalendarError):
            load_kospi_holidays(path)

    def test_holidays_원소_잘못된_날짜_포맷_HolidayCalendarError(self, tmp_path: Path) -> None:
        """holidays 원소 중 YYYY-MM-DD 포맷이 아닌 문자열 → HolidayCalendarError."""
        _, _, HolidayCalendarError, _, load_kospi_holidays = _import_calendar()

        content = _make_valid_yaml_content(holidays=["2025-01-01", "bad-date"])
        path = _write_yaml(tmp_path, content)

        with pytest.raises(HolidayCalendarError):
            load_kospi_holidays(path)

    def test_holidays_중복_원소_HolidayCalendarError(self, tmp_path: Path) -> None:
        """holidays 에 중복 날짜가 있으면 → HolidayCalendarError."""
        _, _, HolidayCalendarError, _, load_kospi_holidays = _import_calendar()

        content = _make_valid_yaml_content(holidays=["2025-01-01", "2025-01-01"])
        path = _write_yaml(tmp_path, content)

        with pytest.raises(HolidayCalendarError):
            load_kospi_holidays(path)


# ===========================================================================
# TestYamlBusinessDayCalendar — Calendar 동작 검증
# ===========================================================================


class TestYamlBusinessDayCalendar:
    """YamlBusinessDayCalendar.is_business_day 동작 검증."""

    def _make_loader(self, tmp_path: Path, holidays: list[str] | None = None):
        """테스트용 YamlBusinessDayCalendar 인스턴스 생성 헬퍼."""
        _, _, _, YamlBusinessDayCalendar, _ = _import_calendar()

        if holidays is None:
            holidays = ["2026-01-01", "2025-05-05"]

        path = _write_yaml(tmp_path, _make_valid_yaml_content(holidays=holidays))
        return YamlBusinessDayCalendar(path=path)

    def test_평일_비공휴일_영업일(self, tmp_path: Path) -> None:
        """평일(월~금)이고 공휴일 목록에 없으면 is_business_day == True."""
        cal = self._make_loader(tmp_path)

        # 2026-04-22 = 수요일, 공휴일 아님
        assert cal.is_business_day(date(2026, 4, 22)) is True

    def test_토요일_비영업일(self, tmp_path: Path) -> None:
        """토요일 → is_business_day == False."""
        cal = self._make_loader(tmp_path)

        # 2026-04-18 = 토요일
        assert cal.is_business_day(date(2026, 4, 18)) is False

    def test_일요일_비영업일(self, tmp_path: Path) -> None:
        """일요일 → is_business_day == False."""
        cal = self._make_loader(tmp_path)

        # 2026-04-19 = 일요일
        assert cal.is_business_day(date(2026, 4, 19)) is False

    def test_공휴일_평일_비영업일(self, tmp_path: Path) -> None:
        """평일이지만 공휴일 → is_business_day == False."""
        cal = self._make_loader(tmp_path, holidays=["2026-01-01"])

        # 2026-01-01 = 목요일 (신정)
        assert cal.is_business_day(date(2026, 1, 1)) is False

    def test_공휴일_주말_겹침_비영업일(self, tmp_path: Path) -> None:
        """주말과 겹치는 공휴일 날짜도 False."""
        # 2026-05-09 = 토요일인데 공휴일로도 등록
        cal = self._make_loader(tmp_path, holidays=["2026-05-09"])

        assert cal.is_business_day(date(2026, 5, 9)) is False

    def test_path_none_기본경로_사용(self, tmp_path: Path, mocker: Any) -> None:
        """path=None 이면 _DEFAULT_HOLIDAYS_PATH 를 사용한다.

        load_kospi_holidays 를 patch 해 실제 파일 시스템 접근 없이 호출 경로 검증.
        """
        _, _, _, YamlBusinessDayCalendar, load_kospi_holidays = _import_calendar()

        # 기본 경로로 호출될 load_kospi_holidays 를 patch
        from stock_agent.data import calendar as cal_module

        fake_calendar = cal_module.HolidayCalendar(
            as_of_date=date(2026, 4, 23),
            source="KRX",
            holidays=frozenset(),
        )
        mock_load = mocker.patch(
            "stock_agent.data.calendar.load_kospi_holidays",
            return_value=fake_calendar,
        )

        cal = YamlBusinessDayCalendar(path=None)
        # is_business_day 를 한 번 호출해 캘린더가 로드되도록 한다
        cal.is_business_day(date(2026, 4, 22))

        # load_kospi_holidays 가 기본 경로(None 또는 _DEFAULT_HOLIDAYS_PATH)로 호출되었는지 확인
        assert mock_load.called

    def test_재호출시_캐시_사용_yaml_한번만_읽음(self, tmp_path: Path, mocker: Any) -> None:
        """is_business_day 를 여러 번 호출해도 YAML 파일은 한 번만 읽는다."""
        _, _, _, YamlBusinessDayCalendar, _ = _import_calendar()

        path = _write_yaml(tmp_path, _make_valid_yaml_content(holidays=["2026-01-01"]))

        # Path.read_text 호출 횟수를 감시
        # autospec=True 로 unbound 메서드의 self 인자를 올바르게 전달한다
        original_read_text = Path.read_text
        read_text_spy = mocker.patch.object(
            Path,
            "read_text",
            autospec=True,
            side_effect=original_read_text,
        )

        cal = YamlBusinessDayCalendar(path=path)
        cal.is_business_day(date(2026, 4, 22))
        cal.is_business_day(date(2026, 4, 23))
        cal.is_business_day(date(2026, 1, 1))

        # yaml 로드는 한 번만 (read_text 호출이 정확히 1번)
        msg = f"YAML 파일은 한 번만 읽어야 한다. 실제 호출 수: {read_text_spy.call_count}"
        assert read_text_spy.call_count == 1, msg

    def test_calendar_프로퍼티_반환(self, tmp_path: Path) -> None:
        """calendar 프로퍼티가 HolidayCalendar 인스턴스를 반환한다."""
        _, HolidayCalendar, _, YamlBusinessDayCalendar, _ = _import_calendar()

        path = _write_yaml(tmp_path, _make_valid_yaml_content())
        cal = YamlBusinessDayCalendar(path=path)

        result = cal.calendar

        assert isinstance(result, HolidayCalendar)
