#!/usr/bin/env python3
"""folder-bot 결정적 엔진 — bots.json 정본으로 봇을 멱등 설치·관리한다.

경로는 전부 HOME 환경변수 기준(테스트가 HOME을 tmpdir로 돌린다).
비파괴 원칙: SESSION.md는 생성·수정하지 않는다. CLAUDE.md는 마커 블록 append/제거만.
launchctl bootout은 실행하지 않는다 — 파일 생성/삭제 + tmux kill-session만.
"""
import argparse
import json
import os
import re
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


def load_config() -> dict:
    p = config_dir() / "config.json"
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


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
    if b["engine"] == "codex":
        b.setdefault("bridge_dir",
                     load_config().get("codex_bridge_dir", "~/codex-discord"))
        b["bridge_dir"] = str(Path(b["bridge_dir"]).expanduser())
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


def _directive_target(bot: dict) -> tuple[str, str, dict]:
    """엔진별 (지침 파일명, asset 파일명, 렌더 치환값)."""
    if bot["engine"] == "codex":
        return ("AGENTS.md", "directive-block-codex.md",
                {"{BRIDGE_DIR}": bot["bridge_dir"], "{ENV_FILE}": f".env.{bot['name']}"})
    return ("CLAUDE.md", "directive-block.md", {})


def install_block(bot: dict) -> list[str]:
    """지침 파일(CLAUDE.md/AGENTS.md)에 블록을 마커로 append(멱등). SESSION.md는 건드리지 않는다."""
    target, asset, render = _directive_target(bot)
    md = Path(bot["folder"]) / target
    body = (ASSETS / asset).read_text()
    for k, v in render.items():
        body = body.replace(k, v)
    cur = md.read_text() if md.exists() else ""
    if MARK_START in cur:
        return []
    block = f"\n{MARK_START}\n{body.rstrip()}\n{MARK_END}\n"
    md.write_text(cur + block)
    return [f"{target} 지침 블록 설치: {md}"]


def remove_block(bot: dict) -> list[str]:
    """마커 범위만 걷어내 원문을 복원한다(블록 외 diff 0)."""
    target, _, _ = _directive_target(bot)
    md = Path(bot["folder"]) / target
    if not md.exists():
        return []
    cur = md.read_text()
    if MARK_START not in cur or MARK_END not in cur:
        return []
    pre, rest = cur.split(MARK_START, 1)
    _, post = rest.split(MARK_END, 1)
    md.write_text(pre.rstrip("\n") + ("\n" if pre.strip() else "") + post.lstrip("\n"))
    return [f"{target} 지침 블록 제거: {md}"]


# ---------------------------------------------------------------- codex 엔진 (브리지 인스턴스)

def find_node() -> str:
    c = shutil.which("node")
    if c:
        return c
    cands = sorted(Path.home().glob(".nvm/versions/node/*/bin/node"))
    if cands:
        return str(cands[-1])
    sys.exit("오류: node를 찾을 수 없음 — codex 브리지 데몬에 필요")


def codex_env_path(bot: dict) -> Path:
    return Path(bot["bridge_dir"]) / f".env.{bot['name']}"


def write_codex_env(bot: dict) -> list[str]:
    """브리지 인스턴스 .env.<이름> 생성(이미 있으면 보존 — 토큰이 들어있을 수 있다)."""
    p = codex_env_path(bot)
    if p.exists():
        return []
    codex_bin = shutil.which("codex") or ""
    lines = [
        f"# folder-bot codex 봇 인스턴스: {bot['name']}",
        "DISCORD_TOKEN=",
        "ALLOWED_USER_IDS=",
        f"CODEX_WORKDIR={bot['folder']}",
        *( [f"CODEX_BIN={codex_bin}"] if codex_bin else [] ),
        f"DATA_DIR=data-{bot['name']}",
        f"TUI_PANE={bot['session']}:0.0",
        "TUI_CHANNEL_ID=",
        "CHANNEL_IDS=",
        "TUI_TRIGGER_GATE=off",  # 전용 채널 — 호명 없이 모든 메시지에 응답
    ]
    p.write_text("\n".join(lines) + "\n")
    p.chmod(0o600)
    (Path(bot["bridge_dir"]) / f"data-{bot['name']}").mkdir(exist_ok=True)
    return [f"브리지 인스턴스 생성: {p} (토큰·채널은 pair에서)"]


def write_codex_plists(bot: dict) -> list[str]:
    import plistlib
    out = []
    la = home() / "Library/LaunchAgents"
    daemon_p = la / f"com.codex-discord.{bot['name']}.plist"
    tui_p = la / f"com.codex-discord.{bot['name']}-tui.plist"
    if not bot["autostart"]:
        for p in (daemon_p, tui_p):
            if p.exists():
                p.unlink()
                out.append(f"plist 제거(autostart off): {p}")
        return out
    node = find_node()
    path_env = f"{Path(node).parent}:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin"
    daemon = {"Label": f"com.codex-discord.{bot['name']}",
              "ProgramArguments": [node, f"--env-file=.env.{bot['name']}", "src/index.mjs"],
              "WorkingDirectory": bot["bridge_dir"],
              "EnvironmentVariables": {"PATH": path_env},
              "StandardOutPath": f"{bot['bridge_dir']}/logs/daemon-{bot['name']}.log",
              "StandardErrorPath": f"{bot['bridge_dir']}/logs/daemon-{bot['name']}.log",
              "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 15}
    tui = {"Label": f"com.codex-discord.{bot['name']}-tui",
           "ProgramArguments": [f"{bot['bridge_dir']}/scripts/tui-up.sh", f".env.{bot['name']}"],
           "EnvironmentVariables": {"PATH": path_env},
           "StandardOutPath": f"{bot['bridge_dir']}/logs/tui-up-{bot['name']}.log",
           "StandardErrorPath": f"{bot['bridge_dir']}/logs/tui-up-{bot['name']}.log",
           "RunAtLoad": True}
    la.mkdir(parents=True, exist_ok=True)
    for p, data in ((daemon_p, daemon), (tui_p, tui)):
        blob = plistlib.dumps(data)
        if not (p.exists() and p.read_bytes() == blob):
            p.write_bytes(blob)
            out.append(f"plist 생성: {p}")
    return out


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


def statusline_script() -> Path | None:
    """usage-coach statusline 정본 탐색 — config 명시 → 하네스 clone 순. 없으면 None(단독 설치)."""
    cands = []
    cfg = load_config().get("usage_coach_dir")
    if cfg:
        cands.append(Path(cfg).expanduser() / "scripts/statusline-command.sh")
    cands.append(home() / ".local/share/discord-harness/repos/usage-coach"
                 / "scripts/statusline-command.sh")
    for c in cands:
        if c.is_file():
            return c
    return None


def write_statusline(bot: dict) -> tuple[list[str], str | None]:
    """대시보드 클로드 카드 데이터원 — statusLine 훅이 세션 스냅샷을 남긴다(usage-coach 정본).

    미주입 시 카드가 영구 공백(2026-08-05 실측). usage-coach가 없으면(단독 설치) 건너뛰고,
    사용자가 이미 설정한 statusLine은 건드리지 않는다. 주입한 명령을 반환해 remove가 회수한다."""
    script = statusline_script()
    if script is None:
        return [], None
    p = Path(bot["folder"]) / ".claude/settings.local.json"
    data = json.loads(p.read_text()) if p.exists() else {}
    if "statusLine" in data:
        return [], None
    cmd = f"bash {script}"
    data["statusLine"] = {"type": "command", "command": cmd}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return [f"대시보드 statusLine 주입: {p}"], cmd


def remove_statusline(bot: dict) -> list[str]:
    """주입 기록과 현재 값이 일치할 때만 statusLine 회수(사용자 변경분 보존)."""
    sc = bot.get("statusline_cmd")
    if not sc:
        return []
    p = Path(bot["folder"]) / ".claude/settings.local.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    cur = data.get("statusLine")
    if not (isinstance(cur, dict) and cur.get("command") == sc):
        return []
    del data["statusLine"]
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return [f"statusLine 회수: {p}"]


def install_all(bot: dict, allow_mcp: bool = False) -> list[str]:
    """add 후 설치 일괄 수행 — 엔진별 스크립트·plist·지침 블록."""
    lines = []
    if bot["engine"] == "codex":
        if not Path(bot["bridge_dir"]).is_dir():
            sys.exit(f"오류: codex 브리지 폴더 없음: {bot['bridge_dir']}\n"
                     "github.com/netwaif/codex-discord 를 받아 설치하거나 "
                     "config.json의 codex_bridge_dir를 지정하세요")
        lines += write_codex_env(bot)
        lines += write_codex_plists(bot)
    else:
        lines += install_scripts()
        lines += write_plist(bot)
    if bot["directive_block"]:
        lines += install_block(bot)
    if allow_mcp:
        lines += allow_project_mcp(Path(bot["folder"]))
    return lines


def cmd_add(a) -> None:
    bots = load_bots()
    prior = bots.get(a.name) or {}
    entry = {"engine": a.engine, "folder": a.folder, "session": a.session}
    if "statusline_cmd" in prior:
        entry["statusline_cmd"] = prior["statusline_cmd"]  # 재등록에도 회수 기록 유지
    if a.engine == "codex":
        entry["bridge_dir"] = a.bridge_dir or load_config().get(
            "codex_bridge_dir", "~/codex-discord")
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
    bot = resolve_bot(a.name)
    lines = install_all(bot, allow_mcp=a.allow_project_mcp)
    if bot["engine"] == "claude":
        sl_lines, injected = write_statusline(bot)
        lines += sl_lines
        if injected:
            entry["statusline_cmd"] = injected
            save_bots(bots)
    for line in lines:
        print(line)
    print(f"등록됨: {a.name}")


def cmd_list(a) -> None:
    for name in load_bots():
        b = resolve_bot(name)
        print(f"{name}\t{b['engine']}\t{b['folder']}\t{b['session']}\t{b['state_dir']}")


def cmd_remove(a) -> None:
    import subprocess
    bots = load_bots()
    if a.name not in bots:
        print(f"이미 없음: {a.name}")
        return
    bot = resolve_bot(a.name)  # 삭제 전에 경로 확보
    del bots[a.name]
    save_bots(bots)
    for line in remove_block(bot):
        print(line)
    if bot["engine"] == "codex":
        # 데몬은 node 직속 job이라 bootout 안전(tmux 서버를 띄우는 job이 아님).
        # KeepAlive라 파일만 지우면 되살아나므로 내리고 지운다. TUI는 kill-session.
        uid = os.getuid()
        subprocess.run(["launchctl", "bootout",
                        f"gui/{uid}/com.codex-discord.{bot['name']}"], capture_output=True)
        subprocess.run([find_tmux(), "kill-session", "-t", bot["session"]], capture_output=True)
        la = home() / "Library/LaunchAgents"
        for p in (la / f"com.codex-discord.{bot['name']}.plist",
                  la / f"com.codex-discord.{bot['name']}-tui.plist"):
            if p.exists():
                p.unlink()
                print(f"plist 제거: {p}")
        print(f"제거됨: {a.name} (토큰 파일 보존: {codex_env_path(bot)})")
        return
    for line in remove_statusline(bot):
        print(line)
    p = plist_path(bot)
    if p.exists():
        p.unlink()
        print(f"plist 제거: {p}")
    # state_dir는 어떤 경우에도 삭제하지 않는다 — 경로만 안내
    print(f"제거됨: {a.name} (페어링 파일 보존: {bot['state_dir']})")


def cmd_pair(a) -> None:
    bot = resolve_bot(a.name)
    if bool(a.token) == bool(a.token_file):
        sys.exit("오류: --token 또는 --token-file 중 하나만 지정")
    tok_file = Path(a.token_file).expanduser() if a.token_file else None
    if tok_file and not tok_file.is_file():
        sys.exit(f"오류: 토큰 파일 없음: {tok_file}")
    token = tok_file.read_text().strip() if tok_file else a.token

    def consume_token_file():
        """페어링 성공 후에만 — 평문 토큰을 디스크에 남기지 않는다."""
        if tok_file:
            tok_file.unlink()
            print(f"토큰 파일 삭제: {tok_file}")

    if bot["engine"] == "codex":
        p = codex_env_path(bot)
        if not p.exists():
            sys.exit(f"오류: {p} 없음 — add를 먼저 실행")
        text = p.read_text()
        if "DISCORD_TOKEN=\n" not in text and not a.force:
            sys.exit(f"오류: {p} 토큰 이미 설정됨 — 덮어쓰려면 --force")
        subst = {"DISCORD_TOKEN": token, "ALLOWED_USER_IDS": a.user_id,
                 "TUI_CHANNEL_ID": a.channel_id, "CHANNEL_IDS": a.channel_id}
        lines = []
        for line in text.splitlines():
            key = line.split("=", 1)[0]
            lines.append(f"{key}={subst[key]}" if key in subst else line)
        p.write_text("\n".join(lines) + "\n")
        p.chmod(0o600)
        consume_token_file()
        print(f"페어링 완료: {p}")
        return
    st = Path(bot["state_dir"])
    env = st / ".env"
    if env.exists() and not a.force:
        sys.exit(f"오류: {env} 이미 존재 — 덮어쓰려면 --force")
    st.mkdir(parents=True, exist_ok=True)
    (st / "inbox").mkdir(exist_ok=True)
    env.write_text(f"DISCORD_BOT_TOKEN={token}\n")
    env.chmod(0o600)
    access = {"dmPolicy": "allowlist", "allowFrom": [a.user_id],
              "groups": {a.channel_id: {"requireMention": False, "allowFrom": [a.user_id]}},
              "pending": {}}
    (st / "access.json").write_text(json.dumps(access, ensure_ascii=False, indent=2) + "\n")
    consume_token_file()
    print(f"페어링 완료: {st}")


def cmd_start(a) -> None:
    import subprocess
    bot = resolve_bot(a.name)
    tmux = find_tmux()
    if bot["engine"] == "codex":
        tui_up = f"{bot['bridge_dir']}/scripts/tui-up.sh"
        if a.dry_run:
            print(f"{tui_up} .env.{bot['name']} + launchctl bootstrap "
                  f"com.codex-discord.{bot['name']}")
            return
        r = subprocess.run([tui_up, f".env.{bot['name']}"], capture_output=True, text=True)
        print(r.stdout.strip())
        if r.returncode != 0:
            sys.exit(f"오류: TUI 기동 실패 — {bot['bridge_dir']}/logs 확인")
        uid = os.getuid()
        label = f"com.codex-discord.{bot['name']}"
        if subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"],
                          capture_output=True).returncode != 0:
            subprocess.run(["launchctl", "bootstrap", f"gui/{uid}",
                            str(home() / f"Library/LaunchAgents/{label}.plist")], check=True)
            print(f"데몬 기동: {label}")
        return
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


def mcp_log_dir(folder: str) -> Path:
    """discord 플러그인 MCP 로그 디렉토리 — 폴더 절대경로의 /·. 을 - 로 치환."""
    return (home() / "Library/Caches/claude-cli-nodejs"
            / re.sub(r"[/.]", "-", folder) / "mcp-logs-plugin-discord-discord")


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

        if b["engine"] == "codex":
            la = home() / "Library/LaunchAgents"
            if b["autostart"]:
                for p in (la / f"com.codex-discord.{b['name']}.plist",
                          la / f"com.codex-discord.{b['name']}-tui.plist"):
                    if not p.exists():
                        rep("FAIL", f"plist 없음: {p}")
            envp = codex_env_path(b)
            if not envp.exists() or "DISCORD_TOKEN=\n" in envp.read_text():
                rep("WARN", f"페어링 안 됨(토큰 없음): {envp}")
            pid = Path(b["bridge_dir"]) / f"data-{b['name']}" / "daemon.pid"
            daemon_alive = False
            try:
                os.kill(int(pid.read_text().strip()), 0)
                daemon_alive = True
            except (OSError, ValueError):
                pass
            if not daemon_alive:
                rep("WARN", "브리지 데몬 죽음/미기동")
        else:
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
            mcp = mcp_log_dir(b["folder"])
            logs = sorted(mcp.glob("*.jsonl"),
                          key=lambda f: (f.stat().st_mtime, f.name)) if mcp.is_dir() else []
            if logs:
                # 낡은 성공 로그의 합격 오판 방지 — 최신 파일만 본다(하네스 judge_mcp 계열)
                text = logs[-1].read_text(errors="ignore")
                # 종료 기록으로 끝난 로그는 이전 세션 것 — 증거로 쓰지 않는다(8/6 양방향
                # 오판 실측). 로그 주인(sessionId) 기준 구분은 이월.
                tail = text[-2000:]
                if "Sending SIGINT" in tail or "exited" in tail:
                    rep("WARN", "MCP 판정 불가 — 최신 로그가 종료된 세션 것"
                                "(현 세션 로그 미생성, 기동 직후일 수 있음)")
                elif "Successfully connected" in text:
                    rep("OK", "MCP 연결 성공(최신 로그 기준)")
                elif "Connection failed" in text:
                    rep("FAIL", "MCP 연결 실패(최신 로그) — 토큰 오입력·"
                                "MESSAGE CONTENT INTENT 미설정·서버 초대 누락 확인")
                else:
                    rep("WARN", f"MCP 연결 판정 불가(성공/실패 기록 없음): {logs[-1]}")
        target, _, _ = _directive_target(b)
        md = Path(b["folder"]) / target
        has_block = md.exists() and MARK_START in md.read_text()
        if b["directive_block"] and not has_block:
            rep("WARN", f"{target} 지침 블록 없음")
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
    ap.add_argument("--engine", choices=["claude", "codex"], default="claude")
    ap.add_argument("--bridge-dir", help="codex 전용: codex-discord 브리지 폴더")
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
    pp.add_argument("--token")
    pp.add_argument("--token-file",
                    help="토큰 파일 경로 — 페어링 성공 시 파일을 삭제한다(평문 잔존 방지)")
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
