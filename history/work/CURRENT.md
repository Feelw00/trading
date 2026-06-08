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
  - **미검증·블로커:** 소스 어댑터(ECOS/공공/FRED REST, KIS MCP) **아직 미구현** → 현재 `/collect`·`/boot`는 어댑터 없으면 `blocked` 보고(웹서치 우회 안 함).

## 최근 완료
- 2026-06-08 — **M1 골격·데이터 계약 5종·journal·리플레이 하네스** → [archive](archive/2026-06-08-m1-skeleton.md)
  (pytest 19 passed, mypy 27 files no issues)

## 다음 후보
1. **소스 어댑터 구현(하네스 본체)** — ECOS/공공데이터/FRED REST 클라이언트 + KIS MCP 연결(모의·trading off). 각 공식 문서에서 엔드포인트·FRED 시리즈ID·KIS TR_ID 확정(추측 금지). 키 발급 동반.
2. `collect-macro`를 ECOS+공공데이터+FRED로 재작성 → `/boot` **웹서치 없이** 재검증.
3. 클러스터/종목을 `domains.py CLUSTERS`로 코드 승격(단일소스) + openclaw cron(하루 2회·gpt-5.5) 배선.
4. 남은 갭 해소 — 국내 투자자별 매매동향(수급) KIS TR 확인, NXT(🔴).
5. **M2** — R1 게이트(검증, 순수코드 유지).
