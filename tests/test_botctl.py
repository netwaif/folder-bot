import json, os, subprocess, sys
from pathlib import Path

BOTCTL = Path(__file__).parent.parent / "plugins/folder-bot/skills/configure-bot/generator/botctl.py"


def run(env_home, *args):
    env = dict(os.environ, HOME=str(env_home))
    return subprocess.run([sys.executable, str(BOTCTL), *args],
                          capture_output=True, text=True, env=env)


def test_add_writes_bots_json(tmp_path):
    folder = tmp_path / "work" / "collab"; folder.mkdir(parents=True)
    r = run(tmp_path, "add", "--name", "collab", "--folder", str(folder),
            "--session", "collab-bot", "--no-autostart", "--no-directive-block")
    assert r.returncode == 0, r.stderr
    data = json.loads((tmp_path / ".config/folder-bot/bots.json").read_text())
    assert data["collab"]["session"] == "collab-bot"
    assert data["collab"]["folder"] == str(folder)


def test_add_is_idempotent(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    for _ in range(2):
        r = run(tmp_path, "add", "--name", "b", "--folder", str(folder),
                "--session", "b-bot", "--no-autostart", "--no-directive-block")
        assert r.returncode == 0
    data = json.loads((tmp_path / ".config/folder-bot/bots.json").read_text())
    assert list(data.keys()) == ["b"]


def test_remove_deletes_entry(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart", "--no-directive-block")
    r = run(tmp_path, "remove", "--name", "b", "--keep-state")
    assert r.returncode == 0
    data = json.loads((tmp_path / ".config/folder-bot/bots.json").read_text())
    assert "b" not in data


def test_resolve_defaults(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart", "--no-directive-block")
    r = run(tmp_path, "list")
    assert "b-bot" in r.stdout            # session
    assert ".discord-state" in r.stdout   # state_dir 기본값 표시


def test_install_scripts_idempotent(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    r = run(tmp_path, "add", "--name", "b", "--folder", str(folder),
            "--session", "b-bot", "--no-autostart", "--no-directive-block")
    assert r.returncode == 0
    bin_dir = tmp_path / ".local/bin"
    assert (bin_dir / "bot-up").exists() and (bin_dir / "bot-restart").exists()
    assert os.access(bin_dir / "bot-up", os.X_OK)
    mtime = (bin_dir / "bot-up").stat().st_mtime
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart", "--no-directive-block")
    assert (bin_dir / "bot-up").stat().st_mtime == mtime  # 내용 동일 → 재쓰기 없음


import plistlib


def test_plist_shape_matches_bot_restart_parser(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    (tmp_path / "Library/LaunchAgents").mkdir(parents=True)
    r = run(tmp_path, "add", "--name", "b", "--folder", str(folder),
            "--session", "b-bot", "--no-directive-block")
    assert r.returncode == 0, r.stderr
    p = tmp_path / "Library/LaunchAgents/com.folder-bot.b.plist"
    a = plistlib.loads(p.read_bytes())["ProgramArguments"]
    # bot-restart.sh 파서 전제: '-s' 다음이 세션명, 마지막 인자가 CMD
    assert a[a.index("-s") + 1] == "b-bot" and "new-session" in a
    cmd = a[-1]
    assert f"cd {folder}" in cmd and "DISCORD_STATE_DIR=" in cmd
    assert "/.local/bin/bot-up" in cmd and "--channels plugin:discord@claude-plugins-official" in cmd
    assert "-n b-bot" in cmd and "--remote-control b-bot" in cmd


def test_no_autostart_removes_plist(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    (tmp_path / "Library/LaunchAgents").mkdir(parents=True)
    run(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
        "--no-directive-block")
    run(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
        "--no-directive-block", "--no-autostart")
    assert not (tmp_path / "Library/LaunchAgents/com.folder-bot.b.plist").exists()


def test_directive_block_nondestructive(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    original = "# 기존 내용\n\n소중한 규칙.\n"
    (folder / "CLAUDE.md").write_text(original)
    (folder / "SESSION.md").write_text("세션 기록\n")
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart")
    text = (folder / "CLAUDE.md").read_text()
    assert original in text and "<!-- store:discord-bot:start -->" in text
    assert (folder / "SESSION.md").read_text() == "세션 기록\n"  # SESSION.md 무접촉
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart")
    assert text == (folder / "CLAUDE.md").read_text()            # 멱등
    run(tmp_path, "remove", "--name", "b", "--keep-state")
    assert (folder / "CLAUDE.md").read_text() == original        # 블록 외 diff 0


def test_pair_writes_state(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart", "--no-directive-block")
    r = run(tmp_path, "pair", "--name", "b", "--token", "tok123",
            "--user-id", "111", "--channel-id", "222")
    assert r.returncode == 0, r.stderr
    st = folder / ".discord-state"
    assert (st / ".env").read_text() == "DISCORD_BOT_TOKEN=tok123\n"
    assert oct((st / ".env").stat().st_mode)[-3:] == "600"
    acc = json.loads((st / "access.json").read_text())
    assert acc["dmPolicy"] == "allowlist" and acc["allowFrom"] == ["111"]
    assert acc["groups"]["222"] == {"requireMention": False, "allowFrom": ["111"]}
    assert (st / "inbox").is_dir()


def test_pair_refuses_overwrite(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart", "--no-directive-block")
    run(tmp_path, "pair", "--name", "b", "--token", "tok1", "--user-id", "1", "--channel-id", "2")
    r = run(tmp_path, "pair", "--name", "b", "--token", "tok2", "--user-id", "1", "--channel-id", "2")
    assert r.returncode != 0            # 기존 페어링 보호 — --force 없이 덮지 않음
    assert "tok1" in (folder / ".discord-state/.env").read_text()


def test_start_dry_run_prints_command(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart", "--no-directive-block")
    r = run(tmp_path, "start", "--name", "b", "--dry-run")
    assert r.returncode == 0
    assert "new-session" in r.stdout and "b-bot" in r.stdout and "bot-up" in r.stdout


def test_doctor_reports_missing_pairing(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    (tmp_path / "Library/LaunchAgents").mkdir(parents=True)
    run(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
        "--no-directive-block")
    r = run(tmp_path, "doctor")
    assert "페어링" in r.stdout and "[WARN]" in r.stdout   # .env 없음 → WARN


def test_allow_project_mcp_merges_settings(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    (folder / ".claude").mkdir()
    (folder / ".claude/settings.local.json").write_text('{"enabledMcpjsonServers": ["gemini"]}')
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart", "--no-directive-block",
        "--allow-project-mcp")
    data = json.loads((folder / ".claude/settings.local.json").read_text())
    assert data["enableAllProjectMcpServers"] is True
    assert data["enabledMcpjsonServers"] == ["gemini"]   # 기존 키 보존


def test_doctor_warns_untrusted_workspace(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    (tmp_path / "Library/LaunchAgents").mkdir(parents=True)
    run(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
        "--no-directive-block")
    r = run(tmp_path, "doctor")
    assert "미신뢰" in r.stdout                      # ~/.claude.json 없음 → 미신뢰 WARN


def _fake_bridge(tmp_path):
    bridge = tmp_path / "bridge"
    (bridge / "scripts").mkdir(parents=True)
    (bridge / "scripts/tui-up.sh").write_text("#!/bin/bash\necho ok\n")
    (bridge / "logs").mkdir()
    return bridge


def test_codex_add_creates_env_and_plists(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    bridge = _fake_bridge(tmp_path)
    (tmp_path / "Library/LaunchAgents").mkdir(parents=True)
    r = run(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
            "--engine", "codex", "--bridge-dir", str(bridge))
    assert r.returncode == 0, r.stderr
    env = (bridge / ".env.b").read_text()
    assert f"CODEX_WORKDIR={folder}" in env and "TUI_PANE=b-bot:0.0" in env
    assert "TUI_TRIGGER_GATE=off" in env and "DISCORD_TOKEN=\n" in env
    assert (bridge / "data-b").is_dir()
    la = tmp_path / "Library/LaunchAgents"
    assert (la / "com.codex-discord.b.plist").exists()
    assert (la / "com.codex-discord.b-tui.plist").exists()
    # 지침 블록은 AGENTS.md로, 브리지 경로가 렌더돼 들어간다
    agents = (folder / "AGENTS.md").read_text()
    assert "<!-- store:discord-bot:start -->" in agents
    assert f"{bridge}/scripts/tui-restart.sh .env.b" in agents
    assert not (folder / "CLAUDE.md").exists()


def test_codex_pair_fills_env_lines(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    bridge = _fake_bridge(tmp_path)
    (tmp_path / "Library/LaunchAgents").mkdir(parents=True)
    run(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
        "--engine", "codex", "--bridge-dir", str(bridge), "--no-directive-block")
    r = run(tmp_path, "pair", "--name", "b", "--token", "tok9",
            "--user-id", "11", "--channel-id", "22")
    assert r.returncode == 0, r.stderr
    env = (bridge / ".env.b").read_text()
    assert "DISCORD_TOKEN=tok9" in env and "ALLOWED_USER_IDS=11" in env
    assert "TUI_CHANNEL_ID=22" in env and "CHANNEL_IDS=22" in env
    r2 = run(tmp_path, "pair", "--name", "b", "--token", "tok10",
             "--user-id", "11", "--channel-id", "22")
    assert r2.returncode != 0 and "tok9" in (bridge / ".env.b").read_text()


def _fake_coach(tmp_path):
    """하네스 clone 위치에 usage-coach statusline 정본을 흉내낸다."""
    script = tmp_path / ".local/share/discord-harness/repos/usage-coach/scripts/statusline-command.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/bash\n")
    return script


def test_add_injects_statusline_when_coach_present(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    script = _fake_coach(tmp_path)
    (folder / ".claude").mkdir()
    (folder / ".claude/settings.local.json").write_text('{"enabledMcpjsonServers": ["gemini"]}')
    r = run(tmp_path, "add", "--name", "b", "--folder", str(folder),
            "--session", "b-bot", "--no-autostart", "--no-directive-block")
    assert r.returncode == 0, r.stderr
    data = json.loads((folder / ".claude/settings.local.json").read_text())
    assert data["statusLine"] == {"type": "command", "command": f"bash {script}"}
    assert data["enabledMcpjsonServers"] == ["gemini"]   # 기존 키 보존


def test_add_skips_statusline_without_coach(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart", "--no-directive-block")
    p = folder / ".claude/settings.local.json"
    assert not p.exists()                                # 단독 설치 — 주입 없음


def test_add_preserves_user_statusline(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    _fake_coach(tmp_path)
    (folder / ".claude").mkdir()
    (folder / ".claude/settings.local.json").write_text(
        '{"statusLine": {"type": "command", "command": "my-own.sh"}}')
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart", "--no-directive-block")
    data = json.loads((folder / ".claude/settings.local.json").read_text())
    assert data["statusLine"]["command"] == "my-own.sh"  # 사용자 설정 무접촉
    r = run(tmp_path, "remove", "--name", "b", "--keep-state")
    assert r.returncode == 0
    data = json.loads((folder / ".claude/settings.local.json").read_text())
    assert data["statusLine"]["command"] == "my-own.sh"  # 회수 대상 아님


def test_remove_recovers_injected_statusline(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    _fake_coach(tmp_path)
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart", "--no-directive-block")
    # 재등록해도 주입 기록이 유지돼야 회수가 성립한다
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart", "--no-directive-block")
    run(tmp_path, "remove", "--name", "b", "--keep-state")
    data = json.loads((folder / ".claude/settings.local.json").read_text())
    assert "statusLine" not in data


def test_pair_token_file_deleted_after_success(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart", "--no-directive-block")
    tok = folder / ".bot-token"; tok.write_text("tok123\n")
    r = run(tmp_path, "pair", "--name", "b", "--token-file", str(tok),
            "--user-id", "111", "--channel-id", "222")
    assert r.returncode == 0, r.stderr
    assert (folder / ".discord-state/.env").read_text() == "DISCORD_BOT_TOKEN=tok123\n"
    assert not tok.exists()                              # 평문 잔존 금지
    assert "tok123" not in r.stdout                      # 토큰 값 화면 미출력


def test_pair_token_file_kept_on_failure(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart", "--no-directive-block")
    run(tmp_path, "pair", "--name", "b", "--token", "tok1", "--user-id", "1", "--channel-id", "2")
    tok = folder / ".bot-token"; tok.write_text("tok2\n")
    r = run(tmp_path, "pair", "--name", "b", "--token-file", str(tok),
            "--user-id", "1", "--channel-id", "2")
    assert r.returncode != 0                             # --force 없이 거부
    assert tok.exists()                                  # 실패 시 파일 보존


def _mcp_log(tmp_path, folder, text):
    import re
    mangled = re.sub(r"[/.]", "-", str(folder))
    d = tmp_path / "Library/Caches/claude-cli-nodejs" / mangled / "mcp-logs-plugin-discord-discord"
    d.mkdir(parents=True)
    (d / "2026-08-06.jsonl").write_text(text)


def test_doctor_judges_mcp_log(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    (tmp_path / "Library/LaunchAgents").mkdir(parents=True)
    run(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
        "--no-directive-block")
    _mcp_log(tmp_path, folder, '{"msg": "Connection failed"}\n')
    r = run(tmp_path, "doctor")
    assert "MCP 연결 실패" in r.stdout and r.returncode != 0
    _mcp_log_dir = tmp_path / "Library/Caches/claude-cli-nodejs"
    # 최신 파일이 성공이면 OK로 뒤집힌다
    import re as _re
    mangled = _re.sub(r"[/.]", "-", str(folder))
    (_mcp_log_dir / mangled / "mcp-logs-plugin-discord-discord" / "2026-08-07.jsonl").write_text(
        '{"msg": "Successfully connected"}\n')
    r2 = run(tmp_path, "doctor")
    assert "MCP 연결 성공" in r2.stdout


def test_codex_doctor_warns_unpaired(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    bridge = _fake_bridge(tmp_path)
    (tmp_path / "Library/LaunchAgents").mkdir(parents=True)
    run(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
        "--engine", "codex", "--bridge-dir", str(bridge), "--no-directive-block")
    r = run(tmp_path, "doctor")
    assert "토큰 없음" in r.stdout and "데몬" in r.stdout
