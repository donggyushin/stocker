---
date: 2026-05-02
status: 승인됨
deciders: donggyu
related: [0017-phase2-pass-1year.md, 0019-phase2-backtest-fail-remediation.md, 0021-step-e-vwap-gap-failed.md, 0022-step-f-gate-redefinition.md]
---

# ADR-0023: F5 RSI 평균회귀 1차 채택 (조건부) — Step F 시나리오 A 판정

## 상태

승인됨 — 2026-05-02. ADR-0019 Phase 2 복구 로드맵 Step F (가설 풀 확장) 의 종합 판정 ADR. ADR-0022 게이트 적용 결과 PR5 (RSI 평균회귀) 가 게이트 3종 동시 통과 — 시나리오 A (PASS 후보 채택) 발동. 단, Phase 3 모의투자 진입은 본 ADR 의 추가 검증 4 항목 통과 후로 게이팅한다.

## 맥락

ADR-0021 (2026-05-01) 로 Step E (VWAP-MR · Gap-Reversal) 폐기 + 일중 데이트레이딩 가정 자체 폐기. ADR-0022 (2026-05-01) 로 Step F 게이트 재정의 (MDD>-25%·DCA 대비 알파·연환산 Sharpe>0.3). `docs/step_f_strategy_pool_plan.md` 의 PR0~PR5 분할로 5 가설 평가:

| PR | 가설 | trades | MDD | Sharpe | 총수익률 | DCA 알파 | 종합 |
|---|---|---|---|---|---|---|---|
| PR1 | F1 DCA baseline | 13 lots | -12.92% | 2.2683 | +51.50% | N/A | PASS |
| PR2 | F2 Golden Cross 200d SMA | 1 | -20.52% | 2.2753 | +182.36% | +130.86%p | PASS (단 trades=1 caveat) |
| PR3 | F3 Cross-sectional 모멘텀 | 14 | -7.70% | 0.9910 | +11.22% | -36.96%p | FAIL |
| PR4 | F4 저변동성 | 19 | -9.62% | 1.1713 | +15.87% | -32.31%p | FAIL |
| PR5 | F5 RSI 평균회귀 | 175 | -6.40% | 2.4723 | +56.31% | +8.13%p | PASS |

상세는 `docs/runbooks/step_f_summary_2026-05-02.md` + 5 PR 별 런북.

ADR-0022 의 시나리오 표 적용 결과 시나리오 A (F2~F5 중 1+ 가 게이트 3종 동시 통과 + DCA 도 PASS) 가 충족됐다. PASS 후보 2종 (PR2·PR5) 비교에서 다음 4 차원으로 PR5 우위:

1. **통계 신뢰도** — PR2 trades=1 vs PR5 trades=175. ADR-0017 의 240 영업일 표본 정신과 정합한 표본 확보는 PR5 만 충족.
2. **MDD·Sharpe** — PR5 가 두 지표 모두 Step F 최고 (MDD -6.40% / Sharpe 2.4723).
3. **데이터 plausibility 영향** — PR2 는 069500 1년 +180% 라는 비현실적 절대 수익 + 단일 종목으로 데이터 보정 오류에 직격. PR5 는 cross-sectional 평균회귀라 종목간 상대 변동성에서 알파 추출 — 인덱스 절대 가격 오차의 영향 작음.
4. **평가 구간 robustness** — PR5 175 entry/exit pair 가 Step F 전체에서 가장 풍부한 표본.

검토한 대안:

- **PR2 채택**: 단일 trade 통계 의미 없음. retail 운영 환경에서 sma_period=200 cross 1회로 1년 +180% 결과 = 100% 운에 의존. → 거부 (1차 채택 보류, sma_period 단축 후속 평가).
- **PR5 + PR2 동시 채택**: ensemble 후보로는 가치 있으나 1차 채택은 단일 전략 명시 필요 (모니터링·튜닝·실패 진단 단순화). → PR5 단독 1차 채택 + PR2 후속 검토 보존.
- **시나리오 B (자동매매 폐기 + DCA 채택)**: 게이트 2 PASS 후보 존재 — 발동 조건 미충족.
- **시나리오 C (Step F 폐기)**: DCA + 2 후보 PASS — 발동 조건 미충족.
- **PR5 즉시 Phase 3 진입**: 본 평가의 4 caveat (universe 부분집합 + 단일 1년 코호트 + 069500 데이터 plausibility 미해결 + sensitivity grid 미실행) 미해결 상태로 모의투자 자본 노출 위험. → 거부.
- **PR5 채택 + Phase 3 진입 조건부 게이팅**: 본 ADR.

## 결정

1. **F5 RSI 평균회귀 (`RSIMRStrategy`) 를 Step F 1차 채택 후보로 선정**. 코드 산출물 (`src/stock_agent/strategy/rsi_mr.py` · `src/stock_agent/backtest/rsi_mr.py` · `scripts/backtest.py --strategy-type rsi-mr` 라우팅) 을 Phase 2 PASS 후보 단일 전략으로 확정.

2. **Phase 3 (모의투자 무중단 운영) 진입은 다음 4 추가 검증 전부 통과 후에만 허가**:

   - **C1. universe 199 종목 전체 백필 + 재평가** — `data/stock_agent.db` 일봉 캐시 현재 101 종목 부분집합. `scripts/backfill_daily_bars.py --universe-yaml config/universe.yaml --from 2024-04-01 --to 2026-04-21` 로 KOSPI 200 universe 199 종목 전체 백필 후 PR5 재실행. ADR-0022 게이트 3종 모두 재통과 확인.
   - **C2. walk-forward 검증** — `src/stock_agent/backtest/walk_forward.py` 본 구현 (현재 PR #70 스켈레톤만 존재) + 다년 일봉 캐시로 2~4 분할 walk-forward. 각 분할에서 ADR-0022 게이트 3종 동시 통과. 단일 1년 코호트 결과만으로 robustness 보장 불가.
   - **C3. 069500 일봉 수정주가 보정 검증** — pykrx 일봉 (액면분할·병합·배당) 수정 여부 KRX 정보데이터시스템 [11003/11006] 직접 비교로 확정. PR1~PR5 절대 수익률이 동일 데이터 소스이므로 본 검증은 Step F 전체 결론의 신뢰도 baseline.
   - **C4. PR5 파라미터 sensitivity grid** — `rsi_period` (현행 14) · `oversold_threshold` (현행 30) · `overbought_threshold` (현행 70) · `stop_loss_pct` (현행 -3%) · `max_positions` (현행 10) 민감도 평가. `step_f_grid` 신설 후 32~96 조합 스윕. PR5 의 청산 사유 64.6% 가 stop_loss 인 만큼 본 축의 민감도 핵심.

3. **C1~C4 통과 후 Phase 2 PASS 공식 선언 + Phase 3 착수 재허가**. 본 ADR 의 상태를 그대로 유지하되 추가 검증 결과는 별도 런북 + 후속 ADR 또는 본 ADR 의 사후 결과 보강 (ADR-0019 와 동일 패턴) 으로 기록.

4. **PR2 Golden Cross 는 1차 채택 보류 (단일 trade caveat)**. 코드 산출물 보존. 후속 옵션 (sma_period 50d/100d 단축 평가 · 다년 백테스트 · RSI MR 와 ensemble) 은 PR5 채택 안정화 후 별도 평가.

5. **PR3 모멘텀 · PR4 저변동성은 채택 후보 제외 (본 평가 환경 한계 인정)**. 두 가설은 다년·다국가 학술 검증 (Jegadeesh-Titman 1993 · Frazzini-Pedersen 2014) 이라 한국 1년 KOSPI 200 부분집합 단일 코호트로 negative 결론 도출 불가. 코드 산출물 보존, Phase 5 다년 walk-forward 시 baseline 으로 재사용.

6. **`docs/step_f_strategy_pool_plan.md` 파일 삭제**. 헤더 명시 (line 3) 에 따라 PR6 진입 후 삭제. 본 ADR + `docs/runbooks/step_f_summary_2026-05-02.md` 가 후속 정본 — Step F 진행 사실은 두 문서 + 5 PR 런북 + ADR-0021/0022 로 완전히 보존됨.

7. **ADR-0019 의 "수익률 확보 전 Phase 3 금지" 정책 유지**. ADR-0022 의 게이트 재정의는 평가 기준 변경이지 정책 완화가 아니다. PR5 가 ADR-0022 게이트 3종 통과 = Phase 2 백테스트 단계 PASS 조건 충족. 단 Phase 3 진입은 본 ADR 의 C1~C4 추가 검증 통과 후로 게이팅 — ADR-0019 정책의 본질 (수익률 확보 후 진입) 과 정합.

8. **이슈 #78 close 유지**. 본 ADR 작성 시점에 #78 (Step E 이슈) 는 ADR-0021 작성 직후 이미 close 됨. 본 ADR 은 Step F 진행 결과로 #78 close 결정과 별도 — Step F 자체 후속 이슈는 미발급 (본 ADR + 런북이 종결 산출물).

## 결과

**긍정**

- Step F 평가 사이클 종료. 5 가설 평가 완료 + 1 채택 후보 확정 → 다음 작업 명확화 (C1~C4 추가 검증).
- ADR-0019 의 230+ 런 / 0 PASS 누적 후 처음으로 PASS 후보 (PR5) 확보. retail 자동매매 영역에서 ADR-0022 게이트 통과 사실 자체가 Step F 전환의 정당화.
- 평균회귀 본질 (단기 noise 회복) 이 인덱스 베타와 직교 → KOSPI 200 강세장에서도 작동. 향후 약세장·횡보장 robustness 도 walk-forward 로 검증 가능.
- 4 caveat 를 결정 본문에 명시 → Phase 3 진입 조건이 명확. 후속 세션에서도 동일 게이팅 유지.
- 코드 산출물 (PR1~PR5 전부) 보존 → 회귀 비교·ensemble·다년 평가 baseline 으로 재사용.

**부정**

- 단일 가설 채택 = 향후 PR5 가 실제 운영에서 실패 시 fallback 부재. PR2·PR3·PR4 코드 보존이 mitigation 이나 즉시 전환 절차 미정의.
- C1~C4 4 검증 = 추가 시간 비용. C2 (walk-forward 본 구현) 는 모듈 신규 작업 분량.
- 본 평가의 데이터 plausibility 미해결 (069500 1년 +180%) → 절대 수익률 수치 전체 신뢰도 baseline 미확정 상태로 채택 결정. C3 통과 시점에 본 ADR 의 절대값들은 재해석 필요.
- 승률 34.29% 는 retail 운영자 심리 부담 큼 (10 trade 중 6~7 손절). 자동매매라 감정 개입 차단되나 운영자가 strategy 이탈 (수동 종료 등) 위험 존재 — Phase 3 운영 절차서에 명시 필요.

**중립**

- ADR-0019 결과 섹션의 "후속 결정 (ADR-0021 + ADR-0022)" 다음에 본 ADR 링크 추가 (로드맵 ADR 의 결과 추가는 사후 수정 금지 규칙 예외).
- ADR-0017 의 "1년치 표본 + 240 영업일 최소" 정책은 PR5 평가 (258 영업일 + trades=175) 로 충족. C2 walk-forward 는 다년 코호트 검증으로 정책 강화.
- `scripts/backtest.py::_verdict_label` 또는 별도 게이트 헬퍼는 Step F 평가에서 ADR-0022 게이트를 표시하도록 갱신 (별도 PR — 본 ADR 은 결정만 기록).
- Phase 3 코드 산출물 (Executor·main.py APScheduler·monitor·storage) 은 그대로 보존 — 채택 후보 PR5 가 `Strategy` Protocol 준수라 전략 주입 경로 재사용 가능.

## 추적

- 코드: `src/stock_agent/strategy/rsi_mr.py`, `src/stock_agent/backtest/rsi_mr.py`, `scripts/backtest.py --strategy-type rsi-mr` (1차 채택). `src/stock_agent/strategy/{golden_cross,momentum,low_volatility,dca}.py` + `src/stock_agent/backtest/{golden_cross,momentum,low_volatility,dca}.py` (보존).
- 산출물: `data/step_f_rsi_mr.{md,csv}`, `data/step_f_dca_same_window.md` (게이트 2 비교 baseline).
- 런북: `docs/runbooks/step_f_summary_2026-05-02.md` (본 ADR 의 정량 근거), 5 PR 별 런북 (`docs/runbooks/step_f_{dca_baseline,golden_cross,momentum,low_volatility,rsi_mr}_2026-05-02.md`).
- 폐기 대상: `docs/step_f_strategy_pool_plan.md` (본 ADR + 종합 런북으로 대체).
- 관련 이슈: #78 (Step E 이슈, ADR-0021 작성 직후 close 완료).
- 관련 ADR: [ADR-0017](./0017-phase2-pass-1year.md), [ADR-0019](./0019-phase2-backtest-fail-remediation.md), [ADR-0021](./0021-step-e-vwap-gap-failed.md), [ADR-0022](./0022-step-f-gate-redefinition.md).
- 도입 PR: TBD (본 ADR 도입 PR — Step F PR6).
- 후속 진행 중:
  - C1 통과 (2026-05-02, `docs/runbooks/c1_universe_full_backfill_2026-05-02.md` — universe 199 종목 백필 + PR5 재평가 MDD -8.17% / Sharpe 2.2966 / 총수익률 +63.44% / DCA 알파 +15.26%p / trades=177, 게이트 3종 PASS).
  - C2 통과 (2026-05-02, `docs/runbooks/c2_walk_forward_rsi_mr_2026-05-02.md` — walk-forward 본 구현 (`scripts/walk_forward_rsi_mr.py`) + 다년 캐시 (2024-04-01~2026-04-21) 분할 평가. step6 (2 windows, degradation -5.16%) + step3 (3 windows, degradation +11.32%) 모두 ADR-0022 게이트 + degradation 임계 (≤ 0.3, ADR-0024) 통과).
  - C3 통과 (2026-05-03, `docs/runbooks/c3_069500_adjusted_plausibility_2026-05-03.md` — pykrx 일봉 캐시가 수정주가 데이터로 확정 (Stage 3 cache vs adjusted=True diff 0). ETF/KOSPI200 비율 점프 0건 (Stage 2). Google Finance 069500 + Wikipedia KOSPI 200 absolute level cross-check 정합 (Stage 4). PR1~PR5 절대 수익률은 데이터 보정 오류가 아닌 한국 KOSPI 200 강세장 macro 의 결과로 확정).
  - C4 통과 (2026-05-03, `docs/runbooks/c4_rsi_mr_sensitivity_2026-05-03.md` — step_f_rsi_mr_grid 96 조합 (5축 3×2×2×4×2) 실행. DCA baseline +48.18% (PR1 정합) 대비 64/96 (66.67%) all_gates_pass. 현행 14/30/70/0.03/10 PASS, 1축 변동 인접 7/8 (87.5%). 게이트 1·3 100% PASS, 게이트 2 만 32 FAIL (DCA 알파 음수). Phase 3 진입 게이트 판정 PASS).
  - **C1~C4 전원 통과 (2026-05-03) → Phase 2 PASS 공식 선언 + Phase 3 착수 재허가**. ADR-0023 결정 3항 조건 충족. `main.py` 모의투자 무중단 운영 착수 가능.
