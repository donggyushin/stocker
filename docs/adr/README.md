# Architecture Decision Records (ADR)

stock-agent 의 아키텍처 수준 결정을 결정 단위로 기록한다. 형식은 [MADR](https://adr.github.io/madr/) 변형(한국어, 4섹션: 상태·맥락·결정·결과).

## 작성 규칙

새 ADR 가 필요한 시점:
- 라이브러리 채택·교체·폐기 (예: `backtesting.py` 폐기)
- 모듈 경계·계층 변경
- 핵심 정책 도입·번복 (예: 키 분리 정책, 예외 전파 기조)
- 외부 의존성에 대한 운영 정책 (예: 수동 vs 자동 데이터 소스 선택)

ADR 가 **불필요** 한 변경:
- 단순 버그 수정 (`fix:`)
- 리팩터링·이름 변경
- 테스트 케이스 추가
- 기존 ADR 의 결정을 그대로 따르는 코드 변경

## 작성 절차

1. 다음 채번을 확인 (가장 큰 번호 + 1)
2. [`template.md`](./template.md) 복사 → `NNNN-제목-kebab-case.md`
3. 4섹션 채우기 (상태·맥락·결정·결과)
4. 본 README 의 인덱스 표에 1줄 추가
5. 동일 PR 에 코드 변경과 함께 포함

기존 ADR 가 번복되면 새 ADR 작성 + 기존 ADR 의 상태를 `폐기됨` 또는 `대체됨`(`Superseded by ADR-MMMM`) 으로 변경.

## 인덱스

| ADR | 제목 | 상태 | 날짜 |
|---|---|---|---|
| [0001](./0001-backtesting-py-deprecation.md) | `backtesting.py` 폐기, 자체 시뮬레이션 루프 채택 | 승인됨 | 2026-04-20 |
| [0002](./0002-paper-live-key-separation.md) | KIS paper/live 키 분리 (시세 전용 실전 키 3종) | 승인됨 | 2026-04-19 |
| [0003](./0003-runtime-error-propagation.md) | 사용자 입력 오류는 `RuntimeError` 전파 | 승인됨 | 2026-04-19 |
| [0004](./0004-kospi200-manual-yaml.md) | KOSPI 200 유니버스 수동 YAML 관리 | 승인됨 | 2026-04-19 |
| [0005](./0005-unit-test-writer-enforcement.md) | `tests/` 작성·수정은 `unit-test-writer` 강제 | 승인됨 | 2026-04-19 |
| [0006](./0006-cost-model-rates.md) | 백테스트 비용 모델 수치 (슬리피지/수수료/거래세) | 승인됨 | 2026-04-20 |
| [0007](./0007-phantom-long-handling.md) | phantom_long 처리 (RiskManager 거부 시 strategy 가짜 LONG 흡수) | 승인됨 | 2026-04-20 |
| [0008](./0008-single-process-only.md) | 단일 프로세스 전용 (멀티스레드/프로세스 safe 미제공) | 승인됨 | 2026-04-19 |
| [0009](./0009-python-312-upgrade.md) | 베이스라인 인터프리터 Python 3.11 → 3.12 업그레이드 | 승인됨 | 2026-04-20 |
| [0010](./0010-tdd-order-enforcement.md) | src-first TDD 순서 강제 (신규 `src/` 파일에 대한 PreToolUse 게이트) | 승인됨 | 2026-04-20 |
| [0011](./0011-apscheduler-adoption-single-process.md) | APScheduler 채택 및 BlockingScheduler + 공휴일 수동 판정 정책 | 승인됨 | 2026-04-21 |
| [0012](./0012-monitor-notifier-design.md) | monitor/notifier 모듈 설계 — Protocol 분리·StepReport 이벤트 확장·silent fail 정책 | 승인됨 | 2026-04-21 |
| [0013](./0013-sqlite-trading-ledger.md) | storage/db.py 모듈 설계 — SQLite 원장·Protocol 분리·silent fail 정책·DB 파일 분리 | 승인됨 | 2026-04-22 |
| [0014](./0014-runtime-state-recovery.md) | 세션 중간 재기동 시 오픈 포지션·RiskManager 상태 DB 복원 경로 | 승인됨 | 2026-04-22 |
| [0015](./0015-partial-fill-policy.md) | 체결조회 API 통합 + 부분체결 정책 — 잔량 취소 + 체결 수량만 원장 기록 | 승인됨 | 2026-04-22 |
| [0016](./0016-kis-minute-bar-cache.md) | KIS 과거 분봉 어댑터 — `data/minute_bars.db` 별도 파일 + `kis.fetch()` 로우레벨 호출 | 승인됨 | 2026-04-22 |
| [0017](./0017-phase2-pass-1year.md) | Phase 2 PASS 판정 기간 완화 — 2~3 년 → 1 년 (MDD > -15% 유지) | 승인됨 | 2026-04-22 |
| [0018](./0018-holiday-calendar-yaml.md) | KisMinuteBarLoader 공휴일 캘린더 — `config/holidays.yaml` YAML 수동 관리 | 승인됨 | 2026-04-23 |
| [0019](./0019-phase2-backtest-fail-remediation.md) | Phase 2 백테스트 1차 FAIL + 수익률 확보 전 Phase 3 금지 정책 + 복구 5단계 로드맵 | 승인됨 | 2026-04-24 |
| [0020](./0020-sensitivity-parallel-execution.md) | Sensitivity 그리드 ProcessPool 병렬 실행 경로 도입 (`run_sensitivity_parallel` + `--workers`) | 승인됨 | 2026-04-24 |
| [0021](./0021-step-e-vwap-gap-failed.md) | Step E VWAP-MR · Gap-Reversal 두 후보 폐기 + Step F 가설 풀 확장으로 전환 | 승인됨 | 2026-05-01 |
| [0022](./0022-step-f-gate-redefinition.md) | Step F 게이트 재정의 — 일중 가정 폐기, 일/월 단위 + DCA baseline 상대 비교 | 승인됨 | 2026-05-01 |

## 관련 문서

- [docs/architecture.md](../architecture.md) — 기술 아키텍처 한눈 조망
- [plan.md](../../plan.md) — 비즈니스 결정·전략 규칙·PASS 기준
- [CLAUDE.md](../../CLAUDE.md) — Claude 작업 지침·Phase 상태·동기화 정책
