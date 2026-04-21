---
description: 현재 변경사항을 커밋하고 원격에 푸시한 뒤 PR을 생성하고 pr-review-toolkit:review-pr 로 자동 코드 리뷰까지 한 흐름에 끝낸다.
---

# Auto Commit + PR + Review

현재 작업 브랜치의 미커밋 변경사항을 커밋하고, main 대비 PR 을 만들고, **생성 직후 자동으로 코드 리뷰까지** 한 흐름에 끝낸다.

## 인자

- 인자 없음: 현재 브랜치 변경사항 자동 커밋 → 현재 브랜치 → `main` 으로 PR 생성
- `base..head` 형식 (예: `main..feature`): 명시된 브랜치쌍으로 PR 생성 (커밋 단계는 현재 브랜치 기준)

## 실행 흐름

### 1. 사전 점검 (병렬 실행)

한 메시지에서 다음을 병렬로:

- `git status` — 현재 변경 상태
- `git diff` — 스테이지되지 않은 변경
- `git diff --cached` — 이미 스테이지된 변경
- `git branch -vv` — 현재 브랜치·원격 추적 여부
- `gh auth status` — gh CLI 인증 상태
- `git log <base>..<head> --oneline` — 기존에 이미 브랜치에 포함된 커밋 목록
- `git log --oneline -10` — 기존 커밋 메시지 스타일·언어 파악
- `git diff <base>...<head> --stat` — 변경 규모 요약

변경이 전혀 없고(워킹 트리 clean) 브랜치도 원격과 동기화 완료면 "PR 만들 변경 없음" 으로 중단. 빈 커밋·빈 PR 은 만들지 않는다.

### 2. 커밋 초안 작성 (변경이 있을 때만)

- 전체 diff 를 훑어 **무엇을 왜 바꿨는지** 1~2문장으로 요약. 파일별 단순 나열은 지양.
- 언어·스타일은 `git log --oneline -10` 결과에 맞춘다. 기존이 한국어면 한국어, 영어면 영어. 로그가 비어 있으면 사용자와의 대화 언어를 따른다.
- 명령형/단정형, 현재시제.
- 접두어(`feat:`/`fix:`/`docs:`/`refactor:`/`chore:`/`test:` 등)는 기존 로그가 사용 중이거나 의미가 명확할 때만.
- 제목 한 줄이 원칙. 본문이 필요하면 빈 줄 후 이어 쓴다.
- 커밋 메시지 말미에 항상 다음을 포함:

  ```
  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  ```

### 3. 스테이지 대상 선별 + 민감정보 1차 점검

- **민감정보 파일 차단**: 경로·파일명에 `.env`, `secret`, `_key`, `credentials`, `token` 같은 키워드가 포함되면 **스테이지 중단하고 사용자에게 경고**. 확인 없이 추가 금지.
- **`git add -A` / `git add .` 금지.** 파일명을 개별 명시.
- `.gitignore` 대상이 우회 포함되지 않았는지 `git status` 재점검.
- 스테이지 전에 `git diff` 결과를 `KIS_APP_KEY` / `secret` / `token` / 36자 이상 연속 영숫자 패턴으로 한 번 grep 해 평문 민감값 유출 여부 점검.

### 4. PR 본문 초안 작성

- **제목**: 한국어 단정형, 접두어 없음(프로젝트 commit 스타일과 일치). 기존 `git log` 의 톤을 따른다.
- **본문 섹션** (한국어):
  - `## 요약` — 1~3줄
  - `## 변경 내역` — 핵심 변경을 글머리표로
  - `## 변경 파일` — 신규/수정 분리
  - `## 검증` — 실제 실행한 검증 체크리스트(`uv run pytest`, `healthcheck`, 스모크 등 — 실행하지 않은 것을 체크하지 않는다)
  - `## 비고` — 보안·민감정보·운영 메모, 의도적 누락 사항

### 5. 사용자 일괄 승인 (단일 승인 지점)

다음을 한 메시지로 제시하고 **명시적 승인 받은 뒤** 다음 단계로 진행:

- 커밋 메시지 초안 (제목 + 본문)
- 스테이지 대상 파일 목록
- PR 대상 브랜치 쌍 (`<base>..<head>`)
- PR 제목·본문 초안
- PR 생성 모드: **draft** (항상 draft 로 생성됨을 명시)

승인 전에는 `git commit`, `git push`, `gh pr create` 중 **무엇도 실행하지 않는다**. 커밋이 로컬 액션이더라도 PR 과 흐름이 묶여 있어 한 번에 확인을 받는 게 원칙 — 사용자가 어느 단계든 거부할 수 있어야 한다.

### 6. 커밋 생성

- 파일 개별 스테이지 후 HEREDOC 으로 실행:

  ```bash
  git commit -m "$(cat <<'EOF'
  <제목>

  <선택적 본문>

  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  EOF
  )"
  ```

- pre-commit 훅이 실패하면: 훅이 수정한 파일을 다시 스테이지하고 **새 커밋**을 만든다. **`--amend` 금지** (이전 커밋을 덮어쓰면 작업 유실 위험).
- 커밋 성공 후 `git status` 로 워킹 트리 clean 여부 확인.

### 7. 브랜치 푸시

원격 추적이 없으면 `git push -u origin <head>`. 이미 추적 중이면 `git push`. **force push 금지**.

### 8. 민감정보 최종 점검 + PR 생성 (draft)

- `gh pr create` 직전에 `git diff <base>..<head>` 결과를 다시 grep 으로 점검(`KIS_APP_KEY` / `secret` / `token` 패턴). 의심 패턴 발견 시 중단하고 사용자에게 알림.
- HEREDOC 으로 `gh pr create --draft --base <base> --head <head> --title ... --body ...` 실행. **항상 `--draft` 로 생성한다.** ready-for-review 전환은 사용자가 자동 리뷰 결과를 확인한 뒤 명시적으로 요청할 때만 수행한다(`gh pr ready <PR>`).
- 결과 URL 을 사용자에게 즉시 보고 (draft 상태임을 명시).

### 9. 자동 코드 리뷰 (이 커맨드의 핵심 추가 동작)

PR 생성이 성공하면 **별도 사용자 확인 없이 곧바로** `pr-review-toolkit:review-pr` 스킬을 호출해 종합 리뷰를 수행한다. 기본 동작:

- 변경 파일 종류에 따라 적용 가능한 리뷰 에이전트만 자동 선별
  - 코드 변경 → `code-reviewer`
  - try/except 또는 에러 처리 변경 → `silent-failure-hunter`
  - 새 docstring/주석 ≥10줄 → `comment-analyzer`
  - 새 클래스/타입 추가 → `type-design-analyzer`
  - 테스트 파일 변경 → `pr-test-analyzer`
- 적용 에이전트가 2개 이상이면 **병렬 실행** (한 메시지에서 다중 Agent 호출).
- 각 에이전트 프롬프트에는 다음 컨텍스트를 함께 전달:
  - PR URL 과 base..head
  - 검토 대상 절대경로 목록
  - 프로젝트 가드: `/Users/sindong-gyu/Documents/stock-agent/CLAUDE.md` (반드시 읽고 가드레일에 비추어 검토할 것)
  - 한국어 보고

### 10. 리뷰 결과 종합

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

- **민감정보 파일 자동 스테이지 금지.** 경로·파일명 패턴(`.env` / `secret` / `_key` / `credentials` / `token`) 일치 시 중단하고 사용자에게 알림. `git diff --cached` 에 평문 민감값 유출 여부도 스테이지 전·PR 생성 전 두 번 점검.
- **`git add -A` / `git add .` 금지.** 파일명을 개별 명시.
- **`--amend` 금지.** pre-commit 훅 실패를 포함해 어떤 경우에도 이전 커밋을 덮어쓰지 않고 **새 커밋**을 만든다.
- **`--no-verify` / `-c commit.gpgsign=false` 같은 훅·서명 우회 금지.**
- **force push / `git push -f` 금지.**
- **커밋·푸시·PR 은 5단계 일괄 승인 후에만 실행.** 승인 없이 자동 진행 금지.
- **PR 은 항상 `--draft` 로 생성.** ready-for-review 전환(`gh pr ready`)은 사용자 명시 요청 후에만 수행.
- **리뷰 단계는 자동 진행** (이게 본 커맨드의 추가 동작). 단, 리뷰 결과에 따른 코드 수정이나 PR 본문 수정은 사용자 명시 요청 후에만.
- **새 명령·라이브러리·플래그를 임의로 만들지 말 것.** PR 본문과 커밋 메시지의 검증 항목은 실제로 실행한 것만 체크.
- **변경이 없으면 빈 커밋·빈 PR 을 만들지 말고 종료.**

## 출력 형식

- 한국어, 담담·단정형
- 이모지 사용 금지
- 중간 진행 안내는 짧게 (≤25 단어)
- 5단계 일괄 승인 요청은 스캔 가능하게 표·목록 위주
- 마지막 종합 리뷰도 표·목록 위주
