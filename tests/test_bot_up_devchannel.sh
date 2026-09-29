#!/usr/bin/env bash
# bot-up.sh 개발 채널 확인창 자동 통과 — tmux 스텁으로 capture-pane 3번째 호출에 문구를 내고 send-keys가 1회만 불리는지.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
BOTUP="$HERE/../plugins/folder-bot/skills/configure-bot/assets/bot-up.sh"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/home/.claude/logs"
cat > "$T/bin/tmux" <<'EOF'
#!/usr/bin/env bash
cnt_file="$STUB_DIR/capture.count"
case "$1" in
  capture-pane)
    n=$(( $(cat "$cnt_file" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$cnt_file"
    if [[ "${STUB_SHOW:-1}" == 1 && $n -ge 3 ]]; then printf '  WARNING: Loading development channels\n  Channels: server:agentlayer\n'; else printf '  loading...\n'; fi ;;
  send-keys) echo "send-keys $*" >> "$STUB_DIR/keys.log" ;;
esac
EOF
chmod +x "$T/bin/tmux"
# claude 스텁: 6초 살아 있다 종료(감시자가 그동안 폴링)
cat > "$T/bin/claude" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$STUB_DIR/claude.args"
sleep "${STUB_SLEEP:-6}"
EOF
chmod +x "$T/bin/claude"
run_case() {  # $1=케이스명 $2=STUB_SHOW $3=인자...
  rm -f "$T/capture.count" "$T/keys.log"
  ( cd "$T" && STUB_DIR="$T" STUB_SHOW="$2" HOME="$T/home" CLAUDE_BIN="$T/bin/claude" TMUX_BIN="$T/bin/tmux" TMUX_PANE="%9" \
      CLAUDE_BOT_LOCK="$T/lock.d" CONNECT_TIMEOUT=1 DEV_CHANNEL_POLL=1 DEV_CHANNEL_TIMEOUT=4 BOT_UP_LOG="$T/home/.claude/logs/bot-up.log" \
      bash "$BOTUP" "${@:3}" >/dev/null 2>&1 ) || true
  sleep 1
}
run_case dialog 1 -n x --channels plugin:discord@claude-plugins-official --dangerously-load-development-channels server:agentlayer
[[ $(grep -c 'send-keys -t %9 Enter' "$T/keys.log" 2>/dev/null || echo 0) == 1 ]] || { echo "FAIL dialog: Enter 1회 기대"; cat "$T/keys.log" 2>/dev/null; exit 1; }
grep -q 'dev-channel: 확인창 통과' "$T/home/.claude/logs/bot-up.log" || { echo "FAIL dialog: 로그 없음"; exit 1; }
run_case no_dialog 0 -n x --channels plugin:discord@claude-plugins-official --dangerously-load-development-channels server:agentlayer
[[ ! -s "$T/keys.log" ]] || { echo "FAIL no_dialog: Enter를 보내면 안 됨"; exit 1; }
grep -q 'dev-channel: 확인창 미출현' "$T/home/.claude/logs/bot-up.log" || { echo "FAIL no_dialog: 미출현 로그 없음"; exit 1; }
run_case no_flag 1 -n x --channels plugin:discord@claude-plugins-official
[[ ! -f "$T/capture.count" ]] || { echo "FAIL no_flag: 플래그 없으면 감시자도 없어야 함"; exit 1; }
# --flag=value 형태도 같은 플래그로 인식한다(리뷰 이월): 확인창 감시자가 뜨고 Enter 1회
run_case dialog_eq 1 -n x --channels plugin:discord@claude-plugins-official --dangerously-load-development-channels=server:agentlayer
[[ $(grep -c 'send-keys -t %9 Enter' "$T/keys.log" 2>/dev/null || echo 0) == 1 ]] || { echo "FAIL dialog_eq: --flag=value 형태에서 Enter 1회 기대"; cat "$T/keys.log" 2>/dev/null; exit 1; }
# --permission-mode: 호출자 지정(두 형태 모두)이 있으면 덧붙이지 않고, 없으면 auto를 붙인다
export STUB_SLEEP=0
run_case perm_eq 0 -n x --permission-mode=plan
[[ $(grep -o -- '--permission-mode' "$T/claude.args" | wc -l | tr -d ' ') == 1 ]] || { echo "FAIL perm_eq: --permission-mode=plan 지정인데 중복 주입: $(cat "$T/claude.args")"; exit 1; }
grep -q -- '--permission-mode=plan' "$T/claude.args" || { echo "FAIL perm_eq: 호출자 값 소실"; exit 1; }
run_case perm_space 0 -n x --permission-mode plan
[[ $(grep -o -- '--permission-mode' "$T/claude.args" | wc -l | tr -d ' ') == 1 ]] || { echo "FAIL perm_space: 중복 주입: $(cat "$T/claude.args")"; exit 1; }
run_case perm_default 0 -n x
grep -q -- '--permission-mode auto' "$T/claude.args" || { echo "FAIL perm_default: 기본 auto 미주입: $(cat "$T/claude.args")"; exit 1; }
# 접두만 같은 다른 플래그는 매칭하지 않는다
run_case perm_prefix 0 -n x --permission-mode-extra=1
grep -q -- '--permission-mode auto' "$T/claude.args" || { echo "FAIL perm_prefix: 접두만 같은 플래그를 지정으로 오인: $(cat "$T/claude.args")"; exit 1; }
echo "PASS test_bot_up_devchannel"
