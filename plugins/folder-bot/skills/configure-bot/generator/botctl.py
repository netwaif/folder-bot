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


def install_all(bot: dict) -> list[str]:
    """add 후 설치 일괄 수행 — 스크립트·plist·지침 블록."""
    lines = []
    lines += install_scripts()
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
    if a.name in bots:
        del bots[a.name]
        save_bots(bots)
    print(f"제거됨: {a.name}")


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

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
