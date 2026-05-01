"""backfill — pykrx 일봉 캐시 일괄 적재 CLI.

사용 예시:

```
uv run python scripts/backfill_daily_bars.py \\
  --from 2025-04-01 --to 2026-04-21 \\
  --universe-yaml config/universe_top100.yaml
```

ADR-0019 Step E Stage 3 선결: `GapReversalStrategy` 의 `prev_close_provider`
(`DailyBarPrevCloseProvider`) 가 세션마다 `HistoricalDataStore.fetch_daily_ohlcv`
를 호출한다. 캐시 미백필 상태에서 백테스트를 돌리면 pykrx 네트워크 호출이
세션×심볼 단위로 누적되어 1) 결정론 깨짐 2) KRX rate limit 위험 3) 장시간 hang.
본 스크립트로 사전 1회 백필해 캐시를 채워두면 이후 백테스트는 모두 SQLite hit.

동작
- `HistoricalDataStore.fetch_daily_ohlcv(symbol, start, end)` 를 universe 또는
  `--symbols` 의 모든 심볼에 대해 1회씩 호출. 단일 호출이 구간 전체 일봉을
  pykrx 로 받아 `data/stock_agent.db` 에 적층한다.
- 캐시 적중 판정 (`historical.py`): `end < today` AND `(symbol, end)` 행 존재 →
  pykrx 재호출 생략. 즉 본 스크립트는 idempotent — 재실행 시 신규 심볼·구간만
  실제 네트워크 호출.
- 심볼 단위 예외 격리: `HistoricalDataError` 한 건이 전체 백필을 죽이지 않는다.
  격리된 실패는 `failed` 카운터와 마지막 요약 로그에 모인다.
- 진행률·요약을 loguru 로 출력. 심볼당 1줄 + 시작/종료 1줄.

exit code (`scripts/backfill_minute_bars.py`·`scripts/backtest.py` 와 정합)
- 0: 모든 심볼 성공.
- 1: 일부 심볼 `HistoricalDataError` (운영자 검토 후 재실행).
- 2: 입력·설정 오류 (`start>end`, 빈 universe, store 생성자 RuntimeError 등).
- 3: I/O 오류 (디스크·권한 — `OSError`).

제약
- pykrx 1.2.7 부터 KRX_ID/KRX_PW 환경변수 필수. `~/.config/stocker/.env`
  또는 repo `.env` 에 등록되어 있어야 한다.
- 단일 프로세스 전용 (ADR-0008).
- 장중 백필 시 당일(T) 분 = `end == today` 케이스는 매번 pykrx 재호출
  (실시간 행 갱신용). 야간 백필 권장.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from loguru import logger

from stock_agent.data import (
    HistoricalDataError,
    HistoricalDataStore,
    KospiUniverse,
    load_kospi200_universe,
)

_EXIT_OK = 0
_EXIT_PARTIAL_FAILURE = 1
_EXIT_INPUT_ERROR = 2
_EXIT_IO_ERROR = 3

_DEFAULT_DB_PATH = Path("data/stock_agent.db")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="pykrx 일봉 캐시 백필",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--from",
        dest="start",
        type=date.fromisoformat,
        required=True,
        help="구간 시작 (YYYY-MM-DD, 경계 포함).",
    )
    parser.add_argument(
        "--to",
        dest="end",
        type=date.fromisoformat,
        required=True,
        help="구간 종료 (YYYY-MM-DD, 경계 포함).",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default="",
        help="쉼표 구분 종목 코드 (미지정 시 --universe-yaml 또는 config/universe.yaml 사용).",
    )
    parser.add_argument(
        "--universe-yaml",
        type=Path,
        default=None,
        help="유니버스 YAML 경로 (미지정 시 load_kospi200_universe 기본값).",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=_DEFAULT_DB_PATH,
        help="HistoricalDataStore SQLite 파일 경로.",
    )
    return parser.parse_args(argv)


def _resolve_symbols(
    raw: str,
    universe_yaml: Path | None,
    universe_loader: Callable[[Path | None], KospiUniverse],
) -> tuple[str, ...]:
    """`--symbols` 우선, 비면 universe loader 사용."""
    if raw.strip():
        return tuple(s.strip() for s in raw.split(",") if s.strip())
    universe = universe_loader(universe_yaml)
    return universe.tickers


def _run_pipeline(
    args: argparse.Namespace,
    *,
    store_factory: Callable[[Path], HistoricalDataStore] | None = None,
    universe_loader: Callable[[Path | None], KospiUniverse] | None = None,
) -> int:
    """백필 파이프라인 본체. exit code 반환."""
    if args.start > args.end:
        logger.error(f"--from({args.start}) 는 --to({args.end}) 이전이어야 합니다.")
        return _EXIT_INPUT_ERROR

    loader = universe_loader if universe_loader is not None else load_kospi200_universe
    try:
        symbols = _resolve_symbols(args.symbols, args.universe_yaml, loader)
    except RuntimeError as exc:
        logger.error(f"유니버스 해석 실패: {exc}")
        return _EXIT_INPUT_ERROR

    if not symbols:
        logger.error("empty universe — --symbols 로 명시하거나 universe YAML 을 갱신하세요.")
        return _EXIT_INPUT_ERROR

    def _default_factory(db_path: Path) -> HistoricalDataStore:
        return HistoricalDataStore(db_path=db_path)

    factory = store_factory if store_factory is not None else _default_factory
    try:
        store = factory(args.db_path)
    except RuntimeError as exc:
        logger.error(f"HistoricalDataStore 생성 실패: {exc}")
        return _EXIT_INPUT_ERROR
    except OSError as exc:
        logger.exception(f"HistoricalDataStore I/O 오류: {exc}")
        return _EXIT_IO_ERROR

    succeeded = 0
    failed = 0
    failed_symbols: list[tuple[str, str]] = []
    n = len(symbols)

    logger.info(
        "backfill_daily.start from={s} to={e} symbols={n} db={db}",
        s=args.start,
        e=args.end,
        n=n,
        db=args.db_path,
    )

    try:
        for idx, symbol in enumerate(symbols, start=1):
            try:
                bars = store.fetch_daily_ohlcv(symbol, args.start, args.end)
            except HistoricalDataError as exc:
                failed += 1
                failed_symbols.append((symbol, str(exc)))
                logger.error(
                    "backfill_daily.symbol_failed symbol={s} idx={i}/{n} err={e}",
                    s=symbol,
                    i=idx,
                    n=n,
                    e=exc,
                )
                continue

            succeeded += 1
            logger.info(
                "backfill_daily.symbol_done symbol={s} idx={i}/{n} bars={b}",
                s=symbol,
                i=idx,
                n=n,
                b=len(bars),
            )
    finally:
        store.close()

    logger.info(
        "backfill_daily.done succeeded={s} failed={f}",
        s=succeeded,
        f=failed,
    )
    if failed_symbols:
        for sym, err in failed_symbols:
            logger.error("backfill_daily.failed_symbol symbol={s} err={e}", s=sym, e=err)

    if failed > 0:
        return _EXIT_PARTIAL_FAILURE
    return _EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return _run_pipeline(args)


if __name__ == "__main__":
    sys.exit(main())
