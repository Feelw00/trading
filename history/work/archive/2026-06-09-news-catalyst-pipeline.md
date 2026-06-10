# P-4 뉴스 촉매 파이프라인 + 보조 슬라이스

기간: 2026-06-09 (KST) · 하루
범위: 디스커버리 파이프라인을 R2~R4 LLM 라운드로 확장(P-4) + 디스패치/스크리너/discuss/뉴스 단일 DB 등 보조 슬라이스.
머지: PR #12·#14·#15 main 머지 (`b522d2e`, `d0b9a37`, `6749684`, ...).

## P-4 뉴스 촉매 파이프라인 (메인 트랙)

**P-4.1 계약·taxonomy + P-4.3 3계층 쿼리플랜**
- `domains.CatalystType` 11종 (earnings·guidance·policy_regulation·ma_restructure·supply_chain·flow_demand·macro·product_tech·legal·management·rumor_unconfirmed — 섹터축과 직교)
- `EventRecord` 촉매필드 확장: `catalyst_type`·`scope`(Scope: single_stock/sector_theme/broad_market)·`catalyst_strength`·`novelty`·`affected`(srtn_cd+relevance). 전부 옵셔널(비-촉매 이벤트 하위호환). **방향·확신은 종목·페르소나별이라 EventRecord 제외**(R3 ThesisRecord 몫)
- `build_query_plan(candidates, sectors, themes)` 3계층: L1 종목 + L2 26 Sector 라벨(`_sector_query`: 괄호제거+1차키워드 결정론화) + L3 거시. **L2 활성 서브테마 보류 → OPEN_QUESTIONS NEWS-L2**(큐레이션 출처 없으면 임의확장=환각)
- 라이브 검증: L1=15·L2=26·L3=6, 295건 적재(섹터링크 260)

**P-4.2 R1 뉴스 신선도·정합성 게이트** (순수 코드, LLM 금지)
- `gates/news.py` — R0 적재 `NewsItem`에 플래그 부착(폐기 안 함). 플래그: `stale`(신선도 지평 초과)·`undated`·`future_dated`·`low_trust`(trust<임계, COLLECT-4 UNVERIFIED)
- `NewsVerdict.fresh`(R5 주문초안 하드게이트)와 `usable`(무결) 구분. `GateConfig` 임계 전부 knob
- 라이브 325건: usable=fresh=314·stale=11(06-01~06-04 발행, DB 일치)
- 설계서 R1 이중-소스 `conflict`(환율·지수 임계괴리)는 **FactRecord 게이트** — 뉴스 비적용 명시

**R2 촉매 스코어러 코어** (단일 호출·`claude -p`, NEWS-R2 결정)
- `llm.py` `LLMClient` 추상화 + `ClaudeCliClient`(실측 봉투 `.result`/`is_error`/`subtype`, **runner 주입**으로 테스트는 프로세스 없이). 모델 `.env` 주입(`R2_MODEL`→`CLAUDE_MODEL`). `extract_json`(코드펜스 관대)
- `rounds/r2.py` — scope 레이어별 배치(`build_batches`: 종목>섹터>테마 1키·최신순) → **배치당 1호출**(`build_prompt`: 기사목록+universe+스키마+환각가드) → `EventRecord`. 관대 처리: `event_type` 5종 enum 미끄러지면 catalyst_type 맵으로 보정, 부적합 catalyst_type/scope/score→None, **summary 누락만 폐기**
- **환각가드**: affected=universe srtn_cd만·evidence=배치 기사 id만 (ghost 탈락)
- 라이브 검증(haiku, 4배치): 7이벤트·0폐기·0에러. **젠슨황→네이버(035420) relevance 0.85 정확 포착** (P-4 핵심 동기: 종목명 검색에 약한 촉매를 L2 섹터뉴스→R2가 연결). 노이즈(충북 화학사고)는 affected=[]로 격리
- 다음: R3 grounding으로 흐름. **R1 게이트·R5.5에 LLM 점수 주입 금지** (CLAUDE.md 절대금지 #2)

**R2 영속화 + run 배선**
- `journal/events.py` `EventStore` — 단일 `data/events.sqlite`(시세·뉴스 DB 동격), **append-only**(재스코어링=같은 id 새 version). 핵심컬럼 + JSON `payload` 무손실 + `event_affected` 정규화 조인 → `recent(limit)`·`for_srtn(srtn_cd)`
- `score_news.py` 러너(스크리너 universe + 최근 뉴스 → gate_news(R1) → run_r2 → EventStore.append) + `trading.run score-news` 라운드

**R4 적대검증** (선별·perspective-diverse, claude -p)
- `Verification`/`LensVerdict` EventRecord 옵셔널 필드 + `rounds/r4.py`
- `select_events` — **전수 검증 금지**(P-4 §4): 미검증·고강도(single_stock≥0.5 OR scope무관≥0.7)만 강도내림차순·max_events 상한
- 이벤트당 **3 lens perspective-diverse**(strength/linkage/timing) 적대 호출 → 다수결(≥2 생존)=`confirmed`. 호출 실패=refute(survived=False, 적대 기본 회의적)
- `verification` 부착한 새 version 으로 EventStore append (R2 v1→R4 v2 감사추적)

**R3 페르소나 분석** (촉매→ThesisRecord, claude -p ×3 입력 격리)
- `rounds/r3.py` — 촉매 보유 종목 1개당 3페르소나(수급/사이클/매크로) 입력 격리 병렬
- 격리는 `_CATALYST_PERSONA`로 분배: 수급→flow_demand·rumor / 사이클→실적·가이던스·공급망·제품·M&A·경영 / 매크로→거시·정책·법률. 페르소나별 데이터: 사이클=재무 YoY, 매크로=거시 백드롭
- `ThesisRecord` 생성 — **invalidation 필수**(누락 시 strict 프롬프트로 1회 재생성, 재실패 폐기). horizon/confidence/direction 범위가드
- **미수집 핵심지표(투자자별 매매·신용·공매도·DRAM가격·캐펙스 🔴) → "보류"+저confidence** (추측 금지)
- `journal/theses.py` `ThesisStore`(append-only·version·srtn_cd/persona 조회) + `reason_news.py` 러너 + `trading.run reason-theses`
- 라이브 검증(haiku, 2종목): 논제 3 적재. **수급 페르소나가 정확히 "보류"**(현대백화점 flat·conf 0.15, 무효화에 "수급데이터 공개 시 재검증"). 사이클이 재무 grounded short(오프라인 -14%·부채+13% / 매출+6% vs 영업이익+102%=마진 이미 실현, 관측가능 무효화)
- 튜닝메모: haiku 페르소나 호출 가끔 JSON 파싱 실패 → 재시도/모델 상향 검토

**P-4 cron 슬롯 배선** (파이프라인 자동화)
- `ops/openclaw/cron_jobs.py` — am `collect-news`(06:20)→`score-news`(06:30)→`verify-catalysts`(06:45)→`reason-theses`(06:55), pm(16:20/32/45/55), 총 9 잡
- **R2/R4 LLM 라운드도 `mode=exec`** — openclaw는 `python -m trading.run` 만 exec하고 **claude -p는 Python 두뇌가 내부 직접 호출**(NEWS-R2/SCHED-3, provider 라우팅 미사용)
- 운영 메모: cron 환경에 `R2_MODEL`(저단가) 주입 필요. score 장기실행 시 verify와 시각겹침 가능

## 보조 슬라이스 (P-4 트랙 외 — 같은 날 진행)

**섹터 태깅 grounded 영속화** (PR #1 머지 `79e8d95`)
- DART KSIC → 26 taxonomy 결정론 분류 (`src/trading/sectors.py`). induty_code 순도 ≥0.75만 채택, 혼재(264 등)는 미분류 유지. 소스 `dart-ksic-v1` (큐레이션·LLM 우선)
- 게이트 43미태깅→12 grounded 분류

**혼재 KSIC 잔존분 보강 + KSIC 천장 finding**
- 미분류 31종 induty_code 5/4/3자리 순도 실측 → 결정론 확장 불가 확인. 대응:
  1. 고확신 유명주 12종 큐레이션 오버라이드(`manual-curated-v1`, 환각가드)
  2. taxonomy 버킷 부재분(해운 HMM·운송 동양고속·레저 강원랜드) → PROPOSALS P-1 등록
  3. LLM 폴백 분류기 → PROPOSALS P-2 제안
- 커버리지 90%→93.8%(288/307)

**후보 fact pack** (R3 입력 슬라이스 결정론 조립)
- `contracts/factpack.py` + `src/trading/factpack.py` — 가격맥락(DB) + 공시(DART list.json 90일·15건) + 재무(DART 주요계정, 연결우선·당기/전기 YoY). 결측은 `notes`에만(추측 금지)
- 실증: 대원제약 영업이익 YoY -53% (실적악화 포착) / 크레오에스지 1251% → 액면병합 아티팩트(20260511 변경상장)로 즉시 판명 → 스크리너 튜닝 #4 입력

**뉴스 수집 코어** + COLLECT-4 결정
- 운영자 결정: 뉴스 한정 웹서치 허용(COLLECT-3 부분 개정, OPEN_QUESTIONS COLLECT-4) — 구조차단을 출처가드로 대체(URL·발행처·published_at KST·source 필수, dedup, 저신뢰=UNVERIFIED, 검색실패=blocked)
- 하이브리드 소스 분담: **국내 후보명→네이버 / 해외 매크로·테마→SearXNG**
- 코어 `collectors/news.py` (백엔드-무관, **교차소스 dedup** URL+제목), 어댑터 `news_naver.py`/`news_searxng.py`
- 라이브: 30건·verified 30/30·fact pack 8건 합류. 네이버 sort=date 노이즈는 R2가 거름(설계상)

**뉴스 단일 영속 DB + 인덱싱(P-3) + 부트 뉴스 신선도**
- 날짜분산 `.runtime/collect/<날짜>/news.sqlite` → **단일 `data/news.sqlite`** (시세 DB 동격). `_latest_news_db()`(시계열 단절) 제거
- `news_entities` 조인 정규화 + 인덱스 → `entities LIKE` 풀스캔 제거. 전역 dedup: URL=PK INSERT OR IGNORE, 제목=`title_norm` 크로스-런 병합
- 부트 스킬(`work-boot`·`boot`)에 1b 뉴스 신선도 추가

**파이프라인 디스패치 (#3)**
- `trading.run` ROUNDS 채움 — 라운드명→결정론 핸들러 (collect-macro/market/news/sectors/screen/factpack + 합성 `daily-eod`). lazy import·`--list`·첫 실패 중단
- `ops/openclaw/cron_jobs.py` 매니페스트 + `sync.py` (dry-run 기본). openclaw CLI 구문 미검증→설치본 확정 보류

**스크리너 튜닝 (#4)**
- 액면병합/분할 아티팩트 가드 — 인접 거래일 상장주식수 비율>1.5면 가격 시리즈 불연속 → 제외. 6/8 307→293 (크레오에스지 실증 해결)
- 하락장 절대필터 knob (`min_mom_long`·`min_high_proximity`, 기본 off)
- 관리종목 제외는 소스 부재 → 🔴 KRX 필요

**뉴스 → fact pack 연동**
- `NewsStore.recent_for(entities)` (발행 최신순) + `FactPack.news` + `factpack._latest_news_db()`. `build_fact_pack(..., news_store)` 옵션
- discuss 스킬은 `--ticker` 출력의 `news` 사용

**종목 분석·토론 스킬 (`discuss`)** + 단일종목 grounding
- 임의 종목(코드/이름) grounded 분석·토론 — 설계서 §6 철학(적대적·무효화 필수·예측 아님, 반-아첨)
- `factpack.build_fact_pack_for(ident)` + `python -m trading.factpack --ticker <코드|이름>`
- 스킬: grounding(factpack+거시+뉴스 DB)→소문 `[UNVERIFIED]` 분리→적대분석→조건문(가설/트리거/무효화/시계/확신도). 시장가 금지. 수급 미수집은 "판단 보류"

## 검증·메트릭
- 마지막 검증 시점 기준: pytest 161 (M1 19 → 161 누적), mypy strict clean (49 파일). 라이브 검증 모든 슬라이스 단계 통과.
- DB: `data/market.sqlite`(1년치·247거래일·711k행), `data/news.sqlite`, `data/events.sqlite`, `data/theses.sqlite`.
- 키 발급: FRED/ECOS/DATA_GO_KR/DART/네이버 — 라이브 검증 완료. SearXNG·KIS 보류(다음 세션에서 KIS 발급).

## 미해결 / 다음 (당시 시점)
- P-3 FTS5 인덱싱 확장
- R3 grounded factpack 흐름 (R3 페르소나가 EventStore.for_srtn 소비) — 머지됨
- R5 합성 (생존 논제→플레이북·OrderDraft)
- R4 thesis-kill (설계서 R4, 촉매검증과 별개)
- 가중치·임계치 튜닝 (리플레이 백테스트 선행)
- KIS·NXT·KRX 외부 의존 🔴

(R3 페르소나·R0~R4 cron 배선은 6/10 세션에서 GitOps 부트스트랩과 함께 검증 종료 → `2026-06-10-m2-bootstrap-validation.md` 참조)
