---
name: configure-bot
description: Use when the user wants to turn a work folder's Claude session into a dedicated Discord channel bot, add/remove/inspect folder bots, or set up Discord remote restart (context recycle). Triggers on "이 폴더를 디스코드 봇으로 만들어줘", "폴더 봇 추가", "폴더 봇 제거", "폴더 봇 점검", "디스코드 봇으로 연결해줘", "/configure-bot". 결정적 엔진(botctl.py)이 bots.json 정본으로 plist·기동 직렬화·원격 재시작·CLAUDE.md 지침 블록을 멱등 설치하고, 수동은 디스코드 포탈 단계뿐(단계별 안내).
---

# configure-bot — 폴더 전용 디스코드 채널 봇

업무 폴더의 Claude 세션을 전용 디스코드 채널 봇으로 만든다. 모든 파일 조작은
결정적 엔진 `generator/botctl.py`가 수행한다 — 직접 plist·설정 파일을 손으로 쓰지 말 것.

**비파괴 원칙 (반드시 지킬 것)**: SESSION.md는 절대 만들거나 고치지 않는다.
CLAUDE.md는 botctl이 마커 블록(`<!-- store:discord-bot:start/end -->`)만 추가·제거한다.
그 밖의 기존 파일은 읽기만 한다.

## 봇 추가 절차

### 1. 전제 점검

- `uname` = Darwin(macOS)인지. 아니면 "현재 macOS만 검증됨(리눅스는 systemd로 대체 가능하나 수동)"을 안내하고 중단.
- discord 공식 플러그인: `~/.claude/plugins/cache/claude-plugins-official/discord/` 존재 확인. 없으면 `/plugin`에서 discord 플러그인 설치 안내 후 중단.
- tmux: `command -v tmux || ls /opt/homebrew/bin/tmux /usr/local/bin/tmux`. 없으면 `brew install tmux` 안내 후 중단.
- **워크스페이스 신뢰**: 대상 폴더가 현재 폴더면 이미 신뢰 수락된 상태다(사용자가 열면서 수락).
  **다른 폴더를 원격 설치하는 경우** `~/.claude.json`의 `projects.<폴더>.hasTrustDialogAccepted`를
  읽어(읽기 전용) false·부재면 안내한다: "그 폴더에서 claude를 한 번 열어 신뢰를 수락해야
  봇 세션이 승인 다이얼로그에 막히지 않습니다" (2026-08-01 collab 실측 — 미신뢰 워크스페이스는
  프로젝트 MCP 승인이 세션 한정이라 재시작마다 다이얼로그에 걸린다).

### 2. 질문 (AskUserQuestion 한 번에)

- 봇 이름(영문 소문자, 예: collab)
- 폴더(기본 = 현재 폴더)
- tmux 세션명(기본 = `<이름>-bot`)
- 리모트 컨트롤 켤지(기본 = 켬 — 폰 claude.ai에서 세션 진입 가능)

AskUserQuestion 도구가 없는 환경(예: codex)에서는 같은 4개를 **채팅으로 질문해 답을 받는다**.
묻지 않고 기본값을 스스로 확정해 진행하지 말 것(2026-08-02 codex 실측 — 질문을 건너뛰고
기본값으로 질주한 편차가 있었다).

### 3. 등록·설치

```bash
python3 "<이 스킬 폴더>/generator/botctl.py" add --name <이름> --folder <폴더> --session <세션명>
```

(리모트 컨트롤 끄면 `--no-remote-control`, 자동 기동 원치 않으면 `--no-autostart`,
지침 블록이 이미 자체 규칙으로 있는 폴더 — 예: 멀티 에이전트 하네스의 오케스트레이터 — 는
`--no-directive-block`.) 출력을 그대로 보여준다.

usage-coach(대시보드)가 설치돼 있으면 add가 봇 폴더 `.claude/settings.local.json`에
statusLine을 자동 주입한다(대시보드 클로드 카드의 데이터원 — 미주입 시 카드가 영구 공백,
2026-08-05 실측). 사용자가 이미 설정한 statusLine은 건드리지 않고, remove가 주입분만 회수한다.
usage-coach가 없으면(단독 설치) 건너뛴다 — 정상이다.

**프로젝트 MCP 주의**: 폴더에 프로젝트 MCP 서버가 설정돼 있으면(.mcp.json 또는
프로젝트 설정), 봇 세션이 뜰 때마다 승인 다이얼로그에 걸려 **무인 재시작이 막힌다**.
이 경우 사용자에게 "이 폴더의 프로젝트 MCP를 자동 허용할까요?"를 확인받고, 동의하면
add에 `--allow-project-mcp`를 붙인다(`.claude/settings.local.json`에
`enableAllProjectMcpServers: true` 병합 — 기존 키 보존). 동의하지 않으면 재시작 후
다이얼로그를 수동으로 풀어야 함을 안내한다.

### 4. 디스코드 포탈 수동 단계 (순서대로 안내, 사용자가 끝냈다고 할 때까지 대기)

1. https://discord.com/developers/applications → **New Application** → 이름 입력
2. **Bot** 탭 → **Reset Token** → 토큰 복사(한 번만 보임).
   **복사한 토큰은 채팅에 붙여넣지 않게 안내한다** — 대상 폴더에 파일로 저장하게 한다:
   `pbpaste > .bot-token && chmod 600 .bot-token` (클립보드에서 바로 파일로 — 셸 히스토리에도 안 남는다)
3. 같은 화면 Privileged Gateway Intents에서 **MESSAGE CONTENT INTENT** 켜기 → Save
4. **OAuth2 → URL Generator**: scope `bot` 체크, Bot Permissions에서
   View Channels / Send Messages / Read Message History / Embed Links / Attach Files 체크
   → 생성된 URL을 브라우저로 열어 서버에 초대
5. 디스코드 서버에 전용 텍스트 채널 생성(예: #협업)
6. 디스코드 설정 → 고급 → 개발자 모드 켠 뒤: 채널 우클릭 → **채널 ID 복사**,
   내 프로필 우클릭 → **사용자 ID 복사**

### 5. 페어링

**토큰은 채팅으로 받지 않는다** — 대화 로그·모델 서버 전송·화면 노출 위험(매뉴얼 1장 원칙과 동일).
사용자 ID·채널 ID만 채팅으로 받고, 토큰은 §4에서 만든 `.bot-token` 파일에서 읽는다:

```bash
python3 "<이 스킬 폴더>/generator/botctl.py" pair --name <이름> --token-file <폴더>/.bot-token --user-id <ID> --channel-id <ID>
```

- **사용자 ID = 사람 계정 ID다**(봇 ID 아님 — 내 프로필 우클릭 → 사용자 ID 복사). 봇 ID를 넣으면
  페어링은 되지만 채널 메시지가 전부 무시된다(allowFrom 불일치).
- 토큰 값은 화면에 출력하지 않는다. `--token-file`이면 botctl이 페어링 성공 직후 파일을
  **직접 삭제**한다("토큰 파일 삭제" 출력으로 확인 — 평문 잔존 방지). 실패 시엔 보존된다.
- 이미 페어링돼 있으면 botctl이 거부한다(덮으려면 `--force`).

### 6. 기동·연결 판정

```bash
python3 "<이 스킬 폴더>/generator/botctl.py" start --name <이름>
```

판정: 그 봇의 MCP 로그 디렉토리
`~/Library/Caches/claude-cli-nodejs/<폴더 절대경로의 / 와 . 을 - 로 치환>/mcp-logs-plugin-discord-discord/`
의 최신 `*.jsonl`에서 `Successfully connected` / `Connection failed`를 확인해 결과를 보고한다.
(기동 직후 파일이 생기기까지 수십 초 걸릴 수 있다 — 잠시 대기 후 재확인.)
실패면 원인 후보를 안내: 토큰 오입력 / MESSAGE CONTENT INTENT 미설정 / 서버 초대 안 됨.

**실패·미기동 폴백** — 첫 MCP spawn이 로그 파일조차 안 남기고 조용히 죽는 증상이
실측됐다(2026-08-05, Claude Code 본체 이슈 추정). 로그가 아예 안 생기거나 실패면:

1. **재기동 1회만**: `stop` → `start` (또는 `bot-restart <세션명>`) 후 로그 재확인.
2. 같은 실패면 재기동을 반복하지 않는다(수렴하지 않는 경우가 실측됨). **예열 복구 1회**:
   봇 폴더에서 아래 형태로 `plugin:discord:discord`가 `✔ Connected`인지 확인한 뒤
   다시 재기동 → 로그 재확인.

   ```bash
   DISCORD_STATE_DIR=<폴더>/.discord-state claude mcp list
   ```

   **`DISCORD_STATE_DIR` 없이 그냥 실행하면 안 된다** — 서버가 전역
   `~/.claude/channels/discord/.env`를 찾다 `DISCORD_BOT_TOKEN required`로 죽고(폴더봇은
   100% 실패, 2026-08-06 실측), 그 실패 로그가 MCP 로그 디렉토리에 남아 doctor 판정까지
   오염시킨다.
3. 그래도 실패면 멈추고 `doctor` 결과와 원인 후보(토큰/인텐트/초대)를 보고한다.
   시간 경과 후 재기동이 성공한 실측이 있다 — 무한 재시도로 시간을 태우지 말 것.

### 7. 마무리 안내

- 채널에서 인사 한 번 시켜 보라고 안내.
- 컨텍스트가 차면(대화가 길어지면) 채널에서 **"세션 마감하고 재시작해"** → 성패 웹훅 알림
  (선택: `~/.config/folder-bot/config.json`에 `{"webhook_url": "..."}`) → **"이어서하자"**.
- 부팅 자동 기동은 다음 재부팅부터. 지금 당장은 이미 `start`로 떠 있다.

## codex 엔진 봇 (폴더가 codex로 운용되는 경우)

폴더를 codex(코덱스 CLI) 세션으로 쓰는 사용자는 `--engine codex`로 추가한다.
전제: codex-discord 브리지(github.com/netwaif/codex-discord)가 설치돼 있어야 하며,
경로는 `~/.config/folder-bot/config.json`의 `codex_bridge_dir`(또는 `--bridge-dir`)로 지정.

```bash
python3 "<이 스킬 폴더>/generator/botctl.py" add --name <이름> --folder <폴더> --session <세션명> --engine codex
python3 "<이 스킬 폴더>/generator/botctl.py" pair --name <이름> --token-file <폴더>/.bot-token --user-id <ID> --channel-id <ID>
python3 "<이 스킬 폴더>/generator/botctl.py" start --name <이름>
```

- add = 브리지 인스턴스(.env.<이름>·data-<이름>) + 데몬·TUI plist + **AGENTS.md** 지침 블록
  (전용 채널이라 호명 게이트 off — `TUI_TRIGGER_GATE=off`).
- **리모트 컨트롤 질문은 생략한다** — claude 전용 개념(claude.ai 세션 진입)이라 codex 엔진
  기동 명령에는 쓰이지 않는다(botctl이 값을 무시).
- start = TUI 기동(tui-up.sh, "준비 완료" 확인) + 데몬 bootstrap. 연결 판정은 브리지 로그
  (`<브리지>/logs/daemon-<이름>.log`)의 "로그인:" 줄로 확인해 보고한다.
- 재시작 리추얼은 동일: "세션 마감하고 재시작해" → tui-restart.sh(웹훅 통지) → "이어서하자".
- 디스코드 포탈 수동 단계는 claude와 완전히 같다(위 4번 절).

## 봇 제거

```bash
python3 "<이 스킬 폴더>/generator/botctl.py" stop --name <이름>
python3 "<이 스킬 폴더>/generator/botctl.py" remove --name <이름>
```

페어링 파일(.discord-state)은 보존된다(재추가 대비). launchctl bootout은 쓰지 않는다.

## 점검

```bash
python3 "<이 스킬 폴더>/generator/botctl.py" doctor
python3 "<이 스킬 폴더>/generator/botctl.py" list
```

doctor는 읽기 전용 — bots.json ↔ plist ↔ 지침 블록 ↔ 페어링 ↔ tmux 세션 생존 ↔
MCP 연결(claude 엔진, 최신 로그 기준 — 낡은 성공 로그 오판 방지)을 대조 보고한다.
