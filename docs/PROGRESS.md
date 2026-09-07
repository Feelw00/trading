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

## live 가동 · 가드 전면 보수 (2026-07-14~15)

live 전환(EXEC-1) 직후 이틀간 집행 계층 전면 보수: 가드 일괄 감사(A/B묶음 수정 — 브래킷
원자성·모드 오염·체결 동기화), 운영자 결정 6건(SEL-4 등급형 회복, EXEC-8 진입 밴드·재진입,
EXEC-9 시간손절 창, EXEC-10 아침 규율·모멘텀, EXEC-11 저잔고·교체 문턱) 구현, 토스 API
실측 교정 3건. **live 첫 사이클 완주**(테스트 매수→첫 자동 익절→청산 큐).
→ 상세: [archive/2026-07-15-guard-overhaul-live-cycle.md](../history/work/archive/2026-07-15-guard-overhaul-live-cycle.md)
잔여: 감사 A5·C2·C3·D1, 분할 OCO 실측 게이트, P-13(수동 매수 자동 편입).

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

---

## v0.3 — 장기 사이클·가치 피벗 (2026-08-26 비준 ~ 08-28 Phase 3 가동)

스윙 체제 전면 폐기(운영자 결정 — 원장 OPEN_QUESTIONS PIVOT-1~10, 정책 POLICY_PARAMS v1.2).
판단 전부 순수 코드(사람·LLM 종목 심사 배제), 저장 SQLite 공식화, 브로커 토스 단일.

- **Phase 0** 복구·대사 완료 — 브로커 실측 보유 0·조건주문 0(정리 대상 소멸).
- **Phase 1 AC 충족** — 시세 2021~ 전 기간(1,385거래일)·DART 연간 10년(222종목)·
  ValuationRecord·섹터 3축 밴드(PBR 7y 창 — 소스 커버리지 실측 한계 명시).
- **Phase 2 AC 충족** — 온도계(23산업)·리플레이 재현(전기·전자 23→25)·검증 사이클
  운송·창고 2024 PASS·화이트리스트/R4 임계/가치 함정 방어 결재.
- **Phase 3 가동 중** — cron 2잡(첫 자동 발화 ok)·R4 페이퍼(통과 1: 금호석유화학)·
  심사 패킷(Fable 5, bear 의무)·공매도/대차/신용 축적·주간 보고 + 테일넷 웹
  (대시보드·종목·산업·보고서·자료실). 집행 경로 없음.
- 미해결: §6 결재(비중·DCA·거부권 창) 후 R5/월간 보고 → Phase 4는 페이퍼 관찰 1~2개월 +
  운영자 명시 전환. 상세 → [archive](../history/work/archive/2026-08-28-v03-pivot-phase3-web.md)
- **P-16 웹 가시성 개편(8/29~31)** — 용어 호버·국면 배지·결정 카드·차트 툴팁·도넛/히트맵·
  보고서 변화 중심 재설계, 포트 80 도메인 접속. 상세 → [archive](../history/work/archive/2026-08-31-p16-web-visibility.md)
- **코어 v2 · 심사 원장 · 매매 가이드 · 실투자 개시(9/1~2, policy v1.7~v2.10)** —
  스크리너 8축(안정·환원·이익질·이익방향·역성장)·국면 v2·지배주주 PBR·DART 환원 수집기·
  /picks·심사 원장(자동 심사 rule-v1)·페이퍼(100주·5단 매도·이익 보호·사이클 재등록)·
  /paper 매매 가이드 전환. **실투자 병행 개시(승인 5종 각 100주)** — 집행은 여전히 수동,
  자동 매매 없음. 검증: pytest 62·mypy 137 clean.
  상세 → [archive](../history/work/archive/2026-09-02-core-v2-review-paper-guide.md)
- **ALERT-1 · EXEC-12 — 실행 보고·미발화 감시 + 가이드 매도 예약 live(9/2)** — cron 5잡
  (eod/weekly + check 2 + guide-orders 08:40), 체인 종료 시 텔레그램 1통(P1 동봉), 토스 조건주문
  (SELL·지정가·상방 감시가만 — 헌법 금지 3 부분 해제) 5건 실등록·수량 변경 시에만 재등록·
  시작가 불변. **Phase 4 매도 선행 — 매수는 수동, 진입 자동화(DCA)는 §6 결재 후.** /paper·
  /picks 재구성(원장·대기 큐 분리). 검증: pytest 전체·mypy 208 clean.
  상세 → [archive](../history/work/archive/2026-09-02-alert1-exec12-guide-orders.md)
- **회귀 여력 v2.13/v2.14 · 결재 6건(v2.15) · SCREEN-1 하드 필터(v2.16) (9/3~9/4)** — 목표가 앵커를 자기 역사
  5년 PBR 밴드 중앙 + 정당 PBR 캡으로 교체·원장 초기화 재심사(승인 20)·가이드 7종=실보유 7종; 과열 산업 가이드 등록 제외·
  승인 없는 실보유 편입 보류·매수는 운영자 재량(자동 DCA 보류); 설계서 R4 탈락 필터 중 미구현이던 관리종목·거래정지·감사의견
  비적정을 **소스 실측(KIS `inquire-price` 상태 필드·DART 감사의견 API) 후 적용**(수집기 2종 → `data/status.sqlite`,
  단독 탈락 10). 검증: pytest 전체·mypy 215 clean.
  상세 → [archive](../history/work/archive/2026-09-03-own-history-pbr-band.md) ·
  [archive](../history/work/archive/2026-09-04-decisions-v215-screen1-v216.md)
- **결재 3건(v2.17) + 배당수익률 액면 오기재 가드 + 추가 매수 반영 (9/4)** — 보유 종목 상태 전이 P1(`holding_status.py`,
  eod/weekly 끝 감시·중복 방지)·PIVOT-10 잔여 종결·eod-v3 트리거 턴 빈 답변 교정(exec stdio 분리, cron 5잡 재등록); DART 배당
  수익률 액면 오기재 12종 23행 가드(판정 미개입, /picks 표시 교정); 운영자 추가 매수(7종 수량 증가, 평가 61→163만원) → 조건주문
  7건 재등록(시작가 불변). 검증: pytest 전체·mypy 217 clean.
  상세 → [archive](../history/work/archive/2026-09-04-holding-status-p1-dividend-guard.md)
- **COLLECT-5 종결 — 리츠 면제 해제·접수분별 배당 저장 · 분할 인적/물적 구분(v2.18) (9/4)** — 실측(리츠 22/23종 반기·분기 결산 →
  접수분 2~4건, 첫 접수분만 저장돼 연간 배당 50~75% 과소 · 분할방법은 DART `cmpDvDecsn` `dv_mth` 원문으로 59개 기업 전수 분류) →
  운영자 결재 (a)(a) → 같은 날 구현·재수집(518종·접수분 2,573건, 결정 56건). 인적분할 강등 해제로 코어 복귀 5종, 물적 배제 승격은 기각
  (헌법 표적 = 물적분할 후 자회사 상장). 검증: pytest 709·mypy 217 clean.
  상세 → [research](research/2026-09-04-collect5-reit-dividends-split-method.md) ·
  [archive](../history/work/archive/2026-09-04-collect5-v218-reit-split.md)
- **P-20 ④ 지배주주 기준 완결(v2.19) (9/4 오후)** — 연간 전체 재무제표 1콜 백필(지배주주지분·귀속 순이익·접수일, 11,000콜·적재
  8,752·잔여 4,871은 weekly 갭 채움) → 밴드 분모 전연도 승격(원장·큐 122/169종)·PER 귀속 분모(변경 860, 고PER 교차 16)·as-of
  적용일 = 접수일 다음날. 발견: 정당 PBR 캡 ROE 기준 혼합 → P-20 ⑧ 결재 안건. 검증: pytest 715·mypy 217 clean.
  상세 → [archive](../history/work/archive/2026-09-04-p20-4-owner-equity-backfill.md)
- **P-20 ⑧ 캡 ROE 기준 정합(v2.20) (9/4 오후)** — 승격 밴드의 정당 PBR 캡 ROE = 귀속 순이익÷지배주주지분 5y 중앙(비지배 ≤0.1%는
  연결 순이익 사용). 캡 ROE 지배주주 122/170, 성우하이텍 승인 보류 파생. pytest 718·mypy clean. 상세 → 위 P-20 ④ archive §6.
- **P-19 ④ KRX 업종 없음 129종 박제 (9/4 오후)** — 재시도 소진(cron 3회 129/129) + 실호출로 "정상 응답·업종명 공백" 확인 →
  `classify_krx`가 정상 응답+업종 없음을 `source="none"`으로 박제(호출 실패는 재시도 유지) · `--retry-pinned`. 수동 실행 129 박제,
  재실행 KIS 콜 0. pytest +3·mypy clean. 상세 → [archive](../history/work/archive/2026-09-04-p19-4-tagging-pin-none.md)
- **EXEC-14 영구 — 매수 자동화 영구 누락 (9/7)** — 운영자 결정("자금이 언제 추가될지 모른다"). 설계서 헤더·§1·§3 R5·§5·§6·
  §10 Phase 4·§11·부록 B 개정 주석, `portfolio/` 신설 취소, POLICY_PARAMS §6 매수 파라미터 폐기. Phase 4 잔여 = 분기 R7·
  논제 붕괴 청산 경로(별도 결재). 같은 날 policy v2.21(보유 상태 해제 P2·weekly 업종 박제분 재시도, pytest 725·mypy clean).
