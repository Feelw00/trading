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

## 미해결 / 다음

**M2 잔여**
- R3 grounding 보강(수급 페르소나 데이터 부재 — 🔴 KRX 의존)
- R4 실검증 실행(선별 18건, ~54 claude 호출 — 비용 승인 후)
- R1 일반 게이트 운영 배선: landing→FactRecord 변환 계층 후 거시·시세 적용

**M3 / Phase 1**
- ✅ `alerts/` P0/P1/P2 어댑터 (2026-06-10): 4요소 강제, Telegram sendMessage 직접(폴링 없음), P0즉시/P1다이제스트/P2보고, append-only AlertStore, cron digest 슬롯 2개(disabled). 실발송 검증. → `history/work/archive/2026-06-10-m3-alerts-adapter.md`
- ✅ R5 합성·플레이북·주문 초안 (2026-06-10): 흐름변수 화이트리스트(계약 거부), 규율 코드 강제(3트랜치 20/50/30·총량상한·손절2종, stop 미제공=폐기), PlaybookStore(append-only), 장중 가드(rc=3)·LLM 장애 P1 알림, cron synth-pm(20:30, disabled). 실거동: 논제 3→비거래 선택(정상 경로). R5-1(논제 적대 라운드 부재) 🟡 등록. → `history/work/archive/2026-06-10-m3-r5-synthesis.md`
- ✅ R5.5 아침 선택기 (2026-06-10): 순수 함수 엔진(`<op><숫자>` 계약, AND, 누락=미충족), approved-only arm(+P1), 장중·휴장 가드, 스냅샷 주입(SEL-1 🟡 — NXT 어댑터 부재=비거래), cron select-am(08:50, disabled). → `history/work/archive/2026-06-10-m3-r55-selector.md`
- ✅ R6 보고 (2026-06-10): Jinja2 모닝(06:50 읽기전용)/저녁(21:00 결재 — 승인 요청·R4 요약·시나리오·P2, 미수집=결측 명시), 분량 가드 7000자(초과=실패+P1), 파일+Telegram 발송, cron report-am/pm(disabled). 실발송 검증. → `history/work/archive/2026-06-10-m3-r6-reports.md`
- R7 평가·캘리브레이션 + 레짐 모니터 (주간)
- 이벤트 감시기(`watch/`) → P0 발화 연결 (서킷브레이커·환율 임계·바이너리 전이·보유 공시)
- KIS 잔고·체결 어댑터 (`KIS_ENABLE_TRADING=false`)

**OPEN_QUESTIONS 🔴**
- KRX 정보데이터시스템 — 시세·투자자별 매매동향 접근/인증 (R3 supply 페르소나 grounding 동시 해소)
- NXT 프리·애프터마켓 데이터
- 증권사 조건부(청산) 주문 API — KIS REAL 키 확보 완료, 청산 인터페이스 스펙 조사 잔여

**운영**
- cron 11개 일괄 활성화 (M3 + alerts 준비 후)
- R2 모델 `.env` 핀(`R2_MODEL`) + 비용 모니터링
- 1Password 'stock / .env' 동기화: `OPENCLAW_GATEWAY_TOKEN`
