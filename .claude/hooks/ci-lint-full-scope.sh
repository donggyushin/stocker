#!/usr/bin/env bash
# stock-agent PreToolUse hook — `git push` 직전에 CI 파이프라인의 2 종 lint
# (`ruff check` · `ruff format --check`) 를 전체 범위
# (`src scripts tests`) 로 강제 검사한다.
#
# 배경: .github/workflows/ci.yml 의 "Lint, format, test" job 은 3 종 검사를
# 모두 `src scripts tests` 범위로 돈다 (ruff check / ruff format --check /
# pyright). `pyright-full-scope.sh` 훅이 pyright 는 커버하지만 나머지 2 종
# 은 구멍이다. 로컬에서 좁은 경로만 돌리거나 ruff 재포맷 후 ruff 재체크를
# 빠뜨리면 push 이후 CI 에서 빨간색이 뜬다 (실사례: Issue #40 PR #43 —
# UP037 타입 어노테이션 따옴표 누락이 CI 에서 먼저 잡혔다).
#
# black 폐기 (ADR-0026, 2026-05-03): ruff format 단일 채택. 본 hook 의 3 종
# → 2 종 축소.
#
# 정책: `git push` 시도 시 2 종 검사를 순차로 실행해 첫 실패에서 exit 2
# 차단. 긴급 우회는 `STOCK_AGENT_LINT_BYPASS=1 git push ...` (24 시간 내
# 회귀 테스트 + 원인 제거 커밋 필수).
#
# 성능: 2 종 합계 ~1-2 초. push 주기가 희소하므로 허용 오버헤드.
#
# 경로 매칭은 symlink 해소 후 PROJECT_ROOT prefix 로 판정.
# hook 스펙: https://code.claude.com/docs/en/hooks.md (PreToolUse)

set -uo pipefail

# 긴급 우회 스위치.
if [ "${STOCK_AGENT_LINT_BYPASS:-0}" = "1" ]; then
  exit 0
fi

PAYLOAD="$(cat)"

# ---------------------------------------------------------------------------
# 1) payload 파싱 — tool_name + command.
# ---------------------------------------------------------------------------

FIELDS="$(
  printf '%s' "$PAYLOAD" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
if not isinstance(d, dict):
    sys.exit(0)
ti = d.get("tool_input") or {}
if not isinstance(ti, dict):
    ti = {}
print(d.get("tool_name") or "")
print(ti.get("command") or "")
' 2>/dev/null || true
)"

TOOL_NAME=""
COMMAND=""
{
  IFS= read -r TOOL_NAME || true
  IFS= read -r COMMAND || true
} <<< "$FIELDS"

# ---------------------------------------------------------------------------
# 2) Bash 가 아니면 통과.
# ---------------------------------------------------------------------------

[ "$TOOL_NAME" = "Bash" ] || exit 0
[ -n "$COMMAND" ] || exit 0

# ---------------------------------------------------------------------------
# 3) `git push` 패턴 매칭 (--dry-run 은 통과).
# ---------------------------------------------------------------------------

if ! printf '%s' "$COMMAND" | grep -Eq '(^|[[:space:];|&])git[[:space:]]+push([[:space:]]|$)'; then
  exit 0
fi

if printf '%s' "$COMMAND" | grep -Eq -- '--dry-run'; then
  exit 0
fi

# ---------------------------------------------------------------------------
# 4) PROJECT_ROOT 판정 — stock-agent 저장소 시그니처 기반.
#    디렉터리 이름(`*/stock-agent`) 만으로 gate 하면 claude-squad worktree
#    (`.claude-squad/worktrees/<branch>/<hash>`) 에서 훅이 비활성화되어
#    오히려 실수가 터지는 곳에서 발동 안 한다. 시그니처 2종 — (1)
#    `.github/workflows/ci.yml` 의 ruff 커맨드 존재, (2) `pyproject.toml`
#    의 `[tool.ruff]` 섹션 존재 — 으로 판정해 worktree 포함 모든 체크아웃
#    을 커버한다.
# ---------------------------------------------------------------------------

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -z "$PROJECT_ROOT" ] && exit 0

PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd -P || echo "$PROJECT_ROOT")"

CI_FILE="$PROJECT_ROOT/.github/workflows/ci.yml"
PYPROJECT="$PROJECT_ROOT/pyproject.toml"
[ -f "$CI_FILE" ] || exit 0
[ -f "$PYPROJECT" ] || exit 0
grep -qE 'uv run ruff check[[:space:]]+src[[:space:]]+scripts[[:space:]]+tests' "$CI_FILE" || exit 0
grep -qE '^\[tool\.ruff\]' "$PYPROJECT" || exit 0

cd "$PROJECT_ROOT"

# ---------------------------------------------------------------------------
# 5) 2 종 lint 순차 실행 — 첫 실패에서 차단.
#    CI 와 동일: src scripts tests 범위. (black 폐기 — ADR-0026)
# ---------------------------------------------------------------------------

run_check() {
  local label="$1"
  shift
  if ! OUTPUT="$("$@" 2>&1)"; then
    {
      echo "[ci-lint-full-scope] git push 차단 — ${label} 실패 (\`src scripts tests\` 범위)."
      echo ""
      echo "CI 파이프라인(.github/workflows/ci.yml) 의 \"Lint, format, test\" job 은"
      echo "ruff check / ruff format --check / pyright 를 모두"
      echo "이 범위로 돌립니다. 로컬에서 좁은 경로만 체크하거나 ruff format 재실행"
      echo "을 빠뜨리면 CI 에서 터집니다."
      echo "(실사례: PR #43 — UP037 따옴표 누락)."
      echo ""
      echo "${label} 출력 (마지막 30 줄):"
      echo "----------------------------------------"
      printf '%s\n' "$OUTPUT" | tail -n 30
      echo "----------------------------------------"
      echo ""
      echo "조치:"
      echo "  1) 출력을 보고 수정 (대부분 ruff --fix 또는 black 재적용으로 해결)."
      echo "  2) 로컬 재검사:"
      echo "       uv run ruff check src scripts tests"
      echo "       uv run ruff format --check src scripts tests"
      echo "  3) green 이면 다시 git push 시도."
      echo ""
      echo "긴급 우회: STOCK_AGENT_LINT_BYPASS=1 git push ..."
      echo "우회 후 24 시간 내 회귀 테스트 + 원인 제거 커밋 필수."
    } >&2
    exit 2
  fi
}

run_check "ruff check" uv run ruff check src scripts tests
run_check "ruff format --check" uv run ruff format --check src scripts tests

exit 0
