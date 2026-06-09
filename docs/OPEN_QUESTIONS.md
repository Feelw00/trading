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

## 수집 (R0)

### 🟢 COLLECT-1 — R0 수집을 LLM이 수행 (CLAUDE.md 금지 #2 override)
- **결정(2026-06-08, 운영자):** R0 수집을 순수 코드가 아니라 **LLM 에이전트가 수행**한다(운영: openclaw cron 하루 2회·gpt-5.5 / 수동: `/collect`·`/boot`). CLAUDE.md 절대금지 #2(R0=순수코드)를 **R0 한정**으로 폐기.
- **R1(검증)·R5.5(선택기)·이벤트 감시기는 순수 코드 유지**(환각 차단 취지는 거기 남음).
- **환각 가드(필수):** 모든 수치·사실은 실제 소스 응답에서만. `source`·`as_of`·`fetched_at`(KST) 필수, 기억 기반 수치 금지, 검증 불가 → `verified=0`/`UNVERIFIED`(추측 금지).

### 🟢 COLLECT-2 — 데이터 소스 확정 (2026-06-08 조사)
| 데이터 | 소스 | 비용/계좌 | 실시간성 |
|---|---|---|---|
| 금리·환율 | 한국은행 **ECOS** Open API | 무료(상업가능) | 일별 |
| 국내지수(KOSPI/KOSDAQ)·종목 EOD | **공공데이터포털 금융위** / **KRX OPEN API** | 무료 | EOD(+1영업일·비상업) |
| 해외지수(SOX·S&P·NASDAQ)·유가(WTI·Brent) | **FRED**(세인트루이스 연은) | 무료 키 | 일별/EOD |
| 국내 종목 실시간 시세·호가 | **KIS Developers**(+`migusdn/KIS_MCP_Server` MCP, trading OFF) | 계좌(모의 지원), 초당 20건 | 실시간 근접 |
- **KIS 해외선물/해외지수는 제외:** SOX 미확인 + 해외선물 계좌·권한 별도 → 해외 매크로는 FRED로.
- 정확한 엔드포인트·FRED 시리즈ID·KIS TR_ID·필드는 **각 공식 문서에서 확정**(추측 금지).
- **남은 갭:** ① 국내 투자자별 매매동향(수급) KIS TR 확인 · ② NXT 프리/애프터(기존 🔴) · ③ 실시간 필요 범위(라운드=EOD 충분, 장중 감시만 KIS 실시간).

### 🟢 COLLECT-3 — 수집 하네스 (LLM 독자 웹서치 금지) · **뉴스는 COLLECT-4로 부분 개정(2026-06-09)**
- **결정(2026-06-08, 운영자):** LLM은 **승인된 소스 어댑터/도구만** 호출한다. **독자 판단의 웹서치(WebSearch/WebFetch) 금지** — 지침이 아니라 구조로 차단.
- 구현: 수집 커맨드/스킬의 `allowed-tools`에서 웹서치 제거 → 승인 도구(소스 어댑터: ECOS/공공데이터/KRX/FRED REST, KIS MCP)만 노출.
- 데이터 fetch는 **결정론적 어댑터 계층**이 수행, LLM은 트리거·기록만. 소스 실패·키 미연결 시 `UNVERIFIED`/`blocked`로 기록하고 **다른 소스나 웹서치로 임의 대체 금지**.
- 어댑터 미구현 구간은 수집을 **건너뛰고 보고**(웹서치 우회 금지).
- **적용범위(2026-06-09 명확화):** 시세·거시·공시 클러스터에 **그대로 엄격 유지**. **뉴스 클러스터만 COLLECT-4로 예외.**

### 🟢 COLLECT-4 — 뉴스 수집은 웹서치 허용 (2026-06-09, 운영자 결정 / COLLECT-3 부분 개정)
- **배경:** 뉴스는 후보 촉매 + **해외 거시·테마**(SOX·연준·글로벌 반도체 등 한국 증시 동인) 둘 다 필요.
  네이버 검색 API는 해외뉴스 약하고, 다수 언론사 RSS 직결은 유지보수 불가 → **웹서치 메타검색이 현실적 유일안.**
- **결정:** **뉴스 클러스터에 한해** COLLECT-3의 '독자 웹서치 금지'를 해제. 단 '구조 차단'을 **출처 가드로 대체**(환각구멍 방지):
  - 뉴스 검색은 **단일 뉴스 search 어댑터**(SearXNG 셀프호스트 또는 검색 API) 경로로만. 임의 도구 난사 금지.
  - 모든 `NewsRecord`: 실제 **URL·발행처·published_at(KST)·fetched_at·source 필수**. 다운스트림이 인용하는 사실은 **실제 fetch된 기사에 귀속** — 기억 기반 헤드라인/출처/수치 날조 금지.
  - **dedup**(URL·제목 정규화) + 출처신뢰 랭킹. 저신뢰·검증불가 → `UNVERIFIED`(드롭 후 날조 금지).
  - LLM은 **쿼리 확장·다양화·필터/그룹핑 가능**(리콜 확보에 필요한 능력). 단 사실은 fetch된 결과만.
  - 검색 실패·백엔드 다운 → `blocked` 보고(빈 결과 지어내지 않음).
- **미결(별도 결정 → PROPOSALS):** ① 검색 백엔드(SearXNG vs 검색 API) ② 저비용 프로바이더(MiniMax 등) 채택 여부 ③ 스코프(후보 촉매 / 일반 트렌드 발굴 / 둘 다).

### 🟡 NEWS-L2 — L2 섹터·테마 쿼리의 활성 서브테마 큐레이션 출처 (PROPOSALS P-4 §3)
- **배경:** P-4 3계층 쿼리플랜의 **L2(섹터·테마)**를 구현하며 — 현재 grounded 베이스는 **26 `Sector` 라벨**(`domains.SECTORS`에서 결정론 파생, `_sector_query`)로 확정. 종목명 검색에 안 잡히는 테마-광범위 촉매(예: 젠슨황 방한→AI 연관주)를 섹터 키워드로 포착.
- **회색지대:** 프로포절은 "26섹터 + **활성테마**(반도체→HBM·CXL, AI→온디바이스 등 **서브테마**)"를 말하나, 서브테마 리스트는 **큐레이션 출처가 없으면 임의확장 = 환각구멍**(절대금지 #1 정신). 현재는 **서브테마 미수집**(26 섹터 라벨만) — 보수적 스텁.
- **잠정 처리:** L2 = 26 섹터 라벨로만 가동. 서브테마 확장은 출처 확정(섹터 메타에 큐레이션 필드 추가 / 운영자 제공 리스트 / R7 학습 산출) 후 `build_query_plan`에 주입. **그 전까지 LLM·기억으로 서브테마 지어내지 않는다.**

### 🟢 NEWS-R2 — R2 촉매 스코어러 아키텍처 (결정: 단일 GPT-5.5, 멀티에이전트 아님)
- **질문(PROPOSALS P-4 잔여미결②):** `catalyst_strength`·분류 스코어러를 GPT-5.5 **단일 호출** vs **멀티에이전트(앙상블)** 중 무엇으로?
- **결정(2026-06-09, 운영자 위임):** **단일 GPT-5.5 스키마-강제 호출(배치).** 멀티에이전트 아님.
- **근거:**
  1. 설계서 §2 모델 라우팅이 "뉴스 분류·요약·이벤트 추출"을 **GPT-5.5(고빈도·저단가·스키마 강제 출력)**로 못박음 — R2는 **구조적 추출**이지 심층 토론·합성이 아님.
  2. R2는 06:30/16:30 **광범위 배치(수백 건)** 처리 → 항목별 멀티에이전트는 고빈도 레이어의 비용·지연을 폭발시킴.
  3. **검증·다양성은 R4가 담당**(perspective-diverse, **선별만** — 고강도·`single_stock`). R2를 멀티에이전트화하면 R4와 중복 → 프로포절 P-4 §4 "전수 멀티에이전트 검증 금지" 위배.
  4. `catalyst_strength` 신뢰는 **R7이 사후 캘리브레이션**(폐루프) → R2 앙상블로 정밀도 끌어올릴 필요 없음.
  5. 멀티에이전트 패턴은 설계상 **R3(페르소나 ×3)·R4(적대)** 전용 — R2 추출엔 부적합.
- **귀결(구현 방향 — R2 마일스톤에서 확정):** 배치 단위 = scope 레이어별(L1 후보별 / L2 섹터별 / L3 거시 묶음), 각 배치 → **1회 GPT-5.5 호출**이 기사를 **이벤트로 클러스터링**(같은 촉매 다기사 = 1 EventRecord + `novelty` 디스카운트) + 촉매필드 채움. 모델명 **.env 주입**(하드코딩 금지). 환각가드: `affected`/`evidence`는 fetch된 기사에만 귀속(미검증=UNVERIFIED, 종목 박제 금지).
- **안전선:** R2 산출은 EventRecord→R3 ThesisRecord grounding으로만. **R1 게이트·R5.5(매매 발동)에 LLM 점수 주입 금지**(절대금지 #2).

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
