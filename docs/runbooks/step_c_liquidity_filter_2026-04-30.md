# Step C 유동성 서브셋 백테스트 — 결과 (FAIL, 2026-04-30)

- 실행 일자: 2026-04-30 (KST)
- 데이터 범위: 2025-04-22 ~ 2026-04-21 (1년치 KIS 분봉 캐시 재사용 — 신규 KIS 호출 0)
- 시작 자본: 1,000,000 KRW
- 서브셋 정의:
  - **Top 50** — `config/universe_top50.yaml` (avg_value_krw 12개월 윈도 2024-04-22 ~ 2025-04-21 랭킹 상위 50)
  - **Top 100** — `config/universe_top100.yaml` (동일 윈도 상위 100)
- 컷오프 방식: pykrx 1.2.7 일봉 거래대금 평균(avg_value_krw) 내림차순. KOSPI 200 기존 199 종목 (`config/universe.yaml`) 부분집합.

## ADR-0019 세 게이트 판정

| 게이트 | 한도 | Top 50 | Top 100 |
|---|---|---|---|
| MDD > -15% | 낙폭 절대값 15% 미만 | **-44.70%** FAIL | **-50.13%** FAIL |
| 승률 × 손익비 > 1.0 | 기대값 양수 | **0.377** FAIL | **0.383** FAIL |
| 연환산 샤프 > 0 | 위험조정 수익 양수 | **-6.68** FAIL | **-7.74** FAIL |

**결론: 두 서브셋 모두 세 게이트 동시 통과 0. Step C FAIL.**

## 메트릭 비교

| 항목 | Top 50 | Top 100 | Baseline 199 (Step A 베이스라인) |
|---|---|---|---|
| 종목 수 | 50 | 100 | 199 |
| 거래 수 | 883 | 1,004 | (Step A 평균 ≈1,000) |
| 총수익률 | -44.97% | -50.01% | -50.05% |
| 최대 낙폭 (MDD) | -44.70% | -50.13% | -51.36% |
| 샤프 (연환산) | -6.68 | -7.74 | -6.81 |
| 승률 | 32.84% | 30.68% | 31.35% |
| 평균 손익비 | 1.147 | 1.248 | 1.28 |
| 일평균 거래 수 | 3.63 | 4.13 | — |
| 순손익 (KRW) | -449,748 | -500,056 | -500,460 |

베이스라인은 `docs/runbooks/step_a_result_2026-04-25.md` 28 조합 평균 + `step_a_result_2026-04-25.md` 표 인용.

## 관찰

- **Top 50 가 가장 덜 손실** — MDD -44.70% (vs Top 100 -50.13%, 199 종목 -51.36%). 유동성 필터링 효과가 미미하지만 존재.
- **모든 서브셋 게이트 통과 0** — 손실 폭만 줄어들 뿐 게이트 한도(MDD -15%) 와 격차 3 배 이상.
- **승률 × 손익비** 는 서브셋 크기와 무관하게 0.38 ± 0.01 — 전략 자체의 기대값 음수 구조 시사.
- **거부 카운트** 패턴: `max_positions_reached` 가 절대 다수(Top 50 2,281 / Top 100 6,096), `below_min_notional` (Top 50 1,991 / Top 100 3,236). Top 100 에서 동시 진입 경합이 거래 수 증가로 이어지지 않고 거부만 늘어남.

## 결론

Step A (민감도 그리드) · Step B (비용 가정 재검정) · Step C (유동성 서브셋) 모두 FAIL. 비용 가정 정상(Step B), 그리드 32 조합 모두 게이트 통과 0(Step A), 유동성 상위 50/100 도 동일(Step C).

→ **Step D — 전략 파라미터 구조 변경** 으로 진행. ADR-0020 작성 안 함 (채택 결정 부재).

## 후속 — Step D

ADR-0019 복구 로드맵 D 단계 진입. 후보 변경 요소:

- OR 윈도 확장(09:00~09:30 → 09:00~10:00) 또는 단축
- `force_close_at` 변경 (15:00 → 14:00 또는 13:00)
- 일 N 진입 캡 변경 (현행 무제한 → 1~3)
- 재진입 정책 (현행 당일 청산 후 금지 → 횟수 제한 허용)

별도 issue 등록(예: `Step D — 전략 파라미터 구조 변경`) 후 진행. 본 runbook 의 베이스라인은 Top 50 (-44.70% MDD) 이 최선이므로 Step D 시작점으로 채택 검토.

## 운영 결과 산출물

- `data/liquidity_ranking.csv` (gitignore, 199 종목 × 12개월 윈도)
- `data/backtest_top50.md` · `data/backtest_top50_metrics.csv` · `data/backtest_top50_trades.csv`
- `data/backtest_top100.md` · `data/backtest_top100_metrics.csv` · `data/backtest_top100_trades.csv`
- `config/universe_top50.yaml` · `config/universe_top100.yaml` (git 추적, PR 781ec54)

## 관련

- ADR-0019 (Phase 2 복구 로드맵)
- ADR-0004 (KOSPI 200 YAML 수동 관리)
- Issue #76 (Step C 인프라)
- Issue #90 (Step C 운영자 실행)
- Step A 결과: `docs/runbooks/step_a_result_2026-04-25.md` (FAIL)
- Step B 결과: `docs/runbooks/step_b_spread_analysis.md` (ADR-0006 슬리피지 0.1% 유지)
