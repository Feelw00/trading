# 디스커버리 파이프라인 + 데이터 소스 + boot/수집 스킬

- **완료(KST):** 2026-06-08
- **커밋 범위:** `d56819e` → `a98835a` (9커밋)
- **검증:** mypy 41 files clean · pytest 48 passed

## 목적
"전종목에서 신호로 후보를 발굴 → 현실 데이터로 grounded 분석"의 토대 구축.
손으로 고른 대장주 감시(반쪽)가 아니라, 전 시장 스크리닝 + 섹터 분류 + 공시/재무 grounding.

## 한 일 (계층별)

### 데이터 소스 어댑터 (`src/trading/collectors/`, 결정론·하네스)
- **base**: HTTP 재시도·백오프(opener 주입), KST, `CollectedFact`, SQLite append-only `write_facts`, `fetch_bytes`(ZIP).
- **macro**(FRED·ECOS·공공데이터): 거시 11항목. 시리즈ID·통계코드·필드 전부 **카탈로그/실호출로 확정**(추측 0).
  - FRED: SP500·NASDAQCOM·NASDAQSOX·DCOILWTICO·DCOILBRENTEU
  - ECOS: 환율 731Y001/0000001(매매기준율) · 시장금리 817Y002(국고채 3Y 010200000·10Y 010210000) · 기준금리 722Y001/0101000
  - 공공데이터 지수: getStockMarketIndex(코스피·코스닥)
- **data_go_kr**: 지수(`DataGoKrIndexClient`) + 종목(`DataGoKrStockClient`: latest + `all_by_date` 전종목 1콜). 엔드포인트 `GetStockSecuritiesInfoService/getStockPriceInfo`, 필드·idxNm·날짜창 실호출 확정.
- **market**: 전종목 EOD DB. `MarketStore`(SQLite `data/market.sqlite`, gitignored) — daily_quotes(append-only/IGNORE), stock_sectors, latest_date/nth_recent_date/rows_since/upsert_sectors/sector_map. **1년 백필 708,913행**.
- **dart**: DART 공시·재무. corp_code 매핑(상장사 3,968)+disclosures(list)+financials(fnlttSinglAcnt). status 처리(000/013/오류). 무료·공개(설계 🟢).

### 분석 계층
- **screener**(순수코드, R5.5 성격): 거래대금 급증 + 모멘텀(20/60일) + 신고가 근접 → 횡단면 백분위 랭크 가중합. 유동성·보통주 게이트. ScreenConfig 튜닝. **2,877 → 게이트 311 → 후보**. 반도체장비 테마 자연 부각.
- **멀티에이전트 섹터 분류**(Workflow): 게이트 311 → 26섹터 다중소속 태깅(10에이전트, 저신뢰 재검증). 293 분류/18 미분류(추측 안 함, 환각 가드 작동). `stock_sectors` 적재 → 스크리너 출력에 섹터 태그.

### 스킬/커맨드 (`.claude/`)
- `/boot` = collect-macro(거시 라이브) + work-boot(DB·history 읽기) + **콘솔 날짜 확인 + 미수집 알림·`/collect` 제안**.
- `/collect` = 전종목 EOD 신규 수집 + 스크리너 갱신.
- 분리: collect-macro(거시)/collect(전종목)/collect-disclosure(공시·DART)/collect-news(뉴스·소스 미확정 스텁).

## 핵심 결정
- **R0 = LLM 수집(rule #2 override) + 하네스**(OPEN_QUESTIONS COLLECT-1/2/3): LLM은 승인 소스 어댑터만, 독자 웹서치 금지(커맨드 allowed-tools에서 제거), 실패=blocked. → 단, 정형 수치는 결정론 어댑터가 수집(LLM은 트리거).
- **방향 전환:** 고정 대장주 universe 폐기 → 전종목 스크리닝 + 섹터=분류렌즈.
- **전망은 현실 데이터 grounding 후**: LLM 기억 추론 금지 → 공시·재무(DART)·가격(DB) 읽고 판단.

## 환경 (키 .env, gitignored)
- 발급·검증됨: `FRED_API_KEY` · `ECOS_API_KEY` · `DATA_GO_KR_API_KEY`(주식시세 15094808 활용신청 승인) · `DART_API_KEY`.
- 막힘: KIS(앱 오류 → 호가·수급·실시간 보류) · 토스 Open API(사전신청 단계) · 뉴스 소스 미확정.

## 다음 (resume 지점)
1. 후보 fact pack(공시+재무+가격) → grounded 분석 에이전트(가설+무효화 ThesisRecord).
2. `trading.run` ROUNDS 채우기 + openclaw cron(하루 2회).
3. 스크리너 튜닝 / 분류 committed export / 일일 diff / 뉴스 소스.
