# 2026-06-11 — 수급(투자자별 매매동향) 해소: KIS TR + flows 파이프라인 / 거시 수집 보고 라운드 내장

## 배경
- OPEN_QUESTIONS 🔴 "KRX 정보데이터시스템 — 시세·투자자별 매매동향"이 R3 수급 페르소나 grounding과 저녁 보고 수급 섹션의 결측 원인.
- 조사 결과 공식 KRX Open API(openapi.krx.co.kr)에는 **투자자별 매매동향 서비스 자체가 없음**(29개 서비스 목록 확인). data.krx.co.kr 비공식 OTP 스크래핑 대신, **이미 키를 보유한 KIS Open API의 공식 TR**로 해소(COLLECT-2 갭①).

## 확정 스펙 (공식 저장소 `koreainvestment/open-trading-api` + 실호출 관측 — 추측 0)
- 토큰: `POST /oauth2/tokenP` (REAL `https://openapi.koreainvestment.com:9443`) — 24h 유효, 6시간 내 재발급=동일 토큰+알림톡 → **파일 캐시 필수** `.runtime/kis/token.json`.
- 종목별(일별): `GET /uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily` TR `FHPTJ04160001` — output2 일별 ~30거래일. 순매수 `{frgn,prsn,orgn}_ntby_qty`(주) / `..._ntby_tr_pbmn`(**백만원** — 삼성전자 수량×주가 대조로 단위 검증).
- 시장별(일별): `GET /uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market` TR `FHPTJ04040000` — **KOSPI=(업종 0001, KSP) / KOSDAQ=(업종 1001, KSQ)** 조합 실호출로 확정(불일치 조합은 0 반환 관측).

## 산출
- `src/trading/collectors/kis.py` — KisClient: 토큰 캐시(만료 5분 마진), 페이싱 0.12s, OSError 백오프 재시도 3회, rt_cd≠0 → CollectError. **조회 전용 — 주문 TR 금지(rule #3) 유지.**
- `src/trading/collectors/flows.py` — FlowStore(`data/flows.sqlite`, append-only INSERT OR IGNORE, UNIQUE(scope,code,bas_dt)): 핵심 3주체 순매수 컬럼 + `raw_json` 원행 보존. collect()는 시장 2 + 스크리너 후보 N, 대상별 실패 격리.
- `trading.run` — `collect-flows` 라운드 신설. daily-eod 체인: 전종목→섹터→스크리너→**수급(best-effort, 실패 시 P1+계속)**→factpack.
- FactPack 계약에 `FlowLine`/`flows` 추가(기본값 — 하위호환), 조립 시 flows.sqlite 주입, 결측은 notes "수급 미수집"(추측 금지). → R3 수급 grounding 충족.
- **`/flows` 스킬** 신설(`.claude/skills/flows/`): `python -m trading.collectors.flows --report` 트리거 — 수집(멱등) 후 최근 거래일 시장·후보 수급을 억원 단위로 결정론 요약(`report_lines`, LLM 미개입). 공식 KRX Open API에 해당 서비스 부재 → KIS TR 경유임을 스킬에 명기.
- **연기금 분리**: KIS `fund_ntby_tr_pbmn`(공식 라벨 "기금 순매수 거래 대금") 확인 + 기관계=금융투자+투신+사모+은행+보험+종금+기금 합산 관계를 실관측(삼성전자 행)으로 산술 검증 → 보고에 `연기금 | 기관(연기금外)` 분해, FlowLine에 `fund_ntby_mn` 추가. 기금은 컬럼 추가 없이 `raw_json` 보존분에서 `json_extract`(append-only 스키마 불변).
- **장중 모드**: 시세성 TR `FHPTJ04030000`(HTS [0403]) — (999,S001)=KOSPI/(999,S101)=KOSDAQ 조합 프로브 관측 확정(13:49~55 장중 실호출). `intraday_lines()`: `in_krx_session` 가드로 장중에만 `[당일 잠정]` 섹션 출력(조회시각 라벨+"확정치 아님"), 장외·휴장 빈 리스트, 적재 금지(응답 날짜 필드 부재). 잔여 🟡: 잠정 단위 교차검증(마감 후 일별 대조) — OPEN_QUESTIONS COLLECT-2 기록.
- (같은 세션 선행 작업) 거시 수집을 macro-am/pm 독립 슬롯에서 **report-am/pm 라운드 내장**으로 이동 — cron 18→16슬롯, 트리거 에이전트 턴 최소화. boot 스킬은 미수집 시 자동 수집(collect/collect-news)으로 개정.

## 검증
- pytest **281 passed** (신규 9: 토큰 캐시/만료 재발급, TR 파라미터·헤더, rt_cd 에러, FlowStore 멱등, 부분실패 격리, FactPack flows 통합), mypy strict 0 issues.
- 실거동: collect-flows 2회 — 1차 690행(3대상 일시 실패) → 재시도 추가 후 2차 360행 보충, 총 1,050행(시장 2+후보 15, 실패 0, 멱등 확인). factpack 재생성 → `flows: 5건 + sources.flows=flows.sqlite(KIS:투자자매매동향)` 확인(티엠씨 6/10: 개인 +2,244 / 외인 -2,163 / 기관 -30 백만원).

## 남은 것
- 저녁 보고(R6) 수급 섹션은 여전히 notes의 "수급 확정치: KRX 접근 미해결" 문구 사용 중 → flows.sqlite 기반으로 채우는 후속 슬라이스 필요.
- cron 슬롯에 collect-flows 독립 등록은 안 함(daily-eod 체인 내장으로 충분 — 16:05).
- KIS 잔고·체결 어댑터는 별개(미구현 유지).
