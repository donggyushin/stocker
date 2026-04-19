#!/usr/bin/env bash
# stock-agent PreToolUse hook — tests/ 쓰기 가드.
#
# 메인 assistant 가 `Write` / `Edit` / `NotebookEdit` 으로 tests/ 아래 .py
# 파일을 직접 수정하려 하면 exit 2 로 차단한다. 서브에이전트(특히
# unit-test-writer) 의 호출은 stdin JSON 의 `agent_id` 필드 존재 여부로
# 식별해 그대로 통과시킨다.
#
# 정책: fail-closed. payload 파싱 자체에 실패하거나 의심스러운 입력이면
# 통과가 아니라 차단이 기본이다. "알 수 없으면 통과" 는 규율 우회의 경로.
#
# 경로 매칭은 symlink 해소 후(`pwd -P`) `PROJECT_ROOT` prefix 로 판정해
# macOS `/var` ↔ `/private/var` 같은 표기 차이에 속지 않는다.
#
# 근거: CLAUDE.md "테스트 작성 정책", 이슈 #4.
# hook 스펙: https://code.claude.com/docs/en/hooks.md (PreToolUse)

set -euo pipefail

PAYLOAD="$(cat)"

# ---------------------------------------------------------------------------
# 1) payload 파싱 — 파싱 실패는 fail-closed.
#    필드가 아예 없는 것(정상 케이스) 과 JSON 자체가 깨진 것(비정상)을 구분.
#    성공 시 3 줄을 출력: agent_id, tool_name, file_path (각 줄은 빈 문자열 가능).
# ---------------------------------------------------------------------------

if ! FIELDS="$(
  printf '%s' "$PAYLOAD" | python3 -c '
import json, sys
raw = sys.stdin.read()
if not raw.strip():
    sys.exit(1)
try:
    d = json.loads(raw)
except Exception:
    sys.exit(1)
if not isinstance(d, dict):
    sys.exit(1)
ti = d.get("tool_input") or {}
if not isinstance(ti, dict):
    ti = {}
agent_id = d.get("agent_id") or ""
tool_name = d.get("tool_name") or ""
file_path = ti.get("file_path") or ti.get("notebook_path") or ""
print(agent_id)
print(tool_name)
print(file_path)
'
)"; then
  {
    echo "[tests-writer-guard] PreToolUse payload 파싱 실패 — 안전상 차단."
    echo ""
    echo "JSON 구조가 아니거나 최상위가 객체가 아닙니다. hook 이 의도치 않게"
    echo "우회될 위험이 있어 fail-closed 정책으로 tool 실행을 막습니다."
    echo ""
    echo "조치: payload 생성 지점(상위 셸 파이프·테스트 스크립트 등)을 확인하거나,"
    echo "      의도한 변경이라면 tests/ 수정은 unit-test-writer 서브에이전트를"
    echo "      경유하세요."
  } >&2
  exit 2
fi

# 각 read 뒤 `|| true` 로 EOF 에서의 exit 1 이 set -e 를 트리거하지 않도록 함.
# (macOS 의 bash 3.2 는 mapfile 을 지원하지 않아 read 기반으로 구현)
AGENT_ID=""
TOOL_NAME=""
FILE_PATH=""
{
  IFS= read -r AGENT_ID || true
  IFS= read -r TOOL_NAME || true
  IFS= read -r FILE_PATH || true
} <<< "$FIELDS"

# ---------------------------------------------------------------------------
# 2) 서브에이전트 호출이면 통과.
# ---------------------------------------------------------------------------

if [ -n "$AGENT_ID" ]; then
  exit 0
fi

# ---------------------------------------------------------------------------
# 3) 대상 도구가 아니면 통과.
# ---------------------------------------------------------------------------

case "$TOOL_NAME" in
  Write|Edit|NotebookEdit) : ;;
  *) exit 0 ;;
esac

# ---------------------------------------------------------------------------
# 4) file_path 가 없으면 판정 근거 부재 — 가드 대상 아님(통과).
#    (Write/Edit 는 파일 경로가 필수라 빈 값은 Claude Code 가 애초에 거부)
# ---------------------------------------------------------------------------

[ -z "$FILE_PATH" ] && exit 0

# ---------------------------------------------------------------------------
# 5) PROJECT_ROOT 확보 + symlink 해소.
# ---------------------------------------------------------------------------

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -z "$PROJECT_ROOT" ] && exit 0  # git 저장소 밖 — stock-agent 와 무관.

PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd -P || echo "$PROJECT_ROOT")"

case "$PROJECT_ROOT" in
  */stock-agent) : ;;
  *) exit 0 ;;  # 다른 프로젝트 — 간섭 안 함.
esac

# ---------------------------------------------------------------------------
# 6) FILE_PATH 정규화.
#    절대경로면 dirname 을 pwd -P 로 symlink 해소해 PROJECT_ROOT 와 동일
#    표기로 맞춘다. 신규 파일 생성 케이스에서 dirname 이 아직 없으면 원문
#    유지 (이후 prefix 매칭 실패 시 간섭 안 함으로 귀결).
# ---------------------------------------------------------------------------

case "$FILE_PATH" in
  /*)
    _dir="$(cd "$(dirname "$FILE_PATH")" 2>/dev/null && pwd -P || true)"
    if [ -n "$_dir" ]; then
      FILE_PATH_NORM="$_dir/$(basename "$FILE_PATH")"
    else
      FILE_PATH_NORM="$FILE_PATH"
    fi
    ;;
  *)
    FILE_PATH_NORM="$FILE_PATH"
    ;;
esac

# ---------------------------------------------------------------------------
# 7) PROJECT_ROOT prefix 로 상대경로 산출.
# ---------------------------------------------------------------------------

case "$FILE_PATH_NORM" in
  "$PROJECT_ROOT"/*) REL="${FILE_PATH_NORM#"$PROJECT_ROOT"/}" ;;
  /*) exit 0 ;;             # stock-agent 밖 절대경로 — 간섭 안 함.
  *) REL="$FILE_PATH_NORM" ;;  # 상대경로 그대로.
esac

# ---------------------------------------------------------------------------
# 8) tests/*.py 매칭 — bash case 의 * 는 / 를 포함해 전부 매칭하므로
#    단일 패턴으로 tests/test_x.py, tests/sub/test_y.py, tests/__init__.py,
#    tests/conftest.py 모두 잡힌다.
# ---------------------------------------------------------------------------

case "$REL" in
  tests/*.py) : ;;
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
