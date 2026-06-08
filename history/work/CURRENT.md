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
  - **전종목 EOD DB (방향 전환):** 고정 대장주 universe **폐기** → **전 상장종목(2,877/일) 1콜 수집 → SQLite(`data/market.sqlite`, gitignored)**. `trading.collectors.market`(MarketStore, append-only/IGNORE). 4거래일 11,508행 실적재 검증. 스크리너(거래대금+모멘텀+신고가)·섹터분류의 토대.

## 최근 완료
- 2026-06-08 — **M1 골격·데이터 계약 5종·journal·리플레이 하네스** → [archive](archive/2026-06-08-m1-skeleton.md)
  (pytest 19 passed, mypy 27 files no issues)

## 다음 후보 (방향 전환: 전종목 스크리닝)
1. **1년 히스토리 백필** — 신호(20·60·252일) 토대. ~250콜, 쿼타 내. `backfill(start,end)`.
2. **유동성 필터 → 멀티에이전트 섹터 분류** — 전종목 중 분석가치 종목만 26섹터(테마)로. confidence·미분류·다중소속·스팟체크 가드(무명 종목 오분류=1번 리스크).
3. **스크리너(순수코드)** — 거래대금+모멘텀+신고가 → 일일 후보 + 섹터 태그(우선주·스팩·관리종목 제외).
4. **일일 diff** — 신규상장·변경 감지해 분류 추가.
5. **LLM 전망 분석**(R2~R7) — 추려진 후보의 촉매·실적·테마(DART·뉴스 결합).
- 보류: 뉴스 2데스크 / KIS(호가·수급·실시간) / openclaw cron / NXT(🔴) / M2 R1 게이트.
