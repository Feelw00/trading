---
name: collect
description: 자료 수집 라운드 — 섹터 클러스터·뉴스·거시지표를 LLM이 실데이터로 수집해 SQLite에 적재한다. "/collect", "수집 트리거", "자료 수집", "수집 라운드" 요청 시 사용. 모든 수치는 실제 조회 출처 필수(환각 가드).
---

# collect — 자료 수집 라운드 (LLM 수집 → SQLite)

한 번의 수집 라운드를 수행한다. 인자로 클러스터 id 1개를 받으면 그것만, 없으면 **전체 11개**(섹터 9 + 뉴스 2).
정형화·검증(R2/R1)은 후속 — 여기선 **원시 적재**까지만.

## 대상 (11)

### A. 섹터 클러스터 9 — 각 클러스터: 구성종목 시세·수급 + 주요 공시 + 섹터 뉴스
| id | 클러스터 | 섹터(대표종목, `src/trading/domains.py` 기준) |
|---|---|---|
| `semis_display` | 반도체·디스플레이 | 반도체(삼성전자·SK하이닉스), 디스플레이(LG디스플레이·덕산네오룩스) |
| `battery_chem` | 2차전지·화학 | 2차전지셀(LG에너지솔루션·삼성SDI), 전지소재(에코프로비엠·포스코퓨처엠), 화학(LG화학·롯데케미칼) |
| `ai_internet_robot` | AI·인터넷·로봇 | AI·SW(네이버·카카오), 인터넷·게임(크래프톤·넷마블), 로봇(레인보우로보틱스·두산로보틱스) |
| `defense_aero_ship` | 방산·우주·조선 | 방산(한화에어로스페이스·LIG넥스원), 우주항공·UAM(한화시스템), 조선(HD현대중공업·한화오션) |
| `industrials` | 산업재 | 자동차(현대차·기아), 기계(두산밥캣), 철강·소재(POSCO홀딩스), 건설(현대건설) |
| `energy_power` | 에너지·전력 | 전력기기·그리드(HD현대일렉트릭·LS일렉트릭), 원자력(두산에너빌리티), 신재생(씨에스윈드) |
| `pharma_bio` | 제약·바이오 | 제약·바이오(삼성바이오로직스·셀트리온) |
| `consumer_ent` | 소비·엔터 | 화장품(아모레퍼시픽·LG생활건강), 엔터(하이브·JYP), 음식료(CJ제일제당), 유통(이마트) |
| `financials_def` | 금융·방어 | 금융(KB금융·삼성화재), 통신(SKT·KT), 지주(삼성물산) |

> 대표종목은 placeholder(`domains.py` kr_examples). 구성종목 확정 전까지 이 목록 + 검색으로 보강.

### B. 뉴스 데스크 2 — 헤드라인 + 요약 + 출처
| id | 데스크 | 범위 |
|---|---|---|
| `news_macro` | 거시·정책 | 금리·환율·통화정책·재정·지정학 |
| `news_market` | 시황·해외 | 국내외 증시 시황, 미국·중국 매크로 |

> **거시지표(금리·환율·유가·지수)는 별도 `collect-macro` 스킬로 분리** — 부팅 시 수집하므로 여기엔 없음.

## 절차
1. **대상 결정** — 인자에 클러스터 id가 있으면 그것만, 없으면 11개 전부.
2. **실데이터 조회** — 각 항목을 **승인된 소스 어댑터로만** 수집(국내 종목·호가·수급 = KIS / KIS MCP, COLLECT-2). **독자 웹서치(WebSearch/WebFetch) 금지.**
3. **정규화** — 각 사실을 아래 스키마 1행으로.
4. **SQLite 적재** — 클러스터별 파일에 append-only INSERT.
5. **요약 보고** — 수집 건수 / `UNVERIFIED` 건수 / 누락 항목.

## 환각 가드 · 하네스 (데이터 품질 — 필수)
- **하네스(COLLECT-3):** 승인된 소스 어댑터만 호출 — 독자 웹서치(WebSearch/WebFetch) 금지. 소스 실패·키 미연결 시 `UNVERIFIED`/`blocked`로 기록하고 다른 소스·웹서치로 임의 대체하지 않는다.
- **기억으로 수치를 만들지 마라.** 모든 수치·사실은 실제 조회 결과에서만. `source`에 URL/엔드포인트 기록.
- `as_of` = 데이터 시점(페이지/공시 기준), `fetched_at` = 수집 시각. **둘 다 KST tz 명시**(ISO8601 `+09:00`).
- 출처 확인 불가·불확실 → `verified=0`, `note`에 사유. **추측값 절대 금지.**
- 휴장/마감 미반영 등으로 값이 없으면 **없다고 기록**(빈칸 추측 금지).

## SQLite 적재
- 경로: `.runtime/collect/<YYYY-MM-DD(KST)>/<cluster_id>.sqlite` — 클러스터별 파일이라 동시 수집해도 충돌 없음. (`.runtime/`은 gitignored)
- append-only: **INSERT만**. 정정은 새 행으로(UPDATE/DELETE 금지).
- 스키마:
```sql
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cluster      TEXT NOT NULL,
  region       TEXT,                 -- KR | US
  asset_class  TEXT,                 -- index | fx | macro | news | sector
  sector       TEXT,                 -- domains.py Sector 값(복수면 콤마)
  ticker       TEXT,
  name         TEXT,
  metric       TEXT NOT NULL,        -- close | change_pct | volume | foreign_net | inst_net | disclosure | headline | rate | fx | oil | index_level ...
  value        TEXT,                 -- 원시값(텍스트 보관; 타입화는 R2)
  unit         TEXT,
  source       TEXT NOT NULL,        -- URL/엔드포인트
  as_of        TEXT NOT NULL,        -- 데이터 시점, KST ISO8601 (+09:00)
  fetched_at   TEXT NOT NULL,        -- 수집 시각, KST
  verified     INTEGER NOT NULL DEFAULT 0,  -- 1=출처확인, 0=UNVERIFIED
  note         TEXT
);
```

## 비고
- 이 절차는 추후 **openclaw cron(하루 2회·gpt-5.5)**이 그대로 재사용. 현재는 **수동 트리거**(`/collect`).
- "수집 라운드 복수"는 같은 절차를 시점만 달리(오전/오후) 호출 — 라운드별 `.runtime/collect/<date>/`로 분리됨.
