# 프로젝트: 트레이딩 리서치·집행 시스템

## 최우선 규칙
`docs/trading-system-design.md`(v0.2)가 이 프로젝트의 헌법이다. 모든 작업 전에 읽어라.
단, 일부 항목은 운영 결정으로 갱신됐다 — **결정·미결정 사항은 `docs/OPEN_QUESTIONS.md`가 최종 기준**이며,
설계서와 OPEN_QUESTIONS가 충돌하면 OPEN_QUESTIONS의 🟢결정을 따른다.
새 모호함을 발견하면 임의 해석하지 말고 `docs/OPEN_QUESTIONS.md`에 항목을 추가하고 보수적 스텁으로 우회하라.

## 확정 아키텍처 (OPEN_QUESTIONS: SCHED-1~3, STRUCT-1)
- **스케줄링은 openclaw 기능만 사용** — openclaw `cron`/`heartbeat` 전용. 자체 스케줄러·system cron·launchd 금지.
- **트레이딩 로직은 별도 Python 두뇌.** openclaw cron/heartbeat가 슬롯마다
  `python -m trading.run <round>`(연속 감시는 `python -m trading.watch`)를 exec로 트리거.
- 순수-코드 경로(R1/R5.5/이벤트 감시기)는 cron 에이전트가 `--tools exec --light-context`로 디스패치 —
  **LLM은 트리거 전용, 데이터·판단엔 절대 미개입**.
- **R0(수집)만 예외: LLM 수집 채택(OPEN_QUESTIONS COLLECT-1).** 단 하네스(COLLECT-3)로 LLM은 승인된 소스 어댑터만 호출, 독자 웹서치 금지 + 환각 가드 적용.
- 라운드 간 전달은 **DB(append-only)/파일로만** (프롬프트 체이닝 금지, 설계서 §3).
- 모델: R2/R3=OpenAI API, R4/R5/R7=`claude -p` 서브프로세스 — **Python 두뇌가 직접 호출**(openclaw provider 라우팅 미사용).
- 알림(P0/P1/P2)은 openclaw 채널(Telegram).

## 절대 금지 사항
1. 외부 데이터 소스의 API 엔드포인트·인증·응답 포맷을 추측해서 구현하지 마라.
   모르는 소스는 어댑터 인터페이스 + 스텁 구현 + `docs/OPEN_QUESTIONS.md` 등록으로 처리한다.
   존재하지 않는 엔드포인트를 그럴듯하게 지어내는 것이 이 프로젝트 최대 리스크다.
2. R1(검증)/R5.5(선택기)/이벤트 감시기의 **계산·판단 로직에 LLM을 넣지 마라**. 순수 코드다.
   (openclaw cron이 스크립트를 exec하기 위한 "트리거"로서의 LLM 에이전트 턴만 허용 — 데이터엔 접근하지 않는다.)
   **R0(수집)은 예외 — 운영자 결정으로 LLM 수집 채택(OPEN_QUESTIONS COLLECT-1). 단 하네스(COLLECT-3): LLM은 승인된 소스 어댑터만 호출하고 독자 웹서치 금지, 환각 가드(출처·as_of 필수, 미검증=UNVERIFIED, 추측 금지) 적용.**
3. 시장가 주문 코드를 어떤 형태로도 작성하지 마라. 주문 관련 코드는 지정가·조건부(청산)만 존재한다.
4. 비밀값(API 키·계좌)을 코드·로그·테스트 픽스처에 넣지 마라.
   전부 환경변수 + `.env.example` + 1Password(`docs/SECRETS.md`). 모델명도 하드코딩 금지(.env 주입).
5. 모든 타임스탬프는 timezone-aware(KST 명시). naive datetime 사용 금지.
6. **자체 스케줄러를 만들지 마라.** 스케줄은 openclaw cron/heartbeat로만.
   시장시간/휴장일은 스케줄러가 아니라 각 잡이 호출하는 가드(`src/trading/market_calendar`)가 처리한다.

## 기술 스택 (고정)
- Python 3.11+, Poetry. 타입힌트 필수, mypy strict 통과.
- 스키마: pydantic v2 (설계서 §4의 데이터 계약을 1:1 구현).
- DB: PostgreSQL (docker-compose 제공), append-only — UPDATE/DELETE 금지, 새 버전 레코드로만 수정.
- 테스트: pytest. 모든 데이터 계약에 스키마 테스트, 모든 게이트(R1, R5.5)에 단위 테스트.
- 스케줄러: **없음(자체 구현 금지)** — openclaw cron/heartbeat 사용.

## 디렉터리 구조
```
src/trading/
  contracts/        # pydantic 스키마 (FactRecord, EventRecord, ThesisRecord, Playbook, OrderDraft)
  collectors/       # R0 — 소스별 클라이언트 (공통: 멱등성, 백오프, as_of/fetched_at/source 필수)
  gates/            # R1 — 신선도·정합성 게이트
  rounds/           # R2~R5, R7 — LLM 라운드 (R2/R3: OpenAI API, R4/R5/R7: claude -p 서브프로세스)
  selector/         # R5.5 — 아침 플레이북 선택기 (순수 함수: 입력→활성화 결정)
  watch/            # 이벤트 감시기 (순수 코드) — heartbeat가 트리거
  reports/          # R6 — 모닝/저녁 보고 렌더링 (Jinja2 템플릿)
  market_calendar/  # 시장일·KRX 휴장일·미국 DST·장중 게이트 (각 잡의 가드)
  journal/          # 저널 DB 접근 계층 (append-only)
  alerts/           # P0/P1/P2 알림 → openclaw 채널(Telegram) 어댑터
  run.py            # 디스패치 엔트리포인트: python -m trading.run <round>
  watch.py          # 연속 감시 엔트리포인트: python -m trading.watch
ops/openclaw/       # openclaw 설정을 코드로: cron/heartbeat/채널 정의(선언적), openclaw.json 템플릿(비밀 op://), idempotent sync 스크립트
ops/bootstrap.sh    # 새 기기 프로비저닝: openclaw 설치(핀 Node 22.22)+poetry+config 적용+cron 등록+.env(1Password)
tests/
fixtures/replay/    # 6/2~6/8 주간 리플레이 픽스처
docs/               # trading-system-design, OPEN_QUESTIONS, SECRETS, claude-code-prompts, PROGRESS, PROPOSALS
.runtime/openclaw/  # 트레이딩 전용 openclaw 런타임(OPENCLAW_HOME) — bootstrap가 생성, gitignored
```

## openclaw 운영 메모
- openclaw는 **Node 22.22 격리본**(`~/.openclaw/bin/openclaw`, install-cli.sh로 설치).
  **Node 23에서 돌리지 말 것** — `node:sqlite`의 `statement.columns` 부재로 상태 마이그레이션 실패. (전역 asdf node 23.9.0과 무관)
- **GitOps·완전 이식성**: repo가 시스템의 단일 소스. 다른 기기에 `clone → ops/bootstrap.sh → 1Password로 .env → 가동`. (OPEN_QUESTIONS INFRA-1/2)
- **트레이딩 전용 openclaw 인스턴스**: 개인 `~/.openclaw`와 분리된 프로젝트 `OPENCLAW_HOME`(`.runtime/openclaw`, gitignored, bootstrap가 생성). openclaw 설정은 손으로 두지 말고 `ops/openclaw/`에 선언적 코드로. **repo에 openclaw 소스(플랫폼)는 두지 마라** — 버전 핀 + bootstrap 설치로 의존.
- cron 잡은 `ops/openclaw/`의 등록 스크립트로 관리. KST 정시는 `--cron "<expr>" --tz Asia/Seoul`.
- 순수-코드 디스패치 잡은 `--tools exec --light-context` + 결정론적 프롬프트로 `python -m trading.run …`만 실행.

## 작업 방식
- 마일스톤 프롬프트(`docs/claude-code-prompts.md` §2)의 수용 기준(AC)을 충족하기 전에 다음 작업으로 넘어가지 마라.
- 각 마일스톤 종료 시: 전체 테스트 + mypy strict 통과 확인 → `docs/PROGRESS.md`에 완료·미해결 항목 기록.
- 설계서에 없는 기능을 추가하고 싶으면 구현하지 말고 `docs/PROPOSALS.md`에 적어라.
- 외부 의존(증권사 API, KRX 인증 등)이 필요한 지점은 막히지 말고 인터페이스로 추상화 후 OPEN_QUESTIONS 등록하고 진행.
