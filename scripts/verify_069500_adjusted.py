"""ADR-0023 C3 — 069500 KODEX 200 일봉 수정주가 plausibility 검증.

목적
- pykrx 가 069500 일봉을 수정주가 (액면분할·병합·배당·분배금) 보정한 값으로 돌려주는지
  직접 비교로 확정.
- Step F PR2 caveat (069500 1년 +180% 비현실적) 의 데이터 측 원인 가설 검증.

비교 4 단계
1. pykrx ``adjusted=True`` (캐시 default) vs ``adjusted=False`` close diff.
   - 차이 > 0 인 trade_date 가 있으면 pykrx 가 수정주가 적용 중 + 그 이전 분배·분할 이벤트 존재.
   - 차이 0 이면 미적용 또는 구간 내 이벤트 부재.
2. 069500 ETF / KOSPI 200 (1028) 인덱스 비율 시계열.
   - ETF 는 NAV 추적 → 비율 거의 일정. 비율 점프 = 분배·분할·데이터 오염 신호.
3. 캐시 close vs 새로 fetch 한 ``adjusted=True`` close 일치 확인.
   - 캐시가 수정주가 데이터인지 미수정 데이터인지 결정 (`backfill_daily_bars.py`
     실행 시점 pykrx default 적용 검증).
4. JSON 결과 dump → ``data/c3_verify_069500.json`` (런북 첨부용).

실행
- KRX_ID / KRX_PW 가 ``~/.config/stocker/.env`` 또는 repo ``.env`` 에 설정돼 있어야
  pykrx 가 KRX 로그인할 수 있다 (pykrx 1.2.7+).
- ``uv run python scripts/verify_069500_adjusted.py``.

산출물
- stdout: 4 단계 요약 + 핵심 점프 일자 표.
- ``data/c3_verify_069500.json``: 비교 raw 결과 (런북 첨부).

본 스크립트는 1회 진단 — 결과 재현이 필요하면 같은 명령 재실행. SQLite 캐시는
변경하지 않는다 (`--no-cache` 경로로 pykrx 직접 호출 + 메모리 비교).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from loguru import logger

from stock_agent.data.historical import _coerce_date  # noqa: PLC2701

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_OUTPUT_JSON = _REPO_ROOT / "data" / "c3_verify_069500.json"
_TARGET_SYMBOL = "069500"
_KOSPI200_INDEX_TICKER = "1028"


def _load_env_files() -> None:
    """Settings 와 동일 우선순위로 .env 를 OS environ 에 inject.

    pydantic-settings 는 Settings 모델 필드만 .env 에서 읽고 OS env 에 export 하지
    않는다. pykrx 가 ``os.environ["KRX_ID"]`` 를 직접 읽기 때문에 본 스크립트는
    명시적으로 load_dotenv 를 호출한다. 우선순위는 stock_agent.config 와 동일:
    ~/.config/stocker/.env → repo .env (뒤가 앞을 override).
    """
    import os  # noqa: PLC0415

    xdg_root = os.environ.get("XDG_CONFIG_HOME")
    home_base = Path(xdg_root) if xdg_root else Path.home() / ".config"
    shared = home_base / "stocker" / ".env"
    repo_env = _REPO_ROOT / ".env"
    for path in (shared, repo_env):
        if path.exists():
            load_dotenv(dotenv_path=path, override=False)
            logger.info("loaded env file: {p}", p=path)


def _fetch_pykrx_ohlcv(
    *,
    symbol: str,
    start: date,
    end: date,
    adjusted: bool,
) -> dict[date, Decimal]:
    """pykrx 일봉 close 만 추출. {trade_date: close_decimal}.

    ``adjusted=True`` 는 pykrx 1.2.7 default 와 동치이지만 명시 호출.
    """
    from pykrx import stock as _stock  # noqa: PLC0415

    df = _stock.get_market_ohlcv_by_date(
        start.strftime("%Y%m%d"),
        end.strftime("%Y%m%d"),
        symbol,
        adjusted=adjusted,
    )
    if df is None or getattr(df, "empty", False):
        return {}

    out: dict[date, Decimal] = {}
    for idx, row in df.iterrows():
        trade_date = _coerce_date(idx)
        close = Decimal(str(row["종가"]))
        out[trade_date] = close
    return out


def _fetch_pykrx_index_ohlcv(
    *,
    ticker: str,
    start: date,
    end: date,
) -> dict[date, Decimal]:
    """KOSPI 200 인덱스 일봉 close. {trade_date: close_decimal}.

    인덱스는 수정주가 옵션이 없다 (의미 자체가 없음). pykrx
    ``get_index_ohlcv_by_date`` 호출.
    """
    from pykrx import stock as _stock  # noqa: PLC0415

    df = _stock.get_index_ohlcv_by_date(
        start.strftime("%Y%m%d"),
        end.strftime("%Y%m%d"),
        ticker,
    )
    if df is None or getattr(df, "empty", False):
        return {}

    out: dict[date, Decimal] = {}
    for idx, row in df.iterrows():
        trade_date = _coerce_date(idx)
        close = Decimal(str(row["종가"]))
        out[trade_date] = close
    return out


def _load_cache_closes(
    db_path: Path,
    symbol: str,
    start: date,
    end: date,
) -> dict[date, Decimal]:
    """data/stock_agent.db 캐시에서 close 만 추출. {trade_date: close_decimal}."""
    import sqlite3  # noqa: PLC0415

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT trade_date, close FROM daily_bars "
            "WHERE symbol = ? AND trade_date BETWEEN ? AND ? "
            "ORDER BY trade_date ASC",
            (symbol, start.isoformat(), end.isoformat()),
        ).fetchall()
    finally:
        conn.close()

    return {date.fromisoformat(d): Decimal(str(c)) for d, c in rows}


def _diff_closes(
    a: dict[date, Decimal],
    b: dict[date, Decimal],
    *,
    label_a: str,
    label_b: str,
) -> list[dict[str, Any]]:
    """공통 trade_date 에서 close 값이 다른 행만 반환.

    출력 정렬: trade_date ASC.
    """
    common = sorted(set(a.keys()) & set(b.keys()))
    out: list[dict[str, Any]] = []
    for d in common:
        ca, cb = a[d], b[d]
        if ca != cb:
            ratio = float(ca / cb) if cb != 0 else None
            out.append(
                {
                    "date": d.isoformat(),
                    label_a: str(ca),
                    label_b: str(cb),
                    "diff": str(ca - cb),
                    f"{label_a}_over_{label_b}": ratio,
                }
            )
    return out


def _ratio_series(
    etf: dict[date, Decimal],
    index: dict[date, Decimal],
) -> list[dict[str, Any]]:
    """ETF / index 비율 시계열. NAV 추적이라 비율이 거의 일정해야 함."""
    common = sorted(set(etf.keys()) & set(index.keys()))
    return [
        {
            "date": d.isoformat(),
            "etf_close": str(etf[d]),
            "index_close": str(index[d]),
            "ratio_etf_over_index": float(etf[d] / index[d]),
        }
        for d in common
        if index[d] != 0
    ]


def _ratio_stats(rows: list[dict[str, Any]]) -> dict[str, float]:
    """비율 시계열의 mean / std / min / max + 시작·종료점 비율."""
    ratios = [r["ratio_etf_over_index"] for r in rows]
    if not ratios:
        return {}
    n = len(ratios)
    mean = sum(ratios) / n
    var = sum((r - mean) ** 2 for r in ratios) / n
    std = var**0.5
    return {
        "n": float(n),
        "mean": mean,
        "std": std,
        "min": min(ratios),
        "max": max(ratios),
        "start_ratio": ratios[0],
        "end_ratio": ratios[-1],
        "end_over_start": ratios[-1] / ratios[0] if ratios[0] != 0 else float("nan"),
        "max_minus_min_pct_of_mean": (max(ratios) - min(ratios)) / mean * 100.0 if mean else 0.0,
    }


def _detect_ratio_jumps(
    rows: list[dict[str, Any]],
    *,
    pct_threshold: float = 1.0,
) -> list[dict[str, Any]]:
    """전일 대비 비율 변화 > pct_threshold (%) 인 일자 추출.

    ETF 일중 추적 오차는 통상 수십 bp 이내. 1% 점프는 분배·분할·데이터 오염 신호.
    """
    out: list[dict[str, Any]] = []
    prev_ratio = None
    for r in rows:
        cur = r["ratio_etf_over_index"]
        if prev_ratio is not None and prev_ratio != 0:
            chg_pct = (cur - prev_ratio) / prev_ratio * 100.0
            if abs(chg_pct) >= pct_threshold:
                out.append(
                    {
                        "date": r["date"],
                        "prev_ratio": prev_ratio,
                        "cur_ratio": cur,
                        "change_pct": chg_pct,
                    }
                )
        prev_ratio = cur
    return out


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ADR-0023 C3 — 069500 일봉 수정주가 plausibility 검증",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2024, 6, 1))
    parser.add_argument("--to", dest="end", type=date.fromisoformat, default=date(2026, 4, 21))
    parser.add_argument("--db-path", type=Path, default=_REPO_ROOT / "data" / "stock_agent.db")
    parser.add_argument("--output-json", type=Path, default=_DEFAULT_OUTPUT_JSON)
    parser.add_argument(
        "--ratio-jump-threshold-pct",
        type=float,
        default=1.0,
        help="ETF/index 비율 전일 대비 변화 임계 (%). 이상치 추출 기준.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _load_env_files()

    logger.info(
        "C3 verify start symbol={s} range={a}~{b}",
        s=_TARGET_SYMBOL,
        a=args.start,
        b=args.end,
    )

    # Stage 1: pykrx adjusted=True vs adjusted=False
    logger.info("stage1: pykrx adjusted=True vs adjusted=False")
    adjusted_closes = _fetch_pykrx_ohlcv(
        symbol=_TARGET_SYMBOL, start=args.start, end=args.end, adjusted=True
    )
    unadjusted_closes = _fetch_pykrx_ohlcv(
        symbol=_TARGET_SYMBOL, start=args.start, end=args.end, adjusted=False
    )
    diffs_adj_unadj = _diff_closes(
        adjusted_closes, unadjusted_closes, label_a="adjusted", label_b="unadjusted"
    )
    logger.info(
        "stage1.done adjusted_n={a} unadjusted_n={u} diff_dates={d}",
        a=len(adjusted_closes),
        u=len(unadjusted_closes),
        d=len(diffs_adj_unadj),
    )

    # Stage 2: ETF / KOSPI 200 비율
    logger.info("stage2: ETF/KOSPI200 ratio series")
    index_closes = _fetch_pykrx_index_ohlcv(
        ticker=_KOSPI200_INDEX_TICKER, start=args.start, end=args.end
    )
    ratio_rows = _ratio_series(adjusted_closes, index_closes)
    ratio_stats = _ratio_stats(ratio_rows)
    ratio_jumps = _detect_ratio_jumps(ratio_rows, pct_threshold=args.ratio_jump_threshold_pct)
    logger.info(
        "stage2.done ratio_n={n} jumps={j} threshold={t}%",
        n=len(ratio_rows),
        j=len(ratio_jumps),
        t=args.ratio_jump_threshold_pct,
    )

    # Stage 3: 캐시 vs adjusted=True
    logger.info("stage3: cache vs pykrx adjusted=True")
    cache_closes = _load_cache_closes(args.db_path, _TARGET_SYMBOL, args.start, args.end)
    diffs_cache_adj = _diff_closes(
        cache_closes, adjusted_closes, label_a="cache", label_b="pykrx_adjusted"
    )
    logger.info(
        "stage3.done cache_n={c} diff_dates={d}",
        c=len(cache_closes),
        d=len(diffs_cache_adj),
    )

    # Stage 4: JSON dump
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "symbol": _TARGET_SYMBOL,
        "kospi200_index_ticker": _KOSPI200_INDEX_TICKER,
        "range": {"from": args.start.isoformat(), "to": args.end.isoformat()},
        "counts": {
            "adjusted": len(adjusted_closes),
            "unadjusted": len(unadjusted_closes),
            "index": len(index_closes),
            "cache": len(cache_closes),
            "diff_adj_vs_unadj": len(diffs_adj_unadj),
            "diff_cache_vs_adj": len(diffs_cache_adj),
            "ratio_jumps": len(ratio_jumps),
        },
        "stage1_adj_vs_unadj_diffs": diffs_adj_unadj,
        "stage2_ratio_stats": ratio_stats,
        "stage2_ratio_jumps": ratio_jumps,
        "stage3_cache_vs_adj_diffs": diffs_cache_adj,
        "ratio_jump_threshold_pct": args.ratio_jump_threshold_pct,
    }
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("stage4.done wrote {p}", p=args.output_json)

    # 콘솔 요약
    print("\n=== ADR-0023 C3 — 069500 plausibility 요약 ===")
    print(f"구간: {args.start} ~ {args.end}")
    print(
        f"row counts: adjusted={len(adjusted_closes)} unadjusted={len(unadjusted_closes)} "
        f"index={len(index_closes)} cache={len(cache_closes)}"
    )
    print("\nstage1: pykrx adjusted=True vs adjusted=False")
    print(f"  diff dates: {len(diffs_adj_unadj)}")
    if diffs_adj_unadj:
        first = diffs_adj_unadj[0]
        last = diffs_adj_unadj[-1]
        print(
            f"  first diff: {first['date']} adj={first['adjusted']} "
            f"unadj={first['unadjusted']} diff={first['diff']}"
        )
        print(
            f"  last  diff: {last['date']} adj={last['adjusted']} "
            f"unadj={last['unadjusted']} diff={last['diff']}"
        )
    print("\nstage2: ETF/KOSPI200 ratio series")
    if ratio_stats:
        print(
            "  stats: n={n} mean={m:.6f} std={s:.6f} min={mn:.6f} max={mx:.6f}".format(
                n=int(ratio_stats["n"]),
                m=ratio_stats["mean"],
                s=ratio_stats["std"],
                mn=ratio_stats["min"],
                mx=ratio_stats["max"],
            )
        )
        print(
            "  start_ratio={s:.6f} end_ratio={e:.6f} end/start={r:.4f} "
            "max-min={mm:.2f}% of mean".format(
                s=ratio_stats["start_ratio"],
                e=ratio_stats["end_ratio"],
                r=ratio_stats["end_over_start"],
                mm=ratio_stats["max_minus_min_pct_of_mean"],
            )
        )
    print(f"  ratio jumps (|Δ|>={args.ratio_jump_threshold_pct}%): {len(ratio_jumps)}")
    for j in ratio_jumps[:10]:
        print(
            "    {d} prev={p:.6f} cur={c:.6f} chg={chg:+.4f}%".format(
                d=j["date"], p=j["prev_ratio"], c=j["cur_ratio"], chg=j["change_pct"]
            )
        )
    if len(ratio_jumps) > 10:
        print(f"    ... +{len(ratio_jumps) - 10} more")

    print("\nstage3: cache vs pykrx adjusted=True")
    print(f"  diff dates: {len(diffs_cache_adj)}")
    for r in diffs_cache_adj[:5]:
        print(
            f"    {r['date']} cache={r['cache']} pykrx_adj={r['pykrx_adjusted']} diff={r['diff']}"
        )

    print(f"\noutput: {args.output_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
