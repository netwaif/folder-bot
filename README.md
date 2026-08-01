# folder-bot

폴더 전용 디스코드 채널 봇 — 업무 폴더의 Claude 세션을 전용 채널 봇으로 만들고,
부팅 자동 기동과 디스코드 원격 재시작(컨텍스트 리사이클)까지 한 번에 설치한다.

"업무 = 폴더"로 일하는 사람을 위한 도구다. 협업 관리 폴더, 프로젝트 폴더 같은
장기 세션 폴더마다 전용 디스코드 채널을 하나 붙여 두면, 밖에서 폰으로 그 폴더
작업을 지시하고, 컨텍스트가 차면 채널에서 세션을 갈아 끼울 수 있다.

## 설치

```
/plugin marketplace add netwaif/folder-bot
/plugin install folder-bot@folder-bot
```

전제: macOS(검증됨), tmux, Claude Code discord 공식 플러그인.

## 사용

봇으로 만들 폴더에서 Claude Code를 열고:

```
이 폴더를 디스코드 봇으로 만들어줘
```

스킬(configure-bot)이 전 과정을 지휘한다 — 자동으로 되는 일과 수동 단계가 명확히 나뉜다.

**자동** (결정적 엔진 botctl이 수행, 몇 번을 실행해도 같은 결과):
- `~/.config/folder-bot/bots.json`에 봇 정의 기록(폴더·세션명·리모트 컨트롤 — 전부 설정으로 관리)
- `~/.local/bin`에 `bot-up`(다중 봇 동시 부팅 경합 직렬화)·`bot-restart`(원격 재시작) 설치
- LaunchAgent 생성(부팅 자동 기동)
- 폴더 CLAUDE.md에 봇 지침 블록 추가(마커 방식 — 기존 내용 무수정, 제거 시 원문 복원)

**수동** (스킬이 단계별로 안내):
- 디스코드 개발자 포탈에서 봇 계정 생성·토큰 발급·MESSAGE CONTENT INTENT·서버 초대
- 서버에 전용 채널 생성, 채널 ID·사용자 ID 복사

## 컨텍스트 리사이클 (핵심 기능)

봇 세션도 오래 쓰면 컨텍스트가 찬다. 자동 압축 대신 세션을 갈아 끼우는 쪽이 품질이 좋다:

```
(디스코드 채널에서) 세션 마감하고 재시작해
→ 봇이 기록 갱신 후 스스로 재시작 (성패는 웹훅 알림 — 선택 설정)
→ 이어서하자
```

재시작 작업은 tmux 서버에 위탁되므로 봇이 죽어도 완주된다. 웹훅 알림은
`~/.config/folder-bot/config.json`에 `{"webhook_url": "https://discord.com/api/webhooks/..."}`.

## 비파괴 보장

- SESSION.md(세션 이어가기 기록)는 생성도 수정도 하지 않는다.
- CLAUDE.md는 마커 블록 추가만 — 제거하면 원문 그대로 복원된다.
- 봇 제거 시에도 페어링 파일(.discord-state)은 보존된다.

## 명령 (직접 쓸 일은 드물다 — 스킬이 대신 실행)

```bash
botctl.py add --name collab --folder ~/work/collab --session collab-bot
botctl.py pair --name collab --token <토큰> --user-id <ID> --channel-id <ID>
botctl.py start|stop|remove|list|doctor
```

## 라이선스

MIT
