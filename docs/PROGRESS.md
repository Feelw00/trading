# PROGRESS

마일스톤 원장(coarse). 세션 단위 디테일은 `history/work/archive/`에 1세션 1파일.

## M1 — 골격 · 데이터 계약 · 리플레이 하네스 ✅ (2026-06-08)

- 프로젝트 골격: Poetry(src-layout `src/trading`, Python 3.13, in-project venv), pydantic v2, pytest, mypy strict(+pydantic plugin), `docker-compose`(PostgreSQL).
- 데이터 계약 5종(`src/trading/contracts`): FactRecord, EventRecord, ThesisRecord, Playbook, OrderDraft.
  - 전 레코드: as_of/fetched_at/source 필수 + timezone-aware(`AwareDatetime`, naive 거부), `extra` 금지, `frozen`(불변).
  - ThesisRecord: `invalidation` 비/공백이면 ValidationError.
  - OrderDraft: `stop`·`time_stop_days` 둘 다 없으면 ValidationError, `created_when_market`=closed만, 시장가 `OrderType` 부재.
- journal(`src/trading/journal`): in-memory append-only + 버전 패턴 + 스키마 위반 알림 훅. PostgreSQL 백엔드는 M2.
- 리플레이 하네스(`src/trading/replay`) + 샘플 픽스처 2일치.
- 엔트리포인트 스텁: `trading.run`(라운드 디스패치), `trading.watch`(감시 틱).

**검증 (AC)**: pytest **19 passed**, mypy **27 files, no issues**.

---

## M2 — R0 수집기 · R1 게이트(뉴스) · 라운드 R2~R4 · GitOps 부트스트랩 🟢 (2026-06-10)

- **R0 수집기** 7종 어댑터(ECOS·FRED·data.go.kr·DART·KRX·Naver·SearXNG) + macro/market/news 통합 수집기. 멱등성·백오프·환각 가드(source/as_of/fetched_at 필수).
- **R1 게이트 (뉴스)**: 신선도·dedup·publisher 신뢰 랭킹. 일반 FactRecord stale/conflict 게이트는 차후.
- **R2/R3/R4 라운드**: 단일 호출 배치(R2), 페르소나 입력격리(R3), perspective-diverse 적대검증(R4). 모두 `claude -p` 서브프로세스, openclaw provider 라우팅 미사용(SCHED-3).
- **격리 OpenClaw 인스턴스** (INFRA-1): `~/.openclaw/bin` (Node 22.22.3), `.runtime/openclaw` state, 포트 18790, 개인 인스턴스와 완전 분리. Telegram 비활성(폴링 충돌 회피).
- **GitOps 부트스트랩 체인** (INFRA-2): `git clone → ops/bootstrap.sh → start-gateway.sh → pair.sh → sync.py --apply`. 멱등. cron 11개 등록 후 일괄 비활성(M3+알림 준비 후 enable).
- **실거동 검증** (2026-06-10): R0 macro/news/eod, R2/R3/R4 — 환각 가드·데이터 부재 인정·적대 추론 메타 시그널 활용 등 LLM 가드 동작 확인. → `history/work/archive/2026-06-10-m2-bootstrap-validation.md`

**검증 (AC)**: pytest **162 passed**, mypy **0 issues** (73 files).

- **M2 마무리 슬라이스** (2026-06-10): 일반 R1 게이트(FactRecord stale/conflict + R5 하드게이트) · `market_calendar/` 가드(휴장일·DST·장중 LLM 거부, CAL-1/2 등록) · score-news 전수(뉴스 395→이벤트 131) · R4 threshold 분포 캘리브레이션(0.4/0.6 + .env knob). pytest **197 passed**, mypy **0 issues** (77 files). → `history/work/archive/2026-06-10-m2-wrapup-slices.md`

---

## M3 — alerts · R5/R5.5/R6/R7 · cron 가동 · 운영 안정화 🟢 (2026-06-10 ~ 06-11)

- **alerts** P0/P1/P2: 4요소({무엇이,규칙,행동,기한}) 스키마 강제, Telegram sendMessage 직접(폴링 없음 — 타 인스턴스 무충돌), P0즉시/P1다이제스트(12:30·15:40)/P2보고, append-only AlertStore. → `2026-06-10-m3-alerts-adapter.md`
- **R5 합성**: 흐름변수 화이트리스트(계약 로드 거부), 규율 코드 강제(3트랜치 20/50/30·총량상한·손절2종·stop 미제공=폐기), PlaybookStore, 장중 가드. 실거동 첫 합성=비거래 선택. R5-1 🟡. → `2026-06-10-m3-r5-synthesis.md`
- **R5.5 선택기**: 순수 함수(`<op><숫자>` AND, 누락=미충족), approved-only arm+P1, 장중·휴장 가드. SEL-1 🟡(NXT 부재=비거래). → `2026-06-10-m3-r55-selector.md`
- **R6 보고**: Jinja2 모닝/저녁 결재, 분량 가드 7000자(초과=실패+P1), 파일+Telegram. → `2026-06-10-m3-r6-reports.md`
- **R7 평가**: 결정론 채점기(적중률·캘리브레이션·R4 정확도·레짐 프록시 — R7-1 🟡), 해석·개정안 claude -p 박제만(자동 적용 금지), ScoreStore. → `2026-06-10-m3-r7-evaluation.md`
- **cron 18개 일괄 enable + 운영 안정화** (06-10 저녁~06-11): 트리거 모델 로컬 핀(qwen2.5:3b — 클라우드 쿼터 제거), `--no-deliver`, **fire-and-forget(setsid) 아키텍처**(LLM babysitting 제거 — kill 월권·rate limit 해소), trading.run 실패 P1 + 잡별 로그, daily-eod 16:05 이동(EOD 공개 시차), **drill.py**(슬롯 대기 없는 즉시 트리거+결정론 검증: PASS/WARN/FAIL). 첫 풀 사이클(저녁 결재+모닝 브리핑 자동 발송) 완주. → `2026-06-11-first-auto-cycle-audit.md`
- **보고·알림 UX**: 저녁 결재 "결정 우선" 재설계 + Telegram HTML 서식 통일(보고·P0·P1, md 미지원 해소). → `2026-06-11-telegram-format.md`
- **수급(투자자별 매매동향) 해소** (06-11): KIS TR 2종 공식 확정+실호출 검증 → `collectors/kis.py`(토큰 캐시)+`flows.py`(`data/flows.sqlite`)+`collect-flows` 라운드(daily-eod 체인)+FactPack `flows`(R3 수급 grounding). 🔴 KRX 정보데이터시스템 의존 해소. 거시 수집은 report-am/pm 라운드 내장으로 이동(cron 18→16슬롯). → `2026-06-11-kis-investor-flows.md`

**검증 (AC)**: pytest **272 passed**, mypy **0 issues (103 files)**. 운영: 16슬롯 자동 가동 중(06-11 거시 수집을 report 라운드에 내장, macro-am/pm 슬롯 제거).

---

## 미해결 / 다음

**M4 / Phase 1 잔여**
- 이벤트 감시기(`watch/`) → P0 발화 연결 (서킷브레이커·환율 임계·바이너리 전이·보유 공시) — heartbeat 배선 포함
- 리플레이 회귀 테스트 (6/2~6/8 주간, M4 프롬프트 §2)
- 승인 전이(draft→approved) 운영자 도구 (§6 수동 결재 UX)
- KIS 잔고·체결 어댑터 (`KIS_ENABLE_TRADING=false`) — 저녁 보고 집행·포지션 섹션 채움

**M2~3 잔여(소)**
- ~~R3 grounding 보강(수급 페르소나)~~ → 06-11 해소(KIS flows → FactPack) / R1 일반 게이트 운영 배선(landing→FactRecord 변환 계층)
- R4 실측 생존률·threshold 재캘리브레이션 — 운영 슬롯 데이터 누적 후
- CAL-1: 2026년 음력·대체공휴일 KRX 공지 확인 → `krx_holidays.json`

**OPEN_QUESTIONS 🔴**
- NXT 프리·애프터마켓 데이터 (SEL-1·R7-1 흐름 관측치 공유)
- 증권사 조건부(청산) 주문 API — KIS REAL 키 확보 완료, 청산 인터페이스 스펙 조사 잔여

**운영(상시)**
- 매 세션 `drill.py --audit`로 전일 사이클 점검 + claude -p 비용 모니터링(R2/R3/R4 일 2회)
- R2 모델 `.env` 핀(`R2_MODEL`) / 1Password 'stock / .env' 동기화: `OPENCLAW_GATEWAY_TOKEN`
