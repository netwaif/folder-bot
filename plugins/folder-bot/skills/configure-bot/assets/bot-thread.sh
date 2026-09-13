#!/usr/bin/env bash
# 폴더 봇 스레드 라이브 뷰 — 디스코드 스레드마다 독립 Claude 세션을 봇 tmux 세션의 창으로 띄우고,
# 메인 봇이 세션 간 메시지(SendMessage)로 원문을 넘긴다. 답변은 Stop 훅(bot-thread-stop)이 REST로 게시.
# 설계 정본: docs/thread-live-view.md
#
# 사용:
#   bot-thread kind   <봇> <chat_id>                    → channel | thread <parent_id> | dm | unknown
#   bot-thread ensure <봇> <thread_id>                  → 창·세션 보장 후 SendMessage 대상 세션명 출력
#   bot-thread post   <봇> <thread_id> <텍스트|-> [--file <경로>]   (- 는 stdin)
#   bot-thread open   <봇> <channel_id> <이름> [message_id]         → 새 스레드 ID 출력
#   bot-thread deliver <봇> <thread_id> -              → stdin 원문을 스레드 세션 pane에 붙여넣고 제출(훅이 호출)
#   bot-thread fetch-attachments <봇> <chat_id> <message_id> → 첨부를 .discord-state/inbox/<mid>/에 받고 경로 출력
#   bot-thread rotate <봇> <thread_id>                  → 새 세션 ID로 회전(옛 transcript 보존)·창 닫기 — compact 대신 세부 보존
#   bot-thread fresh  <봇> <thread_id>                  → 직전 ensure가 새 세션을 만들었으면 1 출력 후 표식 소거(라우팅 훅용)
#   bot-thread gc     <봇> [--idle-hours N] [--all]     → 유휴 창 정리(맵은 유지 — 다음 메시지에 resume)
#   bot-thread list   <봇>
#
# 정본: ~/.config/folder-bot/bots.json(폴더·세션), <폴더>/.discord-state/.env(토큰),
#       <폴더>/.discord-state/threads.json(스레드→세션 맵, 이 스크립트가 관리).
# 테스트 override: BOT_THREAD_CURL(curl 대체), BOT_THREAD_TMUX(tmux 대체), BOT_THREAD_CLAUDE(claude 대체),
#                  BOT_THREAD_READY_TIMEOUT(준비 대기 초, 기본 60), BOT_THREAD_PROJECTS(~/.claude/projects 대체),
#                  BOT_THREAD_BOTS_JSON(bots.json 대체).
set -uo pipefail

CMD="${1:-}"; BOT="${2:-}"
[[ -n "$CMD" && -n "$BOT" ]] || { sed -n '5,12p' "$0" >&2; exit 1; }
shift 2

CURL="${BOT_THREAD_CURL:-curl}"
CLAUDE_BIN="${BOT_THREAD_CLAUDE:-claude}"
PROJECTS="${BOT_THREAD_PROJECTS:-$HOME/.claude/projects}"
API="https://discord.com/api/v10"

TMUX_BIN="${BOT_THREAD_TMUX:-$(command -v tmux || true)}"
[[ -z "$TMUX_BIN" && -x /opt/homebrew/bin/tmux ]] && TMUX_BIN=/opt/homebrew/bin/tmux
[[ -z "$TMUX_BIN" && -x /usr/local/bin/tmux ]] && TMUX_BIN=/usr/local/bin/tmux

BOTS_JSON="${BOT_THREAD_BOTS_JSON:-$HOME/.config/folder-bot/bots.json}"
read -r FOLDER SESSION < <(python3 - "$BOTS_JSON" "$BOT" <<'EOF'
import json, sys
try:
    b = json.load(open(sys.argv[1]))[sys.argv[2]]
except (OSError, KeyError, ValueError):
    print("", ""); sys.exit(0)
print(b["folder"], b.get("session", sys.argv[2] + "-bot"))
EOF
)
[[ -n "$FOLDER" ]] || { echo "오류: bots.json에 봇 없음: $BOT" >&2; exit 1; }
STATE="$FOLDER/.discord-state"
MAP="$STATE/threads.json"
TOKEN=$(sed -n 's/^DISCORD_BOT_TOKEN=//p' "$STATE/.env" 2>/dev/null | head -1)

log() { echo "[$(date '+%F %T')] bot-thread $*" >&2; }

api() {  # api <METHOD> <path> [curl args...]
  local method="$1" path="$2"; shift 2
  [[ -n "$TOKEN" ]] || { echo "오류: 토큰 없음 — $STATE/.env (pair 먼저)" >&2; return 1; }
  "$CURL" -sS -X "$method" -H "Authorization: Bot $TOKEN" "$@" "$API$path"
}

# 맵 조작은 전부 python3 (bash로 JSON 만지지 않는다)
map_get() {  # map_get <thread_id> <field>
  python3 - "$MAP" "$1" "$2" <<'EOF'
import json, sys
try: print(json.load(open(sys.argv[1])).get(sys.argv[2], {}).get(sys.argv[3], ""))
except (OSError, ValueError): print("")
EOF
}
map_set() {  # map_set <thread_id> key=value ...
  python3 - "$MAP" "$@" <<'EOF'
import json, sys, os, datetime
p, tid, kvs = sys.argv[1], sys.argv[2], sys.argv[3:]
try: m = json.load(open(p))
except (OSError, ValueError): m = {}
e = m.setdefault(tid, {})
for kv in kvs:
    k, v = kv.split("=", 1); e[k] = v
e["last"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
e.setdefault("created", e["last"])
os.makedirs(os.path.dirname(p), exist_ok=True)
tmp = p + ".tmp"; json.dump(m, open(tmp, "w"), ensure_ascii=False, indent=2); os.replace(tmp, p)
EOF
}

short_of() { local id="$1"; echo "${id: -6}"; }

# pane 루트 PID의 자손 트리에 claude(네이티브·node 런처·셸 래퍼 어느 형태든)가 있는가
pane_has_claude() {
  local root="$1"
  ps -axo pid=,ppid=,args= 2>/dev/null | python3 -c '
import sys
root = sys.argv[1]; rows = []
for line in sys.stdin:
    parts = line.split(None, 2)
    if len(parts) >= 2:
        rows.append((parts[0], parts[1], parts[2] if len(parts) > 2 else ""))
kids = {}
for pid, ppid, args in rows:
    kids.setdefault(ppid, []).append(pid)
ids, todo = {root}, [root]
while todo:
    for c in kids.get(todo.pop(), []):
        if c not in ids:
            ids.add(c); todo.append(c)
def is_claude(args):
    toks = args.split()
    base = lambda t: t.rsplit("/", 1)[-1]
    return any(base(t) == "claude" for t in toks[:3])
sys.exit(0 if any(pid in ids and is_claude(args) for pid, _, args in rows) else 1)
' "$root"
}
transcript_of() { find "$PROJECTS" -maxdepth 2 -name "$1.jsonl" 2>/dev/null | head -1; }

# 스레드 창의 "세션 pane"을 정한다 — 창 타깃(<세션>:t<short>)은 tmux가 활성 pane으로 해석하므로
# 사용자가 창을 분할하면 붙여넣기가 새 pane으로 간다(2026-09-13 실측: 첨부·메시지가 스레드 세션에 미도착).
# 규칙: 맵의 pane(%N, 불변)이 그 창에 살아 있으면 그것 → 없으면 창 안에서 claude가 도는 pane →
# 그것도 없으면 첫 pane(pane_index 최소). 찾은 값은 맵에 기록해 다음부터 고정.
thread_pane() {  # thread_pane <thread_id> → pane_id 출력(없으면 빈 문자열, rc 1)
  local tid="$1" win="t$(short_of "$1")" want="$SESSION:t$(short_of "$1")"
  local pane; pane=$(map_get "$tid" pane)
  if [[ -n "$pane" ]]; then
    local at; at=$("$TMUX_BIN" display-message -p -t "$pane" '#{session_name}:#{window_name}' 2>/dev/null || true)
    [[ "$at" == "$want" ]] && { echo "$pane"; return 0; }
  fi
  local first="" id pid
  while read -r id pid; do
    [[ -n "$first" ]] || first="$id"
    if pane_has_claude "$pid"; then map_set "$tid" "pane=$id"; echo "$id"; return 0; fi
  done < <("$TMUX_BIN" list-panes -t "$want" -F '#{pane_id} #{pane_pid}' 2>/dev/null | sort -k1.2n)
  [[ -n "$first" ]] || return 1
  map_set "$tid" "pane=$first"; echo "$first"
}

cmd_kind() {
  local cid="$1"
  local cached; cached=$(map_get "$cid" kind)
  if [[ -n "$cached" ]]; then echo "$cached"; return 0; fi
  local body; body=$(api GET "/channels/$cid") || return 1
  local kind; kind=$(python3 - "$body" <<'EOF'
import json, sys
try: d = json.loads(sys.argv[1])
except ValueError: print("unknown"); sys.exit(0)
t = d.get("type")
if t in (10, 11, 12): print(f"thread {d.get('parent_id', '')}")
elif t == 1: print("dm")
elif t is None: print("unknown")
else: print("channel")
EOF
)
  [[ "$kind" == thread* ]] && map_set "$cid" "kind=$kind" "parent_id=${kind#thread }"
  echo "$kind"
}

cmd_ensure() {
  local tid="$1"
  [[ -n "$TMUX_BIN" ]] || { echo "오류: tmux 없음" >&2; return 1; }
  "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null || { echo "오류: 봇 tmux 세션 없음: $SESSION (봇이 떠 있어야 한다)" >&2; return 1; }
  local short; short=$(short_of "$tid")
  local win="t$short" agent="$BOT-t$short"
  local sid; sid=$(map_get "$tid" session_id)
  if [[ -z "$sid" ]]; then
    sid=$(python3 -c 'import uuid; print(uuid.uuid4())')
    map_set "$tid" "session_id=$sid" "window=$win" "agent=$agent"
  fi
  # 창이 살아 있고 claude가 돌고 있으면 그대로. pane_current_command는 믿지 않는다 —
  # 컨테이너의 claude는 셸 래퍼라 sh로 보인다(2026-09-11 실측: 살아 있는 스레드 세션을 매 메시지마다
  # 죽이고 재생성함). pane PID의 자손 트리에서 claude를 찾는다(codex-discord treeHasEngine과 같은 규약).
  local pane ppid=""; pane=$(thread_pane "$tid" 2>/dev/null || true)
  [[ -n "$pane" ]] && ppid=$("$TMUX_BIN" display-message -p -t "$pane" '#{pane_pid}' 2>/dev/null || true)
  if [[ -n "$ppid" ]]; then
    if pane_has_claude "$ppid"; then
      map_set "$tid" "window=$win"; echo "$agent"; cmd_gc --quiet; return 0
    fi
    log "창 $win 있으나 claude 프로세스 없음 — 재생성"
    "$TMUX_BIN" kill-window -t "$SESSION:$win" 2>/dev/null || true
  fi
  local resume_flag="--session-id $sid" fresh=1
  [[ -n "$(transcript_of "$sid")" ]] && { resume_flag="--resume $sid"; fresh=0; }
  map_set "$tid" "fresh=$fresh"   # 새 세션이면 라우팅 훅이 [재정박] 접두를 붙인다(fresh가 소거)
  # 스레드 세션은 discord 플러그인을 끈다 — 토큰 없이 뜬 플러그인이 "토큰 필요"로 죽으면 런타임이
  # ~/.claude/mcp-needs-auth-cache.json에 전역 캐시해 메인 봇의 재기동 연결까지 막는다(2026-09-11 실측).
  # JSON을 인라인으로 넘기면 tmux 명령 문자열의 따옴표가 깨져 세션이 즉시 종료된다(2026-09-11 실측) → 파일로.
  local settings="$STATE/thread-settings.json"
  printf '%s\n' '{"enabledPlugins":{"discord@claude-plugins-official":false}}' > "$settings"
  local inner="cd '$FOLDER'; export DISCORD_THREAD_ID='$tid' DISCORD_BOT_NAME='$BOT'; exec $CLAUDE_BIN -n '$agent' --permission-mode auto --settings '$settings' $resume_flag"
  local new_pane
  new_pane=$("$TMUX_BIN" new-window -d -P -F '#{pane_id}' -t "$SESSION" -n "$win" -c "$FOLDER" "bash -lc \"$inner\"") || { echo "오류: 창 생성 실패" >&2; return 1; }
  map_set "$tid" "pane=$new_pane"
  log "창 생성: $SESSION:$win $new_pane ($resume_flag)"
  # 준비 대기: 입력 프롬프트(❯)가 뜰 때까지
  local t="${BOT_THREAD_READY_TIMEOUT:-60}" i=0
  while (( i < t )); do
    sleep 1; i=$((i+1))
    if "$TMUX_BIN" capture-pane -p -t "$new_pane" 2>/dev/null | grep -q '❯'; then
      map_set "$tid" "window=$win"; echo "$agent"; cmd_gc --quiet; return 0
    fi
    "$TMUX_BIN" list-panes -t "$new_pane" >/dev/null 2>&1 || { echo "오류: 스레드 세션이 바로 종료됨 — 창 $win 로그 확인" >&2; return 1; }
  done
  echo "오류: ${t}초 내 스레드 세션 미준비: $SESSION:$win" >&2; return 1
}

cmd_post() {
  local tid="$1" text="${2:-}"; shift 2 || true
  local file=""
  while [[ $# -gt 0 ]]; do case "$1" in --file) file="$2"; shift 2;; *) shift;; esac; done
  [[ "$text" == "-" ]] && text=$(cat)
  if [[ -n "$file" ]]; then
    [[ -f "$file" ]] || { echo "오류: 파일 없음: $file" >&2; return 1; }
    local payload; payload=$(python3 -c 'import json,sys; print(json.dumps({"content": sys.argv[1][:2000]}))' "$text")
    api POST "/channels/$tid/messages" -F "payload_json=$payload" -F "files[0]=@$file" >/dev/null || return 1
    return 0
  fi
  [[ -n "$text" ]] || return 0
  # 2000자 청크 — 줄 경계 우선
  python3 - "$text" <<'EOF' | while IFS= read -r chunk_json; do
import json, sys
t = sys.argv[1]; out = []
while t:
    if len(t) <= 2000: out.append(t); break
    cut = t.rfind("\n", 0, 2000); cut = cut if cut > 1000 else 2000
    out.append(t[:cut]); t = t[cut:].lstrip("\n")
for c in out: print(json.dumps({"content": c}, ensure_ascii=False))
EOF
    api POST "/channels/$tid/messages" -H "Content-Type: application/json" -d "$chunk_json" >/dev/null || return 1
  done
}

cmd_open() {
  local cid="$1" name="$2" mid="${3:-}"
  local body payload; payload=$(python3 -c 'import json,sys; print(json.dumps({"name": sys.argv[1][:100], "type": 11, "auto_archive_duration": 1440}))' "$name")
  if [[ -n "$mid" ]]; then
    body=$(api POST "/channels/$cid/messages/$mid/threads" -H "Content-Type: application/json" -d "$payload") || return 1
  else
    body=$(api POST "/channels/$cid/threads" -H "Content-Type: application/json" -d "$payload") || return 1
  fi
  local tid; tid=$(python3 -c 'import json,sys; d=json.loads(sys.argv[1]); print(d.get("id",""))' "$body")
  [[ -n "$tid" ]] || { echo "오류: 스레드 생성 실패: $body" >&2; return 1; }
  map_set "$tid" "kind=thread $cid" "parent_id=$cid" "name=$name"
  echo "$tid"
}

# 스레드 pane에 원문 붙여넣기 — codex-discord pasteToPane과 같은 규약:
# (1) 제어문자 제거(bracketed paste 종료 마커 조기 종료 방지, 개행·탭 보존) (2) paste-buffer -p로 본문
# (3) 잠시 후 Enter 별도 전송(텍스트 처리 전 Enter가 닿으면 제출되지 않는다).
cmd_deliver() {
  local tid="$1" text="${2:-}"
  [[ "$text" == "-" || -z "$text" ]] && text=$(cat)
  [[ -n "$TMUX_BIN" ]] || { echo "오류: tmux 없음" >&2; return 1; }
  local win="t$(short_of "$tid")" target
  target=$(thread_pane "$tid") || { echo "오류: 스레드 창 없음: $SESSION:$win (ensure 먼저)" >&2; return 1; }
  local clean; clean=$(printf '%s' "$text" | LC_ALL=C tr -d '\000-\010\013-\037\177')
  local buf="bot-thread-$$-$RANDOM"
  "$TMUX_BIN" set-buffer -b "$buf" -- "$clean" || return 1
  "$TMUX_BIN" paste-buffer -p -d -b "$buf" -t "$target" || return 1
  local n=${#clean}; local ms=$(( 300 + (n / 50 > 800 ? 800 : n / 50) ))
  python3 -c "import time; time.sleep($ms/1000)"
  "$TMUX_BIN" send-keys -t "$target" Enter
  # 제출 확인: 입력줄에 본문 첫 줄이 남아 있으면 Enter 재전송(최대 3회)
  local first; first=$(printf '%s' "$clean" | head -1 | cut -c1-40)
  for _ in 1 2 3; do
    sleep 1
    if "$TMUX_BIN" capture-pane -p -t "$target" | grep -E '^❯ ' | tail -1 | grep -qF -- "$first"; then
      "$TMUX_BIN" send-keys -t "$target" Enter
    else
      break
    fi
  done
  map_set "$tid" "window=$win"
  return 0
}

cmd_fetch_attachments() {
  local cid="$1" mid="$2"
  local body; body=$(api GET "/channels/$cid/messages/$mid") || return 1
  local dir="$STATE/inbox/$mid"; mkdir -p "$dir"
  python3 - "$body" "$dir" <<'EOF2' | while IFS=$'\t' read -r url name; do
import json, sys, re
d = json.loads(sys.argv[1])
for a in d.get("attachments", []):
    name = re.sub(r"[^\w.\-가-힣]", "_", a.get("filename") or a.get("id"))
    print(a["url"] + "\t" + name)
EOF2
    "$CURL" -sSL -o "$dir/$name" "$url" && echo "$dir/$name"
  done
}

# compact 대신 세부 보존: 스레드 세션이 threads/<id>/SESSION.md를 갱신한 뒤 호출한다.
# 새 uuid를 배정하고(옛 세션 ID는 previous에 보관, transcript는 그대로) 창을 닫는다.
# 다음 메시지에 라우팅 훅이 새 세션을 띄우고 "[재정박]" 접두로 SESSION.md를 먼저 읽게 한다.
cmd_rotate() {
  local tid="$1"
  local old; old=$(map_get "$tid" session_id)
  [[ -n "$old" ]] || { echo "오류: 등록되지 않은 스레드: $tid" >&2; return 1; }
  local new; new=$(python3 -c 'import uuid; print(uuid.uuid4())')
  python3 - "$MAP" "$tid" "$old" "$new" <<'EOF'
import json, sys
p, tid, old, new = sys.argv[1:5]
m = json.load(open(p)); e = m[tid]
e.setdefault("previous", []).append(old); e["session_id"] = new; e["fresh"] = "1"
json.dump(m, open(p, "w"), ensure_ascii=False, indent=2)
EOF
  mkdir -p "$FOLDER/threads/$tid"
  local win="t$(short_of "$tid")"
  [[ -n "$TMUX_BIN" ]] && "$TMUX_BIN" kill-window -t "$SESSION:$win" 2>/dev/null
  log "회전: 스레드 $tid 세션 $old → $new (창 $win 닫음, 다음 메시지에 재정박)"
  echo "$new"
}

cmd_fresh() {
  local tid="$1"
  local f; f=$(map_get "$tid" fresh)
  if [[ "$f" == "1" ]]; then map_set "$tid" "fresh=0"; echo 1; else echo 0; fi
}

cmd_gc() {
  local hours=6 all="" quiet=""
  while [[ $# -gt 0 ]]; do case "$1" in --idle-hours) hours="$2"; shift 2;; --all) all=1; shift;; --quiet) quiet=1; shift;; *) shift;; esac; done
  [[ -n "$TMUX_BIN" ]] || return 0
  "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null || return 0
  local now; now=$(date +%s)
  "$TMUX_BIN" list-windows -t "$SESSION" -F '#{window_name}' 2>/dev/null | grep -E '^t[0-9]{6}$' | while read -r win; do
    local short="${win#t}"
    local tid sid; tid=$(python3 - "$MAP" "$short" <<'EOF'
import json, sys
try: m = json.load(open(sys.argv[1]))
except (OSError, ValueError): m = {}
print(next((k for k, v in m.items() if v.get("window") == "t" + sys.argv[2]), ""))
EOF
)
    sid=$(map_get "$tid" session_id)
    local tr; tr=$(transcript_of "$sid")
    local age=0
    if [[ -n "$tr" ]]; then
      local m; m=$(stat -c %Y "$tr" 2>/dev/null || stat -f %m "$tr" 2>/dev/null || echo "$now")
      age=$(( (now - m) / 3600 ))
    fi
    if [[ -n "$all" || -z "$tid" || $age -ge $hours ]]; then
      "$TMUX_BIN" kill-window -t "$SESSION:$win" 2>/dev/null && [[ -z "$quiet" ]] && log "창 정리: $win (유휴 ${age}h, 스레드 ${tid:-미등록})"
    fi
  done
  return 0
}

cmd_list() {
  python3 - "$MAP" "$SESSION" "$TMUX_BIN" <<'EOF'
import json, sys, subprocess
p, session, tmux = sys.argv[1:4]
try: m = json.load(open(p))
except (OSError, ValueError): m = {}
live = set()
if tmux:
    r = subprocess.run([tmux, "list-windows", "-t", session, "-F", "#{window_name}"], capture_output=True, text=True)
    live = set(r.stdout.split())
print("thread_id\tsession_id\twindow\tlive\tlast")
for tid, e in m.items():
    w = e.get("window", "")
    print(f"{tid}\t{e.get('session_id','')}\t{w}\t{'yes' if w in live else 'no'}\t{e.get('last','')}")
EOF
}

case "$CMD" in
  kind)   cmd_kind "$@" ;;
  ensure) cmd_ensure "$@" ;;
  post)   cmd_post "$@" ;;
  open)   cmd_open "$@" ;;
  deliver) cmd_deliver "$@" ;;
  fetch-attachments) cmd_fetch_attachments "$@" ;;
  rotate) cmd_rotate "$@" ;;
  fresh)  cmd_fresh "$@" ;;
  gc)     cmd_gc "$@" ;;
  list)   cmd_list ;;
  *) echo "알 수 없는 명령: $CMD" >&2; exit 1 ;;
esac
