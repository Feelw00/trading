# OPEN QUESTIONS

설계서의 모호함·미결정 사항을 임의 해석하지 않고 여기에 기록한다 (CLAUDE.md rule #1).
형식: 상태(🟢결정 / 🟡회색지대·확인필요 / 🔴미결정) · 항목 · 맥락 · 잠정 처리.

---

## 스케줄러

### 🟢 SCHED-1 — 스케줄링은 openclaw 기능만 사용
- **결정(2026-06-08, 운영자 지시):** 스케줄링은 **openclaw `cron` 또는 `heartbeat` 전용**. 자체 Python 스케줄러·system cron·launchd 등 외부 스케줄러 금지.
- 결과: 설계서/M2의 "`src/scheduler/` 자체 시장시간 스케줄러" 지시는 폐기. 시장 캘린더(KRX 휴장일·US DST)·장중 게이팅은 **각 cron 잡이 호출하는 스크립트 내부 가드**로 구현(openclaw 공식 권장 패턴).
- cron: `--at/--every/--cron`+`--tz Asia/Seoul`, 잡별 `--model/--tools`, isolated 세션, SQLite 영속. heartbeat: 주기 에이전트 턴(기본 30m), `activeHours`(tz 윈도우) → 장중 09:00–15:30 연속 감시에 사용.

### 🟢 SCHED-2 — 순수-코드 경로의 LLM 트리거 (수용됨)
- **맥락:** 설계서는 R0(수집)/R1(검증)/R5.5(아침 선택기)/이벤트 감시기를 **순수 코드(LLM 금지)**로 규정(환각 차단). 그러나 openclaw cron·heartbeat 페이로드는 `--message`(에이전트 턴)/`--system-event`뿐 — **순수 셸 실행 모드(`--command`)가 없음**. 즉 openclaw-only에서는 이 경로들도 에이전트 턴이 트리거.
- **잠정 처리:** cron/heartbeat 에이전트 턴은 `--tools exec --light-context` + 결정론적 디스패치 프롬프트로 **스크립트만 실행**한다. LLM은 트리거 전용 — FactRecord 등 데이터·판단 로직엔 일절 개입하지 않는다(숫자를 보지도 처리하지도 않음). 환각 차단 취지는 유지.
- **수용(2026-06-08):** 운영자가 "LLM=트리거 only, 데이터·판단엔 미개입" 해석을 수용. 디스패치 스크립트가 모든 실제 로직을 결정론적으로 수행.

### 🟢 SCHED-3 — claude 라운드의 모델 라우팅 (결정됨)
- **결정(2026-06-08, STRUCT-1=B의 귀결):** **Python 두뇌가 LLM 호출을 직접 오케스트레이션.** R2/R3=OpenAI API(Python SDK), R4/R5/R7=`claude -p` 서브프로세스. openclaw provider 라우팅은 라운드에 미사용(openclaw는 스케줄러+알림 채널 역할).
- 인증: `claude -p`는 로컬 Claude Code 인증 사용(운영자 보유). R2/R3는 Python `.env`의 `OPENAI_API_KEY` 필요(openclaw의 OpenAI OAuth는 재사용 안 함). 모델명은 `.env` 주입(하드코딩 금지).

---

## 결합/구조

### 🟢 STRUCT-1 — 트레이딩 로직의 언어/결합 (결정됨)
- **결정(2026-06-08):** **별도 Python 두뇌 + openclaw=exec 스케줄러.** 트레이딩 로직은 설계서대로 Python(pydantic v2 데이터 계약·mypy strict·PostgreSQL append-only). openclaw cron/heartbeat가 슬롯마다 `python -m trading.run <round>`(연속 감시는 `trading.watch`) exec. 라운드 간 전달은 DB/파일(설계서 §3). 알림은 openclaw 채널(Telegram).
- 제외: 설계서/M2의 `src/scheduler` 자체 스케줄러(SCHED-1로 폐기). 귀결: SCHED-3(모델 라우팅)도 Python 직접 오케스트레이션으로 결정.

---

## 인프라 / 이식성

### 🟢 INFRA-1 — 트레이딩 전용 openclaw 인스턴스 격리 (결정됨)
- **결정(2026-06-08):** 트레이딩은 개인 `~/.openclaw`(개인 auth·cron·스킬)와 **분리된 전용 openclaw 인스턴스**를 쓴다. 프로젝트 `OPENCLAW_HOME`(예: repo의 `.runtime/openclaw`, gitignored)에 상태/자격증명을 두고 openclaw 바이너리는 공유. 개인 환경과 안 섞여 git 100% 관리·이식 가능.

### 🟢 INFRA-2 — GitOps 완전 이식성 (결정됨)
- **결정(2026-06-08, 운영자 요구):** repo가 시스템의 **단일 소스**. 다른 기기에 `git clone → ops/bootstrap.sh → 1Password로 .env → 즉시 가동`.
- openclaw 설정(cron/heartbeat/채널/openclaw.json 템플릿)을 런타임에 손으로 두지 말고 **`ops/openclaw/`에 선언적 코드**로 두고 idempotent sync 스크립트로 적용.
- `ops/bootstrap.sh`: openclaw 설치(핀 Node 22.22) + poetry install + openclaw config 적용 + cron 등록 + `.env`(1Password/op).
- **git 제외는 단 둘**: 비밀값(1Password), 생성 런타임 상태(`.runtime/`). 버전 핀(Node 22.22, Python 3.13, openclaw, `poetry.lock`) 필수.

---

## 외부 의존 (Phase 1, 미해결 — 부록 A)
- 🔴 KRX 정보데이터시스템: 시세·투자자별 매매동향 접근 방식/인증
- 🔴 NXT 프리·애프터마켓 데이터: 소스·접근
- 🔴 증권사 조건부(청산) 주문 API: 보유 증권사 스펙 확인 선행 (Phase 1 필수 — §6)
- 🟢 DART OpenAPI / 한국은행 ECOS: 공개 문서 기반 구현 가능
