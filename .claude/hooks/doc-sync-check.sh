#!/usr/bin/env bash
# stock-agent Stop hook — doc sync reminder.
#
# Fires at end of Claude's turn. If non-doc files changed in the working tree
# but CLAUDE.md / README.md / plan.md were NOT touched, emit a reminder via
# stderr and exit 2 (which makes Claude continue so it can decide whether to
# sync docs). Fires at most once per session via /tmp marker.

set -euo pipefail

PAYLOAD="$(cat)"
SESSION_ID="$(
  printf '%s' "$PAYLOAD" | python3 -c '
import json, sys
try:
    print(json.loads(sys.stdin.read()).get("session_id", "unknown"))
except Exception:
    print("unknown")
' 2>/dev/null || echo "unknown"
)"

MARKER="/tmp/stock-agent-docsync-${SESSION_ID}"

# One-shot per session
if [ -e "$MARKER" ]; then
  exit 0
fi

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -z "$PROJECT_ROOT" ] && exit 0

# Safety: only fire inside stock-agent
case "$PROJECT_ROOT" in
  */stock-agent) : ;;
  *) exit 0 ;;
esac

cd "$PROJECT_ROOT"

CHANGED="$(git status --porcelain)"
[ -z "$CHANGED" ] && exit 0

DOC_REGEX='^(CLAUDE\.md|README\.md|plan\.md)$'
CODE_CHANGED=0
DOC_CHANGED=0
NON_DOC_LIST=""

while IFS= read -r line; do
  # porcelain: "XY path" — path starts at column 4; handle rename "old -> new"
  f="${line:3}"
  f="${f##* -> }"

  # Ignore .claude/ internals (agents/hooks/commands/settings changes)
  case "$f" in
    .claude/*) continue ;;
  esac

  if printf '%s' "$f" | grep -Eq "$DOC_REGEX"; then
    DOC_CHANGED=1
  else
    CODE_CHANGED=1
    NON_DOC_LIST="${NON_DOC_LIST}  - ${f}"$'\n'
  fi
done <<< "$CHANGED"

if [ "$CODE_CHANGED" = "1" ] && [ "$DOC_CHANGED" = "0" ]; then
  touch "$MARKER"
  {
    echo "[doc-sync-check] CLAUDE.md / README.md / plan.md 갱신이 없는 상태에서 비독스 파일이 수정되었습니다."
    echo ""
    echo "변경된 비독스 파일:"
    printf '%s' "$NON_DOC_LIST"
    echo ""
    echo "CLAUDE.md 문서 동기화 정책에 따라 아래 유형이 포함됐는지 확인하세요:"
    echo "  - Phase 진입/완료, 산출물 달성"
    echo "  - 리스크 한도·전략 파라미터 변경"
    echo "  - 기술 스택 교체"
    echo "  - 디렉토리 구조 추가"
    echo "  - 새 실행 가능 명령/스크립트 추가"
    echo "  - 새 결정 도입 또는 기존 결정 번복"
    echo ""
    echo "해당되면 markdown-writer 서브에이전트로 동기화하세요."
    echo "해당 없으면 그대로 진행해도 됩니다 — 이 리마인더는 이번 세션에 재표시되지 않습니다."
  } >&2
  exit 2
fi

exit 0
