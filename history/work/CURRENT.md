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
  - **어댑터 구현:** `src/trading/collectors`에 FRED(완전)·ECOS(클라이언트 완전, 통계코드 미확정→blocked)·공통(HTTP 재시도·SQLite landing). `python -m trading.collectors.macro` 결정론 실행 → collect-macro 스킬이 이걸 트리거(LLM 데이터 미개입).
  - **검증:** mypy 35 clean · pytest 34 passed. 키 없이 실행 시 0 적재·9 blocked(환각 0). FRED 키 발급 시 해외지수·유가 즉시 동작.

## 최근 완료
- 2026-06-08 — **M1 골격·데이터 계약 5종·journal·리플레이 하네스** → [archive](archive/2026-06-08-m1-skeleton.md)
  (pytest 19 passed, mypy 27 files no issues)

## 다음 후보
1. **FRED 키 발급** → `python -m trading.collectors.macro` 실거동(해외지수·유가 적재) + `/boot` 재검증.
2. **ECOS 통계코드 확정** — 키 발급 후 StatisticItemList 카탈로그로 환율·금리·국고채 코드 확인 → `macro.py` 레지스트리 입력(추측 금지).
3. 공공데이터(국내지수) 어댑터 + KIS MCP(국내종목, 모의·trading off) 연결.
4. 클러스터/종목 `domains.py CLUSTERS` 승격 + openclaw cron(하루 2회·gpt-5.5) 배선.
5. 남은 갭 — 국내 수급 KIS TR, NXT(🔴). **M2** R1 게이트(순수코드).
