"""KisMinuteBarLoader 단위 테스트 (RED 명세).

KIS 과거 분봉 API 어댑터(`src/stock_agent/data/kis_minute_bars.py`) 의 공개 계약을
검증한다. 실 KIS 네트워크·pykis import·외부 파일 I/O 는 절대 발생시키지 않는다.

- `kis.fetch()` 는 `pykis_factory=MagicMock(return_value=fake_kis)` 로 완전 대체.
- `sleep` / `clock` 는 생성자 주입으로 결정론화.
- SQLite 는 `tmp_path / "test.db"` 또는 `":memory:"` (stdlib, 네트워크 없음).
- `install_order_block_guard` 는 mocker.patch 로 목킹.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import call

import pytest
from pytest_mock import MockerFixture

from stock_agent.config import Settings, reset_settings_cache

# ---------------------------------------------------------------------------
# 상수 / KST 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_VALID_BASE_ENV: dict[str, str] = {
    "KIS_HTS_ID": "test-user",
    "KIS_APP_KEY": "T" * 36,
    "KIS_APP_SECRET": "S" * 180,
    "KIS_ACCOUNT_NO": "12345678-01",
    "TELEGRAM_BOT_TOKEN": "dummy-tg-token",
    "TELEGRAM_CHAT_ID": "9999",
    "KIS_ENV": "paper",
    "KIS_KEY_ORIGIN": "paper",
}

_LIVE_KEY_ENV: dict[str, str] = {
    "KIS_LIVE_APP_KEY": "X" * 36,
    "KIS_LIVE_APP_SECRET": "Y" * 180,
    "KIS_LIVE_ACCOUNT_NO": "12345678-01",
}

_SYMBOL = "005930"
_SYMBOL2 = "000660"

# 고정 테스트 날짜
_TODAY = date(2026, 4, 22)  # 화요일
_YESTERDAY = date(2026, 4, 21)  # 월요일


def _kst(d: date, hour: int, minute: int, second: int = 0) -> datetime:
    """지정 날짜+시각의 KST aware datetime 반환."""
    return datetime(d.year, d.month, d.day, hour, minute, second, tzinfo=KST)


def _fixed_clock(dt: datetime):
    """datetime 을 반환하는 단순 clock 팩토리."""
    return lambda: dt


def _make_output2_row(
    d: date,
    hour: int,
    minute: int,
    second: int = 0,
    oprc: str = "71000",
    hgpr: str = "71500",
    lwpr: str = "70800",
    prpr: str = "71200",
    vol: str = "1234",
) -> dict:
    """KIS API output2 응답 행 더미 생성 헬퍼."""
    return {
        "stck_bsop_date": d.strftime("%Y%m%d"),
        "stck_cntg_hour": f"{hour:02d}{minute:02d}{second:02d}",
        "stck_oprc": oprc,
        "stck_hgpr": hgpr,
        "stck_lwpr": lwpr,
        "stck_prpr": prpr,
        "cntg_vol": vol,
    }


def _make_api_response(output2_rows: list[dict], rt_cd: str = "0", msg_cd: str = "") -> dict:
    """KIS API 응답 dict 더미 생성 헬퍼."""
    return {
        "rt_cd": rt_cd,
        "msg_cd": msg_cd,
        "output1": {},
        "output2": output2_rows,
    }


# ---------------------------------------------------------------------------
# autouse: .env 자동 로드 무력화 + Settings 캐시 리셋
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """매 테스트 시작·종료 시 .env 영향 제거 및 lru_cache 초기화."""
    from stock_agent.config import Settings as _Settings

    monkeypatch.setattr(_Settings, "model_config", {**_Settings.model_config, "env_file": None})
    for k in (*_VALID_BASE_ENV, *_LIVE_KEY_ENV):
        monkeypatch.delenv(k, raising=False)
    reset_settings_cache()
    yield
    reset_settings_cache()


# ---------------------------------------------------------------------------
# Settings 생성 헬퍼
# ---------------------------------------------------------------------------


def _make_settings(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    """유효한 기반 환경변수 위에 overrides 를 올려 Settings 인스턴스를 반환."""
    for k, v in {**_VALID_BASE_ENV, **overrides}.items():
        monkeypatch.setenv(k, v)
    reset_settings_cache()
    return Settings()  # type: ignore[call-arg]


def _make_settings_with_live_keys(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Settings:
    """기반 환경변수에 live 키 3종을 포함한 Settings 인스턴스를 반환."""
    merged = {**_VALID_BASE_ENV, **_LIVE_KEY_ENV, **overrides}
    for k, v in merged.items():
        monkeypatch.setenv(k, v)
    reset_settings_cache()
    return Settings()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 공통 픽스처
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_kis(mocker: MockerFixture):
    """호출 인자를 기록하는 MagicMock PyKis 인스턴스."""
    return mocker.MagicMock()


@pytest.fixture
def pykis_factory(fake_kis, mocker: MockerFixture):
    """fake_kis 를 반환하는 팩토리 MagicMock."""
    return mocker.MagicMock(return_value=fake_kis)


@pytest.fixture
def guard_patch(mocker: MockerFixture):
    """install_order_block_guard 를 목으로 교체."""
    return mocker.patch("stock_agent.data.kis_minute_bars.install_order_block_guard")


@pytest.fixture
def mock_sleep(mocker: MockerFixture):
    """sleep 을 no-op MagicMock 으로 교체."""
    return mocker.MagicMock()


# ---------------------------------------------------------------------------
# import 가드 — 파일이 없으면 모든 테스트가 ImportError 로 FAIL (RED 상태)
# ---------------------------------------------------------------------------


def _import_loader():
    """KisMinuteBarLoader 를 지연 import 해 반환. 모듈 없으면 ImportError."""
    from stock_agent.data.kis_minute_bars import KisMinuteBarLoader, KisMinuteBarLoadError

    return KisMinuteBarLoader, KisMinuteBarLoadError


# ===========================================================================
# TestConstructor — 생성자 정상/오류 케이스
# ===========================================================================


class TestConstructor:
    def test_정상_생성_성공(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """live 키 + 유효한 파라미터로 생성 → 예외 없음."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)
        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        loader.close()

    def test_throttle_s_음수_RuntimeError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """throttle_s < 0 → RuntimeError."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)
        with pytest.raises(RuntimeError, match="throttle"):
            KisMinuteBarLoader(
                settings,
                pykis_factory=pykis_factory,
                clock=_fixed_clock(_kst(_TODAY, 10, 0)),
                cache_db_path=tmp_path / "test.db",
                sleep=mock_sleep,
                throttle_s=-0.1,
            )

    def test_rate_limit_max_retries_0이하_RuntimeError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """rate_limit_max_retries < 1 → RuntimeError."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)
        with pytest.raises(RuntimeError, match="rate_limit_max_retries"):
            KisMinuteBarLoader(
                settings,
                pykis_factory=pykis_factory,
                clock=_fixed_clock(_kst(_TODAY, 10, 0)),
                cache_db_path=tmp_path / "test.db",
                sleep=mock_sleep,
                rate_limit_max_retries=0,
            )

    def test_cache_db_path_디렉토리_자동_생성(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """cache_db_path 의 부모 디렉토리가 없으면 자동 생성."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)
        nested = tmp_path / "deep" / "nested" / "test.db"
        assert not nested.parent.exists()
        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=nested,
            sleep=mock_sleep,
        )
        loader.close()
        assert nested.parent.exists()


# ===========================================================================
# TestLiveKeyRequired — has_live_keys=False → 생성자 fail-fast
# ===========================================================================


class TestLiveKeyRequired:
    def test_live_키_없으면_생성자에서_KisMinuteBarLoadError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """has_live_keys=False 인 Settings 주입 시 생성자에서 KisMinuteBarLoadError fail-fast."""
        KisMinuteBarLoader, KisMinuteBarLoadError = _import_loader()
        settings = _make_settings(monkeypatch)
        assert settings.has_live_keys is False

        with pytest.raises(KisMinuteBarLoadError, match="KIS_LIVE"):
            KisMinuteBarLoader(
                settings,
                pykis_factory=pykis_factory,
                clock=_fixed_clock(_kst(_TODAY, 10, 0)),
                cache_db_path=tmp_path / "test.db",
                sleep=mock_sleep,
            )


# ===========================================================================
# TestSymbolValidation — stream 에 잘못된 심볼
# ===========================================================================


class TestSymbolValidation:
    @pytest.mark.parametrize(
        "symbols",
        [
            (),
        ],
        ids=["빈_tuple"],
    )
    def test_빈_symbols_RuntimeError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        symbols: tuple,
    ) -> None:
        """symbols 가 빈 튜플 → RuntimeError."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)
        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        with pytest.raises(RuntimeError):
            list(loader.stream(_YESTERDAY, _YESTERDAY, symbols))
        loader.close()

    @pytest.mark.parametrize(
        "bad_symbol",
        ["12345", "abc123", "0059301", "ABCDEF", ""],
        ids=["5자리", "영소문자혼합", "7자리", "영대문자", "빈문자열"],
    )
    def test_6자리_아닌_심볼_RuntimeError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        bad_symbol: str,
    ) -> None:
        """6자리 숫자가 아닌 symbol → RuntimeError."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)
        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        with pytest.raises(RuntimeError):
            list(loader.stream(_YESTERDAY, _YESTERDAY, (bad_symbol,)))
        loader.close()


# ===========================================================================
# TestDateRangeValidation — start > end
# ===========================================================================


class TestDateRangeValidation:
    def test_start_이후_end_RuntimeError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """start > end → RuntimeError."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)
        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        with pytest.raises(RuntimeError):
            list(loader.stream(_TODAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()


# ===========================================================================
# TestSingleSymbolSinglePage — 1종목 1페이지(< 120건)
# ===========================================================================


class TestSingleSymbolSinglePage:
    def test_30건_응답_MinuteBar_30개_yield(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """fake_kis.fetch 1회 호출 → 30건 MinuteBar yield."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        rows = [_make_output2_row(_YESTERDAY, 15, 30 - i) for i in range(30)]
        fake_kis.fetch.return_value = _make_api_response(rows)

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        assert len(bars) == 30
        loader.close()

    def test_첫_호출_params_검증(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """첫 호출 시 FID_INPUT_HOUR_1='153000', FID_INPUT_DATE_1='20260421' 로 호출됨."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        fake_kis.fetch.return_value = _make_api_response(
            [
                _make_output2_row(_YESTERDAY, 9, 31),
            ]
        )

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))

        fake_kis.fetch.assert_called()
        call_kwargs = fake_kis.fetch.call_args.kwargs
        params = call_kwargs.get("params", {})
        assert params.get("FID_INPUT_HOUR_1") == "153000"
        assert params.get("FID_INPUT_DATE_1") == "20260421"
        assert params.get("FID_INPUT_ISCD") == _SYMBOL
        loader.close()


# ===========================================================================
# TestSingleSymbolPagination — 1종목 다중 페이지
# ===========================================================================


class TestSingleSymbolPagination:
    def test_120건_응답_후_90건_응답_총210건(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """첫 응답 120건 → 두 번째 응답 90건 → 총 210건 yield."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        # 첫 페이지: 120건 (15:30 ~ 13:31, 역방향 커서)
        # total_min: 930(15:30) → 811(13:31), 모두 유효 시각
        first_rows = []
        for i in range(120):
            total_min = 15 * 60 + 30 - i  # 930, 929, ..., 811
            h, m = divmod(total_min, 60)
            first_rows.append(_make_output2_row(_YESTERDAY, h, m))

        # 두 번째 페이지: 90건 (13:30 ~ 12:01, 역방향 커서)
        # total_min: 810(13:30) → 721(12:01), 모두 유효 시각
        # len=90 < 120 이므로 페이지네이션 종료. 총 210건.
        second_rows = []
        for i in range(90):
            total_min = 13 * 60 + 30 - i  # 810, 809, ..., 721
            h, m = divmod(total_min, 60)
            second_rows.append(_make_output2_row(_YESTERDAY, h, m))

        fake_kis.fetch.side_effect = [
            _make_api_response(first_rows),
            _make_api_response(second_rows),
        ]

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        assert len(bars) == 210
        loader.close()

    def test_페이지네이션_시간_단조증가(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """다중 페이지 응답의 최종 yield 순서는 bar_time 단조증가."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        # 역방향 커서: 15:00~14:01 (첫 페이지), 14:00~13:01 (두 번째 페이지)
        first_rows = [_make_output2_row(_YESTERDAY, 15, 60 - i) for i in range(1, 61)]
        second_rows = [_make_output2_row(_YESTERDAY, 14, 60 - i) for i in range(1, 61)]

        fake_kis.fetch.side_effect = [
            _make_api_response(first_rows),
            _make_api_response(second_rows),
        ]

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        bar_times = [b.bar_time for b in bars]
        assert bar_times == sorted(bar_times), "bar_time 이 단조증가해야 한다"
        loader.close()


# ===========================================================================
# TestPaginationTerminatesBelow120 — 응답 < 120건이면 다음 페이지 없음
# ===========================================================================


class TestPaginationTerminatesBelow120:
    def test_119건_응답_다음_페이지_없음(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """응답 행이 119건이면 페이지네이션 종료 — fetch 1회만 호출."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        # 119건, 모두 유효 시각: 15:30 ~ 13:32 (total_min 930→812)
        rows = []
        for i in range(119):
            total_min = 15 * 60 + 30 - i  # 930, 929, ..., 812
            h, m = divmod(total_min, 60)
            rows.append(_make_output2_row(_YESTERDAY, h, m))
        fake_kis.fetch.return_value = _make_api_response(rows)

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        assert len(bars) == 119
        assert fake_kis.fetch.call_count == 1
        loader.close()


# ===========================================================================
# TestPaginationTerminatesAt0900 — min_time <= "090000" 이면 종료
# ===========================================================================


class TestPaginationTerminatesAt0900:
    def test_min_time_090000_이하_추가호출_없음(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """응답 중 가장 이른 시각이 '090000' 이면 추가 페이지 호출 없음."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        # 120건 중 마지막(가장 이른) 시각이 09:00:00
        rows = []
        for i in range(119):
            rows.append(_make_output2_row(_YESTERDAY, 9, 59 - i))
        rows.append(_make_output2_row(_YESTERDAY, 9, 0, 0))  # 09:00:00

        fake_kis.fetch.return_value = _make_api_response(rows)

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        assert fake_kis.fetch.call_count == 1
        loader.close()


# ===========================================================================
# TestMultiDateLoop — start != end 다중 날짜
# ===========================================================================


class TestMultiDateLoop:
    def test_다중날짜_날짜별_fetch_호출(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """start=2026-04-20, end=2026-04-21 → 날짜별로 fetch 호출됨."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        d1 = date(2026, 4, 20)  # 월요일
        d2 = date(2026, 4, 21)  # 화요일

        rows_d1 = [_make_output2_row(d1, 9, 31)]
        rows_d2 = [_make_output2_row(d2, 9, 31)]

        fake_kis.fetch.side_effect = [
            _make_api_response(rows_d2),  # end 먼저 역방향
            _make_api_response(rows_d1),
        ]

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        bars = list(loader.stream(d1, d2, (_SYMBOL,)))
        assert len(bars) == 2
        loader.close()

    def test_주말_빈응답_skip_후_다음_평일_호출(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """토/일(빈 output2) 은 skip → 월요일 데이터만 yield."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        sat = date(2026, 4, 18)  # 토요일 — 빈 응답 기대
        # sun = date(2026, 4, 19) — 일요일도 빈 응답이지만 변수 불필요
        mon = date(2026, 4, 20)  # 월요일

        # 구현은 end=mon 부터 역방향 순회: mon → sun → sat
        # mon: 실데이터, sun: 빈(일요일), sat: 빈(토요일)
        fake_kis.fetch.side_effect = [
            _make_api_response([_make_output2_row(mon, 9, 31)]),  # mon: 실데이터
            _make_api_response([]),  # sun: 빈(일요일)
            _make_api_response([]),  # sat: 빈(토요일)
        ]

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        # 인자 순서: start=sat, end=mon (sat <= mon, 유효)
        bars = list(loader.stream(sat, mon, (_SYMBOL,)))
        # mon 에서만 1건 — 토/일은 빈 응답이므로 yield 없음
        assert len(bars) == 1
        assert bars[0].bar_time.date() == mon
        loader.close()


# ===========================================================================
# TestMultiSymbolHeapqMerge — 다중 심볼 heapq 병합
# ===========================================================================


class TestMultiSymbolHeapqMerge:
    def test_두_심볼_bar_time_symbol_순_병합(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """005930 과 000660 이 같은 분(09:31)에 있으면 000660 먼저 (lexical)."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        # 두 심볼 모두 동일 분봉 1건씩
        def _make_response(symbol: str, d: date) -> dict:
            rows = [_make_output2_row(d, 9, 31)]
            return _make_api_response(rows)

        # 심볼 호출 순서에 따라 응답을 제공
        responses_by_symbol: dict[str, dict] = {
            _SYMBOL: _make_response(_SYMBOL, _YESTERDAY),
            _SYMBOL2: _make_response(_SYMBOL2, _YESTERDAY),
        }

        def _side_effect(*args, **kwargs):
            params = kwargs.get("params", {})
            sym = params.get("FID_INPUT_ISCD", "")
            return responses_by_symbol.get(sym, _make_api_response([]))

        fake_kis.fetch.side_effect = _side_effect

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL, _SYMBOL2)))

        assert len(bars) == 2
        # 같은 bar_time 이면 symbol lexical 순: "000660" < "005930"
        assert bars[0].symbol == _SYMBOL2
        assert bars[1].symbol == _SYMBOL
        loader.close()

    def test_다중_심볼_단조증가(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """다중 심볼 병합 결과가 (bar_time, symbol) 단조증가."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        def _make_multi_response(sym: str, d: date) -> dict:
            rows = [_make_output2_row(d, 9, m) for m in range(31, 34)]
            return _make_api_response(rows)

        def _side_effect(*args, **kwargs):
            params = kwargs.get("params", {})
            sym = params.get("FID_INPUT_ISCD", "")
            return _make_multi_response(sym, _YESTERDAY)

        fake_kis.fetch.side_effect = _side_effect

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL, _SYMBOL2)))

        keys = [(b.bar_time, b.symbol) for b in bars]
        assert keys == sorted(keys), "병합 결과가 (bar_time, symbol) 단조증가해야 한다"
        loader.close()


# ===========================================================================
# TestRateLimitRetrySuccess — EGW00201 재시도 성공
# ===========================================================================


class TestRateLimitRetrySuccess:
    def test_rate_limit_1회_후_성공_sleep_호출됨(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """첫 응답 EGW00201 → sleep(61.0) 호출 → 재시도 → 성공. yield 정상."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        rate_limit_resp = _make_api_response([], rt_cd="1", msg_cd="EGW00201")
        success_resp = _make_api_response([_make_output2_row(_YESTERDAY, 9, 31)])
        fake_kis.fetch.side_effect = [rate_limit_resp, success_resp]

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
            rate_limit_wait_s=61.0,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))

        # sleep(61.0) 이 1회 호출되었어야 함
        mock_sleep.assert_called_with(61.0)
        assert len(bars) == 1
        loader.close()


# ===========================================================================
# TestRateLimitRetryExhausted — EGW00201 재시도 한도 초과
# ===========================================================================


class TestRateLimitRetryExhausted:
    def test_rate_limit_3회_연속_KisMinuteBarLoadError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """3회 연속 EGW00201 → 4회째 없음, KisMinuteBarLoadError. sleep 3회 호출."""
        KisMinuteBarLoader, KisMinuteBarLoadError = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        rate_limit_resp = _make_api_response([], rt_cd="1", msg_cd="EGW00201")
        fake_kis.fetch.return_value = rate_limit_resp

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
            rate_limit_wait_s=61.0,
            rate_limit_max_retries=3,
        )
        with pytest.raises(KisMinuteBarLoadError, match="rate limit"):
            list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))

        assert mock_sleep.call_count == 3
        # 4회째 호출은 없어야 함 (3회 재시도 = 총 4회 fetch, 마지막이 실패)
        assert fake_kis.fetch.call_count == 4
        loader.close()


# ===========================================================================
# TestResponseErrorCode — EGW00201 이 아닌 에러 코드
# ===========================================================================


class TestResponseErrorCode:
    def test_rt_cd_1_OTHER_KisMinuteBarLoadError_재시도_없음(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """rt_cd='1', msg_cd='OTHER' → KisMinuteBarLoadError, 재시도 없음."""
        KisMinuteBarLoader, KisMinuteBarLoadError = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        error_resp = _make_api_response([], rt_cd="1", msg_cd="OTHER_ERROR")
        fake_kis.fetch.return_value = error_resp

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        with pytest.raises(KisMinuteBarLoadError):
            list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))

        # 재시도 없음 — fetch 1회
        assert fake_kis.fetch.call_count == 1
        # sleep 호출 없음
        mock_sleep.assert_not_called()
        loader.close()


# ===========================================================================
# TestResponseParsing — 응답 행 파싱 정확도
# ===========================================================================


class TestResponseParsing:
    def test_OHLC_Decimal_파싱_volume_int_bar_time_KST_aware(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """OHLC Decimal, volume int, bar_time KST aware, 분 경계 검증."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        row = _make_output2_row(
            _YESTERDAY,
            9,
            31,
            0,
            oprc="71000",
            hgpr="71500",
            lwpr="70800",
            prpr="71200",
            vol="1234",
        )
        fake_kis.fetch.return_value = _make_api_response([row])

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))

        assert len(bars) == 1
        bar = bars[0]
        # Decimal 파싱
        assert bar.open == Decimal("71000")
        assert bar.high == Decimal("71500")
        assert bar.low == Decimal("70800")
        assert bar.close == Decimal("71200")
        # volume int
        assert isinstance(bar.volume, int)
        assert bar.volume == 1234
        # bar_time KST aware
        assert bar.bar_time.tzinfo is not None
        assert bar.bar_time.utcoffset() == timedelta(hours=9)
        # 분 경계
        assert bar.bar_time.second == 0
        assert bar.bar_time.microsecond == 0
        loader.close()


# ===========================================================================
# TestCacheHitSkipsAPI — DB hit → API 미호출
# ===========================================================================


class TestCacheHitSkipsAPI:
    def test_캐시_hit_시_fetch_미호출(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """DB에 해당 (symbol, 날짜) bar가 이미 있으면 fetch 호출 0회."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        db_path = tmp_path / "test.db"
        # 미리 DB에 bar 삽입
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS minute_bars (
                symbol TEXT,
                bar_time TEXT,
                open TEXT,
                high TEXT,
                low TEXT,
                close TEXT,
                volume INTEGER,
                PRIMARY KEY (symbol, bar_time)
            );
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO schema_version VALUES (1);
        """)
        bar_time_str = f"{_YESTERDAY.isoformat()}T09:31:00+09:00"
        conn.execute(
            "INSERT OR REPLACE INTO minute_bars VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_SYMBOL, bar_time_str, "71000", "71500", "70800", "71200", 1234),
        )
        conn.commit()
        conn.close()

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=db_path,
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))

        # fetch 호출 없음
        fake_kis.fetch.assert_not_called()
        # DB에서 읽은 bar 1건
        assert len(bars) >= 1
        loader.close()


# ===========================================================================
# TestCacheMissCallsAPIAndWrites — DB miss → API 호출 → DB 저장
# ===========================================================================


class TestCacheMissCallsAPIAndWrites:
    def test_캐시_miss_API_호출_후_DB_저장됨(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """DB 비어있음 → API 호출 → bar DB 저장 → stream yield."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        db_path = tmp_path / "test.db"
        row = _make_output2_row(_YESTERDAY, 9, 31)
        fake_kis.fetch.return_value = _make_api_response([row])

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=db_path,
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()

        assert len(bars) == 1
        fake_kis.fetch.assert_called()

        # DB에 저장 확인
        conn = sqlite3.connect(str(db_path))
        count = conn.execute(
            "SELECT COUNT(*) FROM minute_bars WHERE symbol=?", (_SYMBOL,)
        ).fetchone()[0]
        conn.close()
        assert count == 1


# ===========================================================================
# TestCacheTodayAlwaysRefetches — 오늘 날짜는 캐시 무시하고 재조회
# ===========================================================================


class TestCacheTodayAlwaysRefetches:
    def test_오늘_날짜는_캐시_있어도_API_재호출(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """clock='오늘', end=오늘 → DB에 bar가 있어도 fetch 호출됨."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        db_path = tmp_path / "test.db"
        # 미리 오늘 날짜 bar 삽입
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS minute_bars (
                symbol TEXT,
                bar_time TEXT,
                open TEXT,
                high TEXT,
                low TEXT,
                close TEXT,
                volume INTEGER,
                PRIMARY KEY (symbol, bar_time)
            );
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO schema_version VALUES (1);
        """)
        bar_time_str = f"{_TODAY.isoformat()}T09:31:00+09:00"
        conn.execute(
            "INSERT OR REPLACE INTO minute_bars VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_SYMBOL, bar_time_str, "71000", "71500", "70800", "71200", 1234),
        )
        conn.commit()
        conn.close()

        fake_kis.fetch.return_value = _make_api_response(
            [
                _make_output2_row(_TODAY, 9, 31),
            ]
        )

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            # clock 을 "오늘" 로 고정
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=db_path,
            sleep=mock_sleep,
        )
        list(loader.stream(_TODAY, _TODAY, (_SYMBOL,)))

        # 오늘 날짜이므로 캐시 있어도 API 재호출
        fake_kis.fetch.assert_called()
        loader.close()

    def test_오늘_날짜_DB선삽입값이_아닌_API기반값_반환(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """오늘 자 bar 는 DB 에 무엇이 있든 API 응답값을 반환 — DB 읽기 skip 증명.

        H1: is_today 분기에서 DB 읽기 경로(else 분기)로 빠지면 DB 선삽입값(71200)이
        반환되어 이 테스트가 실패한다. API 기반값(70100)이 반환돼야 GREEN.
        """
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        db_path = tmp_path / "test.db"
        # DB 에 오늘 자 bar 선삽입 — close=71200 (구분 가능한 "stale" 값)
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS minute_bars (
                symbol TEXT,
                bar_time TEXT,
                open TEXT,
                high TEXT,
                low TEXT,
                close TEXT,
                volume INTEGER,
                PRIMARY KEY (symbol, bar_time)
            );
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO schema_version VALUES (1);
        """)
        bar_time_str = f"{_TODAY.isoformat()}T09:31:00+09:00"
        conn.execute(
            "INSERT OR REPLACE INTO minute_bars VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_SYMBOL, bar_time_str, "71000", "71500", "70800", "71200", 1234),
        )
        conn.commit()
        conn.close()

        # API 응답은 DB 와 다른 값 — close=70100 (기본 _make_output2_row 기본값과 다르게 설정)
        fake_kis.fetch.return_value = _make_api_response(
            [
                _make_output2_row(
                    _TODAY,
                    9,
                    31,
                    oprc="70000",
                    hgpr="70500",
                    lwpr="69800",
                    prpr="70100",
                    vol="999",
                ),
            ]
        )

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=db_path,
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_TODAY, _TODAY, (_SYMBOL,)))
        loader.close()

        assert len(bars) >= 1
        # DB 선삽입값(71200)이 아니라 API 응답값(70100)이어야 함 — DB 읽기 skip 증명
        assert bars[0].close == Decimal("70100")


# ===========================================================================
# TestSchemaV1Init — 스키마 초기화 검증
# ===========================================================================


class TestSchemaV1Init:
    def test_생성자_minute_bars_schema_version_테이블_생성(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """생성자 호출 후 minute_bars, schema_version 테이블이 존재하고 version=1."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        db_path = tmp_path / "test.db"
        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=db_path,
            sleep=mock_sleep,
        )
        loader.close()

        conn = sqlite3.connect(str(db_path))
        # minute_bars 테이블 컬럼 확인
        cols = {row[1] for row in conn.execute("PRAGMA table_info(minute_bars)")}
        assert {"symbol", "bar_time", "open", "high", "low", "close", "volume"}.issubset(cols)

        # schema_version == 1
        version = conn.execute("SELECT version FROM schema_version").fetchone()[0]
        assert version == 1
        conn.close()


# ===========================================================================
# TestOrderGuardInstalled — install_order_block_guard 호출 검증
# ===========================================================================


class TestOrderGuardInstalled:
    def test_guard_생성자_또는_첫_stream_시_1회_호출(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """install_order_block_guard(fake_kis) 가 정확히 1회 호출됨.

        Note: 생성자 시점 또는 첫 stream 시점 중 하나로 구현 통일.
              어느 쪽이든 stream 완료 후 총 호출 횟수는 1회여야 한다.
        """
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        fake_kis.fetch.return_value = _make_api_response(
            [
                _make_output2_row(_YESTERDAY, 9, 31),
            ]
        )

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))

        guard_patch.assert_called_once_with(fake_kis)
        loader.close()


# ===========================================================================
# TestStreamReentrant — stream 2회 호출 재진입 안전
# ===========================================================================


class TestStreamReentrant:
    def test_stream_2회_호출_각각_완전_소비_가능(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """같은 (start, end, symbols) 로 stream 2회 호출 → 각각 완전 소비 가능."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        row = _make_output2_row(_YESTERDAY, 9, 31)
        # 첫 번째 stream: API 호출, 두 번째 stream: 캐시 hit
        fake_kis.fetch.return_value = _make_api_response([row])

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        bars1 = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        fetch_count_after_first = fake_kis.fetch.call_count

        bars2 = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))

        assert len(bars1) == 1
        assert len(bars2) == 1
        # 두 번째 호출은 캐시 hit → fetch 추가 호출 없음
        assert fake_kis.fetch.call_count == fetch_count_after_first
        loader.close()


# ===========================================================================
# TestErrorWrapping — 비-RuntimeError 예외는 KisMinuteBarLoadError 로 래핑
# ===========================================================================


class TestErrorWrapping:
    def test_ConnectionError_KisMinuteBarLoadError_래핑_cause_보존(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """fetch 에서 ConnectionError → KisMinuteBarLoadError 래핑, __cause__ 보존."""
        KisMinuteBarLoader, KisMinuteBarLoadError = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        original_error = ConnectionError("network down")
        fake_kis.fetch.side_effect = original_error

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        with pytest.raises(KisMinuteBarLoadError) as exc_info:
            list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))

        assert exc_info.value.__cause__ is original_error
        loader.close()


# ===========================================================================
# TestMalformedPageWarning — output2 rows 있지만 전원 parse 실패 시 logger.error 경보
# ===========================================================================


class TestMalformedPageWarning:
    """M2: _fetch_day 내에서 rows ≥1 이지만 page_bars 가 빈 경우 logger.error 방출 검증.

    dedupe 규칙: 동일 (symbol, day) 조합의 _fetch_day 한 번당 최초 1회만 방출.
    """

    @pytest.fixture
    def _loguru_errors(self):
        """loguru ERROR 레벨 메시지 캡처 — 프로젝트 관례(test_rate_limiter.py 패턴) 재사용."""
        from loguru import logger as _logger

        captured: list[dict] = []

        def _sink(message) -> None:  # type: ignore[no-untyped-def]
            record = message.record
            captured.append({"level": record["level"].name, "message": record["message"]})

        handler_id = _logger.add(_sink, level="ERROR", format="{message}")
        try:
            yield captured
        finally:
            _logger.remove(handler_id)

    def test_rows_전원_malformed_logger_error_1회_심볼_날짜_포함(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        _loguru_errors,
    ) -> None:
        """rows 3건 모두 stck_oprc='' malformed → page_bars 비고 rows ≥1 → logger.error 1회.

        에러 메시지에 symbol 과 date(날짜) 정보가 포함되어야 한다.
        """
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        # stck_oprc="" → _parse_decimal 이 ValueError → _parse_row 가 None 반환
        malformed_rows = [_make_output2_row(_YESTERDAY, 9, 31 + i, oprc="") for i in range(3)]
        fake_kis.fetch.return_value = _make_api_response(malformed_rows)

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()

        errors = [m for m in _loguru_errors if m["level"] == "ERROR"]
        assert len(errors) == 1
        # 에러 메시지에 symbol 과 날짜 정보가 포함돼야 한다
        msg = errors[0]["message"]
        assert _SYMBOL in msg
        assert _YESTERDAY.strftime("%Y%m%d") in msg or str(_YESTERDAY) in msg

    def test_rows_0건_정상_공휴일_logger_error_없음(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        _loguru_errors,
    ) -> None:
        """rows 0건(정상 주말·공휴일 경계) → logger.error 미방출 — 빈 응답과 구별."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        # 빈 output2 — 주말/공휴일 정상 케이스
        fake_kis.fetch.return_value = _make_api_response([])

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()

        errors = [m for m in _loguru_errors if m["level"] == "ERROR"]
        assert len(errors) == 0

    def test_2페이지_모두_malformed_logger_error_1회만_dedupe(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        _loguru_errors,
    ) -> None:
        """2페이지 모두 rows ≥1 + 전원 malformed → 같은 (symbol, day) 쌍 → logger.error 1회만.

        dedupe: _fetch_day 한 번당 동일 (symbol, day) 조합은 최초 페이지 1회만 방출.
        첫 응답 120건(전원 malformed), 두 번째 응답 3건(전원 malformed).
        """
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        # 첫 페이지: 120건 전원 malformed (페이지네이션이 다음 페이지로 계속 진행하게 len=120)
        first_malformed = [_make_output2_row(_YESTERDAY, 9, 31, oprc="") for _ in range(120)]
        # 두 번째 페이지: 3건 전원 malformed (len < 120 → 종료)
        second_malformed = [_make_output2_row(_YESTERDAY, 9, 28 + i, oprc="") for i in range(3)]
        fake_kis.fetch.side_effect = [
            _make_api_response(first_malformed),
            _make_api_response(second_malformed),
        ]

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()

        errors = [m for m in _loguru_errors if m["level"] == "ERROR"]
        assert len(errors) == 1


# ===========================================================================
# TestThrottleBetweenPages — throttle_s 페이지 간 sleep
# ===========================================================================


def _build_page(start_hhmm: tuple[int, int], count: int) -> list[dict]:
    """(start_h, start_m) 부터 1분씩 역방향으로 count 개 행을 만든다.

    모든 시각이 유니크함을 보장 (seen dedupe 로 page_bars 가 비어버리는 문제 방지).
    """
    sh, sm = start_hhmm
    total = sh * 60 + sm
    rows = []
    for i in range(count):
        t = total - i
        h, m = divmod(t, 60)
        rows.append(_make_output2_row(_YESTERDAY, h, m))
    return rows


class TestThrottleBetweenPages:
    def test_throttle_0_5_페이지_3개_sleep_2회(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """throttle_s=0.5, 페이지 3개 → sleep(0.5) 2회 호출 (마지막 페이지 뒤는 제외).

        픽스처 견고화 (Issue #58):
        - page1 = 15:29 → 13:30 (120분, 120건) — 시각 전부 유니크
        - page2 = 13:29 → 11:30 (120분, 120건) — 시각 전부 유니크
        - page3 = 11:29 → 10:40 (50분, 50건)
        seen dedupe 에 걸리는 행이 0건이어야 page_bars 가 항상 페이지 크기 그대로 유지됨.
        """
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        # 각 페이지의 모든 시각이 유니크 — seen dedupe 충돌 없음
        page1 = _build_page((15, 29), 120)  # 15:29 → 13:30
        page2 = _build_page((13, 29), 120)  # 13:29 → 11:30
        page3 = _build_page((11, 29), 50)  # 11:29 → 10:40

        fake_kis.fetch.side_effect = [
            _make_api_response(page1),
            _make_api_response(page2),
            _make_api_response(page3),
        ]

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
            throttle_s=0.5,
        )
        list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))

        # sleep(0.5) 이 2회 (페이지 1→2 사이, 2→3 사이)
        throttle_calls = [c for c in mock_sleep.call_args_list if c == call(0.5)]
        assert len(throttle_calls) == 2
        loader.close()


# ===========================================================================
# TestCloseIsIdempotent — close 멱등성
# ===========================================================================


class TestCloseIsIdempotent:
    def test_close_2회_호출_예외없음(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """close() 2회 호출 안전."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        loader.close()
        loader.close()  # 예외 없음

    def test_컨텍스트_매니저_exit_시_close_호출(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """with 블록 종료 시 close() 가 호출된다."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        with KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        ) as loader:
            assert loader is not None
        # with 블록 종료 후 — 이중 close 예외 없음 확인
        loader.close()


# ===========================================================================
# TestOutOfRangeBarFiltered — start 이전 날짜 bar skip
# ===========================================================================


class TestOutOfRangeBarFiltered:
    def test_start_이전_날짜_bar_stream_yield_안됨(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """API 응답에 start 이전 날짜 bar 가 섞여도 stream 에 yield 안됨."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        earlier_date = date(2026, 4, 20)  # start 이전
        rows = [
            _make_output2_row(earlier_date, 9, 31),  # 범위 밖
            _make_output2_row(_YESTERDAY, 9, 31),  # 범위 내
        ]
        fake_kis.fetch.return_value = _make_api_response(rows)

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        # start=_YESTERDAY, end=_YESTERDAY
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))

        # earlier_date bar 는 yield 되면 안 됨
        assert all(b.bar_time.date() == _YESTERDAY for b in bars)
        loader.close()


# ===========================================================================
# TestInvalidRowSkipped — malformed 행 skip
# ===========================================================================


class TestInvalidRowSkipped:
    def test_빈_oprc_행_skip_나머지_정상_yield(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """output2 의 한 행이 malformed (stck_oprc='') → 해당 행 skip, 나머지 yield."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        bad_row = _make_output2_row(_YESTERDAY, 9, 31, oprc="")  # malformed
        good_row = _make_output2_row(_YESTERDAY, 9, 32)  # 정상
        fake_kis.fetch.return_value = _make_api_response([bad_row, good_row])

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))

        # bad_row skip, good_row yield
        assert len(bars) == 1
        assert bars[0].bar_time.minute == 32
        loader.close()

    def test_잘못된_시각_행_skip_나머지_정상_yield(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """stck_cntg_hour='abc' 인 행 → skip, 나머지 정상 yield."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        bad_row = {
            "stck_bsop_date": _YESTERDAY.strftime("%Y%m%d"),
            "stck_cntg_hour": "abc",  # 파싱 불가
            "stck_oprc": "71000",
            "stck_hgpr": "71500",
            "stck_lwpr": "70800",
            "stck_prpr": "71200",
            "cntg_vol": "1234",
        }
        good_row = _make_output2_row(_YESTERDAY, 9, 32)
        fake_kis.fetch.return_value = _make_api_response([bad_row, good_row])

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))

        assert len(bars) == 1
        assert bars[0].bar_time.minute == 32
        loader.close()


# ===========================================================================
# TestResponseNormalization — KisDynamicDict __data__ 경로 정규화 (갭 C2)
# ===========================================================================


class _FakeKisDynamicDict:
    """python-kis KisDynamicDict 를 흉내내는 stub — `__data__` 에 raw dict 보관."""

    def __init__(self, data: dict) -> None:
        self.__data__ = data


class TestResponseNormalization:
    def test___data___속성_가진_응답_정규화_OK(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """`__data__` 속성에 raw dict 를 담은 stub → 정상 파싱, MinuteBar yield."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        row = _make_output2_row(_YESTERDAY, 9, 31)
        raw_dict = _make_api_response([row])
        # KisDynamicDict 스타일: __data__ 에 raw dict 보관, dict 서브클래스 아님
        fake_kis.fetch.return_value = _FakeKisDynamicDict(raw_dict)

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))

        assert len(bars) == 1
        assert bars[0].bar_time.minute == 31
        loader.close()

    def test_dict_아니고___data___도_없으면_KisMinuteBarLoadError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """dict·__data__ 모두 없으면 KisMinuteBarLoadError 래핑."""
        KisMinuteBarLoader, KisMinuteBarLoadError = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        # dict 도 아니고 __data__ 도 없는 객체
        fake_kis.fetch.return_value = object()

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        with pytest.raises(KisMinuteBarLoadError):
            list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()


# ===========================================================================
# TestMultiSymbolPartialCache — 일부 심볼만 캐시 hit (갭 2)
# ===========================================================================


class TestMultiSymbolPartialCache:
    def test_일부_심볼_캐시_hit_나머지_API_호출_정렬_안정성(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """일부 심볼만 캐시 hit — fetch 는 miss 심볼만 호출, 정렬 안정성 유지."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        db_path = tmp_path / "test.db"

        # 005930 의 _YESTERDAY bar 를 DB 에 미리 삽입
        conn = sqlite3.connect(str(db_path))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS minute_bars (
                symbol TEXT,
                bar_time TEXT,
                open TEXT,
                high TEXT,
                low TEXT,
                close TEXT,
                volume INTEGER,
                PRIMARY KEY (symbol, bar_time)
            );
            CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO schema_version VALUES (1);
        """)
        bar_time_str = f"{_YESTERDAY.isoformat()}T09:31:00+09:00"
        conn.execute(
            "INSERT OR REPLACE INTO minute_bars VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_SYMBOL, bar_time_str, "71000", "71500", "70800", "71200", 1234),
        )
        conn.commit()
        conn.close()

        # 000660 은 fetch 로 얻는 응답
        row_000660 = _make_output2_row(_YESTERDAY, 9, 31)

        def _side_effect(*args, **kwargs):
            params = kwargs.get("params", {})
            sym = params.get("FID_INPUT_ISCD", "")
            if sym == _SYMBOL2:
                return _make_api_response([row_000660])
            return _make_api_response([])

        fake_kis.fetch.side_effect = _side_effect

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=db_path,
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL, _SYMBOL2)))

        # 005930 은 캐시 hit → 000660 만 API 호출 (1회)
        assert fake_kis.fetch.call_count == 1
        called_sym = fake_kis.fetch.call_args.kwargs.get("params", {}).get("FID_INPUT_ISCD")
        assert called_sym == _SYMBOL2

        # 결과: 2건 (각 심볼 1건씩), (bar_time, symbol) 정렬
        assert len(bars) == 2
        keys = [(b.bar_time, b.symbol) for b in bars]
        assert keys == sorted(keys), "결과가 (bar_time, symbol) 단조증가여야 한다"
        # 같은 bar_time 이면 lexical 순: "000660" < "005930"
        assert bars[0].symbol == _SYMBOL2
        assert bars[1].symbol == _SYMBOL
        loader.close()


# ===========================================================================
# TestStreamAfterClose — close 후 stream 호출 RuntimeError (갭 3)
# ===========================================================================


class TestStreamAfterClose:
    def test_close_후_stream_RuntimeError(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
    ) -> None:
        """`close()` 호출 후 `stream()` → `RuntimeError` (공개 API Raises 계약)."""
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        loader.close()

        with pytest.raises(RuntimeError, match="close"):
            list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))


# ===========================================================================
# TestParseRowSkipCategories — Issue #52 회귀: _ParseSkip kind 별 dedupe warning
# ===========================================================================


def _make_loguru_warnings() -> tuple[list[dict], int]:
    """loguru WARNING 레벨 메시지 캡처 sink 등록 헬퍼.

    반환값: (captured_list, handler_id). 호출자가 finally 에서
    `logger.remove(handler_id)` 를 호출해 싱크를 해제해야 한다.
    """
    from loguru import logger as _logger

    captured: list[dict] = []

    def _sink(message) -> None:  # type: ignore[no-untyped-def]
        record = message.record
        captured.append({"level": record["level"].name, "message": record["message"]})

    handler_id = _logger.add(_sink, level="WARNING", format="{message}")
    return captured, handler_id


class TestParseRowSkipCategories:
    """Issue #52 회귀 — `_ParseSkip.kind` 단위 dedupe warning 검증.

    검증 대상:
    - 각 `_ParseFailureKind` 카테고리가 warning 에 `kind=<value>` 로 포함된다.
    - 동일 날짜·동일 kind 는 여러 행이 있어도 1회만 방출된다 (로그 폭주 방지).
    - `keys=` 접두 토큰 + 실제 row key 이름이 warning 메시지에 포함된다.
    - `symbol=` + `date=` 맥락 정보가 포함된다.

    테스트 레벨: `loader.stream(...)` 공개 API 경유 — `_parse_row` 직접 호출 금지.
    """

    # -----------------------------------------------------------------------
    # 공통 픽스처: loguru WARNING sink
    # -----------------------------------------------------------------------

    @pytest.fixture
    def _loguru_warnings(self):
        """loguru WARNING 레벨 메시지 캡처 픽스처."""
        from loguru import logger as _logger

        captured: list[dict] = []

        def _sink(message) -> None:  # type: ignore[no-untyped-def]
            record = message.record
            captured.append({"level": record["level"].name, "message": record["message"]})

        handler_id = _logger.add(_sink, level="WARNING", format="{message}")
        try:
            yield captured
        finally:
            _logger.remove(handler_id)

    # -----------------------------------------------------------------------
    # 헬퍼: loader 생성 (반복 제거)
    # -----------------------------------------------------------------------

    def _make_loader(self, monkeypatch, pykis_factory, mock_sleep, tmp_path, clock_dt=None):
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)
        dt = clock_dt or _kst(_TODAY, 10, 0)
        return KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(dt),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )

    # -----------------------------------------------------------------------
    # 1. missing_date_or_time
    # -----------------------------------------------------------------------

    def test_missing_date_or_time_kind_warning_1회(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        _loguru_warnings,
    ) -> None:
        """stck_bsop_date='' 행 2개 → kind=missing_date_or_time warning 1회, dedupe 검증.

        회귀: Issue #52 — 행 단위가 아니라 (kind) 단위 dedupe 로 로그 폭주 방지.
        """
        # 두 행 모두 stck_bsop_date 가 빈 문자열 → 길이 != 8 → missing_date_or_time
        bad_rows = [
            {
                "stck_bsop_date": "",
                "stck_cntg_hour": f"09310{i}",
                "stck_oprc": "71000",
                "stck_hgpr": "71500",
                "stck_lwpr": "70800",
                "stck_prpr": "71200",
                "cntg_vol": "1234",
            }
            for i in range(2)
        ]
        fake_kis.fetch.return_value = _make_api_response(bad_rows)

        loader = self._make_loader(monkeypatch, pykis_factory, mock_sleep, tmp_path)
        list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()

        # WARNING 중 kind=missing_date_or_time 포함한 것만 추린다
        warn_msgs = [
            m["message"]
            for m in _loguru_warnings
            if m["level"] == "WARNING" and "kind=missing_date_or_time" in m["message"]
        ]
        # dedupe: 2행이지만 1회만 방출
        assert len(warn_msgs) == 1, f"dedupe 실패: {len(warn_msgs)}회 방출됨"
        msg = warn_msgs[0]
        # 맥락 정보 포함 여부
        assert f"symbol={_SYMBOL}" in msg
        assert _YESTERDAY.strftime("%Y%m%d") in msg
        # keys= 토큰 + 실제 키 이름
        assert "keys=" in msg
        assert "stck_bsop_date" in msg

    # -----------------------------------------------------------------------
    # 2. date_mismatch
    # -----------------------------------------------------------------------

    def test_date_mismatch_kind_warning_1회(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        _loguru_warnings,
    ) -> None:
        """stck_bsop_date 가 요청 날짜와 다른 행 3개 → kind=date_mismatch warning 1회."""
        wrong_date = _YESTERDAY - timedelta(days=1)  # _YESTERDAY 보다 1일 전
        bad_rows = [_make_output2_row(wrong_date, 9, 31 + i) for i in range(3)]
        fake_kis.fetch.return_value = _make_api_response(bad_rows)

        # _YESTERDAY 기준으로 stream 요청 — wrong_date 는 date_mismatch
        loader = self._make_loader(monkeypatch, pykis_factory, mock_sleep, tmp_path)
        list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()

        warn_msgs = [
            m["message"]
            for m in _loguru_warnings
            if m["level"] == "WARNING" and "kind=date_mismatch" in m["message"]
        ]
        assert len(warn_msgs) == 1, f"dedupe 실패: {len(warn_msgs)}회 방출됨"

    # -----------------------------------------------------------------------
    # 3. invalid_price
    # -----------------------------------------------------------------------

    def test_invalid_price_kind_warning_1회(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        _loguru_warnings,
    ) -> None:
        """stck_oprc='' 행 2개 → kind=invalid_price warning 1회."""
        bad_rows = [_make_output2_row(_YESTERDAY, 9, 31 + i, oprc="") for i in range(2)]
        fake_kis.fetch.return_value = _make_api_response(bad_rows)

        loader = self._make_loader(monkeypatch, pykis_factory, mock_sleep, tmp_path)
        list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()

        warn_msgs = [
            m["message"]
            for m in _loguru_warnings
            if m["level"] == "WARNING" and "kind=invalid_price" in m["message"]
        ]
        assert len(warn_msgs) == 1, f"dedupe 실패: {len(warn_msgs)}회 방출됨"

    # -----------------------------------------------------------------------
    # 4. invalid_volume
    # -----------------------------------------------------------------------

    def test_invalid_volume_kind_warning_1회(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        _loguru_warnings,
    ) -> None:
        """cntg_vol='' 행 2개 → kind=invalid_volume warning 1회."""
        bad_rows = [_make_output2_row(_YESTERDAY, 9, 31 + i, vol="") for i in range(2)]
        fake_kis.fetch.return_value = _make_api_response(bad_rows)

        loader = self._make_loader(monkeypatch, pykis_factory, mock_sleep, tmp_path)
        list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()

        warn_msgs = [
            m["message"]
            for m in _loguru_warnings
            if m["level"] == "WARNING" and "kind=invalid_volume" in m["message"]
        ]
        assert len(warn_msgs) == 1, f"dedupe 실패: {len(warn_msgs)}회 방출됨"

    # -----------------------------------------------------------------------
    # 5. malformed_bar_time
    # -----------------------------------------------------------------------

    def test_malformed_bar_time_kind_warning_1회(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        _loguru_warnings,
    ) -> None:
        """stck_cntg_hour='999999' 행 2개 → kind=malformed_bar_time warning 1회.

        길이=6 조건을 통과하지만 시=99, 분=99, 초=99 로
        `strptime('%Y%m%d%H%M%S', ...)` 에서 ValueError 가 발생한다.
        """
        bad_rows = [
            {
                "stck_bsop_date": _YESTERDAY.strftime("%Y%m%d"),
                "stck_cntg_hour": "999999",  # 길이 6 이지만 파싱 실패 유도
                "stck_oprc": "71000",
                "stck_hgpr": "71500",
                "stck_lwpr": "70800",
                "stck_prpr": "71200",
                "cntg_vol": "1234",
            }
            for _ in range(2)
        ]
        fake_kis.fetch.return_value = _make_api_response(bad_rows)

        loader = self._make_loader(monkeypatch, pykis_factory, mock_sleep, tmp_path)
        list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()

        warn_msgs = [
            m["message"]
            for m in _loguru_warnings
            if m["level"] == "WARNING" and "kind=malformed_bar_time" in m["message"]
        ]
        assert len(warn_msgs) == 1, f"dedupe 실패: {len(warn_msgs)}회 방출됨"

    # -----------------------------------------------------------------------
    # 6. keys= CSV 전체 정밀 매칭 회귀 (Issue #58)
    # -----------------------------------------------------------------------

    def test_keys_csv_전체_정밀_매칭_7개_키_알파벳_순서(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        _loguru_warnings,
    ) -> None:
        """invalid_price 트리거 → warning 메시지의 keys= CSV 가 정확히 7개 키 알파벳 순서인지 검증.

        회귀 목적: `_fetch_day` 의 `ks=",".join(skip.keys)` 포맷이 바뀌거나
        `_ParseSkipError.keys` 수집 로직이 빠진 키를 놓칠 때 즉시 실패하도록 고정.

        기대 CSV (알파벳 오름차순, 쉼표·공백 없음):
            cntg_vol,stck_bsop_date,stck_cntg_hour,stck_hgpr,stck_lwpr,stck_oprc,stck_prpr
        """
        import re

        # stck_oprc='' 1행 → invalid_price 트리거
        bad_rows = [_make_output2_row(_YESTERDAY, 9, 31, oprc="")]
        fake_kis.fetch.return_value = _make_api_response(bad_rows)

        loader = self._make_loader(monkeypatch, pykis_factory, mock_sleep, tmp_path)
        list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()

        warn_msgs = [
            m["message"]
            for m in _loguru_warnings
            if m["level"] == "WARNING" and "kind=invalid_price" in m["message"]
        ]
        assert len(warn_msgs) == 1, f"warning 1건 기대: {len(warn_msgs)}건"
        msg = warn_msgs[0]

        # "keys=" 뒤의 CSV 문자열 추출
        m = re.search(r"keys=([^\s]+)", msg)
        assert m is not None, f"keys= 토큰을 메시지에서 추출하지 못함: {msg!r}"
        keys_csv = m.group(1)

        # 쉼표로 분리해 정확히 7개 키 확인
        actual_keys = keys_csv.split(",")
        expected_keys = [
            "cntg_vol",
            "stck_bsop_date",
            "stck_cntg_hour",
            "stck_hgpr",
            "stck_lwpr",
            "stck_oprc",
            "stck_prpr",
        ]
        fail_msg = f"keys= CSV 불일치.\n기대: {expected_keys}\n실제: {actual_keys}"
        assert actual_keys == expected_keys, fail_msg

    # -----------------------------------------------------------------------
    # 7. 서로 다른 kind 혼재 — 각각 1회씩
    # -----------------------------------------------------------------------

    def test_서로_다른_kind_혼재_각각_1회씩(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        _loguru_warnings,
    ) -> None:
        """invalid_price 2행 + invalid_volume 2행 → 각 kind 1회씩, 총 warning 2개."""
        rows_invalid_price = [_make_output2_row(_YESTERDAY, 9, 31 + i, oprc="") for i in range(2)]
        rows_invalid_volume = [_make_output2_row(_YESTERDAY, 9, 35 + i, vol="") for i in range(2)]
        fake_kis.fetch.return_value = _make_api_response(rows_invalid_price + rows_invalid_volume)

        loader = self._make_loader(monkeypatch, pykis_factory, mock_sleep, tmp_path)
        list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()

        price_warns = [
            m
            for m in _loguru_warnings
            if m["level"] == "WARNING" and "kind=invalid_price" in m["message"]
        ]
        volume_warns = [
            m
            for m in _loguru_warnings
            if m["level"] == "WARNING" and "kind=invalid_volume" in m["message"]
        ]
        assert len(price_warns) == 1, f"invalid_price dedupe 실패: {len(price_warns)}회"
        assert len(volume_warns) == 1, f"invalid_volume dedupe 실패: {len(volume_warns)}회"


# ===========================================================================
# TestMalformedPageWarningKeysToken — Issue #52: M2 error 메시지에 keys= 포함 검증
# ===========================================================================


class TestMalformedPageWarningKeysToken:
    """M2 error 메시지에 `keys=` 토큰 + 실제 key 이름 CSV 가 포함됨을 검증.

    Issue #52 후속 — `_fetch_day` 의 `logger.error` 가 `keys={first_row_keys_csv}` 를
    동봉해 스키마 변경 진단을 직결하도록 수정됨 (기존 메시지에 keys 필드 추가).
    """

    @pytest.fixture
    def _loguru_errors(self):
        """loguru ERROR 레벨 메시지 캡처."""
        from loguru import logger as _logger

        captured: list[dict] = []

        def _sink(message) -> None:  # type: ignore[no-untyped-def]
            record = message.record
            captured.append({"level": record["level"].name, "message": record["message"]})

        handler_id = _logger.add(_sink, level="ERROR", format="{message}")
        try:
            yield captured
        finally:
            _logger.remove(handler_id)

    def test_error_메시지에_first_row_keys_포함(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        _loguru_errors,
    ) -> None:
        """rows 3건 모두 stck_oprc='' (전원 파싱 실패) → logger.error 메시지에
        `keys=` 토큰 + `stck_bsop_date`, `stck_cntg_hour` 등 실제 key 이름 CSV 포함.

        Issue #52 회귀: M2 경보 메시지가 keys 필드를 동봉해야 스키마 변경 진단이 가능.
        """
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        # 전원 파싱 실패 유도 (invalid_price) — page_bars 가 비어 M2 경보 트리거
        malformed_rows = [_make_output2_row(_YESTERDAY, 9, 31 + i, oprc="") for i in range(3)]
        fake_kis.fetch.return_value = _make_api_response(malformed_rows)

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()

        errors = [m for m in _loguru_errors if m["level"] == "ERROR"]
        assert len(errors) == 1, f"ERROR 경보 1회 기대, 실제 {len(errors)}회"
        msg = errors[0]["message"]

        # keys= 토큰 존재 확인
        assert "keys=" in msg, f"keys= 토큰이 error 메시지에 없음: {msg!r}"
        # _make_output2_row 가 생성하는 실제 key 이름들이 CSV 에 포함돼야 함
        assert "stck_bsop_date" in msg, f"stck_bsop_date 가 keys 에 없음: {msg!r}"
        assert "stck_cntg_hour" in msg, f"stck_cntg_hour 가 keys 에 없음: {msg!r}"


# ===========================================================================
# TestFetchDaySkipSummary — Issue #52 C1: 날짜 단위 skip 요약 warning 포맷 검증
# ===========================================================================


class TestFetchDaySkipSummary:
    """C1: `_fetch_day` return 직전 `parse_skip_counts` 요약 warning 계약 고정.

    요약 warning 은 `"skip 요약"` 부분 문자열을 포함하고,
    symbol / date / counts(정렬 dict repr) / kept=N 필드를 동봉한다.
    """

    @pytest.fixture
    def _loguru_warnings(self):
        """loguru WARNING 레벨 메시지 캡처 픽스처."""
        from loguru import logger as _logger

        captured: list[dict] = []

        def _sink(message) -> None:  # type: ignore[no-untyped-def]
            record = message.record
            captured.append({"level": record["level"].name, "message": record["message"]})

        handler_id = _logger.add(_sink, level="WARNING", format="{message}")
        try:
            yield captured
        finally:
            _logger.remove(handler_id)

    def test_skip_counts_요약_warning_방출_및_포맷(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        _loguru_warnings,
    ) -> None:
        """invalid_price 2행 + invalid_volume 2행 → 날짜 단위 요약 warning 1개 방출.

        C1 계약 고정: 요약 warning 메시지에 symbol / date / kept=0 / counts 정렬 dict repr
        이 포함되어야 한다. PR #56 리뷰 — `parse_skip_counts` 정렬된 dict 를 요약에 포함해
        '1건 실패 vs 119건 실패' 구별 불가 문제 해소.
        """
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        # invalid_price 2행 (stck_oprc="") + invalid_volume 2행 (cntg_vol="") — 전원 skip
        rows_invalid_price = [_make_output2_row(_YESTERDAY, 9, 31 + i, oprc="") for i in range(2)]
        rows_invalid_volume = [_make_output2_row(_YESTERDAY, 9, 35 + i, vol="") for i in range(2)]
        fake_kis.fetch.return_value = _make_api_response(rows_invalid_price + rows_invalid_volume)

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()

        # 전원 skip → 정상 bar 0건
        assert len(bars) == 0

        # 요약 warning: "skip 요약" 부분 문자열
        summary_warns = [
            m["message"]
            for m in _loguru_warnings
            if m["level"] == "WARNING" and "skip 요약" in m["message"]
        ]
        n_summary = len(summary_warns)
        assert n_summary == 1, f"날짜 단위 요약 warning 이 정확히 1개여야 한다 (실제={n_summary})"
        msg = summary_warns[0]

        # symbol / date / kept 필드
        assert f"symbol={_SYMBOL}" in msg, f"symbol 필드 누락: {msg!r}"
        assert "date=20260421" in msg, f"date 필드 누락: {msg!r}"
        assert "kept=0" in msg, f"kept=0 필드 누락: {msg!r}"

        # counts 에 kind 별 횟수 포함 (정렬 dict repr)
        assert "'invalid_price': 2" in msg, f"invalid_price count 누락: {msg!r}"
        assert "'invalid_volume': 2" in msg, f"invalid_volume count 누락: {msg!r}"

        # 카테고리별 kind warning (invalid_price 1 + invalid_volume 1) + 요약 1 = 총 3개
        all_warns = [m for m in _loguru_warnings if m["level"] == "WARNING"]
        assert len(all_warns) == 3, (
            f"총 warning 3개 기대 (kind×2 + 요약×1), 실제={len(all_warns)}: "
            f"{[m['message'] for m in all_warns]}"
        )


# ===========================================================================
# PR #56 리뷰 C2: date_mismatch 일부 행만 불일치 — kept 건수 정확성
# ===========================================================================


class TestDateMismatchPartialRows:
    """C2: `_parse_row` 의 `expected_day` 주입이 실제 동작함을 공개 API 경유로 증명.

    동일 stream 요청에서 날짜가 맞는 행은 yield 하고, 날짜가 맞지 않는 행은
    date_mismatch warning 1회 + skip 처리됨을 검증한다.
    """

    @pytest.fixture
    def _loguru_warnings(self):
        """loguru WARNING 레벨 메시지 캡처 픽스처."""
        from loguru import logger as _logger

        captured: list[dict] = []

        def _sink(message) -> None:  # type: ignore[no-untyped-def]
            record = message.record
            captured.append({"level": record["level"].name, "message": record["message"]})

        handler_id = _logger.add(_sink, level="WARNING", format="{message}")
        try:
            yield captured
        finally:
            _logger.remove(handler_id)

    def test_date_mismatch_일부_행만_불일치(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        _loguru_warnings,
    ) -> None:
        """정상 2행 + date_mismatch 1행 혼재 → bars 2건만 yield, date_mismatch warning 1회.

        PR #56 리뷰 C2: `_parse_row(row, expected_day)` 의 expected_day 주입이
        실제 동작하는지 검증. 구현이 expected_day 를 무시하면 3건이 모두 yield 되어 실패.
        """
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        wrong_date = _YESTERDAY - timedelta(days=1)  # 2026-04-20 (stream 요청 범위 밖)
        rows = [
            _make_output2_row(_YESTERDAY, 9, 31),  # 정상
            _make_output2_row(wrong_date, 9, 32),  # date_mismatch
            _make_output2_row(_YESTERDAY, 9, 33),  # 정상
        ]
        fake_kis.fetch.return_value = _make_api_response(rows)

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        bars = list(loader.stream(_YESTERDAY, _YESTERDAY, (_SYMBOL,)))
        loader.close()

        # 정상 2건만 yield
        assert len(bars) == 2, f"정상 행 2건만 yield 돼야 한다 (실제={len(bars)})"
        # date_mismatch warning 정확히 1회 (dedupe)
        mismatch_warns = [
            m["message"]
            for m in _loguru_warnings
            if m["level"] == "WARNING" and "kind=date_mismatch" in m["message"]
        ]
        n_mismatch = len(mismatch_warns)
        assert n_mismatch == 1, f"date_mismatch warning 1회 기대 (실제={n_mismatch})"
        warn_msg = mismatch_warns[0]
        assert "date=20260421" in warn_msg, f"date 필드 누락: {warn_msg!r}"


# ===========================================================================
# PR #56 리뷰 C3: parse_skip_emitted 가 _fetch_day 로컬 — 날짜 변경 시 리셋
# ===========================================================================


class TestParseSkipEmittedResetsPerFetchDay:
    """C3: `parse_skip_emitted` 는 `_fetch_day` 진입마다 새로 초기화됨을 증명.

    두 날짜 모두 동일한 kind(invalid_price) 실패 행이 있을 때,
    날짜별로 각 1회씩 — 총 2회 — warning 이 방출되어야 한다.
    parse_skip_emitted 가 인스턴스 속성으로 승격되면 두 번째 날짜에서
    dedupe 에 걸려 warning 이 1회만 방출되므로 이 테스트가 실패한다
    (Issue #52 C3 회귀 방지).
    """

    @pytest.fixture
    def _loguru_warnings(self):
        """loguru WARNING 레벨 메시지 캡처 픽스처."""
        from loguru import logger as _logger

        captured: list[dict] = []

        def _sink(message) -> None:  # type: ignore[no-untyped-def]
            record = message.record
            captured.append({"level": record["level"].name, "message": record["message"]})

        handler_id = _logger.add(_sink, level="WARNING", format="{message}")
        try:
            yield captured
        finally:
            _logger.remove(handler_id)

    def test_다중_날짜_같은_kind_날짜당_warning_1회씩(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_kis,
        pykis_factory,
        guard_patch,
        mock_sleep,
        tmp_path: Path,
        _loguru_warnings,
    ) -> None:
        """두 날짜 모두 invalid_price 실패 행 2건씩 → 날짜당 kind warning 1회씩 = 총 2회.

        parse_skip_emitted 는 `_fetch_day` 로컬 — 날짜 변경 시 리셋.
        리팩터로 인스턴스 속성 승격 시 이 테스트가 실패하도록 설계됨 (Issue #52 C3).
        """
        KisMinuteBarLoader, _ = _import_loader()
        settings = _make_settings_with_live_keys(monkeypatch)

        d1 = date(2026, 4, 21)  # _YESTERDAY (월)
        d2 = date(2026, 4, 20)  # 그 전 금요일

        # 두 날짜 모두 invalid_price 행 2건씩
        # _collect_symbol_bars 는 end → start 역방향 순회:
        # day_to=d1 먼저 → day_from=d2 순으로 _fetch_day 호출
        rows_d1 = [_make_output2_row(d1, 9, 31 + i, oprc="") for i in range(2)]
        rows_d2 = [_make_output2_row(d2, 9, 31 + i, oprc="") for i in range(2)]

        fake_kis.fetch.side_effect = [
            _make_api_response(rows_d1),  # d1 (end 먼저)
            _make_api_response(rows_d2),  # d2
        ]

        loader = KisMinuteBarLoader(
            settings,
            pykis_factory=pykis_factory,
            clock=_fixed_clock(_kst(_TODAY, 10, 0)),
            cache_db_path=tmp_path / "test.db",
            sleep=mock_sleep,
        )
        bars = list(loader.stream(d2, d1, (_SYMBOL,)))
        loader.close()

        # 전원 skip → bar 0건
        assert len(bars) == 0

        # kind=invalid_price warning 이 정확히 2회 (날짜당 1회씩)
        price_warns = [
            m["message"]
            for m in _loguru_warnings
            if m["level"] == "WARNING" and "kind=invalid_price" in m["message"]
        ]
        assert len(price_warns) == 2, (
            f"날짜당 1회씩 총 2회 기대 (실제={len(price_warns)}). "
            f"parse_skip_emitted 가 인스턴스 속성으로 승격되면 1회만 방출돼 실패함."
        )

        # 날짜 정보 구분 가능: 20260421 포함 1개, 20260420 포함 1개
        d1_warns = [w for w in price_warns if "date=20260421" in w]
        d2_warns = [w for w in price_warns if "date=20260420" in w]
        assert len(d1_warns) == 1, f"d1(20260421) warning 1개 기대: {price_warns}"
        assert len(d2_warns) == 1, f"d2(20260420) warning 1개 기대: {price_warns}"

        # 날짜 단위 요약 warning 도 2회 (각 날짜 1회씩)
        summary_warns = [
            m["message"]
            for m in _loguru_warnings
            if m["level"] == "WARNING" and "skip 요약" in m["message"]
        ]
        n_summary2 = len(summary_warns)
        assert n_summary2 == 2, f"날짜 단위 요약 warning 2회 기대 (실제={n_summary2})"
