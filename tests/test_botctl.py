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
