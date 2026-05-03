---
date: 2026-05-03
status: 승인됨
deciders: donggyu
related: []
---

# ADR-0026: 포매터 단일화 — ruff format 채택, black 폐기

## 상태

승인됨 — 2026-05-03. Phase 3 PR2 작업 중 발견된 포매터 도구 충돌의 해소.

## 맥락

### 충돌 발견 경위

Phase 0 도구 도입(2026-04-19) 당시 "ruff + black" 를 포매터·린터 표준으로 채택하고, root `CLAUDE.md` "코드 스타일" 섹션에 명시했다. `.pre-commit-config.yaml` 에는 `ruff-pre-commit` (ruff + ruff-format 훅) 와 `psf/black` (black 훅) 가 모두 등록됐고, `.github/workflows/ci.yml` 에도 `Format check (ruff format)` 와 `Format check (black)` 두 step 이 게이팅에 포함됐다.

2026-05-03 Phase 3 PR2 commit 시 두 포매터 간 비호환이 확인됐다.

### 비호환 내용

ruff-format (0.15.11) 와 black (26.3.1) 은 assert 문 wrap 처리에서 서로 다른 출력을 생성한다.

- **ruff-format**: `assert isinstance(x, Y), (msg)` — 메시지를 괄호로 감싼다.
- **black**: `assert isinstance(\n    x, Y\n), msg` — 조건 부분을 괄호로 감싸고 줄 분리한다.

두 포매터가 서로의 출력을 되돌리므로 멱등 도달이 불가능하다. pre-commit hook 에서 ruff-format 이 reformat 한 결과를 black 이 또 reformat 하고, 그 결과를 ruff-format 이 다시 되돌리는 핑퐁이 발생한다.

### 게이팅 이중 충돌

CI `.github/workflows/ci.yml` 와 `.claude/hooks/ci-lint-full-scope.sh` 모두 두 포매터를 동시에 게이팅했다. ruff-format 을 통과하면 black 이 fail 하고, black 을 통과하면 ruff-format 이 fail 하는 구조라 어느 포매터 스타일을 따르더라도 CI 가 항상 실패한다.

### 선택지

1. **ruff format 단일 채택, black 폐기**: ruff-format 은 black "drop-in replacement" 를 표방한다. ruff (lint) 와 ruff-format (포매팅) 이 동일 도구로 통합되어 버전 정합과 실행 속도 면에서 우위가 있다.
2. **black 단일 채택, ruff-format 제거**: ruff lint 를 유지하면서 ruff-format 만 제거하는 것은 가능하나, ruff 가 lint 도구로 이미 정착한 상황에서 같은 계열 포매터를 제거하는 것은 비자연스럽다. 또한 ruff-format 이 없어도 ruff lint 는 독립 작동하므로 이론상 가능하지만 단일 도구 통합의 이점을 포기한다.
3. **두 포매터 설정 조정으로 충돌 해소**: assert 문 처리 옵션을 맞추는 방식. 두 도구의 설정 호환성이 제한적이고, 향후 버전 업에서 새로운 비호환이 재발할 위험이 있다.

## 결정

**ruff format 단일 채택. black 폐기.**

선택 근거:

- ruff-format 은 black "drop-in replacement" 를 공식 표방하므로 포매팅 일관성이 유지된다.
- ruff (lint) 와 ruff-format (포매팅) 이 동일 도구로 통합되어 있어 버전 정합·실행 속도·설정 단일화 면에서 우위다.
- 코드베이스 포매팅 정책을 단일 도구로 집약함으로써 유사 충돌의 재발 가능성을 구조적으로 제거한다.
- 향후 ruff-format 결과가 기존 black 결과와 다른 부분이 코드베이스에 발생하면 ruff-format 결과를 정본으로 채택한다.

변경 영역:

1. `.pre-commit-config.yaml` — `psf/black` repo + black hook 제거. ruff-pre-commit 의 ruff + ruff-format 훅만 유지.
2. `.github/workflows/ci.yml` — `Format check (black)` step 제거.
3. `.claude/hooks/ci-lint-full-scope.sh` — black 검사 제거. CI 3종 lint → CI 2종 lint (`uv run ruff check` + `uv run ruff format --check`). 본문 docstring·에러 메시지·run_check 호출 동기화.
4. `pyproject.toml` — `[tool.black]` 섹션 제거 + `dev` dependency-group 의 `"black>=24.8"` 제거.
5. 전체 코드베이스 `uv run ruff format src scripts tests` 로 1회 재포맷, 멱등 도달 확인.

## 결과

- pre-commit hook 과 CI 포매팅 게이트가 ruff-format 단일 도구로 통일된다.
- `.claude/hooks/ci-lint-full-scope.sh` 의 표기가 "CI 2종 lint" 로 갱신된다.
- root `CLAUDE.md` "코드 스타일" 섹션의 `` `ruff` + `black` `` 표기가 `` `ruff` (lint + format) `` 으로 정정된다. "정적 검사 4종" 예시의 `black --check` 가 `ruff format --check` 로 교체된다.
- 운영자 로컬 워크플로 변경 없음 — `uv run ruff format <path>` 가 `uv run black <path>` 를 대체한다.
- 리스크 고지·수익 보장 표현과 무관한 도구 정책 변경이다.

## 추적

- 도입 PR: Phase 3 PR2 (2026-05-03, `feature_phase3` 브랜치).
- 변경 파일: `.pre-commit-config.yaml`, `.github/workflows/ci.yml`, `.claude/hooks/ci-lint-full-scope.sh`, `pyproject.toml`, `CLAUDE.md`.
