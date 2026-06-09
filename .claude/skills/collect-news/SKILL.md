---
name: collect-news
description: 뉴스 수집 — 후보·시장 뉴스를 별도로 조회한다(공시와 분리, 같이 하면 무거움). "뉴스 수집" 시 사용. COLLECT-4(뉴스 한정 웹서치 허용). 국내→네이버 / 해외→SearXNG.
---

# collect-news — 뉴스 수집 (별도)

뉴스는 공시(`collect-disclosure`)와 **분리** — 함께 조회하면 무겁기 때문. 정성 촉매(테마·이슈·정책) 보강용.

## 소스 분담 (COLLECT-4)
뉴스 한정으로 웹서치 허용(시세·거시·공시는 COLLECT-3 그대로 엄격). **출처 가드**로 환각 차단.
- **국내 후보명 → 네이버 검색 API**(`NaverNewsSource`). `NAVER_CLIENT_ID/SECRET` 필요.
- **해외 매크로·테마 → SearXNG**(`SEARXNG_URL`). ※ 어댑터 후속 — 현재 미연결 시 blocked.

## 절차 (결정론 어댑터 — 하네스)
1. `.env` 로드 후 `poetry run python -m trading.collect_news [top_n]` 실행(독립 커맨드).
2. 라우터가 스크리너 후보→네이버, 테마→SearXNG로 검색 → 정규화·교차소스 dedup → `.runtime/collect/<날짜>/news.sqlite` 적재.
3. 출력(`적재 N건 / blocked …`)을 그대로 보고. 미연결 백엔드는 blocked(빈 결과 날조 금지).

> **하네스(COLLECT-4):** 단일 뉴스 search 어댑터 경로로만. 모든 항목 URL·발행처·published_at(KST)·source 필수, 사실은 fetch된 기사 귀속(날조 금지). 저신뢰=UNVERIFIED, 검색실패=blocked. LLM은 트리거·쿼리확장·그룹핑만, 사실 생성 금지.
