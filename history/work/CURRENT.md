# 현재 작업 (CURRENT)

> 슬림 유지. 완료된 작업은 `archive/YYYY-MM-DD-<slug>.md`로 떨구고 여기엔 한 줄 + 링크만.
> 최종 갱신: 2026-06-09 (KST)

## 진행 중
- (없음)

## 최근 완료
- 2026-06-09 — **뉴스 단일 영속 DB + 인덱싱(P-3) + 부트 뉴스 신선도**: 뉴스 landing을 날짜분산(`.runtime/collect/<날짜>/news.sqlite`) → **단일 `data/news.sqlite`**(시세 DB 동격)로 통합. factpack `_latest_news_db()`(최신 1개 DB만 봐 **시계열 단절** — 어제 촉매가 오늘 안 보임) 제거 → `_open_news_store()`. `news_entities(news_id,entity,entity_type)` 조인 정규화 + 인덱스(entity·published_at·title_norm) → `entities LIKE` 풀스캔·부분일치 오탐 제거. 전역 dedup: URL=PK `INSERT OR IGNORE`, 제목=`title_norm` **크로스-런 병합**(기사 1건 유지, entities만 머지). 부트 스킬(`work-boot`·`boot`)에 **1b 뉴스 신선도**: 오늘자 `data/news.sqlite` 확인 → 미수집 시 ⚠️ + `/collect-news`(키 없으면 키 안내, COLLECT-3 흉내 금지). 기존 0건이라 마이그레이션 불필요. pytest 100(+1 크로스런dedup)·mypy strict clean. PROPOSALS **P-3 채택** 등록. **네이버 키 발급·라이브 검증 완료**(2026-06-09 30건 적재·verified 30/30·fact pack 8건 합류 확인, 연합뉴스 trust 0.95). SearXNG(해외)는 미설정(선택). **후속 튜닝:** 네이버 `sort=date` → 종목 검색에 시장 일반뉴스 혼입(노이즈) → `sim`/혼합 검토.
- 2026-06-09 — **스크리너 튜닝(#4)**: **액면병합/분할 아티팩트 가드**(실증된 크레오에스지 1251% 문제 해결). 탐지법을 실데이터로 검증 — 인접 거래일 상장주식수(lstg_st_cnt) 비율>1.5면 가격 시리즈 불연속 → 제외(분할·병합·무상증자만, 점진 희석은 통과). 6/8 307→293종목(14 제외, 전부 1.5~5배 실자본변동), 크레오에스지 제외·#1=SK네트웍스(실모멘텀). `rows_since`/`series_for`에 lstg_st_cnt 추가. 하락장 절대필터 knob 추가(`min_mom_long`·`min_high_proximity`, 기본 off — 전략선택). pytest 101(+3)·mypy clean. **관리종목 제외는 소스 없음**(data.go.kr 미제공 → 🔴 KRX 필요). 가중치 튜닝은 백테스트(리플레이) 후.
- 2026-06-09 — **파이프라인 디스패치(#3)**: `trading.run` 빈 `ROUNDS` 채움 — 라운드명→결정론 핸들러(collect-macro/collect-market/collect-news/classify-sectors/screen/factpack + 합성 **daily-eod**=시세→분류→스크리너→fact pack). lazy import·`--list`·첫 실패 중단. `ops/openclaw/cron_jobs.py`(선언적 cron 매니페스트, KST 슬롯 5개) + `sync.py`(매니페스트→openclaw 명령, **dry-run 기본**·round 정합성 검증). **openclaw CLI 구문은 미검증→설치본에서 확정**(절대금지 #1, sync는 출력만). pytest 98(+5)·mypy clean. `trading.run screen` end-to-end 확인. 휴장일은 cron(월~금) 아닌 잡 내부 가드(data.go.kr 빈 결과).
- 2026-06-09 — **뉴스 → fact pack 연동**(뉴스 가치사슬 완성): 수집된 뉴스를 종목별로 FactPack에 합류 → `discuss`·fact pack이 grounded 촉매로 뉴스를 본다(원 목표 "정확한 가설 도출"의 빠진 고리). `NewsStore.recent_for(entities)`(발행 최신순) + `FactPack.news` 필드 + `factpack._latest_news_db()`(최신 news.sqlite 자동 탐색). `build_fact_pack(..., news_store)` 옵션(하위호환). discuss 스킬은 `--ticker` 출력의 `news`(발행처·trust·published_at) 사용. 키 불필요(라이브는 Naver/SearXNG 주면 즉시 채워짐). pytest 93(+3)·mypy clean. end-to-end 검증(삼성전자 뉴스 합류).
- 2026-06-09 — **종목 분석·토론 스킬(`discuss`)** + 단일종목 grounding: 임의 종목(코드/이름) grounded 분석·토론 — 설계서 §6 철학(적대적·무효화 필수·예측 아님, 반-아첨). `factpack.build_fact_pack_for(ident)` + `python -m trading.factpack --ticker <코드|이름>`(스크리너 게이트 외도 조회). `screener.signals_from_series` 추출(게이트 무관 신호, `_survivor` 재사용), `MarketStore.series_for`·`find_by_name`. 스킬: grounding(factpack+거시+뉴스 DB)→소문 `[UNVERIFIED]` 분리→적대분석→조건문(가설/트리거/무효화/시계/확신도). 시장가 금지·수급 미수집은 "없음". pytest 90(+3)·mypy clean. **수급(외인·기관) 데이터는 🔴 소스 미결정으로 보류 — 스킬상 "판단 보류" 표기.**
- 2026-06-09 — **뉴스 수집 독립 커맨드 분리**: 러너(`build_sources_from_env`+오케스트레이션)를 코어에서 빼 `src/trading/collect_news.py`로 독립 → `python -m trading.collect_news [top_n]`(factpack 패턴). `collectors/news.py`는 순수 라이브러리화(정규화·dedup·landing·라우터만). `run()` 분리로 추후 `trading.run` ROUNDS 핸들러가 import만 하면 됨(#3 디스패치 대비). `collect-news` 스킬 명령어 갱신. pytest 87(+5, env→소스 구성)·mypy clean.
- 2026-06-09 — **SearXNG 뉴스 어댑터**(해외 분담): `collectors/news_searxng.py`(JSON API — results 파싱·ISO8601→aware[naive=None]·발행처 도메인 추정). 발행처 추정(`publisher_from_url`+도메인맵)을 코어로 올려 두 어댑터 공유(국내+해외 도메인). `build_sources_from_env`에 SEARXNG_URL 배선. pytest 82(+4)·mypy clean. **라이브 검증은 인스턴스(`format=json` 활성) 확보 후.**
- 2026-06-09 — **네이버 뉴스 어댑터** + 러너 배선: `collectors/news_naver.py`(네이버 검색 API — RawNews 파싱·발행처 도메인 추정·pubDate RFC822→KST). `base.fetch_json`에 헤더 인증 지원 추가(기존 호출 무영향). 러너 `python -m trading.collectors.news [top_n]`(env→소스구성·스크리너 후보→쿼리플랜·landing) + `collect-news` 스킬 배선. 키 없이 graceful blocked(날조 없음) 확인. pytest 78(+6)·mypy clean. **라이브 검증·SearXNG 어댑터는 키/인스턴스 확보 후.**
- 2026-06-09 — **뉴스 수집 코어**(백엔드-무관) + **COLLECT-4 결정**: 운영자 결정으로 **뉴스 한정 웹서치 허용**(COLLECT-3 부분 개정, OPEN_QUESTIONS COLLECT-4) — 구조차단을 출처가드로 대체(URL·발행처·published_at KST·source 필수, dedup, 저신뢰=UNVERIFIED, 검색실패=blocked). 하이브리드 소스 분담: **국내 후보명→네이버 / 해외 매크로·테마→SearXNG**(신뢰 소스가 핵심부하·불안정 소스는 보조 → graceful degradation). 코어: `contracts/news.py`(NewsItem) + `collectors/news.py`(정규화·html strip·KST변환·**교차소스 dedup**[URL+제목]·trust랭킹·NewsStore landing·NewsSource Protocol·도메인 라우터). pytest 72(+9)·mypy clean. **어댑터(네이버·SearXNG)는 키/인스턴스 확보 후** 라이브검증하며 구현(주입 fetch+픽스처 패턴).
- 2026-06-09 — **후보 fact pack**(next-work #1): 후보별 grounded 입력 슬라이스(R3 입력) 결정론 조립 — `contracts/factpack.py`(FactPack/PriceContext/DisclosureItem/FinancialLine) + `src/trading/factpack.py`. 가격맥락(DB) + 공시(DART list.json, 90일·15건) + 재무(DART 주요계정, 연결우선·당기/전기 YoY, 올해/작년 기간 폴백). 결측은 `notes`에만(추측 금지). 출력 `.runtime/factpack/<거래일>/<srtn>_<name>.json`. 실행 `python -m trading.factpack [top_n]`. pytest 63(+6)·mypy clean. **실검증**: 대원제약 영업이익 YoY -53%·순익 -61%(실적악화 포착) / **크레오에스지 1251%는 액면병합 아티팩트**(20260511 변경상장+거래소 조회공시요구로 즉시 판명) → 스크리너 튜닝 #4 입력.
- 2026-06-09 — **혼재 KSIC 잔존분 보강 + KSIC 천장 finding**: 미분류 31종 induty_code를 293라벨 대비 5/4/3자리 순도로 실측 → **결정론 확장 데이터 미지지**(거의 n부족·혼재) 확인. 대응: ① 고확신 유명주 12종 큐레이션 오버라이드(`manual-curated-v1`, 환각가드: 확실한 것만 — 롯데케미칼·JYP·루닛·코오롱·하림지주·iM금융지주·앱클론·이노스페이스·풍산 등) ② taxonomy 버킷 부재분(해운 HMM·흥아해운·운송 동양고속·레저 강원랜드)은 `docs/PROPOSALS.md` P-1 등록(추측 안 함) ③ LLM 폴백 분류기 P-2 제안. 커버리지 게이트 **90%→93.8%(288/307)**. pytest 57·mypy clean.
- 2026-06-09 — **섹터 태깅 grounded 영속화**(next-work #5 부분, PR#1 머지 `79e8d95`): DART 회사개황(KSIC 업종) → 26 taxonomy **결정론** 분류(`src/trading/sectors.py`). 기존 293 LLM 라벨 대비 induty_code **순도 실측** → 깨끗한 코드(≥0.75)만 채택, 혼재(264 등)는 미분류 유지(추측 안 함). 소스 `dart-ksic-v1`(큐레이션·LLM 우선·갭만 채움, 스크리너 `sector_map_multi` 병합). `/collect`에 매 거래일 자동 보강 스텝 배선. 6/8 게이트 43미태깅→12 grounded 분류. LLM 미개입.
- 2026-06-08 — **디스커버리 파이프라인 + 데이터소스 + boot/수집 스킬** → [archive](archive/2026-06-08-discovery-pipeline.md)
  - 수집(거시 FRED·ECOS·공공데이터 / 전종목 EOD 708k행 / 공시 DART) → 스크리너(거래대금+모멘텀+신고가) → 멀티에이전트 26섹터 분류 → 섹터 태그 후보. mypy 41 clean·pytest 48. 커밋 `d56819e`→`a98835a`.
- 2026-06-08 — **M1 골격·데이터 계약 5종·journal·리플레이** → [archive](archive/2026-06-08-m1-skeleton.md)

## 빠른 재개 (다음 세션 `/boot` 후)
- **상태:** 디스커버리 파이프라인 작동(전종목→후보→섹터). 키 발급 완료(FRED/ECOS/DATA_GO_KR/DART, **네이버뉴스**). KIS·SearXNG 보류.
- **DB:** `data/market.sqlite`(gitignored) 1년치(247거래일·711k행). 최신 수집일 = 2026-06-08. 새 거래일은 `/collect`로 증분(시세+섹터보강+스크리너).
- **바로 보기:** `/boot` → 거시 백드롭 + 신선도 + 오늘 후보(섹터). 또는 `python -m trading.screener`.

## 다음 후보 (전종목 스크리닝 → grounded 전망)
1. ~~후보 fact pack~~(✅ `trading.factpack` — 가격+공시+재무 결정론 조립, JSON). 후속: `trading.run`/스킬 배선·R1 신선도 게이트 연동.
2. **grounded 분석 에이전트** — fact pack을 *읽고* 가설+무효화(ThesisRecord) 도출. 멀티에이전트 병렬. **공시에 없는 촉매 지어내기 금지**. (입력 슬라이스 = #1 산출 JSON)
3. **파이프라인 디스패치** — `trading.run` ROUNDS 채우기(collect-macro/collect-market/screen/factpack/daily) + openclaw cron(하루 2회).
4. ~~스크리너 튜닝~~(✅ 아티팩트 가드 + 하락장 knob). 남은 것: **관리종목/거래정지 제외**(소스 필요 — 🔴 KRX) · 가중치·임계치 튜닝(리플레이 백테스트 선행).
5. ~~분류 영속화~~(✅ grounded `dart-ksic-v1` + 큐레이션 `manual-curated-v1`, 93.8%). 남은 19 미분류는 **taxonomy 갭(PROPOSALS P-1)** + 진짜 모호(전자부품·다각화). 후속: P-1 taxonomy 확장 합의 / P-2 LLM 폴백 분류기 / 일일 diff(신규상장) / 뉴스 소스.
- 보류: KIS(호가·수급·실시간) / NXT(🔴) / M2 R1 게이트.
