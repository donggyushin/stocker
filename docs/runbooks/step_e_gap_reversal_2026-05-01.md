# Step E Gap-Reversal 백테스트 — 결과 (FAIL, 2026-05-01)

- 실행 일자: 2026-05-01 (KST)
- 데이터 범위: 2025-04-22 ~ 2026-04-21 (1년치 KIS 분봉 캐시 재사용)
- 시작 자본: 1,000,000 KRW
- 전략: `GapReversalStrategy` (Step E PR3 — `src/stock_agent/strategy/gap_reversal.py`)
- prev_close provider: `DailyBarPrevCloseProvider` (Step E PR4 Stage 2 — `src/stock_agent/backtest/prev_close.py`)
- 서브셋: `config/universe_top50.yaml` · `config/universe_top100.yaml`
- 일봉 캐시 사전 백필: `scripts/backfill_daily_bars.py` 100/100 success, 258 bars/sym
- 실행 시간: Top 50 ≒95s, Top 100 ≒200s

## ADR-0019 세 게이트 판정

| 게이트 | 한도 | Top 50 | Top 100 |
|---|---|---|---|
| MDD > -15% | 낙폭 절대값 15% 미만 | **-10.19%** PASS ✅ | **-19.99%** FAIL |
| 승률 × 손익비 > 1.0 | 기대값 양수 | **0.339** FAIL | **0.289** FAIL |
| 연환산 샤프 > 0 | 위험조정 수익 양수 | **-3.23** FAIL | **-6.27** FAIL |

**결론: Top 50 가 MDD 게이트만 단독 통과. 세 게이트 동시 통과 0. Gap-Reversal 후보 폐기.**

> 참고: `scripts/backtest.py` 의 verdict 라벨은 **MDD-only** 판정이라 Top 50 에 PASS 가 찍힌다. ADR-0019 는 MDD + 승×손익비 + 샤프 세 게이트 동시 통과 요구 — Top 50 도 게이트 2/3 미달로 FAIL.

## 풀 메트릭

### Top 50 (`data/step_e_gap_reversal_top50.csv`)

| 지표 | 값 |
|---|---|
| 총수익률 | -9.71% |
| 최대 낙폭 (MDD) | -10.19% |
| 샤프 비율 (연환산) | -3.2306 |
| 승률 | 42.13% |
| 평균 손익비 | 0.8055 |
| 일평균 거래 수 | 0.811 |
| 순손익 (KRW) | -97,082 |
| trades | 197 |
| rejected (사전) | 716 |
| post-slippage rejected | 0 |

### Top 100 (`data/step_e_gap_reversal_top100.csv`)

| 지표 | 값 |
|---|---|
| 총수익률 | -19.89% |
| 최대 낙폭 (MDD) | -19.99% |
| 샤프 비율 (연환산) | -6.2719 |
| 승률 | 36.49% |
| 평균 손익비 | 0.7928 |
| 일평균 거래 수 | 1.173 |
| 순손익 (KRW) | -198,922 |
| trades | 285 |
| rejected (사전) | 1,416 |
| post-slippage rejected | 0 |

## 해석

- **MDD Top 50 -10.19% 는 Step C·D 어떤 ORB 조합보다도 얕음** — 갭 반전이 일중 변동성을 회피하는 효과는 뚜렷. 그러나 절대 수익률이 음수.
- 승률 36~42% × 손익비 0.79~0.81 = 0.29~0.34 — 기대값 음의 영역. **stop-loss 가 take-profit 을 비대칭으로 더 자주 trigger** 한다는 신호. 갭 반전 가설이 한국 시장에서 일관되게 작동하지 않음.
- Top 50 → Top 100 확장 시 trades 197→285 (+45%) 인데 MDD 는 -10.19%→-19.99% 로 거의 2배 악화. 유동성 하위 종목의 갭 변동성이 손실폭을 키움.
- ORB·VWAP-MR 대비 거래 빈도가 1/4~1/5 수준 — 신호 발생 자체가 드문 전략. 표본 195~285 건은 ADR-0017 의 240 영업일 기준에 근접하나 **거래수** 표본은 작아 메트릭 분산 큼.

## 산출물

- `data/step_e_gap_reversal_top50.md` · `data/step_e_gap_reversal_top50.csv` · `data/step_e_gap_reversal_top50_trades.csv`
- `data/step_e_gap_reversal_top100.md` · `data/step_e_gap_reversal_top100.csv` · `data/step_e_gap_reversal_top100_trades.csv`

## 다음 행동

- Step E Stage 4 (민감도 그리드·walk-forward) 진입 조건 미충족 — Gap-Reversal 도 단독 PASS 부재.
- VWAP-MR 결과 (`docs/runbooks/step_e_vwap_mr_2026-05-01.md`) 와 종합 → Stage 5 폐기 ADR.

## 참조

- ADR-0019 — Phase 2 백테스트 FAIL 복구 로드맵.
- `src/stock_agent/strategy/gap_reversal.py` — `GapReversalStrategy` 구현.
- `src/stock_agent/backtest/prev_close.py` — `DailyBarPrevCloseProvider`.
- `tests/test_strategy_gap_reversal.py` — 34 케이스 단위 테스트.
- `docs/step_e_followup_plan.md` — Step E 5단계 명세.
