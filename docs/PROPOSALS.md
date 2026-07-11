# PROPOSALS — 설계서에 없는 기능 제안 (구현 전 합의)

> CLAUDE.md 규칙: 설계서에 없는 기능은 임의 구현 금지 — 여기 적고 합의 후 진행.
> 형식: 상태(💡제안 / 👍채택 / 🗄️보류) · 제안 · 근거 · 영향범위.

## 👍 P-1 — 섹터 taxonomy 확장 (운송·해운·레저) — 채택·구현 2026-07-11
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

**상태:** 👍채택(운영자, P-9 선행 조건으로 2026-07-11) — **구현 완료**: Sector 26→29
(`shipping_logistics`·`transport`·`leisure_casino`), KSIC 실측 규칙 7종 + 큐레이션 8종(LG생건·금융지주 7),
`--retag` 소급 적용. 상위 30 미분류 22→16. 상세: `history/work/archive/2026-07-11-p1-sector-taxonomy.md`.
잔여 미분류 151종(혼재 코드)은 P-2로.

## 👍 P-2 — 미분류 잔존분 LLM 폴백 분류기 (claude -p 스텝) — 채택·구현 2026-07-11
**제안:** grounded(`dart-ksic-v1`)·큐레이션(`manual-curated-v1`)이 못 채운 잔존 미분류를
`claude -p` 서브프로세스(또는 멀티에이전트)로 분류하는 **재사용 스텝**. 현재는 유명주만 수기 큐레이션.

**근거:** 전자부품(262대 코칩·성호전자·자화전자 등)·다각화주(서진시스템)는 KSIC 혼재라
결정론 매핑 불가하고, 수기 큐레이션은 신규 종목마다 손이 든다. CLAUDE.md상 분류는 LLM 허용 영역
(원 `llm-cls-v1`이 멀티에이전트 산출). 환각가드(모르면 미분류) + 출처 근거 필수.

**영향범위:** `sectors.py`에 LLM 폴백 함수, `/collect` 배선. 모델명 .env 주입. 비용↑(거래일마다 잔존분).
**미결:** 멀티에이전트 Workflow vs 단일 `claude -p`. 검증(cross-check) 깊이.

**상태:** 👍채택(운영자, P-9 선행 조건으로 2026-07-11) — **구현 완료**: `sector_llm.py`(단일 `claude -p`
배치 25종, `llm-fallback-v1` 최후순위 소스, 환각가드: 모르면 미분류·taxonomy 밖 폐기·confidence<0.7
미채택·basis 필수). 실행 결과 미분류 151→28(채택 123, 폐기 0), **상위 30 미분류 22→2**.
미결이던 검증 깊이는 "단일 패스 + 코드 재검증 + 임계"로 시작 — cross-check는 오분류 실증 시 추가.
상세: `history/work/archive/2026-07-11-p2-llm-fallback.md`.

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

## 👍 P-6 — 아침 arm-check: 온디맨드 발동 판단 + 해설 + LLM 분석 (운영자 9~10시 집행 보조)

**상태:** 👍채택(운영자 결정 2026-06-12 "KIS 실시간 + 골격 동시") — 구현 진행.

### 문제
저녁 결재 보고는 승인 요청(OrderDraft)을 내지만, 9~10시 실제 집행 시점에 운영자가
"지금 이 주문의 발동 조건이 충족됐나? 이게 무슨 뜻이냐"를 매번 사람(또는 Claude 세션)에게
물어 코드를 뒤져야 한다(2026-06-12 피드백). R5.5 선택기는 cron(08:50)으로 돌지만 그 산출은
arm/P1 알림이고, **운영자가 집행 직전 온디맨드로 "판단+해설"을 받는 경로**가 없다.

### 산출: `/arm-check` 스킬 + `trading.arm_check` 러너
운영자가 9~10시에 `/arm-check` 실행 →
1. **로드(결정론)**: 당일(또는 지정일) approved OrderDraft + 플레이북.
2. **흐름 관측치 수집(결정론)**: KIS 실시간(현재가 체결강도·전일고가 회복·호가 불균형) +
   market.sqlite 전일고가. 프리마켓 거래량(premkt_volume_ratio)은 NXT 부재(SEL-1 🔴)로 미수집 →
   "관측치 없음". KIS 미설정/실패 시 `.runtime/flow/<날짜>.json` 주입 파일 폴백, 그것도 없으면 빈 스냅샷.
3. **발동 판단(순수 코드)**: 기존 `selector/engine.py`의 `select()` 재사용 — arm_conditions AND,
   미충족 사유까지. **LLM 미개입(절대금지 #2).**
4. **결정론 해설**: 흐름변수 한국어 번역 + 3트랜치 진입 구조 + 스탑/시간손절 의미 + cap 표현식 풀이.
5. **LLM 분석 해설**: 스킬(이 Claude 세션)이 3·4의 결정론 산출을 받아 리스크·맥락 코멘터리.
   **판단은 코드가 끝냈고 LLM은 해설만** — discuss 패턴과 동일(결정론 grounding → LLM 토론).

### LLM 경계 (절대금지 #2 정합)
- 로드·흐름 수집·발동 판단·결정론 해설 = **순수 코드**(`trading.arm_check` JSON 산출).
- LLM 개입은 **5단계 분석 해설뿐** — 발동 여부 결정에 미개입(코드가 ACTIVE/INACTIVE 확정 후 설명만).

### 데이터 소스 (절대금지 #1 정합 — 추측 구현 금지)
- KIS 실시간 TR(현재가 `inquire-price` / 호가 `inquire-asking-price-exp-ccn`)은 공식 표준 TR로
  구현하되 **장중 실호출 미검증**(밤 구현, 장 폐장). OPEN_QUESTIONS에 **장중 1회 검증 게이트** 등록 →
  운영 전 검증 필수. 미검증 동안 응답 필드 불확실분은 **None=관측치 없음**(보수, 추측 금지).
- premkt_volume_ratio·gap 등 NXT 의존분은 여전히 SEL-1 🔴 — "관측치 없음"으로 정직 표기.

### 영향범위
`collectors/kis.py`(현재가·호가 TR 추가)·`flowsnap.py`(신규: KIS→FlowSnapshot)·
`reports/explain.py`(신규: 흐름변수·트랜치 결정론 해설)·`arm_check.py`(신규 러너)·
`.claude/skills/arm-check/SKILL.md`(신규). 기존 라운드·selector·R5 무변경(재사용만).

### 미해소(등록)
- SEL-1: premkt_volume_ratio NXT 소스 — 잔존 🔴(이 기능은 KIS 가용분으로 부분 충족).
- KIS-RT-1(신규): KIS 실시간 현재가·호가 TR 장중 실호출 검증.

## 👍 P-7 — approved 활성 풀 + TTL + 승인 도구 (OrderDraft 다일 생명주기)

**상태:** 👍채택(운영자 결정 2026-06-12 "2번으로, time_stop_days TTL + 승인 도구 같이") — 구현 완료.

### 문제 (2번 선택의 배경)
arm-check/R5.5가 **당일 날짜 라벨**(`playbooks_for_day(today)`)로 플레이북을 조회한다. 그런데:
1. **날짜 어긋남 버그**: R5(synth-pm)는 밤 20:30에 `pb.<당일>`로 생성, R5.5(select-am)·arm-check는
   다음날 아침에 `playbooks_for_day(<다음날>)`로 조회 → **하루 어긋나 전일 밤 승인분을 못 찾는다**
   (6/12 아침 arm-check가 `pb.20260611`을 못 봄, 실증).
2. **다일 셋업 누락**: 3트랜치의 flush(투매일 지정가 매집 50%)는 갭다운 날을 기다리는 셋업 — 며칠
   안 올 수 있다. R5가 매일 새로 도니 "어제 승인·오늘 미발동 → 모레 진입"이 사라진다.
3. **승인 수단 부재**: draft→approved 전이 도구가 없어 승인 자체가 불가능했다.

### 산출
- **`PlaybookStore.active_playbooks(now)`**: 날짜가 아니라 **status=approved + TTL(초안 거래일 +
  time_stop_days 거래일) 미경과**로 조회. 같은 (종목, 방향)은 최신 초안만(매일 R5 재생성 중복 제거).
  → 날짜 어긋남 버그가 구조적으로 사라진다(라벨 비의존).
- **`MarketCalendar.add_trading_days(d, n)`**: TTL 만료일(거래일 단위) 계산.
- **`trading.approve`**: draft→approved CLI(`--list` + id 명시, append-only). 자동 승인 없음(마찰은 의도).
- **arm-check**: `active_playbooks` 조회로 전환. 만료일 표기 + "승인 대기 N건" 힌트.
- **`/approve` 스킬**: 저녁 결재 후 운영자 확인 → 승인.

### TTL 정의 (운영자 결정)
`time_stop_days`를 셋업 유효기간으로 재사용(스키마 추가 없음). 의미: "이 셋업이 유효한 거래일 수" —
그 안에 arm 조건이 안 오면 만료(추격 금지). 진입 후 시간손절과 동일 파라미터를 진입 전 대기 한도로 공용.

### 영향범위
`journal/playbooks.py`(active_playbooks·pending_drafts)·`market_calendar/calendar.py`(add_trading_days)·
`arm_check.py`·`select_playbooks.py`(둘 다 풀 조회로 전환)·`flowsnap.py`(inject_dir 파라미터)·
`approve.py`(신규)·`.claude/skills/approve/`(신규)·arm-check SKILL.md(개정). 저녁보고·R5 무변경.

### SEL-3 동반 해소(2026-06-12)
R5.5 cron(`select_playbooks`)도 `active_playbooks` + `flowsnap.build_snapshot`으로 통일(날짜 라벨·
흐름 소스 일원화, `load_snapshot` 제거). 날짜 어긋남 자동 arm 경로까지 해소.

### 승인을 아침 arm-check에 통합(2026-06-12, 운영자 결정 "아침 통합")
저녁 CLI 강제·id 타이핑이 번거롭다는 피드백 → 승인 단계를 저녁에서 **아침 arm-check로 이관**.
"검토 후 의식적 승인"이라는 본질적 마찰(§6 충동 차단)은 유지하되, 우발적 마찰(타이핑·저녁 CLI)은 제거.
- `PlaybookStore.candidate_playbooks`: 미승인(draft) 후보 풀(TTL 미적용, 만료일은 "승인 시 유효기간" 참고).
- `arm_check`: **승인된 셋업**(활성 풀) + **승인 후보**(미승인) 두 섹션. 후보도 흐름 판단해 "지금 승인 시
  발동" 미리보기. 각 후보에 `approve <id>` 명령 동봉.
- arm-check 스킬: 후보 검토 정보 제시 → 운영자에게 승인 질문(자동 승인 금지) → 승인 → 갱신 판단.
- 저녁 보고: "결정 — 승인 요청" → "내일 검토 후보(아침 arm-check에서 승인)"로 톤 조정.
- 텔레그램 양방향 직접 승인은 채널 발신 전용·openclaw 수신 인프라 부재로 보류(후속).

## 👍 P-8 — 포지션 관리 레이어 (보유 테이블 + 계획 스냅샷 + 정리 점검)

**상태:** 👍채택(운영자 요청 2026-06-12) — 구현 진행.

### 문제
시스템이 "사기 전"(후보→승인→발동)만 관리하고 "산 후"가 비어 있다. 설계서 §8 저녁 결재 보고의
"보유 포지션의 무효화 조건 잔여 거리" 항목이 "KIS 잔고 어댑터 미구현" 결측으로 방치. discuss가
만든 조건문(가설·트리거·무효화·스탑·시계·확신도)도 세션이 끝나면 휘발 — 보유 중 무효화 감시에
재사용되지 않는다.

### 산출
- **`contracts/position.py`**: PositionRecord — 종목·수량·평단 + **계획 스냅샷**(hypothesis·
  trigger_text·invalidation_text·stop_level·time_stop_days·confidence·plan_doc 전문·source_ref)
  + status(open/closed). 분석 문서를 그대로 박제(운영자 요청 "분석 문서 그대로 저장").
- **`journal/positions.py`**: PositionStore(`data/positions.sqlite`, append-only — close=새 version).
- **포지션 점검(순수 코드)**: 현재가(KIS 실시간 → EOD 폴백, as_of 표기) 대비 손익%·스탑 거리%·
  시간손절 잔여 거래일(market_calendar) → **정리 검토 플래그**(스탑 이탈/시간손절 도래).
  자유문 무효화 조건은 코드가 평가하지 않고 표시만 — 스킬(LLM)이 최신 이벤트와 대조 해설,
  **정리 판단은 운영자**(절대금지 #2: 판단=코드/운영자, LLM=해설).
- **`trading.positions` CLI**: add(계획 스냅샷 포함 등록)·list(점검 포함)·close(사유 박제).
  등록은 수동 — KIS 잔고·체결 어댑터는 별도 미해소 항목 유지(잔고 대사는 후속).
- **노출 3곳**: ① arm-check "보유 포지션 점검" 섹션(아침) ② 저녁 보고 포지션 섹션(§8 결측 해소)
  ③ `/positions` 스킬(등록·정리·점검 인터랙티브, boot에서 점검 호출).

### 영향범위
contracts/position.py·journal/positions.py·position_check.py·positions.py(신규),
arm_check.py·reports/render.py(섹션 추가), .claude/skills/positions/(신규)·work-boot/arm-check SKILL 갱신.

### 미해소(유지)
- KIS 잔고·체결 어댑터 — 수동 등록과 실계좌 대사(체결 자동 반영)는 후속.

## 👍 P-9 — 중장기 스윙 스크리너 (2-레이어 점수: 단기 열기 병기 + 스윙 품질 신설) — 1단계 구현 2026-07-11

> **상태:** 1·2단계 구현 완료(2026-07-11).
> 1단계 — `swing.py`(4축 유니버스+기회 트리거, 순수 코드) + `collectors/fins.py`(DART 재무 캐시,
> 240/243) + `daily-eod` 체인 배선 + `SwingStore`. 첫 실행: 게이트 243→유니버스 30, 트리거 11종.
> 2단계 — R6 저녁 결재 보고 "스윙 기회" 섹션(3단 명시성) · R7 `SwingTriggerScore` 채점
> (5거래일 창, 미성숙/결측 미채점 → 임계 튜닝 루프 완성) · flows 수집 = 스크리너 ∪ 스윙 유니버스
> (수급 축 커버리지 해소). 상세: archive `2026-07-11-p9-swing-screener.md` / `2026-07-11-p9-stage2.md`.
> **잔여(3단계 검토):** 트리거 발화분의 R3~R5 분석 승격 배선(현재는 보고 노출+`/discuss` 수동) ·
> MDD 컷·점화 임계 적정성(R7 표본 축적 후).
**제안(운영자 발의 2026-07-11):** 현행 스크리너(거래대금 급증·모멘텀·신고가)는 변동성이 높은
단기 급등주에 점수가 쏠려 상시 관찰이 필요하고 리스크가 크다. 이를 **폐기하지 않고 "단기 열기(heat)"
지표로 보존**하면서, 중장기 스윙·장기 투자 후보를 뽑는 **별도 스윙 점수 레이어**를 신설한다.

### 점수 구조 (모두 순수 코드 — LLM은 기존 R2 산출물 소비만)
- **Layer A — 단기 열기(기존 유지):** 현행 3신호 점수. 컬럼 병기(보조 지표·과열 경고 겸용).
- **Layer B — 스윙 품질(신설), 4축 + 변동성 페널티, 횡단면 백분위 가중합:**
  1. **추세 품질** (`market.sqlite`): 변동성 조정 모멘텀(60/120일 수익률 ÷ 일간수익률 표준편차,
     Sharpe류) + 최대낙폭(MDD) 컷 + 이동평균 정배열/신고가 *유지일수*. 스파이크형 급등이 아니라
     **완만하고 꾸준한 추세**에 점수 — "변동성 높아 위험" 직접 대응.
  2. **도메인 열기** (섹터 단위 집계): ①시세 기반(순수 코드) — 섹터 거래대금 점유율 변화·상승
     breadth(섹터 내 상승 종목 비율) ②뉴스 기반 — **R2 EventStore**(`catalyst_strength`·
     `event_affected`)를 섹터로 집계. 뉴스→도메인 판단은 이미 LLM 허용 라운드(R2)의 산출물이므로
     스크리너는 DB 행 소비만(절대금지 #2 정합). ①은 뉴스 공백 구간(6/15~7/10)에도 작동.
  3. **펀더멘털** (DART `financials()` — 승인 소스): 매출·영업이익 YoY/QoQ 성장, 영업이익률 추이,
     부채비율 게이트. `data/fins.sqlite` 캐시 신설(분기 단위 — `as_of`=보고서 접수일 명시,
     미취득 종목은 **결측 표기**(0 폴백 침묵 금지 — 기존 `mom_*_ok` 패턴 재사용)).
  4. **수급 지속성** (`flows.sqlite`): 외인+기관 순매수 지속일수·누적 규모(개인 단독 랠리 감점 —
     6/12 신세계 사례). ⚠️ 현 커버리지가 "당일 후보 위주"라 후보 선정에 쓰려면 확대 필요(미결).

### 운영 형태 — "관심 유니버스는 느리게, 기회 포착은 매일" (운영자 정정 2026-07-11: 주 1회 아님)
스윙은 정기 리스트 갱신이 아니라 **기회가 왔을 때 진입**하는 것 — 따라서 2단 구조:
- **① 스윙 품질 유니버스(천천히 변함):** Layer B 4축으로 "펀더멘털·추세·수급이 받쳐주는 종목" 풀 유지.
  매일 EOD 체인에서 재계산하되(재무 축은 `fins.sqlite` 캐시 — 분기 공시 때만 갱신이라 일일 비용 미미)
  구성은 일 단위로 크게 안 뒤집힘. 이것은 "살 종목"이 아니라 **"기회가 오면 살 자격이 있는 종목"**.
- **② 기회 트리거(매일, EOD 체인 내):** 유니버스 종목에서 **진입 기회 신호**가 발화하는 날 후보로 승격:
  - 눌림목 — 추세 유지 중 단기 조정 후 지지(예: 20/60일선 접근 + 거래량 수축→회복). 6/12 히스토리
    "전종목 추격 금지, 3종 눌림 트리거 대기" 선례의 체계화.
  - 도메인 점화 — 소속 섹터의 열기 축(거래대금 점유율·breadth·R2 촉매)이 임계 돌파.
  - 종목 촉매 — R2 이벤트(`event_affected`)가 해당 종목에 강도 높은 촉매 부착.
  - 수급 전환 — 외인/기관 순매수 전환·가속.
- **승격된 후보만 그날 저녁 R3~R5 분석 대상** → 저녁 결재 보고에 "스윙 기회" 섹션 → 아침 승인·arm-check.
  기존 일일 사이클(daily-eod→score→verify→reason→synth→report)에 얹는 것 — **신규 cron 슬롯 불필요**.
- R7 주간 평가에 트리거 적중률 채점 축 추가 → 트리거 임계·가중치를 실측으로 튜닝.

### 전제 (critical path)
**섹터 커버리지 선행 필수** — 현재 상위 30 중 22종 미분류. 이 상태로는 도메인 축(2)이 작동하지 않음.
→ **P-1(taxonomy 버킷 확장) + P-2(미분류 LLM 폴백 분류기) 채택·구현이 선행 조건.**

### 영향범위
`screener.py`(Layer A 점수 유지) + `swing_screener.py`(유니버스+트리거, 신설) · `collectors/dart.py`
재무 수집 확장 + `fins.sqlite` · 섹터 집계 모듈 · daily-eod 체인 배선(신규 cron 슬롯 없음) ·
R6 보고 "스윙 기회" 섹션 · R7 채점 축.

### 미결
- 4축 가중치 초기값(균등 시작 → R7 실측으로 조정?) · MDD/부채비율 컷 임계치.
- 기회 트리거 정의·임계(눌림 지지 판정, 도메인 점화 돌파선, 수급 전환 기준) — 초기값 보수적으로 잡고 R7로 조정.
- 수급 커버리지 확대 범위 — 게이트 통과 전 종목(243)이면 KIS 호출량 부담, 유니버스 종목만이면 절충.
- 유니버스 진입·이탈 조건(추세 품질 하락 vs 절대 손절선) — 트리거 발화 전 종목의 관리 규칙.
- 섹터 열기 집계 방식(시총가중 vs 동일가중), 미분류 잔존분 처리(집계 제외 명시).
