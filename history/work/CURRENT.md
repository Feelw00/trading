# 현재 작업 (CURRENT)

> 슬림 유지. 완료된 작업은 `archive/YYYY-MM-DD-<slug>.md`로 떨구고 여기엔 한 줄 + 링크만.
> 최종 갱신: 2026-06-09 (KST)

## 진행 중
- (없음)

## 최근 완료
- 2026-06-09 — **후보 fact pack**(next-work #1): 후보별 grounded 입력 슬라이스(R3 입력) 결정론 조립 — `contracts/factpack.py`(FactPack/PriceContext/DisclosureItem/FinancialLine) + `src/trading/factpack.py`. 가격맥락(DB) + 공시(DART list.json, 90일·15건) + 재무(DART 주요계정, 연결우선·당기/전기 YoY, 올해/작년 기간 폴백). 결측은 `notes`에만(추측 금지). 출력 `.runtime/factpack/<거래일>/<srtn>_<name>.json`. 실행 `python -m trading.factpack [top_n]`. pytest 63(+6)·mypy clean. **실검증**: 대원제약 영업이익 YoY -53%·순익 -61%(실적악화 포착) / **크레오에스지 1251%는 액면병합 아티팩트**(20260511 변경상장+거래소 조회공시요구로 즉시 판명) → 스크리너 튜닝 #4 입력.
- 2026-06-09 — **혼재 KSIC 잔존분 보강 + KSIC 천장 finding**: 미분류 31종 induty_code를 293라벨 대비 5/4/3자리 순도로 실측 → **결정론 확장 데이터 미지지**(거의 n부족·혼재) 확인. 대응: ① 고확신 유명주 12종 큐레이션 오버라이드(`manual-curated-v1`, 환각가드: 확실한 것만 — 롯데케미칼·JYP·루닛·코오롱·하림지주·iM금융지주·앱클론·이노스페이스·풍산 등) ② taxonomy 버킷 부재분(해운 HMM·흥아해운·운송 동양고속·레저 강원랜드)은 `docs/PROPOSALS.md` P-1 등록(추측 안 함) ③ LLM 폴백 분류기 P-2 제안. 커버리지 게이트 **90%→93.8%(288/307)**. pytest 57·mypy clean.
- 2026-06-09 — **섹터 태깅 grounded 영속화**(next-work #5 부분, PR#1 머지 `79e8d95`): DART 회사개황(KSIC 업종) → 26 taxonomy **결정론** 분류(`src/trading/sectors.py`). 기존 293 LLM 라벨 대비 induty_code **순도 실측** → 깨끗한 코드(≥0.75)만 채택, 혼재(264 등)는 미분류 유지(추측 안 함). 소스 `dart-ksic-v1`(큐레이션·LLM 우선·갭만 채움, 스크리너 `sector_map_multi` 병합). `/collect`에 매 거래일 자동 보강 스텝 배선. 6/8 게이트 43미태깅→12 grounded 분류. LLM 미개입.
- 2026-06-08 — **디스커버리 파이프라인 + 데이터소스 + boot/수집 스킬** → [archive](archive/2026-06-08-discovery-pipeline.md)
  - 수집(거시 FRED·ECOS·공공데이터 / 전종목 EOD 708k행 / 공시 DART) → 스크리너(거래대금+모멘텀+신고가) → 멀티에이전트 26섹터 분류 → 섹터 태그 후보. mypy 41 clean·pytest 48. 커밋 `d56819e`→`a98835a`.
- 2026-06-08 — **M1 골격·데이터 계약 5종·journal·리플레이** → [archive](archive/2026-06-08-m1-skeleton.md)

## 빠른 재개 (다음 세션 `/boot` 후)
- **상태:** 디스커버리 파이프라인 작동(전종목→후보→섹터). 키 발급 완료(FRED/ECOS/DATA_GO_KR/DART). KIS·뉴스 보류.
- **DB:** `data/market.sqlite`(gitignored) 1년치(247거래일·711k행). 최신 수집일 = 2026-06-08. 새 거래일은 `/collect`로 증분(시세+섹터보강+스크리너).
- **바로 보기:** `/boot` → 거시 백드롭 + 신선도 + 오늘 후보(섹터). 또는 `python -m trading.screener`.

## 다음 후보 (전종목 스크리닝 → grounded 전망)
1. ~~후보 fact pack~~(✅ `trading.factpack` — 가격+공시+재무 결정론 조립, JSON). 후속: `trading.run`/스킬 배선·R1 신선도 게이트 연동.
2. **grounded 분석 에이전트** — fact pack을 *읽고* 가설+무효화(ThesisRecord) 도출. 멀티에이전트 병렬. **공시에 없는 촉매 지어내기 금지**. (입력 슬라이스 = #1 산출 JSON)
3. **파이프라인 디스패치** — `trading.run` ROUNDS 채우기(collect-macro/collect-market/screen/factpack/daily) + openclaw cron(하루 2회).
4. **스크리너 튜닝** — 가중치·임계치 + 하락장 절대필터·관리종목 제외 + **액면병합/분할 모멘텀 아티팩트 가드**(크레오에스지 사례).
5. ~~분류 영속화~~(✅ grounded `dart-ksic-v1` + 큐레이션 `manual-curated-v1`, 93.8%). 남은 19 미분류는 **taxonomy 갭(PROPOSALS P-1)** + 진짜 모호(전자부품·다각화). 후속: P-1 taxonomy 확장 합의 / P-2 LLM 폴백 분류기 / 일일 diff(신규상장) / 뉴스 소스.
- 보류: KIS(호가·수급·실시간) / NXT(🔴) / M2 R1 게이트.
