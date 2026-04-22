"""backfill — KIS 과거 분봉 캐시 일괄 적재 CLI.

사용 예시:

```
uv run python scripts/backfill_minute_bars.py \
  --from 2025-04-22 --to 2026-04-22 \
  --symbols 005930,000660 \
  --throttle-s 0.0
```

동작
- `KisMinuteBarLoader` (실전 키 전용) 로 심볼별 `stream(start, end, (symbol,))`
  호출. iterator 를 모두 소진해 캐시(`data/minute_bars.db`) 에 적층한다.
- 캐시 정책(`(symbol, 날짜)` 단위, 한 건이라도 있으면 캐시됨 — ADR-0016) 이 곧
  체크포인트 역할. 중단 후 재실행 시 누락 날짜만 KIS API 재호출.
- 심볼 단위 예외 격리: `KisMinuteBarLoadError` 한 건이 전체 백필을 죽이지 않는다.
  격리된 실패는 `failed` 카운터와 마지막 요약 로그에 모인다.
- 진행률·요약을 loguru 로 출력. 운영자가 장시간 백그라운드로 띄워둘 때 stdout
  꼬리만 봐도 진척을 가늠할 수 있도록 심볼당 1줄 + 시작/종료 1줄.

exit code (`scripts/backtest.py`·`scripts/sensitivity.py` 와 정합)
- 0: 모든 심볼 성공.
- 1: 일부 심볼 실패 (`KisMinuteBarLoadError`). 운영자 검토 후 재실행.
- 2: 입력·설정 오류 (`start>end`, `throttle_s<0`, 빈 universe, live 키 없음 등) —
  재시도 무의미.
- 3: I/O 오류 (디스크·권한 — 재시도 가치 있음).

제약
- 실전 KIS API 호출이 발생한다 (시세 도메인 — `KisMinuteBarLoader` 가 실전 키
  전용). KIS Developers 포털 IP 화이트리스트 등록이 선행되어야 한다.
- KIS 서버 분봉 보관 한도는 약 1년 — `--from` 이 1년을 넘어가면 빈 응답이 늘어
  나며 PASS 판정용 표본 수가 미달할 수 있다 (ADR-0017 240 영업일 최소 기준).
- 단일 프로세스 전용 (ADR-0008). `KisMinuteBarLoader._lock` 은 PyKis 지연
  초기화만 보호하며, 다른 스레드에서 같은 인스턴스의 DB 호출 경로를 진입하면
  `sqlite3.ProgrammingError` 로 폭파한다.
- 본 스크립트는 백필 전용 — 실제 백테스트는 `scripts/backtest.py --loader=kis`
  로 별도 실행한다 (캐시는 공유).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

from loguru import logger

from stock_agent.config import get_settings
from stock_agent.data import (
    KisMinuteBarLoader,
    KisMinuteBarLoadError,
    load_kospi200_universe,
)

_EXIT_OK = 0
_EXIT_PARTIAL_FAILURE = 1
_EXIT_INPUT_ERROR = 2
_EXIT_IO_ERROR = 3


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="KIS 과거 분봉 캐시 백필",
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
        help="쉼표 구분 종목 코드 (미지정 시 config/universe.yaml 전체 사용).",
    )
    parser.add_argument(
        "--throttle-s",
        type=float,
        default=0.0,
        help="페이지 호출 사이 추가 sleep 초 (KIS 레이트 리밋 완화 — 0 이상).",
    )
    parser.add_argument(
        "--cache-db-path",
        type=Path,
        default=None,
        help="캐시 DB 경로 (미지정 시 KisMinuteBarLoader 기본값 data/minute_bars.db).",
    )
    return parser.parse_args(argv)


def _resolve_symbols(raw: str) -> tuple[str, ...]:
    """`--symbols` 인자 해석 — 빈 값이면 universe.yaml 전체.

    `scripts/backtest.py:_resolve_symbols` 와 동일 계약. 공용 헬퍼 승격은 YAGNI
    (현재 소비자 3개로 늘었지만 모듈 의존 방향이 scripts/ → data/ 일방이라
    공용화의 이득이 작다).
    """
    if raw.strip():
        return tuple(s.strip() for s in raw.split(",") if s.strip())
    universe = load_kospi200_universe()
    if not universe.tickers:
        raise RuntimeError(
            "config/universe.yaml 이 비어있습니다 — --symbols 로 명시하거나 "
            "유니버스 YAML 을 갱신하세요."
        )
    return universe.tickers


def _run_pipeline(args: argparse.Namespace) -> tuple[int, int, int]:
    """심볼별 백필을 실행. `(succeeded, failed, total_bars)` 반환.

    부분 실패는 예외 없이 카운터로 보고 — 한 심볼의 `KisMinuteBarLoadError` 가
    전체 백필을 중단시키지 않도록 try/except 로 격리한다. 격리된 실패 심볼은
    마지막에 한 번 더 요약 로그로 출력한다.

    Raises:
        KisMinuteBarLoadError: 생성자에서 발생한 경우 (`has_live_keys=False` 등) —
            모든 심볼 진행 불가능하므로 `main` 에서 입력 오류로 분류.
        RuntimeError: `_resolve_symbols` 가 빈 universe 를 만난 경우 등.
        OSError: 캐시 DB 디렉토리 생성 실패 등 — `main` 에서 I/O 오류로 분류.
    """
    symbols = _resolve_symbols(args.symbols)
    settings = get_settings()

    loader_kwargs: dict[str, Any] = {"throttle_s": args.throttle_s}
    if args.cache_db_path is not None:
        loader_kwargs["cache_db_path"] = args.cache_db_path
    loader = KisMinuteBarLoader(settings, **loader_kwargs)

    succeeded = 0
    failed = 0
    total_bars = 0
    failed_symbols: list[tuple[str, str]] = []
    started = time.monotonic()
    n = len(symbols)

    logger.info(
        "backfill.start from={s} to={e} symbols={n} throttle_s={t}",
        s=args.start,
        e=args.end,
        n=n,
        t=args.throttle_s,
    )

    try:
        for idx, symbol in enumerate(symbols, start=1):
            sym_started = time.monotonic()
            try:
                bars_count = sum(1 for _ in loader.stream(args.start, args.end, (symbol,)))
            except KisMinuteBarLoadError as exc:
                failed += 1
                failed_symbols.append((symbol, str(exc)))
                logger.error(
                    "backfill.symbol_failed symbol={s} idx={i}/{n} err={e}",
                    s=symbol,
                    i=idx,
                    n=n,
                    e=exc,
                )
                continue

            succeeded += 1
            total_bars += bars_count
            elapsed = time.monotonic() - sym_started
            logger.info(
                "backfill.symbol_done symbol={s} idx={i}/{n} bars={b} elapsed_s={t:.1f}",
                s=symbol,
                i=idx,
                n=n,
                b=bars_count,
                t=elapsed,
            )
    finally:
        loader.close()

    elapsed_total = time.monotonic() - started
    logger.info(
        "backfill.done succeeded={s} failed={f} total_bars={b} elapsed_s={t:.1f}",
        s=succeeded,
        f=failed,
        b=total_bars,
        t=elapsed_total,
    )
    if failed_symbols:
        for sym, err in failed_symbols:
            logger.error("backfill.failed_symbol symbol={s} err={e}", s=sym, e=err)

    return succeeded, failed, total_bars


def main(argv: list[str] | None = None) -> int:
    """CLI 엔트리포인트 — 예외 → exit code 매핑만 책임진다.

    예외 분류 (프로젝트 가드레일 "generic except Exception 금지" 기조 준수):

    - `KisMinuteBarLoadError` (생성자 단계) · `RuntimeError` → exit 2 (입력·설정
      오류, 재시도 무의미).
    - `OSError` → exit 3 (I/O 오류, 재시도 가치 있음).
    - 부분 실패 (`failed > 0`) → exit 1. 모든 심볼 성공 → exit 0.
    - 그 외 예외는 버그로 간주해 Python traceback 그대로 종료 (loguru 가 stderr
      에 기록).
    """
    args = _parse_args(argv)

    if args.start > args.end:
        logger.error(f"--from({args.start}) 는 --to({args.end}) 이전이어야 합니다.")
        return _EXIT_INPUT_ERROR
    if args.throttle_s < 0:
        logger.error(f"--throttle-s 는 0 이상이어야 합니다 (got={args.throttle_s}).")
        return _EXIT_INPUT_ERROR

    try:
        _, failed, _ = _run_pipeline(args)
    except KisMinuteBarLoadError as exc:
        logger.error(f"KIS 분봉 입력 오류: {exc}")
        return _EXIT_INPUT_ERROR
    except RuntimeError as exc:
        logger.error(f"설정·검증 오류: {exc}")
        return _EXIT_INPUT_ERROR
    except OSError as exc:
        logger.exception(f"I/O 오류 (재시도 가능): {exc}")
        return _EXIT_IO_ERROR

    if failed > 0:
        return _EXIT_PARTIAL_FAILURE
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
