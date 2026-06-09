---
name: collect
description: 신규 데이터 수집 — 전 상장종목 EOD 시세를 1콜로 받아 DB에 적재하고 스크리너로 후보를 갱신한다. "/collect", "수집", "신규 데이터 수집" 시 사용. 거시는 collect-macro, 공시는 collect-disclosure, 뉴스는 collect-news 별도.
---

# collect — 전종목 신규 데이터 수집

전 상장종목 EOD 시세(신규)를 받아 DB에 적재하고 스크리너로 후보를 갱신한다.
거시(`collect-macro`)·공시(`collect-disclosure`)·뉴스(`collect-news`)는 **별도 스킬** — 같이 돌리면 무겁다.

## 절차 (결정론적 어댑터 — 하네스)
스크립트가 수행하고 스킬은 트리거·보고만(LLM은 데이터 미개입):
1. **콘솔 날짜 확인** — `TZ=Asia/Seoul date '+%Y-%m-%d (%a)'`.
2. `.env` 로드 후 `poetry run python -m trading.collectors.market` — 최근 거래일 전 상장종목(≈2,877) EOD를 **1콜**로 적재(idempotent, append-only). EOD는 +1영업일 공개라 최신 basDt가 당일이 아닐 수 있음(정상).
3. `poetry run python -m trading.sectors` — **섹터 태깅 보강(grounded·결정론)**: 게이트 통과 종목 중 미태깅분만 DART 회사개황 업종(KSIC)으로 분류해 `dart-ksic-v1` 적재. 신규 상장·신규 진입 종목만 처리(시도분은 스킵). 혼재 업종코드는 추측 안 하고 미분류 유지(환각가드). DART 키 없으면 스킵(blocked).
4. `poetry run python -m trading.screener` — 갱신된 DB로 후보 + 섹터 태그 산출(`llm-cls-v1`+`dart-ksic-v1` 병합).
5. 보고: 수집 일자·신규 행수 / 섹터 보강(신규분류·미분류유지) / 스크리너 후보 Top-N(섹터 태그) / blocked.

> **하네스(COLLECT-3):** 승인 소스(data.go.kr·DART) 어댑터만 호출. **독자 웹서치 금지.** 키 미설정·소스 실패는 `blocked` 보고 — 다른 소스·웹서치로 대체하지 않는다.
> **섹터 분류는 LLM 미개입**(CLAUDE.md): DART 등록업종을 커밋된 KSIC 크로스워크로 매핑하는 순수 코드. 크로스워크는 기존 라벨 대비 실측 순도≥0.75 코드만 채택.
