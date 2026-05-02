# Step F PR4 — F4 Low Volatility 백테스트 결과 (2026-05-02)

> **작성**: 2026-05-02. ADR-0019 Step F PR4 (F4 저변동성 anomaly) 결과 + ADR-0022 게이트 판정.

## 컨텍스트

ADR-0019 Step F (가설 풀 확장) 의 네 번째 후보 평가. ADR-0022 게이트 (MDD > -25%, DCA baseline 대비 양의 알파, Sharpe > 0.3) 적용.

학술 검증: Frazzini-Pedersen 2014 "Betting Against Beta" — 저베타·저변동성 종목이 위험조정수익률 우월. 본 PR 은 단순화 — 직전 60 영업일 일별 수익률 표준편차 하위 N 종목 보유, 분기 리밸런싱.

DCA baseline 비교 구간 정렬: PR3 momentum 와 동일하게 평가 구간 (2025-04-01 ~ 2026-04-21) 으로 DCA 를 재실행한 결과 (`data/step_f_dca_same_window.md`) 를 fair 비교 baseline 으로 인용.

## 데이터 의존성 (운영자 검토 필수)

`data/stock_agent.db` 일봉 캐시 현황 (PR3 와 동일):
- 101 종목 / 평균 258 영업일 (2025-04-01 ~ 2026-04-21).
- KOSPI 200 universe (`config/universe.yaml`) 199 종목 중 101 종목만 백필.

**운영 결정 필요**: universe 199 종목 전체 백필은 후속 작업. 본 PR4 1차 평가는 캐시 101 종목 + lookback_days=60 (학술 표준) + top_n=10 (작은 universe 절충) 실행.

## 실행 명세

```bash
# 캐시된 종목 추출
sqlite3 data/stock_agent.db \
  "SELECT DISTINCT symbol FROM daily_bars ORDER BY symbol;" \
  | tr '\n' ',' | sed 's/,$//' > /tmp/cached_symbols.txt

# 저변동성 백테스트 (101 종목 universe, lookback 60일, top_n 10, 분기 리밸런싱)
SYMBOLS=$(cat /tmp/cached_symbols.txt)
uv run python scripts/backtest.py \
  --loader=daily \
  --from 2025-04-01 --to 2026-04-21 \
  --symbols "$SYMBOLS" \
  --strategy-type low-vol \
  --starting-capital 2000000 \
  --lookback-days 60 \
  --top-n 10 \
  --rebalance-month-interval 3 \
  --output-markdown data/step_f_low_volatility.md \
  --output-csv data/step_f_low_volatility_metrics.csv \
  --output-trades-csv data/step_f_low_volatility_trades.csv

# 동일 구간 DCA baseline 재실행 — PR3 momentum 런북에서 이미 산출 (재실행 불필요)
# 결과: data/step_f_dca_same_window.md, total_return +48.18%
```

### 설정

| 항목 | 값 |
|---|---|
| 전략 | LowVolStrategy (cross-sectional, quarterly rebalance) |
| 파라미터 | lookback_days=60, top_n=10, rebalance_month_interval=3, position_pct=1.0 |
| universe | 캐시 101 종목 (KOSPI 200 부분집합) |
| 시작 자본 | 2,000,000 KRW |
| 데이터 소스 | pykrx 일봉 (`data/stock_agent.db` 캐시) |
| 평가 구간 | 2025-04-01 ~ 2026-04-21 (258 영업일) |
| 첫 리밸런싱 | lookback 60일 충족 후 첫 분기 첫 영업일 (~2025-07 부근) |

## 결과

### Low Volatility (PR4)

| 항목 | 값 |
|---|---|
| 기간 | 2025-04-01 ~ 2026-04-21 (258 영업일) |
| 시작 자본 | 2,000,000 KRW |
| 거래 수 | 19 (entry+exit pair × 7 + 잔존 lot 가상청산 5) |
| 총수익률 (mark-to-market) | **+15.87%** |
| 최대 낙폭 (MDD) | **-9.62%** |
| 샤프 비율 (연환산) | **1.1713** |
| 승률 | 78.95% (15/19) |
| 평균 손익비 | 3.1797 |
| 일평균 거래 수 | 0.074 |
| 순손익 | +317,481 KRW |
| 종료 시점 자본 | 2,317,481 KRW |
| 최저점 자본 (2025-04-01) | 2,000,000 KRW |
| 최고점 자본 | 2,468,251 KRW |

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
| 게이트 1 (MDD) | MDD > -25% | -9.62% | **PASS** |
| 게이트 2 (DCA 대비 알파) | (LowVol 총수익률) - (DCA 총수익률) > 0 | 15.87% - 48.18% = **-32.31%p** | **FAIL** |
| 게이트 3 (Sharpe) | 연환산 Sharpe > 0.3 | 1.1713 | **PASS** |

**종합 판정: FAIL (게이트 2 — DCA baseline 대비 음의 알파)**

## 분석

### LowVol vs DCA — 절대 수익률 차이

DCA (KODEX 200 ETF 월 적립) +48.18% 가 저변동성 +15.87% 를 3.0× 압도. PR3 momentum (+11.22%) · PR4 low-vol (+15.87%) 모두 인덱스 베타 (DCA) 에 패배 — KOSPI 200 인덱스 1년 강세장 (~+45~50%) 환경에서 cross-sectional anomaly 가 인덱스 베타 압도 어려움.

저변동성 anomaly (Frazzini-Pedersen 2014) 의 근본은 **고베타 종목의 leverage-aversion premium 회수** — 강세장에서는 고베타가 더 빠르게 상승해 저변동성 전략이 underperform 정상 작동. 약세장·횡보장에서 위험조정수익률 우월성이 드러나는 패턴.

### MDD·Sharpe — 위험조정 측면

- MDD -9.62%: PR3 momentum -7.70% 보다 약간 깊으나 DCA -12.53% 보다 우수.
- Sharpe 1.1713: PR3 momentum 0.9910 보다 우수 (분산 효과). DCA 2.1555 의 절반 — 강세장 베타 압도 못함.
- 승률 78.95% + 평균 손익비 3.18: 단일 trade 기준 손익 분포는 견고.

### 데이터 plausibility (PR2~PR3 와 동일 caveat)

KOSPI 200 인덱스 1년 +45~50% 가정은 한국 시장 historical 평균 (5~10%) 대비 비현실적. **pykrx 일봉의 수정주가 보정 (액면분할·병합·배당) 검증이 PR2~PR3 에서 미해결 — PR4 도 동일 caveat 적용**.

### 실행 동작 — backtest layer 와 strategy holdings 불일치 (PR3 와 동일)

로그에서 `LowVol exit skip: 보유 없음 (sym=...)` debug 메시지 다수 관측. 원인: strategy 가 `holdings = top_n_set` 으로 갱신하지만 backtest 가 entry skip (qty=0 / 잔액 부족) 한 종목은 `active_lots` 미존재. 다음 리밸런싱에서 strategy 가 이 종목을 ExitSignal 로 emit 하면 backtest 가 skip.

→ **에러는 아니나 strategy holdings 와 실제 보유의 drift**. PR3 와 동일하게 PR4 MVP 로 그대로 두고 후속 PR 에서 strategy 콜백 (entry_failed) 도입 검토.

## 주요 caveat (운영자 검토 필수)

1. **universe 부분집합**: 199 종목 KOSPI 200 중 101 종목만 평가. 결과 편향 가능 — 캐시 안 된 98 종목이 저변동성 상위에 있을 가능성 있음.
2. **단일 평가 구간**: 1년 (2025-04-01 ~ 2026-04-21). walk-forward 검증 (Phase 5) 미적용. 강세장 단일 코호트.
3. **시장 강세장 편향**: 평가 기간 한국 KOSPI 200 인덱스 +45~50% 강세 — 저변동성 anomaly 의 본질은 약세장·횡보장 우월. 본 평가는 환경 mismatch.
4. **데이터 plausibility (PR2~PR3 와 동일)**: pykrx 일봉 수정주가 보정 여부 미검증.
5. **Strategy-backtest drift**: entry skip 시 strategy holdings 와 실 lot 불일치 — PR3 와 공통, 후속 보강 필요.
6. **lookback_days=60 표본 부족**: 학술 문헌은 보통 252일 (1년) 변동성 — 60일은 단기 노이즈 흡수 미흡.

## 다음 단계

운영자 결정 항목:

1. **universe 199 종목 전체 백필 + lookback_days=252 재평가** — 학술 표준 충실 시도.
2. **069500 일봉 수정주가 보정 검증** — PR2~PR3 와 공통 caveat. 미해결 시 PR1~PR4 절대 수익률 수치 모두 재해석 필요.
3. **저변동성 결과 인정 + PR5 (F5 RSI 평균회귀) 진행** — 게이트 2 FAIL 결과를 인정하고 다음 가설 후보로.
4. **PR4 코드 산출물 보존** (`strategy/low_volatility.py`, `backtest/low_volatility.py`, `scripts/backtest.py --strategy-type low-vol`).

## 참조

- ADR-0019 — Phase 2 백테스트 FAIL 복구 로드맵.
- ADR-0021 — Step E 폐기 + Step F 전환 결정.
- ADR-0022 — Step F 게이트 재정의 (MDD > -25%, DCA 대비 알파, Sharpe > 0.3).
- `docs/step_f_strategy_pool_plan.md` — Step F 가설 풀 plan.
- `docs/runbooks/step_f_dca_baseline_2026-05-02.md` — F1 DCA baseline (구 비교 기준).
- `docs/runbooks/step_f_golden_cross_2026-05-02.md` — F2 Golden Cross (PR2).
- `docs/runbooks/step_f_momentum_2026-05-02.md` — F3 Cross-sectional Momentum (PR3).
- `data/step_f_low_volatility.md` — 자동 생성 저변동성 리포트.
- `data/step_f_dca_same_window.md` — 동일 구간 DCA 재실행 리포트 (게이트 2 비교 baseline, PR3 인용).
- `data/step_f_low_volatility_metrics.csv` / `data/step_f_low_volatility_trades.csv` — 자동 생성 메트릭/체결 CSV.
- Frazzini, A. & Pedersen, L. H. (2014). "Betting Against Beta". *Journal of Financial Economics*, 111(1), 1-25.
