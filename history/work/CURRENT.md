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
  - **거시지표 수집 완료:** `src/trading/collectors`에 FRED·ECOS·공통(HTTP 재시도·SQLite landing). `python -m trading.collectors.macro` 결정론 실행 → collect-macro 스킬이 트리거(LLM 데이터 미개입). ECOS 통계코드는 카탈로그(StatisticItemList)로 확정(추측 0).
  - **검증:** mypy 35 clean · pytest 35 passed. **거시 9건 실적재 전부 verified**(FRED: SOX·S&P·NASDAQ·WTI·Brent / ECOS: USD/KRW 1543·기준금리 2.5%·국고채 3Y 3.882%·10Y 4.254%).

## 최근 완료
- 2026-06-08 — **M1 골격·데이터 계약 5종·journal·리플레이 하네스** → [archive](archive/2026-06-08-m1-skeleton.md)
  (pytest 19 passed, mypy 27 files no issues)

## 다음 후보
1. **공공데이터 국내지수 어댑터**(KOSPI/KOSDAQ) — data.go.kr 키 발급 동반.
2. **KIS 어댑터/MCP**(국내 종목 시세·호가·수급, 섹터 9클러스터) — 계좌 필요(모의·trading off).
3. **뉴스 2데스크**(거시·정책/시황·해외) — LLM 수집 경로.
4. 클러스터/종목 `domains.py CLUSTERS` 승격 + `/collect` 배선 + openclaw cron(하루 2회·gpt-5.5).
5. 남은 갭 — 국내 수급 KIS TR, NXT(🔴). **M2** R1 게이트(순수코드).
