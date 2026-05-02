# Step F 가설 풀 확장 — 종합 판정 (2026-05-02)

> **작성**: 2026-05-02. ADR-0019 Step F PR1~PR5 결과 종합 + 시나리오 판정 + ADR-0023 채택 결정 근거.

## 컨텍스트

ADR-0021 (2026-05-01) 로 Step E (VWAP-MR · Gap-Reversal) 폐기 후 일중 데이트레이딩 가정을 폐기하고 Step F (가설 풀 확장 + DCA baseline 비교) 로 전환. ADR-0022 게이트 (MDD > -25% · DCA baseline 대비 양의 알파 · 연환산 Sharpe > 0.3) 적용.

`docs/step_f_strategy_pool_plan.md` 의 PR0~PR6 분할로 5 가설 (F1 DCA baseline · F2 Golden Cross · F3 Cross-sectional 모멘텀 · F4 저변동성 · F5 RSI 평균회귀) 평가 완료. 본 런북은 PR6 종합 판정 + 채택 결정 근거.

## 평가 결과 요약

### Step F 5 후보 비교 표

| PR | 가설 | 평가 구간 | 거래 수 | MDD | Sharpe | 총수익률 | DCA 알파 | 게이트 1 (MDD>-25%) | 게이트 2 (DCA 알파>0) | 게이트 3 (Sharpe>0.3) | 종합 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| PR1 | F1 DCA baseline | 2025-04-22 ~ 2026-04-21 | 13 lots | -12.92% | 2.2683 | +51.50% | N/A (자기 자신) | PASS | N/A | PASS | **PASS** |
| PR2 | F2 Golden Cross (200d SMA) | 2024-06-01 ~ 2026-04-21 | 1 | -20.52% | 2.2753 | +182.36% | +130.86%p (vs PR1) | PASS | PASS | PASS | **PASS (단 caveat)** |
| PR3 | F3 Cross-sectional 모멘텀 (lookback 6m, top10) | 2025-04-01 ~ 2026-04-21 | 14 | -7.70% | 0.9910 | +11.22% | -36.96%p (vs same-window DCA +48.18%) | PASS | FAIL | PASS | **FAIL** |
| PR4 | F4 저변동성 (lookback 60d, top10, Q rebalance) | 2025-04-01 ~ 2026-04-21 | 19 | -9.62% | 1.1713 | +15.87% | -32.31%p (vs same-window DCA +48.18%) | PASS | FAIL | PASS | **FAIL** |
| PR5 | F5 RSI 평균회귀 (RSI14, 30/70, stop -3%) | 2025-04-01 ~ 2026-04-21 | 175 | -6.40% | 2.4723 | +56.31% | +8.13%p (vs same-window DCA +48.18%) | PASS | PASS | PASS | **PASS** |

### DCA baseline 비교 기준 분기

| 비교 baseline | 평가 구간 | 총수익률 | 인용 PR |
|---|---|---|---|
| PR1 자체 baseline | 2025-04-22 ~ 2026-04-21 | +51.50% | PR2 (069500 단일 종목 정렬 위해 PR1 직접 인용) |
| Same-window DCA | 2025-04-01 ~ 2026-04-21 | +48.18% | PR3 / PR4 / PR5 (multi-symbol cross-sectional 평가 구간 정렬) |

PR2 와 PR3~5 의 DCA 비교 기준이 다른 이유 — PR2 는 069500 단일 종목 + 200d SMA lookback 으로 2024-06-01 부터 시작해야 평가 가능했고, PR3~5 는 KOSPI 200 캐시 101종목 cross-sectional 평가가 가능한 2025-04-01 시작. 두 baseline 모두 +48~52% 구간으로 정렬됨 (KOSPI 200 1년 강세 환경).

## 시나리오 판정

`docs/step_f_strategy_pool_plan.md` PR6 절 시나리오 표 적용:

| # | 결과 패턴 | 본 평가 일치 여부 |
|---|---|---|
| A | F2~F5 중 1+ 가 게이트 3종 동시 통과 (DCA 도 PASS) | **충족** — PR5 (RSI MR, trades=175) + PR2 (Golden Cross, trades=1 caveat) 두 후보 + DCA PR1 도 PASS |
| B | F1 DCA 만 PASS, F2~F5 모두 FAIL | 불일치 |
| C | 전부 FAIL (DCA 도 FAIL) | 불일치 |

**시나리오 A 판정 — PASS 전략 채택 ADR + Phase 3 모의투자 진입 검토 단계로 이행**.

## 채택 후보 우선순위

PASS 후보 2종 — PR2 Golden Cross · PR5 RSI MR — 비교:

| 차원 | PR2 Golden Cross | PR5 RSI MR | 비교 우위 |
|---|---|---|---|
| 통계 신뢰도 (거래 수) | trades=1 — 표본 부족 | trades=175 — Step F 최고 | **PR5** |
| 게이트 2 알파 | +130.86%p (단일 trade 한계) | +8.13%p (175 trade 누적) | PR2 표면값, 단 PR5 가 통계 의미 |
| MDD | -20.52% | -6.40% | **PR5** |
| Sharpe | 2.2753 | 2.4723 | **PR5** |
| 데이터 plausibility | 069500 1년 +180% 비현실적 | cross-sectional 상대값 — 영향 적음 | **PR5** |
| 평가 구간 robustness | 단일 cross-up · cross-down 미발생 | 175 entry/exit pair 누적 | **PR5** |
| 학술 검증 | Faber 2007 / Hurst 2017 | Wilder 1978 — 일관 alpha 보고 약함 | PR2 |
| retail 운영 적합성 | 1~2회/년 진입 (저빈도, 단순) | 일평균 0.68 (자동매매 부담 보통) | PR2 단순성, PR5 자동화 강점 |

**1차 채택 후보 = PR5 (RSI 평균회귀)**.

선정 사유:
1. **trades=175 통계 신뢰도** — PR2 의 단일 trade 한계 우회. ADR-0017 의 240 영업일 표본 정신과 정합.
2. **MDD -6.40%** — Step F 전 후보 중 최저, ADR-0022 게이트 1 (-25%) 대비 18.6%p 여유.
3. **Sharpe 2.4723** — Step F 최고. 위험조정수익률 우월.
4. **데이터 plausibility 영향 최소** — cross-sectional 평균회귀라 인덱스 절대 가격 오차에 덜 민감 (PR2~PR4 와 차별).

## Caveat (Phase 3 진입 전 운영자 검토 필수)

ADR-0023 채택 결정에는 **다음 4 검증을 Phase 3 진입 조건으로 명시한다** (ADR-0023 본문 참조):

1. **universe 199 종목 전체 백필 + 재평가**: 본 평가는 `data/stock_agent.db` 캐시 101 종목 부분집합. KOSPI 200 universe 199 종목 (`config/universe.yaml`) 전체 백필 후 재평가 시 결과 변동 가능. `scripts/backfill_daily_bars.py --universe-yaml config/universe.yaml --from 2024-04-01 --to 2026-04-21` 로 백필 후 재실행.

2. **walk-forward 검증 (Phase 5 본 구현)**: 단일 1년 코호트 (2025-04 ~ 2026-04) 결과만으로 robustness 검증 부족. `src/stock_agent/backtest/walk_forward.py` 본 구현 (PR #70 스켈레톤 도입 완료) + 다년 KIS 분봉 또는 pykrx 일봉 캐시로 2~4 분할 walk-forward. 각 분할에서 ADR-0022 게이트 3종 동시 통과해야 진입 허가.

3. **069500 일봉 수정주가 보정 검증**: pykrx 일봉의 액면분할·병합·배당 수정 여부 미검증. PR1·PR2·PR3·PR4·PR5 절대 수익률 모두 동일 데이터 소스. KRX 정보데이터시스템 [11003/11006] 직접 비교로 데이터 신뢰성 확정 후에만 채택 결정 유효.

4. **PR5 파라미터 sensitivity grid**: stop_loss_pct (현행 -3%) · oversold/overbought (현행 30/70) · rsi_period (현행 14) · max_positions (현행 10) 민감도 평가. `step_f_grid` 신설 후 32~96 조합 스윕. 본 결과의 stop_loss=-3% 가 청산 사유 64.6% 차지하는 만큼 민감도 핵심 축.

위 4 항목 전부 통과 후에만 Phase 3 (모의투자 무중단 운영) 착수. ADR-0019 의 "수익률 확보 전 Phase 3 금지" 정책과 정합.

## 추가 보존 후보 (PR2 Golden Cross)

PR2 는 단일 trade caveat 로 1차 채택 우선순위에서 제외하나, 코드 산출물 (`strategy/golden_cross.py` · `backtest/golden_cross.py` · CLI 라우팅) 보존. 후속 옵션:

- **sma_period 단축 평가** — 50d / 100d 로 cross 사이클 표본 확대. 학술적으로 200d 가 정본이나 retail 운영 환경의 통계 신뢰도 확보 우선.
- **다년 평가 구간 확장** — pykrx 일봉 5~10년 백필 후 다중 cross 사이클 포착.
- **합성 전략 후보** — RSI MR 청산 룰 + Golden Cross 게이팅 (SMA 위에서만 진입) 등 ensemble 검토 (Phase 5 후보).

## 폐기 후보 (PR3 Momentum · PR4 저변동성)

두 가설 모두 게이트 2 (DCA 알파) FAIL. **본 평가 환경 (KOSPI 200 1년 강세장 + universe 부분집합) 한계 인정**:

- 모멘텀: Jegadeesh-Titman 1993 의 미국 1965~1989 결과는 다년·다국가 코호트 산출. 한국 단일 시장 1년 부분집합 으로 결론 도출 불가 — 본 평가의 negative 결과는 환경 특이값일 가능성. 코드 산출물 보존하되 채택 후보에서는 제외.
- 저변동성: Frazzini-Pedersen 2014 의 핵심 가정 (고베타 leverage-aversion premium) 은 다년 거시 사이클 가정. 1년 강세장 단일 코호트 평가는 본질적으로 환경 mismatch — 본 평가는 anomaly 가 작동 안 하는 정상 환경의 사실 기록.

두 코드 산출물 (`strategy/momentum.py` · `strategy/low_volatility.py` · 대응 backtest 모듈 + CLI 라우팅) 보존. Phase 5 다년 walk-forward 시 비교 baseline 으로 재사용 가능.

## ADR-0023 결정 요약

본 런북의 종합 판정에 따라 [ADR-0023](../adr/0023-rsi-mr-strategy-adoption-conditional.md) 작성:

- **결정**: F5 RSI 평균회귀 (`RSIMRStrategy`) 를 Step F 1차 채택 후보로 선정.
- **조건**: 위 Caveat 절 4 검증 (universe 전체 백필 + walk-forward + 069500 수정주가 검증 + PR5 sensitivity grid) 전부 통과 후에만 Phase 3 모의투자 진입 허가.
- **부결과**: PR2 Golden Cross 는 단일 trade caveat 로 1차 채택 보류 (코드 보존, 후속 검토). PR3 모멘텀 · PR4 저변동성은 본 평가 환경에서 채택 후보 제외 (코드 보존).
- **Phase 2 PASS 선언**: ADR-0019 의 일중 가정 게이트 (MDD>-15%·승×손익비>1.0·샤프>0) 는 Step F 평가에 적용하지 않음 (ADR-0022 의 게이트 재정의). PR5 가 ADR-0022 게이트 3종 통과 = Phase 2 백테스트 단계 PASS 조건 충족. 단 Phase 3 진입은 위 4 추가 검증 통과 후로 게이팅.

## Step F 결산 메트릭

- **신규 코드 모듈**: 5 strategy + 5 backtest baseline + 1 daily_bar_loader = 11 파일.
- **신규 테스트**: 79 (PR1) + 81 (PR2) + 85 (PR3) + 85 (PR4) + 85 (PR5) ≈ **415건 신규** (실제 collected 합산은 pytest 통계 기준).
- **pytest 진척**: PR1 진입 시점 ~1845 → PR5 완료 시점 **2140 collected**.
- **CLI 인자 신설**: `--strategy-type {dca,golden-cross,momentum,low-vol,rsi-mr}` + `--loader=daily` + 가설별 파라미터 (`--monthly-investment` · `--top-n` · `--lookback-months` · `--lookback-days` · `--rebalance-day` · `--rebalance-month-interval` · `--rsi-period` · `--oversold-threshold` · `--overbought-threshold` · `--stop-loss-pct` · `--max-positions`).
- **실행 백테스트 런 수**: PR1 1 런 + PR2 1 런 + PR3 2 런 (모멘텀 + same-window DCA) + PR4 1 런 (LowVol) + PR5 1 런 (RSI MR) = **6 런**. ADR-0019 Step A~E 230+ 런 대비 표본 효율 우월 (cross-sectional 가설로 단일 런 표본 풍부).

## 다음 단계

운영자 결정 항목:

1. **ADR-0023 검토 + 승인** — 본 런북 + ADR 본문으로 채택 결정 검토.
2. **Caveat 4 검증 우선순위 결정** — 4 검증 중 어느 것부터 착수할지 (universe 백필 우선 권장 — 데이터 인프라 작업으로 다른 검증의 선결 조건).
3. **PR5 1차 추가 검증 (universe 전체 + 데이터 plausibility) 통과 시** — walk-forward 본 구현 + sensitivity grid 진입.
4. **모든 Caveat 통과 시** — Phase 3 모의투자 무중단 10영업일 운영 착수.
5. **Step F PR0~PR5 코드 산출물 보존** — git 이력에 잔존, 회귀 비교·후속 ensemble 가능.
6. **`docs/step_f_strategy_pool_plan.md` 폐기** — 헤더 명시 (line 3) 에 따라 PR6 진입 후 삭제. 본 런북 + ADR-0023 이 후속 정본.

## 참조

- ADR-0019 — Phase 2 백테스트 FAIL 복구 로드맵 (Step A~E 일중 가정).
- ADR-0021 — Step E VWAP-MR · Gap-Reversal 폐기 + Step F 전환 결정.
- ADR-0022 — Step F 게이트 재정의 (MDD>-25%·DCA 알파·Sharpe>0.3).
- ADR-0023 — F5 RSI 평균회귀 1차 채택 (조건부, 본 런북과 동일 PR).
- `docs/runbooks/step_f_dca_baseline_2026-05-02.md` — F1 DCA baseline.
- `docs/runbooks/step_f_golden_cross_2026-05-02.md` — F2 Golden Cross.
- `docs/runbooks/step_f_momentum_2026-05-02.md` — F3 Cross-sectional 모멘텀.
- `docs/runbooks/step_f_low_volatility_2026-05-02.md` — F4 저변동성.
- `docs/runbooks/step_f_rsi_mr_2026-05-02.md` — F5 RSI 평균회귀.
- `data/step_f_*.{md,csv}` — 자동 생성 리포트·메트릭·체결 CSV.
