#!/bin/bash
# Claude Code hook: macOS 알림 + 효과음.
# 다음 3 개 이벤트에서 공용 호출된다 (stdin JSON 스키마가 이벤트별로 다름):
#   - Notification: { "session_id", "message" }  (권한 프롬프트 · 60초 idle)
#   - PreToolUse(AskUserQuestion|ExitPlanMode): { "tool_name", "tool_input", ... }
#   - Stop(선택): { "stop_hook_active", ... }
# 스크립트는 항상 exit 0 으로 도구 실행을 막지 않는다.

input=$(cat)

msg=$(printf '%s' "$input" | jq -r '
  if (.message // "") != "" then
    .message
  elif .tool_name == "AskUserQuestion" then
    "질문 대기 중"
  elif .tool_name == "ExitPlanMode" then
    "플랜 승인 대기 중"
  else
    "응답 대기 중"
  end
')

# osascript 문자열 이스케이프 (\ 와 " 만 처리)
safe_msg=${msg//\\/\\\\}
safe_msg=${safe_msg//\"/\\\"}

/usr/bin/osascript <<OSA >/dev/null 2>&1 &
display notification "${safe_msg}" with title "Claude Code" subtitle "응답 대기" sound name "Glass"
OSA

# PreToolUse 에서도 호출되므로 stdin passthrough 로 후속 훅·툴 실행을 방해하지 않음.
printf '%s' "$input"
exit 0
