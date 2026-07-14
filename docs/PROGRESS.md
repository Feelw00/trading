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
- **R6 보고 가독성 2차 개편** (06-12, 운영자 피드백): R5 시나리오를 산출 시점에 축 구조(`ScenarioAxis`)로 강제(통문단·사후 정규식 재구성 제거), 결재 섹션 자기완결화(종목명+근거 1줄 `Playbook.summary`+발동 조건), ID `<code>`화로 텔레그램 `.buy` gTLD 자동링크 차단. → `2026-06-12-report-readability.md`
- **arm-check 온디맨드 집행 보조** (06-12, P-6): 9~10시 운영자 실행 → 흐름 관측치 수집(KIS 실시간 체결강도·호가·전고회복 — KIS-RT-1 관측 확정) → `selector` 순수 판단 → 흐름변수·트랜치 결정론 해설(`explain.py`) → 스킬 LLM 분석. 판단=코드/분석=LLM 분리(절대금지 #2). SEL-1 부분 해소(premkt만 NXT 잔존), SEL-2(==true 계약 틈) 발견·등록. → `2026-06-12-arm-check.md`
- **포지션 관리 레이어** (06-12, P-8): 보유 테이블(PositionRecord — discuss 조건문·분석 문서 전문 스냅샷, append-only) + 점검(순수 코드: 실시간/EOD 손익·스탑 잔여 거리·시간손절 잔여 거래일 → [정리 검토] 플래그) + `trading.positions` CLI + `/positions` 스킬. arm-check·저녁 보고(§8 "무효화 조건 잔여 거리" 결측 해소)·boot 노출. 잔고 대사(KIS 체결)는 후속. → `2026-06-12-position-layer.md`
- **SEL-2 🟢 boolean 조건 평가** (06-12): selector·explain이 `==true/==false` 흐름변수 조건을 평가(관측치 1.0=참, flowsnap 정합) + R5 프롬프트 문법 가이드. R5 산출(`prev_day_high_reclaim ==true`)이 평가 불가로 빠지던 문제 해소 — 전고 회복 등이 발동 판정에 반영. → `2026-06-12-sel2-boolean-conditions.md`
- **approved 활성 풀 + TTL + 승인 통합** (06-12, P-7): arm-check·R5.5 cron 당일 날짜 조회 → **status=approved + TTL(time_stop_days 거래일) 풀**로 전환 — **날짜 어긋남 버그**(SEL-3 🟢)·다일 셋업 누락 해소. 승인을 저녁 CLI→**아침 arm-check에 통합**(승인 후보 섹션 + "승인 시 발동" 미리보기 + `approve` 동봉). `MarketCalendar.add_trading_days`, `PlaybookStore.active/candidate_playbooks`, `flowsnap` 일원화, `trading.approve` CLI + `/approve` 스킬, 저녁 보고 "검토 후보"로 톤 조정. → `2026-06-12-approved-pool-ttl.md`

**검증 (AC)**: pytest **272 passed**, mypy **0 issues (103 files)**. 운영: 16슬롯 자동 가동 중(06-11 거시 수집을 report 라운드에 내장, macro-am/pm 슬롯 제거).

---

## 미해결 / 다음

**M4 / Phase 1 잔여**
- 이벤트 감시기(`watch/`) → P0 발화 연결 (서킷브레이커·환율 임계·바이너리 전이·보유 공시) — heartbeat 배선 포함
- 리플레이 회귀 테스트 (6/2~6/8 주간, M4 프롬프트 §2)
- 승인 전이(draft→approved) 운영자 도구 (§6 수동 결재 UX)
- KIS 잔고·체결 어댑터 (`KIS_ENABLE_TRADING=false`) — 저녁 보고 집행·포지션 섹션 채움

**데이터 무결성 (2026-07-11)**
- 한 달 미가동(6/12~7/10) 중 시세 16거래일이 조용히 결측 → 백필 + **연속성 가드**(자가 치유·`--check`) 도입,
  스크리너/FactPack의 **침묵 폴백 제거**(히스토리 부족을 0.0/"52주"로 위장하던 경로 → `n/a`·`None`·"미산출").
  1년치 히스토리(262일자) 확보로 `mom_long`·52주 신고가 정상화 → [archive](../history/work/archive/2026-07-11-continuity-guard.md)
- **CAL-1 종결(2026-07-11)**: 관측된 미등록 휴장 9일을 공식 공지와 대조(9/9 일치) + 미래 휴장 5일 등록
  (**2026-07-17 제헌절** 포함 — 관측 자가치유로는 못 잡던 건). `covered_through`+`--check` 만료 경고로
  내년 재발 차단 → [archive](../history/work/archive/2026-07-11-cal1-holidays.md)

**M2~3 잔여(소)**
- ~~R3 grounding 보강(수급 페르소나)~~ → 06-11 해소(KIS flows → FactPack) / R1 일반 게이트 운영 배선(landing→FactRecord 변환 계층)
- R4 실측 생존률·threshold 재캘리브레이션 — 운영 슬롯 데이터 누적 후
- ~~CAL-1: 음력·대체공휴일 KRX 공지 확인~~ → **07-11 종결**(위 데이터 무결성 블록). 잔여는 연 1회 갱신(2027 공지)

**시장시간 (2026-07-11, CAL-3 결정)**
- KRX **애프터마켓 16:00–20:00이 2026-09-14 시행** → §5 "장중" = 정규장 ∪ 애프터마켓(운영자 결정).
  §5 가드가 **정의만 되고 미배선**이던 것을 적발해 `trading.run` 디스패치에 배선(장중 rc=3 스킵) +
  pm 체인 재배치(저녁 결재 보고 21:00→**21:30**) + 매니페스트 회귀 테스트.
  프리마켓은 **2027년 말로 재연기** → SEL-1(NXT 의존) 미해소 → [archive](../history/work/archive/2026-07-11-cal3-after-market.md)

**OPEN_QUESTIONS 🔴**
- NXT 프리·애프터마켓 데이터 (SEL-1·R7-1 흐름 관측치 공유) — KRX 프리마켓 2027 말이라 당분간 유지
- 증권사 조건부(청산) 주문 API — KIS REAL 키 확보 완료, 청산 인터페이스 스펙 조사 잔여

**운영(상시)**
- 매 세션 `drill.py --audit`로 전일 사이클 점검 + claude -p 비용 모니터링(R2/R3/R4 일 2회)
- R2 모델 `.env` 핀(`R2_MODEL`) / 1Password 'stock / .env' 동기화: `OPENCLAW_GATEWAY_TOKEN`
