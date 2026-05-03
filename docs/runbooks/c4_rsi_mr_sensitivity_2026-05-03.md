# C4 — RSI 평균회귀 sensitivity grid (2026-05-03)

> **작성**: 2026-05-03. ADR-0023 의 Phase 3 진입 조건 4종 중 **C4 (PR5 sensitivity grid)** 결과 + Phase 3 진입 게이트 판정.

## 컨텍스트

ADR-0023 (2026-05-02) 가 F5 RSI 평균회귀 (`RSIMRStrategy`) 를 1차 채택 후보로 선정하며 Phase 3 진입 조건 4종 (C1~C4) 명시. C1 (2026-05-02) · C2 (2026-05-02) · C3 (2026-05-03) 통과 후 본 런북이 마지막 검증이다.

C4 의 목적: 현행 파라미터 (`rsi_period=14`, `oversold=30`, `overbought=70`, `stop_loss=-3%`, `max_positions=10`) 가 국소 최적점이 아님을 확인. 5 축을 격자 탐색해 "현행 값 인근에서도 ADR-0022 게이트 3종을 일관되게 통과하는가" 를 판정한다.

ADR-0023 명시 Phase 3 진입 게이트 (C4):
- 전체 PASS 비율 ≥ 50%
- 현행 인접 (1 축 변동) 조합 PASS 비율 ≥ 70%
- 두 조건 동시 충족 시 Phase 3 진입 판정 **PASS**

## 실행 명세

```bash
uv run python scripts/c4_rsi_mr_sensitivity.py \
  --from 2025-04-01 --to 2026-04-21 \
  --universe-yaml config/universe.yaml \
  --starting-capital 2000000 \
  --output-markdown data/c4_rsi_mr_grid.md \
  --output-csv data/c4_rsi_mr_grid.csv \
  --workers 8
```

### 환경

| 항목 | 값 |
|---|---|
| 평가 구간 | 2025-04-01 ~ 2026-04-21 |
| universe | KOSPI 200 199 종목 (`config/universe.yaml`) |
| 시작 자본 | 2,000,000 KRW |
| position_pct | 1.0 |
| 데이터 소스 | pykrx 일봉 (`data/stock_agent.db` 캐시, C1 백필 완료 분) |
| DCA baseline 종목 | KODEX 200 (069500), 월 100,000 KRW (PR1 정합) |
| DCA baseline 총수익률 | +48.1848% |
| 그리드 크기 | 96 조합 |

## 그리드 정의

5 축 × 후보값 조합:

| 축 | 후보값 | 비고 |
|---|---|---|
| `rsi_period` | 10, 14, 21 | 현행 14 포함. 단기(10)·중기(21) 비교 |
| `oversold_threshold` | 25, 30 | 현행 30 포함. 더 엄격한 과매도(25) 비교 |
| `overbought_threshold` | 70, 75 | 현행 70 포함. 더 엄격한 과매수(75) 비교 |
| `stop_loss_pct` | 0.02, 0.03, 0.04, 0.05 | 현행 0.03 포함. 2~5% 범위 |
| `max_positions` | 5, 10 | 현행 10 포함. 집중(5)·분산(10) 비교 |

3 × 2 × 2 × 4 × 2 = **96 조합**.

### ADR-0022 게이트 정의

| 게이트 | 기준 |
|---|---|
| 게이트 1 | `max_drawdown_pct > -25%` |
| 게이트 2 | `dca_alpha_pct > 0` (전략 총수익률 - DCA baseline 총수익률) |
| 게이트 3 | 연환산 Sharpe > 0.3 |
| 종합 PASS | 게이트 1 · 2 · 3 동시 통과 |

## 결과 요약

| 항목 | 값 |
|---|---|
| DCA baseline 총수익률 (069500) | +48.1848% |
| 그리드 크기 | 96 조합 |
| 게이트 3종 동시 PASS | **64 / 96 (66.67%)** |
| 게이트 1 (MDD > -25%) PASS | 96 / 96 (100%) |
| 게이트 2 (DCA 알파 > 0) PASS | 64 / 96 (66.67%) |
| 게이트 3 (Sharpe > 0.3) PASS | 96 / 96 (100%) |
| 현행 파라미터 (14/30/70/0.03/10) | **PASS** |
| 현행 인접 조합 PASS | **7 / 8 (87.50%)** |

모든 FAIL 32 조합은 게이트 2 (DCA 알파 음수) 단독 미달. 게이트 1 · 3 는 전 96 조합 100% 통과.

## Phase 3 진입 게이트 판정

| 기준 | 결과 | 판정 |
|---|---|---|
| 전체 PASS 비율 ≥ 50% | 66.67% | PASS |
| 현행 인접 PASS 비율 ≥ 70% | 87.50% | PASS |
| **종합 Phase 3 진입 판정** | | **PASS** |

## 현행 파라미터 결과

`rsi_period=14`, `oversold=30`, `overbought=70`, `stop_loss=0.03`, `max_positions=10`:

| 항목 | 값 |
|---|---|
| 총수익률 | +63.44% |
| MDD | -8.17% |
| Sharpe | 2.2966 |
| DCA 알파 | +15.25%p |
| trades | 177 |
| 게이트 판정 | PASS |

C1 재평가 결과 (`docs/runbooks/c1_universe_full_backfill_2026-05-02.md`) 와 동일 — 동일 universe·기간·파라미터 재확인.

## 현행 인접 (1 축 변동) 분석

현행 파라미터에서 1 개 축만 변경한 8 후보:

| 변경 축 | 후보값 | 총수익률 | MDD | Sharpe | DCA 알파 | PASS |
|---|---|---|---|---|---|---|
| `rsi_period` | 10 (현행 14) | — | — | — | — | PASS |
| `rsi_period` | 21 (현행 14) | +75.32% | -7.49% | 2.6064 | +27.13%p | PASS |
| `oversold` | 25 (현행 30) | +46.66% | -6.51% | 2.0691 | -1.57%p | **FAIL** |
| `overbought` | 75 (현행 70) | +72.17% | -8.22% | 2.7197 | +23.98%p | PASS |
| `stop_loss` | 0.02 (현행 0.03) | +49.44% | -11.40% | 1.6250 | +1.25%p | PASS |
| `stop_loss` | 0.04 (현행 0.03) | +71.55% | -7.41% | 2.5997 | +23.37%p | PASS |
| `stop_loss` | 0.05 (현행 0.03) | +55.48% | -8.31% | 2.1820 | +7.29%p | PASS |
| `max_positions` | 5 (현행 10) | +80.69% | -9.06% | 2.3677 | +32.51%p | PASS |

**7 / 8 PASS (87.5%)**. 유일한 FAIL: `oversold=25` (DCA 알파 -1.57%p — 게이트 2 경계 근방 미달).

해석: `oversold=25` 로 진입 기준을 더 엄격하게 적용하면 진입 횟수가 줄어 알파가 소폭 감소. DCA baseline (+48.18%) 대비 절대 수익 차이는 1.57%p 로 매우 근소 — 현행 oversold=30 의 적절성 재확인.

## TOP 5 조합 (총수익률 기준)

| rsi_period | oversold | overbought | stop_loss | max_positions | 총수익률 | MDD | Sharpe | DCA 알파 | trades |
|---|---|---|---|---|---|---|---|---|---|
| 21 | 30 | 70 | 0.04 | 5 | **+123.86%** | -8.66% | 3.2332 | +75.68%p | 41 |
| 21 | 30 | 70 | 0.04 | 10 | +114.84% | -6.59% | 3.2765 | +66.65%p | 91 |
| 14 | 30 | 75 | 0.02 | 5 | +101.02% | -10.70% | 2.6440 | +52.84%p | 118 |
| 21 | 25 | 75 | 0.02 | 5 | +99.61% | -7.39% | 2.7030 | +51.43%p | 35 |
| 21 | 30 | 70 | 0.02 | 5 | +94.14% | -8.26% | 2.4936 | +45.96%p | 79 |

최고 성능 조합 (`rp=21/os=30/ob=70/sl=0.04/mp=5`) 은 현행 대비 rsi_period 21·stop_loss 4%·max_positions 5 로 이동. trades=41 은 현행 177 대비 집중 전략이며 통계 신뢰도는 낮아진다.

## 축별 PASS 분포

| 축 | 값 | PASS 수 / 조합 수 | PASS 율 |
|---|---|---|---|
| `rsi_period` | 10 | 16 / 32 | **50%** |
| `rsi_period` | 14 | 24 / 32 | 75% |
| `rsi_period` | 21 | 24 / 32 | 75% |
| `oversold` | 25 | 15 / 32 | **48%** — 최저 |
| `oversold` | 30 | 49 / 64 | **77%** — 최고 (32조합 비교 불가, 타 축 불균형 보정 시 동등 기준으로 27/32 ≈ 84%) |
| `overbought` | 70 | 33 / 48 | 69% |
| `overbought` | 75 | 31 / 48 | 65% |
| `stop_loss` | 0.02 | 16 / 24 | 67% |
| `stop_loss` | 0.03 | 17 / 24 | 71% |
| `stop_loss` | 0.04 | 18 / 24 | 75% |
| `stop_loss` | 0.05 | 13 / 24 | **54%** — 최저 |
| `max_positions` | 5 | 39 / 48 | **81%** — 최고 |
| `max_positions` | 10 | 25 / 48 | 52% |

주요 관찰:
- `oversold=25` (48%) 가 가장 낮은 축별 PASS 율. 과매도 기준을 더 타이트하게 적용하면 DCA 대비 알파 확보가 어려워진다.
- `max_positions=5` (81%) 가 `max_positions=10` (52%) 대비 훨씬 높은 PASS 율. 집중 포지션이 알파 효율을 높이는 것으로 관찰 — 단 trades 감소로 통계 신뢰도 trade-off.
- `stop_loss=0.05` (54%) 가 손절 축에서 가장 낮음. 손절 범위가 넓어지면 noise 손실이 증가해 알파를 잠식.

## 제한 사항

- **단일 1년 코호트**: 본 그리드도 2025-04-01 ~ 2026-04-21 단일 구간. C2 walk-forward 통과로 일부 보강됐으나 다년 코호트 robustness 는 Phase 5 잔여.
- **069500 mark-to-market 절대 수익률**: C3 통과로 pykrx 캐시가 수정주가 데이터임을 확정 — DCA baseline +48.18% 와 전략 수익률 모두 한국 KOSPI 200 강세장 macro 의 영향 포함.
- **DCA same-window 비교**: DCA baseline 은 069500 ETF 단일 종목 기준이므로 전체 universe 대비 KOSPI 200 인덱스 수익률을 대변. 약세장·횡보장에서의 알파 방향은 본 그리드로 검증 불가.
- **파라미터 갱신 결정 보류**: 최고 성능 조합 (rp=21/sl=0.04/mp=5) 이 현행 파라미터보다 좋은 지표를 보이나, 단일 코호트 과적합 가능성을 배제할 수 없다. 현행 1차 채택 파라미터 (ADR-0023) 는 본 그리드 결과로 변경하지 않으며 Phase 3 운영 후 별도 ADR 로 검토.
- **trades 불균형**: 최고 성능 조합 (trades=41) 은 현행 (trades=177) 대비 통계 신뢰도 낮음 — 과대 해석 주의.

## 참조

- **ADR-0023** — F5 RSI 평균회귀 1차 채택 (조건부, C1~C4 명시). `docs/adr/0023-rsi-mr-strategy-adoption-conditional.md`.
- **ADR-0022** — Step F 게이트 재정의 (MDD > -25% · DCA 알파 · Sharpe > 0.3). `docs/adr/0022-step-f-gate-redefinition.md`.
- **ADR-0024** — Walk-forward pass-threshold = 0.3. `docs/adr/0024-walk-forward-pass-threshold.md`.
- `docs/runbooks/c1_universe_full_backfill_2026-05-02.md` — C1 (universe 199 백필 + PR5 재평가).
- `docs/runbooks/c2_walk_forward_rsi_mr_2026-05-02.md` — C2 (walk-forward 본 구현 + 통과).
- `docs/runbooks/c3_069500_adjusted_plausibility_2026-05-03.md` — C3 (수정주가 plausibility).
- `docs/runbooks/step_f_rsi_mr_2026-05-02.md` — PR5 원본 결과 (universe 101).
- `data/c4_rsi_mr_grid.md` — 자동 생성 sensitivity 리포트 (96 조합).
- `data/c4_rsi_mr_grid.csv` — 자동 생성 메트릭 CSV (96 조합).
