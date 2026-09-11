# 폴더 봇 스레드 라이브 뷰 — 설계 (0.1.9)

작성 2026-09-11. 사용자 결정: 폴더 봇의 디스코드 스레드마다 **독립 세션 + 라이브 pane**. 헤드리스 아님.
근거: 디스코드는 결과만 보이고 과정이 안 보인다. 지금 봇들은 전부 pane에 세션이 살아 있어 터미널에서
지켜볼 수 있으니 스레드도 같은 성질이어야 한다.

## 검증된 전제 (2026-09-11 실측)
- 공식 discord 플러그인 0.0.4의 인바운드는 `<channel chat_id= message_id= user= ts=>`뿐이다. 스레드면
  `chat_id`가 스레드 ID이고 부모 정보는 없다 → 스레드 판별은 REST `GET /channels/{id}`(type 11/12,
  `parent_id`)로 한다. 스레드는 부모 채널의 opt-in을 상속하므로 access.json 변경은 없다.
- Claude Code 세션 간 메시지(`SendMessage`, 로컬 UDS)는 맥·컨테이너(uid별 폴백 `/tmp/cc-socks-<uid>`)
  둘 다 동작. 컨테이너 dev-claudecode ↔ 임시 세션 왕복 확인.
- transcript(`~/.claude/projects/<slug>/<session>.jsonl`)는 `type=assistant` 줄의
  `message.content[].text`에 답변이 있다 → Stop 훅이 마지막 턴을 추출할 수 있다.
- 봇 토큰으로 REST 게시는 게이트웨이 접속이 아니라 이중 접속 제약과 무관하다.

## 구조
```
디스코드 스레드 ──게이트웨이──▶ 메인 봇 세션(라이브 TUI, 플러그인 보유)
                                │  chat_id ≠ 등록 채널 → bot-thread kind → 스레드
                                │  bot-thread ensure <봇> <스레드ID> → tmux 창 + 세션명
                                └─ SendMessage(세션명, 원문+메타) ─▶ 스레드 세션(라이브 TUI, 창 t<short>)
                                                                        │ 답변 완료
                                                                        └─ Stop 훅 → bot-thread post → REST → 스레드
```
- **메인 봇 컨텍스트**에는 라우팅 한 줄씩만 남는다. 스레드 작업 내용은 각 스레드 세션에.
- **스레드 세션**은 봇 폴더에서 뜨므로 폴더 CLAUDE.md·스킬·메모리가 그대로 적용된다.
- 세션 ID를 스레드별로 고정 배정(`--session-id <uuid>`, 이후 `--resume <uuid>`) → 봇 재시작·창 정리 뒤에도
  그 스레드의 맥락이 이어진다. 맵은 `<폴더>/.discord-state/threads.json`.

## 구성 요소
### 1. `bot-thread` (assets/bot-thread.sh → ~/.local/bin, bash)
| 명령 | 동작 |
|---|---|
| `kind <chat_id>` | REST 채널 조회 → `channel` / `thread <parent_id>` / `dm`. 결과는 threads.json에 캐시 |
| `ensure <봇> <thread_id>` | 맵 조회(없으면 uuid 발급) → 봇 tmux 세션에 창 `t<스레드ID 끝 6자리>`가 없으면 생성:<br>`env DISCORD_THREAD_ID=<id> DISCORD_BOT_NAME=<봇> claude -n <봇>-t<short> --permission-mode auto (--session-id <uuid> | --resume <uuid>)`<br>세션 소켓(`cc-socks*/`)이 나타날 때까지 최대 30초 대기 → 세션명 출력 |
| `post <thread_id> (<text> \| --file <path>)` | 봇 폴더 `.discord-state/.env`의 토큰으로 `POST /channels/<id>/messages`(2000자 청크, 파일은 multipart) |
| `open <channel_id> <이름> [message_id]` | REST로 스레드 생성 → thread_id 출력 (봇이 "스레드 파서 해줘"를 처리할 때) |
| `gc [--idle-hours N]` | transcript mtime이 N시간(기본 6) 지난 창 닫기. 맵은 유지(다음 메시지에 resume). ensure가 호출 때마다 같이 돈다 |
| `list <봇>` | 맵·창 상태 표 |
봇 폴더·세션·토큰 경로는 `~/.config/folder-bot/bots.json`에서 읽는다(정본 불변).

### 2. Stop 훅 (assets/bot-thread-stop.sh)
- `<폴더>/.claude/settings.local.json`의 `hooks.Stop`에 등록(botctl add가 병합, remove가 주입분만 회수 — statusLine과 같은 규칙).
- stdin JSON의 `transcript_path`·`stop_hook_active`를 읽는다. `DISCORD_THREAD_ID` 환경변수가 없으면 즉시 exit 0
  (메인 봇 세션·로컬 세션에는 무영향). `stop_hook_active`면 exit 0(재진입 방지).
- 마지막 사용자 턴 이후의 assistant `text` 블록을 이어 붙여 `bot-thread post $DISCORD_THREAD_ID`로 게시.
  빈 답변이면 게시하지 않는다. 게시 실패는 stderr에만 남기고 exit 0(훅이 세션을 막지 않는다).

### 3. 지침 블록 (directive-block.md 증분)
메인 봇 세션용:
- `chat_id`가 등록 채널이 아니면 `bot-thread kind <chat_id>`로 판별. 스레드면 직접 답하지 않고
  `bot-thread ensure <봇> <thread_id>`가 출력한 세션명으로 `SendMessage`한다.
  메시지 본문 = `<discord-thread chat_id=… message_id=… user=… ts=…>원문</discord-thread>` + 첨부가 있으면
  `download_attachment`로 받은 경로 목록. 그 스레드 첫 메시지엔 reply로 "이 스레드는 세션 `<이름>`이 담당(터미널: tmux 창 t<short>)" 한 줄.
- "스레드 파서 해줘/이 건은 스레드로" → `bot-thread open <chat_id> <이름>` → ensure → 첫 메시지 전달.
- DM은 기존 규칙대로(처리하지 않음).
스레드 세션용(`DISCORD_THREAD_ID`가 있으면):
- 이 세션은 스레드 `<id>` 전담. 답변은 Stop 훅이 자동 게시하므로 평소처럼 답하면 된다. reply 도구는 없다.
  파일을 보내려면 `bot-thread post $DISCORD_THREAD_ID --file <경로>`.
- 진행이 길면 중간 보고를 `bot-thread post`로 직접 올려도 된다.

### 4. botctl 변경
- `install_scripts`: bot-thread·bot-thread-stop 추가.
- `add`: settings.local.json에 Stop 훅 병합(기존 hooks 보존, 주입 기록은 bots.json의 `thread_hook` 필드).
- `remove`: 훅 회수, 스레드 창 정리(`gc --all`), threads.json은 보존(토큰 파일과 같은 취급).
- `doctor`: bot-thread 설치·훅 등록·`curl` 존재·threads.json 파싱·고아 창(맵에 없는 t* 창) 점검.

## 범위 밖 (이번 반복에서 하지 않는 것, 처음부터 명시)
- **codex·agy 스레드 라이브**: 세션 간 메시지가 없어 스레드마다 TUI pane + 붙여넣기 + tail이 필요하다.
  codex-discord의 인스턴스당 pane 1개 장치를 스레드당 N개로 넓히는 별도 작업(2차). 3엔진 동일 동작 규칙상
  반드시 뒤따른다.
- **메인 대화 복제로 스레드 시작**(`--resume <메인> --fork-session`, `/branch` 대응): 메인 세션 ID를
  세션 안에서 안정적으로 얻는 방법 확인 뒤 1.1로.
- 스레드 세션의 권한 승인 프롬프트: auto 모드 상속. 승인이 필요한 작업은 메인 채널에서.

## 매트릭스
| | 맥 | 리눅스(systemd) | 리눅스 컨테이너 | 윈도우(WSL2) |
|---|---|---|---|---|
| Claude Code | 같은 코드(bash·curl·tmux·claude) | 같음 | 같음(UDS 폴백 확인) | 같음 |
| Codex | 2차 | 2차 | 2차 | 2차 |
| Gemini(agy) | 2차 | 2차 | 2차 | 2차 |

## 검증 (완료 조건)
1. 단위: bot-thread `kind` 파싱(가짜 curl), 맵 증분, Stop 훅 추출(픽스처 transcript: 도구 호출·중간 텍스트 섞인 턴에서 마지막 턴 텍스트만) — 기존 36 + 신규 통과.
2. 실기(dev-claudecode, 컨테이너): 채널에 스레드 생성 → 사용자가 스레드에 메시지 → 봇 tmux 세션에 창 `t<short>` 생성·SendMessage 도달 → 답변이 스레드에 게시. 봇 재시작 후 같은 스레드에 다시 메시지 → `--resume`으로 맥락 유지. 메인 채널 메시지는 메인 세션이 그대로 처리. `gc` 후 창 사라지고 다음 메시지에 복원.
3. 실기(맥, 폴더 봇 1개): 같은 시나리오.
