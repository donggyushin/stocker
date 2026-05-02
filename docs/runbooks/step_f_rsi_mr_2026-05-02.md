# Step F PR5 — F5 RSI 평균회귀 백테스트 결과 (2026-05-02)

> **작성**: 2026-05-02. ADR-0019 Step F PR5 (F5 RSI 평균회귀) 결과 + ADR-0022 게이트 판정.

## 컨텍스트

ADR-0019 Step F (가설 풀 확장) 의 다섯 번째 후보 평가. ADR-0022 게이트 (MDD > -25%, DCA baseline 대비 양의 알파, Sharpe > 0.3) 적용.

학술 검증: RSI 평균회귀 자체는 학술적으로 약함 (Wilder 1978 이후 지속 검증되었으나 일관된 alpha 보고는 제한적). 본 PR 은 단순화 — 일봉 RSI(14), oversold (RSI<30) 진입, overbought (RSI>70) 또는 stop_loss(-3%) 청산. simple average gain/loss 방식 (Wilder smoothing 미사용).

DCA baseline 비교 구간 정렬: PR3/PR4 와 동일하게 평가 구간 (2025-04-01 ~ 2026-04-21) 으로 DCA 를 재실행한 결과 (`data/step_f_dca_same_window.md`) 를 fair 비교 baseline 으로 인용.

## 데이터 의존성 (운영자 검토 필수)

`data/stock_agent.db` 일봉 캐시 현황 (PR3·PR4 와 동일):
- 101 종목 / 평균 258 영업일 (2025-04-01 ~ 2026-04-21).
- KOSPI 200 universe (`config/universe.yaml`) 199 종목 중 101 종목만 백필.

**운영 결정 필요**: universe 199 종목 전체 백필은 후속 작업. 본 PR5 1차 평가는 캐시 101 종목 + rsi_period=14 (학술 표준) + oversold/overbought=30/70 (학술 표준) + stop_loss_pct=0.03 + max_positions=10 실행.

## 실행 명세

```bash
# 캐시된 종목 추출
sqlite3 data/stock_agent.db \
  "SELECT DISTINCT symbol FROM daily_bars ORDER BY symbol;" \
  | tr '\n' ',' | sed 's/,$//' > /tmp/cached_symbols.txt

# RSI 평균회귀 백테스트 (101 종목 universe, RSI(14), oversold 30, overbought 70, stop -3%, max 10 포지션)
SYMBOLS=$(cat /tmp/cached_symbols.txt)
uv run python scripts/backtest.py \
  --loader=daily \
  --from 2025-04-01 --to 2026-04-21 \
  --symbols "$SYMBOLS" \
  --strategy-type rsi-mr \
  --starting-capital 2000000 \
  --rsi-period 14 \
  --oversold-threshold 30 \
  --overbought-threshold 70 \
  --stop-loss-pct 0.03 \
  --max-positions 10 \
  --output-markdown data/step_f_rsi_mr.md \
  --output-csv data/step_f_rsi_mr_metrics.csv \
  --output-trades-csv data/step_f_rsi_mr_trades.csv

# 동일 구간 DCA baseline — PR3 momentum 런북에서 이미 산출 (재실행 불필요)
# 결과: data/step_f_dca_same_window.md, total_return +48.18%
```

### 설정

| 항목 | 값 |
|---|---|
| 전략 | RSIMRStrategy (multi-symbol, per-bar 시그널) |
| 파라미터 | rsi_period=14, oversold=30, overbought=70, stop_loss_pct=0.03, max_positions=10, position_pct=1.0 |
| RSI 계산 | simple average gain/loss (Wilder smoothing 미사용) |
| 동일 세션 재진입 | 차단 (청산 후 같은 date 내 재진입 X — RSI 회복 즉시 무한 루프 방지) |
| universe | 캐시 101 종목 (KOSPI 200 부분집합) |
| 시작 자본 | 2,000,000 KRW |
| 데이터 소스 | pykrx 일봉 (`data/stock_agent.db` 캐시) |
| 평가 구간 | 2025-04-01 ~ 2026-04-21 (258 영업일) |

## 결과

### RSI 평균회귀 (PR5)

| 항목 | 값 |
|---|---|
| 기간 | 2025-04-01 ~ 2026-04-21 (258 영업일) |
| 시작 자본 | 2,000,000 KRW |
| 거래 수 | 175 (entry+exit pair 기준) |
| 총수익률 | **+56.31%** |
| 최대 낙폭 (MDD) | **-6.40%** |
| 샤프 비율 (연환산) | **2.4723** |
| 승률 | 34.29% (60/175) |
| 평균 손익비 | 4.3799 |
| 일평균 거래 수 | 0.678 |
| 순손익 | +1,126,256 KRW |
| 종료 시점 자본 | 3,126,256 KRW |
| 최저점 자본 (2025-05-23) | 1,905,386 KRW |
| 최고점 자본 | 3,147,706 KRW |

### 청산 사유 분포

| 사유 | 카운트 | 비율 |
|---|---|---|
| `stop_loss` | 113 | 64.6% |
| `take_profit` | 58 | 33.1% |
| `force_close` (잔존 lot 가상 청산) | 4 | 2.3% |

승률 34.29% 는 낮으나 평균 손익비 4.38 (winners avg / |losers avg|) 가 높아 expectancy 양수. 학술 평균회귀 패턴: 손절 다수 + 가끔의 큰 익절.

### DCA baseline (동일 구간, PR3 인용)

| 항목 | 값 |
|---|---|
| 기간 | 2025-04-01 ~ 2026-04-21 (258 영업일) |
| 시작 자본 | 2,000,000 KRW |
| 매수 횟수 (lots) | 13 (월 적립) |
| 총수익률 (mark-to-market) | **+48.18%** |
| 최대 낙폭 (MDD) | -12.53% |
| 샤프 비율 (연환산) | 2.1555 |
| 순손익 | +963,696 KRW |
| 종료 시점 자본 | 2,963,696 KRW |

## ADR-0022 게이트 판정

| 게이트 | 기준 | 결과 | 판정 |
|---|---|---|---|
| 게이트 1 (MDD) | MDD > -25% | -6.40% | **PASS** |
| 게이트 2 (DCA 대비 알파) | (RSI MR 총수익률) - (DCA 총수익률) > 0 | 56.31% - 48.18% = **+8.13%p** | **PASS** |
| 게이트 3 (Sharpe) | 연환산 Sharpe > 0.3 | 2.4723 | **PASS** |

**종합 판정: PASS (게이트 3종 전원 통과)**

## 분석

### 게이트 2 — DCA 대비 양의 알파 확보

Step F 5개 후보 중 **DCA 대비 양의 알파를 확보한 두 번째 사례** (PR2 Golden Cross +130.86%p 이후). 단 PR2 는 trades=1 통계 신뢰도 약함 caveat 적용. PR5 는 trades=175 — Step F 전체에서 통계적으로 가장 신뢰도 높은 알파 확인.

PR3 momentum (-36.96%p) · PR4 low-vol (-32.31%p) 는 인덱스 베타에 패배. PR5 는 평균회귀 본질이 단기 noise 회복 — 강세장 횡보 구간에서도 작동 (인덱스 베타와 직교).

### MDD·Sharpe — 위험조정 측면 우수

- MDD -6.40%: PR3 momentum (-7.70%) · PR4 low-vol (-9.62%) · DCA (-12.53%) 보다 모두 얕음. stop_loss -3% 가 빠른 손절을 강제 → drawdown 제한.
- Sharpe 2.4723: PR3 (0.99) · PR4 (1.17) · DCA (2.16) 보다 우수. PR2 Golden Cross (2.28) 와 비슷.
- 승률 34.29% + 평균 손익비 4.38: 평균회귀 전략 전형 — 작은 손실 다수 + 큰 익절 소수. expectancy = win_rate × avg_win - loss_rate × avg_loss = 양수 (연환산 +56.31% 자체가 expectancy 양수의 누적 결과).

### stop_loss 효과 — drawdown 제한의 핵심

청산 사유 분포에서 stop_loss 가 64.6% 차지. -3% 손절 한도가 손실 lot 의 연쇄를 끊어 MDD -6.40% 달성. 만약 stop_loss 제거하면 평균회귀 실패 (재상승 미발생) lot 이 MDD 를 키울 가능성 — 후속 sensitivity 분석 가치 있음.

### 데이터 plausibility (PR2~PR4 와 동일 caveat)

KOSPI 200 인덱스 1년 +45~50% 가정은 한국 시장 historical 평균 (5~10%) 대비 비현실적. **pykrx 일봉의 수정주가 보정 (액면분할·병합·배당) 검증이 PR2~PR4 에서 미해결 — PR5 도 동일 caveat 적용**.

다만 PR5 는 cross-sectional 평균회귀라 인덱스 절대 수익률에 덜 민감 — 종목간 상대 변동성에서 알파 추출. 인덱스 가격 조정 오류가 상쇄될 가능성 있음 (모든 종목이 동일 비율로 어긋나면 RSI 시그널은 영향 적음).

### 제한 사항 — sample 1 코호트

평가 구간 1년 (258 영업일) 단일 코호트. walk-forward 검증 (Phase 5) 미적용. 강세장 일부 + 약세장 일부 혼재라 평균회귀 환경에는 우호적이나, 다년 강세장 또는 다년 약세장에서의 robustness 미검증.

## 주요 caveat (운영자 검토 필수)

1. **universe 부분집합**: 199 종목 KOSPI 200 중 101 종목만 평가. 결과 편향 가능 — 캐시 안 된 98 종목이 평균회귀 시그널에 더 자주 등장할 가능성 있음.
2. **단일 평가 구간**: 1년 (2025-04-01 ~ 2026-04-21). walk-forward 검증 미적용. 다년 코호트 검증 필요.
3. **RSI 계산 방식**: simple average gain/loss 사용 (Wilder smoothing 미사용). 표준 Wilder 와 결과 다소 차이. 후속 비교 가치 있음.
4. **데이터 plausibility (PR2~PR4 와 동일)**: pykrx 일봉 수정주가 보정 여부 미검증. 단 cross-sectional 전략이라 영향 상대적으로 작을 가능성.
5. **승률 34.29%**: 낮은 승률 → 운영 중 심리적 부담 큼 (10 trade 중 6~7 손절). retail 운영자가 strategy 이탈 위험.
6. **stop_loss_pct 민감도**: -3% 손절이 본 결과의 핵심. -1.5% / -5% / -10% 등 sensitivity 미검증. Phase 5 grid 후보.
7. **동일 세션 재진입 차단 룰**: 일봉 strategy 이지만 분봉 백테스트에서도 동작하도록 추가한 안전장치. 일봉 평가에서는 자연스레 무영향 (날짜 경계 매번 변경).
8. **평균회귀 환경 의존**: 강세장 횡보 구간에서 강함. 강한 단방향 추세 (강세 또는 약세) 에서 약함. 시장 regime 검증 필요.

## 다음 단계

운영자 결정 항목:

1. **PR5 결과 인정 + PR6 (종합 판정 + ADR-0023) 진행** — Step F 가설 풀 5개 평가 완료 + 시나리오 A (PR5 채택) 또는 시나리오 A' (PR2 + PR5 후보 비교) ADR 작성.
2. **PR5 추가 검증 (선택)**:
   - universe 199 종목 전체 백필 + 재평가 — 부분집합 편향 해소.
   - rsi_period / oversold / overbought / stop_loss_pct sensitivity grid — 파라미터 민감도 확인.
   - Wilder smoothing RSI 변형 비교 — 학술 표준과의 차이 측정.
   - walk-forward (Phase 5 본 구현) — 다년 코호트 검증.
3. **PR5 코드 산출물 보존** (`strategy/rsi_mr.py`, `backtest/rsi_mr.py`, `scripts/backtest.py --strategy-type rsi-mr`).

## 참조

- ADR-0019 — Phase 2 백테스트 FAIL 복구 로드맵.
- ADR-0021 — Step E 폐기 + Step F 전환 결정.
- ADR-0022 — Step F 게이트 재정의 (MDD > -25%, DCA 대비 알파, Sharpe > 0.3).
- `docs/step_f_strategy_pool_plan.md` — Step F 가설 풀 plan.
- `docs/runbooks/step_f_dca_baseline_2026-05-02.md` — F1 DCA baseline (구 비교 기준 2025-04-22 ~ 2026-04-21).
- `docs/runbooks/step_f_golden_cross_2026-05-02.md` — F2 Golden Cross (PR2, PASS, 단 단일 trade caveat).
- `docs/runbooks/step_f_momentum_2026-05-02.md` — F3 Cross-sectional Momentum (PR3, FAIL).
- `docs/runbooks/step_f_low_volatility_2026-05-02.md` — F4 Low Volatility (PR4, FAIL).
- `data/step_f_rsi_mr.md` — 자동 생성 RSI 평균회귀 리포트.
- `data/step_f_dca_same_window.md` — 동일 구간 DCA 재실행 리포트 (게이트 2 비교 baseline, PR3 인용).
- `data/step_f_rsi_mr_metrics.csv` / `data/step_f_rsi_mr_trades.csv` — 자동 생성 메트릭/체결 CSV.
- Wilder, J. W. (1978). *New Concepts in Technical Trading Systems*. Trend Research. (RSI 원전)
