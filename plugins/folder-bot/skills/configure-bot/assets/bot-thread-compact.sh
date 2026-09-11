#!/usr/bin/env bash
# PreCompact 훅 — 스레드 세션의 컨텍스트가 차서 auto-compact가 돌 때 스레드에 알린다(사후 알림).
# compact는 폴백으로 둔다. 세부를 보존하려면 사용자가 "세션 마감하고 재시작해"로 회전(bot-thread rotate)한다.
# 메인 봇·로컬 세션(DISCORD_THREAD_ID 없음)에서는 즉시 exit 0.
set -uo pipefail
[[ -n "${DISCORD_THREAD_ID:-}" && -n "${DISCORD_BOT_NAME:-}" ]] || exit 0
cat >/dev/null
BOT_THREAD="${BOT_THREAD_BIN:-$HOME/.local/bin/bot-thread}"
"$BOT_THREAD" post "$DISCORD_BOT_NAME" "$DISCORD_THREAD_ID" \
  "ℹ️ 이 스레드 세션의 컨텍스트가 차서 자동 압축(compact)이 실행됩니다. 세부를 보존하려면 \"세션 마감하고 재시작해\"라고 하면 기록을 남기고 새 세션으로 이어갑니다." >/dev/null 2>&1 || true
exit 0
