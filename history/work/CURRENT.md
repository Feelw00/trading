# 현재 작업 (CURRENT)

> 슬림 유지. 완료된 작업은 `archive/YYYY-MM-DD-<slug>.md`로 떨구고 여기엔 한 줄 + 링크만.
> 최종 갱신: 2026-06-08 (KST)

## 진행 중
- (없음 — 다음 후보 1번부터 착수)

## 최근 완료
- 2026-06-08 — **디스커버리 파이프라인 + 데이터소스 + boot/수집 스킬** → [archive](archive/2026-06-08-discovery-pipeline.md)
  - 수집(거시 FRED·ECOS·공공데이터 / 전종목 EOD 708k행 / 공시 DART) → 스크리너(거래대금+모멘텀+신고가) → 멀티에이전트 26섹터 분류 → 섹터 태그 후보. mypy 41 clean·pytest 48. 커밋 `d56819e`→`a98835a`.
- 2026-06-08 — **M1 골격·데이터 계약 5종·journal·리플레이** → [archive](archive/2026-06-08-m1-skeleton.md)

## 빠른 재개 (다음 세션 `/boot` 후)
- **상태:** 디스커버리 파이프라인 작동(전종목→후보→섹터). 키 발급 완료(FRED/ECOS/DATA_GO_KR/DART). KIS·뉴스 보류.
- **DB:** `data/market.sqlite`(gitignored) 1년치. 최신 수집일 = 2026-06-05. 새 거래일은 `/collect`로 증분.
- **바로 보기:** `/boot` → 거시 백드롭 + 신선도 + 오늘 후보(섹터). 또는 `python -m trading.screener`.

## 다음 후보 (전종목 스크리닝 → grounded 전망)
1. **후보 fact pack** — 후보별 공시(DART)+재무(DART)+가격맥락(DB) 결정론 수집. (현실 데이터 grounding)
2. **grounded 분석 에이전트** — fact pack을 *읽고* 가설+무효화(ThesisRecord) 도출. 멀티에이전트 병렬. **공시에 없는 촉매 지어내기 금지**.
3. **파이프라인 디스패치** — `trading.run` ROUNDS 채우기(collect-macro/collect-market/screen/daily) + openclaw cron(하루 2회).
4. **스크리너 튜닝** — 가중치·임계치 + 하락장 절대필터·관리종목 제외.
5. 분류 영속화(committed export) / 일일 diff(신규상장) / 뉴스 소스 확정.
- 보류: KIS(호가·수급·실시간) / NXT(🔴) / M2 R1 게이트.
