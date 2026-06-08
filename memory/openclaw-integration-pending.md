---
name: openclaw-integration-pending
description: "트레이딩 프로젝트 아키텍처: openclaw=exec 스케줄러(cron/heartbeat 전용) + 별도 Python 두뇌. 핵심 결정 확정됨"
metadata: 
  node_type: memory
  type: project
  originSessionId: 7ea14fad-c8ef-45e3-8548-e7e1fb2a0f7c
---

이 프로젝트(`/Users/lucas/Project/stock`, 트레이딩 리서치·집행 시스템)는 **openclaw**(Node/TS 개인 AI 어시스턴트 플랫폼, cron·channels·provider 라우팅·skills·memory 제공)를 기반 플랫폼으로 채택했다. Lucas는 이미 4~5월부터 openclaw를 써온 기존 사용자(`~/.openclaw`에 openclaw.json·credentials/auth-profiles·cron 존재, repo 밖).

**설치 형태 (2026-06-08 정리됨):** openclaw **v2026.6.1**을 `install-cli.sh` **로컬 prefix 격리** 설치 — `~/.openclaw/tools`의 **Node 22.22.0** 위에서 동작, 바이너리는 `~/.openclaw/bin/openclaw`, PATH는 `~/.zshrc` line 40에 등록. 전역 asdf node는 **23.9.0 유지**(다른 프로젝트용).
- **이유(중요):** Node **23.9.0**의 `node:sqlite`에는 `statement.columns`가 없어(undefined) openclaw 2026.6.1의 SQLite 마이그레이션이 `statement.columns is not a function`으로 실패. Node 22 LTS·24는 이 API 있음 → 22.22.0 격리로 회피. **openclaw를 Node 23에서 돌리지 말 것.**
- npm-전역(node 23) openclaw는 제거함.
- `doctor --fix` 적용 완료(2026-06-08, 사용자 승인): 레거시 `agents.defaults.agentRuntime` → 모델 스코프(`agents.defaults.models["openai/gpt-5.5"].agentRuntime={id:codex}`)로 정상 이전, config 유효. JSON cron 2건(둘 다 `*-demo` 테스트, 1건은 채널 미설정으로 과거 38회 실패)은 SQLite로 이전됨 — gateway off라 현재 휴면. OAuth 프로파일(openai, dhrtn1006@gmail.com) 보존. **부작용**: 선택 스킬 ~40개가 `enabled:false`로 명시 비활성화(필요시 `openclaw skills enable <name>`), `workspace/HEARTBEAT.md`가 템플릿으로 교체됨. 백업: `~/.openclaw/_pre-fix-backup-20260608-110512`, `~/.openclaw/openclaw.json.bak`.
- 데모 cron 2건 삭제는 gateway 기동이 필요해(cron 명령이 gateway 클라이언트) 미실행 — 트레이딩용 gateway 셋업 시 함께 `openclaw cron rm <id>`로 정리 예정. id: 07b64b92-af88-4bd1-800b-410375d561ef(manual-demo), b5fb854c-0181-45b8-9915-6c4c48867d02(agentturn-demo).

**스케줄러: 결정됨 (2026-06-08, 사용자 지시).**
- **스케줄링은 openclaw 제공 기능만 사용 — openclaw `cron` 또는 `heartbeat` 전용.** 자체 Python 스케줄러·system cron·launchd 등 외부 스케줄러 금지. (M2의 `src/scheduler/` 자체 구현 지시는 이 결정으로 폐기.)
- 사실관계: openclaw cron/heartbeat 페이로드는 `--message`(에이전트 턴=LLM)/`--system-event`뿐, **순수 셸 실행(`--command`) 모드 없음** → 둘 다 LLM 에이전트를 깨움. cron은 `--at/--every/--cron`+`--tz Asia/Seoul`(KST 정시), 잡별 `--model`/`--tools`/isolated, SQLite 영속. heartbeat는 주기 에이전트 턴(기본 30m), `activeHours`(tz 윈도우) 지원.
- 따라서 설계서의 **순수-코드 경로(R0 수집/R1 게이트/R5.5 선택기/이벤트 감시기, LLM 금지)**도 openclaw cron/heartbeat **에이전트 턴이 `--tools exec --light-context`로 결정론적 스크립트를 디스패치**하는 방식으로 트리거 — LLM은 트리거 전용, 데이터·판단엔 미개입(설계서 "환각 차단" 취지 유지). 단 "LLM을 그 경로에 넣지 마라"의 문자적 해석과는 회색지대 → 사용자 확인/ OPEN_QUESTIONS 등록 대상.
- LLM 라운드(R2/R3=gpt-5.5, R4/R5/R7=claude)는 cron isolated + `--model`로 라우팅. 단 현재 openclaw엔 **OpenAI auth만** 있음 → claude 라우팅하려면 Anthropic auth 추가 필요(or `claude -p` 유지) [미결정].
- 시장 캘린더(KRX 휴장일·US DST)·장중 게이팅은 디스패치되는 스크립트 내부 가드로 구현(문서 권장 패턴). 장중 연속 감시는 heartbeat `activeHours` 09:00–15:30 활용 가능.

**결합/언어: 결정됨 (2026-06-08, STRUCT-1=B).** **별도 Python 두뇌 + openclaw=exec.** 트레이딩 로직은 Python(pydantic v2·mypy strict·PostgreSQL append-only, 설계서 그대로). openclaw cron/heartbeat가 슬롯마다 `python -m trading.run <round>`(연속 감시 `trading.watch`) exec. 라운드 간 전달은 DB/파일(설계서 §3). 알림은 openclaw 채널(Telegram).
- 귀결 SCHED-3(모델 라우팅 결정됨): Python 두뇌가 직접 LLM 호출 — R2/R3=OpenAI API(`OPENAI_API_KEY` 필요), R4/R5/R7=`claude -p`(로컬 Claude Code 인증). openclaw provider 라우팅은 라운드에 미사용.
- openclaw 스킬(Node/TS) 전면 구현·MCP 노출 방식은 기각.

**다음 단계:** 핵심 아키텍처 확정 완료 → 빌드 진입(M1: 골격+데이터계약+리플레이 하네스) 전, repo 루트 `CLAUDE.md`를 위 결정 반영해 신규 작성 + `trading-system-design.md`를 `docs/`로 이동 필요. (현재 CLAUDE.md 없음, 설계서는 repo 루트에 있음)

관련: [[secrets-and-git-conventions]]
