#!/usr/bin/env bash
# stock-agent PreToolUse hook — tests/ 쓰기 가드.
#
# 메인 assistant 가 `Write` / `Edit` / `NotebookEdit` 으로 tests/ 아래 .py
# 파일을 직접 수정하려 하면 exit 2 로 차단한다. 서브에이전트(특히
# unit-test-writer) 의 호출은 stdin JSON 의 `agent_id` 필드 존재 여부로
# 식별해 그대로 통과시킨다.
#
# 근거: CLAUDE.md "테스트 작성 정책" — tests/ 수정은 unit-test-writer 경유.
# hook 스펙: https://code.claude.com/docs/en/hooks.md (PreToolUse)

set -euo pipefail

PAYLOAD="$(cat)"

# agent_id 가 있으면 서브에이전트 호출. 그대로 통과.
AGENT_ID="$(
  printf '%s' "$PAYLOAD" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
    print(d.get("agent_id") or "")
except Exception:
    print("")
' 2>/dev/null || echo ""
)"

if [ -n "$AGENT_ID" ]; then
  exit 0
fi

TOOL_NAME="$(
  printf '%s' "$PAYLOAD" | python3 -c '
import json, sys
try:
    print(json.loads(sys.stdin.read()).get("tool_name", ""))
except Exception:
    print("")
' 2>/dev/null || echo ""
)"

case "$TOOL_NAME" in
  Write|Edit|NotebookEdit) : ;;
  *) exit 0 ;;
esac

FILE_PATH="$(
  printf '%s' "$PAYLOAD" | python3 -c '
import json, sys
try:
    ti = json.loads(sys.stdin.read()).get("tool_input", {}) or {}
    print(ti.get("file_path", "") or ti.get("notebook_path", ""))
except Exception:
    print("")
' 2>/dev/null || echo ""
)"

[ -z "$FILE_PATH" ] && exit 0

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -z "$PROJECT_ROOT" ] && exit 0

# stock-agent 밖이면 간섭하지 않는다.
case "$PROJECT_ROOT" in
  */stock-agent) : ;;
  *) exit 0 ;;
esac

# 상대경로로 정규화.
case "$FILE_PATH" in
  "$PROJECT_ROOT"/*) REL="${FILE_PATH#$PROJECT_ROOT/}" ;;
  /*) exit 0 ;;  # stock-agent 외부 절대경로는 간섭 대상 아님
  *) REL="$FILE_PATH" ;;
esac

# tests/*.py 또는 tests/**/*.py 만 대상.
case "$REL" in
  tests/*.py|tests/**/*.py) : ;;
  *) exit 0 ;;
esac

{
  echo "[tests-writer-guard] 메인 assistant 의 \`tests/\` 직접 쓰기 차단됨."
  echo ""
  echo "대상 도구: ${TOOL_NAME}"
  echo "대상 경로: ${REL}"
  echo ""
  echo "CLAUDE.md \"테스트 작성 정책\" 에 따라 \`tests/\` 하위 Python 파일의 생성·수정은"
  echo "반드시 \`unit-test-writer\` 서브에이전트를 경유해야 합니다."
  echo ""
  echo "조치: Agent 툴로 subagent_type=\"unit-test-writer\" 를 호출해 이 파일의 작성·보강을"
  echo "      위임하세요. 목적·대상 파일·검증 요건을 프롬프트에 명시할 것."
  echo ""
  echo "예외 (드묾): 임포트 경로·네이밍 단순 리팩터처럼 테스트 로직이 바뀌지 않는 경우에만"
  echo "            사용자에게 명시적으로 확인을 받고 hook 을 일시적으로 우회하세요."
} >&2

exit 2
