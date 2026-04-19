---
description: 현재 브랜치를 원격에 푸시하고 PR을 생성한 뒤, pr-review-toolkit:review-pr 로 자동 코드 리뷰까지 실행한다.
---

# Auto PR + Review

현재 작업 브랜치를 main 대비 비교해 PR 을 만들고, **생성 직후 자동으로 코드 리뷰까지** 한 흐름에 끝낸다.

## 인자

- 인자 없음: 현재 브랜치 → `main` 으로 PR 생성
- `base..head` 형식 (예: `main..feature`): 명시된 브랜치쌍으로 PR 생성

## 실행 흐름

### 1. 사전 점검 (병렬 실행)

한 메시지에서 다음을 병렬로:

- `git status` — 미커밋 변경 있으면 사용자에게 경고 후 중단(자동 commit·push 금지)
- `git branch -vv` — 현재 브랜치·원격 추적 여부 확인
- `gh auth status` — gh CLI 인증 상태
- `git log <base>..<head> --oneline` — 포함될 커밋 목록
- `git diff <base>...<head> --stat` — 변경 파일·규모

### 2. 브랜치 푸시

원격 추적이 없으면 `git push -u origin <head>`. 이미 추적 중이면 `git push`. force push 금지.

### 3. PR 본문 초안 작성

- **제목**: 한국어 단정형, 접두어 없음(프로젝트 commit 스타일과 일치). 기존 `git log` 의 톤을 따른다.
- **본문 섹션** (한국어):
  - `## 요약` — 1~3줄
  - `## 변경 내역` — 핵심 변경을 글머리표로
  - `## 변경 파일` — 신규/수정 분리
  - `## 검증` — 실제 실행한 검증 체크리스트(uv run pytest, healthcheck 등 — 실행하지 않은 것을 체크하지 않는다)
  - `## 비고` — 보안·민감정보·운영 메모, 의도적 누락 사항
- HEREDOC 으로 `gh pr create --base <base> --head <head> --title ... --body ...`

### 4. 사용자 확인

PR 생성 전 제목/본문/대상 브랜치를 사용자에게 보여주고 **명시적 승인 받은 뒤** 생성. PR 은 외부 가시 액션이므로 자동 진행 금지.

### 5. PR 생성

`gh pr create ...` 실행. 결과 URL 을 사용자에게 즉시 보고.

### 6. 자동 코드 리뷰 (이 커맨드의 핵심 추가 동작)

PR 생성이 성공하면 **별도 사용자 확인 없이 곧바로** `pr-review-toolkit:review-pr` 스킬을 호출해 종합 리뷰를 수행한다. 기본 동작:

- 변경 파일 종류에 따라 적용 가능한 리뷰 에이전트만 자동 선별
  - 코드 변경 → `code-reviewer`
  - try/except 또는 에러 처리 변경 → `silent-failure-hunter`
  - 새 docstring/주석 ≥10줄 → `comment-analyzer`
  - 새 클래스/타입 추가 → `type-design-analyzer`
  - 테스트 파일 변경 → `pr-test-analyzer`
- 적용 에이전트가 2개 이상이면 **병렬 실행**(한 메시지에서 다중 Agent 호출)
- 각 에이전트 프롬프트에는 다음 컨텍스트를 함께 전달:
  - PR URL 과 base..head
  - 검토 대상 절대경로 목록
  - 프로젝트 가드: `/Users/sindong-gyu/Documents/stock-agent/CLAUDE.md` (반드시 읽고 가드레일에 비추어 검토할 것)
  - 한국어 보고

### 7. 리뷰 결과 종합

에이전트 보고들을 받아 다음 형식으로 사용자에게 한 메시지로 정리:

```text
# PR #N 종합 리뷰 결과

## Critical (머지 전 수정 권장)
| # | 출처 | 위치 | 요지 |
...

## Important
...

## Suggestion
...

## Strengths
...

## 권장 행동 순서
1. ...
```

각 항목은 `file:line` 절대경로 또는 상대경로 명시.

## 가드 (절대 규칙)

- **사용자 미커밋 변경 자동 commit 금지.** 발견 시 사용자에게 알리고 중단.
- **force push / `--no-verify` / `git push -f` 금지.**
- **PR 생성 자체는 사용자 명시 승인 후에만.** 제목/본문 보여주고 한 번 묻기.
- **리뷰 단계는 자동 진행** (이게 본 커맨드의 추가 동작). 단, 리뷰 결과에 따른 코드 수정이나 PR 본문 수정은 사용자 명시 요청 후에만.
- **민감정보 누출 점검**: `gh pr create` 직전에 `git diff <base>..<head>` 결과를 한 번 grep 으로 점검 (`KIS_APP_KEY`, `secret`, `token`, 패턴 매칭). 의심 패턴 발견 시 중단.
- **새 명령·라이브러리·플래그를 임의로 만들지 말 것.** PR 본문의 검증 항목은 실제로 실행한 것만 체크.

## 출력 형식

- 한국어, 담담·단정형
- 이모지 사용 금지
- 중간 진행 안내는 짧게 (≤25 단어)
- 마지막 종합 리뷰는 표·목록 위주로 스캔 가능하게
