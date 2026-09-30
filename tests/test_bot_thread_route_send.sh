#!/usr/bin/env bash
# bot-thread-route — 스레드 원문은 `agentlayer send`(채널)로 먼저 넘기고, 그게 실패할 때만 pane 붙여넣기(deliver)로 되돌아간다.
# 붙여넣기는 이미지 경로가 든 여러 줄에서 "[Pasted text …]"로 접힌 채 제출되지 않는다(2026-09-30 실기).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
ROUTE="$HERE/../plugins/folder-bot/skills/configure-bot/assets/bot-thread-route.sh"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/folder/.discord-state"
echo '{"groups":{}}' > "$T/folder/.discord-state/access.json"
printf '{"b":{"engine":"claude","folder":"%s","session":"b-bot","dev_channels":["server:agentlayer"]}}' "$T/folder" > "$T/bots.json"

cat > "$T/bin/bot-thread" <<'STUB'
#!/usr/bin/env bash
cmd=$1; shift 2
case "$cmd" in
  kind) echo "thread 111" ;;
  list) : ;;
  ensure) echo "b-t000999" ;;
  fresh) echo 0 ;;
  fetch-attachments) echo "$STUB_DIR/inbox/0-image.png" ;;
  deliver) cat > "$STUB_DIR/deliver.body" ;;
  post) : ;;
esac
STUB
cat > "$T/bin/agentlayer" <<'STUB'
#!/usr/bin/env bash
echo "$*" > "$STUB_DIR/agentlayer.args"; cat > "$STUB_DIR/agentlayer.body"
[[ "${STUB_FAIL:-0}" == 1 ]] && { echo "b-bot:t000999(WAIT): 승인 대기 중입니다" >&2; exit 1; }
echo '{"session":"b-bot","window":"t000999","pane":"%17","state":"idle","sent":true,"via":"channel"}'
STUB
chmod +x "$T/bin/bot-thread" "$T/bin/agentlayer"

PROMPT='<channel source="plugin:discord:discord" chat_id="777000999" message_id="5" user="u" attachment_count="1">사진 봐줘</channel>'
INPUT=$(python3 -c 'import json,sys; print(json.dumps({"session_id":"x","prompt":sys.argv[1],"cwd":sys.argv[2]}))' "$PROMPT" "$T/folder")

run() {  # $1=STUB_FAIL
  rm -f "$T/deliver.body" "$T/agentlayer.args" "$T/agentlayer.body"
  set +e
  (unset DISCORD_THREAD_ID; STUB_DIR="$T" STUB_FAIL="$1" BOT_THREAD_BIN="$T/bin/bot-thread" BOT_THREAD_AGENTLAYER="$T/bin/agentlayer" \
    BOT_THREAD_BOTS_JSON="$T/bots.json" bash "$ROUTE" <<<"$INPUT" >"$T/out" 2>"$T/err")
  rc=$?; set -e; return 0
}

run 0
[[ "$rc" == 2 ]] || { echo "FAIL channel: exit 2 기대, $rc"; cat "$T/err"; exit 1; }
[[ "$(cat "$T/agentlayer.args")" == "send --json b-bot:t000999 -" ]] || { echo "FAIL channel: agentlayer 인자 $(cat "$T/agentlayer.args")"; exit 1; }
grep -q '사진 봐줘' "$T/agentlayer.body" || { echo "FAIL channel: 원문이 send 본문에 없음"; exit 1; }
grep -q '0-image.png' "$T/agentlayer.body" || { echo "FAIL channel: 첨부 경로가 send 본문에 없음"; exit 1; }
[[ ! -f "$T/deliver.body" ]] || { echo "FAIL channel: 채널로 보냈는데 붙여넣기도 함(두 번 전달)"; exit 1; }
grep -q 'via=channel' "$T/folder/.discord-state/thread-route.log" || { echo "FAIL channel: 로그에 via 없음"; cat "$T/folder/.discord-state/thread-route.log"; exit 1; }

run 1
[[ "$rc" == 2 ]] || { echo "FAIL fallback: exit 2 기대, $rc"; cat "$T/err"; exit 1; }
[[ -f "$T/deliver.body" ]] || { echo "FAIL fallback: send 실패면 deliver로 되돌아가야 함"; exit 1; }
grep -q '사진 봐줘' "$T/deliver.body" || { echo "FAIL fallback: deliver 본문에 원문 없음"; exit 1; }

echo "PASS test_bot_thread_route_send"
