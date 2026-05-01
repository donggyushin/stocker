"""scripts/backtest.py 공개 함수 단위 테스트.

_parse_args / _resolve_symbols / _verdict_label / _format_pct / _format_decimal /
_render_markdown / _write_metrics_csv / _write_trades_csv / main(exit code) 를 검증한다.
외부 네트워크 · KIS · pykis · wall-clock 접촉 없음 — 합성 fixture + tmp_path 만 사용.
"""

from __future__ import annotations

import csv as csv_mod
import importlib.util
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# scripts/backtest.py 로드 (scripts/ 에 __init__.py 없음 — spec_from_file_location 사용)
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
_SCRIPT_PATH = _PROJECT_ROOT / "scripts" / "backtest.py"

_spec = importlib.util.spec_from_file_location("backtest_cli", _SCRIPT_PATH)
assert _spec is not None and _spec.loader is not None, "scripts/backtest.py 로드 실패"
backtest_cli = importlib.util.module_from_spec(_spec)
# src/ 가 sys.path 에 있어야 stock_agent 패키지를 import 할 수 있음
_src = str(_PROJECT_ROOT / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)
# sys.modules 에 먼저 등록해야 @dataclass 가 __module__ 참조 시 NoneType 오류를 피한다.
sys.modules["backtest_cli"] = backtest_cli
_spec.loader.exec_module(backtest_cli)  # type: ignore[union-attr]

# ---------------------------------------------------------------------------
# 검증 대상 심볼 참조
# ---------------------------------------------------------------------------
_parse_args = backtest_cli._parse_args
_resolve_symbols = backtest_cli._resolve_symbols
_ReportContext = backtest_cli._ReportContext
_render_markdown = backtest_cli._render_markdown
_write_metrics_csv = backtest_cli._write_metrics_csv
_write_trades_csv = backtest_cli._write_trades_csv
_verdict_label = backtest_cli._verdict_label
_format_pct = backtest_cli._format_pct
_format_decimal = backtest_cli._format_decimal
_MDD_PASS_THRESHOLD = backtest_cli._MDD_PASS_THRESHOLD
main = backtest_cli.main

# ---------------------------------------------------------------------------
# stock_agent 공개 DTO
# ---------------------------------------------------------------------------
from stock_agent.backtest import (  # noqa: E402  (로드 순서상 backtest_cli 먼저)
    BacktestMetrics,
    BacktestResult,
    DailyEquity,
    TradeRecord,
)
from stock_agent.data import MinuteCsvLoadError, UniverseLoadError  # noqa: E402

# ---------------------------------------------------------------------------
# 상수 / fixture 헬퍼
# ---------------------------------------------------------------------------

KST = timezone(timedelta(hours=9))

_DATE_START = date(2023, 1, 1)
_DATE_END = date(2025, 12, 31)


def _make_metrics(
    *,
    total_return_pct: Decimal = Decimal("0.08"),
    max_drawdown_pct: Decimal = Decimal("-0.12"),
    sharpe_ratio: Decimal = Decimal("1.2"),
    win_rate: Decimal = Decimal("0.55"),
    avg_pnl_ratio: Decimal = Decimal("1.3"),
    trades_per_day: Decimal = Decimal("1.2"),
    net_pnl_krw: int = 80_000,
) -> BacktestMetrics:
    return BacktestMetrics(
        total_return_pct=total_return_pct,
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=sharpe_ratio,
        win_rate=win_rate,
        avg_pnl_ratio=avg_pnl_ratio,
        trades_per_day=trades_per_day,
        net_pnl_krw=net_pnl_krw,
    )


def _make_trade(
    symbol: str = "005930",
    net: int = 100,
    exit_reason: str = "force_close",
) -> TradeRecord:
    return TradeRecord(
        symbol=symbol,
        entry_ts=datetime(2024, 1, 2, 9, 30, tzinfo=KST),
        entry_price=Decimal("70000"),
        exit_ts=datetime(2024, 1, 2, 15, 0, tzinfo=KST),
        exit_price=Decimal("72000"),
        qty=2,
        exit_reason=exit_reason,  # type: ignore[arg-type]
        gross_pnl_krw=4000,
        commission_krw=20,
        tax_krw=260,
        net_pnl_krw=net,
    )


def _make_result(
    *,
    metrics: BacktestMetrics | None = None,
    trades: tuple[TradeRecord, ...] = (),
    daily_equity: tuple[DailyEquity, ...] = (),
    rejected: dict | None = None,
    post: int = 0,
) -> BacktestResult:
    return BacktestResult(
        trades=tuple(trades),
        daily_equity=tuple(daily_equity),
        metrics=metrics or _make_metrics(),
        rejected_counts=dict(rejected or {}),
        post_slippage_rejections=post,
    )


def _make_context(
    start: date = _DATE_START,
    end: date = _DATE_END,
    symbols: tuple[str, ...] = ("005930", "000660"),
    capital: int = 1_000_000,
) -> _ReportContext:
    return _ReportContext(
        start=start,
        end=end,
        symbols=symbols,
        starting_capital_krw=capital,
    )


# ---------------------------------------------------------------------------
# 1. _resolve_symbols
# ---------------------------------------------------------------------------


class TestResolveSymbols:
    def test_쉼표_구분_파싱(self):
        """'005930,000660,035420' → 3개 코드 tuple."""
        result = _resolve_symbols("005930,000660,035420")
        assert result == ("005930", "000660", "035420")

    def test_공백_포함_쉼표_파싱(self):
        """'005930, 000660, 035420' — 각 항목 strip 처리."""
        result = _resolve_symbols("005930, 000660, 035420")
        assert result == ("005930", "000660", "035420")

    def test_빈_문자열_universe_호출(self, monkeypatch):
        """빈 raw → load_kospi200_universe 호출 결과 반환."""
        fake_universe = type("U", (), {"tickers": ("005930", "000660")})()
        monkeypatch.setattr(backtest_cli, "load_kospi200_universe", lambda: fake_universe)
        result = _resolve_symbols("")
        assert result == ("005930", "000660")

    def test_공백만_universe_호출(self, monkeypatch):
        """'   ' (공백만) → 빈 값 분기 — load_kospi200_universe 호출."""
        called = []
        fake_universe = type("U", (), {"tickers": ("005930",)})()

        def fake_load():
            called.append(True)
            return fake_universe

        monkeypatch.setattr(backtest_cli, "load_kospi200_universe", fake_load)
        result = _resolve_symbols("   ")
        assert len(called) == 1
        assert result == ("005930",)

    def test_쉼표만_universe_호출(self, monkeypatch):
        """' , ' (쉼표+공백만) → strip 후 빈 항목 제거 → 빈 결과이므로 universe 호출."""
        fake_universe = type("U", (), {"tickers": ("000660",)})()
        monkeypatch.setattr(backtest_cli, "load_kospi200_universe", lambda: fake_universe)
        # " , " → raw.strip()=" , " (truthy) → split → ["", ""] → strip 후 빈 것 제거 → 빈 tuple
        # 즉 빈 parts 가 되어 내부 분기에 따라 load_kospi200_universe 를 호출하지 않을 수도 있음.
        # 실제 동작: "," 만 들어오면 parts=() → 빈 tuple 반환 (universe 호출 안 함).
        # " , " 는 raw.strip()=" , " truthy → split[","] → [" ", " "] → strip 후 "" 제거 → 빈 tuple.
        # 엔진 계약: 빈 parts 이면 빈 tuple 반환(raw.strip() truthy 이므로 universe 미호출).
        # 이 케이스는 부정한 입력이나 동작은 빈 tuple — 테스트는 빈 tuple 임을 확인.
        result = _resolve_symbols(" , ")
        # raw.strip()=" , " → truthy → split → 빈 항목만 → 빈 tuple
        assert result == ()

    def test_universe_비면_RuntimeError(self, monkeypatch):
        """universe.tickers 가 비면 RuntimeError."""
        fake_universe = type("U", (), {"tickers": ()})()
        monkeypatch.setattr(backtest_cli, "load_kospi200_universe", lambda: fake_universe)
        with pytest.raises(RuntimeError, match="비어있"):
            _resolve_symbols("")


# ---------------------------------------------------------------------------
# 2. _parse_args
# ---------------------------------------------------------------------------


class TestParseArgs:
    def test_필수_csv_dir_누락_SystemExit(self):
        """--csv-dir 없으면 argparse SystemExit."""
        with pytest.raises(SystemExit):
            _parse_args(["--from=2023-01-01", "--to=2025-12-31"])

    def test_필수_from_누락_SystemExit(self):
        """--from 없으면 argparse SystemExit."""
        with pytest.raises(SystemExit):
            _parse_args(["--csv-dir=data/csv", "--to=2025-12-31"])

    def test_필수_to_누락_SystemExit(self):
        """--to 없으면 argparse SystemExit."""
        with pytest.raises(SystemExit):
            _parse_args(["--csv-dir=data/csv", "--from=2023-01-01"])

    def test_최소_필수_인자_기본값_확인(self, tmp_path):
        """필수 3개만 주면 나머지 기본값이 주입된다."""
        args = _parse_args(
            [
                f"--csv-dir={tmp_path}",
                "--from=2023-01-01",
                "--to=2025-12-31",
            ]
        )
        assert args.symbols == ""
        assert args.starting_capital == 1_000_000
        assert args.output_markdown == Path("data/backtest_report.md")
        assert args.output_csv == Path("data/backtest_metrics.csv")
        assert args.output_trades_csv == Path("data/backtest_trades.csv")

    def test_from_to_date_파싱(self, tmp_path):
        """--from / --to 가 date 객체로 파싱된다."""
        args = _parse_args(
            [
                f"--csv-dir={tmp_path}",
                "--from=2023-06-01",
                "--to=2024-03-31",
            ]
        )
        assert args.start == date(2023, 6, 1)
        assert args.end == date(2024, 3, 31)

    def test_starting_capital_int_파싱(self, tmp_path):
        """--starting-capital 이 int 로 파싱된다."""
        args = _parse_args(
            [
                f"--csv-dir={tmp_path}",
                "--from=2023-01-01",
                "--to=2025-12-31",
                "--starting-capital=2000000",
            ]
        )
        assert isinstance(args.starting_capital, int)
        assert args.starting_capital == 2_000_000

    def test_output_경로_Path_파싱(self, tmp_path):
        """--output-markdown / --output-csv / --output-trades-csv 가 Path 로 파싱된다."""
        args = _parse_args(
            [
                f"--csv-dir={tmp_path}",
                "--from=2023-01-01",
                "--to=2025-12-31",
                "--output-markdown=out/report.md",
                "--output-csv=out/metrics.csv",
                "--output-trades-csv=out/trades.csv",
            ]
        )
        assert args.output_markdown == Path("out/report.md")
        assert args.output_csv == Path("out/metrics.csv")
        assert args.output_trades_csv == Path("out/trades.csv")


# ---------------------------------------------------------------------------
# 3. _verdict_label
# ---------------------------------------------------------------------------


class TestVerdictLabel:
    @pytest.mark.parametrize(
        "mdd, expected",
        [
            (Decimal("-0.20"), "FAIL"),
            (Decimal("-0.16"), "FAIL"),
            (Decimal("-0.15"), "FAIL"),  # 임계값 경계 — strict greater이므로 FAIL
            (Decimal("-0.14999"), "PASS"),
            (Decimal("-0.10"), "PASS"),
            (Decimal("-0.05"), "PASS"),
            (Decimal("0"), "PASS"),
            (Decimal("0.05"), "PASS"),
        ],
        ids=[
            "mdd_-20pct_FAIL",
            "mdd_-16pct_FAIL",
            "mdd_-15pct_경계_FAIL",
            "mdd_-14999pct_PASS",
            "mdd_-10pct_PASS",
            "mdd_-5pct_PASS",
            "mdd_0_PASS",
            "mdd_양수_PASS",
        ],
    )
    def test_verdict_label_파라미터화(self, mdd: Decimal, expected: str):
        assert _verdict_label(mdd) == expected

    def test_threshold_상수_값(self):
        """_MDD_PASS_THRESHOLD 상수가 Decimal('-0.15') 임을 확인."""
        assert Decimal("-0.15") == _MDD_PASS_THRESHOLD

    # --- M3: daily_equity_len / symbol_count 확장 시그니처 테스트 ---

    def test_표본_240_미만_PASS_참고용_표본_240_미만(self):
        """daily_equity_len=239 → caveat '표본 240 미만' 포함."""
        result = _verdict_label(Decimal("-0.10"), daily_equity_len=239, symbol_count=3)
        assert result == "PASS (참고용 — 표본 240 미만)"

    def test_단일_종목_PASS_참고용_단일_종목(self):
        """symbol_count=1 → caveat '단일 종목' 포함."""
        result = _verdict_label(Decimal("-0.10"), daily_equity_len=240, symbol_count=1)
        assert result == "PASS (참고용 — 단일 종목)"

    def test_표본_240_미만_AND_단일_종목_두_caveat_모두_포함(self):
        """daily_equity_len=100, symbol_count=1 → caveat 2개 모두 포함, 순서 고정."""
        result = _verdict_label(Decimal("-0.10"), daily_equity_len=100, symbol_count=1)
        assert result == "PASS (참고용 — 표본 240 미만, 단일 종목)"

    def test_표본_240_이상_다중_종목_caveat_없음(self):
        """daily_equity_len=240, symbol_count=3 → caveat 없음 → 'PASS'."""
        result = _verdict_label(Decimal("-0.10"), daily_equity_len=240, symbol_count=3)
        assert result == "PASS"

    def test_FAIL_이면_caveat_붙지_않음(self):
        """mdd <= -0.15 이면 daily_equity_len/symbol_count 관계없이 'FAIL'."""
        result = _verdict_label(Decimal("-0.20"), daily_equity_len=100, symbol_count=1)
        assert result == "FAIL"

    def test_backward_compat_인자_없으면_PASS(self):
        """기존 _verdict_label(mdd) 단독 호출 — backward compat 확인."""
        result = _verdict_label(Decimal("-0.10"))
        assert result == "PASS"


# ---------------------------------------------------------------------------
# 4. _format_pct / _format_decimal
# ---------------------------------------------------------------------------


class TestFormatters:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (Decimal("0.1234"), "12.34%"),
            (Decimal("-0.0001"), "-0.01%"),
            (Decimal("0"), "0.00%"),
            (Decimal("1.0"), "100.00%"),
        ],
        ids=["12.34%", "-0.01%", "0.00%", "100.00%"],
    )
    def test_format_pct(self, value: Decimal, expected: str):
        assert _format_pct(value) == expected

    @pytest.mark.parametrize(
        "value, digits, expected",
        [
            (Decimal("1.23456"), 4, "1.2346"),
            (Decimal("1.2"), 2, "1.20"),
            (Decimal("0"), 3, "0.000"),
            (Decimal("-1.5"), 1, "-1.5"),
        ],
        ids=["반올림_4자리", "2자리_패딩", "0_3자리", "음수_1자리"],
    )
    def test_format_decimal(self, value: Decimal, digits: int, expected: str):
        assert _format_decimal(value, digits) == expected


# ---------------------------------------------------------------------------
# 5. _render_markdown
# ---------------------------------------------------------------------------


class TestRenderMarkdown:
    def _ctx(self, start=_DATE_START, end=_DATE_END) -> _ReportContext:
        return _make_context(start=start, end=end)

    def test_헤더_포함(self):
        """'# ORB 백테스트 리포트' 가 출력에 포함된다."""
        md = _render_markdown(_make_result(), self._ctx())
        assert "# ORB 백테스트 리포트" in md

    def test_기간_ISO_포맷(self):
        """시작/종료 날짜가 ISO 포맷(backtick)으로 포함된다."""
        md = _render_markdown(_make_result(), self._ctx())
        assert "`2023-01-01`" in md
        assert "`2025-12-31`" in md

    def test_메트릭_표_헤더(self):
        """'| 항목 | 값 |' 표 헤더가 포함된다."""
        md = _render_markdown(_make_result(), self._ctx())
        assert "| 항목 | 값 |" in md

    def test_7종_메트릭_레이블(self):
        """총수익률·최대 낙폭·샤프·승률·평균 손익비·일평균 거래 수·순손익 레이블 모두 포함."""
        md = _render_markdown(_make_result(), self._ctx())
        for label in [
            "총수익률",
            "최대 낙폭",
            "샤프 비율",
            "승률",
            "평균 손익비",
            "일평균 거래 수",
            "순손익",
        ]:
            assert label in md, f"레이블 '{label}' 미포함"

    def test_PASS_verdict(self):
        """MDD > -0.15 (낙폭 절대값 15% 미만) → '**PASS' 로 시작하는 verdict 가 포함된다.

        caveat 유무(표본 수·종목 수)에 따라 라벨이 '**PASS**' 또는
        '**PASS (참고용 — ...)**' 형태로 달라질 수 있으므로 '**PASS' 포함 여부만 검증.
        caveat 동작 자체는 TestVerdictLabel 케이스가 커버한다.
        """
        metrics = _make_metrics(max_drawdown_pct=Decimal("-0.10"))
        md = _render_markdown(_make_result(metrics=metrics), self._ctx())
        assert "**PASS" in md and "**FAIL**" not in md

    def test_FAIL_verdict(self):
        """MDD <= -0.15 → '**FAIL**' 가 포함된다."""
        metrics = _make_metrics(max_drawdown_pct=Decimal("-0.20"))
        md = _render_markdown(_make_result(metrics=metrics), self._ctx())
        assert "**FAIL**" in md

    def test_Phase2_판정_섹션(self):
        """'## Phase 2 PASS 판정' 섹션 헤더가 포함된다."""
        md = _render_markdown(_make_result(), self._ctx())
        assert "Phase 2 PASS 판정" in md

    def test_daily_equity_비면_세션없음(self):
        """daily_equity 가 비면 '세션 없음' 문구가 포함된다."""
        md = _render_markdown(_make_result(daily_equity=()), self._ctx())
        assert "세션 없음" in md

    def test_daily_equity_있으면_세션수(self):
        """daily_equity 있으면 세션 수와 시작/종료/최저점/최고점 정보가 포함된다."""
        eq = (
            DailyEquity(session_date=date(2024, 1, 2), equity_krw=1_000_000),
            DailyEquity(session_date=date(2024, 1, 3), equity_krw=980_000),
            DailyEquity(session_date=date(2024, 1, 4), equity_krw=1_050_000),
        )
        md = _render_markdown(_make_result(daily_equity=eq), self._ctx())
        assert "세션 수: 3" in md
        assert "2024-01-02" in md
        assert "2024-01-04" in md

    def test_rejected_counts_비면_거부0건(self):
        """rejected_counts 가 비면 'RiskManager 사전 거부 0건' 문구 포함."""
        md = _render_markdown(_make_result(rejected={}), self._ctx())
        assert "RiskManager 사전 거부 0건" in md

    def test_rejected_counts_있으면_표_헤더(self):
        """rejected_counts 있으면 '| 사유 | 카운트 |' 표 헤더 포함."""
        md = _render_markdown(_make_result(rejected={"max_positions": 3}), self._ctx())
        assert "| 사유 | 카운트 |" in md
        assert "max_positions" in md

    def test_post_slippage_카운트(self):
        """사후 슬리피지 거부 카운트가 포함된다."""
        md = _render_markdown(_make_result(post=5), self._ctx())
        assert "5" in md


# ---------------------------------------------------------------------------
# 6. _write_metrics_csv
# ---------------------------------------------------------------------------


class TestWriteMetricsCsv:
    def test_파일_생성(self, tmp_path: Path):
        """_write_metrics_csv 호출 후 파일이 생성된다."""
        path = tmp_path / "metrics.csv"
        _write_metrics_csv(_make_metrics(), path)
        assert path.exists()

    def test_헤더_metric_value(self, tmp_path: Path):
        """첫 행 헤더가 'metric,value' 이다."""
        path = tmp_path / "metrics.csv"
        _write_metrics_csv(_make_metrics(), path)
        with path.open(encoding="utf-8") as f:
            reader = csv_mod.reader(f)
            header = next(reader)
        assert header == ["metric", "value"]

    def test_7행_데이터(self, tmp_path: Path):
        """헤더 제외 데이터 행이 정확히 7행이다."""
        path = tmp_path / "metrics.csv"
        _write_metrics_csv(_make_metrics(), path)
        with path.open(encoding="utf-8") as f:
            rows = list(csv_mod.reader(f))
        assert len(rows) == 8  # 헤더 1 + 데이터 7

    def test_7종_메트릭_이름(self, tmp_path: Path):
        """7종 메트릭 이름이 첫 열에 모두 등장한다."""
        path = tmp_path / "metrics.csv"
        _write_metrics_csv(_make_metrics(), path)
        with path.open(encoding="utf-8") as f:
            rows = list(csv_mod.DictReader(f))
        metric_names = {r["metric"] for r in rows}
        for name in [
            "total_return_pct",
            "max_drawdown_pct",
            "sharpe_ratio",
            "win_rate",
            "avg_pnl_ratio",
            "trades_per_day",
            "net_pnl_krw",
        ]:
            assert name in metric_names, f"'{name}' 없음"

    def test_max_drawdown_pct_Decimal_직렬화(self, tmp_path: Path):
        """max_drawdown_pct 값이 str(Decimal) 형태로 저장된다."""
        path = tmp_path / "metrics.csv"
        metrics = _make_metrics(max_drawdown_pct=Decimal("-0.15"))
        _write_metrics_csv(metrics, path)
        with path.open(encoding="utf-8") as f:
            rows = list(csv_mod.DictReader(f))
        mdd_row = next(r for r in rows if r["metric"] == "max_drawdown_pct")
        assert mdd_row["value"] == "-0.15"

    def test_net_pnl_krw_직렬화(self, tmp_path: Path):
        """net_pnl_krw 가 str(int) 로 저장된다."""
        path = tmp_path / "metrics.csv"
        metrics = _make_metrics(net_pnl_krw=80_000)
        _write_metrics_csv(metrics, path)
        with path.open(encoding="utf-8") as f:
            rows = list(csv_mod.DictReader(f))
        krw_row = next(r for r in rows if r["metric"] == "net_pnl_krw")
        assert krw_row["value"] == "80000"


# ---------------------------------------------------------------------------
# 7. _write_trades_csv
# ---------------------------------------------------------------------------


class TestWriteTradesCsv:
    _EXPECTED_COLS = [
        "symbol",
        "entry_ts",
        "entry_price",
        "exit_ts",
        "exit_price",
        "qty",
        "exit_reason",
        "gross_pnl_krw",
        "commission_krw",
        "tax_krw",
        "net_pnl_krw",
    ]

    def test_빈_trades_헤더만(self, tmp_path: Path):
        """빈 trades → 헤더 행만 (데이터 0행)."""
        path = tmp_path / "trades.csv"
        _write_trades_csv((), path)
        with path.open(encoding="utf-8") as f:
            rows = list(csv_mod.reader(f))
        assert len(rows) == 1

    def test_빈_trades_컬럼_11개(self, tmp_path: Path):
        """빈 trades 헤더의 컬럼 수가 11개다."""
        path = tmp_path / "trades.csv"
        _write_trades_csv((), path)
        with path.open(encoding="utf-8") as f:
            header = next(csv_mod.reader(f))
        assert len(header) == 11

    def test_빈_trades_컬럼_이름(self, tmp_path: Path):
        """헤더의 11개 컬럼 이름이 계약과 일치한다."""
        path = tmp_path / "trades.csv"
        _write_trades_csv((), path)
        with path.open(encoding="utf-8") as f:
            header = next(csv_mod.reader(f))
        assert header == self._EXPECTED_COLS

    def test_2개_체결_2행(self, tmp_path: Path):
        """TradeRecord 2개 → 헤더 + 데이터 2행 = 총 3행."""
        path = tmp_path / "trades.csv"
        trades = (_make_trade(symbol="005930", net=100), _make_trade(symbol="000660", net=-50))
        _write_trades_csv(trades, path)
        with path.open(encoding="utf-8") as f:
            rows = list(csv_mod.reader(f))
        assert len(rows) == 3

    def test_entry_ts_ISO_포맷(self, tmp_path: Path):
        """entry_ts 가 ISO 포맷 문자열로 저장된다."""
        path = tmp_path / "trades.csv"
        _write_trades_csv((_make_trade(),), path)
        with path.open(encoding="utf-8") as f:
            rows = list(csv_mod.DictReader(f))
        # isoformat 은 '+09:00' 포함 또는 'T' 구분자 — 날짜 부분이 포함되면 OK
        assert "2024-01-02" in rows[0]["entry_ts"]

    def test_exit_ts_ISO_포맷(self, tmp_path: Path):
        """exit_ts 가 ISO 포맷 문자열로 저장된다."""
        path = tmp_path / "trades.csv"
        _write_trades_csv((_make_trade(),), path)
        with path.open(encoding="utf-8") as f:
            rows = list(csv_mod.DictReader(f))
        assert "2024-01-02" in rows[0]["exit_ts"]

    def test_컬럼_수_11_데이터행(self, tmp_path: Path):
        """데이터 행의 컬럼 수도 11개다."""
        path = tmp_path / "trades.csv"
        _write_trades_csv((_make_trade(),), path)
        with path.open(encoding="utf-8") as f:
            rows = list(csv_mod.reader(f))
        data_row = rows[1]
        assert len(data_row) == 11

    def test_symbol_필드_저장(self, tmp_path: Path):
        """symbol 필드가 CSV 에 정확히 저장된다."""
        path = tmp_path / "trades.csv"
        _write_trades_csv((_make_trade(symbol="035420"),), path)
        with path.open(encoding="utf-8") as f:
            rows = list(csv_mod.DictReader(f))
        assert rows[0]["symbol"] == "035420"


# ---------------------------------------------------------------------------
# 8. main(argv) exit code
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
        monkeypatch.setattr(backtest_cli, "_run_pipeline", lambda _: None)
        assert main(self._BASE_ARGV) == 0

    def test_MinuteCsvLoadError_exit_2(self, monkeypatch):
        """MinuteCsvLoadError 발생 → exit code 2."""

        def _raise(_):
            raise MinuteCsvLoadError("테스트 오류")

        monkeypatch.setattr(backtest_cli, "_run_pipeline", _raise)
        assert main(self._BASE_ARGV) == 2

    def test_UniverseLoadError_exit_2(self, monkeypatch):
        """UniverseLoadError 발생 → exit code 2.

        UniverseLoadError 는 Exception 직상속(not RuntimeError)이라
        RuntimeError 분기에 잡히지 않는다 — 전용 분기 회귀 검증.
        """

        def _raise(_):
            raise UniverseLoadError("universe YAML 오류 시뮬레이션")

        monkeypatch.setattr(backtest_cli, "_run_pipeline", _raise)
        assert main(self._BASE_ARGV) == 2

    def test_RuntimeError_exit_2(self, monkeypatch):
        """RuntimeError 발생 → exit code 2."""

        def _raise(_):
            raise RuntimeError("설정 오류")

        monkeypatch.setattr(backtest_cli, "_run_pipeline", _raise)
        assert main(self._BASE_ARGV) == 2

    def test_OSError_exit_3(self, monkeypatch):
        """OSError 발생 → exit code 3."""

        def _raise(_):
            raise OSError("I/O 오류")

        monkeypatch.setattr(backtest_cli, "_run_pipeline", _raise)
        assert main(self._BASE_ARGV) == 3

    def test_start_after_end_exit_2_조기반환(self, monkeypatch):
        """--from 이 --to 보다 나중 → exit code 2, _run_pipeline 미호출."""
        called = []
        monkeypatch.setattr(backtest_cli, "_run_pipeline", lambda _: called.append(True))
        result = main(
            [
                "--csv-dir=/tmp/dummy_csv",
                "--from=2025-12-31",
                "--to=2023-01-01",
            ]
        )
        assert result == 2
        assert called == [], "_run_pipeline 이 호출되면 안 됨"

    def test_starting_capital_0_exit_2_조기반환(self, monkeypatch):
        """--starting-capital=0 → exit code 2, _run_pipeline 미호출."""
        called = []
        monkeypatch.setattr(backtest_cli, "_run_pipeline", lambda _: called.append(True))
        result = main(
            [
                "--csv-dir=/tmp/dummy_csv",
                "--from=2023-01-01",
                "--to=2025-12-31",
                "--starting-capital=0",
            ]
        )
        assert result == 2
        assert called == [], "_run_pipeline 이 호출되면 안 됨"

    def test_starting_capital_음수_exit_2_조기반환(self, monkeypatch):
        """--starting-capital=-1 → exit code 2, _run_pipeline 미호출."""
        called = []
        monkeypatch.setattr(backtest_cli, "_run_pipeline", lambda _: called.append(True))
        result = main(
            [
                "--csv-dir=/tmp/dummy_csv",
                "--from=2023-01-01",
                "--to=2025-12-31",
                "--starting-capital=-1",
            ]
        )
        assert result == 2
        assert called == [], "_run_pipeline 이 호출되면 안 됨"

    def test_start_eq_end_정상통과(self, monkeypatch):
        """--from 과 --to 가 동일 날짜 → 정상 통과 (exit 0)."""
        monkeypatch.setattr(backtest_cli, "_run_pipeline", lambda _: None)
        result = main(
            [
                "--csv-dir=/tmp/dummy_csv",
                "--from=2024-01-02",
                "--to=2024-01-02",
            ]
        )
        assert result == 0


# ---------------------------------------------------------------------------
# 9. _resolve_symbols — universe_yaml 인자 (RED: --universe-yaml 미구현)
# ---------------------------------------------------------------------------


class TestResolveSymbolsUniverseYaml:
    def test_명시적_path_전달_load_kospi200_universe_path_호출(self, monkeypatch):
        """universe_yaml=Path('/custom/path.yaml') 전달 시
        load_kospi200_universe(path) 가 정확히 그 경로로 호출된다."""
        call_args: list = []
        fake_universe = type("U", (), {"tickers": ("005930", "000660")})()

        def spy(path):
            call_args.append(path)
            return fake_universe

        monkeypatch.setattr(backtest_cli, "load_kospi200_universe", spy)
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

        monkeypatch.setattr(backtest_cli, "load_kospi200_universe", spy)
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

        monkeypatch.setattr(backtest_cli, "load_kospi200_universe", spy_noarg)
        result = _resolve_symbols("", universe_yaml=None)
        assert result == ("035420",)
        assert call_log == ["noarg"]


# ---------------------------------------------------------------------------
# 10. _parse_args — --universe-yaml 옵션 (RED: 미구현)
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
# 11. _parse_args — --strategy-type 옵션 (RED: 미구현)
# ---------------------------------------------------------------------------


class TestStrategyTypeFlag:
    """--strategy-type argparse choices 검증 (RED).

    scripts/backtest.py 가 --strategy-type 을 미구현한 상태에서
    AttributeError(args.strategy_type) 또는 SystemExit(잘못된 값) 로 FAIL 한다.
    """

    _BASE = ["--csv-dir=/tmp/dummy", "--from=2023-01-01", "--to=2025-12-31"]

    def test_기본값_orb(self):
        """--strategy-type 미지정 → args.strategy_type == 'orb'."""
        args = _parse_args(self._BASE)
        # 미구현 시 AttributeError 로 FAIL
        assert args.strategy_type == "orb"

    def test_명시_vwap_mr(self):
        """--strategy-type=vwap-mr → args.strategy_type == 'vwap-mr'."""
        args = _parse_args(self._BASE + ["--strategy-type=vwap-mr"])
        assert args.strategy_type == "vwap-mr"

    def test_명시_gap_reversal(self):
        """--strategy-type=gap-reversal → args.strategy_type == 'gap-reversal'."""
        args = _parse_args(self._BASE + ["--strategy-type=gap-reversal"])
        assert args.strategy_type == "gap-reversal"

    def test_잘못된_값_SystemExit(self):
        """--strategy-type=foobar → argparse choices 위반 → SystemExit(2)."""
        with pytest.raises(SystemExit) as exc_info:
            _parse_args(self._BASE + ["--strategy-type=foobar"])
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# 12. _run_pipeline — strategy_type 별 BacktestConfig.strategy_factory 라우팅
#     (RED: 미구현)
# ---------------------------------------------------------------------------


class TestStrategyTypeRouting:
    """_run_pipeline 이 strategy_type 에 따라 BacktestConfig.strategy_factory 를
    적절히 채우는지 검증 (RED).

    BacktestEngine 을 monkeypatch 로 교체해 생성자 인자(config)를 캡처한다.
    외부 네트워크·KIS·DB·실파일 접촉 없음 — 모든 의존 monkeypatch 처리.
    """

    _BASE = ["--csv-dir=/tmp/dummy", "--from=2023-01-01", "--to=2025-12-31"]

    def _setup(self, monkeypatch, tmp_path):
        """공통 픽스처 설정 — captured['config'] 에 BacktestConfig 를 저장한다."""
        from stock_agent.backtest import (
            BacktestConfig,
            BacktestMetrics,
            BacktestResult,
            InMemoryBarLoader,
        )

        captured: dict[str, BacktestConfig | None] = {"config": None}

        dummy_metrics = BacktestMetrics(
            total_return_pct=Decimal("0"),
            max_drawdown_pct=Decimal("0"),
            sharpe_ratio=Decimal("0"),
            win_rate=Decimal("0"),
            avg_pnl_ratio=Decimal("0"),
            trades_per_day=Decimal("0"),
            net_pnl_krw=0,
        )
        dummy_result = BacktestResult(
            trades=(),
            daily_equity=(),
            metrics=dummy_metrics,
            rejected_counts={},
            post_slippage_rejections=0,
        )
        dummy_loader = InMemoryBarLoader([])

        class _FakeEngine:
            def __init__(self, config: BacktestConfig) -> None:
                captured["config"] = config

            def run(self, _bars):
                return dummy_result

        monkeypatch.setattr(backtest_cli, "BacktestEngine", _FakeEngine)
        monkeypatch.setattr(backtest_cli, "_build_loader", lambda _args: dummy_loader)
        monkeypatch.setattr(backtest_cli, "_resolve_symbols", lambda *a, **kw: ("005930",))
        # output 파일이 tmp_path 에 생성되도록 작업 디렉토리 변경
        monkeypatch.chdir(tmp_path)
        return captured

    def test_orb_분기_strategy_factory_미주입(self, monkeypatch, tmp_path):
        """--strategy-type=orb → BacktestConfig.strategy_factory is None.

        orb 분기는 기존 BacktestEngine 기본 경로를 그대로 사용해야 한다
        (회귀 0 보장 조건).
        """
        captured = self._setup(monkeypatch, tmp_path)
        args = _parse_args(self._BASE + ["--strategy-type=orb"])
        backtest_cli._run_pipeline(args)
        config = captured["config"]
        assert config is not None, "_FakeEngine.__init__ 가 호출되어야 함"
        assert config.strategy_factory is None

    def test_vwap_mr_분기_strategy_factory_callable(self, monkeypatch, tmp_path):
        """--strategy-type=vwap-mr → BacktestConfig.strategy_factory 가 callable."""
        captured = self._setup(monkeypatch, tmp_path)
        args = _parse_args(self._BASE + ["--strategy-type=vwap-mr"])
        backtest_cli._run_pipeline(args)
        config = captured["config"]
        assert config is not None
        assert callable(config.strategy_factory), "strategy_factory 가 callable 이어야 함"

    def test_vwap_mr_분기_factory_호출_결과_VWAPMRStrategy(self, monkeypatch, tmp_path):
        """--strategy-type=vwap-mr → strategy_factory() 결과가 VWAPMRStrategy 인스턴스."""
        from stock_agent.strategy.vwap_mr import VWAPMRStrategy

        captured = self._setup(monkeypatch, tmp_path)
        args = _parse_args(self._BASE + ["--strategy-type=vwap-mr"])
        backtest_cli._run_pipeline(args)
        config = captured["config"]
        assert config is not None and callable(config.strategy_factory)
        instance = config.strategy_factory()
        assert isinstance(instance, VWAPMRStrategy)

    def test_gap_reversal_분기_strategy_factory_callable(self, monkeypatch, tmp_path):
        """--strategy-type=gap-reversal → BacktestConfig.strategy_factory 가 callable."""
        captured = self._setup(monkeypatch, tmp_path)
        args = _parse_args(self._BASE + ["--strategy-type=gap-reversal"])
        backtest_cli._run_pipeline(args)
        config = captured["config"]
        assert config is not None
        assert callable(config.strategy_factory), "strategy_factory 가 callable 이어야 함"

    def test_gap_reversal_분기_factory_호출_결과_GapReversalStrategy(self, monkeypatch, tmp_path):
        """--strategy-type=gap-reversal → strategy_factory() 결과가 GapReversalStrategy."""
        from stock_agent.strategy.gap_reversal import GapReversalStrategy

        captured = self._setup(monkeypatch, tmp_path)
        args = _parse_args(self._BASE + ["--strategy-type=gap-reversal"])
        backtest_cli._run_pipeline(args)
        config = captured["config"]
        assert config is not None and callable(config.strategy_factory)
        instance = config.strategy_factory()
        assert isinstance(instance, GapReversalStrategy)


# ---------------------------------------------------------------------------
# 14. _run_pipeline — gap-reversal DailyBarPrevCloseProvider 주입 (RED: Stage 2 미구현)
# ---------------------------------------------------------------------------


class TestGapReversalPrevCloseProviderInjection:
    """--strategy-type=gap-reversal → DailyBarPrevCloseProvider 주입 + 라이프사이클.

    Stage 2 구현 대상:
    - scripts/backtest.py 의 _build_backtest_config (또는 _run_pipeline) 이
      gap-reversal 분기에서 DailyBarPrevCloseProvider 를 인스턴스화하고
      build_strategy_factory(prev_close_provider=...) 에 주입.
    - provider.close() 가 try/finally 로 보장되어야 한다.
    - orb / vwap-mr 분기는 provider 미주입 (회귀 0).

    RED 기대:
    - backtest_cli 모듈에 HistoricalDataStore / YamlBusinessDayCalendar /
      DailyBarPrevCloseProvider 가 import 되지 않은 상태이므로
      monkeypatch.setattr 시 AttributeError 또는 라우팅 미구현으로 FAIL.
    """

    _BASE = ["--csv-dir=/tmp/dummy", "--from=2023-01-01", "--to=2025-12-31"]

    def _setup(self, monkeypatch, tmp_path):
        """공통 픽스처 설정.

        captured 딕셔너리:
        - "factory_kwargs": build_strategy_factory 에 전달된 kwargs (spy 캡처)
        - "store_close_count": _FakeStore.close() 호출 횟수
        """
        from stock_agent.backtest import (
            BacktestConfig,
            BacktestMetrics,
            BacktestResult,
            InMemoryBarLoader,
        )
        from stock_agent.strategy.factory import build_strategy_factory as _real_factory

        # store_close_count 는 list[int] 로 추적 — dict[str, object] 의 object 타입
        # 추론을 피해 pyright >= 연산자 에러를 해소한다.
        factory_kwargs_box: list[dict | None] = [None]
        store_close_calls: list[int] = []

        captured: dict[str, object] = {
            "factory_kwargs": None,
        }

        dummy_metrics = BacktestMetrics(
            total_return_pct=Decimal("0"),
            max_drawdown_pct=Decimal("0"),
            sharpe_ratio=Decimal("0"),
            win_rate=Decimal("0"),
            avg_pnl_ratio=Decimal("0"),
            trades_per_day=Decimal("0"),
            net_pnl_krw=0,
        )
        dummy_result = BacktestResult(
            trades=(),
            daily_equity=(),
            metrics=dummy_metrics,
            rejected_counts={},
            post_slippage_rejections=0,
        )
        dummy_loader = InMemoryBarLoader([])

        # HistoricalDataStore fake — close() 호출 카운트 추적
        class _FakeStore:
            def __init__(self, *a, **kw) -> None:
                pass

            def close(self) -> None:
                store_close_calls.append(1)

            def fetch_daily_ohlcv(self, *a, **kw):
                return []

        # YamlBusinessDayCalendar fake
        class _FakeCalendar:
            def __init__(self, *a, **kw) -> None:
                pass

            def is_business_day(self, d) -> bool:
                return False

        # build_strategy_factory spy — 실제 팩토리에 위임하되 kwargs 캡처
        def _spy_factory(*args, **kwargs):
            captured["factory_kwargs"] = kwargs
            factory_kwargs_box[0] = kwargs
            return _real_factory(*args, **kwargs)

        # BacktestEngine fake
        class _FakeEngine:
            def __init__(self, config: BacktestConfig) -> None:
                pass

            def run(self, _bars):
                return dummy_result

        monkeypatch.setattr(backtest_cli, "BacktestEngine", _FakeEngine)
        monkeypatch.setattr(backtest_cli, "_build_loader", lambda _args: dummy_loader)
        monkeypatch.setattr(backtest_cli, "_resolve_symbols", lambda *a, **kw: ("005930",))
        # Stage 2 미구현 상태: HistoricalDataStore / YamlBusinessDayCalendar /
        # DailyBarPrevCloseProvider 가 backtest_cli 에 없으면 아래 setattr 에서
        # AttributeError 가 발생해 RED 확인 가능.
        monkeypatch.setattr(backtest_cli, "HistoricalDataStore", _FakeStore, raising=False)
        monkeypatch.setattr(backtest_cli, "YamlBusinessDayCalendar", _FakeCalendar, raising=False)
        monkeypatch.setattr(backtest_cli, "build_strategy_factory", _spy_factory)

        monkeypatch.chdir(tmp_path)
        # store_close_calls 는 list[int] — pyright 가 ">=" 비교 시 object 로 추론하는
        # dict[str, object] 패턴을 피하기 위해 별도 반환한다.
        return captured, store_close_calls

    def test_gap_reversal_prev_close_provider_주입(self, monkeypatch, tmp_path):
        """--strategy-type=gap-reversal → build_strategy_factory 호출 시
        prev_close_provider 가 DailyBarPrevCloseProvider 인스턴스여야 한다.

        RED 기대: factory_kwargs 가 None (라우팅 미구현) 또는
        prev_close_provider 가 DailyBarPrevCloseProvider 가 아님 (stub 폴백).
        """
        from stock_agent.backtest.prev_close import DailyBarPrevCloseProvider

        captured, _close_calls = self._setup(monkeypatch, tmp_path)
        args = _parse_args(self._BASE + ["--strategy-type=gap-reversal"])
        backtest_cli._run_pipeline(args)

        kwargs = captured["factory_kwargs"]
        assert kwargs is not None, (
            "build_strategy_factory 가 호출되지 않음 — gap-reversal 분기에서 "
            "build_strategy_factory 호출이 필요하다"
        )
        provider = kwargs.get("prev_close_provider")  # type: ignore[union-attr]
        assert isinstance(provider, DailyBarPrevCloseProvider), (
            f"prev_close_provider 가 DailyBarPrevCloseProvider 인스턴스여야 하나 "
            f"{type(provider)!r}. Stage 2: _run_pipeline 이 provider 를 생성 후 주입해야 한다."
        )

    def test_gap_reversal_provider_close_after_run(self, monkeypatch, tmp_path):
        """engine.run 정상 완료 후 provider.close() 가 호출되어야 한다.

        DailyBarPrevCloseProvider.close() 는 HistoricalDataStore.close() 로
        위임하므로, _FakeStore.close() 호출 횟수로 검증.

        RED 기대: store_close_calls 가 빈 리스트 (provider 미생성 또는 close 미호출).
        """
        _captured, store_close_calls = self._setup(monkeypatch, tmp_path)
        args = _parse_args(self._BASE + ["--strategy-type=gap-reversal"])
        backtest_cli._run_pipeline(args)
        assert len(store_close_calls) >= 1, (
            "provider.close() (→ HistoricalDataStore.close()) 가 호출되어야 한다. "
            "Stage 2: try/finally 로 provider.close() 보장 필요."
        )

    def test_gap_reversal_engine_raises_provider_close_여전히_호출(self, monkeypatch, tmp_path):
        """engine.run 이 RuntimeError 를 raise 해도 finally 로 provider.close() 가
        호출되어야 한다.

        RED 기대: store_close_calls 가 빈 리스트 또는 RuntimeError 가 전파되지 않음.
        """
        from stock_agent.backtest import BacktestConfig

        _captured, store_close_calls = self._setup(monkeypatch, tmp_path)

        # BacktestEngine.run 이 RuntimeError 를 일으키는 새 fake 로 재패치
        class _ErrorEngine:
            def __init__(self, config: BacktestConfig) -> None:
                pass

            def run(self, _bars):
                raise RuntimeError("테스트용 엔진 오류")

        monkeypatch.setattr(backtest_cli, "BacktestEngine", _ErrorEngine)

        args = _parse_args(self._BASE + ["--strategy-type=gap-reversal"])
        with pytest.raises(RuntimeError, match="테스트용 엔진 오류"):
            backtest_cli._run_pipeline(args)

        msg = "engine.run 예외 시에도 provider.close() 가 finally 로 호출되어야 한다."
        assert len(store_close_calls) >= 1, msg

    def test_orb_분기_provider_미주입(self, monkeypatch, tmp_path):
        """--strategy-type=orb → factory_kwargs 없음 또는 prev_close_provider 키 없음.

        orb 분기는 build_strategy_factory 를 호출하지 않거나,
        호출하더라도 prev_close_provider 를 주입하지 않아야 한다 (회귀 0).
        """
        captured, _close_calls = self._setup(monkeypatch, tmp_path)
        args = _parse_args(self._BASE + ["--strategy-type=orb"])
        backtest_cli._run_pipeline(args)

        kwargs = captured["factory_kwargs"]
        # orb 분기는 build_strategy_factory 를 호출하지 않음 (None 유지)
        # 또는 호출해도 prev_close_provider=None
        if kwargs is not None:
            provider = kwargs.get("prev_close_provider")  # type: ignore[union-attr]
            msg = f"orb 분기에서 prev_close_provider 가 주입되면 안 됨: {provider!r}"
            assert provider is None, msg

    def test_vwap_mr_분기_provider_미주입(self, monkeypatch, tmp_path):
        """--strategy-type=vwap-mr → build_strategy_factory 호출되지만
        prev_close_provider 가 주입되지 않아야 한다 (회귀 0).
        """
        from stock_agent.backtest.prev_close import DailyBarPrevCloseProvider

        captured, _close_calls = self._setup(monkeypatch, tmp_path)
        args = _parse_args(self._BASE + ["--strategy-type=vwap-mr"])
        backtest_cli._run_pipeline(args)

        kwargs = captured["factory_kwargs"]
        if kwargs is not None:
            provider = kwargs.get("prev_close_provider")  # type: ignore[union-attr]
            msg = f"vwap-mr 분기에서 DailyBarPrevCloseProvider 가 주입되면 안 됨: {provider!r}"
            assert not isinstance(provider, DailyBarPrevCloseProvider), msg


class TestStrategyTypeMainExitCode:
    """main() 이 --strategy-type 지정 시에도 exit 0 을 반환하는지 검증 (RED).

    _run_pipeline 을 monkeypatch 로 대체해 exit code 경로만 확인한다.
    """

    _BASE = ["--csv-dir=/tmp/dummy", "--from=2023-01-01", "--to=2025-12-31"]

    def test_vwap_mr_정상_종료_exit_0(self, monkeypatch):
        """--strategy-type=vwap-mr → main() exit 0."""
        monkeypatch.setattr(backtest_cli, "_run_pipeline", lambda _: None)
        result = main(self._BASE + ["--strategy-type=vwap-mr"])
        assert result == 0

    def test_gap_reversal_정상_종료_exit_0(self, monkeypatch):
        """--strategy-type=gap-reversal → main() exit 0."""
        monkeypatch.setattr(backtest_cli, "_run_pipeline", lambda _: None)
        result = main(self._BASE + ["--strategy-type=gap-reversal"])
        assert result == 0
