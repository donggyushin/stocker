# Step E VWAP Mean-Reversion 백테스트 — 결과 (FAIL, 2026-05-01)

- 실행 일자: 2026-05-01 (KST)
- 데이터 범위: 2025-04-22 ~ 2026-04-21 (1년치 KIS 분봉 캐시 재사용)
- 시작 자본: 1,000,000 KRW
- 전략: `VWAPMRStrategy` (Step E PR2 — `src/stock_agent/strategy/vwap_mr.py`)
- 서브셋: `config/universe_top50.yaml` · `config/universe_top100.yaml`
- 일봉 캐시 사전 백필: `scripts/backfill_daily_bars.py` 100/100 success, 258 bars/sym
- 실행 시간: Top 50 ≒100s, Top 100 ≒170s

## ADR-0019 세 게이트 판정

| 게이트 | 한도 | Top 50 | Top 100 |
|---|---|---|---|
| MDD > -15% | 낙폭 절대값 15% 미만 | **-49.09%** FAIL | **-50.11%** FAIL |
| 승률 × 손익비 > 1.0 | 기대값 양수 | **0.0458** FAIL | **0.0451** FAIL |
| 연환산 샤프 > 0 | 위험조정 수익 양수 | **-11.02** FAIL | **-10.35** FAIL |

**결론: 두 서브셋 모두 세 게이트 동시 통과 0. VWAP-MR 후보 폐기.**

## 풀 메트릭

### Top 50 (`data/step_e_vwap_mr_top50.csv`)

| 지표 | 값 |
|---|---|
| 총수익률 | -49.22% |
| 최대 낙폭 (MDD) | -49.09% |
| 샤프 비율 (연환산) | -11.0189 |
| 승률 | 65.64% |
| 평균 손익비 | 0.0697 |
| 일평균 거래 수 | 4.156 |
| 순손익 (KRW) | -492,248 |
| trades | 1,010 |
| rejected (사전) | 6,107 |
| post-slippage rejected | 0 |

### Top 100 (`data/step_e_vwap_mr_top100.csv`)

| 지표 | 값 |
|---|---|
| 총수익률 | -50.10% |
| 최대 낙폭 (MDD) | -50.11% |
| 샤프 비율 (연환산) | -10.3473 |
| 승률 | 66.12% |
| 평균 손익비 | 0.0683 |
| 일평균 거래 수 | 4.263 |
| 순손익 (KRW) | -500,953 |
| trades | 1,036 |
| rejected (사전) | 12,590 |
| post-slippage rejected | 0 |

## 해석

- **승률은 60% 대 이지만 손익비 0.07** — 작은 이익 다수 + 큰 손실 소수 패턴. VWAP 회귀 가설은 잦은 미세 이익을 제공하지만 추세 반전 구간에서 stop-loss 1.5% 가 손실폭을 상쇄하지 못한다.
- 일평균 거래 4.2회 — `daily_max_entries` 한도 내에서 거의 매 영업일 진입. 노출 빈도가 높아 누적 손실이 가중.
- Top 100 으로 유니버스 확장 시 거래 수 +26 (1010→1036), 손실은 더 커짐 — 유동성 하위 종목의 슬리피지·갭 노출이 추가 부담.
- ORB Step C/D 와 동일한 한국 시장 특성 (개별 종목 일중 추세 부재) 이 mean-reversion 가설에도 동등하게 부정적.

## 산출물

- `data/step_e_vwap_mr_top50.md` · `data/step_e_vwap_mr_top50.csv` · `data/step_e_vwap_mr_top50_trades.csv`
- `data/step_e_vwap_mr_top100.md` · `data/step_e_vwap_mr_top100.csv` · `data/step_e_vwap_mr_top100_trades.csv`

## 다음 행동

- Step E Stage 4 (민감도 그리드·walk-forward) 진입 조건 미충족 — VWAP-MR 단독 PASS 부재.
- Gap-Reversal 결과 (`docs/runbooks/step_e_gap_reversal_2026-05-01.md`) 와 종합 → Stage 5 폐기 ADR.

## 참조

- ADR-0019 — Phase 2 백테스트 FAIL 복구 로드맵.
- `src/stock_agent/strategy/vwap_mr.py` — `VWAPMRStrategy` 구현.
- `tests/test_strategy_vwap_mr.py` — 35 케이스 단위 테스트.
- `docs/step_e_followup_plan.md` — Step E 5단계 명세.
