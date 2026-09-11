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

## 구조 (0.1.9 최종 — 훅 기반 결정적 라우팅)
```
디스코드 스레드 ──게이트웨이──▶ 메인 봇 세션(라이브 TUI, 플러그인 보유)
   UserPromptSubmit 훅(bot-thread-route): chat_id가 등록 채널이 아니면
     → bot-thread kind(REST) → ensure(창·세션 보장) → fetch-attachments → deliver(스레드 pane에 bracketed paste)
     → 첫 메시지면 담당 안내 post → exit 2 (메인은 이 메시지를 보지 않는다)
                                                          스레드 세션(라이브 TUI, 창 t<short>, 플러그인 비활성)
                                                            └─ Stop 훅(bot-thread-stop) → bot-thread post → REST → 스레드
```
첫 설계는 메인 봇이 지침을 보고 SendMessage로 넘기는 것이었으나 1차 실기에서 메인이 지침을 무시하고 직접 답해 폐기.
- **메인 봇 컨텍스트**에는 스레드 메시지가 도달하지 않는다(훅 exit 2). 스레드 작업 내용은 각 스레드 세션에.
- **스레드 세션**은 봇 폴더에서 뜨므로 폴더 CLAUDE.md·스킬·메모리가 그대로 적용된다. `--settings` 파일로 discord 플러그인을 끈다
  (토큰 없는 플러그인이 "인증 필요"를 전역 캐시해 메인 봇 재기동 연결을 막던 원인).
- 세션 ID를 스레드별로 고정 배정(`--session-id <uuid>`, 이후 `--resume <uuid>`) → 봇 재시작·창 정리 뒤에도
  그 스레드의 맥락이 이어진다. 맵은 `<폴더>/.discord-state/threads.json`.

## 구성 요소
### 1. `bot-thread` (assets/bot-thread.sh → ~/.local/bin, bash)
| 명령 | 동작 |
|---|---|
| `kind <chat_id>` | REST 채널 조회 → `channel` / `thread <parent_id>` / `dm`. 결과는 threads.json에 캐시 |
| `ensure <봇> <thread_id>` | 맵 조회(없으면 uuid 발급) → 봇 tmux 세션에 창 `t<스레드ID 끝 6자리>`가 없으면(프로세스 트리에 claude 없음) 생성:<br>`claude -n <봇>-t<short> --permission-mode auto --settings <state>/thread-settings.json (--session-id <uuid> \| --resume <uuid>)`<br>입력 프롬프트(❯)가 뜰 때까지 최대 60초 대기 → 세션명 출력. 새 세션이면 `fresh=1` 표식 |
| `deliver <봇> <thread_id> -` | stdin 원문을 스레드 pane에 bracketed paste + Enter(미제출 시 재전송) |
| `fetch-attachments <봇> <chat_id> <message_id>` | 첨부를 `.discord-state/inbox/<mid>/`에 받고 경로 출력 |
| `rotate <봇> <thread_id>` / `fresh` | 새 uuid로 회전(옛 ID previous)·창 닫기 / fresh 표식 1회 소거 |
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
메인 봇 세션용: 스레드 메시지는 훅이 처리하므로 규칙은 셋뿐 — "[스레드 라우팅 실패]" 접두가 붙어 오면 그때만 직접 답한다 /
"스레드 파서 해줘"는 `bot-thread open` / 스레드 관련 질문은 `threads/*/log.md`·SESSION.md·`fetch_messages`로 찾아 답한다.
스레드 세션용(`DISCORD_THREAD_ID`가 있으면): 답변은 Stop 훅이 자동 게시(reply 도구 없음), 파일은 `bot-thread post --file`,
정본은 `threads/$DISCORD_THREAD_ID/SESSION.md`, 마감 신호 시 4단계(갱신→폴더 SESSION.md 결정 한 줄→게시→rotate).

### 4. botctl 변경
- `install_scripts`: bot-thread·bot-thread-stop·bot-thread-route·bot-thread-compact.
- `add`: settings.local.json hooks에 UserPromptSubmit·Stop·PreCompact 병합(기존 hooks 보존, 주입 기록은 bots.json `thread_hooks`). bot-up이 기동 직전 `mcp-needs-auth-cache.json`의 discord 항목을 걷는다.
- `remove`: 훅 회수, 스레드 창 정리(`gc --all`), threads.json은 보존(토큰 파일과 같은 취급).
- `doctor`: bot-thread 설치·훅 등록·`curl` 존재·threads.json 파싱·고아 창(맵에 없는 t* 창) 점검.

## 범위 밖 (이번 반복에서 하지 않는 것, 처음부터 명시)
- **codex·agy 스레드 라이브**: B단계 완료(codex-discord 0.1.10, 2026-09-11) — 데몬이 스레드마다 창 `t<끝6자리>`를 띄우고
  스레드별 tail로 게시(`scripts/tui-up.sh --window`, `data-<이름>/threads.json`). A단계(메인의 스레드 생성·threads/<ID>/SESSION.md·log.md·회전·재개)는 후속.
  원래 메모: 세션 간 메시지가 없어 스레드마다 TUI pane + 붙여넣기 + tail이 필요하다.
  codex-discord의 인스턴스당 pane 1개 장치를 스레드당 N개로 넓히는 별도 작업(2차). 3엔진 동일 동작 규칙상
  반드시 뒤따른다.
- **메인 대화 복제로 스레드 시작**(`--resume <메인> --fork-session`, `/branch` 대응): 메인 세션 ID를
  세션 안에서 안정적으로 얻는 방법 확인 뒤 1.1로.
- 스레드 세션의 권한 승인 프롬프트: auto 모드 상속. 승인이 필요한 작업은 메인 채널에서.

## 매트릭스
| | 맥 | 리눅스(systemd) | 리눅스 컨테이너 | 윈도우(WSL2) |
|---|---|---|---|---|
| Claude Code | 같은 코드(bash·curl·tmux·claude) | 같음 | 같음(UDS 폴백 확인) | 같음 |
| Codex | B(codex-discord 0.1.10, 실기는 한도 해제 후) | 같음 | 같음 | 같음 |
| Gemini(agy) | B 됨(맥 실기 9/11) | 같음 | B 됨(컨테이너 실기 9/11) | 같음 |

## 검증 (완료 조건)
1. 단위: bot-thread `kind` 파싱(가짜 curl), 맵 증분, Stop 훅 추출(픽스처 transcript: 도구 호출·중간 텍스트 섞인 턴에서 마지막 턴 텍스트만) — 기존 36 + 신규 통과.
2. 실기(dev-claudecode, 컨테이너): 채널에 스레드 생성 → 사용자가 스레드에 메시지 → 봇 tmux 세션에 창 `t<short>` 생성·SendMessage 도달 → 답변이 스레드에 게시. 봇 재시작 후 같은 스레드에 다시 메시지 → `--resume`으로 맥락 유지. 메인 채널 메시지는 메인 세션이 그대로 처리. `gc` 후 창 사라지고 다음 메시지에 복원.
3. 실기(맥, 폴더 봇 1개): 같은 시나리오.

## 0.1.10 — 컨텍스트 관리와 메인↔스레드 지식 (2026-09-11 사용자 결정)
- **compact는 폴백으로 둔다.** auto-compact를 끄지 않는다(끄면 세션이 멈춘다). 회전은 세부 보존이 필요할 때만 사람이 고른다.
- **회전(`bot-thread rotate`)**: 스레드 세션이 `threads/<ID>/SESSION.md`(loadout 세션 이어가기 규율 그대로, 같은 템플릿) 증분 갱신 →
  메인 전제가 될 결론만 폴더 SESSION.md 결정 기록에 한 줄 → "재시작 들어감" 게시 → rotate(새 uuid, 옛 ID는 `previous`, 창 닫기).
  다음 메시지에 라우팅 훅이 `fresh` 표식을 보고 `[재정박] threads/<ID>/SESSION.md를 먼저 읽고…` 접두를 결정적으로 붙인다.
- **PreCompact 훅(bot-thread-compact)**: compact 직전 스레드에 사후 알림 한 줄.
- **메인은 스레드를 모른다(설계).** Stop 훅이 `threads/<ID>/log.md`에 "시각 Q→A" 한 줄을 자동 기록. 메인 지침: 스레드 관련 질문이면
  `threads/*/log.md`·SESSION.md → 부족하면 `fetch_messages(chat_id=스레드ID)` 원문 → 답. 추측 금지.
- 규율의 주인은 loadout 그대로. folder-bot 블록은 "스레드 세션이면 정본은 threads/<ID>/SESSION.md" 경로 분기만 얹는다.
  loadout 세션 이어가기 조각에 "[folder-bot 스레드 세션인 경우]" 조건부 한 줄 추가는 loadout 다음 갱신 항목.
- 0.1.10 후속 항목: add가 봇 폴더 settings.local.json permissions.allow에 `Bash(bot-restart:*)`·`Bash(bot-thread:*)` 주입
  (auto 모드 분류기가 bot-restart를 막은 실측 2026-09-11).
