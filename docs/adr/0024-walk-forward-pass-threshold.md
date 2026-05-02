---
date: 2026-05-02
status: 승인됨
deciders: donggyu
related: [0017-phase2-pass-1year.md, 0019-phase2-backtest-fail-remediation.md, 0022-step-f-gate-redefinition.md, 0023-rsi-mr-strategy-adoption-conditional.md]
---

# ADR-0024: Walk-forward pass-threshold 결정 (degradation_pct ≤ 0.3) — ADR-0023 C2 통과 기록

## 상태

승인됨 — 2026-05-02. ADR-0023 의 Phase 3 진입 조건 4종 중 **C2 (walk-forward 본 구현 + 다년 코호트 검증)** 의 결정 ADR. Issue #67 의 walk-forward skeleton 본 구현 + RSI 평균회귀 채택 후보 (`RSIMRStrategy`, ADR-0023) 의 robustness 검증 기준 확정.

## 맥락

ADR-0023 (2026-05-02) 가 F5 RSI 평균회귀를 Step F 1차 채택 후보로 선정하며 Phase 3 진입 조건으로 4 추가 검증 (C1~C4) 명시. C1 (universe 199 전체 백필 + 재평가) 은 같은 날 PASS (`docs/runbooks/c1_universe_full_backfill_2026-05-02.md`). 본 ADR 은 **C2 walk-forward 검증** 의 결정 기록.

ADR-0023 C2 명세:
- `src/stock_agent/backtest/walk_forward.py` 본 구현 (현재 PR #70 스켈레톤만).
- 다년 일봉 캐시 (이미 2024-04-01 부터 백필됨) 로 2~4 분할 walk-forward.
- 각 분할에서 ADR-0022 게이트 3종 동시 통과.
- 단일 1년 코호트 결과만으로 robustness 보장 불가 → walk-forward 로 train→test 안정성 확인 필수.

본 ADR 직전까지 결정되지 않은 항목:
1. **walk-forward pass-threshold (degradation_pct 허용 임계치)** — Issue #67 노트 = `degradation_pct <= 0.3` 제안 (train→test 수익률 악화 30% 이하 PASS).
2. **walk-forward window 분할 정책** — train/test/step 월 수 + 총 분할 수.
3. **per-window 게이트 검증 vs aggregate degradation 비교 방식** — ADR-0022 게이트 3종을 각 test window 마다 독립 적용할지, aggregate metric 만 비교할지.
4. **PASS 선언 기준** — 모든 window 게이트 PASS + aggregate degradation PASS 동시 충족 강제 여부.

검토한 대안:

- **degradation_pct ≤ 0.5 (관대)**: train→test 50% 악화 허용. 더 많은 전략·파라미터 조합이 통과하나 retail 자본 노출 후 실제 운영 악화 시 손실 위험 큼. → 거부.
- **degradation_pct ≤ 0.1 (엄격)**: train→test 10% 악화 한도. 이론상 이상적이나 retail KOSPI 200 1년 단일 백테스트에서 노이즈가 본질적으로 10%p 변동을 만들어 통과 확률 매우 낮음 — 실용 PASS 후보 차단 위험. → 거부.
- **degradation_pct ≤ 0.3 (중도)**: Issue #67 제안값. 30% 악화 한도 = "test 수익률이 train 의 70% 이상 보존" 으로 해석. retail 자동매매 운영 안전 마진 + 실용 PASS 가능성 균형. → **채택**.
- **PASS 기준 단일 (per-window 게이트 통과 만)**: aggregate 미적용. window 통과 여부만 본다. degradation 누적 평가 손실. → 거부.
- **PASS 기준 단일 (aggregate degradation 만)**: per-window 게이트 미적용. 한 window 가 게이트 1·2·3 중 하나 FAIL 이어도 aggregate 만 통과하면 채택 = 운영 위험. → 거부.
- **이중 PASS (per-window 게이트 + aggregate degradation 동시 충족)**: 가장 보수적. → **채택**.

walk-forward 분할 정책 검토:
- **train 12m / test 6m / step 6m**: non-overlapping test (W0 test 와 W1 test 가 시간상 분리). 24m 데이터로 2 windows. 통계 신뢰도 ↓ (n=2) 단 독립성 보장. 본 ADR 의 primary.
- **train 12m / test 6m / step 3m**: overlapping test (test 윈도우 50% 중첩). 24m 데이터로 3 windows. 통계 신뢰도 ↑ (n=3) 단 train 누설 없음 (각 W 의 train_to < test_from). 본 ADR 의 secondary.
- **train 6m / test 4m / step 4m**: 더 많은 분할 (3+) 가능하나 RSI(14) lookback 고려 시 train 6m = 약 126 영업일 = RSI 누적은 가능하나 평균회귀 시계열 다양성 부족. → 거부.

본 ADR 은 두 분할 모두 **primary + secondary** 로 동시 평가해 robustness 교차 확인.

## 결정

1. **walk-forward pass-threshold = `Decimal("0.3")` 채택**. ``WalkForwardMetrics.is_pass = degradation_pct <= pass_threshold`` 계약. Issue #67 의 30% 악화 임계 그대로 채택 — 추가 보수화·완화 없음.

2. **walk-forward 분할 정책 — primary `train 12m / test 6m / step 6m` (2 windows) + secondary `train 12m / test 6m / step 3m` (3 windows)** 동시 평가. 두 구성 모두 PASS 시 C2 통과 확정. 한쪽만 통과하면 후속 ADR 로 분할 정책 재정의.

3. **walk-forward PASS 기준 — "이중 PASS"**:
   - 각 test window 의 ADR-0022 게이트 3종 (MDD > -25% · DCA 대비 양의 알파 · Sharpe > 0.3) 동시 통과.
   - aggregate ``degradation_pct ≤ 0.3`` 통과.
   - 두 조건 모두 충족 시에만 walk-forward PASS.

4. **per-window 게이트 2 (DCA 대비 알파) 산출** — 각 test window 마다 동일 시간 구간으로 ``compute_dca_baseline`` (069500 KODEX 200, 월 200,000 KRW) 호출 + ``RSI MR test 총수익률 - DCA test 총수익률`` 계산. window 마다 baseline 이 다름 — 시장 시기별 비교의 정확도 보장.

5. **`scripts/walk_forward_rsi_mr.py` CLI 도입** — `compute_rsi_mr_baseline` + `compute_dca_baseline` + `generate_windows` + `run_rsi_mr_walk_forward` 조합으로 1회 실행 → Markdown + CSV 리포트. ADR-0023 C2 검증 + 향후 walk-forward 평가의 운영 도구.

6. **`run_walk_forward(loader, BacktestConfig, windows)` (ORB engine 경로) 는 `NotImplementedError` 유지**. 본 PR 의 채택 후보 (PR5 RSI MR) 가 BacktestEngine 우회 평가 함수 (`compute_rsi_mr_baseline`) 사용이라 ORB 경로 본 구현 불필요. Phase 5 이후 ORB·VWAP-MR·Gap-Reversal 회귀 평가가 필요해지면 별도 PR 에서 추가.

7. **C2 통과 결정** — 본 ADR 의 결과 섹션에 두 분할 정책 (step6 + step3) 의 PASS 결과를 명시. 다음 검증 우선순위 = C3 (069500 수정주가 plausibility) → C4 (PR5 sensitivity grid).

## 결과

**긍정**

- ADR-0023 의 C2 (walk-forward 검증) 통과. 단일 1년 코호트 (PR5 원본 + C1 universe 199) 결과의 robustness 가 다년 train→test 분할로 확인됨.
- pass_threshold = 0.3 채택으로 후속 walk-forward 평가 (ORB 회귀, 다른 전략 추가 평가) 의 비교 baseline 정착.
- step6 (2 windows non-overlap) + step3 (3 windows overlap) 동시 통과 = 분할 정책 선택과 무관한 안정성 확인.
- per-window 게이트 + aggregate degradation 이중 검증 = 보수적 결정 — 한 분기 운영 부진이 누적 평균에 묻혀 통과되는 시나리오 차단.

**부정**

- 본 평가도 단일 데이터 소스 (pykrx 일봉, `data/stock_agent.db`) 의존. C3 (수정주가 plausibility) 미해결 상태에서 절대 수익률 신뢰도 baseline 잠정.
- step6 의 표본 수 (n=2) 가 통계적으로는 약함 — step3 의 n=3 도 retail 백테스트 한계. 다년 코호트 (Phase 5 의 학술 walk-forward 표본 12+ 분할) 와 비교 시 robustness 신뢰도 제한.
- aggregate degradation 계산이 단순 산술 평균 — train_avg 가 0 에 가까울수록 분모 불안정. 본 평가에서는 train_avg = +18~19% 로 분모 충분.
- walk-forward CLI (`scripts/walk_forward_rsi_mr.py`) 가 본 PR 도입 — 운영자가 재실행할 수 있도록 README/docs 갱신 필요.

**중립**

- Phase 3 (모의투자 무중단 운영) 진입은 여전히 C3 + C4 통과 후로 게이팅 (ADR-0023 7항 유지). 본 ADR 의 C2 통과는 ADR-0023 의 진입 조건 4종 중 2번째 통과 (C1 + C2) 에 해당.
- ORB 경로 walk-forward 본 구현은 의도적 보류. 향후 RSI MR 외 추가 채택 후보가 등장하면 BacktestEngine 통합 walk-forward 본 구현 별도 ADR.
- ``WalkForwardMetrics.is_pass`` 의 의미는 "aggregate degradation 통과" 만이며 per-window 게이트 통과는 ``WalkForwardResult.per_window_metrics`` 를 호출자가 검사. 운영 PASS 선언은 두 조건 동시 충족 — 본 결정의 3항.

## 추적

- 코드: `src/stock_agent/backtest/walk_forward.py::generate_windows` (본 구현), `run_rsi_mr_walk_forward` (신규), `_add_months` (helper). 기존 DTO + 가드는 변경 없음. `run_walk_forward(BacktestConfig, ...)` 는 `NotImplementedError` 유지.
- 코드: `scripts/walk_forward_rsi_mr.py` 신규 — argparse + DailyBarLoader + per-window 게이트 + aggregate degradation + Markdown/CSV 리포트.
- 테스트: `tests/test_walk_forward.py` (`TestGenerateWindows` 15 케이스 추가, `TestGenerateWindowsStub` 제거), `tests/test_walk_forward_rsi_mr.py` 신규 10 케이스.
- 산출물: `data/c2_walk_forward_rsi_mr_step6.{md,csv}` (primary, 2 windows), `data/c2_walk_forward_rsi_mr_step3.{md,csv}` (secondary, 3 windows).
- 런북: `docs/runbooks/c2_walk_forward_rsi_mr_2026-05-02.md` (본 ADR 의 정량 근거).
- 관련 이슈: #67 (walk-forward skeleton, 본 PR 에서 본 구현 진행).
- 관련 ADR: [ADR-0017](./0017-phase2-pass-1year.md), [ADR-0019](./0019-phase2-backtest-fail-remediation.md), [ADR-0022](./0022-step-f-gate-redefinition.md), [ADR-0023](./0023-rsi-mr-strategy-adoption-conditional.md).
- 도입 PR: TBD (본 ADR 도입 PR — ADR-0023 C2 추가 검증).
- 후속 진행 중: C3 (069500 수정주가 plausibility 검증), C4 (PR5 sensitivity grid). C2 PASS 후 우선순위 = C3 → C4.
