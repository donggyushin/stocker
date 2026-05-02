# Step F PR3 — F3 Cross-sectional Momentum 백테스트 결과 (2026-05-02)

> **작성**: 2026-05-02. ADR-0019 Step F PR3 (F3 Cross-sectional 모멘텀) 결과 + ADR-0022 게이트 판정.

## 컨텍스트

ADR-0019 Step F (가설 풀 확장) 의 세 번째 후보 평가. ADR-0022 게이트 (MDD > -25%, DCA baseline 대비 양의 알파, Sharpe > 0.3) 적용.

학술 검증: Jegadeesh-Titman 1993 — 12개월 누적 수익률 ranking 상위 N 종목 보유.

DCA baseline (PR1) 은 평가 구간이 다르므로 본 PR3 평가 구간 (2025-04-01 ~ 2026-04-21) 으로 DCA 를 재실행해 fair 비교 baseline 산출.

## 데이터 의존성 (운영자 검토 필수)

`data/stock_agent.db` 일봉 캐시 현황:
- 101 종목 / 평균 258 영업일 (2025-04-01 ~ 2026-04-21).
- KOSPI 200 universe (`config/universe.yaml`) 199 종목 중 101 종목만 백필 (대략 절반).
- KODEX 200 ETF (069500) 만 458 영업일 (2024-06-03 부터, PR2 Golden Cross 백필분).

**운영 결정 필요**: universe 199 종목 전체 백필 + lookback_months=12 학술 표준 적용은 후속 작업. 본 PR3 1차 평가는 캐시 101 종목 + lookback_months=6 절충 실행.

## 실행 명세

```bash
# 캐시된 종목 추출
sqlite3 data/stock_agent.db \
  "SELECT DISTINCT symbol FROM daily_bars ORDER BY symbol;" > /tmp/cached_symbols.txt

# 모멘텀 백테스트 (101 종목 universe, lookback 6개월, top_n 10)
SYMBOLS=$(cat /tmp/cached_symbols.txt | tr '\n' ',' | sed 's/,$//')
uv run python scripts/backtest.py \
  --loader=daily \
  --from 2025-04-01 --to 2026-04-21 \
  --symbols "$SYMBOLS" \
  --strategy-type momentum \
  --starting-capital 2000000 \
  --lookback-months 6 \
  --top-n 10 \
  --output-markdown data/step_f_momentum.md \
  --output-csv data/step_f_momentum_metrics.csv \
  --output-trades-csv data/step_f_momentum_trades.csv

# 동일 구간 DCA baseline 재실행 (게이트 2 fair 비교)
uv run python scripts/backtest.py \
  --loader=daily \
  --from 2025-04-01 --to 2026-04-21 \
  --symbols 069500 \
  --strategy-type dca \
  --starting-capital 2000000 \
  --monthly-investment 100000 \
  --output-markdown data/step_f_dca_same_window.md \
  --output-csv data/step_f_dca_same_window_metrics.csv \
  --output-trades-csv data/step_f_dca_same_window_trades.csv
```

### 설정

| 항목 | 값 |
|---|---|
| 전략 | MomentumStrategy (cross-sectional, monthly rebalance) |
| 파라미터 | lookback_months=6, top_n=10, rebalance_day=1, position_pct=1.0 |
| universe | 캐시 101 종목 (KOSPI 200 부분집합) |
| 시작 자본 | 2,000,000 KRW |
| 데이터 소스 | pykrx 일봉 (`data/stock_agent.db` 캐시) |
| 평가 구간 | 2025-04-01 ~ 2026-04-21 (258 영업일) |
| 실 평가 시작 | 첫 ~126 영업일 lookback — 첫 리밸런싱 ~2025-10 부근 |

## 결과

### Momentum (PR3)

| 항목 | 값 |
|---|---|
| 기간 | 2025-04-01 ~ 2026-04-21 (258 영업일) |
| 시작 자본 | 2,000,000 KRW |
| 거래 수 | 14 (entry+exit pair × 7 + 잔존 lot 가상청산) |
| 총수익률 (mark-to-market) | **+11.22%** |
| 최대 낙폭 (MDD) | **-7.70%** |
| 샤프 비율 (연환산) | **0.9910** |
| 승률 | 71.43% (5/7) |
| 평균 손익비 | 2.2528 |
| 일평균 거래 수 | 0.054 |
| 순손익 | +224,426 KRW |
| 종료 시점 자본 | 2,224,426 KRW |
| 최저점 자본 (2026-01-02) | 1,983,742 KRW |

### DCA baseline (동일 구간 재실행)

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
| 게이트 1 (MDD) | MDD > -25% | -7.70% | **PASS** |
| 게이트 2 (DCA 대비 알파) | (Momentum 총수익률) - (DCA 총수익률) > 0 | 11.22% - 48.18% = **-36.96%p** | **FAIL** |
| 게이트 3 (Sharpe) | 연환산 Sharpe > 0.3 | 0.9910 | **PASS** |

**종합 판정: FAIL (게이트 2 — DCA baseline 대비 음의 알파)**

## 분석

### Momentum vs DCA — 절대 수익률 압도적 차이

DCA (KODEX 200 ETF 월 적립) 가 +48.18% 로 모멘텀 +11.22% 를 4.3× 압도. KOSPI 200 인덱스 자체가 1년간 +50% 안팎으로 강하게 상승한 시기 (PR2 Golden Cross 와 동일 패턴). 인덱스 강세장에서 cross-sectional 모멘텀의 종목 picking 이 인덱스 베타를 따라잡지 못함.

학술 결과 (Jegadeesh-Titman 1993) 는 미국 주식 1965-1989 기준 월 ~1% 수준 alpha. 한국 단일 시장 + 1년 + 부분 universe 조건에서 알파 음수는 의외 아님 — 표본 부족 + 시장 강세장 노이즈.

### MDD·Sharpe 측면

- MDD -7.70%: DCA -12.53% 보다 우수. 모멘텀의 cash 비중 (lookback 미충족 / qty=0 skip) 이 drawdown 흡수.
- Sharpe 0.9910: 양호하나 DCA 2.1555 의 절반 미만. 위험조정수익률 측면에서도 DCA 우위.

### 데이터 plausibility (PR2 와 동일 caveat)

KOSPI 200 ETF 069500 가격 2025-04 → 2026-04 약 1.45× (32,000 → 46,500 추정). 1년 +45~50% 라는 한국 시장은 historical 평균 (5~10%) 대비 비현실적. **pykrx 일봉의 수정주가 보정 (액면분할·병합·배당) 검증이 PR2 에서 미해결 — PR3 도 동일 caveat 적용**.

### 실행 동작 — backtest layer 와 strategy holdings 불일치

로그에서 `Momentum exit skip: 보유 없음 (sym=...)` debug 메시지 다수 관측. 원인: strategy 가 `holdings = top_n_set` 으로 갱신하지만 backtest 가 entry skip (qty=0 / 잔액 부족) 한 종목은 `active_lots` 미존재. 다음 리밸런싱에서 strategy 가 이 종목을 ExitSignal 로 emit 하면 backtest 가 skip.

→ **에러는 아니나 strategy holdings 와 실제 보유의 drift**. PR3 MVP 로 그대로 두고 후속 PR 에서 strategy 콜백 (entry_failed) 도입 검토.

## 주요 caveat (운영자 검토 필수)

1. **universe 부분집합**: 199 종목 KOSPI 200 중 101 종목만 평가. 결과 편향 가능 — 캐시 안 된 98 종목이 모멘텀 상위에 있을 가능성 있음.
2. **lookback 단축**: 학술 표준 12개월 → 6개월. Jegadeesh-Titman 1993 결과와 직접 비교 어려움.
3. **단일 평가 구간**: 1년 (2025-04-01 ~ 2026-04-21). walk-forward 검증 (Phase 5) 미적용.
4. **시장 강세장 편향**: 평가 기간 한국 KOSPI 200 인덱스 +45~50% 강세 — 모멘텀의 종목 picking 알파가 인덱스 베타를 이기기 어려운 환경. 약세장·횡보장 결과는 별개.
5. **데이터 plausibility (PR2 와 동일)**: pykrx 일봉 수정주가 보정 여부 미검증. 절대 수익률 수치는 데이터 검증 후 재해석 권장.
6. **Strategy-backtest drift**: entry skip 시 strategy holdings 와 실 lot 불일치 — 후속 보강 필요.

## 다음 단계

운영자 결정 항목:

1. **universe 199 종목 전체 백필 + lookback_months=12 재평가** — 학술 표준 충실 시도. `scripts/backfill_daily_bars.py --symbols=$(yaml 전체)` 으로 백필 후 재실행. 평가 가능 구간 1년 미만으로 줄어들지만 표준 충실.
2. **069500 일봉 수정주가 보정 검증** — PR2 와 공통 caveat. 미해결 시 PR1~PR3 절대 수익률 수치 모두 재해석 필요.
3. **모멘텀 결과 인정 + PR4 (F4 저변동성) 진행** — 게이트 2 FAIL 결과를 인정하고 다음 가설 후보로.
4. **PR3 코드 산출물 보존** (`strategy/momentum.py`, `backtest/momentum.py`, `scripts/backtest.py --strategy-type momentum`).

## 참조

- ADR-0019 — Phase 2 백테스트 FAIL 복구 로드맵.
- ADR-0021 — Step E 폐기 + Step F 전환 결정.
- ADR-0022 — Step F 게이트 재정의 (MDD > -25%, DCA 대비 알파, Sharpe > 0.3).
- `docs/step_f_strategy_pool_plan.md` — Step F 가설 풀 plan.
- `docs/runbooks/step_f_dca_baseline_2026-05-02.md` — F1 DCA baseline (구 비교 기준).
- `docs/runbooks/step_f_golden_cross_2026-05-02.md` — F2 Golden Cross (PR2).
- `data/step_f_momentum.md` — 자동 생성 모멘텀 리포트.
- `data/step_f_dca_same_window.md` — 동일 구간 DCA 재실행 리포트 (게이트 2 비교 baseline).
- `data/step_f_momentum_metrics.csv` / `data/step_f_momentum_trades.csv` — 자동 생성 메트릭/체결 CSV.
- Jegadeesh & Titman (1993). "Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency". *The Journal of Finance*, 48(1), 65-91.
