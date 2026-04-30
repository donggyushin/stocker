"""유동성 랭킹 산출 CLI — Step C (Issue #76, ADR-0019).

ADR-0019 Phase 2 복구 로드맵 Step C. KOSPI 200 199 종목의 12개월 평균 거래대금과
일간 수익률 표준편차를 산출해 거래대금 상위 N (Top 50/100) 서브셋 구성을 위한
입력을 만든다. Look-ahead bias 방지를 위해 산출 윈도는 백테스트 구간 시작 직전
12개월로 운영자가 지정한다.

사용 예시:

```
uv run python scripts/build_liquidity_ranking.py \
  --start 2024-04-22 --end 2025-04-21 \
  --universe-yaml config/universe.yaml \
  --output-csv data/liquidity_ranking.csv
```

CSV 컬럼 (헤더 포함, 정확한 순서):

- `symbol`: 6자리 문자열
- `avg_value_krw`: int (mean(거래대금) 반올림)
- `daily_return_std`: float (std(daily_return), 표본 표준편차 ddof=1)
- `sample_days`: int (해당 종목이 데이터로 잡힌 영업일 수)
- `rank_value`: int (1=최대 거래대금, 동률 시 symbol 오름차순)

exit code
- 0: 정상.
- 2: 입력·설정 오류 (`start > end`, 빈 영업일, 50% 이상 영업일 실패) — 재시도 무의미.
- 3: I/O 오류 — 재시도 가치 있음.

제약
- 실 pykrx 네트워크 호출 — KRX 정보데이터시스템 응답 (외부 인증·키 무관).
- 영업일 캘린더는 `config/holidays.yaml` (`YamlBusinessDayCalendar`).
- 본 스크립트는 단발 ETL — 결과 CSV 는 운영자가 git 추적 외부(`data/`) 에서 검토 후
  `scripts/build_universe_subset.py` 로 yaml 산출에 투입한다.
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from stock_agent.data import (
    BusinessDayCalendar,
    UniverseLoadError,
    YamlBusinessDayCalendar,
    load_kospi200_universe,
)

_EXIT_OK = 0
_EXIT_INPUT_ERROR = 2
_EXIT_IO_ERROR = 3

_FAILURE_RATIO_THRESHOLD = 0.5

_CSV_HEADER = ("symbol", "avg_value_krw", "daily_return_std", "sample_days", "rank_value")


def _default_pykrx_factory() -> Any:
    """기본 pykrx 팩토리 — 지연 import (`historical.py` 와 동일 패턴)."""
    from pykrx import stock as _stock  # noqa: PLC0415

    class _PykrxNs:
        stock = _stock

    return _PykrxNs()


def _enumerate_business_days(start: date, end: date, calendar: BusinessDayCalendar) -> list[date]:
    days: list[date] = []
    cur = start
    one_day = timedelta(days=1)
    while cur <= end:
        if calendar.is_business_day(cur):
            days.append(cur)
        cur += one_day
    return days


def _row_value(df: Any, sym: str, col: str) -> Any:
    """DataFrame 의 (symbol, col) 셀을 안전하게 가져온다."""
    return df.loc[sym, col]


def build_ranking(
    *,
    start: date,
    end: date,
    universe_yaml: Path,
    output_csv: Path,
    pykrx_factory: Callable[[], Any] | None = None,
    calendar: BusinessDayCalendar | None = None,
) -> None:
    """KOSPI bulk 일봉 거래대금 + 종가를 영업일별 호출해 종목별 평균·표준편차 CSV 출력."""
    if start > end:
        raise RuntimeError(f"start > end: {start} > {end}")

    universe = load_kospi200_universe(universe_yaml)
    universe_set = set(universe.tickers)

    cal = calendar if calendar is not None else YamlBusinessDayCalendar()
    business_days = _enumerate_business_days(start, end, cal)
    if not business_days:
        raise RuntimeError(f"no business days in range {start.isoformat()}..{end.isoformat()}")

    factory = pykrx_factory if pykrx_factory is not None else _default_pykrx_factory
    pykrx = factory()

    # symbol -> {date -> (close, value)}
    daily_data: dict[str, dict[date, tuple[float, int]]] = {}
    failed_days = 0

    for d in business_days:
        yyyymmdd = d.strftime("%Y%m%d")
        try:
            df = pykrx.stock.get_market_ohlcv_by_ticker(yyyymmdd, market="KOSPI")
        except Exception:
            logger.exception(f"pykrx 호출 실패 — date={d.isoformat()}")
            failed_days += 1
            continue

        if df is None or len(df) == 0:
            logger.warning(f"pykrx 빈 응답 — date={d.isoformat()}")
            failed_days += 1
            continue

        try:
            present_idx = list(df.index)
        except Exception:
            logger.exception(f"DataFrame index 추출 실패 — date={d.isoformat()}")
            failed_days += 1
            continue

        for sym in present_idx:
            if not isinstance(sym, str) or sym not in universe_set:
                continue
            try:
                close = float(_row_value(df, sym, "종가"))
                value = int(_row_value(df, sym, "거래대금"))
            except (KeyError, ValueError, TypeError):
                logger.warning(f"row 파싱 실패 — date={d.isoformat()} symbol={sym}")
                continue
            daily_data.setdefault(sym, {})[d] = (close, value)

    n_business = len(business_days)
    if n_business > 0 and failed_days / n_business > _FAILURE_RATIO_THRESHOLD:
        raise RuntimeError(
            f"excessive_failures: {failed_days}/{n_business} business days returned empty"
        )

    rows: list[tuple[str, int, float, int]] = []
    for sym in universe.tickers:
        sym_data = daily_data.get(sym)
        if not sym_data:
            logger.warning(f"symbol {sym} — 모든 영업일에서 데이터 누락, CSV 제외")
            continue
        ordered_days = sorted(sym_data.keys())
        sample_days = len(ordered_days)
        values = [sym_data[d][1] for d in ordered_days]
        avg_value_krw = int(round(sum(values) / sample_days))

        returns: list[float] = []
        for i in range(1, sample_days):
            prev_close = sym_data[ordered_days[i - 1]][0]
            cur_close = sym_data[ordered_days[i]][0]
            if prev_close > 0:
                returns.append(cur_close / prev_close - 1.0)

        std_val = statistics.stdev(returns) if len(returns) >= 2 else 0.0

        rows.append((sym, avg_value_krw, std_val, sample_days))

    rows.sort(key=lambda r: (-r[1], r[0]))

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_CSV_HEADER)
        for rank, (sym, avg, std_val, sample) in enumerate(rows, start=1):
            writer.writerow([sym, avg, _format_std(std_val), sample, rank])

    logger.info(
        "liquidity_ranking.done symbols={n} business_days={d} failed_days={f} output={p}",
        n=len(rows),
        d=n_business,
        f=failed_days,
        p=str(output_csv),
    )


def _format_std(value: float) -> str:
    """`daily_return_std` 직렬화 — 정밀도 보존 + 0 은 '0' 으로."""
    if value == 0.0:
        return "0"
    return repr(value)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="KOSPI 200 유동성 랭킹 산출 (Step C, ADR-0019)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        required=True,
        help="윈도 시작 (YYYY-MM-DD, 경계 포함). 백테스트 구간 시작 직전 12개월 권장.",
    )
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        required=True,
        help="윈도 종료 (YYYY-MM-DD, 경계 포함).",
    )
    parser.add_argument(
        "--universe-yaml",
        type=Path,
        default=Path("config/universe.yaml"),
        help="KOSPI 200 유니버스 YAML 경로.",
    )
    parser.add_argument(
        "--output-csv",
        type=Path,
        default=Path("data/liquidity_ranking.csv"),
        help="유동성 랭킹 CSV 출력 경로.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        build_ranking(
            start=args.start,
            end=args.end,
            universe_yaml=args.universe_yaml,
            output_csv=args.output_csv,
        )
    except UniverseLoadError as e:
        logger.error(f"universe 로드 실패: {e}")
        return _EXIT_INPUT_ERROR
    except RuntimeError as e:
        logger.error(f"입력·설정 오류: {e}")
        return _EXIT_INPUT_ERROR
    except OSError as e:
        logger.error(f"I/O 오류: {e}")
        return _EXIT_IO_ERROR

    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
