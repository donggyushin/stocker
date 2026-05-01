---
date: 2026-05-01
status: 승인됨
deciders: donggyu
related: [0019-phase2-backtest-fail-remediation.md, 0022-step-f-gate-redefinition.md]
---

# ADR-0021: Step E VWAP-MR · Gap-Reversal 두 후보 폐기 + Step F 가설 풀 확장으로 전환

## 상태

승인됨 — 2026-05-01. 운영자 결정으로 Step E 평가 사이클 종료. 다음 옵션은 Step F (전략 가설 풀 확장 + DCA baseline 비교) 로 전환 — [ADR-0022](0022-step-f-gate-redefinition.md) 참조.

## 맥락

ADR-0019 (Phase 2 복구 로드맵) Step E 는 ORB 가 Step A~D 에서 모두 FAIL 한 뒤 도입한 **전략 교체** 단계로, 두 후보를 평가하기로 결정했다 (2026-05-01 사용자 결정):

1. **VWAP Mean-Reversion** — 분 단위 VWAP 회귀 가설.
2. **Opening Gap Reversal** — 갭 후 평균회귀 가설.

Step E 코드 산출물은 PR1~PR4 (4 PR, #94~#97) 로 머지 완료. PR4 Stage 1+2 에서 `--strategy-type` CLI 인자 + `DailyBarPrevCloseProvider` 가 통합되었고, 본 결정 직전 사용자가 `scripts/backfill_daily_bars.py` 도입(커밋 7cd880f) 으로 일봉 캐시 결정론을 확보한 뒤 4 런 백테스트를 실행했다.

### 결과 (2026-05-01, 1년치 KIS 분봉 캐시 + Top 50 / Top 100)

| 후보 × 서브셋 | MDD | 승률 × 손익비 | 샤프 |
|---|---|---|---|
| VWAP-MR Top 50 | -49.09% ❌ | 0.0458 ❌ | -11.02 ❌ |
| VWAP-MR Top 100 | -50.11% ❌ | 0.0451 ❌ | -10.35 ❌ |
| Gap-Reversal Top 50 | -10.19% ✅ | 0.339 ❌ | -3.23 ❌ |
| Gap-Reversal Top 100 | -19.99% ❌ | 0.289 ❌ | -6.27 ❌ |

ADR-0019 의 세 게이트 (MDD > -15% · 승률×손익비 > 1.0 · 샤프 > 0) **동시 통과 0**. Gap-Reversal Top 50 만 MDD 게이트 단독 통과 (스크립트 verdict 가 MDD-only 라 PASS 라벨 표시) — 세 게이트 종합 시 FAIL.

상세 메트릭은 `docs/runbooks/step_e_vwap_mr_2026-05-01.md` · `docs/runbooks/step_e_gap_reversal_2026-05-01.md`.

### 검토한 대안 (이 시점에서 가능한 다음 행동)

- **Stage 4 진입**: 민감도 그리드 + walk-forward. ADR-0019 L210 정의에 따라 "한 후보·한 서브셋이라도 PASS" 가 조건. 본 결과는 0 PASS → **자격 미달**.
- **Stage 5 시나리오 A/B (한 후보 채택)**: 본 결과로는 채택 가능 후보 없음 → **자격 미달**.
- **Stage 5 시나리오 C (둘 다 폐기) + 다음 옵션 평가**: 본 ADR.

### 시나리오 C 의 다음 옵션 후보 (plan.md L281, ADR-0019 후속)

(a) **Pre-market pullback** — 시간외 단일가 갭에 대한 풀백 진입. 데이터 요건: 시간외 단일가 정보 (KIS API 미제공 가능성 높음). 추가 인프라 부담.

(b) **Intraday momentum (SMA crossover)** — 분봉 SMA 5/20 골든크로스. 가설은 다르나 ORB 와 같은 "추세 추종" 계열이라 ORB FAIL 의 동일 원인 (한국 일중 추세 부재) 에 노출.

(c) **데이트레이딩 자체 폐기** — Phase 1~2 의 분봉·일중 가정을 근본 폐기. **스윙 트레이딩** (보유 기간 일~주) 또는 **다른 자산 클래스** (선물·ETF·해외) 로 전환. plan.md 광범위 재작성 필요.

(d) **현 단계 동결** — 추가 전략 평가 중단, Phase 2 PASS 무기한 보류. ORB 코드·Step E 산출물 보존하되 실전 진입 시도 중단. 학습 산출물(코드·테스트·문서·인프라) 은 다음 시도의 기반으로 활용.

## 결정

1. **VWAP-MR Strategy 폐기**. `VWAPMRStrategy` 코드는 Step E PR2 의 회귀·재현용으로 보존하되 Phase 2 PASS 후보에서 제외.

2. **Gap-Reversal Strategy 폐기**. `GapReversalStrategy` 및 `DailyBarPrevCloseProvider` 코드는 Step E PR3·PR4 회귀·재현용으로 보존하되 Phase 2 PASS 후보에서 제외.

3. **ORB Strategy 비채택 유지**. ADR-0019 Step A~D 결과로 사실상 폐기 상태. 본 ADR 은 ORB 폐기를 명시적으로 재확인하나 코드는 보존 (회귀 비교용).

4. **다음 옵션 = Step F (가설 풀 확장)**. 본 결정과 함께 운영자가 (a)~(d) 4 옵션 중 **(b) intraday momentum 변형 + DCA baseline 비교** 경로의 확장 버전을 선택. 일중 데이트레이딩 가정을 폐기하고 일/월 시간프레임 전환 + 학술 검증 가설 풀 (이평선 cross · cross-sectional 모멘텀 · 저변동성 · RSI 평균회귀) + Buy-and-Hold DCA 를 baseline 으로 비교한다. 게이트는 ADR-0019 의 일중 가정 (MDD>-15%·승×손익비>1.0·샤프>0) 을 [ADR-0022](0022-step-f-gate-redefinition.md) 의 일/월 단위 게이트 (MDD>-25%·DCA 대비 양의 알파·연환산 샤프>0.3) 로 재정의. 상세 진행 plan 은 `docs/step_f_strategy_pool_plan.md`.

5. **이슈 #78 close**. Step E 평가 완료 — 본 ADR + 두 runbook 으로 마무리.

6. **Phase 2 PASS 미달성 상태 유지**. ADR-0019 의 세 게이트 통과 전까지 Phase 3 진입 금지 원칙은 그대로. 다음 옵션 결정 후 평가 재개.

7. **문서 동기화**: root `CLAUDE.md` 현재 상태 단락에 Step E FAIL 사실 추가, `README.md` Phase 상태 단락 동일 갱신, `docs/adr/README.md` 인덱스 1줄 추가, `docs/step_e_followup_plan.md` 는 Stage 3·5 결과 반영 후 본 ADR 링크하고 폐기 (plan 헤더 명시: "진행 완료 후 결과는 별도 `docs/runbooks/` 에 기록하고 본 파일은 삭제").

## 결과

**긍정**
- Step E 평가 사이클 종료. 한정된 운영 시간을 명확한 다음 결정으로 회수.
- 두 후보의 정확한 실패 양상 (VWAP-MR: 손익비 0.07 / Gap-Reversal: 승률 36~42%) 이 향후 전략 설계에 유효한 sanity 데이터로 남음.
- 백테스트 인프라 (`--strategy-type`·`DailyBarPrevCloseProvider`·`backfill_daily_bars`·민감도 그리드 병렬화) 가 다음 후보 평가에 그대로 재사용 가능.

**부정**
- Phase 2 PASS 미달성 누적 — Phase 3 모의투자 무중단 운영 계획 보류 지속.
- 한국 시장 일중 데이트레이딩의 ORB·VWAP-MR·Gap-Reversal 세 가설 모두 부정적 — 가설 풀이 좁아짐. 다음 옵션 (a)~(d) 모두 새로운 인프라 또는 plan 광범위 재작성을 요구.
- 본 결정은 "다음에 무엇을 할지" 를 미루므로 일정상 추가 보류 기간 발생.

**중립**
- 코드 산출물 보존 — `VWAPMRStrategy`·`GapReversalStrategy`·`DailyBarPrevCloseProvider`·민감도 그리드 모두 git 이력에 남음. 향후 walk-forward 등 비교 baseline 으로 재사용 가능.
- ADR-0019 의 결과 섹션은 "Step E FAIL → 다음 옵션 결정 보류" 로 갱신 (로드맵 ADR 의 결과 섹션 추가는 사후 수정 금지 규칙의 예외 — root CLAUDE.md 명문화).

## 추적

- 코드: `src/stock_agent/strategy/vwap_mr.py`, `src/stock_agent/strategy/gap_reversal.py`, `src/stock_agent/backtest/prev_close.py`, `scripts/backfill_daily_bars.py`
- 산출물: `data/step_e_vwap_mr_top{50,100}.{md,csv}`, `data/step_e_gap_reversal_top{50,100}.{md,csv}`, 동일 prefix `_trades.csv`
- 런북: `docs/runbooks/step_e_vwap_mr_2026-05-01.md`, `docs/runbooks/step_e_gap_reversal_2026-05-01.md`
- 관련 PR: #94 (PR1) · #95 (PR2) · #96 (PR3) · #97 (PR4 Stage 1+2) · 본 결정 도입 PR (TBD)
- 관련 이슈: #78 (Step E 본 이슈)
- 관련 ADR: [ADR-0019](0019-phase2-backtest-fail-remediation.md)
