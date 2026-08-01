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
