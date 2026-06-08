# 현재 작업 (CURRENT)

> 슬림 유지. 완료된 작업은 `archive/YYYY-MM-DD-<slug>.md`로 떨구고 여기엔 한 줄 + 링크만.
> 최종 갱신: 2026-06-08 (KST)

## 진행 중
- **작업 부팅 + 수집 트리거 구축** — 완료분:
  - `history/` 스캐폴드(work/trading 분리, CURRENT·INDEX·템플릿).
  - 수집: `/collect`(섹터 9 + 뉴스 2 = 11) + `collect` 스킬, `collect-macro` 독립 스킬(거시지표).
  - 부팅: `/boot` 커맨드 + `work-boot` 스킬. 부팅 시 `collect-macro` 먼저 호출(시장 백드롭) → 컨텍스트 로드.
  - 소스 확정(COLLECT-2): 금리·환율=ECOS, 국내지수=공공데이터/KRX, 해외지수·유가=FRED, 국내종목=KIS(+MCP). 문서·.env 등록 완료.
  - **하네스(COLLECT-3) 적용:** 수집 커맨드 `allowed-tools`에서 웹서치 제거 → 승인 소스만. `/boot` 첫 실행은 웹서치였고(오차 큼), 이제 그 경로 차단됨.
  - **거시지표 수집 완료:** `src/trading/collectors`에 FRED·ECOS·공공데이터(data.go.kr)·공통(HTTP 재시도·SQLite landing). `python -m trading.collectors.macro` 결정론 실행 → collect-macro 스킬이 트리거(LLM 데이터 미개입). 코드·필드는 카탈로그/실호출로 확정(추측 0).
  - **검증:** mypy 36 clean · pytest 39 passed. **거시 11건 실적재 전부 verified** — FRED(SOX·S&P·NASDAQ·WTI·Brent) / ECOS(USD/KRW 1543·기준금리 2.5%·국고채 3Y 3.882%·10Y 4.254%) / 공공데이터(KOSPI 8160.59·KOSDAQ 1002.44, EOD +1영업일).
  - **국내 종목 시세(EOD):** KIS 앱 오류로 막혀 **공공데이터 주식시세(getStockPriceInfo)로 우회** — `DataGoKrStockClient`. 호가·수급·실시간은 KIS/토스 보류.
  - **전종목 EOD DB (방향 전환):** 고정 대장주 universe **폐기** → **전 상장종목(2,877/일) 1콜 수집 → SQLite(`data/market.sqlite`, gitignored)**. `trading.collectors.market`(MarketStore, append-only/IGNORE). **1년 백필 완료(246일·708,913행)**.
  - **스크리너 v1(순수코드):** `trading.screener` — 거래대금 급증 + 모멘텀(20/60일) + 신고가 근접을 **횡단면 백분위 랭크 가중합**, 유동성·보통주 게이트. **실DB 검증: 2,877 → 게이트 311 → 상위 30**, 반도체장비 테마 자연 부각. ScreenConfig로 튜닝.
  - **멀티에이전트 섹터 분류:** 게이트 311종목 → Workflow(9 분류 + 저신뢰 재검증, 10에이전트, ~22만 토큰) → 26섹터 **다중소속** 태깅. **293 분류 / 18 미분류**(추측 안 함). `stock_sectors` 적재(365행), 스크리너 출력에 섹터 태그 결합. *(분류 데이터는 gitignored market.sqlite — 재실행 비용 있어 추후 committed export 검토.)*

## 최근 완료
- 2026-06-08 — **M1 골격·데이터 계약 5종·journal·리플레이 하네스** → [archive](archive/2026-06-08-m1-skeleton.md)
  (pytest 19 passed, mypy 27 files no issues)

## 다음 후보 (전종목 스크리닝)
1. **LLM 전망 분석**(R2~R7) — 섹터 태그된 상위 후보의 촉매·실적·테마(DART·뉴스 결합) → 가설+무효화(ThesisRecord). 파이프라인의 다음 핵심.
2. **스크리너 튜닝** — 가중치·임계치 + 절대 필터(하락장 가드)·관리종목 제외.
3. **분류 영속화 결정** — stock_sectors를 committed export(JSON)로 둘지(재실행 비용 회피) — 이전 "SQLite git 공유" 보류 건과 함께.
4. **일일 diff** — 신규상장·변경 감지 → 분류 증분.
- 보류: 뉴스 2데스크 / KIS(호가·수급·실시간) / openclaw cron / NXT(🔴) / M2 R1 게이트.
