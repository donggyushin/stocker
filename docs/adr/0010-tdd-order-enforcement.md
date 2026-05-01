---
date: 2026-04-20
status: 승인됨
deciders: donggyu
related: [ADR-0005]
---

# ADR-0010: src-first TDD 순서 강제 — `src/` 신규 파일 생성 시 대응 테스트 선행 요구

## 상태

승인됨 — 2026-04-20. [ADR-0005](./0005-unit-test-writer-enforcement.md) 를 **확장** (폐기 아님) 한다. 0005 는 `tests/` 쓰기 자체를 서브에이전트 경유로 제한했고, 본 ADR 은 `src/stock_agent/` 신규 파일이 **대응 테스트보다 먼저 만들어지지 않도록** 순서를 강제한다.

## 맥락

0005 도입 이후 외부 의존 목킹 규율은 안정됐지만, Claude Code 메인 assistant 의 작업 흐름에는 여전히 다음 공백이 있었다.

1. **src 선구현 유혹**. 메인 assistant 가 `src/stock_agent/새모듈.py` 를 먼저 만들고, 뒤에 테스트를 붙이는 경로가 열려 있었다. Stop 훅 `test-coverage-check.sh` 가 사후 리마인더를 주기는 하지만, 이미 구현된 코드에 "통과용 테스트" 가 붙는 역방향 흐름을 완전히 막지는 못한다.
2. **RED 단계 계약 부재**. `unit-test-writer` 는 "존재하지 않는 모듈의 테스트를 만들어내지 말 것" 을 기본 규칙으로 뒀고, TDD 는 "호출자가 명시할 때만" 허용되는 예외였다. 결과적으로 Red-Green-Refactor 사이클의 Red 단계를 **선제 의무화** 하는 문서·자동화 장치가 없었다.
3. **운영 정책 문서 공백**. `CLAUDE.md` 의 "테스트 작성 정책" 은 "tests 는 서브에이전트 경유" 까지만 규정하고, "src 먼저 쓰지 말 것" 순서 규칙을 담고 있지 않았다.

이 조합은 금융 자동매매 코드 특성상 위험하다 — 미검증 분기가 실거래 주문 경로에 올라갈 가능성을 낮추려면 `src/` 가 생기기 전에 의도된 동작이 테스트로 **먼저 고정**돼야 한다.

검토한 대안:

- **A. Stop 훅 차단 승격** (`test-coverage-check.sh` 의 exit 2 를 무한 반복 차단으로 강화). Claude 턴 종료 자체가 막혀 무한 루프 위험 — 거부.
- **B. Git pre-commit hook**. Claude Code 외부에서도 동작하지만 메인 assistant 의 워크플로 시점(작업 중) 에는 걸리지 않아 "순서 강제" 목표 미달.
- **C. AST 기반 신규 public 심볼 감지**. PreToolUse 시점에 파일이 아직 없어 신뢰도 낮음. 비용 대비 실효성 낮음.
- **D. 새 PreToolUse 훅으로 `Write` 신규 파일 시점에 대응 테스트 존재 확인 + unit-test-writer 기본 모드를 RED 로 전환** (채택).

## 결정

다음 세 요소를 한 PR 에 묶어 도입한다.

1. **새 PreToolUse 훅 `.claude/hooks/src-first-requires-tests.sh`**
   - 매처: `Write|Edit|NotebookEdit` (기존 체인 공유) 이지만 **`Write` 만 실동작** — `Edit`/`NotebookEdit` 은 내부 분기에서 통과시켜 회귀·리팩터·버그 수정 경로를 막지 않는다.
   - 대상: `src/stock_agent/**/*.py` **신규 파일** (`-e` 로 파일 부재 확인). `__init__.py` 는 면제.
   - 서브에이전트 면제: payload `agent_id` 필드 존재 시 통과 (0005 와 동일 식별 규약).
   - 후보 테스트 경로 3 종 중 하나라도 존재하면 통과:
     - `tests/test_<basename>.py`
     - `tests/test_<subpkg>_<basename>.py` (현 flat 관례 — `test_strategy_orb.py`)
     - `tests/<subpkg>/test_<basename>.py` (미러 구조, 미래 확장 대비)
   - 전부 부재 시 exit 2 + 후보 경로·`unit-test-writer` 호출 안내 출력.
   - 우회: `STOCK_AGENT_TDD_BYPASS=1` 환경변수 (세션 한정, stderr 에 우회 사실 기록).
   - fail-closed 정책·`pwd -P` symlink 해소·`PROJECT_ROOT` prefix 체크는 `tests-writer-guard.sh` 패턴 그대로 재사용.

2. **`unit-test-writer` 에이전트 TDD 모드 계약 명문화**
   - `description` 에 "기본 계약은 TDD RED-first" 명시.
   - 본문에 `## TDD 모드 계약` 섹션 추가. 3 모드(RED / regression / refactor-invariant) 의 입력·산출·완료 보고 형태를 정의. RED 모드는 가짜 스텁으로 테스트를 억지로 통과시키는 것을 명시적으로 금지하고, `uv run pytest -x` FAIL 확인 리포트를 완료 보고 필수 항목으로 둠.
   - 산출물 형식 섹션에 현 프로젝트의 3 종 네이밍 관례를 훅과 일관되게 명시.

3. **`CLAUDE.md` 에 "TDD 순서 강제 (하드 규칙)" 섹션 추가**
   - 5 단계 플로우 (요구사항 정리 → unit-test-writer RED → src 구현 → GREEN → 리팩터).
   - 훅 3 종 역할표 (`tests-writer-guard`·`src-first-requires-tests`·`test-coverage-check`).
   - 예외 분류 (훅 스코프 밖 / 명시 우회 필요) 명시.
   - 긴급 핫픽스 우회 시 **24 시간 내 회귀 테스트 작성** 규정.

## 결과

**긍정**

- `src/` 신규 파일이 검증 없이 실거래 경로에 진입할 가능성을 시스템 차원에서 차단. 특히 새 전략 엔진·주문 경로·리스크 게이트 같은 고위험 신규 모듈에 대해 Red 단계가 강제된다.
- unit-test-writer 호출 완료 보고에 RED 실패 리포트가 포함되면서 메인 assistant 가 GREEN 단계로 매끄럽게 이어받는 계약이 생겼다.
- 훅 3 종 역할이 CLAUDE.md 표로 시각화돼 신규 기여자가 하네스 구조를 한눈에 파악 가능.

**부정**

- 메인 assistant 가 새 기능 추가 시 항상 서브에이전트 호출을 선행해야 해 작업 흐름이 1 단계 늘어난다. 작은 유틸리티 파일 추가도 동일.
- 긴급 핫픽스 시 `STOCK_AGENT_TDD_BYPASS=1` 설정을 깜빡하면 훅에 막혀 시간 손실. 완화: 훅 메시지에 우회 방법을 명시 출력.
- 훅이 "테스트 파일 존재" 만 검증하며 내용 품질은 검증하지 않는다. 이는 unit-test-writer 계약(RED 모드 FAIL 확인 보고) 이 흡수해야 하는 규율 영역.

**중립**

- `Edit` 경로는 이 훅이 막지 않는다 — 기존 src 파일에 새 public 함수를 추가하는 경우는 Stop 훅 `test-coverage-check.sh` 의 사후 리마인더로 보완. AST 분석 없이 PreToolUse 시점에 신뢰성 있게 차단하기 어렵다는 명시적 선택.
- 훅 바인딩·우회 메커니즘은 Claude Code 환경에 한정 — 다른 IDE 에서 편집 시 작동하지 않음 (0005 와 동일 제약).

## 추적

- 코드: `.claude/hooks/src-first-requires-tests.sh`, `.claude/settings.json` (PreToolUse 체인)
- 에이전트: `.claude/agents/unit-test-writer.md` (TDD 모드 계약 섹션)
- 문서: [CLAUDE.md](../../CLAUDE.md) "TDD 순서 강제 (하드 규칙)" 섹션
- 관련 ADR: [0005](./0005-unit-test-writer-enforcement.md) (확장 관계, 0005 폐기 아님)
- 도입 PR: #15
