"""bot-start 스킬(botstart.py) — bots.json 정본에서 봇을 찾아 켜고·끄고·재시작·상태 점검.

실물 무접촉: HOME은 tmp, tmux·bot-restart·agentlayer는 스텁(PATH 맨 앞). launchctl·systemctl은 conftest가 차단한다.
"""
import json, os, re, subprocess, sys
import pytest
from pathlib import Path

ROOT = Path(__file__).parent.parent / "plugins/folder-bot/skills"
BOTCTL = ROOT / "configure-bot/generator/botctl.py"
SKILL_DIR = ROOT / "bot-start"
BOTSTART = SKILL_DIR / "scripts/botstart.py"

TMUX_STUB = r'''#!/usr/bin/env bash
S="$STUB_DIR/sessions"; touch "$S"
echo "tmux $*" >> "$STUB_DIR/tmux.log"
cmd="$1"; shift
target=""; args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  case "${args[i]}" in -t|-s) target="${args[i+1]}";; esac
done
target="${target#=}"; sess="${target%%:*}"
case "$cmd" in
  has-session) grep -qx "$sess" "$S" ;;
  new-session)
    echo "$sess" >> "$S"
    if [[ -n "${STUB_MCP_DIR:-}" ]]; then
      mkdir -p "$STUB_MCP_DIR"; echo "{\"m\":\"${STUB_MCP_LINE:-Successfully connected (transport: stdio)}\"}" > "$STUB_MCP_DIR/new.jsonl"
    fi ;;
  kill-session) grep -vx "$sess" "$S" > "$S.n"; mv "$S.n" "$S" ;;
  display-message)
    if [[ -n "$target" ]]; then grep -qx "$sess" "$S" || exit 1; echo "${STUB_PANE_CMD:-claude}"; else echo "${STUB_SELF:-}"; fi ;;
  list-windows) grep -qx "$sess" "$S" || exit 1; printf '2.1.0\nt123456\n' ;;
  list-sessions) cat "$S" ;;
esac
'''

RESTART_STUB = r'''#!/usr/bin/env bash
echo "bot-restart $*" >> "$STUB_DIR/restart.log"
mkdir -p "$HOME/.claude/logs"
{ echo "[bot-restart t] === $1 재시작 시작 ==="
  echo "[bot-restart t] 판정: ${STUB_VERDICT:-✅ Successfully connected — '이어서하자'로 재정박}"
  echo "[bot-restart t] === $1 재시작 종료 ==="; } >> "$HOME/.claude/logs/bot-restart.log"
echo "재시작 예약됨: $1"
'''


class Env:
    def __init__(self, tmp_path):
        self.home = tmp_path
        self.stub = tmp_path / "stub"; self.stub.mkdir()
        self.bin = tmp_path / "stubbin"; self.bin.mkdir()
        self._write(self.bin / "tmux", TMUX_STUB)
        self.extra = {}

    @staticmethod
    def _write(p, text):
        p.write_text(text); p.chmod(0o755)

    def env(self, **kw):
        e = dict(os.environ, HOME=str(self.home), STUB_DIR=str(self.stub),
                 PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                 BOTSTART_TIMEOUT="3", BOTSTART_POLL="0.2")
        e.pop("TMUX", None)
        e.update(self.extra); e.update(kw)
        return e

    def botctl(self, *args, **kw):
        return subprocess.run([sys.executable, str(BOTCTL), *args], capture_output=True, text=True, env=self.env(**kw))

    def add(self, name, folder_name=None, session=None):
        folder = self.home / "work" / (folder_name or name); folder.mkdir(parents=True, exist_ok=True)
        (self.home / "Library/LaunchAgents").mkdir(parents=True, exist_ok=True)
        r = self.botctl("add", "--name", name, "--folder", str(folder), "--session", session or f"{name}-bot",
                        "--no-directive-block")
        assert r.returncode == 0, r.stderr
        self._write(self.home / ".local/bin/bot-restart", RESTART_STUB)   # 실물 대신 스텁
        return folder

    def live(self, *sessions):
        (self.stub / "sessions").write_text("".join(s + "\n" for s in sessions))

    def sessions(self):
        p = self.stub / "sessions"
        return p.read_text().split() if p.exists() else []

    def log(self, name):
        p = self.stub / name
        return p.read_text() if p.exists() else ""

    def run(self, *args, **kw):
        return subprocess.run([sys.executable, str(BOTSTART), *args], capture_output=True, text=True, env=self.env(**kw))


@pytest.fixture
def e(tmp_path):
    return Env(tmp_path)


def test_list_reads_bots_json_and_shows_state(e):
    e.add("alpha"); e.add("beta")
    e.live("alpha-bot")
    r = e.run("list")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    a = next(l for l in lines if "alpha" in l); b = next(l for l in lines if "beta" in l)
    assert a.startswith("✅") and "alpha-bot" in a and "claude" in a
    assert b.startswith("❌") and "beta-bot" in b
    assert len(lines) == 2   # bots.json에 있는 봇만 — 내장 목록 없음


def test_list_without_bots_json_guides_to_configure(e):
    r = e.run("list")
    assert r.returncode == 0 and "등록된 봇 없음" in r.stdout and "configure-bot" in r.stdout


def test_resolve_by_name_session_folder_and_noise_words(e):
    e.add("alpha", folder_name="Projects-X")
    e.live("alpha-bot")
    for q in (["alpha"], ["alpha-bot"], ["Projects-X"], ["projects-x", "폴더", "bot"], ["알파가", "아닌", "alpha", "봇"][2:]):
        r = e.run("status", *q)
        assert r.returncode == 0 and "alpha-bot" in r.stdout, (q, r.stdout)


def test_resolve_aliases_from_optional_file(e):
    e.add("alpha")
    r = e.run("status", "알파")
    assert r.returncode == 3
    (e.home / ".config/folder-bot/aliases.json").write_text(json.dumps({"alpha": ["알파", "첫째"]}, ensure_ascii=False))
    r = e.run("status", "알파 봇")
    assert r.returncode == 0 and "alpha-bot" in r.stdout, r.stdout


def test_ambiguous_or_missing_returns_3_with_candidates(e):
    e.add("alpha"); e.add("alphabet")
    r = e.run("start", "alph")
    assert r.returncode == 3 and "alpha" in r.stdout and "alphabet" in r.stdout and "여러 개" in r.stdout
    r = e.run("start", "zzz")
    assert r.returncode == 3 and "없음" in r.stdout and "후보" in r.stdout
    assert e.log("tmux.log").count("new-session") == 0


def test_start_when_off_uses_botctl_start_and_judges_mcp_log(e):
    folder = e.add("alpha")
    mcp = (e.home / "Library/Caches/claude-cli-nodejs" / re.sub(r"[/.]", "-", str(folder))
           / "mcp-logs-plugin-discord-discord")
    r = e.run("start", "alpha", STUB_MCP_DIR=str(mcp))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "new-session -d -s alpha-bot" in e.log("tmux.log")
    assert "✅" in r.stdout and "Successfully connected" in r.stdout
    assert e.log("restart.log") == ""   # 꺼진 봇은 정본 기동 경로(botctl start)


def test_start_reports_connection_failure_and_timeout(e):
    folder = e.add("alpha")
    mcp = (e.home / "Library/Caches/claude-cli-nodejs" / re.sub(r"[/.]", "-", str(folder))
           / "mcp-logs-plugin-discord-discord")
    r = e.run("start", "alpha", STUB_MCP_DIR=str(mcp), STUB_MCP_LINE="Connection failed: bad token")
    assert r.returncode == 1 and "❌" in r.stdout and "Connection failed" in r.stdout
    e.live()
    (mcp / "new.jsonl").unlink()
    r = e.run("start", "alpha")   # 로그가 안 생김
    assert r.returncode == 1 and "⏱" in r.stdout, r.stdout


def test_start_when_running_is_noop_unless_restart(e):
    e.add("alpha"); e.live("alpha-bot")
    r = e.run("start", "alpha")
    assert r.returncode == 0 and "이미 실행 중" in r.stdout
    assert e.log("restart.log") == "" and "new-session" not in e.log("tmux.log")
    r = e.run("start", "alpha", "--restart")
    assert r.returncode == 0, r.stdout + r.stderr
    assert e.log("restart.log").strip() == "bot-restart alpha-bot"
    assert "판정: ✅" in r.stdout


def test_dead_pane_counts_as_off(e):
    e.add("alpha"); e.live("alpha-bot")
    r = e.run("status", "alpha", STUB_PANE_CMD="zsh")   # 세션은 있는데 엔진이 죽고 셸만 남음
    assert r.stdout.startswith("❌") and "pane=zsh" in r.stdout
    r = e.run("start", "alpha", STUB_PANE_CMD="zsh")
    assert e.log("restart.log").strip() == "bot-restart alpha-bot"   # 세션이 있으니 pane 교체 경로


def test_stop_exact_match_only_and_kills_session(e):
    e.add("alpha"); e.live("alpha-bot", "other")
    r = e.run("stop", "alph")
    assert r.returncode == 3 and "alpha-bot" in e.sessions()
    r = e.run("stop", "alpha")
    assert r.returncode == 0, r.stdout + r.stderr
    assert e.sessions() == ["other"] and "종료함" in r.stdout
    r = e.run("stop", "alpha")
    assert r.returncode == 0 and "이미 꺼져 있음" in r.stdout


def test_stop_refuses_own_session_without_force(e):
    e.add("alpha"); e.live("alpha-bot")
    r = e.run("stop", "alpha", TMUX="/tmp/x,1,0", STUB_SELF="alpha-bot")
    assert r.returncode == 4 and "alpha-bot" in e.sessions() and "--force" in r.stdout
    r = e.run("stop", "alpha", "--force", TMUX="/tmp/x,1,0", STUB_SELF="alpha-bot")
    assert r.returncode == 0 and e.sessions() == []


def test_stop_refuses_busy_bot_when_agentlayer_present(e):
    e.add("alpha"); e.add("beta"); e.live("alpha-bot", "beta-bot")
    e._write(e.bin / "agentlayer", '''#!/usr/bin/env bash
if [[ "$1 $2" == "task list" ]]; then echo '[{"task_id":"T-1","session":"beta-bot","state":"assigned"}]'; exit 0; fi
printf 'STATE   AGENT   SESSION   TASK\\n[WORK]  claude  alpha-bot ⌁  작업 중\\n[DONE]  claude  beta-bot ⌁  끝\\n'
''')
    r = e.run("stop", "alpha")
    assert r.returncode == 5 and "작업 중" in r.stdout and "alpha-bot" in e.sessions()
    r = e.run("stop", "beta")
    assert r.returncode == 5 and "T-1" in r.stdout
    r = e.run("stop", "alpha", "--force")
    assert r.returncode == 0 and "alpha-bot" not in e.sessions()


def test_unknown_option_rejected(e):
    e.add("alpha"); e.live("alpha-bot")
    r = e.run("stop", "alpha", "--now")
    assert r.returncode == 2 and "알 수 없는 옵션" in r.stdout and "alpha-bot" in e.sessions()


def _fake_bridge(home):
    bridge = home / "bridge"
    (bridge / "scripts").mkdir(parents=True); (bridge / "logs").mkdir()
    (bridge / "scripts/tui-up.sh").write_text("#!/bin/bash\necho ok\n")
    (bridge / "scripts/tui-restart.sh").write_text(
        '#!/bin/bash\necho "tui-restart $*" >> "$STUB_DIR/tui.log"\nd="$(cd "$(dirname "$0")/.." && pwd)"\n'
        '{ echo "=== cx-bot 재시작 시작 ($1) ==="; echo "판정: ✅ 준비 완료"; echo "=== cx-bot 재시작 종료 ==="; } >> "$d/logs/tui-restart.log"\n')
    for f in (bridge / "scripts").iterdir():
        f.chmod(0o755)
    return bridge


def test_bridge_bot_status_and_restart(e):
    bridge = _fake_bridge(e.home)
    folder = e.home / "work/cx"; folder.mkdir(parents=True)
    (e.home / "Library/LaunchAgents").mkdir(parents=True)
    e._write(e.bin / "node", "#!/bin/bash\n")
    r = e.botctl("add", "--name", "cx", "--folder", str(folder), "--session", "cx-bot", "--engine", "codex",
                 "--bridge-dir", str(bridge), "--no-directive-block")
    assert r.returncode == 0, r.stderr
    e.live("cx-bot")
    r = e.run("status", "cx", STUB_PANE_CMD="codex")
    assert r.stdout.startswith("❌") and "데몬=죽음" in r.stdout          # TUI는 살아도 데몬이 없으면 ❌
    (bridge / "data-cx/daemon.pid").write_text(str(os.getpid()))
    r = e.run("status", "cx", STUB_PANE_CMD="codex")
    assert r.stdout.startswith("✅") and f"데몬={os.getpid()}" in r.stdout
    r = e.run("start", "cx", "--restart", STUB_PANE_CMD="codex")
    assert r.returncode == 0, r.stdout + r.stderr
    assert e.log("tui.log").strip() == "tui-restart .env.cx" and "판정: ✅" in r.stdout


def test_no_personal_values_in_skill():
    text = BOTSTART.read_text() + (SKILL_DIR / "SKILL.md").read_text()
    for banned in ("/Users/", "ai-folder", "VSCodeWorkspace", "codex-live", "gemini-live", "textreview", "텍스트검수",
                   "비서실장", "company-bot", "총괄", "search-youtube", "haendaechacne", "sendmanual", "crosscheck",
                   "claude-discord", "슬라이드", "모션", "제미나이"):
        assert banned not in text, banned
    fm = (SKILL_DIR / "SKILL.md").read_text().split("---")[1]
    assert re.search(r"^name: bot-start$", fm, re.M) and "description:" in fm
