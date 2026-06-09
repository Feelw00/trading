# 현재 작업 (CURRENT)

> 슬림 유지. 완료된 작업은 `archive/YYYY-MM-DD-<slug>.md`로 떨구고 여기엔 한 줄 + 링크만.
> 최종 갱신: 2026-06-09 (KST)

## 진행 중
- (없음)

## 최근 완료
- 2026-06-09 — **섹터 태깅 grounded 영속화**(next-work #5 부분): DART 회사개황(KSIC 업종) → 26 taxonomy **결정론** 분류(`src/trading/sectors.py`). 기존 293 LLM 라벨 대비 induty_code **순도 실측** → 깨끗한 코드(≥0.75)만 채택, 혼재(264 등)는 미분류 유지(추측 안 함). 소스 `dart-ksic-v1`(큐레이션 `llm-cls-v1` 우선·갭만 채움, 스크리너 `sector_map_multi` 병합). `/collect`에 매 거래일 자동 보강 스텝 배선. **6/8 게이트 43미태깅→12 grounded 분류**(대원제약→pharma_bio·크레오에스지→ai_software·태양금속/화신→auto·에이팩트→semi·대한조선→shipbuilding 등 스폿체크 정확). mypy strict clean·pytest 55(+7). LLM 미개입.
- 2026-06-08 — **디스커버리 파이프라인 + 데이터소스 + boot/수집 스킬** → [archive](archive/2026-06-08-discovery-pipeline.md)
  - 수집(거시 FRED·ECOS·공공데이터 / 전종목 EOD 708k행 / 공시 DART) → 스크리너(거래대금+모멘텀+신고가) → 멀티에이전트 26섹터 분류 → 섹터 태그 후보. mypy 41 clean·pytest 48. 커밋 `d56819e`→`a98835a`.
- 2026-06-08 — **M1 골격·데이터 계약 5종·journal·리플레이** → [archive](archive/2026-06-08-m1-skeleton.md)

## 빠른 재개 (다음 세션 `/boot` 후)
- **상태:** 디스커버리 파이프라인 작동(전종목→후보→섹터). 키 발급 완료(FRED/ECOS/DATA_GO_KR/DART). KIS·뉴스 보류.
- **DB:** `data/market.sqlite`(gitignored) 1년치(247거래일·711k행). 최신 수집일 = 2026-06-08. 새 거래일은 `/collect`로 증분(시세+섹터보강+스크리너).
- **바로 보기:** `/boot` → 거시 백드롭 + 신선도 + 오늘 후보(섹터). 또는 `python -m trading.screener`.

## 다음 후보 (전종목 스크리닝 → grounded 전망)
1. **후보 fact pack** — 후보별 공시(DART)+재무(DART)+가격맥락(DB) 결정론 수집. (현실 데이터 grounding)
2. **grounded 분석 에이전트** — fact pack을 *읽고* 가설+무효화(ThesisRecord) 도출. 멀티에이전트 병렬. **공시에 없는 촉매 지어내기 금지**.
3. **파이프라인 디스패치** — `trading.run` ROUNDS 채우기(collect-macro/collect-market/screen/daily) + openclaw cron(하루 2회).
4. **스크리너 튜닝** — 가중치·임계치 + 하락장 절대필터·관리종목 제외.
5. ~~분류 영속화~~(✅ grounded `dart-ksic-v1`) / 남은 것: **혼재 KSIC 코드 보강**(264·262·292·201·649… 저순도 31종 미분류 잔존 → 5자리 세분 규칙 or LLM 폴백) · 일일 diff(신규상장) · 뉴스 소스 확정.
- 보류: KIS(호가·수급·실시간) / NXT(🔴) / M2 R1 게이트.
