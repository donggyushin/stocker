---
date: 2026-04-24
status: 승인됨
deciders: donggyu
related: [ADR-0017, ADR-0018, ADR-0006]
---

# ADR-0019: Phase 2 백테스트 1차 FAIL 반영 + 수익률 확보 전 Phase 3 진입 금지 정책

## 상태

승인됨 — 2026-04-24.

## 맥락

2026-04-24 02:06 UTC+9 에 1년치 KIS 분봉 백필이 완료됐다 (199 심볼, `data/minute_bars.db` 2.78 GB, 러닝 시간 약 11시간). Issue #51 에서 계획한 후속 단계인 백테스트 1회 실행 (`uv run python scripts/backtest.py --loader=kis --from 2025-04-22 --to 2026-04-21`) 이 2026-04-24 10:25 완료됐다.

결과 (`data/backtest_report.md`):

| 항목 | 값 |
|---|---|
| 기간 | 2025-04-22 ~ 2026-04-21 (243 영업일) |
| 종목 수 | 199 |
| 시작 자본 | 1,000,000 KRW |
| 종료 자본 | 499,489 KRW |
| 총수익률 | -50.05% |
| **최대 낙폭 (MDD)** | **-51.36%** |
| 샤프 비율 (연환산) | -6.81 |
| 승률 | 31.35% |
| 평균 손익비 | 1.28 |
| 거래 수 | 1027 (일평균 4.226) |
| 거부 (max_positions_reached) | 14,568 |
| 거부 (below_min_notional) | 5,370 |
| 거부 (halted_daily_loss) | 7 |
| 거부 (daily_entry_cap) | 3 |

**Phase 2 PASS 기준** (ADR-0017): `max_drawdown_pct > -15%`. 실측 `-51.36%` 로 기준 3.4배 초과 미달 — **FAIL**.

트레이드당 기대값: `0.3135 × 1.28 − 0.6865 × 1.0 ≈ −0.28R`. 비용 (슬리피지 0.1% + 수수료 0.015% + 거래세 0.18%) 차감 전 기준으로도 구조적 손실. 백테스트 이상의 실전 슬리피지·체결 지연·VI·상하한가 반영 시 괴리는 더 벌어질 가능성이 크다.

PR #51 당초 계획은 PASS 시 docs-only PR 로 Phase 2 선언, FAIL 시 "ORB 파라미터 · 비용 가정 재검토 (별도 이슈)". 이번 ADR 은 그 "재검토" 범위를 구조화하고, 나아가 **수익률이 확인되기 전 Phase 3 (모의투자) 에 절대 진입하지 않는다는 상위 정책**을 못박는다. 근거는 사용자(`donggyu`) 의 명시적 지시 (2026-04-24): "수익률이 생길때까지 절대로 다음 Phase 로 넘어가면 안될 것 같아". 이는 `README.md` 리스크 고지·`plan.md` 전반의 "모의투자 → 백테스트 → 페이퍼트레이딩 선행" 원칙과 정합한다.

## 결정

1. **Phase 3 진입을 백테스트 기대값 양수 확인 전까지 금지한다**. 구체적 게이트:
   - 단일 구간 `max_drawdown_pct > -15%` (ADR-0017 계승).
   - 승률 × 평균 손익비 > 1.0 (트레이드당 기대값 양수).
   - 연환산 샤프 비율 > 0.
   - 세 조건 **전부** 충족 시에만 Phase 3 착수 재허가 (이 ADR 의 정책 상태를 `대체됨` 으로 전환 + 신규 ADR 에서 새 기준 명시).

2. **Phase 2 복구 5단계 로드맵** (사용자 제안 · 저비용 → 고비용 순):
   - **A. 민감도 그리드 실행** — `scripts/sensitivity.py` 기본 32조합 스윕 (캐시 재사용, 추가 KIS 호출 0). `or_end × stop_loss_pct × take_profit_pct` 축에서 현재 운영 기본값을 개선하는 조합이 있는지 확인.
   - **B. 비용 가정 재검정** — 슬리피지 0.1% 가정 (ADR-0006) 이 KOSPI 200 실제 호가 스프레드와 정합한지 측정. KIS 실전 키로 1주 호가 샘플 수집 → 종목별·시간대별 중앙값 스프레드 산출 → 백테스트 비용 모델 재보정.
   - **C. 유니버스 유동성 필터** — 199 종목 중 거래대금·변동성 하위 제외. `pykrx` 일봉 거래대금 기준 상위 N (예: 50, 100) 서브셋에서 A 단계 재실행.
   - **D. 전략 파라미터 구조 변경** — OR 윈도 (`09:00~09:30` → `09:00~09:15` / `09:00~10:00`), `force_close_at` (15:00 → 14:50 / 15:20), 재진입 허용 여부, 1일 1진입 → 2진입. 각 변경은 ADR 또는 Issue 단위로 관리.
   - **E. 전략 교체** — A~D 전부 수행 후에도 PASS 조건 미달 시 ORB 폐기. 대체 후보 (VWAP mean-reversion, opening gap reversal, pre-market pullback) 평가 → 별도 ADR 로 채택·교체 결정.

3. **단계 게이팅**: A → B → C → D → E 순서. 중간 단계에서 세 게이트(MDD·승률×손익비·샤프) 전부 PASS 하면 **walk-forward 검증 (Phase 5 스켈레톤 `backtest/walk_forward.py`, PR #70) 추가 게이트 통과 후** Phase 3 착수.

4. **부수적 개선 (본 PR 포함)**:
   - `config/holidays.yaml` 에 근로자의날 2 건 (`2025-05-01`, `2026-05-01`) 보강. 1차 백테스트 시 이 날짜 캐시 누락으로 199 심볼 × 4 페이지 KIS 허탕 호출 + `EGW00201` 캐스케이드 발생 → 프로세스 비정상 종료 원인이 됨. 이 패치는 ADR-0018 의 YAML 수동 관리 정책 계승 (신규 결정 아님).

## 결과

**긍정**
- 실전 자본 손실 회피 (현재 파라미터로 연간 -50% 수준).
- 복구 로드맵 A~E 의 가능한 조합을 명시적으로 나열 → 임의 개입·yak-shaving 없이 순차 검증.
- 사용자 정책 (수익 전 Phase 3 금지) 을 ADR 로 성문화 → 향후 세션에서도 동일 규칙 유지.
- 1차 백테스트 실측치 보존 (`data/backtest_report.md` 는 git 제외이나 ADR 본문에 요약 기록).

**부정**
- 전체 일정 지연. Phase 3 실전 검증 지점 후퇴.
- 단계 A~E 가 모두 실패할 경우 (E 에서 ORB 교체) 수 개월 추가 작업 가능.
- 복구 작업이 길어질수록 KIS 분봉 캐시 표본 구간도 뒤로 밀려 (KIS 서버 1년 보관 한도) 재백필 필요.

**중립**
- Phase 2 전체 PASS 선언 시점 불확정. `plan.md` Phase 2 Verification 섹션이 이 정책을 명시적으로 반영함.
- 민감도 그리드 (A) 는 기존 인프라 (`scripts/sensitivity.py`) 그대로 활용. 신규 코드 최소.
- walk-forward 검증 모듈 (`backtest/walk_forward.py`) 은 이미 PR #70 으로 main 에 포함 — 단계 D·E 전환 시 활용 예정.

## 추적

- 코드: `config/holidays.yaml` (근로자의날 2 건 보강), `scripts/sensitivity.py` · `scripts/backtest.py` (기존 인프라, 단계 A 에서 사용), `src/stock_agent/backtest/walk_forward.py` (단계 이후 게이트)
- 문서: [plan.md](../../plan.md) Phase 2 Verification · Phase 2 진행 요약, [CLAUDE.md](../../CLAUDE.md) 현재 상태, [README.md](../../README.md) Phase 요약
- 기준 결정: [ADR-0017](./0017-phase2-pass-1year.md) Phase 2 PASS 기준 기간 1년, [ADR-0018](./0018-holiday-calendar-yaml.md) 공휴일 캘린더 YAML
- 관련 Issue: #51 (1차 백테스트 실행 모이슈), 후속 Issue A~E (본 PR 머지 후 생성 예정)
- 도입 PR: #N (본 PR 머지 시 갱신)
