# PROPOSALS — 설계서에 없는 기능 제안 (구현 전 합의)

> CLAUDE.md 규칙: 설계서에 없는 기능은 임의 구현 금지 — 여기 적고 합의 후 진행.
> 형식: 상태(💡제안 / 👍채택 / 🗄️보류) · 제안 · 근거 · 영향범위.

## 💡 P-1 — 섹터 taxonomy 확장 (운송·해운·레저)
**제안:** `domains.Sector` 26종에 다음 버킷 추가 검토.
- `shipping_logistics` (해운·물류) — HMM·흥아해운·팬오션 등 외항/벌크 해운, 택배·물류.
- `transport` (육상운송) — 고속버스·항공여객 등. (※ 항공'우주'는 기존 aerospace_uam와 구분)
- `leisure_casino` (레저·카지노) — 강원랜드·파라다이스·호텔/리조트.

**근거:** grounded 섹터 분류(`dart-ksic-v1`) 결과 게이트 통과 종목 중 미분류 잔존분에
**26 taxonomy에 정확 버킷이 없는** 실거래 종목이 다수(HMM·흥아해운·동양고속·강원랜드·흥구석유).
이들은 추측이 아니라 **버킷 부재**로 미분류 — taxonomy를 늘리지 않으면 영구 미분류.
해운은 KOSPI 대형주(HMM)도 포함돼 무시하기 어렵다.

**영향범위:** `domains.py`(enum+SECTORS 메타), 기존 분류 소스 재태깅 불필요(append),
스크리너 출력. KSIC 매핑(`50112` 해운·`49220` 운송·`91249` 레저)이 깨끗하면 grounded 규칙도 추가 가능.

**미결:** taxonomy를 "거래 테마" 기준으로 늘릴지(트레이더 관점) vs 산업 기준으로 둘지 —
설계서 §도메인 정의와 정합 확인 필요. 운영자 합의 전 보류.

## 💡 P-2 — 미분류 잔존분 LLM 폴백 분류기 (claude -p 스텝)
**제안:** grounded(`dart-ksic-v1`)·큐레이션(`manual-curated-v1`)이 못 채운 잔존 미분류를
`claude -p` 서브프로세스(또는 멀티에이전트)로 분류하는 **재사용 스텝**. 현재는 유명주만 수기 큐레이션.

**근거:** 전자부품(262대 코칩·성호전자·자화전자 등)·다각화주(서진시스템)는 KSIC 혼재라
결정론 매핑 불가하고, 수기 큐레이션은 신규 종목마다 손이 든다. CLAUDE.md상 분류는 LLM 허용 영역
(원 `llm-cls-v1`이 멀티에이전트 산출). 환각가드(모르면 미분류) + 출처 근거 필수.

**영향범위:** `sectors.py`에 LLM 폴백 함수, `/collect` 배선. 모델명 .env 주입. 비용↑(거래일마다 잔존분).
**미결:** 멀티에이전트 Workflow vs 단일 `claude -p`. 검증(cross-check) 깊이.

## 👍 P-3 — 뉴스 단일 영속 DB + 인덱싱 (시계열 통합)
**제안:** 뉴스 landing을 날짜별 분산(`.runtime/collect/<날짜>/news.sqlite`)에서
**단일 영속 DB `data/news.sqlite`**(시세 `market.sqlite`와 동격)로 통합하고, 조회를 인덱스로 가속.
- entity 정규화: `news_entities(news_id, entity, entity_type)` 조인 테이블 — `entities LIKE` 풀스캔/부분일치 오탐 제거.
- 인덱스: `news_entities(entity)`, `news_items(published_at)`, `news_items(title_norm)`.
- 전역 dedup: URL은 PK(`id`)로 자동(`INSERT OR IGNORE`), 제목은 `title_norm` 매칭으로 크로스-런 병합(entities 머지).
- (후속) FTS5 전문검색(title/snippet) · 보존정책(published_at 윈도).

**근거:** 현 구조 결함 — ① factpack `_latest_news_db()`가 **최신 1개 DB만** 봐서 어제 촉매가 오늘 안 보임
(촉매는 며칠 지속 → **시계열 단절**) ② `entities LIKE '%"코드"%'` 풀스캔·부분일치 오탐 ③ 크로스-데이 중복 미차단.
시세는 이미 단일 `data/market.sqlite`라 뉴스만 분산된 게 비일관.

**영향범위:** `collectors/news.py`(NewsStore 스키마·upsert·recent_for), `collect_news.py`(경로),
`factpack.py`(`_latest_news_db` 제거→단일 경로), `work-boot` 스킬(신선도 체크 경로).
기존 데이터 마이그레이션 불필요(현재 news.sqlite 0개).
append-only(`INSERT OR IGNORE`) 유지 — 프로젝트 철학 보존, "언제 처음 봤나"는 `fetched_at`에 잔존.

**상태:** 👍채택(운영자 지시 2026-06-09) — 구현 진행.

## 💡 P-4 — 경제 뉴스 촉매 파이프라인 (수집→검증→분류·스코어→적대검증→인덱싱)
**제안:** 뉴스를 "종목 쿼리 적재"에서 **촉매 인텔리전스 파이프라인**으로 격상. 핵심 통찰:
종목이 움직이는 이유는 자사 실적보다 거시·테마·수급일 때가 더 많고 빠르다(실증: 젠슨황 방한이
SK네트웍스 등 AI 연관주 촉매로 종목명 검색에 약하게만 잡힘). **거르지 말고 폭넓게 적재 + 판단은 LLM에 위임.**

> **설계서 정합:** 새 구조가 아니라 **R0→R1→R2→R3→R4 골격의 뉴스 인스턴스화**.
> `AssetClass.NEWS`("정형화 대상")·`ThesisRecord(direction·horizon_days·confidence·invalidation)`가 이미 존재.
> | 파이프라인 | 라운드 | 주체 |
> |---|---|---|
> | 광범위 수집 | R0 | 코드 어댑터(COLLECT-4) |
> | 신선도·정합성 검증 | **R1** | **코드, LLM 금지** |
> | 분류·스코어링 | R2 | GPT-5.5 |
> | 적대 스코어 검증 | R4 | claude -p 멀티에이전트 |
> | 인덱싱 | Fact Store | 코드(P-3 확장) |

### 1. 스코어 = EventRecord 확장 (방향·확신은 R3 몫 — 중복 금지)
사건의 **객관 속성만** EventRecord(R2)에. 방향/시계/확신은 종목·페르소나별이라 R3 ThesisRecord가 채움
(같은 사건이 A엔 호재·B엔 악재 → 사건 레벨에 방향 박으면 오류).
- `catalyst_strength: float 0~1` — 사건 시장 임팩트(종목 독립)
- `scope: {single_stock | sector_theme | broad_market}` — entity 카디널리티 1차 추정 + LLM 확정
- `catalyst_type: CatalystType` — §2
- `affected: list[(srtn_cd, relevance 0~1)]` — 영향종목 + 연결강도
- `novelty: float 0~1` — 신규성(재탕 디스카운트)
- 근거 기사 id는 `EventRecord.evidence`(기존)에 필수(환각가드). 재스코어링=append 새 버전.

### 2. 분류 taxonomy = 2축 직교
- **섹터축:** 기존 26 `Sector` enum **재사용**(종목 분류와 조인 — 신설 금지).
- **촉매유형축(신설 `CatalystType`):** `earnings`·`guidance`·`policy_regulation`·`ma_restructure`·
  `supply_chain`·`flow_demand`·`macro`·`product_tech`·`legal`·`management`·`rumor_unconfirmed`.
- 근거: R7 캘리브레이션이 "어떤 촉매유형 가설이 적중하나" 학습하려면 촉매유형 라벨 필요.

### 3. 수집 = 3계층 쿼리플랜 (현행 L1만)
- **L1 종목(현행):** 후보 srtn_cd × name → 네이버 (~600콜/일).
- **L2 섹터·테마(신설):** 26섹터 + 활성테마(반도체·AI·로봇…) → 네이버 (~520콜/일).
- **L3 거시(현 `FOREIGN_THEMES` 확장):** Fed·CPI·환율·유가·지정학 → SearXNG.
- 예산: 네이버 2.5만콜/일 → ~1,100콜 사용(여유 충분). `scope=broad_market`은 L3에서 자연발생.

### 4. 스케줄 + **전수 검증 안 함** (지연·실효성 제동)
- 06:20/16:20 R0 수집(3계층) + 장중 heartbeat 속보 / 06:30/16:30 R1검증→R2분류·스코어 /
  06:45/16:45 **R4 적대검증 — 선별만**.
- **전수 멀티에이전트 검증 금지**(토큰 무관이라도): `catalyst_strength` 상위 + `scope=single_stock`/고강도만
  perspective-diverse(강도/종목연결/시점정합) 검증. loop-until-dry는 신규 고강도에만.
  저강도·broad는 검증 없이 grounding 노출(R3가 가중).

**안전선(절대):** 모든 스코어·검증 산출은 **R2 EventRecord → R3 ThesisRecord grounding으로만** 흐른다.
**R5.5(매매 발동)·R1(게이트)에 LLM 점수 주입 금지**(절대금지 #2 — 매매판단 LLM 차단). 매매 발동은 코드 게이트 + 미시구조 유지.
R7이 스코어 적중도를 사후 캘리브레이션해 `catalyst_strength` 신뢰 보정(폐루프).

**영향범위:** `contracts/event.py`(필드 추가), `domains.py`(`CatalystType`), `collectors/news.py`(3계층 `build_query_plan`),
`rounds/`(R2 분류·스코어러, R4 검증), `gates/`(R1 뉴스 검증), `ops/openclaw/cron_jobs.py`(슬롯), 인덱싱(P-3 news DB 확장).

**잔여 미결:** ① 활성테마 리스트 큐레이션 출처(임의확장 금지 — 운영자/섹터 메타 → **🟡 OPEN_QUESTIONS NEWS-L2**) ② ~~`catalyst_strength` 스코어러 GPT-5.5 단일 vs 멀티에이전트~~ → **🟢 결정: 단일 GPT-5.5(OPEN_QUESTIONS NEWS-R2)** ③ R7 캘리브레이션 연동 시점(파이프라인 안정 후).

**상태:** 👍채택(운영자 지시 2026-06-09) — **부분 구현 착수.** 마일스톤 분할:
- ✅ **P-4.1 계약·taxonomy 바닥**(2026-06-09): `domains.CatalystType`(11종) + `EventRecord` 촉매필드(`catalyst_type`·`scope`·`catalyst_strength`·`novelty`·`affected`[AffectedStock]) + `Scope` enum + `Score`(0~1) 타입. 전부 옵셔널(하위호환). 방향·확신은 §1대로 EventRecord 제외(R3 몫). 스키마 테스트 +7.
- ✅ **P-4.3 3계층 쿼리플랜**(2026-06-09): `build_query_plan(candidates, sectors, themes)` — L1 종목 + **L2 26 `Sector` 라벨**(`_sector_query` 결정론 키워드화) + L3 거시. `collect_news`가 `list(Sector)` 주입. 라이브 검증: L1=15·L2=26·L3=6(SearXNG 미설정 blocked), 295건 적재(섹터링크 260). **L2 활성 서브테마는 보류 → OPEN_QUESTIONS NEWS-L2**(임의확장 금지). 노이즈(naver sort=date)는 설계상 R2가 거름.
- ✅ **P-4.2 R1 뉴스 신선도·정합성 게이트**(2026-06-09, 순수 코드·LLM 금지): `gates/news.py` — `NewsItem`에 플래그 **부착**(폐기 안 함). 플래그 `stale`(신선도 지평 초과)·`undated`(published_at 미상)·`future_dated`(미래·시계오류)·`low_trust`(<임계, COLLECT-4 UNVERIFIED). `NewsVerdict.fresh`(신선도 결함 무 → **R5 하드게이트** 기준)·`usable`(무결). `GateConfig`(max_age_days 3·min_trust 0.5·future_skew 60m, 전부 knob). 플래그는 `(item,now,config)` 결정론 함수 → **영속화 안 함**(now 상대적). 이중-소스 `conflict`는 환율·지수 FactRecord용이라 뉴스 비적용(docstring 명시). `NewsStore.recent()` 추가(R2도 사용). **라이브 검증**: 325건 → usable/fresh 314·stale 11(06-01~06-04 기사, DB 쿼리와 일치)·나머지 0. pytest +8.
- ✅ **R2 분류·스코어러 코어**(2026-06-09, 단일 호출·`claude -p` — NEWS-R2): `llm.py`(LLMClient 추상화 + `ClaudeCliClient` — claude -p 봉투 실측 기반·runner 주입) + `rounds/r2.py`. R1 통과 뉴스를 scope 레이어별 배치(`build_batches`, 종목>섹터>테마 1키) → **배치당 1호출** → 기사 클러스터링 → `EventRecord`(촉매필드). 환각가드: affected=universe srtn_cd만·evidence=배치 기사 id만. 관대처리: event_type 보정(catalyst_type 맵), 부적합 옵셔널→None, **summary 누락만 폐기**(+`rejected_reasons`). 모델 .env 주입(`R2_MODEL`/`CLAUDE_MODEL`). **라이브 검증**(haiku, 4배치): 7이벤트·0폐기 — **젠슨황→네이버(035420) 0.85 정확 포착**(프로포절 핵심동기 입증), 노이즈(충북 화학사고)는 affected=[]로 격리. pytest +24(llm 14·r2 10).
- ✅ **R2 영속화 + run 배선**(2026-06-09): `journal/events.py` `EventStore`(단일 `data/events.sqlite`, **append-only**·id별 version, 핵심컬럼+JSON payload 무손실, `event_affected` 종목조인 → `recent`/`for_srtn`). `score_news.py` 러너(후보 universe + NewsStore 최근 → R1 게이트 → R2 → EventStore, client 주입가능) + `trading.run` `score-news` 라운드. pytest +4(EventStore 왕복·버전·종목조회).
- ✅ **R4 적대 스코어 검증**(2026-06-09, claude -p·선별): `contracts/event.py`에 `Verification`/`LensVerdict`(EventRecord 옵셔널 필드) + `rounds/r4.py`. **전수 금지** — `select_events`로 미검증·고강도(single_stock≥0.5 OR any≥0.7)만 선별·강도순·상한. 이벤트당 **3렌즈 perspective-diverse**(강도/종목연결/시점정합) 적대 호출 → **다수결**(≥2 생존) `confirmed`. 적대 기본 회의적(근거 약함·호출실패→survived=False). 검증결과 `verification` 부착한 **새 version** EventStore append(R3 grounding 정합). `NewsStore.by_ids`(근거 기사) + `verify_news.py` 러너 + `trading.run` **`verify-catalysts` 라운드**. pytest +9.
- ✅ **cron 슬롯 배선**(2026-06-09): `ops/openclaw/cron_jobs.py`에 뉴스 촉매 파이프라인 슬롯 — am `collect-news`(06:20)→`score-news`(06:30)→`verify-catalysts`(06:45), pm(16:20/32/45). 9잡 전부 **`mode=exec`**(R2/R4도 — openclaw는 exec만, claude -p는 Python 내부 호출, NEWS-R2/SCHED-3). `sync.py` round 정합성 통과(dry-run). test_dispatch `_EXPECTED` 갱신.
- ✅ **R3 페르소나가 EventStore 소비**(2026-06-09, 다운스트림): `rounds/r3.py` — 촉매 보유 종목별 3페르소나(수급/사이클/매크로) **입력격리**(catalyst_type으로 촉매 분배 + 페르소나별 데이터) → ThesisRecord(invalidation 필수·1회 재생성). `ThesisStore`(append-only) + `reason_news.py` + `reason-theses` 라운드 + cron(06:55/16:55). 미수집 핵심지표(투자자별 매매·DRAM가격 🔴)는 **보류·저확신**(추측 금지). 라이브 입증: 수급 페르소나가 "보류"(conf 0.15)·사이클이 재무 grounded short.
- ⬜ 인덱싱(P-3 확장 — FTS5 등, 선택) · ⬜ R3 산출의 R4 적대검증(설계서 R4 thesis-kill, 별도) · ⬜ R5 합성.

---

## 💡 P-5 — DiscussPack: 종목 토론 컨텍스트 사전 조립 + 캐싱 (discuss 개편)

**동기 (운영자, 2026-06-11):** `/discuss <종목>`이 토론 전에 필요한 컨텍스트(종목·테마, 관련 뉴스+사실검증,
가격 추이, 투자자별 수급 포지션)를 **미리 결정론 조립**하고 **별도 DB에 캐싱** — 같은 종목 재질문 시
갱신 여부를 운영자에게 확인 후 재사용. 현재 discuss는 factpack 단일종목 조회만 쓰고, 뉴스 검증·수급
누적 포지션·캐시가 없다.

### 산출물: `DiscussPack` (pydantic 계약 — FactPack 확장이 아니라 별도 계약)
| 섹션 | 내용 | 소스(결정론) |
|---|---|---|
| identity | srtn_cd·name·market·시총·**섹터/테마 태그** | market.sqlite(stock_sectors: llm-cls-v1→dart-ksic-v1) |
| price_trend | 최근 5/20/60거래일 변동률·거래대금 서지·252d 신고가 근접·일별 요약 N건 | market.sqlite (스크리너 신호 함수 재사용) |
| flows_position | **개인/외인/연기금/기관(연기금外)** 최근 5/20일 누적 순매수(억원) + 일별 5건 | flows.sqlite — 없으면 KIS 종목별 TR 1콜(≈30거래일) 즉시 수집 |
| news | 해당 종목 entity 매칭 최근 뉴스 ≤8 (trust·R1 플래그) | news.sqlite `recent_for` — 빈약하면 종목명 쿼리 수집(COLLECT-4 범위) |
| verified_events | **뉴스 사실검증(멀티에이전트)**: 기존 검증된 이벤트(EventStore) + 미평가 뉴스만 scoped R2(이벤트화)→R4(3렌즈 적대검증) | EventStore + rounds/r2·r4 재사용(claude -p) |
| disclosures/financials | 공시 90d·재무 YoY | DART (factpack 로직 재사용) |
| meta | built_at(KST)·섹션별 as_of·notes(결측 사유) | — |

### 캐시: `data/discuss.sqlite` `discuss_packs` (append-only)
- `(srtn_cd, version, built_at, price_as_of, pack_json)` — 갱신=새 version(UPDATE 금지). 최신 version 조회.
- **신선도 판정(결정론)**: `price_as_of < 시세 DB 최신 거래일` → STALE 라벨. 뉴스 최신 fetched_at도 비교 표기.

### 조립기: `src/trading/discuss_pack.py` (CLI)
- `--check <종목>`: 캐시 유무·version·built_at·STALE 여부 출력 → **스킬이 "갱신할까?" 질문에 사용**
- `--build <종목> [--no-verify]`: 결정론 수집(가격·수급·뉴스·공시) → 신규 뉴스만 R2→R4 검증 → 새 version 저장 + 요약 출력
- 기본 `<종목>`: 캐시 최신 version 출력(없으면 build 안내)

### 스킬 흐름 (discuss SKILL.md 개정)
1. `--check` → **캐시 있으면 운영자에게 갱신 여부 질문**(STALE이면 갱신 권장 명시) — 운영자 지시: 자동 갱신 아님
2. 갱신 OK/캐시 없음 → `--build` (LLM은 트리거만)
3. pack을 grounding으로 기존 적대 토론(§2~5) — §4 "수급 데이터 없음" 문구 폐기(flows로 대체)

### LLM 경계 (절대금지 #2 정합)
- 조립·캐시·신선도·수급·가격 = **순수 코드**. LLM 개입은 ① 뉴스 검증(기존 승인 경로 R2→R4 그대로) ② 토론 자체, 둘뿐.
- 비용: build당 claude -p는 **신규(미평가) 뉴스에 비례**(기검증 이벤트 재사용, 상한 8건) — 캐시가 반복 비용 상쇄.

**영향범위:** `contracts/discuss.py`(신규)·`discuss_pack.py`(신규)·`journal/discuss.py`(캐시 스토어)·
`collectors/flows.py`(단일종목 수집 함수)·`.claude/skills/discuss/SKILL.md`(개정). 기존 라운드 무변경.

**미결 해소(운영자 결정 2026-06-11):** ① 뉴스 보강 **build 내장** ② TTL 없이 **영구 보존**(append-only)
③ 이벤트 범위 = **종목 + 소속 섹터(sector_theme)까지**(broad_market 제외).

**상태:** 👍채택·구현 완료(2026-06-11) — `contracts/discuss.py`·`journal/discuss.py`(DiscussStore)·
`discuss_pack.py`(--check/--build/기본)·`flows.collect_stock`·discuss SKILL.md 개정. pytest 289·mypy 0.
실거동: 후성 v1(--no-verify)→v2(풀 빌드: R2 신규 8건→이벤트 1, R4 선별 1·검증 1) 캐시 적재 확인.
