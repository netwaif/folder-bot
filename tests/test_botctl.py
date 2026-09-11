import json, os, shutil, subprocess, sys
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
    data = json.loads(p.read_text())                     # 단독 설치 — statusLine 주입 없음(스레드 훅만)
    assert "statusLine" not in data
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "bash ~/.local/bin/bot-thread-stop"
    assert data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == "bash ~/.local/bin/bot-thread-route"


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
    # 종료된 세션의 로그는 증거가 아니다 — 성공 기록이 있어도 판정 불가(8/6 합격 오판 실측)
    (_mcp_log_dir / mangled / "mcp-logs-plugin-discord-discord" / "2026-08-08.jsonl").write_text(
        '{"msg": "Successfully connected"}\n{"msg": "Sending SIGINT"}\n'
        '{"msg": "MCP server process exited cleanly"}\n')
    r3 = run(tmp_path, "doctor")
    assert "판정 불가" in r3.stdout and "MCP 연결 성공" not in r3.stdout


def test_codex_doctor_warns_unpaired(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    bridge = _fake_bridge(tmp_path)
    (tmp_path / "Library/LaunchAgents").mkdir(parents=True)
    run(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
        "--engine", "codex", "--bridge-dir", str(bridge), "--no-directive-block")
    r = run(tmp_path, "doctor")
    assert "토큰 없음" in r.stdout and "데몬" in r.stdout


# ---------------------------------------------------------------- 리눅스(systemd) 분기

def run_linux(env_home, *args):
    env = dict(os.environ, HOME=str(env_home), HARNESS_OS="Linux")
    return subprocess.run([sys.executable, str(BOTCTL), *args],
                          capture_output=True, text=True, env=env)


def test_guard_shim_blocks_real_calls(tmp_path):
    # conftest 안전장치 자체 검증: 실호출은 exit 99 — 표식은 여기서 바로 지워 픽스처 단언을 통과시킨다
    r = subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
    assert r.returncode == 99 and "BLOCKED" in r.stderr
    marker = Path(os.environ["PATH"].split(os.pathsep)[0]) / "CALLED"
    assert "systemctl --user daemon-reload" in marker.read_text()
    marker.unlink()


def test_linux_add_writes_unit_and_sidecars(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    r = run_linux(tmp_path, "add", "--name", "b", "--folder", str(folder),
                  "--session", "b-bot", "--no-directive-block")
    assert r.returncode == 0, r.stderr
    d = tmp_path / ".config/systemd/user"
    unit = d / "com.folder-bot.b.service"
    assert unit.exists() and not (tmp_path / "Library/LaunchAgents").exists()
    text = unit.read_text()
    assert "# folder-bot: name=b session=b-bot folder=" + str(folder) in text
    assert "Type=oneshot" in text and "RemainAfterExit=yes" in text and "KillMode=process" in text
    assert f"ExecStart=/bin/bash {d}/b-bot.up.sh" in text
    assert "kill-session -t b-bot" in text and "WantedBy=default.target" in text
    cmd = (d / "b-bot.tmux-cmd").read_text()
    assert cmd.startswith("/bin/bash -lc '") and f"cd {folder}" in cmd
    assert "DISCORD_STATE_DIR=" in cmd and "/.local/bin/bot-up -n b-bot --remote-control b-bot" in cmd
    up = d / "b-bot.up.sh"
    assert up.stat().st_mode & 0o111 and f'new-session -d -s b-bot "$(cat "{d}/b-bot.tmux-cmd")"' in up.read_text()
    assert "유닛 생성" in r.stdout
    # 멱등: 재실행은 무출력
    r2 = run_linux(tmp_path, "add", "--name", "b", "--folder", str(folder),
                   "--session", "b-bot", "--no-directive-block")
    assert "유닛 생성" not in r2.stdout


def test_linux_no_autostart_removes_unit(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    run_linux(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
              "--no-directive-block")
    r = run_linux(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
                  "--no-directive-block", "--no-autostart")
    assert r.returncode == 0, r.stderr
    d = tmp_path / ".config/systemd/user"
    assert not list(d.iterdir()), list(d.iterdir())


def test_linux_remove_leaves_nothing(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    run_linux(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
              "--no-directive-block")
    r = run_linux(tmp_path, "remove", "--name", "b")
    assert r.returncode == 0, r.stderr
    d = tmp_path / ".config/systemd/user"
    assert not list(d.iterdir()) and "유닛 제거" in r.stdout


def test_linux_start_dry_run_uses_bash(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    run_linux(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
              "--no-directive-block")
    r = run_linux(tmp_path, "start", "--name", "b", "--dry-run")
    assert r.returncode == 0 and "systemctl --user start com.folder-bot.b.service" in r.stdout
    r = run_linux(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
                  "--no-directive-block", "--no-autostart")
    r = run_linux(tmp_path, "start", "--name", "b", "--dry-run")
    assert "new-session -d -s b-bot /bin/bash -lc" in r.stdout and "/bin/zsh" not in r.stdout


def test_linux_doctor_checks_unit_and_cache_log(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    run_linux(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
              "--no-directive-block")
    (tmp_path / ".config/systemd/user/com.folder-bot.b.service").unlink()
    r = run_linux(tmp_path, "doctor", "--name", "b")
    assert r.returncode == 1 and "유닛 없음" in r.stdout
    run_linux(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
              "--no-directive-block")
    mcp = (tmp_path / ".cache/claude-cli-nodejs" / str(folder).replace("/", "-").replace(".", "-")
           / "mcp-logs-plugin-discord-discord")
    mcp.mkdir(parents=True)
    (mcp / "a.jsonl").write_text('{"m":"Successfully connected (transport: stdio) in 9ms"}\n')
    r = run_linux(tmp_path, "doctor", "--name", "b")
    assert "MCP 연결 성공" in r.stdout and "유닛 없음" not in r.stdout


def run_no_systemd(env_home, *args):
    """systemctl·loginctl 부재 시뮬레이션(도커 컨테이너): PATH에 tmux만, FAKE 해제 → 실호출 경로가 FileNotFoundError를 맞는다."""
    bindir = env_home / "bin"; bindir.mkdir(exist_ok=True)
    (bindir / "tmux").exists() or (bindir / "tmux").symlink_to(shutil.which("tmux"))
    env = {k: v for k, v in os.environ.items() if k != "HARNESS_FAKE_SYSTEMCTL"}
    env.update(HOME=str(env_home), HARNESS_OS="Linux", PATH=str(bindir))
    return subprocess.run([sys.executable, str(BOTCTL), *args],
                          capture_output=True, text=True, env=env)


def test_linux_add_without_systemd_leaves_sidecar_only(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    r = run_no_systemd(tmp_path, "add", "--name", "b", "--folder", str(folder),
                       "--session", "b-bot", "--no-directive-block")
    assert r.returncode == 0, r.stderr
    d = tmp_path / ".config/systemd/user"
    assert not (d / "com.folder-bot.b.service").exists()
    assert (d / "b-bot.tmux-cmd").exists() and (d / "b-bot.up.sh").exists()   # bot-restart가 읽는 정본은 남는다
    assert "[WARN] systemd 없음" in r.stdout and "사이드카만" in r.stdout
    r = run_no_systemd(tmp_path, "start", "--name", "b", "--dry-run")
    assert r.returncode == 0 and "new-session -d -s b-bot" in r.stdout and "systemctl" not in r.stdout
    r = run_no_systemd(tmp_path, "remove", "--name", "b")
    assert r.returncode == 0 and not list(d.iterdir()), (r.stderr, list(d.iterdir()))


def test_linux_doctor_without_systemd_warns_not_fails(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    run_no_systemd(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
                   "--no-directive-block")
    r = run_no_systemd(tmp_path, "doctor", "--name", "b")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "systemctl 없음" in r.stdout and "유닛 없음" not in r.stdout


def test_linux_codex_add_writes_units(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    bridge = _fake_bridge(tmp_path)
    r = run_linux(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot",
                  "--engine", "codex", "--bridge-dir", str(bridge))
    assert r.returncode == 0, r.stderr
    d = tmp_path / ".config/systemd/user"
    daemon = (d / "com.codex-discord.b.service").read_text()
    assert "Type=simple" in daemon and "Restart=always" in daemon
    assert f"--env-file=.env.b src/index.mjs" in daemon and f"WorkingDirectory={bridge}" in daemon
    tui = (d / "com.codex-discord.b-tui.service").read_text()
    assert "Type=oneshot" in tui and "KillMode=process" in tui
    assert f"ExecStart=/bin/bash {bridge}/scripts/tui-up.sh .env.b" in tui
    assert not (tmp_path / "Library/LaunchAgents").exists()
    r = run_linux(tmp_path, "remove", "--name", "b")
    assert r.returncode == 0, r.stderr
    assert not list(d.iterdir())


def test_bot_scripts_syntax_and_linux_branches():
    assets = BOTCTL.parent.parent / "assets"
    for s in ("bot-up.sh", "bot-restart.sh"):
        r = subprocess.run(["bash", "-n", str(assets / s)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


def test_bot_restart_reads_sidecar_on_linux(tmp_path):
    d = tmp_path / ".config/systemd/user"; d.mkdir(parents=True)
    (d / "b-bot.tmux-cmd").write_text("/bin/bash -lc 'cd /srv/w; exec bot-up -n b-bot'\n")
    env = dict(os.environ, HOME=str(tmp_path), HARNESS_OS="Linux", BOT_RESTART_DETACHED="1",
               BOT_RESTART_DRY_RUN="1", BOT_RESTART_LOG=str(tmp_path / "r.log"))
    r = subprocess.run(["bash", str(BOTCTL.parent.parent / "assets/bot-restart.sh"), "b-bot"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr + r.stdout
    assert "CMD=/bin/bash -lc 'cd /srv/w; exec bot-up -n b-bot'" in r.stdout
    assert f"MCP_LOG_DIR={tmp_path}/.cache/claude-cli-nodejs/-srv-w/mcp-logs-plugin-discord-discord" in r.stdout
    # 사이드카가 없으면 plist 탐색 없이 실패 메시지에 .tmux-cmd를 안내
    (d / "b-bot.tmux-cmd").unlink()
    r = subprocess.run(["bash", str(BOTCTL.parent.parent / "assets/bot-restart.sh"), "b-bot"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 1 and ".tmux-cmd" in r.stdout


def test_remove_block_deletes_file_it_created(tmp_path):
    # 원래 CLAUDE.md가 없던 폴더: add가 블록만 든 파일을 만들고, remove 뒤 0바이트 빈 파일을 남기지 않는다
    folder = tmp_path / "w"; folder.mkdir()
    run(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot")
    assert (folder / "CLAUDE.md").exists()
    r = run(tmp_path, "remove", "--name", "b")
    assert r.returncode == 0, r.stderr
    assert not (folder / "CLAUDE.md").exists() and "빈 파일 삭제" in r.stdout


# ---------------------------------------------------------------- 스레드 라이브 뷰 (0.1.9)
ASSETS = BOTCTL.parent.parent / "assets"
THREAD_HOOK = "bash ~/.local/bin/bot-thread-stop"
ROUTE_HOOK = "bash ~/.local/bin/bot-thread-route"
COMPACT_HOOK = "bash ~/.local/bin/bot-thread-compact"


def test_add_injects_thread_hook_preserving_user_hooks(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    (folder / ".claude").mkdir()
    user_hook = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}],
                           "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo pre"}]}]}}
    (folder / ".claude/settings.local.json").write_text(json.dumps(user_hook))
    r = run(tmp_path, "add", "--name", "b", "--folder", str(folder),
            "--session", "b-bot", "--no-autostart", "--no-directive-block")
    assert r.returncode == 0, r.stderr
    data = json.loads((folder / ".claude/settings.local.json").read_text())
    cmds = [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]]
    assert cmds == ["echo mine", THREAD_HOOK]
    assert data["hooks"]["PreToolUse"][0]["matcher"] == "Bash"
    bots = json.loads((tmp_path / ".config/folder-bot/bots.json").read_text())
    assert set(bots["b"]["thread_hooks"]) == {THREAD_HOOK, ROUTE_HOOK, COMPACT_HOOK}
    assert data["hooks"]["PreCompact"][0]["hooks"][0]["command"] == COMPACT_HOOK
    assert data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"] == ROUTE_HOOK
    # 재실행해도 중복 주입 없음
    run(tmp_path, "add", "--name", "b", "--folder", str(folder),
        "--session", "b-bot", "--no-autostart", "--no-directive-block")
    data = json.loads((folder / ".claude/settings.local.json").read_text())
    assert [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]] == ["echo mine", THREAD_HOOK]
    # remove는 주입분만 걷는다
    r = run(tmp_path, "remove", "--name", "b")
    assert r.returncode == 0, r.stderr
    data = json.loads((folder / ".claude/settings.local.json").read_text())
    assert [h["command"] for g in data["hooks"]["Stop"] for h in g["hooks"]] == ["echo mine"]
    assert "UserPromptSubmit" not in data["hooks"] and "PreCompact" not in data["hooks"]
    assert (tmp_path / ".local/bin/bot-thread").exists() and (tmp_path / ".local/bin/bot-thread-route").exists()


def _fake_curl(tmp_path, response: str):
    """curl 대체 — 마지막 인자(URL)와 -d/-F 본문을 기록하고 고정 응답을 낸다."""
    shim = tmp_path / "shim"; shim.mkdir(exist_ok=True)
    log = tmp_path / "curl.log"
    (shim / "curl").write_text(
        "#!/bin/bash\n"
        f"printf '%s\\n' \"$*\" >> '{log}'\n"
        f"cat <<'RESP'\n{response}\nRESP\n")
    (shim / "curl").chmod(0o755)
    return shim / "curl", log


def _thread_env(tmp_path, bot_folder):
    (tmp_path / ".config/folder-bot").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".config/folder-bot/bots.json").write_text(json.dumps(
        {"b": {"engine": "claude", "folder": str(bot_folder), "session": "b-bot"}}))
    st = bot_folder / ".discord-state"; st.mkdir(parents=True, exist_ok=True)
    (st / ".env").write_text("DISCORD_BOT_TOKEN=tok123\n")
    return st


def test_bot_thread_kind_parses_and_caches(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    st = _thread_env(tmp_path, folder)
    curl, log = _fake_curl(tmp_path, '{"id":"999","type":11,"parent_id":"555"}')
    env = dict(os.environ, HOME=str(tmp_path), BOT_THREAD_CURL=str(curl), BOT_THREAD_TMUX="/bin/false")
    r = subprocess.run(["bash", str(ASSETS / "bot-thread.sh"), "kind", "b", "999"],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "thread 555"
    assert "Authorization: Bot tok123" in log.read_text()
    assert json.loads((st / "threads.json").read_text())["999"]["parent_id"] == "555"
    # 캐시 적중 — curl 재호출 없음
    log.write_text("")
    r = subprocess.run(["bash", str(ASSETS / "bot-thread.sh"), "kind", "b", "999"],
                       capture_output=True, text=True, env=env)
    assert r.stdout.strip() == "thread 555" and log.read_text() == ""
    # 일반 채널
    curl2, _ = _fake_curl(tmp_path, '{"id":"1","type":0}')
    r = subprocess.run(["bash", str(ASSETS / "bot-thread.sh"), "kind", "b", "1"],
                       capture_output=True, text=True, env=dict(env, BOT_THREAD_CURL=str(curl2)))
    assert r.stdout.strip() == "channel"


def test_bot_thread_post_chunks_and_stdin(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    _thread_env(tmp_path, folder)
    curl, log = _fake_curl(tmp_path, '{"id":"m1"}')
    env = dict(os.environ, HOME=str(tmp_path), BOT_THREAD_CURL=str(curl), BOT_THREAD_TMUX="/bin/false")
    long = "가" * 2500 + "\n끝"
    r = subprocess.run(["bash", str(ASSETS / "bot-thread.sh"), "post", "b", "999", "-"],
                       input=long, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    calls = [l for l in log.read_text().splitlines() if "/channels/999/messages" in l]
    assert len(calls) == 2                                  # 2000자 초과 → 2청크
    assert all("Authorization: Bot tok123" in c for c in calls)


def test_stop_hook_posts_last_turn_only(tmp_path):
    fake_bt = tmp_path / "bot-thread"; out = tmp_path / "posted.txt"
    fake_bt.write_text(f"#!/bin/bash\necho \"$1 $2 $3\" > '{out}'\ncat >> '{out}'\n"); fake_bt.chmod(0o755)
    tr = tmp_path / "t.jsonl"
    lines = [
        {"type": "user", "message": {"content": "첫 질문"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "옛 답변"}]}},
        {"type": "user", "message": {"content": [{"type": "text", "text": "두 번째 질문"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "확인 중"}, {"type": "tool_use", "id": "x", "name": "Bash", "input": {}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "최종 답변"}]}},
    ]
    tr.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n")
    hook_in = json.dumps({"session_id": "s", "transcript_path": str(tr), "stop_hook_active": False})
    env = dict(os.environ, DISCORD_THREAD_ID="999", DISCORD_BOT_NAME="b", BOT_THREAD_BIN=str(fake_bt))
    r = subprocess.run(["bash", str(ASSETS / "bot-thread-stop.sh")], input=hook_in,
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    posted = out.read_text()
    assert posted.startswith("post b 999")
    assert "확인 중" in posted and "최종 답변" in posted
    assert "옛 답변" not in posted and "두 번째 질문" not in posted
    # stop_hook_active면 게시 없음 / 환경변수 없으면 무동작
    out.unlink()
    subprocess.run(["bash", str(ASSETS / "bot-thread-stop.sh")],
                   input=json.dumps({"transcript_path": str(tr), "stop_hook_active": True}),
                   capture_output=True, text=True, env=env)
    assert not out.exists()
    subprocess.run(["bash", str(ASSETS / "bot-thread-stop.sh")], input=hook_in,
                   capture_output=True, text=True, env=dict(os.environ, BOT_THREAD_BIN=str(fake_bt)))
    assert not out.exists()


def test_directive_block_refreshes_in_place(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    (folder / "CLAUDE.md").write_text("# 내 규칙\n\n위쪽 원문\n")
    run(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot", "--no-autostart")
    md = folder / "CLAUDE.md"
    cur = md.read_text() + "\n아래쪽 원문\n"
    md.write_text(cur)
    # 블록 본문을 옛 버전처럼 바꿔 놓고 add 재실행 → 마커 안쪽만 최신으로, 바깥은 그대로
    stale = cur.replace("스레드 = 독립 세션", "옛 문구")
    assert stale != cur
    md.write_text(stale)
    r = run(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot", "--no-autostart")
    assert "지침 블록 갱신" in r.stdout
    assert md.read_text() == cur
    # 동일하면 무변경
    r = run(tmp_path, "add", "--name", "b", "--folder", str(folder), "--session", "b-bot", "--no-autostart")
    assert "지침 블록" not in r.stdout and md.read_text() == cur


def _fake_bot_thread(tmp_path, kind="thread 555"):
    """bot-thread 대체 — 호출 기록을 남기고 kind/ensure/list/deliver/post에 고정 응답."""
    log = tmp_path / "bt.log"; fake = tmp_path / "bot-thread"
    fake.write_text(
        "#!/bin/bash\n"
        f"echo \"$1 $2 $3\" >> '{log}'\n"
        "case \"$1\" in\n"
        f"  kind) echo '{kind}';;\n"
        "  ensure) echo 'b-t000999';;\n"
        "  list) printf 'thread_id\\tsession_id\\twindow\\tlive\\tlast\\n'; printf '%s\\t\\tt000999\\tno\\t-\\n' \"$3\";;\n"
        f"  deliver) cat >> '{log}';;\n"
        "esac\n")
    fake.chmod(0o755)
    return fake, log


def _route(tmp_path, prompt, cwd, env_extra=None):
    hook_in = json.dumps({"session_id": "s", "prompt": prompt, "cwd": str(cwd)})
    env = dict(os.environ, HOME=str(tmp_path), BOT_THREAD_BOTS_JSON=str(tmp_path / ".config/folder-bot/bots.json"))
    env.update(env_extra or {})
    return subprocess.run(["bash", str(ASSETS / "bot-thread-route.sh")], input=hook_in,
                          capture_output=True, text=True, env=env)


def test_route_hook_delegates_thread_and_blocks_main(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    st = _thread_env(tmp_path, folder)
    (st / "access.json").write_text(json.dumps({"groups": {"1000": {}}}))
    fake, log = _fake_bot_thread(tmp_path)
    tag = '<channel source="plugin:discord:discord" chat_id="777000999" message_id="42" user="u" ts="t">'
    r = _route(tmp_path, f"{tag}\n스레드 질문\n</channel>", folder, {"BOT_THREAD_BIN": str(fake)})
    assert r.returncode == 2, (r.stdout, r.stderr)
    calls = log.read_text()
    assert "kind b 777000999" in calls and "ensure b 777000999" in calls and "deliver b 777000999" in calls
    assert "스레드 질문" in calls and 'chat_id="777000999"' in calls   # 태그째 전달
    assert "post b 777000999" in calls                               # 첫 메시지 담당 안내
    assert "b-t000999" in r.stderr


def test_route_hook_passes_main_channel_dm_and_thread_sessions(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    st = _thread_env(tmp_path, folder)
    (st / "access.json").write_text(json.dumps({"groups": {"1000": {}}}))
    fake, log = _fake_bot_thread(tmp_path, kind="dm")
    tag_main = '<channel source="plugin:discord:discord" chat_id="1000" message_id="1" user="u" ts="t">'
    assert _route(tmp_path, f"{tag_main}\n안녕\n</channel>", folder, {"BOT_THREAD_BIN": str(fake)}).returncode == 0
    assert not log.exists()                                          # 등록 채널은 bot-thread 호출도 없음
    tag_dm = '<channel source="plugin:discord:discord" chat_id="2000" message_id="1" user="u" ts="t">'
    assert _route(tmp_path, f"{tag_dm}\nDM\n</channel>", folder, {"BOT_THREAD_BIN": str(fake)}).returncode == 0
    assert "ensure" not in log.read_text()                           # DM은 kind까지만
    # 스레드 세션 안에서는 무동작 / 채널 태그 없는 일반 프롬프트도 무동작
    assert _route(tmp_path, f"{tag_dm}\nx\n</channel>", folder, {"BOT_THREAD_BIN": str(fake), "DISCORD_THREAD_ID": "9"}).returncode == 0
    assert _route(tmp_path, "그냥 로컬 프롬프트", folder, {"BOT_THREAD_BIN": str(fake)}).returncode == 0


def test_route_hook_falls_back_to_main_on_ensure_failure(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    st = _thread_env(tmp_path, folder)
    (st / "access.json").write_text(json.dumps({"groups": {"1000": {}}}))
    fake = tmp_path / "bot-thread"
    fake.write_text("#!/bin/bash\ncase \"$1\" in kind) echo 'thread 1000';; list) echo;; ensure) echo 'boom' >&2; exit 1;; esac\n")
    fake.chmod(0o755)
    tag = '<channel source="plugin:discord:discord" chat_id="777000999" message_id="42" user="u" ts="t">'
    r = _route(tmp_path, f"{tag}\n질문\n</channel>", folder, {"BOT_THREAD_BIN": str(fake)})
    assert r.returncode == 0 and "[스레드 라우팅 실패]" in r.stdout


def test_rotate_assigns_new_session_and_fresh_flag(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    st = _thread_env(tmp_path, folder)
    (st / "threads.json").write_text(json.dumps({"777": {"session_id": "old-sid", "window": "t000777"}}))
    env = dict(os.environ, HOME=str(tmp_path), BOT_THREAD_TMUX="/bin/false")
    r = subprocess.run(["bash", str(ASSETS / "bot-thread.sh"), "rotate", "b", "777"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    m = json.loads((st / "threads.json").read_text())["777"]
    assert m["session_id"] == r.stdout.strip() and m["session_id"] != "old-sid"
    assert m["previous"] == ["old-sid"] and m["fresh"] == "1"
    assert (folder / "threads/777").is_dir()
    # fresh는 한 번만 1, 이후 0
    r = subprocess.run(["bash", str(ASSETS / "bot-thread.sh"), "fresh", "b", "777"], capture_output=True, text=True, env=env)
    assert r.stdout.strip() == "1"
    r = subprocess.run(["bash", str(ASSETS / "bot-thread.sh"), "fresh", "b", "777"], capture_output=True, text=True, env=env)
    assert r.stdout.strip() == "0"


def test_route_hook_prefixes_reanchor_when_fresh_and_session_md_exists(tmp_path):
    folder = tmp_path / "w"; folder.mkdir()
    st = _thread_env(tmp_path, folder)
    (st / "access.json").write_text(json.dumps({"groups": {"1000": {}}}))
    (folder / "threads/777000999").mkdir(parents=True)
    (folder / "threads/777000999/SESSION.md").write_text("# SESSION\n")
    log = tmp_path / "bt.log"; fake = tmp_path / "bot-thread"
    fake.write_text("#!/bin/bash\necho \"$1 $2 $3\" >> '%s'\ncase \"$1\" in kind) echo 'thread 555';; ensure) echo 'b-t000999';; fresh) echo 1;; list) printf 'thread_id\\tsession_id\\n777000999\\tsid\\n';; deliver) cat >> '%s';; esac\n" % (log, log))
    fake.chmod(0o755)
    tag = '<channel source="plugin:discord:discord" chat_id="777000999" message_id="42" user="u" ts="t">'
    r = _route(tmp_path, f"{tag}\n이어서\n</channel>", folder, {"BOT_THREAD_BIN": str(fake)})
    assert r.returncode == 2
    body = log.read_text()
    assert "[재정박]" in body and "threads/777000999/SESSION.md" in body and body.index("[재정박]") < body.index("이어서")


def test_stop_hook_appends_thread_log(tmp_path):
    fake_bt = tmp_path / "bot-thread"; fake_bt.write_text("#!/bin/bash\ncat >/dev/null\n"); fake_bt.chmod(0o755)
    folder = tmp_path / "w"; folder.mkdir()
    tr = tmp_path / "t.jsonl"
    tr.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in [
        {"type": "user", "message": {"content": '<channel chat_id="9">질문 하나</channel>'}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "답 하나"}]}},
    ]) + "\n")
    env = dict(os.environ, DISCORD_THREAD_ID="9", DISCORD_BOT_NAME="b", BOT_THREAD_BIN=str(fake_bt), PWD=str(folder))
    r = subprocess.run(["bash", str(ASSETS / "bot-thread-stop.sh")], input=json.dumps({"transcript_path": str(tr), "stop_hook_active": False}),
                       capture_output=True, text=True, env=env, cwd=str(folder))
    assert r.returncode == 0, r.stderr
    line = (folder / "threads/9/log.md").read_text().strip()
    assert "Q: 질문 하나" in line and "A: 답 하나" in line and "<channel" not in line


def test_compact_hook_posts_notice_only_in_thread_session(tmp_path):
    out = tmp_path / "posted.txt"; fake = tmp_path / "bot-thread"
    fake.write_text(f"#!/bin/bash\necho \"$1 $2 $3 $4\" > '{out}'\n"); fake.chmod(0o755)
    subprocess.run(["bash", str(ASSETS / "bot-thread-compact.sh")], input="{}", capture_output=True, text=True,
                   env=dict(os.environ, DISCORD_THREAD_ID="9", DISCORD_BOT_NAME="b", BOT_THREAD_BIN=str(fake)))
    assert out.read_text().startswith("post b 9") and "compact" in out.read_text()
    out.unlink()
    subprocess.run(["bash", str(ASSETS / "bot-thread-compact.sh")], input="{}", capture_output=True, text=True,
                   env=dict(os.environ, BOT_THREAD_BIN=str(fake)))
    assert not out.exists()
