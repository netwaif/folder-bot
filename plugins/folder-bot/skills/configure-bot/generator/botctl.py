#!/usr/bin/env python3
"""folder-bot 결정적 엔진 — bots.json 정본으로 봇을 멱등 설치·관리한다.

경로는 전부 HOME 환경변수 기준(테스트가 HOME을 tmpdir로 돌린다).
비파괴 원칙: SESSION.md는 생성·수정하지 않는다. CLAUDE.md는 마커 블록 append/제거만.
launchctl bootout은 실행하지 않는다 — 파일 생성/삭제 + tmux kill-session만.
OS 분기: macOS=LaunchAgent plist, 리눅스(VPS·WSL2)=systemd 사용자 유닛(하네스 6차 2026-09-07 패턴).
  리눅스 유닛은 com.folder-bot.<이름>.service(라벨 동일 — agentlayer wiring 매칭용) +
  <세션>.tmux-cmd·<세션>.up.sh 사이드카(bot-restart.sh·agentlayer가 읽음).
엔진: claude(discord 플러그인) / codex·agy(codex-discord 브리지 인스턴스 — systemd 없으면 데몬도 tmux 세션 <이름>-daemon).
"""
from __future__ import annotations  # macOS 기본 python3(3.9)에서 `X | None` 표기 크래시 방지 — 8/6 실측

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


def host_os() -> str:
    """'darwin' | 'linux'. HARNESS_OS(Darwin/Linux)는 테스트 override — 정본 셸 스크립트와 같은 규약."""
    o = os.environ.get("HARNESS_OS", "").strip().lower()
    if o:
        return o
    return "darwin" if sys.platform == "darwin" else ("linux" if sys.platform.startswith("linux") else sys.platform)


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def service_dir() -> Path:
    return home() / ("Library/LaunchAgents" if host_os() == "darwin" else ".config/systemd/user")


def unit_name(label: str) -> str:
    return f"{label}.service"


def has_systemd() -> bool:
    """systemctl 바이너리 존재 여부. 없는 환경(도커 컨테이너 등)은 유닛 없이 사이드카만 남긴다 —
    자동 기동 없음, 재기동은 외부 몫(bot-restart·호스트 감시자)."""
    return bool(os.environ.get("HARNESS_FAKE_SYSTEMCTL")) or shutil.which("systemctl") is not None


def systemctl_user(*args) -> int:
    """systemctl --user 호출, 반환 코드를 돌려준다. HARNESS_FAKE_SYSTEMCTL=1이면 무접촉·0(테스트, 값이 경로면 호출을
    그 파일에 기록). 바이너리 없으면 WARN 후 1."""
    import subprocess
    fake = os.environ.get("HARNESS_FAKE_SYSTEMCTL")
    if fake:
        if fake.startswith("/"):
            with open(fake, "a") as f:
                f.write(" ".join(args) + "\n")
        return 0
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True).returncode
    except FileNotFoundError:
        print(f"[WARN] systemctl 없음 — 건너뜀: systemctl --user {' '.join(args)}")
        return 1


def enable_linger() -> None:
    """VPS: 로그아웃·부팅 뒤에도 사용자 유닛이 살게(실패 무시 — WSL2 등 권한 없는 환경·loginctl 부재)."""
    import subprocess
    if os.environ.get("HARNESS_FAKE_SYSTEMCTL"):
        return
    try:
        subprocess.run(["loginctl", "enable-linger", os.environ.get("USER", "")], capture_output=True)
    except FileNotFoundError:
        pass


def systemd_user_state() -> str:
    """리눅스 systemd --user 상태('running'/'degraded'면 정상). 없으면 ''."""
    import subprocess
    if os.environ.get("HARNESS_FAKE_SYSTEMCTL"):
        return "running"
    try:
        r = subprocess.run(["systemctl", "--user", "is-system-running"], capture_output=True, text=True)
        return (r.stdout or r.stderr).strip()
    except FileNotFoundError:
        return ""


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
    if b["engine"] in BRIDGE_ENGINES:
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
    for src_name, dst_name in (("bot-up.sh", "bot-up"), ("bot-restart.sh", "bot-restart"),
                               ("bot-thread.sh", "bot-thread"), ("bot-thread-stop.sh", "bot-thread-stop"),
                               ("bot-thread-route.sh", "bot-thread-route"), ("bot-thread-compact.sh", "bot-thread-compact")):
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
    sys.exit("오류: tmux를 찾을 수 없음 — " + ("apt install tmux" if host_os() == "linux" else "brew install tmux"))


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
    shell = "/bin/bash" if host_os() == "linux" else "/bin/zsh"   # 리눅스는 zsh가 없을 수 있다
    return shell + " -lc '" + "; ".join(parts) + "'"


def plist_path(bot: dict) -> Path:
    """자동 기동 정의 파일 — macOS plist / 리눅스 systemd 유닛(이름은 호환용)."""
    label = f"com.folder-bot.{bot['name']}"
    if host_os() == "linux":
        return service_dir() / unit_name(label)
    return service_dir() / f"{label}.plist"


def sidecar_paths(session: str) -> tuple[Path, Path]:
    """리눅스 tmux 유닛 사이드카: <세션>.tmux-cmd(세션 명령 원문)·<세션>.up.sh(유닛이 부르는 기동 스크립트)."""
    d = service_dir()
    return d / f"{session}.tmux-cmd", d / f"{session}.up.sh"


def write_tmux_unit(label: str, session: str, description: str, cmd: str, extra_unit: str = "") -> bool:
    """리눅스: tmux 세션을 띄우는 oneshot 유닛 + 사이드카. KillMode=process라 stop 때 공유 tmux 서버를
    죽이지 않는다(다른 봇 세션 보호). 반환 = 유닛 본문이 새로 쓰였는지."""
    d = service_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmux = find_tmux()
    cmd_file, up = sidecar_paths(session)
    unit = d / unit_name(label)
    cmd_file.write_text(cmd + "\n")
    up.write_text(f'#!/bin/bash\nexec "{tmux}" new-session -d -s {session} "$(cat "{cmd_file}")"\n')
    up.chmod(0o755)
    if not has_systemd():
        return False   # 사이드카만 — 유닛은 systemd가 있어야 의미가 있다
    body = (f"# {description}\n[Unit]\nDescription={label} (tmux 세션 {session})\n"
            "After=network-online.target\n\n"
            "[Service]\nType=oneshot\nRemainAfterExit=yes\nKillMode=process\n"
            f"ExecStart=/bin/bash {up}\nExecStop=-{tmux} kill-session -t {session}\n{extra_unit}\n"
            "[Install]\nWantedBy=default.target\n")
    changed = not (unit.exists() and unit.read_text() == body)
    unit.write_text(body)
    systemctl_user("daemon-reload")
    # enable만 — 첫 기동은 start 명령의 몫(macOS plist와 동형). --now로 띄우면 페어링 전에
    # 자격증명 없는 세션이 먼저 떠서 "이미 실행 중"만 찍힌다(WSL2 실측 2026-09-10).
    systemctl_user("enable", unit.name)
    enable_linger()
    return changed


def remove_units(labels: list[str], sessions: list[str], stop: bool) -> list[str]:
    """리눅스: 유닛 disable(+stop) → 유닛·사이드카 삭제 → daemon-reload. 잔존 0."""
    out = []
    for label in labels:
        u = service_dir() / unit_name(label)
        if u.exists():
            systemctl_user("disable", *(["--now"] if stop else []), u.name)
            u.unlink()
            out.append(f"유닛 제거: {u}")
    for sess in sessions:
        for q in sidecar_paths(sess):
            if q.exists():
                q.unlink()
    if out:
        systemctl_user("daemon-reload")
    return out


def write_unit(bot: dict) -> list[str]:
    label = f"com.folder-bot.{bot['name']}"
    if not bot["autostart"]:
        return [f"{line}(autostart off)" for line in remove_units([label], [bot["session"]], stop=False)]
    desc = f"folder-bot: name={bot['name']} session={bot['session']} folder={bot['folder']}"
    changed = write_tmux_unit(label, bot["session"], desc, build_cmd(bot))
    if not has_systemd():
        return [f"[WARN] systemd 없음 — 유닛 생략, 사이드카만: {sidecar_paths(bot['session'])[0]}"
                " (자동 기동 없음, 재기동은 bot-restart·외부 감시자 몫)"]
    if changed:
        return [f"유닛 생성: {plist_path(bot)} (systemd --user, 부팅 자동 기동 — WSL2는 우분투가 켜져 있는 동안)"]
    return []


def write_plist(bot: dict) -> list[str]:
    import plistlib
    if host_os() == "linux":
        return write_unit(bot)
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


BRIDGE_ENGINES = ("codex", "agy")   # codex-discord 브리지로 뜨는 엔진(agy = Antigravity CLI, 브리지 0.1.9+)
# agy 규칙 위치(agy 1.2.0 내장 agy-customizations 스킬 문서): GEMINI.md·AGENTS.md(디렉터리 계층, 프런트매터 없음)
# 또는 `.agents/rules/*.md`(프런트매터 trigger: always_on 이어야 무조건 로드). 사용자의 AGENTS.md/GEMINI.md를
# 건드리지 않도록 전용 always_on 규칙 파일을 쓴다.
AGY_RULE_FILE = ".agents/rules/discord-bot.md"
AGY_RULE_HEADER = "---\ntrigger: always_on\n---\n"


def is_bridge(bot: dict) -> bool:
    return bot["engine"] in BRIDGE_ENGINES


def _directive_target(bot: dict) -> tuple[str, str, dict]:
    """엔진별 (지침 파일명, asset 파일명, 렌더 치환값)."""
    if is_bridge(bot):
        render = {"{BRIDGE_DIR}": bot["bridge_dir"], "{ENV_FILE}": f".env.{bot['name']}",
                  "{ENGINE}": bot["engine"]}
        return (AGY_RULE_FILE if bot["engine"] == "agy" else "AGENTS.md",
                "directive-block-codex.md", render)
    return ("CLAUDE.md", "directive-block.md", {})


def install_block(bot: dict) -> list[str]:
    """지침 파일(CLAUDE.md/AGENTS.md)에 블록을 마커로 append(멱등). SESSION.md는 건드리지 않는다."""
    target, asset, render = _directive_target(bot)
    md = Path(bot["folder"]) / target
    body = (ASSETS / asset).read_text()
    for k, v in render.items():
        body = body.replace(k, v)
    cur = md.read_text() if md.exists() else (AGY_RULE_HEADER if target == AGY_RULE_FILE else "")
    if MARK_START in cur and MARK_END in cur:
        # 블록이 이미 있으면 마커 안쪽만 최신 본문으로 교체(블록 밖 diff 0) — 플러그인 업그레이드가 지침을 따라가게
        pre, rest = cur.split(MARK_START, 1)
        old_body, post = rest.split(MARK_END, 1)
        if old_body.strip("\n") == body.rstrip():
            return []
        md.write_text(f"{pre}{MARK_START}\n{body.rstrip()}\n{MARK_END}{post}")
        return [f"{target} 지침 블록 갱신: {md}"]
    block = f"\n{MARK_START}\n{body.rstrip()}\n{MARK_END}\n"
    md.parent.mkdir(parents=True, exist_ok=True)
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
    rest = pre.rstrip("\n") + ("\n" if pre.strip() else "") + post.lstrip("\n")
    if not rest.strip() or rest.strip() == AGY_RULE_HEADER.strip():
        # 블록만 있던 파일(원래 없던 폴더에 add가 만든 것) — 빈 파일을 남기지 않는다
        md.unlink()
        return [f"{target} 지침 블록 제거 후 빈 파일 삭제: {md}"]
    md.write_text(rest)
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
    engine = bot["engine"]
    eng_bin = shutil.which(engine) or ""
    lines = [
        f"# folder-bot {engine} 봇 인스턴스: {bot['name']}",
        "DISCORD_TOKEN=",
        "ALLOWED_USER_IDS=",
        f"CODEX_WORKDIR={bot['folder']}",   # 엔진 무관 키 이름 — 브리지·관제탑(agentlayer) 매칭 키
        *( ["ENGINE=agy"] if engine == "agy" else [] ),
        *( [f"{'AGY_BIN' if engine == 'agy' else 'CODEX_BIN'}={eng_bin}"] if eng_bin else [] ),
        f"DATA_DIR=data-{bot['name']}",
        f"TUI_PANE={bot['session']}:0.0",
        "TUI_CHANNEL_ID=",
        "CHANNEL_IDS=",
        "TUI_TRIGGER_GATE=off",  # 전용 채널 — 호명 없이 모든 메시지에 응답
    ]
    if engine == "codex" and host_os() == "linux" and not has_systemd():
        # 도커 컨테이너: codex 샌드박스(bwrap)가 네임스페이스를 못 만들어 셸 명령마다 승인 프롬프트 → 무인 pane 정지
        # (2026-09-11 실측). 컨테이너가 바깥 샌드박스이므로 codex 것을 끈다(codex-discord 0.1.15+ tui-up.sh).
        lines += ["CODEX_TUI_SANDBOX=off"]
    p.write_text("\n".join(lines) + "\n")
    p.chmod(0o600)
    (Path(bot["bridge_dir"]) / f"data-{bot['name']}").mkdir(exist_ok=True)
    return [f"브리지 인스턴스 생성: {p} (토큰·채널은 pair에서)"]


def ensure_codex_trust(folder: str) -> list[str]:
    """작업 폴더를 codex 신뢰 목록(~/.codex/config.toml)에 선등록 — 멱등, 섹션은 파일 끝에 추가.
    미신뢰 새 폴더면 TUI 첫 화면이 "Do you trust the contents of this directory?"라 무인 기동이 멈춘다
    (WSL2 실기 2026-09-12: tui-up.sh가 이 화면을 준비로 오판 → 더미 턴이 "No, quit" 선택). codex-discord install.sh와 동형."""
    cfg = Path.home() / ".codex" / "config.toml"
    key = f'[projects."{folder}"]'
    section = f'{key}\ntrust_level = "trusted"\n'
    if cfg.exists():
        text = cfg.read_text()
        if key in text:
            return []
        if text and not text.endswith("\n"):
            text += "\n"
        text += "\n" + section if text else section
    else:
        cfg.parent.mkdir(parents=True, exist_ok=True)
        text = section
    cfg.write_text(text)
    cfg.chmod(0o600)
    return [f"codex 신뢰 등록: {folder} (~/.codex/config.toml)"]


def codex_labels(bot: dict) -> tuple[str, str]:
    return f"com.codex-discord.{bot['name']}", f"com.codex-discord.{bot['name']}-tui"


def daemon_session(bot: dict) -> str:
    """systemd 없음 폴백의 데몬 tmux 세션명(관제탑은 kind 없음으로 무시)."""
    return f"{bot['name']}-daemon"


def write_codex_sidecars(bot: dict, node: str, path_env: str) -> list[str]:
    """systemd 없음(컨테이너): 유닛 대신 사이드카만 — 데몬은 tmux 세션 <이름>-daemon, TUI는 tui-up.sh.
    데몬 pane 루트는 셸이어야 한다(exec 없이 node를 자식으로 — 관제탑이 브리지 자식 codex/agy를
    유령 레코드로 잡지 않게, 2026-09-11 agentlayer 접점 확인). 재기동은 <세션>.up.sh(bot-restart·외부 감시자)."""
    bd, name = bot["bridge_dir"], bot["name"]
    daemon_l, _ = codex_labels(bot)
    ds = daemon_session(bot)
    # 끝의 `exit $?`는 bash 5.1+가 -c 목록의 마지막 명령을 exec로 바꾸는 최적화를 막는다(그러면 pane 루트가 node가 됨).
    cmd = (f"/bin/bash -lc 'cd {bd}; export PATH=\"{path_env}\"; "
           f"{node} --env-file=.env.{name} src/index.mjs >> logs/daemon-{name}.log 2>&1; exit $?'")
    write_tmux_unit(daemon_l, ds, f"folder-bot {bot['engine']} daemon: name={name}", cmd)
    cmd_file, up = sidecar_paths(bot["session"])
    tui = f"/bin/bash {bd}/scripts/tui-up.sh .env.{name}"
    cmd_file.write_text(tui + "\n")
    up.write_text(f"#!/bin/bash\nexec {tui}\n")
    up.chmod(0o755)
    return [f"[WARN] systemd 없음 — 유닛 생략, 사이드카만: {sidecar_paths(ds)[0]}·{cmd_file}"
            f" (데몬은 tmux 세션 {ds}, 자동 기동 없음 — 재기동은 사이드카 up.sh·외부 감시자 몫)"]


def write_codex_units(bot: dict) -> list[str]:
    """리눅스: 데몬 = node 상주(simple, Restart=always ↔ launchd KeepAlive), TUI = tmux 세션 oneshot.
    codex-discord scripts/install.sh 리눅스 분기와 동형."""
    daemon_l, tui_l = codex_labels(bot)
    if not bot["autostart"]:
        return [f"{line}(autostart off)" for line in
                remove_units([daemon_l, tui_l], [bot["session"]], stop=False)]
    node = find_node()
    d = service_dir()
    d.mkdir(parents=True, exist_ok=True)
    path_env = f"{Path(node).parent}:/usr/local/bin:/usr/bin:/bin"
    bd = bot["bridge_dir"]
    Path(bd, "logs").mkdir(exist_ok=True)
    if not has_systemd():
        return write_codex_sidecars(bot, node, path_env)
    out = []
    eng = bot["engine"]   # 0.1.18: Description을 엔진명으로(agy 유닛이 "codex"로 보이던 문제)
    daemon_body = (f"# folder-bot codex daemon: name={bot['name']} folder={bot['folder']}\n"
                   f"[Unit]\nDescription={daemon_l} (Discord ↔ {eng} 브리지)\nAfter=network-online.target\n\n"
                   f"[Service]\nType=simple\nWorkingDirectory={bd}\nEnvironment=\"PATH={path_env}\"\n"
                   f"ExecStart={node} --env-file=.env.{bot['name']} src/index.mjs\nRestart=always\nRestartSec=15\n"
                   f"StandardOutput=append:{bd}/logs/daemon-{bot['name']}.log\n"
                   f"StandardError=append:{bd}/logs/daemon-{bot['name']}.log\n\n"
                   "[Install]\nWantedBy=default.target\n")
    tui_body = (f"# folder-bot codex tui: name={bot['name']} session={bot['session']} folder={bot['folder']}\n"
                f"[Unit]\nDescription={tui_l} ({eng} TUI tmux 세션 {bot['session']})\nAfter=network-online.target\n\n"
                f"[Service]\nType=oneshot\nRemainAfterExit=yes\nKillMode=process\nWorkingDirectory={bd}\n"
                f"Environment=\"PATH={path_env}\"\nExecStart=/bin/bash {bd}/scripts/tui-up.sh .env.{bot['name']}\n"
                f"ExecStop=-{find_tmux()} kill-session -t {bot['session']}\n"   # `-`: 세션이 이미 없어도 exit 1을 실패로 안 남김(WSL2 실측 9/12)

                f"StandardOutput=append:{bd}/logs/tui-up-{bot['name']}.log\n"
                f"StandardError=append:{bd}/logs/tui-up-{bot['name']}.log\n\n"
                "[Install]\nWantedBy=default.target\n")
    for label, body in ((daemon_l, daemon_body), (tui_l, tui_body)):
        u = d / unit_name(label)
        if not (u.exists() and u.read_text() == body):
            u.write_text(body)
            out.append(f"유닛 생성: {u}")
    systemctl_user("daemon-reload")
    # enable만 — 첫 기동은 start 명령의 몫(claude 엔진 write_unit·macOS plist와 동형). --now로 띄우면
    # 페어링 전에 토큰 없는 데몬이 15초마다 재시작을 반복하고(Restart=always) TUI 유닛은 failed로 남는다
    # (WSL2 실기 2026-09-12, add가 tui-up.sh 180초 대기를 안고 3분 걸림).
    systemctl_user("enable", unit_name(tui_l))
    systemctl_user("enable", unit_name(daemon_l))
    enable_linger()
    return out


def write_codex_plists(bot: dict) -> list[str]:
    import plistlib
    if host_os() == "linux":
        return write_codex_units(bot)
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
    if is_bridge(bot):
        if not Path(bot["bridge_dir"]).is_dir():
            sys.exit(f"오류: codex-discord 브리지 폴더 없음: {bot['bridge_dir']}\n"
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


# 스레드 라이브 뷰 훅 2종 — UserPromptSubmit: 스레드 메시지를 스레드 세션으로 라우팅(메인 처리 차단, exit 2) /
# Stop: 스레드 세션의 답변을 REST로 게시. 둘 다 해당 안 되는 세션에서는 즉시 exit 0.
THREAD_HOOKS = {"UserPromptSubmit": "bash ~/.local/bin/bot-thread-route",
                "Stop": "bash ~/.local/bin/bot-thread-stop",
                "PreCompact": "bash ~/.local/bin/bot-thread-compact"}


def write_thread_hooks(bot: dict) -> tuple[list[str], list[str]]:
    """<폴더>/.claude/settings.local.json hooks.<이벤트>에 병합(기존 훅 보존). 주입 명령 목록을 반환해 remove가 회수한다."""
    p = Path(bot["folder"]) / ".claude/settings.local.json"
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return [f"[WARN] settings.local.json 파싱 실패 — 스레드 훅 미주입: {p}"], []
    hooks = data.setdefault("hooks", {})
    added = []
    for event, cmd in THREAD_HOOKS.items():
        groups = hooks.setdefault(event, [])
        if any(h.get("command") == cmd for grp in groups for h in (grp.get("hooks") or [])):
            continue
        groups.append({"hooks": [{"type": "command", "command": cmd}]})
        added.append(event)
    if added:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return ([f"스레드 훅 주입({'·'.join(added)}): {p}"] if added else []), list(THREAD_HOOKS.values())


# auto 권한 분류기가 지침의 `bot-restart`·`bot-thread` 실행을 막은 실측(2026-09-11 컨테이너) — 폴더 로컬 허용 규칙
PERM_ALLOW = ["Bash(bot-restart:*)", "Bash(bot-thread:*)"]


def write_permissions(bot: dict) -> tuple[list[str], list[str]]:
    """<폴더>/.claude/settings.local.json permissions.allow에 병합(기존 규칙 보존). 주입 목록을 반환해 remove가 회수한다."""
    p = Path(bot["folder"]) / ".claude/settings.local.json"
    try:
        data = json.loads(p.read_text()) if p.exists() else {}
    except ValueError:
        return [f"[WARN] settings.local.json 파싱 실패 — 권한 미주입: {p}"], []
    allow = data.setdefault("permissions", {}).setdefault("allow", [])
    added = [r for r in PERM_ALLOW if r not in allow]
    if added:
        allow.extend(added)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    recorded = sorted(set(bot.get("perm_allow") or []) | set(added))
    return ([f"권한 주입({'·'.join(added)}): {p}"] if added else []), recorded


def remove_permissions(bot: dict) -> list[str]:
    """주입 기록이 있을 때만, 정확히 그 규칙만 걷는다(사용자 규칙 보존)."""
    rules = set(bot.get("perm_allow") or [])
    if not rules:
        return []
    p = Path(bot["folder"]) / ".claude/settings.local.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    perms = data.get("permissions") or {}
    allow = perms.get("allow") or []
    kept = [r for r in allow if r not in rules]
    if kept == allow:
        return []
    if kept:
        perms["allow"] = kept
    else:
        perms.pop("allow", None)
    if not perms:
        data.pop("permissions", None)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return [f"권한 회수: {p}"]


def remove_thread_hooks(bot: dict) -> list[str]:
    """주입 기록이 있을 때만, 정확히 그 명령의 훅 항목만 걷는다(사용자 훅 보존)."""
    cmds = set(bot.get("thread_hooks") or [])
    if not cmds:
        return []
    p = Path(bot["folder"]) / ".claude/settings.local.json"
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return []
    hooks = data.get("hooks") or {}
    changed = False
    for event in list(hooks):
        groups = hooks[event] or []
        kept = []
        for grp in groups:
            hs = [h for h in (grp.get("hooks") or []) if h.get("command") not in cmds]
            if hs:
                kept.append({**grp, "hooks": hs})
        if kept != groups:
            changed = True
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
    if not changed:
        return []
    if not hooks:
        del data["hooks"]
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return [f"스레드 훅 회수: {p}"]


def cmd_add(a) -> None:
    bots = load_bots()
    prior = bots.get(a.name) or {}
    entry = {"engine": a.engine, "folder": a.folder, "session": a.session}
    for k in ("statusline_cmd", "thread_hooks", "perm_allow"):
        if k in prior:
            entry[k] = prior[k]  # 재등록에도 회수 기록 유지
    if a.engine in BRIDGE_ENGINES:
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
    if bot["engine"] == "codex":
        lines += ensure_codex_trust(bot["folder"])
    if bot["engine"] == "claude":
        sl_lines, injected = write_statusline(bot)
        lines += sl_lines
        if injected:
            entry["statusline_cmd"] = injected
            save_bots(bots)
        th_lines, th_cmds = write_thread_hooks(bot)
        lines += th_lines
        if th_cmds:
            entry["thread_hooks"] = th_cmds
            save_bots(bots)
        pm_lines, pm_rules = write_permissions(bot)
        lines += pm_lines
        if pm_rules:
            entry["perm_allow"] = pm_rules
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
    if is_bridge(bot) and host_os() == "linux":
        daemon_l, tui_l = codex_labels(bot)
        subprocess.run([find_tmux(), "kill-session", "-t", daemon_session(bot)], capture_output=True)
        for line in remove_units([daemon_l], [daemon_session(bot)], stop=True):   # 데몬은 Restart=always라 내리고 지운다
            print(line)
        subprocess.run([find_tmux(), "kill-session", "-t", bot["session"]], capture_output=True)
        for line in remove_units([tui_l], [bot["session"]], stop=False):
            print(line)
        print(f"제거됨: {a.name} (토큰 파일 보존: {codex_env_path(bot)})")
        return
    if is_bridge(bot):
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
    for line in remove_thread_hooks(bot):
        print(line)
    for line in remove_permissions(bot):
        print(line)
    subprocess.run([str(home() / ".local/bin/bot-thread"), "gc", a.name, "--all"],
                   capture_output=True)  # 스레드 창만 정리, threads.json은 보존(토큰 파일과 같은 취급)
    if host_os() == "linux":
        # disable만(--now 없이) — 세션 종료는 stop 명령의 몫(macOS remove와 동형: 파일만 걷는다)
        for line in remove_units([f"com.folder-bot.{bot['name']}"], [bot["session"]], stop=False):
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

    if is_bridge(bot):
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
    if is_bridge(bot):
        tui_up = f"{bot['bridge_dir']}/scripts/tui-up.sh"
        daemon_l, tui_l = codex_labels(bot)
        tui_u, daemon_u = unit_name(tui_l), unit_name(daemon_l)
        if host_os() == "linux" and has_systemd() and (service_dir() / tui_u).exists():
            # 유닛 경유 — tui-up.sh를 직접 돌리면 세션은 살아도 TUI 유닛이 inactive로 남아 systemctl status·
            # is-system-running과 실물이 어긋난다(WSL2 실기 2026-09-12 발견③). oneshot이라 start가 tui-up.sh 완주까지
            # 막히고 실패면 비0. 세션은 죽었는데 유닛이 active(exited)면 start가 무동작이라 restart(ExecStop의
            # kill-session은 세션이 없어도 무해).
            log = f"{bot['bridge_dir']}/logs/tui-up-{bot['name']}.log"
            if a.dry_run:
                print(f"systemctl --user start {tui_u} {daemon_u}")
                return
            alive = subprocess.run([tmux, "has-session", "-t", bot["session"]], capture_output=True).returncode == 0
            verb = "restart" if (not alive and systemctl_user("is-active", tui_u) == 0) else "start"
            systemctl_user("reset-failed", tui_u)
            if systemctl_user(verb, tui_u) != 0:
                sys.exit(f"오류: TUI 기동 실패 — {log} 확인")
            systemctl_user("start", daemon_u)
            print(f"기동: {bot['session']} (유닛 {tui_u}) + 데몬 {daemon_u} (systemd --user) — TUI 로그 {log}")
            return
        if a.dry_run:
            print(f"{tui_up} .env.{bot['name']} + launchctl bootstrap "
                  f"com.codex-discord.{bot['name']}")
            return
        r = subprocess.run([tui_up, f".env.{bot['name']}"], capture_output=True, text=True)
        print(r.stdout.strip())
        if r.returncode != 0:
            sys.exit(f"오류: TUI 기동 실패 — {bot['bridge_dir']}/logs 확인")
        if host_os() == "linux":
            if has_systemd():   # 유닛 없음(autostart off): TUI는 위에서 직접, 데몬만 유닛
                systemctl_user("start", daemon_u)
                print(f"데몬 기동: {daemon_l} (systemd --user)")
                return
            ds = daemon_session(bot)
            if subprocess.run([tmux, "has-session", "-t", ds], capture_output=True).returncode == 0:
                print(f"데몬 이미 실행 중: {ds}")
                return
            subprocess.run(["/bin/bash", str(sidecar_paths(ds)[1])], check=True)
            print(f"데몬 기동: tmux 세션 {ds} (systemd 없음 — 사이드카 up.sh 경유)")
            return
        uid = os.getuid()
        label = f"com.codex-discord.{bot['name']}"
        if subprocess.run(["launchctl", "print", f"gui/{uid}/{label}"],
                          capture_output=True).returncode != 0:
            subprocess.run(["launchctl", "bootstrap", f"gui/{uid}",
                            str(home() / f"Library/LaunchAgents/{label}.plist")], check=True)
            print(f"데몬 기동: {label}")
        return
    argv = [tmux, "new-session", "-d", "-s", bot["session"], build_cmd(bot)]
    unit = plist_path(bot) if host_os() == "linux" and bot["autostart"] else None
    if a.dry_run:
        print(f"systemctl --user start {unit.name}" if unit and unit.exists() else " ".join(argv))
        return
    if subprocess.run([tmux, "has-session", "-t", bot["session"]],
                      capture_output=True).returncode == 0:
        print(f"이미 실행 중: {bot['session']}")
        return
    if unit and unit.exists() and not os.environ.get("HARNESS_FAKE_SYSTEMCTL"):
        # 유닛 경유 기동 — systemd가 세션 소유를 알아야 재부팅·stop이 일관된다
        systemctl_user("start", unit.name)
    else:
        subprocess.run(argv, check=True)
    print(f"기동: {bot['session']} — 연결 판정은 MCP 로그(bot-up이 감시)")


def cmd_stop(a) -> None:
    import subprocess
    bot = resolve_bot(a.name)
    if host_os() == "linux" and bot["engine"] == "claude" and plist_path(bot).exists():
        systemctl_user("stop", plist_path(bot).name)   # ExecStop = kill-session
    if host_os() == "linux" and is_bridge(bot) and has_systemd():
        daemon_l, tui_l = codex_labels(bot)
        for u in (unit_name(tui_l), unit_name(daemon_l)):
            if (service_dir() / u).exists():
                systemctl_user("stop", u)   # TUI ExecStop=kill-session, 데몬은 Restart=always라 유닛으로 내려야 안 되살아난다
    if host_os() == "linux" and is_bridge(bot) and not has_systemd():
        subprocess.run([find_tmux(), "kill-session", "-t", daemon_session(bot)], capture_output=True)
    subprocess.run([find_tmux(), "kill-session", "-t", bot["session"]], capture_output=True)
    print(f"중지: {bot['session']}")


def mcp_log_dir(folder: str) -> Path:
    """discord 플러그인 MCP 로그 디렉토리 — 폴더 절대경로의 /·. 을 - 로 치환."""
    cache = "Library/Caches/claude-cli-nodejs" if host_os() == "darwin" else ".cache/claude-cli-nodejs"
    return home() / cache / re.sub(r"[/.]", "-", folder) / "mcp-logs-plugin-discord-discord"


def cmd_doctor(a) -> None:
    import subprocess
    fails = 0
    names = [a.name] if a.name else list(load_bots())
    if host_os() == "linux" and names:
        st = systemd_user_state()
        if st not in ("running", "degraded"):
            print(f"[WARN] systemd --user: {st or '없음'} — 자동 기동 불가"
                  + (" (WSL2: /etc/wsl.conf에 [boot] systemd=true, PowerShell에서 wsl --shutdown 뒤 다시)" if is_wsl()
                     else "" if has_systemd() else " (systemctl 없음: 유닛 없이 사이드카만, 재기동은 외부 몫)"))
    for name in names:
        b = resolve_bot(name)

        def rep(level, msg):
            nonlocal fails
            if level == "FAIL":
                fails += 1
            print(f"[{level}] {name}: {msg}")

        if is_bridge(b):
            la = home() / "Library/LaunchAgents"
            if b["autostart"] and host_os() == "linux" and not has_systemd():
                for sess in (daemon_session(b), b["session"]):
                    if not sidecar_paths(sess)[1].exists():
                        rep("WARN", f"사이드카 없음(add 재실행): {sidecar_paths(sess)[1]}")
            elif b["autostart"] and host_os() == "linux":
                for label in codex_labels(b):
                    if not (service_dir() / unit_name(label)).exists():
                        rep("FAIL", f"유닛 없음: {service_dir() / unit_name(label)}")
            elif b["autostart"]:
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
            if b["autostart"] and not p.exists() and (host_os() != "linux" or has_systemd()):
                rep("FAIL", f"{'유닛' if host_os() == 'linux' else 'plist'} 없음: {p}")
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
        if b["engine"] == "claude":
            for tool in ("bot-thread", "bot-thread-stop", "bot-thread-route", "bot-thread-compact"):
                if not (home() / ".local/bin" / tool).exists():
                    rep("WARN", f"스레드 라이브 뷰 스크립트 없음: ~/.local/bin/{tool} (add 재실행)")
            if not shutil.which("curl"):
                rep("WARN", "curl 없음 — 스레드 답변 게시(REST) 불가")
            if not b.get("thread_hooks"):
                rep("WARN", "스레드 훅(라우팅·게시) 미주입 (add 재실행)")
            tmap = Path(b["state_dir"]) / "threads.json"
            if tmap.exists():
                try:
                    json.loads(tmap.read_text())
                except ValueError:
                    rep("WARN", f"threads.json 손상: {tmap}")
    sys.exit(1 if fails else 0)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    ap = sub.add_parser("add", help="봇 등록 + 설치")
    ap.add_argument("--name", required=True)
    ap.add_argument("--folder", required=True)
    ap.add_argument("--session", required=True)
    ap.add_argument("--engine", choices=["claude", "codex", "agy"], default="claude")
    ap.add_argument("--bridge-dir", help="codex·agy 전용: codex-discord 브리지 폴더")
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
