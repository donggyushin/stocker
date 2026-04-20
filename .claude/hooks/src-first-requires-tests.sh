#!/usr/bin/env bash
# stock-agent PreToolUse hook — src-first TDD 게이트.
#
# 메인 assistant 가 `Write` 로 `src/stock_agent/` 아래 신규 Python 파일을
# 만들려 할 때, 대응하는 `tests/test_*.py` 가 먼저 존재하지 않으면 exit 2 로
# 차단한다. unit-test-writer 서브에이전트 호출은 stdin JSON 의 `agent_id`
# 필드 존재 여부로 식별해 그대로 통과시킨다.
#
# 정책 근거:
#   - CLAUDE.md "TDD 순서 강제" 섹션 (Red-Green-Refactor 의 Red 단계 선제 의무).
#   - ADR 0010 (src-first-tdd-enforcement). 기존 ADR 0005 확장.
#
# 스코프:
#   - `settings.json` 의 matcher 가 `Write|Edit|NotebookEdit` 세 도구에만
#     이 훅을 호출한다. 그중 **`Write` 만 실동작** — `Edit`/`NotebookEdit`
#     은 기존 파일 보강 경로라 내부 분기(`tool_name != "Write"` → exit 0)
#     에서 조기 통과시키고, Stop 단계의 `test-coverage-check.sh` 가 사후
#     검증한다. matcher 가 바뀌면 이 전제가 무너지니 함께 수정할 것.
#   - 대상 파일이 이미 존재하면(`-e`) overwrite 경로 → 이 훅은 통과하고,
#     Stop 단계 `test-coverage-check.sh` 가 src 변경 + tests 미갱신을
#     사후 리마인더로 포착한다 (세션당 1회).
#   - `__init__.py` 는 보통 얇은 패키지 마커 → 통과. 단 re-export surface
#     (`broker/__init__.py` 등) 에 새 심볼을 추가하는 변경은 이 훅 범위
#     밖이며, Stop 훅 리마인더에만 의존한다.
#
# 우회:
#   - `STOCK_AGENT_TDD_BYPASS=1` 환경변수. 이 훅이 실행될 때 상속된 값만
#     보므로 "설정된 쉘에서 기동된 `claude` 프로세스 lifetime" 단위로
#     작동한다 (쉘에 남겨두면 이후 세션에도 상속되니 export 회수 주의).
#     BYPASS 통과 시 stderr 에 UTC 시각·대상 파일·CLAUDE.md 후속 규정을
#     함께 남겨 추적성 확보.
#
# 정책: fail-closed. payload 파싱 실패, `tool_name` 부재, 경계 판정 실패
# 는 모두 exit 2 또는 stderr 경고를 동반한다 — "알 수 없으면 조용히 통과"
# 경로를 두지 않는다 (ADR 0003 기조).
#
# 경로 매칭은 symlink 해소 후(`pwd -P`) `PROJECT_ROOT` prefix 로 판정해
# macOS `/var` ↔ `/private/var` 표기 차이에 속지 않는다.
#
# hook 스펙: https://code.claude.com/docs/en/hooks.md (PreToolUse)

set -euo pipefail

PAYLOAD="$(cat)"

# ---------------------------------------------------------------------------
# 1) payload 파싱 — 파싱 실패는 fail-closed.
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
file_path = ti.get("file_path") or ""
print(agent_id)
print(tool_name)
print(file_path)
'
)"; then
  {
    echo "[src-first-requires-tests] PreToolUse payload 파싱 실패 — 안전상 차단."
    echo ""
    echo "조치: payload 생성 지점(상위 셸 파이프·테스트 스크립트 등)을 확인하세요."
  } >&2
  exit 2
fi

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
# 3) tool_name 분기.
#    빈 값은 fail-closed (payload 에 `tool_name` 이 반드시 실리는 것이
#    Claude Code 의 계약 — 빈 값은 비정상 입력이므로 조용히 통과시키지
#    않는다. 원칙은 ADR 0003 "조용한 fallback 금지").
#    Write 가 아닌 도구(Edit/NotebookEdit/기타)는 정상 통과.
# ---------------------------------------------------------------------------

if [ -z "$TOOL_NAME" ]; then
  {
    echo "[src-first-requires-tests] payload 에 tool_name 이 비어 있음 — 안전상 차단."
    echo ""
    echo "Claude Code 계약상 PreToolUse payload 는 tool_name 을 반드시 포함합니다."
    echo "빈 값이면 업스트림 스펙 변경 또는 payload 조작이 의심됩니다."
    echo "조치: payload 생성 지점을 확인하거나, 의도한 변경이면"
    echo "      unit-test-writer 서브에이전트 경유로 작업하세요."
  } >&2
  exit 2
fi

if [ "$TOOL_NAME" != "Write" ]; then
  exit 0
fi

# ---------------------------------------------------------------------------
# 4) BYPASS 환경변수가 설정됐으면 통과 + 기록.
#    금융 자동매매 프로젝트 특성상 우회 사실은 사용자가 명시적으로
#    선택했더라도 추적 가능해야 한다. UTC 시각·대상 파일·후속 규정
#    요약을 stderr 에 남긴다.
# ---------------------------------------------------------------------------

if [ "${STOCK_AGENT_TDD_BYPASS:-}" = "1" ]; then
  BYPASS_TS="$(date -u +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || echo 'unknown')"
  {
    echo "[src-first-requires-tests] STOCK_AGENT_TDD_BYPASS=1 — TDD 게이트 우회."
    echo "  시각(UTC): ${BYPASS_TS}"
    echo "  대상 파일: ${FILE_PATH:-<unspecified>}"
    echo ""
    echo "CLAUDE.md \"TDD 순서 강제\" 정책상 긴급 핫픽스 우회는 24 시간 내"
    echo "회귀 테스트 작성이 필수입니다. 우회 사실은 PR 본문·커밋 메시지에"
    echo "명시하세요."
  } >&2
  exit 0
fi

# ---------------------------------------------------------------------------
# 5) file_path 가 없으면 판정 불가 — 통과(Write 는 Claude Code 가 이미 필수화).
# ---------------------------------------------------------------------------

[ -z "$FILE_PATH" ] && exit 0

# ---------------------------------------------------------------------------
# 6) PROJECT_ROOT 확보 + symlink 해소.
#    git 저장소 밖·suffix 불일치는 "stock-agent 작업이 아님" 판정이지만,
#    그 판정 자체가 오진일 수 있으므로 stderr 경고를 남기고 통과한다
#    (fail-closed 비대칭 해소 — ADR 0003).
# ---------------------------------------------------------------------------

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$PROJECT_ROOT" ]; then
  echo "[src-first-requires-tests] PROJECT_ROOT 확정 실패 (git 저장소 밖?) — 간섭 없이 통과." >&2
  exit 0
fi

PROJECT_ROOT="$(cd "$PROJECT_ROOT" 2>/dev/null && pwd -P || echo "$PROJECT_ROOT")"

case "$PROJECT_ROOT" in
  */stock-agent) : ;;
  *)
    echo "[src-first-requires-tests] PROJECT_ROOT='${PROJECT_ROOT}' 가 */stock-agent suffix 와 불일치 — 간섭 없이 통과." >&2
    exit 0
    ;;
esac

# ---------------------------------------------------------------------------
# 7) FILE_PATH 정규화 (dirname 을 pwd -P 로 해소, 신규 파일 dirname 부재 시 원문).
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
# 8) PROJECT_ROOT prefix 로 상대경로 산출.
# ---------------------------------------------------------------------------

case "$FILE_PATH_NORM" in
  "$PROJECT_ROOT"/*) REL="${FILE_PATH_NORM#"$PROJECT_ROOT"/}" ;;
  /*) exit 0 ;;
  *) REL="$FILE_PATH_NORM" ;;
esac

# ---------------------------------------------------------------------------
# 9) src/stock_agent/**/*.py 가 아니면 통과.
# ---------------------------------------------------------------------------

case "$REL" in
  src/stock_agent/*.py) : ;;
  *) exit 0 ;;
esac

# ---------------------------------------------------------------------------
# 10) 대상 파일이 이미 존재(절대경로 기준)하면 overwrite — 이 훅은 통과.
#     `test-coverage-check.sh` 가 Stop 단계에서 사후 검증.
# ---------------------------------------------------------------------------

ABS_PATH="${PROJECT_ROOT}/${REL}"
if [ -e "$ABS_PATH" ]; then
  exit 0
fi

# ---------------------------------------------------------------------------
# 11) __init__.py 는 통과.
# ---------------------------------------------------------------------------

case "$REL" in
  */__init__.py) exit 0 ;;
esac

# ---------------------------------------------------------------------------
# 12) 후보 테스트 경로 3종 생성 + 존재 확인.
#     REL = "src/stock_agent/strategy/orb.py"
#       → AFTER = "strategy/orb.py"
#       → BASENAME = "orb", SUBPATH = "strategy"
#       → CAND1 tests/test_orb.py (flat)
#       → CAND2 tests/test_strategy_orb.py (flat + first-subpkg prefix, 현 관례)
#       → CAND3 tests/strategy/test_orb.py (mirror)
#     REL = "src/stock_agent/foo.py" (최상위, 현재는 없지만 미래 대비)
#       → AFTER = "foo.py"
#       → BASENAME = "foo", SUBPATH = ""
#       → CAND1 tests/test_foo.py 만
# ---------------------------------------------------------------------------

AFTER="${REL#src/stock_agent/}"
FILENAME="$(basename "$AFTER")"
BASENAME="${FILENAME%.py}"

SUBPATH="${AFTER%/*}"
if [ "$SUBPATH" = "$AFTER" ]; then
  SUBPATH=""
fi

CAND1="tests/test_${BASENAME}.py"
CAND2=""
CAND3=""
if [ -n "$SUBPATH" ]; then
  FIRST_SUBPKG="${SUBPATH%%/*}"
  CAND2="tests/test_${FIRST_SUBPKG}_${BASENAME}.py"
  CAND3="tests/${SUBPATH}/test_${BASENAME}.py"
fi

for c in "$CAND1" "$CAND2" "$CAND3"; do
  if [ -n "$c" ] && [ -f "${PROJECT_ROOT}/$c" ]; then
    exit 0
  fi
done

# ---------------------------------------------------------------------------
# 13) 후보 전부 부재 → exit 2 + 안내.
# ---------------------------------------------------------------------------

{
  echo "[src-first-requires-tests] 대응 테스트 없이 src/ 신규 파일 생성 차단됨."
  echo ""
  echo "대상 도구: ${TOOL_NAME}"
  echo "신규 파일: ${REL}"
  echo ""
  echo "CLAUDE.md \"TDD 순서 강제\" 에 따라 src/stock_agent/ 아래 신규 Python 파일은"
  echo "대응 pytest 파일이 **먼저** 존재해야 합니다 (Red 단계 선행)."
  echo ""
  echo "기대 경로 후보 (하나만 존재하면 통과):"
  echo "  - ${CAND1}"
  if [ -n "$CAND2" ]; then
    echo "  - ${CAND2}"
  fi
  if [ -n "$CAND3" ]; then
    echo "  - ${CAND3}"
  fi
  echo ""
  echo "조치: Agent 툴로 subagent_type=\"unit-test-writer\" 를 호출해 실패하는"
  echo "      테스트(RED) 를 먼저 작성하세요. 호출 시 다음을 프롬프트에 명시:"
  echo "        (a) 대상 src 경로 및 새로 추가할 공개 동작"
  echo "        (b) 모드 = RED (기본)"
  echo "        (c) uv run pytest -x <테스트 경로> 로 FAIL 확인 후 리턴"
  echo ""
  echo "예외 우회 (긴급 핫픽스·명시 리팩터 등): STOCK_AGENT_TDD_BYPASS=1 환경변수."
  echo "                                        24 시간 내 회귀 테스트 작성 필수."
} >&2

exit 2
