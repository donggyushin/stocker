"""scripts/build_liquidity_ranking.py 공개 계약 단위 테스트 (RED 명세).

build_ranking / main(exit code) 를 검증한다.
pykrx, BusinessDayCalendar, load_kospi200_universe 는 전부 MagicMock 으로 교체.
실 pykrx 네트워크·실 KIS 호출·실 wall-clock 없음. 파일 I/O 는 tmp_path 만.
"""

from __future__ import annotations

import csv
import importlib.util
import sys
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# scripts/build_liquidity_ranking.py 로드
# (scripts/ 에 __init__.py 없음 — spec_from_file_location, backfill_cli 와 동일 패턴)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "build_liquidity_ranking.py"

_src = str(_PROJECT_ROOT / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

_LOAD_ERROR: Exception | None = None
liquidity_cli = None  # type: ignore[assignment]

try:
    _spec = importlib.util.spec_from_file_location("liquidity_cli", _SCRIPT_PATH)
    if _spec is None or _spec.loader is None:
        raise ImportError(f"spec_from_file_location 이 None 반환: {_SCRIPT_PATH}")
    liquidity_cli = importlib.util.module_from_spec(_spec)
    sys.modules["liquidity_cli"] = liquidity_cli
    _spec.loader.exec_module(liquidity_cli)  # type: ignore[union-attr]
except Exception as _e:
    _LOAD_ERROR = _e


def _require_module() -> None:
    """각 테스트 시작 시 호출 — 모듈 로드 실패면 pytest.fail() 로 FAIL."""
    if _LOAD_ERROR is not None:
        pytest.fail(
            f"scripts/build_liquidity_ranking.py 로드 실패 (RED 예상): {_LOAD_ERROR}",
            pytrace=False,
        )


# ---------------------------------------------------------------------------
# 공개 심볼 참조 래퍼
# ---------------------------------------------------------------------------


def build_ranking(**kwargs):  # type: ignore[misc]
    _require_module()
    return liquidity_cli.build_ranking(**kwargs)  # type: ignore[union-attr]


def main(argv=None):  # type: ignore[misc]
    _require_module()
    return liquidity_cli.main(argv)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 외부 의존 심볼
# ---------------------------------------------------------------------------

from stock_agent.data import (  # noqa: E402
    BusinessDayCalendar,
    UniverseLoadError,
)

# ---------------------------------------------------------------------------
# 상수 / 헬퍼
# ---------------------------------------------------------------------------

_SYM_A = "005930"  # 삼성전자
_SYM_B = "000660"  # SK하이닉스


def _make_mini_universe_yaml(tmp_path: Path, tickers: list[str]) -> Path:
    """테스트용 최소 universe.yaml 을 tmp_path 에 작성하고 경로를 반환한다."""
    yaml_path = tmp_path / "universe.yaml"
    tickers_str = "\n".join(f'  - "{t}"' for t in tickers)
    yaml_path.write_text(
        f"as_of_date: 2026-01-01\nsource: test\ntickers:\n{tickers_str}\n",
        encoding="utf-8",
    )
    return yaml_path


def _make_calendar_stub(business_days: set[date]) -> MagicMock:
    """지정된 날짜만 영업일로 반환하는 BusinessDayCalendar 더블."""
    cal = MagicMock(spec=BusinessDayCalendar)
    cal.is_business_day.side_effect = lambda d: d in business_days
    return cal


def _make_pykrx_factory(day_data: dict[date, dict[str, dict[str, int]]]):
    """
    day_data: {date: {symbol: {"종가": int, "거래대금": int}}}
    pykrx.stock.get_market_ohlcv_by_ticker(yyyymmdd, market="KOSPI") → DataFrame 더블 반환.
    DataFrame 더블은 iterrows() / __contains__ / __getitem__ 지원 MagicMock.
    """
    try:
        import pandas as pd

        def _factory():
            pykrx_mock = MagicMock()

            def _get_market_ohlcv_by_ticker(yyyymmdd: str, market: str = "KOSPI"):
                d = date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))
                rows = day_data.get(d, {})
                if not rows:
                    return pd.DataFrame()
                # 인덱스가 종목코드, 컬럼에 "종가"·"거래대금" 포함
                records = {sym: vals for sym, vals in rows.items()}
                df = pd.DataFrame.from_dict(records, orient="index")
                return df

            pykrx_mock.stock.get_market_ohlcv_by_ticker.side_effect = _get_market_ohlcv_by_ticker
            return pykrx_mock

        return _factory
    except ImportError:
        # pandas 없을 경우 fallback — 테스트는 ImportError 로 FAIL
        return lambda: MagicMock()


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    """CSV 파일을 읽어 헤더 기반 dict list 반환."""
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _make_3day_factory(
    sym_a_closes: list[int],
    sym_a_values: list[int],
    sym_b_closes: list[int],
    sym_b_values: list[int],
    days: list[date],
) -> object:
    """3영업일 × 2종목 표준 팩토리 생성 헬퍼."""
    assert len(days) == 3
    day_data: dict[date, dict[str, dict[str, int]]] = {}
    for i, d in enumerate(days):
        day_data[d] = {
            _SYM_A: {"종가": sym_a_closes[i], "거래대금": sym_a_values[i]},
            _SYM_B: {"종가": sym_b_closes[i], "거래대금": sym_b_values[i]},
        }
    return _make_pykrx_factory(day_data)


# ===========================================================================
# A. build_ranking 정상 케이스
# ===========================================================================


class TestBuildRankingNormal:
    """A-1 ~ A-4: 정상 경로 검증."""

    # 3영업일
    _DAYS = [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]

    def test_A1_단순_2종목_3영업일_CSV_헤더_및_평균거래대금(self, tmp_path: Path):
        """2종목 × 3영업일 — 헤더, 평균 거래대금(반올림 정수), sample_days=3 검증."""
        _require_module()
        universe_yaml = _make_mini_universe_yaml(tmp_path, [_SYM_A, _SYM_B])
        output_csv = tmp_path / "ranking.csv"
        cal = _make_calendar_stub(set(self._DAYS))

        # SYM_A: 거래대금 300, 200, 100 → 평균 200
        # SYM_B: 거래대금 600, 600, 600 → 평균 600
        factory = _make_3day_factory(
            sym_a_closes=[10000, 10100, 10200],
            sym_a_values=[300, 200, 100],
            sym_b_closes=[50000, 51000, 52000],
            sym_b_values=[600, 600, 600],
            days=self._DAYS,
        )

        build_ranking(
            start=self._DAYS[0],
            end=self._DAYS[-1],
            universe_yaml=universe_yaml,
            output_csv=output_csv,
            pykrx_factory=factory,
            calendar=cal,
        )

        assert output_csv.exists(), "output_csv 가 생성되어야 한다"
        rows = _read_csv_rows(output_csv)
        # 헤더 컬럼 정확
        assert set(rows[0].keys()) == {
            "symbol",
            "avg_value_krw",
            "daily_return_std",
            "sample_days",
            "rank_value",
        }
        # sample_days = 3
        for row in rows:
            assert int(row["sample_days"]) == 3
        # SYM_B 평균 거래대금 = 600, SYM_A = 200
        by_sym = {r["symbol"]: r for r in rows}
        assert int(by_sym[_SYM_A]["avg_value_krw"]) == 200
        assert int(by_sym[_SYM_B]["avg_value_krw"]) == 600

    def test_A1_rank_value_높은_평균이_1(self, tmp_path: Path):
        """평균 거래대금이 높은 종목의 rank_value = 1."""
        _require_module()
        universe_yaml = _make_mini_universe_yaml(tmp_path, [_SYM_A, _SYM_B])
        output_csv = tmp_path / "ranking.csv"
        cal = _make_calendar_stub(set(self._DAYS))

        factory = _make_3day_factory(
            sym_a_closes=[10000, 10100, 10200],
            sym_a_values=[100, 100, 100],  # SYM_A 낮음
            sym_b_closes=[50000, 51000, 52000],
            sym_b_values=[900, 900, 900],  # SYM_B 높음
            days=self._DAYS,
        )

        build_ranking(
            start=self._DAYS[0],
            end=self._DAYS[-1],
            universe_yaml=universe_yaml,
            output_csv=output_csv,
            pykrx_factory=factory,
            calendar=cal,
        )

        rows = _read_csv_rows(output_csv)
        by_sym = {r["symbol"]: r for r in rows}
        assert int(by_sym[_SYM_B]["rank_value"]) == 1
        assert int(by_sym[_SYM_A]["rank_value"]) == 2

    def test_A1_daily_return_std_ddof1(self, tmp_path: Path):
        """daily_return std(ddof=1) 정확도 검증.

        SYM_A 종가: [10000, 10100, 10200]
        returns: [(10100/10000)-1, (10200/10100)-1] = [0.01, ~0.009901]
        std(ddof=1) ≈ 0.000070...
        """
        _require_module()
        import math

        universe_yaml = _make_mini_universe_yaml(tmp_path, [_SYM_A])
        output_csv = tmp_path / "ranking.csv"
        cal = _make_calendar_stub(set(self._DAYS))

        factory = _make_pykrx_factory(
            {
                self._DAYS[0]: {_SYM_A: {"종가": 10000, "거래대금": 100}},
                self._DAYS[1]: {_SYM_A: {"종가": 10100, "거래대금": 100}},
                self._DAYS[2]: {_SYM_A: {"종가": 10200, "거래대금": 100}},
            }
        )

        build_ranking(
            start=self._DAYS[0],
            end=self._DAYS[-1],
            universe_yaml=universe_yaml,
            output_csv=output_csv,
            pykrx_factory=factory,
            calendar=cal,
        )

        rows = _read_csv_rows(output_csv)
        assert len(rows) == 1
        std_val = float(rows[0]["daily_return_std"])

        # 수동 계산: returns = [0.01, 10200/10100 - 1]
        r1 = 10100 / 10000 - 1
        r2 = 10200 / 10100 - 1
        mean_r = (r1 + r2) / 2
        expected_std = math.sqrt(((r1 - mean_r) ** 2 + (r2 - mean_r) ** 2) / 1)
        assert abs(std_val - expected_std) < 1e-9, f"std={std_val!r} expected≈{expected_std!r}"

    def test_A2_sample_days_종목별_차이(self, tmp_path: Path):
        """한 종목이 1일 누락되면 sample_days=2 로 기록된다."""
        _require_module()
        universe_yaml = _make_mini_universe_yaml(tmp_path, [_SYM_A, _SYM_B])
        output_csv = tmp_path / "ranking.csv"
        cal = _make_calendar_stub(set(self._DAYS))

        # SYM_B 는 day1 에 데이터 없음 → sample_days=2
        factory = _make_pykrx_factory(
            {
                self._DAYS[0]: {
                    _SYM_A: {"종가": 10000, "거래대금": 100},
                    # _SYM_B 없음
                },
                self._DAYS[1]: {
                    _SYM_A: {"종가": 10100, "거래대금": 100},
                    _SYM_B: {"종가": 50000, "거래대금": 500},
                },
                self._DAYS[2]: {
                    _SYM_A: {"종가": 10200, "거래대금": 100},
                    _SYM_B: {"종가": 51000, "거래대금": 500},
                },
            }
        )

        build_ranking(
            start=self._DAYS[0],
            end=self._DAYS[-1],
            universe_yaml=universe_yaml,
            output_csv=output_csv,
            pykrx_factory=factory,
            calendar=cal,
        )

        rows = _read_csv_rows(output_csv)
        by_sym = {r["symbol"]: r for r in rows}
        assert int(by_sym[_SYM_A]["sample_days"]) == 3
        assert int(by_sym[_SYM_B]["sample_days"]) == 2

    def test_A3_rank_value_tiebreak_symbol_오름차순(self, tmp_path: Path):
        """평균 거래대금 동일 시 symbol 오름차순이 rank_value 낮음."""
        _require_module()
        universe_yaml = _make_mini_universe_yaml(tmp_path, [_SYM_A, _SYM_B])
        output_csv = tmp_path / "ranking.csv"
        cal = _make_calendar_stub(set(self._DAYS))

        # 동일 거래대금
        factory = _make_3day_factory(
            sym_a_closes=[10000, 10100, 10200],
            sym_a_values=[500, 500, 500],
            sym_b_closes=[50000, 51000, 52000],
            sym_b_values=[500, 500, 500],
            days=self._DAYS,
        )

        build_ranking(
            start=self._DAYS[0],
            end=self._DAYS[-1],
            universe_yaml=universe_yaml,
            output_csv=output_csv,
            pykrx_factory=factory,
            calendar=cal,
        )

        rows = _read_csv_rows(output_csv)
        by_sym = {r["symbol"]: r for r in rows}
        # SYM_A("005930") < SYM_B("000660") 는 거짓 → "000660" < "005930"
        # 따라서 000660 이 rank_value=1
        assert int(by_sym[_SYM_B]["rank_value"]) == 1  # "000660" < "005930"
        assert int(by_sym[_SYM_A]["rank_value"]) == 2

    def test_A4_output_directory_자동_생성(self, tmp_path: Path):
        """output_csv.parent 가 존재하지 않아도 자동으로 mkdir 된다."""
        _require_module()
        universe_yaml = _make_mini_universe_yaml(tmp_path, [_SYM_A])
        deep_dir = tmp_path / "deep" / "nested"
        output_csv = deep_dir / "ranking.csv"
        assert not deep_dir.exists()

        cal = _make_calendar_stub({self._DAYS[0]})
        factory = _make_pykrx_factory({self._DAYS[0]: {_SYM_A: {"종가": 10000, "거래대금": 100}}})

        build_ranking(
            start=self._DAYS[0],
            end=self._DAYS[0],
            universe_yaml=universe_yaml,
            output_csv=output_csv,
            pykrx_factory=factory,
            calendar=cal,
        )

        assert output_csv.exists()


# ===========================================================================
# B. 누락 종목 / 영업일 필터
# ===========================================================================


class TestBuildRankingFilters:
    """B-5 ~ B-7: 누락 종목 처리·영업일 필터 검증."""

    _DAYS = [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)]

    def test_B5_유니버스에_있으나_pykrx에_없는_종목_경고_후_제외(self, tmp_path: Path, caplog):
        """유니버스에 있지만 모든 영업일 pykrx DataFrame에 없는 종목은 결과 CSV 에서 제외된다."""
        _require_module()

        # SYM_A 만 universe 에 있고, pykrx 에도 있음
        # SYM_B 는 universe 에 있지만 pykrx 에 없음
        _SYM_MISSING = "999999"
        universe_yaml = _make_mini_universe_yaml(tmp_path, [_SYM_A, _SYM_MISSING])
        output_csv = tmp_path / "ranking.csv"
        cal = _make_calendar_stub(set(self._DAYS))

        factory = _make_pykrx_factory(
            {
                d: {_SYM_A: {"종가": 10000 + i * 100, "거래대금": 100}}
                for i, d in enumerate(self._DAYS)
            }
        )

        build_ranking(
            start=self._DAYS[0],
            end=self._DAYS[-1],
            universe_yaml=universe_yaml,
            output_csv=output_csv,
            pykrx_factory=factory,
            calendar=cal,
        )

        rows = _read_csv_rows(output_csv)
        symbols_in_csv = {r["symbol"] for r in rows}
        # 누락 종목은 결과에서 제외
        assert _SYM_MISSING not in symbols_in_csv
        assert _SYM_A in symbols_in_csv

    def test_B6_주말_skip_pykrx_호출_없음(self, tmp_path: Path):
        """start=금, end=월 — 토·일은 pykrx 호출이 발생하지 않는다."""
        _require_module()
        # 2026-01-02(금), 2026-01-03(토), 2026-01-04(일), 2026-01-05(월) — 토·일은
        # 캘린더가 비영업일로 가짜 표시. 호출 로그로 skip 검증.
        fri = date(2026, 1, 2)
        mon = date(2026, 1, 5)

        universe_yaml = _make_mini_universe_yaml(tmp_path, [_SYM_A])
        output_csv = tmp_path / "ranking.csv"
        # 캘린더: 금·월만 영업일
        cal = _make_calendar_stub({fri, mon})

        call_log: list[str] = []

        import pandas as pd

        def _factory():
            pykrx_mock = MagicMock()

            def _get(yyyymmdd: str, market: str = "KOSPI"):
                call_log.append(yyyymmdd)
                d = date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))
                if d in (fri, mon):
                    return pd.DataFrame.from_dict(
                        {_SYM_A: {"종가": 10000, "거래대금": 100}}, orient="index"
                    )
                return pd.DataFrame()

            pykrx_mock.stock.get_market_ohlcv_by_ticker.side_effect = _get
            return pykrx_mock

        build_ranking(
            start=fri,
            end=mon,
            universe_yaml=universe_yaml,
            output_csv=output_csv,
            pykrx_factory=_factory,
            calendar=cal,
        )

        # 토·일에 대응하는 yyyymmdd 호출이 없어야 한다
        assert "20260103" not in call_log, "토요일 pykrx 호출 발생"
        assert "20260104" not in call_log, "일요일 pykrx 호출 발생"
        assert "20260102" in call_log, "금요일 pykrx 호출 누락"
        assert "20260105" in call_log, "월요일 pykrx 호출 누락"

    def test_B7_공휴일_skip_calendar_false_날짜_호출_없음(self, tmp_path: Path):
        """calendar.is_business_day=False 인 평일은 pykrx 호출이 발생하지 않는다."""
        _require_module()
        # 2026-01-02(금), 2026-01-05(월 — 공휴일로 처리)
        fri = date(2026, 1, 2)
        mon_holiday = date(2026, 1, 5)

        universe_yaml = _make_mini_universe_yaml(tmp_path, [_SYM_A])
        output_csv = tmp_path / "ranking.csv"
        # 금만 영업일, 월은 공휴일
        cal = _make_calendar_stub({fri})

        call_log: list[str] = []

        import pandas as pd

        def _factory():
            pykrx_mock = MagicMock()

            def _get(yyyymmdd: str, market: str = "KOSPI"):
                call_log.append(yyyymmdd)
                return pd.DataFrame.from_dict(
                    {_SYM_A: {"종가": 10000, "거래대금": 100}}, orient="index"
                )

            pykrx_mock.stock.get_market_ohlcv_by_ticker.side_effect = _get
            return pykrx_mock

        build_ranking(
            start=fri,
            end=mon_holiday,
            universe_yaml=universe_yaml,
            output_csv=output_csv,
            pykrx_factory=_factory,
            calendar=cal,
        )

        assert "20260105" not in call_log, "공휴일(월) pykrx 호출 발생"
        assert "20260102" in call_log, "영업일(금) pykrx 호출 누락"


# ===========================================================================
# C. fail-fast / 입력 가드
# ===========================================================================


class TestBuildRankingGuards:
    """C-8 ~ C-11: 입력 검증·fail-fast."""

    _BD = date(2026, 1, 2)

    def _single_day_factory(self):
        import pandas as pd

        def _factory():
            m = MagicMock()
            m.stock.get_market_ohlcv_by_ticker.return_value = pd.DataFrame.from_dict(
                {_SYM_A: {"종가": 10000, "거래대금": 100}}, orient="index"
            )
            return m

        return _factory

    def test_C8_start_after_end_RuntimeError(self, tmp_path: Path):
        """start > end 이면 RuntimeError 가 발생한다."""
        _require_module()
        universe_yaml = _make_mini_universe_yaml(tmp_path, [_SYM_A])
        output_csv = tmp_path / "ranking.csv"
        cal = _make_calendar_stub({self._BD})

        with pytest.raises(RuntimeError):
            build_ranking(
                start=date(2026, 1, 10),
                end=date(2026, 1, 5),
                universe_yaml=universe_yaml,
                output_csv=output_csv,
                pykrx_factory=self._single_day_factory(),
                calendar=cal,
            )

    def test_C9_영업일_0개_RuntimeError(self, tmp_path: Path):
        """start..end 범위에 영업일이 0개면 RuntimeError 가 발생한다."""
        _require_module()
        universe_yaml = _make_mini_universe_yaml(tmp_path, [_SYM_A])
        output_csv = tmp_path / "ranking.csv"
        # 모든 날짜가 비영업일
        cal = _make_calendar_stub(set())  # 영업일 없음

        with pytest.raises(RuntimeError):
            build_ranking(
                start=date(2026, 1, 3),  # 토
                end=date(2026, 1, 4),  # 일
                universe_yaml=universe_yaml,
                output_csv=output_csv,
                pykrx_factory=self._single_day_factory(),
                calendar=cal,
            )

    def test_C10_50퍼센트_이상_영업일_pykrx_실패_RuntimeError(self, tmp_path: Path):
        """영업일 10일 중 6일 빈 DataFrame → RuntimeError('excessive_failures: ...')."""
        _require_module()
        import pandas as pd

        # 10영업일 생성 (2026-01-05 ~ 2026-01-16, 주말 제외 10일)
        ten_days = [
            date(2026, 1, 5),
            date(2026, 1, 6),
            date(2026, 1, 7),
            date(2026, 1, 8),
            date(2026, 1, 9),
            date(2026, 1, 12),
            date(2026, 1, 13),
            date(2026, 1, 14),
            date(2026, 1, 15),
            date(2026, 1, 16),
        ]
        universe_yaml = _make_mini_universe_yaml(tmp_path, [_SYM_A])
        output_csv = tmp_path / "ranking.csv"
        cal = _make_calendar_stub(set(ten_days))

        # 처음 4일만 데이터, 나머지 6일 빈 DataFrame
        good_days = set(ten_days[:4])

        def _factory():
            m = MagicMock()

            def _get(yyyymmdd: str, market: str = "KOSPI"):
                d = date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))
                if d in good_days:
                    return pd.DataFrame.from_dict(
                        {_SYM_A: {"종가": 10000, "거래대금": 100}}, orient="index"
                    )
                return pd.DataFrame()  # 빈 DataFrame

            m.stock.get_market_ohlcv_by_ticker.side_effect = _get
            return m

        with pytest.raises(RuntimeError, match="excessive_failures"):
            build_ranking(
                start=ten_days[0],
                end=ten_days[-1],
                universe_yaml=universe_yaml,
                output_csv=output_csv,
                pykrx_factory=_factory,
                calendar=cal,
            )

    def test_C11_universe_yaml_누락_UniverseLoadError_전파(self, tmp_path: Path):
        """universe_yaml 이 존재하지 않으면 UniverseLoadError 가 그대로 전파된다."""
        _require_module()
        missing_yaml = tmp_path / "nonexistent_universe.yaml"
        output_csv = tmp_path / "ranking.csv"
        cal = _make_calendar_stub({date(2026, 1, 2)})

        import pandas as pd

        def _factory():
            m = MagicMock()
            m.stock.get_market_ohlcv_by_ticker.return_value = pd.DataFrame()
            return m

        with pytest.raises(UniverseLoadError):
            build_ranking(
                start=date(2026, 1, 2),
                end=date(2026, 1, 2),
                universe_yaml=missing_yaml,
                output_csv=output_csv,
                pykrx_factory=_factory,
                calendar=cal,
            )


# ===========================================================================
# D. CLI main()
# ===========================================================================


class TestMainCli:
    """D-12 ~ D-14: CLI main() exit code 검증."""

    def _patch_build_ranking(self, monkeypatch, *, side_effect=None, return_value=None):
        """build_ranking 을 monkeypatch 로 대체."""
        _require_module()
        if side_effect is not None:
            mock = MagicMock(side_effect=side_effect)
        else:
            mock = MagicMock(return_value=return_value)
        monkeypatch.setattr(liquidity_cli, "build_ranking", mock)
        return mock

    def test_D12_정상_1영업일_CSV_생성_return_0(self, tmp_path: Path, monkeypatch):
        """정상 경로 — 1영업일 mock pykrx → CSV 생성 + return 0."""
        _require_module()

        universe_yaml = _make_mini_universe_yaml(tmp_path, [_SYM_A])
        output_csv = tmp_path / "ranking.csv"

        # build_ranking 을 실제 호출이 아닌 mock 으로 교체
        # (main 이 build_ranking 을 올바르게 호출하는지만 검증)
        mock_br = self._patch_build_ranking(monkeypatch)

        result = main(
            [
                "--start=2026-01-02",
                "--end=2026-01-02",
                f"--universe-yaml={universe_yaml}",
                f"--output-csv={output_csv}",
            ]
        )

        assert result == 0
        mock_br.assert_called_once()

    def test_D13_입력_오류_start_after_end_return_2(self, tmp_path: Path, monkeypatch):
        """start > end → return 2."""
        _require_module()
        universe_yaml = _make_mini_universe_yaml(tmp_path, [_SYM_A])
        output_csv = tmp_path / "ranking.csv"

        # build_ranking 이 RuntimeError 를 raise 하도록
        self._patch_build_ranking(monkeypatch, side_effect=RuntimeError("start > end"))

        result = main(
            [
                "--start=2026-01-10",
                "--end=2026-01-05",
                f"--universe-yaml={universe_yaml}",
                f"--output-csv={output_csv}",
            ]
        )

        assert result == 2

    def test_D14_IO_오류_readonly_디렉터리_return_3(self, tmp_path: Path, monkeypatch):
        """output_csv 부모 디렉터리가 쓰기 불가 → return 3."""
        _require_module()
        universe_yaml = _make_mini_universe_yaml(tmp_path, [_SYM_A])
        output_csv = tmp_path / "ranking.csv"

        # build_ranking 이 OSError/PermissionError 를 raise 하도록
        self._patch_build_ranking(monkeypatch, side_effect=OSError("Permission denied"))

        result = main(
            [
                "--start=2026-01-02",
                "--end=2026-01-02",
                f"--universe-yaml={universe_yaml}",
                f"--output-csv={output_csv}",
            ]
        )

        assert result == 3
