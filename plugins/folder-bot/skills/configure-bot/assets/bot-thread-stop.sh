#!/usr/bin/env bash
# Stop 훅 — 스레드 세션의 마지막 답변을 디스코드 스레드에 게시한다(bot-thread post 경유).
# botctl add가 <봇 폴더>/.claude/settings.local.json hooks.Stop에 등록하고 remove가 회수한다.
# 메인 봇·로컬 세션(DISCORD_THREAD_ID 없음)에서는 즉시 0으로 끝나 무영향.
# stdin: Claude Code Stop 훅 JSON {session_id, transcript_path, stop_hook_active, ...}
# 테스트 override: BOT_THREAD_BIN(bot-thread 경로).
set -uo pipefail

[[ -n "${DISCORD_THREAD_ID:-}" && -n "${DISCORD_BOT_NAME:-}" ]] || exit 0
INPUT=$(cat)
BOT_THREAD="${BOT_THREAD_BIN:-$HOME/.local/bin/bot-thread}"
LOG="${BOT_THREAD_STOP_LOG:-$HOME/.claude/logs/bot-thread-stop.log}"
log() { mkdir -p "$(dirname "$LOG")" 2>/dev/null; echo "[$(date '+%F %T')] $DISCORD_BOT_NAME $*" >> "$LOG" 2>/dev/null || true; }

extract() {
python3 - "$INPUT" <<'EOF'
import json, sys
try:
    hook = json.loads(sys.argv[1])
except ValueError:
    sys.exit(0)
if hook.get("stop_hook_active"):
    sys.exit(0)
path = hook.get("transcript_path")
if not path:
    sys.exit(0)
# 마지막 사람 턴(문자열 content 또는 text 블록이 있고 tool_result가 없는 user 줄) 이후의
# assistant text 블록만 이어 붙인다. tool_result만 담긴 user 줄은 턴 경계가 아니다.
parts = []
try:
    for line in open(path, encoding="utf-8"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get("type")
        msg = d.get("message") or {}
        content = msg.get("content")
        if t == "user":
            if isinstance(content, str):
                parts = []
            elif isinstance(content, list):
                kinds = {c.get("type") for c in content if isinstance(c, dict)}
                if "tool_result" not in kinds:
                    parts = []
        elif t == "assistant" and isinstance(content, list):
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text" and c.get("text", "").strip():
                    parts.append(c["text"].strip())
except OSError:
    sys.exit(0)
print("\n\n".join(parts))
EOF
}

# Stop 훅은 마지막 답변이 transcript에 flush되기 전에 돌 수 있다(2026-09-11 컨테이너 실측: 훅 49ms에
# 빈 텍스트로 종료 → 게시 누락). 비어 있으면 최대 ~3초 재시도.
TEXT=$(extract)
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [[ -n "${TEXT// /}" ]] && break
  sleep 0.3
  TEXT=$(extract)
done
if [[ -z "${TEXT// /}" ]]; then
  log "스레드 $DISCORD_THREAD_ID: 게시할 텍스트 없음(재시도 후) — transcript=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("transcript_path",""))' "$INPUT")"
  exit 0
fi
if OUT=$(printf '%s' "$TEXT" | "$BOT_THREAD" post "$DISCORD_BOT_NAME" "$DISCORD_THREAD_ID" - 2>&1); then
  log "스레드 $DISCORD_THREAD_ID: 게시 ${#TEXT}자"
else
  log "스레드 $DISCORD_THREAD_ID: 게시 실패 — $OUT"
  echo "bot-thread-stop: 게시 실패(스레드 $DISCORD_THREAD_ID)" >&2
fi
exit 0
