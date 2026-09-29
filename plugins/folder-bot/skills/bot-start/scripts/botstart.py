#!/usr/bin/env python3
"""폴더 봇 기동기 — 이름으로 봇을 찾아 상태 확인·기동·재시작·종료.

사용:
  botstart.py list                      전체 봇 상태표
  botstart.py status <이름>              한 봇 상태
  botstart.py start <이름> [--restart]   꺼져 있으면 기동(켜져 있으면 --restart일 때만 재시작)
  botstart.py stop <이름> [--force]      세션 통째로 종료(스레드 창 포함). 작업 중·자기 세션이면 --force 없이는 거부

봇 목록은 내장하지 않는다 — 정본 ~/.config/folder-bot/bots.json(botctl이 관리)에서 매번 읽는다.
이름은 봇 키·tmux 세션명·폴더명, 그리고 선택 파일 ~/.config/folder-bot/aliases.json({"봇키": ["별칭", …]})의
별칭으로 찾는다. OS·엔진 분기는 botctl(start/stop)에 맡긴다.

기동 경로:
  - claude, 세션 있음: bot-restart <세션>(pane 교체 — kill-session 재기동과 달리 유령 리스를 만들지 않는다)
  - claude, 세션 없음: botctl start(부팅 때와 같은 정본 경로) 뒤 discord MCP 로그로 연결 판정
  - codex·agy(브리지), 세션 있음: <브리지>/scripts/tui-restart.sh .env.<이름> — 데몬이 죽었으면 botctl start로 보강
  - codex·agy(브리지), 세션 없음: botctl start(TUI + 데몬)

종료 코드: 0 성공 / 1 실패 / 2 사용법·옵션 오류 / 3 이름 매칭 없음·여러 개 / 4 자기 세션 / 5 작업 중
"""
from __future__ import annotations  # macOS 기본 python3(3.9) 호환

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BOTCTL = Path(__file__).resolve().parents[2] / "configure-bot/generator/botctl.py"
_spec = importlib.util.spec_from_file_location("folder_bot_botctl", BOTCTL)
botctl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(botctl)

NOISE = ("폴더", "봇", "bot")   # "xx 폴더 bot 켜줘"의 군말 — 이름 비교에서 뺀다


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def timeout(default: int) -> float:
    return float(os.environ.get("BOTSTART_TIMEOUT", default))


def poll() -> float:
    return float(os.environ.get("BOTSTART_POLL", 3))


def tmux() -> str:
    return botctl.find_tmux()


def load_bots() -> dict:
    """이름 → botctl.resolve_bot 결과(기본값이 채워진 봇 정의)."""
    try:
        names = list(botctl.load_bots())
    except ValueError as err:
        sys.exit(f"오류: bots.json 파싱 실패({botctl.bots_path()}): {err}")
    return {n: botctl.resolve_bot(n) for n in names}


def load_aliases() -> dict:
    """선택 파일 aliases.json — 없거나 깨졌으면 별칭 없이 동작한다."""
    try:
        data = json.loads((botctl.config_dir() / "aliases.json").read_text())
    except (OSError, ValueError):
        return {}
    return {k: [str(a) for a in v] for k, v in data.items() if isinstance(v, list)} if isinstance(data, dict) else {}


def norm(s: str) -> str:
    s = re.sub(r"[\s_\-]", "", s.lower())
    for w in NOISE:
        s = s.replace(w, "")
    return s


def resolve(q: str, bots: dict, exact: bool = False) -> list[str]:
    """정확 일치가 있으면 그것만, 없으면 부분 일치 후보. exact=True면 부분 일치 금지(stop처럼 되돌리기 번거로운 동작용)."""
    nq = norm(q)
    if not nq:
        return []
    aliases = load_aliases()
    exact_hits, part_hits = [], []
    for name, b in bots.items():
        keys = [name, b["session"], os.path.basename(b["folder"].rstrip("/"))] + aliases.get(name, [])
        keys = [k for k in (norm(k) for k in keys) if k]
        if nq in keys:
            exact_hits.append(name)
        elif any(nq in k or k in nq for k in keys):
            part_hits.append(name)
    if exact_hits or exact:
        return exact_hits
    return part_hits


def has_session(session: str) -> bool:
    return sh([tmux(), "has-session", "-t", f"={session}"]).returncode == 0


def pane_cmd(session: str):
    """봇 pane에서 도는 프로세스 이름. 세션이 없으면 None."""
    for target in (f"={session}:0.0", f"={session}:"):   # 0번 창이 없으면(base-index 설정) 활성 창
        r = sh([tmux(), "display-message", "-p", "-t", target, "#{pane_current_command}"])
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return None


def engine_alive(b: dict, cmd) -> bool:
    if not cmd:
        return False
    if b["engine"] == "claude":
        return cmd == "claude" or bool(re.match(r"^\d+\.\d+", cmd))   # claude는 프로세스명이 버전 문자열로 보이기도 한다
    if b["engine"] == "codex":
        return "codex" in cmd or cmd == "node"
    return b["engine"] in cmd


def daemon_pid(b: dict):
    """브리지 데몬 PID — <브리지>/data-<이름>/daemon.pid가 가리키는 프로세스가 살아 있을 때만(OS 무관, doctor와 같은 기준)."""
    try:
        pid = int((Path(b["bridge_dir"]) / f"data-{b['name']}" / "daemon.pid").read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    return pid


def status(b: dict) -> dict:
    cmd = pane_cmd(b["session"])
    st = dict(name=b["name"], engine=b["engine"], session=b["session"], pane=cmd or "-",
              engine_ok=engine_alive(b, cmd))
    if botctl.is_bridge(b):
        st["daemon"] = daemon_pid(b) or "죽음"
    st["ok"] = st["engine_ok"] and st.get("daemon") != "죽음"
    return st


def fmt(st: dict) -> str:
    extra = f" 데몬={st['daemon']}" if "daemon" in st else ""
    return f"{'✅' if st['ok'] else '❌'} {st['name']:<15} {st['engine']:<6} 세션={st['session']:<18} pane={st['pane']}{extra}"


def wait_log(path: Path, offset: int, start_marker: str, end_marker: str, limit: float):
    """로그의 offset 이후에 start_marker…end_marker 구간이 찍힐 때까지 기다렸다가 그 구간을 돌려준다."""
    deadline = time.time() + limit
    while True:
        try:
            with open(path, errors="ignore") as f:
                f.seek(offset)
                txt = f.read()
        except OSError:
            txt = ""
        i = txt.find(start_marker)
        if i >= 0 and end_marker in txt[i:]:
            return txt[i:]
        if time.time() >= deadline:
            return None
        time.sleep(poll())


def log_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def wait_mcp(b: dict, since: float, limit: float):
    """기동 뒤 새로 생긴 discord MCP 로그에서 연결 판정 줄을 찾는다(bot-up·bot-restart와 같은 기준). 없으면 None."""
    d = botctl.mcp_log_dir(b["folder"])
    deadline = time.time() + limit
    while True:
        for f in sorted(d.glob("*.jsonl")) if d.is_dir() else []:
            if f.stat().st_mtime < int(since):
                continue
            m = re.findall(r'Successfully connected[^,"]*|Connection failed[^,"]*', f.read_text(errors="ignore"))
            if m:
                return m[-1]
        if time.time() >= deadline:
            return None
        time.sleep(poll())


def botctl_run(verb: str, b: dict):
    r = sh([sys.executable, str(BOTCTL), verb, "--name", b["name"]])
    out = (r.stdout.strip() + "\n" + r.stderr.strip()).strip()
    if out:
        print(out)
    return r.returncode


def report_segment(seg) -> None:
    if seg is None:
        print("⏱ 제한 시간 내 완료 로그 없음 — pane을 직접 확인할 것")
        return
    verdict = [l for l in seg.splitlines() if "판정" in l or "실패" in l or "경고" in l]
    print("\n".join(verdict) if verdict else seg.strip()[-400:])


def start_claude(b: dict) -> bool:
    """반환 = 판정까지 성공했는지."""
    s = b["session"]
    if has_session(s):
        local = botctl.home() / ".local/bin/bot-restart"
        exe = str(local) if local.exists() else shutil.which("bot-restart")
        if not exe:
            print("bot-restart를 찾을 수 없음 — folder-bot:configure-bot으로 봇을 다시 등록(botctl add)할 것")
            return False
        log = Path(os.environ.get("BOT_RESTART_LOG") or botctl.home() / ".claude/logs/bot-restart.log")
        off = log_size(log)
        r = sh([exe, s])
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode != 0:
            return False
        seg = wait_log(log, off, f"=== {s} 재시작 시작", f"=== {s} 재시작 종료", timeout(330))
        report_segment(seg)
        return seg is not None and "판정: ✅" in seg
    since = time.time()
    if botctl_run("start", b) != 0:
        return False
    line = wait_mcp(b, since, timeout(300))
    if line is None:
        print("⏱ 제한 시간 내 연결 판정 없음 — pane을 직접 확인할 것")
        return False
    ok = line.startswith("Successfully")
    print(f"판정: {'✅' if ok else '❌'} {line}")
    return ok


def start_bridge(b: dict, st: dict, restart: bool) -> bool:
    s = b["session"]
    if not has_session(s):
        return botctl_run("start", b) == 0   # TUI(tui-up.sh "준비 완료"까지) + 데몬
    ok = True
    if restart or not st["engine_ok"]:
        bridge = Path(b["bridge_dir"])
        log = bridge / "logs/tui-restart.log"
        off = log_size(log)
        r = sh([str(bridge / "scripts/tui-restart.sh"), f".env.{b['name']}"], cwd=str(bridge))
        print(r.stdout.strip() or r.stderr.strip())
        if r.returncode != 0:
            return False
        seg = wait_log(log, off, f"=== {s} 재시작 시작", f"=== {s} 재시작 종료", timeout(400))
        report_segment(seg)
        ok = seg is not None and "판정: ✅" in seg
    if not daemon_pid(b):
        print("데몬이 죽어 있음 — botctl start로 보강")
        ok = botctl_run("start", b) == 0 and ok
    return ok


def start(b: dict, restart: bool) -> int:
    st = status(b)
    if st["ok"] and not restart:
        print(f"이미 실행 중 — 아무것도 안 함 (재시작하려면 --restart)\n{fmt(st)}")
        return 0
    judged = start_bridge(b, st, restart) if botctl.is_bridge(b) else start_claude(b)
    final = status(b)
    print(fmt(final))
    return 0 if (judged and final["ok"]) else 1


def own_session():
    """이 스크립트가 tmux 안에서 돌고 있으면 그 세션명."""
    if not os.environ.get("TMUX"):
        return None
    r = sh([tmux(), "display-message", "-p", "#S"])
    return r.stdout.strip() or None


def busy_reasons(b: dict) -> list[str]:
    """끄면 안 되는 이유 — agentlayer(관제탑)가 설치된 환경에서만: 배정된 업무 / 작업 중. 없으면 빈 목록."""
    if not shutil.which("agentlayer"):
        return []
    why = []
    try:
        for t in json.loads(sh(["agentlayer", "task", "list", "--json"]).stdout or "[]"):
            if isinstance(t, dict) and t.get("session") == b["session"]:
                why.append(f"배정된 업무: {t.get('task_id') or t.get('id')} ({t.get('state', '?')})")
    except ValueError:
        pass
    for line in sh(["agentlayer", "status"]).stdout.splitlines():
        cols = line.split()
        if len(cols) >= 3 and cols[2] == b["session"] and re.match(r"\[(WORK|WORKING|BUSY|RUN)", cols[0], re.I):
            why.append(f"작업 중: {line.strip()[:80]}")
    return why


def stop(b: dict, force: bool) -> int:
    s = b["session"]
    if not has_session(s):
        print(f"이미 꺼져 있음 — 아무것도 안 함\n{fmt(status(b))}")
        return 0
    if own_session() == s and not force:
        print(f"끄지 않음 — {s}는 지금 이 명령을 실행 중인 세션이다(끄면 이 세션이 죽는다). 그래도 끄려면 --force")
        return 4
    why = busy_reasons(b)
    if why and not force:
        print("끄지 않음 — 아래 이유. 그래도 끄려면 --force\n- " + "\n- ".join(why))
        return 5
    wins = sh([tmux(), "list-windows", "-t", f"={s}", "-F", "#W"]).stdout.split()
    rc = botctl_run("stop", b)   # OS·엔진 분기(systemd 유닛 정지 등)는 botctl 몫
    if rc != 0 or has_session(s):
        print(f"종료 실패: {s}")
        return 1
    print(f"종료함: {s} 창 {len(wins)}개({', '.join(wins)})")
    print(fmt(status(b)))
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd not in ("list", "status", "start", "stop"):
        print(__doc__)
        return 2
    bots = load_bots()
    if cmd == "list":
        if not bots:
            print("등록된 봇 없음 — 봇 추가는 folder-bot:configure-bot 스킬(\"이 폴더를 디스코드 봇으로 만들어줘\")")
            return 0
        for b in bots.values():
            print(fmt(status(b)))
        return 0
    flags = [a for a in argv[1:] if a.startswith("--")]
    allowed = {"start": {"--restart"}, "stop": {"--force"}, "status": set()}[cmd]
    bad = [f for f in flags if f not in allowed]
    if bad:
        print(f"알 수 없는 옵션: {' '.join(bad)} — 아무것도 안 함")
        return 2
    q = " ".join(a for a in argv[1:] if not a.startswith("--"))
    if not q:
        print(__doc__)
        return 2
    hits = resolve(q, bots, exact=(cmd == "stop"))
    if len(hits) != 1:
        print(f"'{q}' 매칭 {'없음' if not hits else '여러 개: ' + ', '.join(hits)} — 후보: {', '.join(bots) or '(등록된 봇 없음)'}")
        return 3
    b = bots[hits[0]]
    if cmd == "status":
        print(fmt(status(b)))
        return 0
    if cmd == "stop":
        return stop(b, "--force" in flags)
    return start(b, "--restart" in flags)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
