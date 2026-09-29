---
name: bot-start
description: Use when the user wants to turn an already-registered folder bot on or off, restart it, or check whether bots are alive. 등록된 폴더 봇(claude·codex·agy 엔진)을 이름으로 찾아 켜고·끄고·재시작하고 상태를 점검한다. Triggers on "xx 폴더 bot 시작해줘", "xx 봇 켜줘", "xx 봇 살려줘", "xx 봇 꺼줘", "안 쓰는 봇 꺼줘", "xx 봇 재시작해줘", "봇 다 살아 있어?", "봇 상태 보여줘", "재부팅하고 봇이 꺼졌어", "/bot-start". 사용자가 봇 이름(봇 키·폴더명·세션명·별칭)과 시작/켜/살려/꺼/재시작/상태를 함께 말하면 이 스킬이다. 새 봇 추가·제거·설치 점검(doctor)은 이 스킬이 아니라 configure-bot이다.
---

# bot-start — 폴더 봇 기동·종료·재시작·상태

봇 목록은 스킬에 적혀 있지 않다. 스크립트가 정본 `~/.config/folder-bot/bots.json`(configure-bot의
botctl이 관리)을 매번 읽어 이름 매칭 → 상태 확인 → 맞는 기동 경로를 처리한다. 명령을 외우거나
tmux·launchctl·systemctl을 직접 부르지 말 것 — OS·엔진 분기는 스크립트와 botctl이 진다.

## 스크립트

```bash
S="<이 스킬 폴더>/scripts/botstart.py"
python3 "$S" list                      # 전체 상태표 (✅/❌, pane 프로세스, 브리지 데몬 PID)
python3 "$S" status <이름>              # 한 봇
python3 "$S" start <이름>               # 꺼져 있을 때만 기동, 켜져 있으면 아무것도 안 함
python3 "$S" start <이름> --restart     # 켜져 있어도 재시작
python3 "$S" stop <이름>                # 세션 통째로 종료(스레드 창 포함)
python3 "$S" stop <이름> --force        # 작업 중이거나 자기 세션이어도 종료
```

이름은 봇 키·tmux 세션명·폴더명 어느 것이든 된다. "폴더", "봇", "bot"은 무시한다
(예: 봇 키가 `notes`면 `notes`, `notes-bot`, `notes 폴더 bot` 모두 같은 봇).

한글 별칭을 쓰고 싶으면 선택 파일 `~/.config/folder-bot/aliases.json`을 둔다(없어도 된다).
사용자가 별칭을 부탁할 때만 만들고, 기존 내용은 보존하며 키만 추가한다:

```json
{"notes": ["노트", "메모"]}
```

## 기동 경로 (스크립트가 고른다)

claude 엔진은 세션이 살아 있으면 `bot-restart <세션>`으로 pane만 갈아 끼우고 그 로그의 판정 줄을 읽는다.
세션이 없으면 `botctl start`(부팅 때와 같은 경로)로 띄운 뒤 discord MCP 로그에서 연결 판정을 기다린다.

codex·agy 엔진(브리지)은 세션이 살아 있으면 `<브리지>/scripts/tui-restart.sh .env.<이름>`으로 TUI를 갈아 끼우고,
데몬(`data-<이름>/daemon.pid`)이 죽어 있으면 `botctl start`로 보강한다. 세션이 없으면 `botctl start`가 TUI와 데몬을 함께 띄운다.

종료는 `botctl stop`을 거친다(리눅스 systemd 유닛 상태가 실물과 어긋나지 않게).

## 절차

0. "꺼줘/종료/내려줘"면 `stop <이름>`. 종료 코드 4(자기 세션)·5(작업 중)로 거부되면 이유를 그대로 전하고,
   사용자가 "그래도 꺼"라고 할 때만 `--force`. 이름이 정확히 안 맞으면(코드 3) 후보를 보여주고 묻는다 —
   stop은 부분 일치를 받지 않는다(엉뚱한 봇을 끄지 않게).
1. 사용자 말에서 봇 이름을 뽑아 `start <이름>`을 실행한다. 사용자가 "재시작"이라고 하면 `--restart`를 붙이고,
   "시작/켜/살려"라고 하면 붙이지 않는다.
2. 종료 코드 3(매칭 없음·여러 개)이면 출력된 후보를 보여주고 어느 봇인지 묻는다. 추측으로 고르지 않는다.
3. 완료까지 claude는 최대 5분 30초, 브리지는 최대 7분 걸린다. Bash timeout은 600000으로 준다.
4. 보고: 판정 줄(✅/❌/⏱)과 최종 상태 줄을 그대로 전한다. ✅여도 "채널에서 한마디 걸어 답이 오는지
   확인해 달라"고 덧붙인다. 스크립트는 연결까지만 보고, 실제 대화 응답은 보지 못한다.
5. ❌·⏱이면 `tmux capture-pane -p -t <세션>:`로 화면을 읽어 원인을 보고한다. 화면에 승인 다이얼로그
   (예: MCP 서버 승인·워크스페이스 신뢰)가 떠 있으면 사용자에게 그대로 알린다. 대신 누르지 않는다.
   설치 상태가 의심되면 configure-bot의 `botctl.py doctor --name <이름>`을 돌려 함께 보고한다.
   로그가 아예 안 생기는 ⏱는 configure-bot SKILL.md의 "무로그 불발 폴백"(유령 리스, 약 90분)을 안내한다.

## 주의

- **자기 자신 재시작**: 이 세션이 그 봇의 세션이면 `start <이름> --restart`는 자기 pane을 갈아 끼운다.
  재시작 작업은 tmux 서버에 위탁되므로 완주하지만, 그 전에 세션 마감 기록을 먼저 한다
  (폴더 CLAUDE.md 지침 블록의 재시작 절차).
- 여러 봇이 전부 꺼졌으면(재부팅·업데이트 후) `list`로 ❌만 골라 하나씩 `start`한다.
  동시에 여러 개를 띄우면 부하로 판정이 늦어진다(bot-up이 기동을 직렬화한다).
- 봇이 작업 중일 때 `--restart`하면 진행 중인 작업이 끊긴다. 재시작 전에 사용자에게 확인한다.
- stop의 "작업 중" 판정은 agentlayer(관제탑)가 설치된 환경에서만 동작한다(`agentlayer status`의 WORK,
  `agentlayer task list`의 배정 업무). 없으면 자기 세션 여부만 막는다.
- 자동 기동을 켠 봇은 끈 채로 유지되다가 재부팅·로그인 때 다시 켜진다. 계속 꺼 두려면 configure-bot으로
  `--no-autostart` 재등록한다.
- 로그: claude `~/.claude/logs/bot-restart.log`, 브리지 `<브리지>/logs/tui-restart.log`.
