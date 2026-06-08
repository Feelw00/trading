---
name: collect-disclosure
description: 공시 수집 — 스크리너 후보 종목의 DART 전자공시·재무를 조회한다(전망 분석 grounding). 뉴스와 분리(같이 조회하면 무거움). "공시 수집", "DART 조회" 시 사용.
---

# collect-disclosure — 후보 공시·재무 수집 (DART)

스크리너 후보의 **현실 데이터**(공시·재무)를 DART에서 조회한다. 전종목이 아니라 **후보 대상**(무거움 방지). 뉴스는 `collect-news`로 **분리** — 공시와 함께 돌리면 무겁다.

## 절차 (결정론적 어댑터 — 하네스)
1. 스크리너 후보 종목코드 확보(`python -m trading.screener`).
2. `.env`의 `DART_API_KEY`로 `trading.collectors.dart`(`DartClient`):
   - `corp_code_map()` — 단축코드 → corp_code (1회).
   - 후보별 `disclosures(corp_code, bgn_de, end_de)` — 최근 공시 목록.
   - 후보별 `financials(corp_code, bsns_year, reprt_code)` — 주요계정(당기/전기).
3. 후보별 **fact pack**(공시 + 재무 + 가격맥락 DB)으로 정리 → 전망 분석 입력.

> **가드:** 공시에 없는 촉매를 지어내지 않는다. status 013(데이터없음)은 빈 처리, 그 외 오류는 blocked. 승인 소스(DART)만 — 독자 웹서치 금지.
