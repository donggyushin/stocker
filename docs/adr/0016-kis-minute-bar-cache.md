---
date: 2026-04-22
status: 승인됨
deciders: donggyu
related: [0002-paper-live-key-separation.md, 0003-runtime-error-propagation.md, 0008-single-process-only.md, 0013-sqlite-trading-ledger.md]
---

# ADR-0016: KIS 과거 분봉 어댑터 — `data/minute_bars.db` 별도 파일 + `kis.fetch()` 로우레벨 호출

## 상태

승인됨 — 2026-04-22.

## 맥락

Phase 2 백테스트는 그동안 `MinuteCsvBarLoader` 한 가지 경로만 지원했고, 운영자가 2~3년치 과거 분봉을 외부에서 CSV 로 변환해 `data/minute_csv/{symbol}.csv` 에 채워 넣어야만 백테스트·민감도 그리드가 돌았다. 이 수작업이 Phase 2 PASS 판정 (MDD > -15%) 의 선행 조건을 블로킹했고, Issue #5 "2~3년 백테스트" 도 데이터 수집 지연에 묶여 출발하지 못했다. Issue #35 는 이 병목을 풀기 위해 KIS Developers 의 과거 분봉 API 를 `BarLoader` Protocol 뒤에 어댑터로 숨긴 `KisMinuteBarLoader` 를 추가한다.

구현 경계에서 아래 네 가지 설계 결정이 필요했다.

1. **python-kis 래핑 vs 로우레벨 호출**: python-kis 2.1.6 의 `day_chart(kis, symbol, market, start, end)` 는 인자 타입이 `start: time | timedelta | None`, `end: time | None` 으로 **"당일 내 시간 범위" 만 지원**한다. `domestic_day_chart` 는 `/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice` (TR `FHKST03010200`) 를 호출 — **과거 일자 분봉용 `inquire-time-dailychartprice` (TR `FHKST03010230`) 는 래핑하지 않는다**. 2024-05 이후 KIS 가 공식 추가한 API 를 python-kis 가 아직 따라잡지 못한 상태.
2. **SQLite 캐시 저장소 위치**: 기존 `data/stock_agent.db` (일봉 캐시, 스키마 v3) 에 `minute_bars` 테이블을 추가하고 스키마 v4 마이그레이션 경로를 확장할지, 아니면 `data/trading.db` (원장, ADR-0013) 분리 선례와 대칭으로 **`data/minute_bars.db` 별도 파일** 을 신설할지.
3. **보관 기간 한계 대응**: KIS 서버는 분봉을 **최대 1년만 보관** (공식 샘플 주석 명시 — `koreainvestment/open-trading-api:examples_llm/domestic_stock/inquire_time_dailychartprice/inquire_time_dailychartprice.py` line 41: "최대 1년 분봉 보관"). 2~3년 요구와 직접 충돌.
4. **페이지네이션·레이트 리밋·실전 키 가드**: 1 회 호출 최대 120 건 응답. KIS 레이트 리밋(`EGW00201`) 재시도 정책. 실전 키가 반드시 필요하므로 paper 경로 분리와 주문 차단 가드 설치.

검토한 대안:

- **python-kis 에 과거 분봉 API 를 자체 fork 하여 공개 API 로 래핑**: 업스트림 패치 제출이 본 PR 범위 밖이며, 운영 필요성은 `kis.fetch(...)` 로우레벨 호출 하나로 충족된다. python-kis 업데이트 시 계약이 달라질 위험은 감수 — 엔드포인트 경로·TR_ID 는 KIS Developers 포털 공식 스펙이라 python-kis 버전 교체와 독립. → **거부**.

- **`data/stock_agent.db` 에 `minute_bars` 테이블 추가 + 스키마 v4 마이그레이션**: 일봉·분봉을 한 파일에 몰면 (a) 분봉 누적 크기가 커지면서(200 종목 × 390 bar/day × 1 년 ≈ 2 천만 행) 파일 전체 백업·복구·삭제 비용이 일봉 캐시와 연결돼 불편, (b) 스키마 v4 마이그레이션은 v3 보존 경로를 새로 짜야 해 기존 `historical.py` 테스트 범위가 확장됨, (c) 장기적으로 일봉 캐시 정책과 분봉 캐시 정책은 TTL·정리 전략이 달라질 가능성이 높음(분봉은 1 년 KIS 보관 제약, 일봉은 pykrx 영구). → **거부**.

- **캐시 없이 매 백테스트 KIS API 재호출**: 구현 단순. 단점: (a) 2~3 년 KOSPI 200 전 종목 = 수백만 건 API 호출 → KIS 일일 상한 소진 + 백테스트 1 회 실행 수 시간, (b) 동일 구간을 민감도 그리드 32 조합 × 4 회 재호출. → **거부**. 캐시는 필수.

- **Phase 2 PASS 기준을 "1 년 MDD > -15%" 로 완화** (결정 B 후보): KIS 서버 1 년 보관 제약을 수용해 백테스트 검증 범위를 축소. 단점: (a) plan.md Phase 2 명시 기준은 2~3 년이라 결정 번복 필요, (b) 1 년 표본은 시장 국면 편향(2025 년이 강세장이면 과대평가) 에 취약. → **범위 밖** — 본 ADR 은 어댑터 도입만 결정. 2~3 년 데이터 소스는 Issue #5 후속으로 외부 벤더·KRX 유료 분리 검토.

- **별도 장기 백필 스크립트 (`scripts/backfill_minute_bars.py`) 를 본 PR 에 포함**: 어댑터 + 백필 + 리포트 3 종을 1 PR 에. 단점: (a) 수 시간~수 일 러닝이라 retry·체크포인트·진행률 UX 설계가 PR 범위를 두 배로 키움, (b) 본 PR 은 어댑터 · CLI 스위치 · 1 주일 smoke DoD 로 이미 충분한 작업 단위. → **범위 밖** — 후속 PR.

## 결정

1. **`src/stock_agent/data/kis_minute_bars.py`** 신설 — `KisMinuteBarLoader` (`BarLoader` Protocol 준수) + `KisMinuteBarLoadError`. `data/__init__.py` 에 두 심볼 공개.

2. **KIS API 는 `kis.fetch(...)` 로우레벨 직접 호출** — python-kis 의 세션·토큰·내장 rate limiter 재사용하되 엔드포인트·TR_ID·응답 dict 파싱은 어댑터가 담당. 경로 `"/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"`, TR `"FHKST03010230"`, `domain="real"`. params: `FID_COND_MRKT_DIV_CODE="J"`, `FID_INPUT_ISCD=<symbol>`, `FID_INPUT_HOUR_1=<HHMMSS>`, `FID_INPUT_DATE_1=<YYYYMMDD>`, `FID_PW_DATA_INCU_YN="N"`, `FID_FAKE_TICK_INCU_YN=""`. 응답 파싱: `output2` 리스트의 각 행을 `stck_bsop_date`·`stck_cntg_hour`·`stck_oprc`·`stck_hgpr`·`stck_lwpr`·`stck_prpr`·`cntg_vol` 기준으로 `MinuteBar` DTO 로 변환 — **가격은 `Decimal(str)` 파싱** (float 우회 금지).

3. **SQLite 캐시는 별도 파일** `data/minute_bars.db` (기본값, 생성자 인자로 override 가능). 스키마 v1 — `minute_bars(symbol TEXT, bar_time TEXT, open TEXT, high TEXT, low TEXT, close TEXT, volume INTEGER, PRIMARY KEY(symbol, bar_time))` + `schema_version(version INTEGER PRIMARY KEY)`. 가격·bar_time 은 `TEXT` (Decimal 정밀도 + ISO8601 with tz). PRAGMA `journal_mode=WAL` (파일 모드 한정) + `synchronous=NORMAL` + `foreign_keys=ON`. 스키마 초기화는 `BEGIN IMMEDIATE` 감싸 멱등 실행.

4. **캐시 판정은 `(symbol, 날짜)` 단위** — 특정 날짜에 bar 가 한 건이라도 DB 에 있으면 해당 날짜는 캐시됨으로 간주해 API 재호출 생략. **`date == clock().date()` 인 "오늘" 자는 항상 재조회** 하고 DB 에도 쓰지 않는다 — 장중에는 분봉이 확정되지 않아 캐시하면 다음 호출에서 stale 데이터가 돌아온다. `historical.py` 의 end 날짜 휴리스틱과 동일 기조.

5. **페이지네이션은 120 건 역방향 커서** — 최초 호출 커서 `FID_INPUT_HOUR_1="153000"`. 응답의 가장 이른 시각을 다음 호출 커서로 재주입 (1 분 감소). 종료 조건 (OR): `len(output2) < 120` · `min_time <= "090000"`. 페이지 사이 `throttle_s` (기본 0) 로 옵션 스로틀.

6. **레이트 리밋 재시도** — 응답 `rt_cd != "0"` 이고 `msg_cd == "EGW00201"` 이면 `sleep(rate_limit_wait_s=61.0)` 후 동일 params 로 재시도. 최대 `rate_limit_max_retries=3` (총 4 회 호출). 초과 시 `KisMinuteBarLoadError`. 다른 에러 코드는 재시도 없이 즉시 `KisMinuteBarLoadError` 승격.

7. **실전 키 fail-fast** — `settings.has_live_keys == False` 이면 생성자에서 즉시 `KisMinuteBarLoadError`. 실전 PyKis 인스턴스 생성 직후 **`install_order_block_guard(kis)` 설치 필수** (`realtime.py` 와 동일) — `/trading/order*` 경로를 도메인 무관 차단해 본 어댑터가 주문을 낼 수 없음을 구조적으로 보장. ADR-0002 (paper/live 분리) 기조 유지.

8. **`scripts/backtest.py` + `scripts/sensitivity.py` 에 `--loader={csv,kis}` 옵션** — default `"csv"` (하위 호환). `--loader=csv` 일 때 `--csv-dir` 필수(parse 단계 `parser.error()` 로 `SystemExit`), `--loader=kis` 는 `--csv-dir` 무시. `_build_loader(args)` 헬퍼로 분기 집중화. `_run_pipeline` 은 `try/finally` 로 `loader.close()` 멱등 호출 (KIS 경로 SQLite 정리). exit code 매핑에 `KisMinuteBarLoadError → 2` 추가.

9. **KIS 서버 1 년 보관 제약은 본 ADR 범위 밖 문서로 명시** — 어댑터는 "최근 1 년 자동 갱신" 용도. Phase 2 2~3 년 백테스트 요구는 Issue #5 후속으로 별도 데이터 소스(외부 벤더·KRX 유료) 평가. 본 PR 은 1 주일 smoke (005930, 평일 장중) 를 DoD 로 함.

10. **범위 밖 (후속 PR)**:
    - 대량 백필 전용 CLI (`scripts/backfill_minute_bars.py`) — retry·체크포인트·진행률.
    - 체결량 `cntg_vol` 해석 정합성 검증 (실시간 `realtime.py` 의 누적/델타 이슈와 별개 — 과거 분봉은 분당 누적값).
    - `daily_orders()` 와의 비교 검증 — 백테스트 체결가 정확도 개선.
    - 호가 단위 라운딩 (`historical.py` 와 동일 기조로 현재는 raw Decimal 그대로).

## 결과

**긍정**
- `MinuteCsvBarLoader` 와 교체 가능한 `BarLoader` 로 어댑터가 제공되어 운영자가 CSV 수작업 없이 `--loader=kis` 스위치만으로 최근 1 년 백테스트 갱신 가능. 민감도 그리드 32 조합 재실행 시 캐시 hit 으로 두 번째 런부터 API 호출 0.
- 캐시 DB 분리로 일봉 캐시(ADR 없음, `historical.py` v3) 와 생명주기 독립 — 파일 삭제만으로 분봉 캐시 초기화, `stock_agent.db` 손상 위험 0.
- 실전 키 전용 경로 + `install_order_block_guard` 로 어댑터가 주문을 낼 수 없음이 구조적으로 보장되어 ADR-0002 키 분리 원칙과 정합.
- `kis.fetch()` 로우레벨 호출은 python-kis 래핑 계약 변경과 분리되어 라이브러리 업그레이드 영향 최소화. 엔드포인트·TR_ID 는 KIS Developers 포털 공식 스펙 기준.
- `--loader` 스위치는 default `"csv"` 라 기존 CLI 테스트 65 + 55 건 회귀 0. 기존 CSV 파이프라인은 그대로 작동.

**부정**
- python-kis 가 향후 과거 분봉 API 를 공식 래핑하면 본 어댑터가 중복이 된다. 업스트림 채택 시점에 어댑터 제거 또는 공식 API 로 교체하는 후속 ADR 필요.
- KIS 1 년 보관 제약 때문에 Issue #5 "2~3 년 백테스트" 는 본 어댑터로 해결 불가 — 별도 데이터 소스 조달이 선행. Phase 2 PASS 판정 일정이 데이터 벤더 계약·가격 협상에 의존.
- `cntg_vol` 해석 정확도는 본 ADR 에서 실측하지 않는다 — 실전 대비 백테스트 괴리가 volume 관련 전략 지표에서 발생할 수 있으나 ORB 는 현재 OHLC 기반이라 영향 작음.
- 페이지네이션 커서 `min_time - 1분` 감소 로직은 KIS 응답이 초 단위 분 경계(`HHMM00`) 만 돌려준다는 가정. 초가 다른 bar 가 섞여 오면 커서가 과대 감소해 누락 가능 — 현재 샘플 코드 관찰상 KIS 는 분 경계만 반환하나, 실운영 중 이상 발견 시 재평가.

**중립**
- SQLite WAL 모드는 파일 경로 한정 — `:memory:` 는 WAL 미적용 (sqlite3 제약). 테스트는 `tmp_path` 또는 `":memory:"` 둘 다 허용.
- 어댑터는 `close()` 멱등 + 컨텍스트 매니저 지원. CLI 는 `try/finally` 로 감싸 정리. `MinuteCsvBarLoader` 는 `close()` 가 없으므로 `getattr(loader, "close", None)` 로 안전 호출.
- `rate_limit_max_retries=3` · `rate_limit_wait_s=61.0` 기본값은 KIS 공식 샘플(`backtester/kis_backtest/providers/kis/data.py`) 의 `_RATE_LIMIT_WAIT = 61` · `_RATE_LIMIT_MAX_RETRIES = 3` 과 정합.

## 추적

- 코드: `src/stock_agent/data/kis_minute_bars.py` (신규), `src/stock_agent/data/__init__.py` (공개 심볼), `scripts/backtest.py` (`--loader` 분기), `scripts/sensitivity.py` (`--loader` 분기).
- 테스트: `tests/test_kis_minute_bar_loader.py` (신규, 39 케이스 — 생성자·live 키·심볼·날짜 검증·단일/다중 페이지·다중 날짜·heapq 병합·레이트 리밋·응답 파싱·캐시·스키마·가드·재진입·예외 래핑·throttle·멱등 close·out-of-range·malformed 행).
- 후속 PR: `scripts/backfill_minute_bars.py` — 결정 10 의 대량 백필 전용 CLI 이행 (Issue #47, branch `issue_47`).
- 관련 ADR: [ADR-0002](./0002-paper-live-key-separation.md) (paper/live 키 분리), [ADR-0003](./0003-runtime-error-propagation.md) (RuntimeError 전파), [ADR-0008](./0008-single-process-only.md) (단일 프로세스), [ADR-0013](./0013-sqlite-trading-ledger.md) (DB 파일 분리 선례).
- 문서: [data/CLAUDE.md](../../src/stock_agent/data/CLAUDE.md), [backtest/CLAUDE.md](../../src/stock_agent/backtest/CLAUDE.md), [plan.md](../../plan.md), root [CLAUDE.md](../../CLAUDE.md), Issue #5 (2~3 년 데이터 소스 분리 논의).
- 도입 PR: TBD (Issue #35).
