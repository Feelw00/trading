---
description: 신규 데이터 수집 — 전 상장종목 EOD 시세 적재 + 스크리너 갱신.
allowed-tools: Read, Bash
---

`collect` 스킬로 **신규 데이터(전 상장종목 EOD 시세)**를 수집하고 스크리너를 갱신한다.

- 콘솔 날짜 확인 → `.env` 로드 → `python -m trading.collectors.market`(전종목 1콜 적재) → `python -m trading.screener`(후보).
- **거시·공시·뉴스는 별도** — `collect-macro` / `collect-disclosure` / `collect-news`.
- **하네스:** 승인 소스(data.go.kr)만, 독자 웹서치 금지, 실패=blocked.
- 보고: 수집 일자·행수 / 후보 Top-N(섹터 태그).
