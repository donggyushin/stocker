# C3 — 069500 KODEX 200 일봉 수정주가 plausibility 검증 (2026-05-03)

> **작성**: 2026-05-03. ADR-0023 의 Phase 3 진입 조건 4종 중 **C3 (069500 일봉 수정주가 보정 plausibility 검증)** 결과.

## 컨텍스트

ADR-0023 (2026-05-02) 가 F5 RSI 평균회귀 (`RSIMRStrategy`) 를 Step F 1차 채택 후보로 선정하며 Phase 3 진입 조건으로 4 추가 검증 (C1~C4) 명시. 본 런북은 C3 검증 — pykrx 일봉이 액면분할·병합·배당·분배금 수정주가 보정을 적용하는지, 그리고 캐시 (`data/stock_agent.db`) 에 적층된 069500 일봉이 외부 reference 와 정합한지 직접 비교로 확정.

본 검증은 ADR-0023 의 caveat 3 (069500 1년 +180% 비현실적) 데이터 측 원인 확정 + Step F 전체 절대 수익률 수치의 신뢰도 baseline 결정. PR1 DCA baseline (+51.50%) · PR2 Golden Cross (+182.36%) · PR5 RSI MR (+56.31%) 모두 동일 데이터 소스 (pykrx 일봉 캐시) 이므로 본 검증 결과가 절대 수익률 해석의 정본.

## 검증 4 단계

| Stage | 비교 대상 | 의미 |
|---|---|---|
| 1 | pykrx `adjusted=True` (캐시 default) vs `adjusted=False` close | 수정주가 적용 여부 + 분배·분할 이벤트 추적 |
| 2 | 069500 ETF / KOSPI 200 인덱스 (1028) 비율 시계열 | NAV 추적 정합성. 비율 점프 = 분배·분할·데이터 오염 신호 |
| 3 | `data/stock_agent.db` 캐시 close vs pykrx `adjusted=True` close | 백필 시점 (2026-05-02 C1) 의 캐시가 수정주가 데이터인지 확정 |
| 4 | 외부 reference (Google Finance · Wikipedia KOSPI 200) cross-check | 절대 가격 plausibility 확정 |

## 실행 명세

```bash
uv run python scripts/verify_069500_adjusted.py \
  --from 2024-06-01 --to 2026-04-21 \
  --db-path data/stock_agent.db \
  --output-json data/c3_verify_069500.json \
  --ratio-jump-threshold-pct 1.0
```

검증 구간 `2024-06-01 ~ 2026-04-21`: ADR-0023 PR2 Golden Cross 평가 구간 (caveat 발원 지점) 정렬. 458 영업일.

## 결과

### Stage 1 — pykrx adjusted=True vs adjusted=False

```
Error occurred in get_stock_ticker_isin: 'NoneType' object is not subscriptable
Error occurred in get_market_ohlcv_by_date: "None of [Index(['TRD_DD', 'TDD_OPNPRC',
       'TDD_HGPRC', 'TDD_LWPRC', 'TDD_CLSPRC', 'ACC_TRDVOL', 'ACC_TRDVAL',
       'FLUC_RT'], dtype='object')] are in the [columns]"
```

- `adjusted=True`: 458 행 정상 반환.
- `adjusted=False`: **0 행** — pykrx 1.2.7 의 `get_market_ohlcv_by_date(..., adjusted=False)` 경로가 KRX 응답 컬럼 schema 변경 (현재 KRX 응답이 `TRD_DD` 등 영문 컬럼 미포함) 으로 파싱 실패.

→ Stage 1 직접 비교 **불가**. 수정주가 적용 여부는 Stage 2 + 3 의 우회 검증으로 확정.

### Stage 2 — 069500 / KOSPI 200 (1028) 비율 시계열

ETF 는 NAV 추적이라 ETF / index 비율이 거의 일정해야 함. 비율 점프 (전일 대비 |Δ| ≥ 1%) = 분배·분할·데이터 오염 신호.

| 통계 | 값 |
|---|---|
| 표본 수 | 458 영업일 |
| 평균 비율 | 99.13 |
| 표준편차 | 1.16 |
| 최소 | 97.11 |
| 최대 | 101.31 |
| start_ratio (2024-06-03) | 97.27 |
| end_ratio (2026-04-21) | 100.72 |
| end / start | 1.0355 (+3.55% drift) |
| (max - min) / mean | **4.23%** |
| 점프 (\|Δ\| ≥ 1%) | **0건** |

→ 비율 변동 4.23% 는 KOSPI 200 구성종목 거래 시각 차이 + ETF 운용보수·추적오차의 통상 범위. 점프 0건 = 분배·분할 이벤트가 ETF 가격에 정확히 보정 적용됨 또는 검증 구간 내 corporate action 부재. 데이터 내부 정합성 확정.

### Stage 3 — 캐시 vs pykrx adjusted=True

| 항목 | 값 |
|---|---|
| 캐시 행 수 | 458 |
| pykrx adjusted=True 행 수 | 458 |
| close 차이 일자 수 | **0** |

→ `data/stock_agent.db` 캐시는 pykrx `adjusted=True` (default) 결과와 모든 일자 close 가 완전 일치. 즉 백필 (2026-05-02 C1) 시점 캐시는 **수정주가 데이터**. C1 + C2 + Step F PR1~PR5 평가의 절대 수익률은 모두 수정주가 기반.

### Stage 4 — 외부 reference cross-check

#### 4a. Google Finance 069500:KRX (2026-04-30 시점)

| 항목 | Google Finance | 캐시 (2026-04-21) | 정합 |
|---|---|---|---|
| 현재가 | ₩99,905 (Apr 30) | 96,920 | +3% drift (8 영업일) plausible |
| 52주 최고 | ₩102,230 | — | 캐시 max ~96,920 + 그 후 +5% |
| 52주 최저 | ₩33,690 | 34,772 (2025-05-15) | -3% drift (~5 영업일) 정합 |

→ 캐시의 절대 가격이 외부 시장가 reference 와 정합.

#### 4b. Wikipedia KOSPI 200 인덱스 absolute level

| 시점 | KOSPI 200 (Wikipedia) | Stage 2 추정 (069500/100.72 또는 /97.27) |
|---|---|---|
| 2024 close | 317.82 (-11.22%) | — |
| 2025 close | 605.98 (+90.67%, 사상최고) | — |
| 2024-06-03 (검증 시작) | — | ETF 35,549 / ratio 97.27 ≈ **365.5** |
| 2026-04-21 (검증 종료) | — | ETF 96,920 / ratio 100.72 ≈ **962.3** |

KOSPI 본 인덱스 마일스톤 (Wikipedia):
- 2025-10-27: 4,000
- 2026-01-27: 5,000
- 2026-02-25: 6,000
- 2026-04-27: 6,500
- 2026-04-29: 6,690.90 (사상최고)

검증: 2025 말 KOSPI 200 close 605.98 → 2026-04-21 추정 962.3. KOSPI 본 인덱스 2025-12-31 ~ 2026-04-29 상승률 4,214 → 6,690 (+58.7%) 적용 시 605.98 × 1.587 ≈ **961.9** — Stage 2 추정 962.3 과 거의 정확 일치.

→ 069500 ETF 가격 +172.7% (35,549 → 96,920) 는 **한국 KOSPI 200 강세장 macro 의 결과**. 데이터 오염 아님. PR2 caveat (1년 +180%) 의 원인 = 시장 상승률 자체이며 데이터 보정 오류 아님.

#### 4c. corporate actions (분배·분할·병합) 직접 확인

scope 외 — KIS Developers 권리주주 endpoint 가 `broker/kis_client.py` 미구현. Samsung Asset Management 분배 페이지 webfetch 시도 실패 (404 / 동적 컨텐츠). Stage 2 ratio 점프 **0건** 이 강력한 간접 증거 — 분배·분할 이벤트가 가격에 잘못 반영됐다면 ratio 가 점프해야 함. 0건 = 정확 보정 또는 이벤트 부재.

ETF 분배금 측면: KODEX 200 (069500) 은 통상 4월 / 7월 / 10월 / 1월 분기 분배 + 기타 결산 분배. 검증 구간 (2024-06-01 ~ 2026-04-21) 에 7~8 회 분배 이벤트 발생 추정. Stage 2 ratio 점프 0건 = 모두 정확 보정 (pykrx adjusted=True 가 분배금 재투자 효과까지 가격에 반영) 으로 해석.

## 종합 판정: **PASS**

ADR-0023 C3 (069500 일봉 수정주가 plausibility 검증) **통과**.

| 검증 항목 | 결과 |
|---|---|
| 캐시 = pykrx adjusted=True 결과 (Stage 3) | PASS — 0 diff |
| ETF/KOSPI200 비율 안정성 (Stage 2) | PASS — 점프 0 / 변동 4.23% |
| 외부 가격 reference 정합 (Stage 4a) | PASS — Google Finance ±3% drift |
| KOSPI 200 absolute level cross-check (Stage 4b) | PASS — 추정 962.3 vs 외부 961.9 |

→ **PR1 DCA baseline (+51.50%) · PR2 Golden Cross (+182.36%) · PR5 RSI MR (+56.31%) 의 절대 수익률 수치는 데이터 보정 오류가 아닌 시장 강세장 macro 의 결과로 확정**. ADR-0022 게이트 2 (DCA 대비 알파) 비교 자체의 신뢰도는 baseline·전략 모두 동일 데이터 소스라 무관했으나, **절대 수익률 절대값의 운영자 해석 baseline 이 본 검증으로 확정**된다.

## 제한 사항 / 잔존 caveat

- **Stage 1 직접 비교 미실행**: pykrx 1.2.7 + KRX 응답 schema 변경으로 `adjusted=False` 경로 자체가 작동 불가. 본 검증은 우회 (Stage 2 + 3) 로 결론 도출. pykrx 측 패치 (또는 KRX 응답 안정화) 후 직접 비교 재실행 권장 — 단 Stage 2 ratio 점프 0건이 강력한 우회 증거라 운영 가치 추가는 marginal.
- **corporate actions 직접 호출 미실행**: KIS Developers 권리주주 endpoint 가 broker 미구현. 별도 PR 로 분리 — 본 검증 측면에서는 간접 증거로 충분.
- **검증 구간 단일**: 2024-06-01 ~ 2026-04-21 단일 1.9년 구간. 더 긴 (2~3년) 또는 약세장 구간에서의 분배 보정 robustness 는 미검증. 현재 KIS 보관 한도 (1년 분봉) 와 별개로 일봉은 pykrx 가 더 긴 history 제공 — 후속 walk-forward (C2) 로 보강.
- **069500 단일 종목**: universe 199 종목 전체의 수정주가 정합성은 별도 검증 미실행. 069500 ETF 가 KOSPI 200 추적이라 macro 정합성으로 충분 평가됐으나, 개별 주식 (특히 분할/병합 이력 풍부 종목) 은 별도 spot-check 권장 — 다만 Step F 평가는 cross-sectional 평균회귀라 종목간 상대 가격 정확성이 핵심이고, 절대값 보정은 mean-reversion 신호 산출에 영향 없음.

## ADR-0023 C3 통과 — Phase 3 진입 잔여 검증

| 검증 | 상태 |
|---|---|
| C1 (universe 199 백필 + 재평가) | **PASS** (2026-05-02) |
| C2 (walk-forward 검증) | **PASS** (2026-05-02) |
| **C3 (069500 수정주가 plausibility)** | **PASS** (2026-05-03, 본 런북) |
| C4 (PR5 sensitivity grid) | 잔여 |

C4 통과 후 Phase 3 진입 재허가 + Phase 2 PASS 공식 선언 (ADR-0023 결정 3).

## 산출물

- `scripts/verify_069500_adjusted.py` — C3 검증 CLI (재실행 가능, idempotent).
- `data/c3_verify_069500.json` — Stage 1~3 raw 결과.
- `docs/runbooks/c3_069500_adjusted_plausibility_2026-05-03.md` — 본 런북.

## 참조

- ADR-0023 — F5 RSI 평균회귀 1차 채택 (조건부, C1~C4 명시).
- ADR-0022 — Step F 게이트 재정의.
- `docs/runbooks/c1_universe_full_backfill_2026-05-02.md` — C1 결과.
- `docs/runbooks/c2_walk_forward_rsi_mr_2026-05-02.md` — C2 결과.
- `docs/runbooks/step_f_golden_cross_2026-05-02.md` — PR2 caveat 발원 (069500 +180%).
- `docs/runbooks/step_f_summary_2026-05-02.md` — Step F 종합 판정.
