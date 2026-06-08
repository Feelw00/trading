---
name: collect-macro
description: 거시지표 수집 — 금리·환율·유가·주요지수를 실데이터로 수집해 SQLite에 적재하는 독립 스킬. 부팅 시 자동 호출되며 단독 실행도 가능. "거시지표 수집", "매크로 수집", 시장 백드롭 요청 시 사용. 출처·as_of 필수(환각 가드).
---

# collect-macro — 거시지표 수집 (독립)

시장 백드롭(금리·환율·유가·지수)을 빠르게 수집해 적재한다. 섹터·뉴스 수집(`collect`)과 **분리** — 가볍고 자주 호출(부팅마다).

## 대상 (항목 → 승인 소스, COLLECT-2)
| 항목 | 승인 소스 |
|---|---|
| 한국 기준금리·국고채 3Y/10Y | **ECOS** |
| USD/KRW | **ECOS** |
| KOSPI·KOSDAQ | **공공데이터/KRX** |
| S&P500·NASDAQ·SOX(필라델피아 반도체) | **FRED** |
| WTI·Brent | **FRED** |

## 절차 (하네스 — 독자 웹서치 금지)
1. **승인 소스 어댑터로만** 각 항목 최신값 조회(위 표). **WebSearch/WebFetch 등 독자 웹서치 금지.**
2. 스키마 1행으로 정규화 → SQLite append-only INSERT.
3. 요약 보고: 수집 건수 / `UNVERIFIED` 건수 / 누락·`blocked` 항목.

> **하네스 규칙(COLLECT-3):** LLM은 승인된 소스 어댑터만 호출한다. 소스 실패·키 미연결 시 해당 항목을 `blocked`/`UNVERIFIED`로 기록하고 **다른 소스나 웹서치로 임의 대체하지 않는다.** 어댑터 미구현 구간은 건너뛰고 보고만 한다.

## 환각 가드 (데이터 품질 — 필수)
- **기억으로 수치를 만들지 마라.** 실제 조회 출처만. `source`에 URL 기록.
- `as_of`(데이터 시점)·`fetched_at`(수집 시각) **둘 다 KST**(ISO8601 `+09:00`).
- 확인 불가·불확실 → `verified=0`, `note`에 사유. **추측 금지.**
- 마감/휴장으로 값 없으면 **없다고 기록**(빈 추측 금지).

## SQLite 적재
- 경로: `.runtime/collect/<YYYY-MM-DD(KST)>/macro_indicators.sqlite` (gitignored, append-only)
- 스키마: `collect` 스킬의 `facts` 테이블과 동일.
```sql
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cluster      TEXT NOT NULL,        -- 'macro_indicators'
  region       TEXT,                 -- KR | US
  asset_class  TEXT,                 -- macro | fx | index
  sector       TEXT, ticker TEXT, name TEXT,
  metric       TEXT NOT NULL,        -- rate | fx | oil | index_level
  value        TEXT, unit TEXT,
  source       TEXT NOT NULL,
  as_of        TEXT NOT NULL,        -- KST ISO8601 (+09:00)
  fetched_at   TEXT NOT NULL,        -- KST
  verified     INTEGER NOT NULL DEFAULT 0,
  note         TEXT
);
```

## 비고
- `/boot`가 부팅 시 이 스킬을 먼저 호출(시장 백드롭). 단독 호출도 가능.
- 추후 openclaw cron(하루 2회·gpt-5.5)이 라운드/부팅 시점에 재사용.
