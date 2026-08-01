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

### 2. 질문 (AskUserQuestion 한 번에)

- 봇 이름(영문 소문자, 예: collab)
- 폴더(기본 = 현재 폴더)
- tmux 세션명(기본 = `<이름>-bot`)
- 리모트 컨트롤 켤지(기본 = 켬 — 폰 claude.ai에서 세션 진입 가능)

### 3. 등록·설치

```bash
python3 "<이 스킬 폴더>/generator/botctl.py" add --name <이름> --folder <폴더> --session <세션명>
```

(리모트 컨트롤 끄면 `--no-remote-control`, 자동 기동 원치 않으면 `--no-autostart`,
지침 블록이 이미 자체 규칙으로 있는 폴더 — 예: 멀티 에이전트 하네스의 오케스트레이터 — 는
`--no-directive-block`.) 출력을 그대로 보여준다.

### 4. 디스코드 포탈 수동 단계 (순서대로 안내, 사용자가 끝냈다고 할 때까지 대기)

1. https://discord.com/developers/applications → **New Application** → 이름 입력
2. **Bot** 탭 → **Reset Token** → 토큰 복사(한 번만 보임)
3. 같은 화면 Privileged Gateway Intents에서 **MESSAGE CONTENT INTENT** 켜기 → Save
4. **OAuth2 → URL Generator**: scope `bot` 체크, Bot Permissions에서
   View Channels / Send Messages / Read Message History / Embed Links / Attach Files 체크
   → 생성된 URL을 브라우저로 열어 서버에 초대
5. 디스코드 서버에 전용 텍스트 채널 생성(예: #협업)
6. 디스코드 설정 → 고급 → 개발자 모드 켠 뒤: 채널 우클릭 → **채널 ID 복사**,
   내 프로필 우클릭 → **사용자 ID 복사**

### 5. 페어링

토큰·사용자 ID·채널 ID를 받아:

```bash
python3 "<이 스킬 폴더>/generator/botctl.py" pair --name <이름> --token <토큰> --user-id <ID> --channel-id <ID>
```

토큰은 받은 뒤 화면에 다시 출력하지 않는다. 이미 페어링돼 있으면 botctl이 거부한다(덮으려면 `--force`).

### 6. 기동·연결 판정

```bash
python3 "<이 스킬 폴더>/generator/botctl.py" start --name <이름>
```

판정: 그 봇의 MCP 로그 디렉토리
`~/Library/Caches/claude-cli-nodejs/<폴더 절대경로의 / 와 . 을 - 로 치환>/mcp-logs-plugin-discord-discord/`
의 최신 `*.jsonl`에서 `Successfully connected` / `Connection failed`를 확인해 결과를 보고한다.
(기동 직후 파일이 생기기까지 수십 초 걸릴 수 있다 — 잠시 대기 후 재확인.)
실패면 원인 후보를 안내: 토큰 오입력 / MESSAGE CONTENT INTENT 미설정 / 서버 초대 안 됨.

### 7. 마무리 안내

- 채널에서 인사 한 번 시켜 보라고 안내.
- 컨텍스트가 차면(대화가 길어지면) 채널에서 **"세션 마감하고 재시작해"** → 성패 웹훅 알림
  (선택: `~/.config/folder-bot/config.json`에 `{"webhook_url": "..."}`) → **"이어서하자"**.
- 부팅 자동 기동은 다음 재부팅부터. 지금 당장은 이미 `start`로 떠 있다.

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

doctor는 읽기 전용 — bots.json ↔ plist ↔ 지침 블록 ↔ 페어링 ↔ tmux 세션 생존을 대조 보고한다.
