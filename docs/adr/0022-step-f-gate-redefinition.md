---
date: 2026-05-01
status: 승인됨
deciders: donggyu
related: [0017-phase2-pass-1year.md, 0019-phase2-backtest-fail-remediation.md, 0021-step-e-vwap-gap-failed.md]
---

# ADR-0022: Step F 게이트 재정의 — 일중 가정 폐기, 일/월 단위 + DCA baseline 상대 비교

## 상태

승인됨 — 2026-05-01. ADR-0019 의 Phase 2 PASS 게이트 (일중 데이트레이딩 가정) 를 Step F (가설 풀 확장) 범위에서 재정의. ADR-0019 자체는 폐기 X — 일중 가정 평가 사이클의 사실 기록으로 보존.

## 맥락

ADR-0019 (2026-04-24) 는 Phase 2 1차 백테스트 FAIL (MDD -51%) 직후 작성한 복구 로드맵으로, **세 게이트** 를 정의했다:

1. MDD > -0.15 (낙폭 절대값 15% 미만)
2. 승률 × 평균 손익비 > 1.0 (기대값 양수)
3. 연환산 샤프 > 0 (위험조정 수익 양수)

이 게이트는 **ORB 일중 데이트레이딩 가정** 하에 설계됐다. 일중 force_close (15:00) + stop-loss 1.5% + take-profit 3% 로 일일 변동성을 묶어두는 전제로 -15% 임계가 합리적이었다.

Step E (2026-05-01) 까지 진행한 평가 결과:

- ORB Step A~D = 220+ 런 / 0 PASS (Step C·D 두 서브셋 전원 FAIL)
- VWAP-MR + Gap-Reversal Step E = 4 런 / 0 PASS

상세는 [ADR-0021](0021-step-e-vwap-gap-failed.md) + 관련 runbook. 전 230+ 런이 ADR-0019 게이트 통과 0 — 한국 KOSPI 200 일중 long-only 데이트레이딩으로 retail 자본 (100~200만원) 에서 alpha 확보 불가능함을 확인했다 (Barber-Lee-Liu-Odean 2014 / Chague 2019 학술 통계와 정합).

운영자는 (사용자 결정, 2026-05-01) 일중 가정을 폐기하고 **일/월 단위 + 학술 검증 가설 풀 + DCA baseline 비교** 로 전환을 결정했다. 이 전환은 ADR-0019 의 게이트 임계값을 그대로 유지할 수 없는 4 가지 사유:

### ADR-0019 게이트가 일/월 단위에 부적합한 사유

1. **MDD > -15% 임계는 일중 force_close 전제 산물**. 일/월 단위 보유는 overnight gap·이벤트 리스크에 노출되며 코스피 200 ETF 자체가 1980~ 단일 -50% drawdown 사례 다수 (1997 IMF · 2008 금융위기 · 2020 코로나). KOSPI 200 historical MDD 가 -50% 인데 그것을 추종하는 자동매매에 -15% 요구 = 가설 자체가 시장 수익 추구를 거부하는 형태가 됨.

2. **승률 × 손익비 > 1.0 은 빈번 매매 가정**. 매월 1~2회 거래하는 모멘텀·이평선 전략은 표본 (12~24 거래/년) 이 작아 메트릭 분산 큼. 단일 임계로 정규화 어려움.

3. **연환산 샤프 > 0 (절대값)** 은 DCA baseline 의 historical 샤프 (KOSPI 200 ≒0.3~0.5) 보다 낮은 구간을 PASS 시킴. 자동매매가 DCA 보다 못한데 PASS 시키는 모순.

4. **Buy-and-Hold (DCA) 비교 부재**. ADR-0019 게이트는 절대 수익만 봄. retail 영역의 진짜 질문은 "그냥 인덱스 사는 것보다 나은가?" — 절대 수익이 아닌 상대 알파.

검토한 대안:

- **ADR-0019 게이트 그대로 유지** + 일/월 전략에 적용. 단점 — 위 4 사유로 의미 없는 게이트화. → 거부.
- **모든 임계 폐기, 정성적 판단**. 단점 — sanity check 부재로 과적합 함정 노출. → 거부.
- **ADR-0019 폐기 후 신규 ADR**. 단점 — 일중 가정 평가 사이클의 역사 기록 손실. → 거부 (ADR-0019 보존).
- **ADR-0019 의 Step F 범위만 재정의 (본 ADR)**. 일중 가정 ADR-0019 결과 섹션은 보존, Step F 범위는 본 ADR 게이트 적용. → **선택**.

## 결정

Step F (가설 풀 확장 — `docs/step_f_strategy_pool_plan.md`) 범위 내 모든 백테스트는 다음 **세 게이트** 로 판정한다. ADR-0019 게이트는 Step F 평가에 사용하지 않는다.

### 게이트 1 — MDD > -25% (절대 손실 한도 완화)

낙폭 절대값 25% 미만. KOSPI 200 ETF 자체의 historical MDD (-50% 수준) 대비 완화된 한계로, "시장 평균보다 손실폭이 절반 이하" 를 요구한다. 일/월 단위 보유 + 분산 자동매매가 시장 평균보다 변동성 낮아야 함을 sanity check 한다. strict greater (`> -0.25`) — `-0.25` 정확값은 FAIL.

### 게이트 2 — DCA baseline 대비 양의 알파 (상대 비교, 핵심 게이트)

`(전략 총수익률) - (F1 DCA baseline 동일 기간 총수익률) > 0`. KOSPI 200 ETF 매월 정액 매수의 동일 기간 수익률을 산출 (`F1 DCA baseline` PR 산출물) 하고, 평가 전략이 그 baseline 을 단순 초과하는지 본다.

retail 자동매매의 진짜 가치 = "패시브 인덱스보다 나은가?". 본 게이트가 PASS 못 하면 자동매매 자체가 무의미 — 그냥 ETF DCA 가 정답. 0 또는 음수면 자동매매 폐기 + DCA 채택 결정 트리거.

본 게이트는 단순 "전체 기간 수익률 비교" 로 시작 (구현 단순성). 향후 보강 후보:
- α (Jensen) — 시장 모형 회귀 잔차.
- Information Ratio — (전략 - baseline) / tracking error.
- 기간별 (월·분기) baseline 초과 비율.

본 ADR 은 단순 비교만 강제. 보강은 별도 ADR 로.

### 게이트 3 — 연환산 Sharpe > 0.3 (위험조정 수익)

KOSPI 200 ETF historical Sharpe 가 0.3~0.5 구간 (1980~2025, 무위험 = 한국 단기금리). 게이트 1·2 만으로는 "운으로 baseline 살짝 이긴" 표본이 통과할 수 있어, 위험조정 양의 수익을 추가 sanity check.

`avg(daily_returns) / pstdev(daily_returns) * sqrt(252)`. 표본 < 240 영업일이면 메트릭 분산 큼 — 참고용으로만 사용 (PASS 라벨은 240 이상에서만 인정).

### 적용 범위 (Step F PR 별)

본 게이트 3종은 Step F 의 **모든** 백테스트 산출물에 적용한다 (`scripts/backtest.py` verdict 라벨 또는 별도 판정 스크립트). 각 PR runbook 에 게이트별 PASS/FAIL 명시.

### Step F PASS 조건

게이트 1 + 게이트 2 + 게이트 3 **세 조건 동시** 통과 시 PASS. 한 게이트라도 FAIL 시 FAIL — ADR-0019 와 동일 strict 정책.

게이트 1 단독 PASS · 게이트 2 단독 PASS 등 부분 통과 라벨은 `verdict` 필드에서 표시하되 최종 PASS 선언 근거로는 사용하지 않는다.

### Step F 종합 판정 시나리오

| 시나리오 | 다음 행동 |
|---|---|
| F1 DCA + F2~F5 중 1+ 가 세 게이트 동시 통과 | PASS 전략 채택 ADR + Phase 3 모의투자 진입 검토 |
| F1 DCA 만 PASS, 다른 모두 FAIL | 자동매매 폐기 + DCA 단순 cron 으로 정리. 본 프로젝트 자동매매 영역 close ADR. retail 영역 정직한 결과 |
| 전부 FAIL (DCA 도 FAIL) | KOSPI 200 시장 자체가 1년치 음수 구간 — Step F 폐기 + 다른 자산 (해외 ETF 등) 평가 신규 ADR |

## 결과

**긍정**
- 일/월 시간프레임 전환과 정합. ADR-0019 의 일중 가정 게이트가 Step F 에 무리하게 적용되는 모순 해소.
- DCA baseline 비교 게이트 도입 — retail 자동매매의 진짜 가치 (패시브 대비 알파) 를 직접 측정.
- 시장 평균 대비 손실폭 절반 임계 (게이트 1) 가 retail 자동매매의 sanity check 로 합리적.
- 학술 통계 (KOSPI 200 historical Sharpe ≒0.3~0.5) 와 정합한 절대 임계 (게이트 3).

**부정**
- 게이트 임계 변경은 평가 기준 완화로 비치기 쉬움. ADR-0019 임계 (-15%) 가 강했던 만큼 (-25%) 완화는 명시적 정당화 필요 — 본 ADR 의 4 사유로 기록.
- DCA 비교 게이트는 baseline 산출 PR (F1) 이 선행되어야 후속 PR (F2~F5) 평가 가능 — 의존성.
- Step F 도 모두 FAIL 시 다음 결정이 또 다시 ADR + plan 작성 사이클 — 시간 비용.

**중립**
- ADR-0019 결과 섹션에 "Step F 부터는 본 ADR-0022 게이트로 전환" 사후 추가 (root CLAUDE.md 명시: 로드맵 ADR 의 결과 추가는 사후 수정 금지 규칙 예외).
- ADR-0017 의 "1년치 표본 + 240 영업일 최소" 정책은 Step F 에도 그대로 적용 — 본 ADR 은 임계만 재정의.
- `scripts/backtest.py::_verdict_label` 또는 별도 판정 헬퍼는 Step F 진입 시 본 ADR 게이트를 반영하도록 갱신 (별도 PR — 본 ADR 은 결정만 기록).

## 추적

- 코드 (예정): `scripts/backtest.py::_verdict_label` 또는 `src/stock_agent/backtest/gate.py` (Step F 진입 PR), `src/stock_agent/strategy/<신규 전략>` (F1~F5 각 PR)
- 문서: `docs/step_f_strategy_pool_plan.md`, root `CLAUDE.md` 현재 상태, `README.md` Phase 상태
- 도입 PR: TBD (본 ADR 은 ADR-0021 + Step F plan 과 동일 PR)
- 관련 ADR: [ADR-0019](0019-phase2-backtest-fail-remediation.md), [ADR-0021](0021-step-e-vwap-gap-failed.md)
