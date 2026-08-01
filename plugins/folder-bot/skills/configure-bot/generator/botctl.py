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


def allow_project_mcp(folder: Path) -> list[str]:
    """폴더의 프로젝트 MCP 자동 허용 — 무인 재시작이 승인 다이얼로그에 막히지 않게 한다.

    .claude/settings.local.json에 enableAllProjectMcpServers=true를 병합(기존 키 보존).
    """
    p = folder / ".claude/settings.local.json"
    data = json.loads(p.read_text()) if p.exists() else {}
    if data.get("enableAllProjectMcpServers") is True:
        return []
    data["enableAllProjectMcpServers"] = True
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return [f"프로젝트 MCP 자동 허용 설정: {p}"]


def install_all(bot: dict, allow_mcp: bool = False) -> list[str]:
    """add 후 설치 일괄 수행 — 스크립트·plist·지침 블록."""
    lines = []
    lines += install_scripts()
    lines += write_plist(bot)
    if bot["directive_block"]:
        lines += install_block(Path(bot["folder"]))
    if allow_mcp:
        lines += allow_project_mcp(Path(bot["folder"]))
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
    for line in install_all(resolve_bot(a.name), allow_mcp=a.allow_project_mcp):
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


def cmd_start(a) -> None:
    import subprocess
    bot = resolve_bot(a.name)
    tmux = find_tmux()
    argv = [tmux, "new-session", "-d", "-s", bot["session"], build_cmd(bot)]
    if a.dry_run:
        print(" ".join(argv))
        return
    if subprocess.run([tmux, "has-session", "-t", bot["session"]],
                      capture_output=True).returncode == 0:
        print(f"이미 실행 중: {bot['session']}")
        return
    subprocess.run(argv, check=True)
    print(f"기동: {bot['session']} — 연결 판정은 MCP 로그(bot-up이 감시)")


def cmd_stop(a) -> None:
    import subprocess
    bot = resolve_bot(a.name)
    subprocess.run([find_tmux(), "kill-session", "-t", bot["session"]], capture_output=True)
    print(f"중지: {bot['session']}")


def cmd_doctor(a) -> None:
    import subprocess
    fails = 0
    names = [a.name] if a.name else list(load_bots())
    for name in names:
        b = resolve_bot(name)

        def rep(level, msg):
            nonlocal fails
            if level == "FAIL":
                fails += 1
            print(f"[{level}] {name}: {msg}")

        p = plist_path(b)
        if b["autostart"] and not p.exists():
            rep("FAIL", f"plist 없음: {p}")
        trusted = False
        try:
            proj = json.loads((home() / ".claude.json").read_text())
            trusted = proj.get("projects", {}).get(b["folder"], {}).get(
                "hasTrustDialogAccepted") is True
        except (OSError, ValueError):
            pass
        if not trusted:
            rep("WARN", "워크스페이스 미신뢰 — 봇 세션이 승인 다이얼로그에 막힐 수 있음"
                        " (해당 폴더에서 claude를 한 번 열어 신뢰를 수락할 것)")
        env = Path(b["state_dir"]) / ".env"
        if not env.exists():
            rep("WARN", f"페어링 안 됨(.env 없음): {env}")
        md = Path(b["folder"]) / "CLAUDE.md"
        has_block = md.exists() and MARK_START in md.read_text()
        if b["directive_block"] and not has_block:
            rep("WARN", "CLAUDE.md 지침 블록 없음")
        alive = subprocess.run([find_tmux(), "has-session", "-t", b["session"]],
                               capture_output=True).returncode == 0
        rep("OK" if alive else "WARN", f"tmux 세션 {'생존' if alive else '없음'}: {b['session']}")
    sys.exit(1 if fails else 0)


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
    ap.add_argument("--allow-project-mcp", action="store_true")
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

    sp = sub.add_parser("start", help="봇 즉시 기동")
    sp.add_argument("--name", required=True)
    sp.add_argument("--dry-run", action="store_true")
    sp.set_defaults(fn=cmd_start)

    tp = sub.add_parser("stop", help="봇 중지 (tmux kill-session)")
    tp.add_argument("--name", required=True)
    tp.set_defaults(fn=cmd_stop)

    dp = sub.add_parser("doctor", help="읽기 전용 진단")
    dp.add_argument("--name")
    dp.set_defaults(fn=cmd_doctor)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
