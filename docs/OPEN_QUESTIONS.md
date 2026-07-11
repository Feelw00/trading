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
- **남은 갭:** ~~① 국내 투자자별 매매동향(수급) KIS TR 확인~~(**2026-06-11 해소** — 아래) · ② NXT 프리/애프터(기존 🔴) · ③ 실시간 필요 범위(라운드=EOD 충분, 장중 감시만 KIS 실시간).
- **갭① 해소(2026-06-11) — 수급은 KIS TR로 확정** (공식 저장소 `koreainvestment/open-trading-api` + 실호출 관측 검증, KRX 정보데이터시스템 직접 접근 불필요):
  - 종목별 투자자매매동향(일별): `GET /uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily` TR `FHPTJ04160001` — output2가 일별 ~30거래일, `{frgn,prsn,orgn}_ntby_qty`(주)/`..._ntby_tr_pbmn`(**백만원** — 수량×주가 대조로 단위 검증).
  - 시장별 투자자매매동향(일별): `GET /uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market` TR `FHPTJ04040000` — KOSPI=(업종 0001, KSP)/KOSDAQ=(업종 1001, KSQ) 파라미터 조합 실호출 확정.
  - 토큰: `POST /oauth2/tokenP`(24h 유효, 6시간 내 재발급=동일 토큰+알림톡) → 파일 캐시 필수(`.runtime/kis/token.json`).
  - 구현: `collectors/kis.py`(조회 전용 — 주문 TR 금지 유지) + `collectors/flows.py`(`data/flows.sqlite` append-only) + `collect-flows` 라운드(daily-eod 체인 내 best-effort) + FactPack `flows` 섹션(R3 수급 grounding).
  - 장중 잠정(시세성): `GET /uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market` TR `FHPTJ04030000` — 파라미터 (999, S001)=KOSPI/(999, S101)=KOSDAQ은 장중 조합 프로브 관측 확정(그 외 0/오류, HTS [0403] 코스피·코스닥 화면과 정합). 표시 전용·적재 금지(응답에 날짜 필드 부재). **잠정 단위 🟢 해소(2026-06-11 15:38 마감 직후 교차검증):** 단위는 일별과 동일 **백만원** 확인. 단 **시세성 피드는 신뢰 불가 실증**(2026-06-11): KRX 정보데이터시스템 장중 CSV와 대조 결과 절대값 5~30배 과소(13:38 외인: KIS 잠정 -1,564억 vs KRX -29,529억) — 동시호가 미반영 수준이 아니라 커버리지 자체가 부분. 방향 판단에도 쓰지 말 것(6/11 '동시호가 외인 매도' 오판 사례 → `history/trading/events/2026-06-11-intraday-flow-reversal.md`). 장중 수급 정답 소스는 KRX 정보데이터시스템 수동 CSV(자동화는 비공식 스크래핑이라 보류).

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

### 🟢 NEWS-R2 — R2 촉매 스코어러 아키텍처 (결정: 단일 호출·claude -p 런타임, 멀티에이전트 아님)
- **질문(PROPOSALS P-4 잔여미결②):** `catalyst_strength`·분류 스코어러를 **단일 호출** vs **멀티에이전트(앙상블)**? + 어느 모델/프로바이더?
- **결정(2026-06-09, 운영자):**
  - **(아키)** **단일 스키마-강제 호출(배치).** 멀티에이전트 아님.
  - **(프로바이더)** **`claude -p` 서브프로세스(로컬 Claude Code 인증, 외부 OpenAI 키 불필요).** 설계서 §2의 "GPT-5.5"는 **프레임워크로 서빙할 모델의 플레이스홀더**였지 OpenAI 확정이 아니었음(운영자 명확화). 확정 아키(SCHED-3)가 이미 "Python 두뇌가 **직접 호출**, openclaw provider 라우팅 미사용"이라 R4/R5/R7과 **같은 claude -p 경로 재사용** → 두 번째 프로바이더·키·과금 0. 모델/프로바이더는 **`Scorer`/`LLMClient` 인터페이스 뒤 추상화** → .env 스위치로 드롭인 교체.
- **단일호출 근거:**
  1. 설계서 §2가 "뉴스 분류·요약·이벤트 추출"을 **고빈도·저단가·스키마 강제** 레이어로 둠 — R2는 **구조적 추출**이지 심층 토론·합성이 아님.
  2. R2는 06:30/16:30 **광범위 배치** 처리 → 항목별 멀티에이전트는 비용·지연 폭발.
  3. **검증·다양성은 R4가 담당**(perspective-diverse, **선별만** — 고강도·`single_stock`). R2 멀티화는 R4와 중복 → 프로포절 §4 "전수 검증 금지" 위배.
  4. `catalyst_strength` 신뢰는 **R7이 사후 캘리브레이션**(폐루프).
  5. 멀티에이전트 패턴은 설계상 **R3(페르소나 ×3)·R4(적대)** 전용.
- **claude -p 실측 봉투(2026-06-09):** `--output-format json` → `{is_error, subtype, result, total_cost_usd, modelUsage, …}`. 텍스트=`.result`, 성공=`is_error==false && subtype=="success"`, 모델=`--model`. 비용 유의(콜당 캐시생성 포함 ~$0.06) → R2는 **저단가 모델 .env 주입**(하드코딩 금지) + 배치로 콜 수 억제.
- **귀결(구현):** 배치 = scope 레이어별(L1 후보별 / L2 섹터별 / L3 거시), 각 배치 → **1회 호출**이 기사를 **이벤트로 클러스터링**(같은 촉매 다기사 = 1 EventRecord + `novelty` 디스카운트) + 촉매필드 채움. 환각가드: `affected`/`evidence`는 fetch된 기사에만 귀속(미검증=UNVERIFIED, 종목 박제 금지).
- **안전선:** R2 산출은 EventRecord→R3 ThesisRecord grounding으로만. **R1 게이트·R5.5(매매 발동)에 LLM 점수 주입 금지**(절대금지 #2).

---

## 시장 캘린더

### 🟢 CAL-1 — KRX 휴장일 데이터 출처 (음력·대체공휴일·임시휴장) — **2026-07-11 종결**
- **맥락:** `market_calendar` 가드(SCHED-1)가 거래일을 판정한다. 월-일 고정 공휴일(신정·삼일절·근로자의날·어린이날·현충일·광복절·개천절·한글날·성탄절·연말휴장 12/31)은 코드(`_FIXED_CLOSED_MD`)에 확정. **음력 공휴일(설날·추석·석가탄신일)·대체공휴일·임시휴장·제헌절은 추측 금지** — 연도별 명시 날짜가 필요했다.
- **관측(2026-07-11, 연속성 가드):** `--check`가 달력 미등록 휴장 **9일**을 드러냈다. 승인 소스(data.go.kr)가 **정상 응답 + 무자료**로 답한 날들(소스 장애는 `CollectError` raise이므로 빈 응답=휴장 관측).
- **결정(운영자 2026-07-11):** 관측 9일을 **공식 출처로 대조해 전부 확인**하고 `krx_holidays.json`에 등록. 대조 결과 관측치와 공지가 **9/9 일치**(추측 수정 없음). 미관측 미래 휴장일 5일도 같은 공지에서 확인해 함께 등록:
  | 등록분 | 근거 |
  |---|---|
  | 2025-10-06·07·08 | 추석 연휴 + 대체공휴일(10/8) |
  | 2026-02-16·17·18 | 설 연휴 |
  | 2026-03-02 / 05-25 / 08-17 / 10-05 | 대체공휴일 4회(삼일절·부처님오신날·광복절·개천절) |
  | 2026-06-03 | 제8회 전국동시지방선거 — **KRX 발표** |
  | **2026-07-17** | **제헌절(2026년 공휴일 재지정) — KRX 발표. 관측 불가(미래)라 이번 대조가 아니었으면 놓쳤을 휴장일** |
  | 2026-09-24·25 | 추석 연휴(9/26은 토) — **대체공휴일 없음**(설·추석은 일요일 중복 시에만 적용) |
- **출처:** KRX 발표 보도([한국경제 2026-05-20](https://www.hankyung.com/article/2026052094456) — 6/3·7/17 전 시장 휴장) · [2026 휴장일·대체공휴일 4회 목록](https://www.dpi1004.com/10769) · [2026 추석 대체공휴일 미적용](https://www.hankyung.com/article/2025063085857) · 관공서의 공휴일에 관한 규정.
- **제헌절은 연도별 항목으로 둔다** — `_FIXED_CLOSED_MD`(연도 무관)에 (7,17)을 넣으면 2025년 이전 7/17 거래일을 휴장으로 오판한다(2025-07-17은 정상 거래일). 테스트로 박제.
- **재발 방지(침묵 금지):** 파일에 `covered_through`(현재 `2026-12-31`) — 이 범위를 넘긴 판정은 `MarketCalendar.is_covered()=False`이고 `--check`가 ⚠️ 만료 경고를 낸다. **2027년 KRX 공지가 나오면 파일 갱신**(연 1회 운영 작업). 관측 휴장일도 `--check`가 등록/미등록으로 분리 보고 → 미등록분만 갱신 대상.
- **자동화(미채택):** data.go.kr 특일정보 API 어댑터는 승인 소스 미확정이라 구현하지 않았다(엔드포인트 추측 금지). 연 1회 수동 갱신으로 충분 — 필요해지면 PROPOSALS로.

### 🟢 CAL-3 — KRX 거래시간 연장(애프터마켓)과 §5 "장중"의 범위 — **2026-07-11 결정**
- **확인된 사실(2026-06-19 KRX 일정 재조정):**
  - **애프터마켓 16:00–20:00 — 2026-09-14 시행**([한국일보](https://www.hankookilbo.com/news/article/A2026062310130002438), [뉴스핌](https://www.newspim.com/news/view/20260619000738)).
  - **프리마켓 07:00–07:50 — 2027년 말로 재연기**([파이낸셜뉴스](https://www.fnnews.com/news/202606191644561782), [비즈워치](https://news.bizwatch.co.kr/article/market/2026/06/19/0028)). 당초 6/29 → 9/14 연기([노컷뉴스](https://www.nocutnews.co.kr/news/6486442))에서 프리마켓만 재차 밀렸다.
  - **정규장 09:00–15:30 불변.**
- **결정(운영자 2026-07-11):** **§5 "장중" = 정규장 ∪ 애프터마켓.** 애프터마켓은 실거래가 도는 시간이므로 LLM 라운드·주문 설계를 모두 휴면시킨다(보수적 해석).
- **구현:**
  - `in_after_market`/`in_extended_session` + `AFTER_MARKET_EFFECTIVE=2026-09-14`(시행일 이전 날짜엔 창 없음 — 과거 리플레이가 열려 있던 시장을 휴장으로 오판하지 않게). `require_llm_rounds_allowed`·`require_market_closed`가 확장 창을 본다. `in_krx_session`(정규장)은 **불변** — 체결·수급·arm-check가 쓰는 축이라 분리.
  - **가드 배선(신규):** §5 휴면은 그동안 **슬롯 배치로만** 지켜졌다(`require_llm_rounds_allowed`가 정의만 되고 호출부 0). `trading.run` 디스패치가 LLM 라운드 진입 전 가드를 호출하도록 배선 — 장중이면 `rc=3`(정상 스킵, P1 알림 없음). 수동 CLI(`python -m trading.score_news`)는 이 경로를 안 타므로 CAL-2대로 우회 유지.
  - **pm 체인 재배치**(애프터마켓 마감 후): score-pm 16:32→**20:05** · verify-pm 16:45→**20:20** · reason-pm 16:55→**20:35** · synth-pm 20:30→**21:30** · report-pm(저녁 결재) 21:00→**22:00**. 간격은 실측 소요(R2 ~64분·R4 ~23분·R3 ~25분·R5 ~4분) 기준 — R5는 R2/R3 산출을 DB로 받으므로 체인 종료 뒤에 둔다. news-pm(16:20)은 **순수 어댑터 수집이라 유지**(마감 뉴스를 제때 받는다).
  - 매니페스트 회귀 테스트: LLM 슬롯이 휴면 창 안으로 되돌아오면 `test_cron_llm_slots_outside_dormant_window`가 깨진다.
- **SEL-1 영향:** **해소되지 않는다.** KRX 프리마켓이 2027 말로 밀려 프리마켓 흐름변수는 여전히 NXT 의존(🔴 유지).
- **잔여(9/14 전후 확인):** 애프터마켓 체결이 **data.go.kr 일별시세의 종가·거래량 정의**에 포함되는지 미확인 — 포함되면 스크리너 거래대금·모멘텀의 의미가 바뀐다. 시행 후 실데이터로 대조(추측 금지).

### 🟢 CAL-2 — 장중 LLM 가드의 적용 범위 (cron 한정 vs 수동 실행 포함) — **2026-06-12 결정**
- **맥락:** 설계서 §5 "장중(09:00–15:30) LLM 라운드 전면 휴면". 가드(`require_llm_rounds_allowed`/
  `require_market_closed`)는 구현됨. 항상 강제하면 **운영자 수동 실행**(장중 재합성 등)도 차단된다.
- **결정(운영자 2026-06-12):** **cron 자동 경로는 가드 강제, 수동 CLI는 `--force`로 우회 허용.**
  R5(synth_playbooks)에 `--force` 플래그 — 장중 가드(`require_market_closed`)를 수동 한정으로 우회.
  정합 근거: ① R5 입력은 EOD라 장중 실시간 가격에 휩쓸리지 않음 ② P-7로 집행이 아침 승인으로
  분리돼 산출은 draft → 다음 거래일 arm-check 승인(충동 집행 차단 유지). 장중 강제 시 경고 출력.
  cron 디스패치(`run._synth_playbooks`)는 force 미전달 → 가드 유지(장중 자동 실행 없음).
- **확장:** 다른 LLM 라운드(R2~R4·R7)도 수동 백필 필요 시 동일 `--force` 패턴 적용 가능(현재는 R5만).

---

## 라운드 파이프라인

### 🟡 SEL-1 — R5.5 흐름 관측치 소스 (NXT 프리마켓 어댑터 부재) — **2026-06-12 부분 해소**
- **맥락:** R5.5 선택기 입력(갭·프리마켓 거래량·미국 마감·환율 개장가) 중 프리마켓 계열은 NXT 데이터(🔴 외부 의존) 필요. 어댑터 미구현.
- **2026-07-11(CAL-3 조사):** KRX 자체 프리마켓(07:00–07:50)이 **2027년 말로 재연기** → 이 경로로는 해소되지 않는다. NXT 의존 유지(🔴).
- **2026-06-12 부분 해소(P-6 arm-check):** `flowsnap.build_snapshot`이 KIS 실시간(KIS-RT-1)으로
  **체결강도(execution_strength)·전일고가 회복(prev_day_high_reclaim)·호가 불균형(orderbook_imbalance)**
  3종을 실데이터로 채움(2026-06-12 장중 실호출 검증). **프리마켓 거래량(premkt_volume_ratio)·갭(gap_pct)은
  여전히 NXT 의존 🔴** — "관측치 없음"으로 정직 표기. 잔여 NXT 변수는 주입 파일 보충 경로 유지.
- **R5 조건 제약(2026-06-12, 운영자 결정):** R5가 미수집 변수(premkt 등)로 조건을 짜면 매일
  '관측치 없음'이라 그 플레이북은 영영 발동 불가(실증: 6/11 3종 다 premkt가 첫 조건이라 미발동).
  → `flowsnap.OBSERVABLE_FLOW_VARS`(KIS 자동 수집 3종) 단일 소스를 R5 프롬프트에 주입해 arm/abort/
  confirmation 조건을 **관측 가능 변수로만** 짜게 제약. NXT 어댑터가 생기면 OBSERVABLE에 추가만 하면
  R5 프롬프트가 자동 반영. (기존 premkt 포함 플레이북은 재합성 전까지 잔존 — 다음 R5부터 정리.)
- **잠정 처리(보수):** 선택기는 순수 함수, 관측치는 KIS 실시간 + `.runtime/flow/<YYYYMMDD>.json` 주입 파일.
  **둘 다 없음=빈 스냅샷=전 플레이북 비활성(비거래)** — 관측치 추측 금지. 조건식 문법은 `<op><숫자>`·
  `==true/==false`(SEL-2), 시각·문자열은 평가 불가=미충족.
- **해소책:** NXT 소스 확정(🔴) 후 premkt 계열을 OBSERVABLE_FLOW_VARS·스냅샷에 추가 — R5·선택기 변경 불필요.

### 🟢 KIS-RT-1 — KIS 실시간 시세성 TR (P-6 arm-check) — **2026-06-12 관측 확정**
- **맥락:** arm-check가 9~10시 흐름 관측치를 KIS 실시간으로 채우려면 현재가·체결강도·호가 TR 필요.
- **관측 확정(2026-06-12 장중 실호출):**
  - 주식현재가 체결 `GET /uapi/domestic-stock/v1/quotations/inquire-ccnl` TR `FHKST01010300` —
    output[0]에 `stck_prpr`(현재가)·`tday_rltv`(당일 체결강도, 100 기준) 관측. (현재가 시세 `inquire-price`
    `FHKST01010100`엔 체결강도 필드 없음 — 미사용 확인.)
  - 주식현재가 호가 `GET /uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn` TR `FHKST01010200` —
    output1에 `total_bidp_rsqn`·`total_askp_rsqn`(매수/매도 총호가잔량) → imbalance=(bid−ask)/(bid+ask) 관측.
- **상태:** 🟢 확정. `collectors/kis.py`(quote_ccnl·quote_asking_price), 결측·비수치는 None=관측치 없음(추측 금지).

### 🟢 SEL-3 — R5.5 cron(select-am) 날짜 라벨 어긋남 — **2026-06-12 해소**
- **맥락(2026-06-12 발견):** R5(synth-pm, 밤 20:30)는 `pb.<당일>`로 생성하는데 R5.5(select-am,
  다음날 아침)·기존 arm-check는 `playbooks_for_day(<다음날>)`로 조회 → 하루 어긋나 전일 밤 승인분을
  못 찾았다(실증: 6/12 아침이 `pb.20260611`을 못 봄). 자동 arm이 사실상 빈 풀로 동작했을 가능성.
- **해소(P-7):** arm-check·`select_playbooks` 둘 다 `PlaybookStore.active_playbooks`(status=approved
  + TTL(time_stop_days 거래일), 날짜 라벨 비의존)로 통일. select_playbooks의 흐름 소스도 arm-check와
  같은 `flowsnap.build_snapshot`(KIS 실시간 + 주입 파일)으로 일원화, `load_snapshot` 제거.
  검증: `test_runner_arms_across_date_label_mismatch`(6/11 승인 → 6/12 아침 arm), 실DB 6/12 실행 정상.

### 🟢 SEL-2 — R5 흐름변수 boolean 조건과 selector 숫자 문법 불일치 — **2026-06-12 해소**
- **맥락:** R5가 `prev_day_high_reclaim ==true`처럼 boolean 흐름변수 조건을 산출하는데, selector(`engine._COND`)는
  `<op><숫자>`만 평가 → `==true`는 평가 불가=미충족으로 빠졌다(arm-check 실호출에서 노출). 현재가가
  전고를 회복(1.0)해도 발동 판정에 반영되지 않았다.
- **해소(택1 — 둘 다 적용):** ① selector `engine.eval_condition`에 `==true/==false`(대소문자 무관) 평가 추가
  (`_BOOL`, 관측치 1.0=참/0.0=거짓 — flowsnap 인코딩과 정합). ② R5 프롬프트에 조건식 문법 가이드
  명시(연속 변수=`<op><숫자>`, boolean 변수=`==true/==false`, 시각·문자열 금지). explain도 boolean을
  '= 예/아니오'로 표기. 기존 6/11 데이터(`==true`)가 재산출 없이 즉시 평가됨(실증). 시각(`09:30`)
  등은 여전히 평가 불가.
- 검증: `test_boolean_condition_eval`·`test_boolean_arm_condition_activates`(end-to-end), 실DB arm-check
  에서 `prev_day_high_reclaim = 예(true) → 충족(O)` 확인.

### 🟡 R7-1 — R7 채점·레짐의 입력 데이터 갭
- **맥락:** 설계서 §3 R7 채점은 "트리거 발동 후 시계 내 방향 일치"·운영자 준수율, 레짐은 시초 1시간 변동성·개인 강도 프록시(신용잔고·예탁금·레버리지 ETF)를 요구. 현재 ①흐름 데이터 부재로 트리거 발동 감지 불가 ②집행 데이터 부재(KIS 미구현)로 준수율 측정 불가 ③레짐 입력(금융투자협회 신용잔고 등, 부록 A) 수집기 부재.
- **잠정 처리(보수, notes에 명시):** 방향 채점은 **트리거 무관**(as_of 익영업일→horizon 종가), 준수율 미측정 명시, 레짐은 전종목 |등락률| 중앙값 비율 프록시만. 미성숙·결측 논제는 채점하지 않음(부분 채점 금지).
- **해소책:** 흐름 스냅샷 어댑터(SEL-1과 공유) + KIS 체결 어댑터 + 금융투자협회 수집기.

### 🟡 R5-1 — "생존 논제" 판정 (논제 레벨 적대 라운드 부재)
- **맥락:** 설계서 §3 R4는 **논제(ThesisRecord) 살해 시도**, R5 입력은 "생존 논제". 그러나 현 구현의 R4(verify-catalysts)는 **이벤트(촉매) 검증**이다 — 논제 레벨 적대 라운드는 미구현. R3 논제를 무엇으로 거를지 미결정.
- **잠정 처리(보수적):** R5는 최근 논제 중 `direction != flat` 전부를 입력하되, 촉매 이벤트의 R4 검증 상태([생존]/[기각])를 프롬프트에 동봉해 합성 단계가 반영하게 한다. 플레이북 채택은 R5가 보수적으로(빈 배열=정답).
- **해소책:** 논제 레벨 R4(페르소나 성적표 입력 포함) 구현 또는 운영자 결정으로 현 구조 승인.

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
- 🟢 ~~KRX 정보데이터시스템: 시세·투자자별 매매동향~~ → **해소(2026-06-11)**: 시세=data.go.kr(기 운영), 수급=KIS TR(COLLECT-2 갭① 해소 참조). KRX 직접 접근 불필요. (공식 KRX Open API엔 투자자별 매매동향 자체가 없음 — 29개 서비스 목록 확인)
- 🔴 NXT 프리·애프터마켓 데이터: 소스·접근
- 🔴 증권사 조건부(청산) 주문 API: 보유 증권사 스펙 확인 선행 (Phase 1 필수 — §6)
- 🟢 DART OpenAPI / 한국은행 ECOS: 공개 문서 기반 구현 가능
