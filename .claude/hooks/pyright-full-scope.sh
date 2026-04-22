#!/usr/bin/env bash
# stock-agent PreToolUse hook — `git push` 직전에 pyright 를 전체 범위
# (`src scripts tests`) 로 강제 검사한다.
#
# 배경: CI 파이프라인의 pyright job 은 `uv run pyright src scripts tests` 로
# 돈다. 로컬에서 관행적으로 `uv run pyright src scripts` 만 돌리면 `tests/`
# 범위에서 드리프트가 누적되어 push 이후 CI 가 82건 에러를 쏟아내는 사고가
# 재발한다. (근거: 이슈 #32 PR #39 — FakeOrderSubmitter.cancel_order 누락).
#
# 정책: `git push` 시도 시 pyright 전체 범위를 실행해서 실패면 exit 2 로 차단.
# 필요하면 `STOCK_AGENT_PYRIGHT_BYPASS=1` 로 긴급 우회 (24 시간 내 회귀 테스트
# + 원인 제거 커밋 필수).
#
# 성능: pyright 전체 범위 ~3-5 초. push 주기가 희소하므로 허용 오버헤드.
#
# 경로 매칭은 symlink 해소 후 PROJECT_ROOT prefix 로 판정해 macOS `/var` ↔
# `/private/var` 표기 차이에 속지 않는다.
#
# hook 스펙: https://code.claude.com/docs/en/hooks.md (PreToolUse)

set -euo pipefail

# 긴급 우회 스위치 — 환경변수로만.
if [ "${STOCK_AGENT_PYRIGHT_BYPASS:-0}" = "1" ]; then
  exit 0
fi

PAYLOAD="$(cat)"

# ---------------------------------------------------------------------------
# 1) payload 파싱 — tool_name 과 command 를 추출.
#    파싱 실패는 fail-open (이 훅은 안전벨트이지 primary gate 가 아니다).
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
# 3) `git push` 패턴인지 확인.
#    - 단순 `git push`, `git push origin branch`, `git push -u ...` 모두 매칭.
#    - `git push --dry-run` 은 통과 (네트워크 미접촉).
# ---------------------------------------------------------------------------

# 앞뒤 공백·시작 ; 등을 완화하기 위해 grep 사용.
if ! printf '%s' "$COMMAND" | grep -Eq '(^|[[:space:];|&])git[[:space:]]+push([[:space:]]|$)'; then
  exit 0
fi

# --dry-run 은 실제 push 아님 — 통과.
if printf '%s' "$COMMAND" | grep -Eq -- '--dry-run'; then
  exit 0
fi

# ---------------------------------------------------------------------------
# 4) PROJECT_ROOT 확보 — stock-agent 저장소 시그니처 기반.
#    디렉터리 이름(`*/stock-agent`) 만으로 gate 하면 claude-squad worktree
#    에서 훅이 비활성화되어 오히려 실수가 터지는 곳에서 발동 안 한다.
#    시그니처 2종 — (1) `.github/workflows/ci.yml` 의 pyright 커맨드 존재,
#    (2) `pyproject.toml` 의 `[tool.pyright]` 섹션 존재 — 으로 판정해
#    worktree 포함 모든 체크아웃을 커버한다.
# ---------------------------------------------------------------------------

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -z "$PROJECT_ROOT" ] && exit 0

PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd -P || echo "$PROJECT_ROOT")"

CI_FILE="$PROJECT_ROOT/.github/workflows/ci.yml"
PYPROJECT="$PROJECT_ROOT/pyproject.toml"
[ -f "$CI_FILE" ] || exit 0
[ -f "$PYPROJECT" ] || exit 0
grep -qE 'uv run pyright[[:space:]]+src[[:space:]]+scripts[[:space:]]+tests' "$CI_FILE" || exit 0
grep -qE '^\[tool\.pyright\]' "$PYPROJECT" || exit 0

# ---------------------------------------------------------------------------
# 5) pyright 실행 — CI 와 정확히 동일 범위.
# ---------------------------------------------------------------------------

cd "$PROJECT_ROOT"

if ! OUTPUT="$(uv run pyright src scripts tests 2>&1)"; then
  {
    echo "[pyright-full-scope] git push 차단 — pyright 가 \`src scripts tests\` 범위에서 에러를 감지."
    echo ""
    echo "CI 파이프라인의 pyright job 은 이 세 디렉터리를 모두 검사합니다."
    echo "로컬에서 src/scripts 만 돌리고 push 하면 tests/ 드리프트가 CI 에서 터져"
    echo "PR 이 빨개집니다. 과거 사고 기록: PR #39 (FakeOrderSubmitter.cancel_order 누락)."
    echo ""
    echo "pyright 출력 (마지막 30 줄):"
    echo "----------------------------------------"
    printf '%s\n' "$OUTPUT" | tail -n 30
    echo "----------------------------------------"
    echo ""
    echo "조치:"
    echo "  1) 에러가 프로젝트 src 라면 직접 수정."
    echo "  2) 에러가 tests/ 라면 unit-test-writer 서브에이전트 경유 수정."
    echo "  3) pyright 재실행: uv run pyright src scripts tests"
    echo "  4) green 이면 다시 git push 시도."
    echo ""
    echo "긴급 우회 (모의투자 운영 중 핫픽스 등): STOCK_AGENT_PYRIGHT_BYPASS=1 git push ..."
    echo "우회 후 24 시간 내 회귀 테스트 + 원인 제거 커밋 필수."
  } >&2
  exit 2
fi

exit 0
