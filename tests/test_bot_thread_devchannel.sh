#!/usr/bin/env bash
# bot-thread ensure — 봇에 dev_channels가 있으면 스레드 세션도 그 플래그로 뜨고, 확인창을 Enter 한 번으로 넘긴 뒤에야 준비로 본다.
# 확인창에도 '❯'가 있어("❯ 1. I am using…") 확인창을 준비 상태로 오인하면 첫 지시가 확인창에 들어간다.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BT="$HERE/../plugins/folder-bot/skills/configure-bot/assets/bot-thread.sh"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/folder/.discord-state" "$T/projects"
cat > "$T/bin/tmux" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  has-session) exit 0 ;;
  new-window) echo "$*" > "$STUB_DIR/new-window.args"; echo "%77" ;;
  list-panes) echo "%77 4242" ;;
  display-message) echo "" ;;
  capture-pane)
    if [[ "${STUB_DIALOG:-0}" == 1 && ! -f "$STUB_DIR/keys.log" ]]; then
      printf '  WARNING: Loading development channels\n  Channels: server:agentlayer\n  ❯ 1. I am using this for local development\n'
    else
      printf '❯ \n'
    fi ;;
  send-keys) echo "send-keys $*" >> "$STUB_DIR/keys.log" ;;
  kill-window) : ;;
esac
STUB
chmod +x "$T/bin/tmux"

run() {  # $1=bots.json 내용 $2=STUB_DIALOG
  rm -f "$T/keys.log" "$T/new-window.args" "$T/folder/.discord-state/threads.json"
  printf '%s' "$1" > "$T/bots.json"
  STUB_DIR="$T" STUB_DIALOG="$2" BOT_THREAD_TMUX="$T/bin/tmux" BOT_THREAD_CLAUDE=claude BOT_THREAD_PROJECTS="$T/projects" \
    BOT_THREAD_BOTS_JSON="$T/bots.json" BOT_THREAD_READY_TIMEOUT=6 bash "$BT" ensure b 777000999 2>"$T/err.log"
}
WITH="{\"b\":{\"folder\":\"$T/folder\",\"session\":\"b-bot\",\"dev_channels\":[\"server:agentlayer\"]}}"
WITHOUT="{\"b\":{\"folder\":\"$T/folder\",\"session\":\"b-bot\"}}"
UNSAFE="{\"b\":{\"folder\":\"$T/folder\",\"session\":\"b-bot\",\"dev_channels\":[\"server:x; rm -rf /\"]}}"

out=$(run "$WITH" 1) || { echo "FAIL with: ensure 실패"; cat "$T/err.log"; exit 1; }
[[ "$out" == "b-t000999" ]] || { echo "FAIL with: 출력 $out"; exit 1; }
grep -q -- '--dangerously-load-development-channels server:agentlayer' "$T/new-window.args" \
  || { echo "FAIL with: 플래그 없음: $(cat "$T/new-window.args")"; exit 1; }
[[ $(grep -c 'send-keys -t %77 Enter' "$T/keys.log" 2>/dev/null || echo 0) == 1 ]] \
  || { echo "FAIL with: 확인창 Enter 1회 기대"; cat "$T/keys.log" 2>/dev/null; exit 1; }

out=$(run "$WITHOUT" 0) || { echo "FAIL without: ensure 실패"; cat "$T/err.log"; exit 1; }
if grep -q -- '--dangerously-load-development-channels' "$T/new-window.args"; then echo "FAIL without: 플래그가 붙음"; exit 1; fi
[[ ! -s "$T/keys.log" ]] || { echo "FAIL without: Enter를 보내면 안 됨"; exit 1; }

out=$(run "$UNSAFE" 0) || { echo "FAIL unsafe: ensure 실패"; cat "$T/err.log"; exit 1; }
if grep -q -- 'rm -rf' "$T/new-window.args"; then echo "FAIL unsafe: 위험한 값이 명령에 들어감"; exit 1; fi

echo "PASS test_bot_thread_devchannel"
