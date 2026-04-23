"""debug_kis_minute — KIS 과거 분봉(FHKST03010230) 실 응답 덤프 진단 도구.

Issue #52 DoD 1항 — `output2` 각 행의 실제 dict 키/값 shape 을 확인하기 위한 one-shot
호출 스크립트. `KisMinuteBarLoader._parse_row` 가 가정하는 키(`stck_bsop_date`,
`stck_cntg_hour`, `stck_oprc`, `stck_hgpr`, `stck_lwpr`, `stck_prpr`, `cntg_vol`) 와
실제 응답이 일치하는지 비교한다.

사용 예시 (장중 평일 09:00~15:30 KST):

```
uv run python scripts/debug_kis_minute.py --symbol 005930 --date 2026-04-17
```

동작
- `Settings.has_live_keys == True` 게이트 — 실전 키 없으면 즉시 exit 2.
- 실전 키 PyKis 인스턴스 생성 + `install_order_block_guard` 설치. 생성·fetch 실패는
  `RuntimeError` 로 래핑해 exit 2 로 귀속 (Issue #52 C2).
- `kis.fetch(<path>, api="FHKST03010230", params={...}, domain="real")` 1회 호출.
- 응답 메타(`type`, `__data__` 존재, `rt_cd`·`msg_cd`·`msg1`) + `output2` 길이·첫
  행 타입·정렬된 key 목록 + 첫 3 행 샘플을 stdout 과 파일 두 곳에 기록.
- **기본은 key 목록만** (가격·거래량 값 유출 차단). `--include-values` opt-in
  플래그를 지정하면 첫 3행 raw dict 값까지 포함 — 민감 가격 정보가 디스크·stdout
  에 남을 수 있으므로 진단 긴급 시에만 사용.
- `output1` 은 설계상 항상 미수집 — 민감 정보(계좌번호·토큰 등) 가 섞여 올
  가능성이 있어 조건부 분기 없이 고정 배제한다. 존재 여부는
  `response.root_keys` 로만 확인 가능.

exit code (`scripts/backfill_minute_bars.py` 기조)
- 0: 정상 완료.
- 2: 입력·설정 오류 (실전 키 없음, 날짜 파싱 실패, symbol 포맷 등).
- 3: I/O 오류 (디스크·권한 — 출력 파일 쓰기 실패).

범위 제외
- 응답 페이지네이션 / 캐시 / 재시도 — `KisMinuteBarLoader` 본체에서 검증.
- 단위 테스트 — 진단 도구라 `scripts/healthcheck.py` 와 동일하게 생략.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from stock_agent.config import get_settings
from stock_agent.safety import install_order_block_guard

_API_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
_TR_ID = "FHKST03010230"
_SYMBOL_RE = re.compile(r"^\d{6}$")
_CURSOR_RE = re.compile(r"^\d{6}$")
KST = timezone(timedelta(hours=9))

_EXIT_OK = 0
_EXIT_INPUT_ERROR = 2
_EXIT_IO_ERROR = 3

_DEFAULT_OUTPUT_DIR = Path("data")


def _default_date() -> date:
    """직전 평일 (토요일 → 금요일, 일요일 → 금요일, 그 외 → 전일)."""
    today = datetime.now(KST).date()
    candidate = today - timedelta(days=1)
    while candidate.weekday() >= 5:  # 5=토, 6=일
        candidate -= timedelta(days=1)
    return candidate


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="KIS 과거 분봉(FHKST03010230) 실 응답 덤프 진단 도구",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default="005930",
        help="종목 코드 6자리 숫자. 기본 삼성전자.",
    )
    parser.add_argument(
        "--date",
        dest="trade_date",
        type=date.fromisoformat,
        default=None,
        help="조회 날짜 (YYYY-MM-DD). 미지정 시 직전 평일.",
    )
    parser.add_argument(
        "--cursor",
        type=str,
        default="153000",
        help="역방향 커서 HHMMSS. 기본 장종료 시각.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="덤프 JSON 저장 경로. 미지정 시 data/debug_kis_minute_<ts>.json.",
    )
    parser.add_argument(
        "--include-values",
        action="store_true",
        default=False,
        help=(
            "첫 3행 샘플에 가격·거래량 값까지 포함 (opt-in). 기본은 key 목록만 "
            "기록해 로그·JSON 파일에 가격 유출을 차단한다."
        ),
    )
    return parser.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> None:
    if not _SYMBOL_RE.match(args.symbol):
        raise RuntimeError(f"--symbol 은 6자리 숫자여야 합니다: {args.symbol!r}")
    if not _CURSOR_RE.match(args.cursor):
        raise RuntimeError(f"--cursor 는 HHMMSS 6자리 숫자여야 합니다: {args.cursor!r}")
    if args.trade_date is None:
        args.trade_date = _default_date()


def _build_pykis(settings: Any) -> Any:
    """실전 키 PyKis 인스턴스 생성 + order block guard 설치.

    `KisMinuteBarLoader._ensure_kis` 와 같은 파라미터 계약을 사용하되, PyKis
    생성·guard 설치 실패는 모두 `RuntimeError` 로 래핑해 `main` 의 exit 2 경로로
    귀속시킨다 — `scripts/backfill_minute_bars.py` · `KisMinuteBarLoader` 의
    예외 계약과 정합 (Issue #52 C2).
    """
    from pykis import PyKis  # noqa: PLC0415

    assert settings.kis_live_app_key is not None
    assert settings.kis_live_app_secret is not None
    assert settings.kis_live_account_no is not None

    try:
        kis = PyKis(
            id=settings.kis_hts_id,
            account=settings.kis_live_account_no,
            appkey=settings.kis_live_app_key.get_secret_value(),
            secretkey=settings.kis_live_app_secret.get_secret_value(),
            keep_token=True,
        )
        install_order_block_guard(kis)
    except Exception as exc:
        raise RuntimeError(f"PyKis 실전 인스턴스 생성 실패: {exc}") from exc
    return kis


def _resolve_output_path(arg_path: Path | None) -> Path:
    if arg_path is not None:
        return arg_path
    ts = datetime.now(KST).strftime("%Y%m%d_%H%M%S")
    return _DEFAULT_OUTPUT_DIR / f"debug_kis_minute_{ts}.json"


def _extract_raw_data(response: Any) -> tuple[dict[str, Any] | None, str]:
    """`kis.fetch` 응답에서 raw dict 를 추출. `(data, type_name)` 반환."""
    data = getattr(response, "__data__", None)
    if isinstance(data, dict):
        return data, type(response).__name__
    if isinstance(response, dict):
        return response, type(response).__name__
    return None, type(response).__name__


def _coerce_json_safe(value: Any) -> Any:
    """dict/list/primitive 만 허용. KisDynamic 등 비-표준 타입은 `repr` 로 폴백."""
    if isinstance(value, dict):
        return {str(k): _coerce_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_coerce_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)


def run_diagnostic(args: argparse.Namespace) -> dict[str, Any]:
    """1회 호출 + 진단 정보 수집. 반환 dict 는 그대로 JSON 덤프."""
    settings = get_settings()
    if not settings.has_live_keys:
        raise RuntimeError(
            "실전 키 3종(KIS_LIVE_APP_KEY · KIS_LIVE_APP_SECRET · KIS_LIVE_ACCOUNT_NO) "
            "이 필요합니다. `.env` 를 확인하세요."
        )

    kis = _build_pykis(settings)
    date_str = args.trade_date.strftime("%Y%m%d")
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": args.symbol,
        "FID_INPUT_HOUR_1": args.cursor,
        "FID_INPUT_DATE_1": date_str,
        "FID_PW_DATA_INCU_YN": "N",
        "FID_FAKE_TICK_INCU_YN": "",
    }

    logger.info(
        "debug.kis_minute.request symbol={s} date={d} cursor={c}",
        s=args.symbol,
        d=date_str,
        c=args.cursor,
    )
    try:
        response = kis.fetch(_API_PATH, api=_TR_ID, params=params, domain="real")
    except Exception as exc:
        raise RuntimeError(f"KIS fetch 호출 실패: {exc}") from exc

    data, response_type = _extract_raw_data(response)
    diagnostic: dict[str, Any] = {
        "request": {
            "api_path": _API_PATH,
            "tr_id": _TR_ID,
            "symbol": args.symbol,
            "date": date_str,
            "cursor": args.cursor,
        },
        "response": {
            "type": response_type,
            "has_data_attr": hasattr(response, "__data__"),
            "root_keys": sorted(data.keys()) if data else None,
            "rt_cd": data.get("rt_cd") if data else None,
            "msg_cd": data.get("msg_cd") if data else None,
            "msg1": data.get("msg1") if data else None,
        },
        "output2": {
            "length": None,
            "first_row_type": None,
            "first_row_keys": None,
            "sample_rows": None,
            "include_values": bool(args.include_values),
        },
    }

    if data is None:
        return diagnostic

    output2 = data.get("output2")
    if not isinstance(output2, list):
        diagnostic["output2"]["length"] = 0
        diagnostic["output2"]["first_row_type"] = type(output2).__name__
        return diagnostic

    diagnostic["output2"]["length"] = len(output2)
    diagnostic["output2"]["include_values"] = bool(args.include_values)
    if output2:
        first = output2[0]
        diagnostic["output2"]["first_row_type"] = type(first).__name__
        if isinstance(first, dict):
            diagnostic["output2"]["first_row_keys"] = sorted(first.keys())
        if args.include_values:
            diagnostic["output2"]["sample_rows"] = [_coerce_json_safe(row) for row in output2[:3]]
        else:
            diagnostic["output2"]["sample_rows"] = [
                sorted(row.keys()) if isinstance(row, dict) else type(row).__name__
                for row in output2[:3]
            ]

    return diagnostic


def _print_human(diagnostic: dict[str, Any]) -> None:
    req = diagnostic["request"]
    res = diagnostic["response"]
    out = diagnostic["output2"]

    logger.info(f"request: symbol={req['symbol']} date={req['date']} cursor={req['cursor']}")
    logger.info(f"response.type: {res['type']}  has_data_attr: {res['has_data_attr']}")
    logger.info(f"response.rt_cd: {res['rt_cd']}  msg_cd: {res['msg_cd']}  msg1: {res['msg1']}")
    logger.info(f"response.root_keys: {res['root_keys']}")
    logger.info(f"output2.length: {out['length']}  first_row_type: {out['first_row_type']}")
    logger.info(f"output2.first_row_keys: {out['first_row_keys']}")
    logger.info(f"output2.include_values: {out.get('include_values')}")
    sample = out.get("sample_rows") or []
    for idx, row in enumerate(sample):
        logger.info(f"output2[{idx}]: {row!r}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        _validate_args(args)
    except RuntimeError as exc:
        logger.error(f"입력 오류: {exc}")
        return _EXIT_INPUT_ERROR

    try:
        diagnostic = run_diagnostic(args)
    except RuntimeError as exc:
        logger.error(f"설정·입력 오류: {exc}")
        return _EXIT_INPUT_ERROR

    _print_human(diagnostic)

    output_path = _resolve_output_path(args.output_json)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(diagnostic, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.exception(f"JSON 덤프 쓰기 실패: {output_path}: {exc}")
        return _EXIT_IO_ERROR

    logger.info(f"debug.kis_minute.done output={output_path}")
    return _EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
