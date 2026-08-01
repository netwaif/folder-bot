#!/usr/bin/env python3
"""folder-bot 결정적 엔진 — bots.json 정본으로 봇을 멱등 설치·관리한다.

경로는 전부 HOME 환경변수 기준(테스트가 HOME을 tmpdir로 돌린다).
비파괴 원칙: SESSION.md는 생성·수정하지 않는다. CLAUDE.md는 마커 블록 append/제거만.
launchctl bootout은 실행하지 않는다 — 파일 생성/삭제 + tmux kill-session만.
"""
import argparse
import json
import os
import shutil
import stat
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def home() -> Path:
    return Path(os.environ["HOME"])


def config_dir() -> Path:
    return home() / ".config/folder-bot"


def bots_path() -> Path:
    return config_dir() / "bots.json"


def load_bots() -> dict:
    if not bots_path().exists():
        return {}
    return json.loads(bots_path().read_text())


def save_bots(bots: dict) -> None:
    config_dir().mkdir(parents=True, exist_ok=True)
    bots_path().write_text(json.dumps(bots, ensure_ascii=False, indent=2) + "\n")


def resolve_bot(name: str) -> dict:
    raw = load_bots().get(name)
    if raw is None:
        sys.exit(f"오류: 봇 '{name}' 없음 — botctl.py list 로 확인")
    b = dict(raw)
    b["name"] = name
    b.setdefault("engine", "claude")
    b.setdefault("remote_control", b["session"])
    b.setdefault("state_dir", str(Path(b["folder"]) / ".discord-state"))
    b.setdefault("autostart", True)
    b.setdefault("directive_block", True)
    for k in ("folder", "state_dir"):
        b[k] = str(Path(b[k]).expanduser())
    return b


def install_scripts() -> list[str]:
    """assets의 bot-up/bot-restart를 ~/.local/bin에 멱등 설치."""
    out = []
    bin_dir = home() / ".local/bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    for src_name, dst_name in (("bot-up.sh", "bot-up"), ("bot-restart.sh", "bot-restart")):
        src, dst = ASSETS / src_name, bin_dir / dst_name
        if not (dst.exists() and dst.read_bytes() == src.read_bytes()):
            shutil.copyfile(src, dst)
            out.append(f"스크립트 설치: {dst}")
        dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return out


def find_tmux() -> str:
    for c in (shutil.which("tmux"), "/opt/homebrew/bin/tmux", "/usr/local/bin/tmux"):
        if c and Path(c).exists():
            return c
    sys.exit("오류: tmux를 찾을 수 없음 — brew install tmux")


def build_cmd(bot: dict) -> str:
    """plist·수동 기동 공용 세션 명령. bot-restart.sh 파서가 'cd <폴더>'를 읽는다."""
    path_esc = os.environ.get("PATH", "").replace("&", "&amp;")
    parts = [f"cd {bot['folder']}",
             f'export PATH="{path_esc}"',
             f"export DISCORD_STATE_DIR={bot['state_dir']}"]
    flags = ""
    if bot["remote_control"]:
        flags = f" -n {bot['session']} --remote-control {bot['remote_control']}"
    parts.append(f"exec {home()}/.local/bin/bot-up{flags}"
                 " --channels plugin:discord@claude-plugins-official")
    return "/bin/zsh -lc '" + "; ".join(parts) + "'"


def plist_path(bot: dict) -> Path:
    return home() / f"Library/LaunchAgents/com.folder-bot.{bot['name']}.plist"


def write_plist(bot: dict) -> list[str]:
    import plistlib
    p = plist_path(bot)
    if not bot["autostart"]:
        if p.exists():
            p.unlink()
            return [f"plist 제거(autostart off): {p}"]
        return []
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"Label": f"com.folder-bot.{bot['name']}",
            "ProgramArguments": [find_tmux(), "new-session", "-d", "-s",
                                 bot["session"], build_cmd(bot)],
            "RunAtLoad": True}
    blob = plistlib.dumps(data)
    if not (p.exists() and p.read_bytes() == blob):
        p.write_bytes(blob)
        return [f"plist 생성: {p} (다음 부팅부터 자동 기동)"]
    return []


MARK_START = "<!-- store:discord-bot:start -->"
MARK_END = "<!-- store:discord-bot:end -->"


def install_block(folder: Path) -> list[str]:
    """CLAUDE.md에 지침 블록을 마커로 append(멱등). SESSION.md는 건드리지 않는다."""
    md = folder / "CLAUDE.md"
    body = (ASSETS / "directive-block.md").read_text()
    cur = md.read_text() if md.exists() else ""
    if MARK_START in cur:
        return []
    block = f"\n{MARK_START}\n{body.rstrip()}\n{MARK_END}\n"
    md.write_text(cur + block)
    return [f"CLAUDE.md 지침 블록 설치: {md}"]


def remove_block(folder: Path) -> list[str]:
    """마커 범위만 걷어내 원문을 복원한다(블록 외 diff 0)."""
    md = folder / "CLAUDE.md"
    if not md.exists():
        return []
    cur = md.read_text()
    if MARK_START not in cur or MARK_END not in cur:
        return []
    pre, rest = cur.split(MARK_START, 1)
    _, post = rest.split(MARK_END, 1)
    md.write_text(pre.rstrip("\n") + ("\n" if pre.strip() else "") + post.lstrip("\n"))
    return [f"CLAUDE.md 지침 블록 제거: {md}"]


def install_all(bot: dict) -> list[str]:
    """add 후 설치 일괄 수행 — 스크립트·plist·지침 블록."""
    lines = []
    lines += install_scripts()
    lines += write_plist(bot)
    if bot["directive_block"]:
        lines += install_block(Path(bot["folder"]))
    return lines


def cmd_add(a) -> None:
    bots = load_bots()
    entry = {"engine": "claude", "folder": a.folder, "session": a.session}
    if a.no_remote_control:
        entry["remote_control"] = False
    elif a.remote_control:
        entry["remote_control"] = a.remote_control
    if a.no_directive_block:
        entry["directive_block"] = False
    if a.no_autostart:
        entry["autostart"] = False
    bots[a.name] = entry
    save_bots(bots)
    for line in install_all(resolve_bot(a.name)):
        print(line)
    print(f"등록됨: {a.name}")


def cmd_list(a) -> None:
    for name in load_bots():
        b = resolve_bot(name)
        print(f"{name}\t{b['engine']}\t{b['folder']}\t{b['session']}\t{b['state_dir']}")


def cmd_remove(a) -> None:
    bots = load_bots()
    if a.name not in bots:
        print(f"이미 없음: {a.name}")
        return
    bot = resolve_bot(a.name)  # 삭제 전에 경로 확보
    del bots[a.name]
    save_bots(bots)
    for line in remove_block(Path(bot["folder"])):
        print(line)
    p = plist_path(bot)
    if p.exists():
        p.unlink()
        print(f"plist 제거: {p}")
    # state_dir는 어떤 경우에도 삭제하지 않는다 — 경로만 안내
    print(f"제거됨: {a.name} (페어링 파일 보존: {bot['state_dir']})")


def cmd_pair(a) -> None:
    bot = resolve_bot(a.name)
    st = Path(bot["state_dir"])
    env = st / ".env"
    if env.exists() and not a.force:
        sys.exit(f"오류: {env} 이미 존재 — 덮어쓰려면 --force")
    st.mkdir(parents=True, exist_ok=True)
    (st / "inbox").mkdir(exist_ok=True)
    env.write_text(f"DISCORD_BOT_TOKEN={a.token}\n")
    env.chmod(0o600)
    access = {"dmPolicy": "allowlist", "allowFrom": [a.user_id],
              "groups": {a.channel_id: {"requireMention": False, "allowFrom": [a.user_id]}},
              "pending": {}}
    (st / "access.json").write_text(json.dumps(access, ensure_ascii=False, indent=2) + "\n")
    print(f"페어링 완료: {st}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    ap = sub.add_parser("add", help="봇 등록 + 설치")
    ap.add_argument("--name", required=True)
    ap.add_argument("--folder", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--remote-control")
    ap.add_argument("--no-remote-control", action="store_true")
    ap.add_argument("--no-directive-block", action="store_true")
    ap.add_argument("--no-autostart", action="store_true")
    ap.set_defaults(fn=cmd_add)

    sub.add_parser("list", help="봇 목록").set_defaults(fn=cmd_list)

    rp = sub.add_parser("remove", help="봇 제거")
    rp.add_argument("--name", required=True)
    rp.add_argument("--keep-state", action="store_true")
    rp.set_defaults(fn=cmd_remove)

    pp = sub.add_parser("pair", help="토큰·접근 파일 생성")
    pp.add_argument("--name", required=True)
    pp.add_argument("--token", required=True)
    pp.add_argument("--user-id", required=True)
    pp.add_argument("--channel-id", required=True)
    pp.add_argument("--force", action="store_true")
    pp.set_defaults(fn=cmd_pair)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
