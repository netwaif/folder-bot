#!/usr/bin/env bash
# UserPromptSubmit 훅 — 디스코드 스레드 메시지를 메인 봇이 보기 전에 스레드 세션으로 넘긴다(결정적 라우팅).
# botctl add가 <봇 폴더>/.claude/settings.local.json hooks.UserPromptSubmit에 등록하고 remove가 회수한다.
#
# 흐름: prompt의 <channel … chat_id="…"> 태그 → 등록 채널(access.json groups)이면 exit 0(메인이 처리)
#       → bot-thread kind로 스레드 판별 → bot-thread ensure(창·세션 보장) → 첨부 다운로드(REST)
#       → bot-thread deliver(스레드 pane에 원문 붙여넣기) → 첫 메시지면 담당 안내 게시 → exit 2(메인 처리 차단)
# 실패 시: exit 0 + stdout으로 "[스레드 라우팅 실패]" 안내를 붙여 메인이 이번만 대신 답하게 한다.
# 스레드 세션(DISCORD_THREAD_ID)·로컬 세션에서는 즉시 exit 0.
# stdin: {session_id, prompt, cwd, ...}. 테스트 override: BOT_THREAD_BIN, BOT_THREAD_CURL.
set -uo pipefail

[[ -z "${DISCORD_THREAD_ID:-}" ]] || exit 0
INPUT=$(cat)
BOT_THREAD="${BOT_THREAD_BIN:-$HOME/.local/bin/bot-thread}"

# prompt·cwd·chat_id·message_id 추출 (python으로 — 태그 속성 순서에 의존하지 않는다)
read -r CHAT_ID MSG_ID CWD < <(python3 - "$INPUT" <<'EOF'
import json, re, sys
try:
    d = json.loads(sys.argv[1])
except ValueError:
    print("", "", ""); sys.exit(0)
p = d.get("prompt") or ""
m = re.search(r'<channel\b[^>]*\bsource="plugin:discord:discord"[^>]*>', p)
if not m:
    print("", "", ""); sys.exit(0)
tag = m.group(0)
cid = re.search(r'\bchat_id="(\d+)"', tag)
mid = re.search(r'\bmessage_id="(\d+)"', tag)
print(cid.group(1) if cid else "", mid.group(1) if mid else "", d.get("cwd") or "")
EOF
)
[[ -n "$CHAT_ID" ]] || exit 0

# 봇 이름·폴더: cwd가 bots.json의 folder와 일치하는 항목
read -r BOT FOLDER < <(python3 - "${BOT_THREAD_BOTS_JSON:-$HOME/.config/folder-bot/bots.json}" "$CWD" <<'EOF'
import json, sys, os
try:
    bots = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    print("", ""); sys.exit(0)
cwd = os.path.realpath(sys.argv[2]) if sys.argv[2] else ""
for name, b in bots.items():
    if b.get("engine", "claude") == "claude" and os.path.realpath(b["folder"]) == cwd:
        print(name, b["folder"]); sys.exit(0)
print("", "")
EOF
)
[[ -n "$BOT" ]] || exit 0

STATE="$FOLDER/.discord-state"
# 등록 채널(메인)이면 메인이 처리
if python3 - "$STATE/access.json" "$CHAT_ID" <<'EOF'
import json, sys
try:
    g = json.load(open(sys.argv[1])).get("groups", {})
except (OSError, ValueError):
    sys.exit(0)
sys.exit(0 if sys.argv[2] in g else 1)
EOF
then exit 0; fi

fail() {  # 라우팅 실패 → 메인이 이번만 대신 답하되 실패를 먼저 알리게
  echo "[스레드 라우팅 실패] $1 — 이 메시지는 스레드($CHAT_ID)의 것이다. reply(chat_id=$CHAT_ID)로 '전용 세션 기동에 실패해 이번만 메인이 답합니다' 한 줄을 먼저 붙이고 질문에만 답하라. 원인 조사·로그·.discord-state·설정 파일 열람은 하지 말 것(운영자가 따로 본다)."
  echo "bot-thread-route: $1" >&2
  exit 0
}

KIND=$("$BOT_THREAD" kind "$BOT" "$CHAT_ID" 2>/dev/null) || fail "채널 조회 실패"
[[ "$KIND" == thread* ]] || exit 0   # DM·기타는 기존 규칙(메인)

FIRST=""
[[ -z "$("$BOT_THREAD" list "$BOT" 2>/dev/null | awk -F'\t' -v t="$CHAT_ID" '$1==t && $2!=""')" ]] && FIRST=1
AGENT=$("$BOT_THREAD" ensure "$BOT" "$CHAT_ID" 2>>"$STATE/thread-route.log") || fail "스레드 세션 기동 실패(ensure)"

# 원문(태그 포함) + 첨부 경로
BODY=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1]).get("prompt",""))' "$INPUT")
if [[ -n "$MSG_ID" ]] && grep -q 'attachment_count=' <<<"$BODY"; then
  ATT=$("$BOT_THREAD" fetch-attachments "$BOT" "$CHAT_ID" "$MSG_ID" 2>>"$STATE/thread-route.log" || true)
  [[ -n "$ATT" ]] && BODY="$BODY
첨부 파일(다운로드됨):
$ATT"
fi
printf '%s' "$BODY" | "$BOT_THREAD" deliver "$BOT" "$CHAT_ID" - 2>>"$STATE/thread-route.log" || fail "스레드 세션 전달 실패(deliver)"
if [[ -n "$FIRST" ]]; then
  SHORT="${CHAT_ID: -6}"
  "$BOT_THREAD" post "$BOT" "$CHAT_ID" "이 스레드는 전용 세션 \`$AGENT\`이 담당합니다 (터미널: tmux 창 \`t$SHORT\`)." >/dev/null 2>&1 || true
fi
echo "스레드 $CHAT_ID → 세션 $AGENT 로 위임됨(메인 미처리)" >&2
exit 2
