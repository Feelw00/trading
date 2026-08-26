# 현재 작업 (CURRENT)

> 슬림 유지. 완료된 작업은 `archive/YYYY-MM-DD-<slug>.md`로 떨구고 여기엔 한 줄 + 링크만.
> 최종 갱신: 2026-08-26 (KST)

## 진행 중
- **v0.3 Phase 1 (밸류에이션·히스토리 기반)** — 코드 골격 완료(계약·R2 계산기·수동 입력·백필
  함수, 커밋 4d58684). **잔여: 실데이터 적재**(.env 필요 — DART 백필 `--backfill-years` 실행,
  DART 10년 커버리지 실측 → PIVOT-3④ 백필 연수 확정, `python -m trading.valuation` 첫 산출)
  + 환원·거버넌스 수집(PIVOT-3 API 확정 후).

## 운영 상태 (상시 확인)
- **🔄 2026-08-26 전략 전면 피벗(P-14) — 설계서 v0.3 비준**: 단타·스윙 폐기 → 장기 사이클·
  가치 투자. 판단=순수 코드(운영자·LLM 종목 심사 배제), 결정 원장은 OPEN_QUESTIONS PIVOT-1~8.
- **❄️ v0.2 스윙 계열 전면 동결**: 감시기·브래킷·EXEC 규율·cron 슬롯 재가동 금지.
  스킬 5종 → `.claude/skills-frozen/`, `ops/openclaw/FROZEN.md` 참조. 자동 매매 없음.
- **환경**: python 3.13.13·poetry 복구 완료, pytest·mypy 기준선 통과. PostgreSQL/docker 폐기
  (PIVOT-8 — SQLite). **`.env` 없음 · openclaw 미설치 · cron 미등록**(의도된 상태).
- **⏳ 운영자 입력 대기 2건**: ① `gh auth login`(브랜치 `pivot/v0.3-longterm` 4커밋 푸시 대기)
  ② 1Password `.env` → bootstrap 완주 + **브로커 대사**(`ops/reconcile_broker.py` — 7/15 이후
  방치된 피에스케이 5주·S-Oil 4주·OCO 브래킷 실측, PIVOT-6).

## 최근 완료
- 2026-07-14~15 — **가드 전면 보수 + live 첫 실사이클: 감사(A/B묶음 수정)·SEL-4(reclaim 폐지·이격 주입)·EXEC-8(밴드·재진입)·EXEC-9(시간손절 14:30)·EXEC-10(아침 규율·모멘텀)·EXEC-11(저잔고·교체 Δ1.5%p)·청산 큐·토스 실측 3건(OCO 순서·status·빈 본문). 첫 자동 익절(뉴파워 +3.6%). 잔여: A5·C2·C3·D1** → [archive](archive/2026-07-15-guard-overhaul-live-cycle.md)
- 2026-07-14 — **부팅 재구조화: work-boot를 읽기 전용 요약으로(신선도 자동 수집·drill --audit 제거 — 갭은 수동 /collect), session.md 동기화. 유령 중복 `.claude/commands/collect.md` 발견(삭제 미결)** → [archive](archive/2026-07-14-boot-readonly-summary.md)
- 2026-07-14 — **live 전환 사고 대응: 7/13 지시("소액 진입") dry-run 격하 인코딩 사고 + 게이트웨이 env 스냅샷 사고 → 운영자 "dry run 다 빼"로 live 전환(10:05). 유령 포지션 정리·dedup mode 분리·잔여 R:R 가드(EXEC_MIN_RR)·R5 2단 사다리 기본 강제·/end 스킬 신설** → [archive](archive/2026-07-14-live-transition.md)
- 2026-07-14 — **boot 실시간 싱크(운영자 지적): 부팅 백드롭이 폭락 미반영 7/10 EOD로 나감 → `trading.regime` CLI·`approve --pool` 신설, boot/collect-macro/work-boot 스킬에 실시간 오버레이·감시 풀 우선·stale 라벨 배선** → [archive](archive/2026-07-14-boot-live-sync.md)
- 2026-07-13 — **EXEC-1: 자동 집행 전환 — 자동 승인+거부권(`--veto`), 토스 지정가 주문+조건부 손절 어댑터(시장가 경로 부재), 집행기(dry-run 기본·킬스위치), arm-watch 배선. 2차 개정: 투자 정책 캡 제거 — 사이징=가용액×R5 cap 계수, 최소 1주 보장, 계단식 허용, 폭주 가드(일 5건)만 잔존. 미국 주식은 KR live 검증 후. **3차(P-11 Stage B): OCO 익절·R5 풀 8개+대안 셋업·sector_ignition 흐름변수** · **4차(EXEC-2): 계단식 청산 — R5 지정 targets/soft_stop, 브래킷 상주+manage_exits(본전 상향·경고 축소)** · **5차(EXEC-3): 애프터 청산 전용 감시+레그 체결 확인** · **6차(EXEC-4): 주당 200만 상한+갈아타기+잔고 부족 재시도** · **7차: 첫 실산출 보정(당일 한정 승인+veto·셋업 붕괴 가드)** · **8차: 승인 P0 다이제스트화** · **9차(EXEC-5): 풀 비례 배분** · **10차(EXEC-5 개정+EXEC-6): 동적 몫(잔여 풀 분모)+회수 사다리(갈아타기→부분 트림 50% 상한·러너 보호)**. dry-run D1=7/14. 토스 키·IP·계좌 연결 완료** → [archive](archive/2026-07-13-exec-autotrade.md)
- 2026-07-13 — **토스 Open API 전수 조사(조건주문 OCO/OTO=§6 청산 정합, NXT 캘린더, 업종·수급·웹소켓 없음) + 섹터 기준 KRX 공식 업종 전환(SECT-1 🟢: `kis-bstp-v1` 최우선, 231/232 태깅, 잔여=계약층 taxonomy) + P-10 제안(감시기 기동 알림 — 7/13 리마인더 침묵=빈 approved 풀 진단)** → [archive](archive/2026-07-13-krx-sector-switch.md)
- 2026-07-12 — **장중 발동 감시기(watch/arm_watch): approved 풀 arm 조건 실시간 감시 → 충족 순간 P0(초안·일자당 1회) + 14:40 마감 정리 리마인더. cron 09:00 기동·12:00 재기동(18잡). 운영자 거래 창(9~15시) 3터치 프로토콜 확립. M4 이벤트 감시기 골격 첫 인스턴스 — 서킷브레이커·환율 임계는 같은 골격에 후속** → [archive](archive/2026-07-12-arm-watch.md)
- 2026-07-11 — **P-9 3단계: 스윙 트리거 발화분 → R3~R5 자동 승격(`reason_news` max_swing=5, "스윙 승격 근거" 슬라이스 주입 — 촉매 없어도 grounded). 트리거→논제→플레이북→결재 루프 완결. 7/13(월) 저녁 체인이 첫 실전** → [archive](archive/2026-07-11-p9-stage3.md)
- 2026-07-11 — **P-9 2단계: R6 저녁 보고 "스윙 기회" 섹션(3단 명시성) + R7 트리거 적중률 채점(`SwingTriggerScore`, 임계 튜닝 루프 완성) + flows 수집을 스윙 유니버스로 확장(수급 축 커버리지 해소) + swing DB 테스트 격리. 첫 실채점은 7/18 eval-sat(7/17 휴장 주의)** → [archive](archive/2026-07-11-p9-stage2.md)
- 2026-07-11 — **P-9 1단계: 스윙 스크리너(`swing.py` 4축 유니버스+기회 트리거, 순수 코드) + DART 재무 캐시(`fins.py`, 240/243) + daily-eod 체인 배선(sector-llm·fins·swing best-effort). 첫 실행 유니버스 30·트리거 11. 뉴스 stale 가드(공백 오독 차단). 잔여: R6 섹션·R7 채점·수급 커버리지** → [archive](archive/2026-07-11-p9-swing-screener.md)
- 2026-07-11 — **P-2 LLM 폴백 분류기(`sector_llm.py`, `llm-fallback-v1` 최후순위 + 환각가드 코드 재검증) — 미분류 151→28, 상위 30 미분류 2종만 잔존. /collect 4단계 배선. P-9 도메인 축 선행 조건 완료 → 다음은 P-9 구현** → [archive](archive/2026-07-11-p2-llm-fallback.md)
- 2026-07-11 — **P-1 섹터 taxonomy 확장(26→29: 해운·물류/운송/레저·카지노) + KSIC 실측 규칙 7종·큐레이션 8종(금융지주 일괄)·`--retag` 소급 — 상위 30 미분류 22→16. P-9(스윙 스크리너) 방향 합의, 선행 조건 P-1 완료 → 다음은 P-2** → [archive](archive/2026-07-11-p1-sector-taxonomy.md)
- 2026-07-11 — **CAL-3 결정·구현: §5 "장중" = 정규장 ∪ 애프터마켓(16:00~20:00, 9/14 시행). §5 가드가 미배선이던 것 적발 → `trading.run`에 배선(rc=3 스킵). pm 체인 재배치(score 20:02·verify 20:15·reason 20:32·synth 21:05·**저녁 결재 보고 21:00→21:30**) + 게이트웨이 sync. 프리마켓은 2027 말 재연기 → SEL-1 미해소** → [archive](archive/2026-07-11-cal3-after-market.md)
- 2026-07-11 — **CAL-1 종결: 관측 휴장 9일 공식 공지 대조(9/9 일치) + 미래 휴장 5일 등록(2026-07-17 제헌절·8/17·9/24-25·10/5). `covered_through`+`--check` 만료 경고로 내년 재발 차단. CAL-3(거래시간 연장 9/14) 신규 등록** → [archive](archive/2026-07-11-cal1-holidays.md)
- 2026-07-11 — **시세 갭 백필(16거래일) + 1년치 히스토리(262일자) + 연속성 가드(자가 치유·`--check`·`no_data_days` 박제) + 스크리너/FactPack 폴백 침묵 제거(`mom_*_ok`·`float|None`·R3 "미산출"). CAL-1 실체 적발(달력 미등록 휴장 9일)** → [archive](archive/2026-07-11-continuity-guard.md)
- 2026-06-12 — **포지션 관리 레이어(P-8): PositionRecord(계획 스냅샷 박제)+PositionStore+점검(손익·스탑 거리·시간손절→[정리 검토])+positions CLI+/positions 스킬. arm-check·저녁 보고(§8 무효화 잔여 거리 결측 해소)·boot 배선** → [archive](archive/2026-06-12-position-layer.md)
- 2026-06-12 — **R5 수동 `--force` 장중 실행(CAL-2 🟢): cron 자동은 장중 가드 유지, 수동 CLI만 `--force`로 우회(입력 EOD·산출 draft·아침 승인이라 충동 집행 차단 유지). `python -m trading.synth_playbooks --force`**
- 2026-06-12 — **R5 조건을 관측 가능 흐름변수로 제약(SEL-1 우회): `flowsnap.OBSERVABLE_FLOW_VARS`(KIS 자동 3종)를 R5 프롬프트에 주입 — premkt 등 미수집 변수로 영영 미발동 플레이북 나오는 것 차단. 다음 R5(장 마감 후/밤)부터 적용**
- 2026-06-12 — **SEL-2 🟢: selector가 boolean 흐름변수 조건(==true/==false) 평가 — R5 산출과 selector 숫자 문법 불일치 해소(전고 회복 등 발동 판정 반영), explain·R5 프롬프트 정합** → [archive](archive/2026-06-12-sel2-boolean-conditions.md)
- 2026-06-12 — **approved 활성 풀+TTL+승인 통합(P-7): arm-check·R5.5 cron 날짜 라벨→status·TTL 전환(날짜 어긋남·다일 셋업 해소, SEL-3 🟢), 승인을 아침 arm-check에 통합(승인 후보 섹션+`approve` 동봉, 저녁 CLI 강제 제거), `trading.approve` CLI+`/approve` 스킬** → [archive](archive/2026-06-12-approved-pool-ttl.md)
- 2026-06-12 — **arm-check(P-6): 9~10시 온디맨드 발동 판단(순수코드)+흐름변수·트랜치 해설+LLM 분석. KIS 실시간 TR(ccnl 체결강도·호가) 관측 확정, flowsnap, explain 모듈** → [archive](archive/2026-06-12-arm-check.md)
- 2026-06-12 — **R6 보고 가독성 2차 개편 — R5 시나리오 축 구조화(통문단 해소)·결재 근거 1줄 배선·종목명 병기·ID `<code>`화(텔레그램 .buy 자동링크 차단)** → [archive](archive/2026-06-12-report-readability.md)
- 2026-06-11 — **P-5 DiscussPack: 토론 컨텍스트 사전 조립+캐싱(discuss 개편 — 수급 포지션·뉴스 R2→R4 검증·캐시 버전)** → [PROPOSALS P-5](../../docs/PROPOSALS.md)
- 2026-06-11 — **수급 해소: KIS 투자자매매동향 TR 확정+flows 파이프라인(FactPack R3 grounding) + 거시 수집 report 라운드 내장(16슬롯) + boot 자동수집화** → [archive](archive/2026-06-11-kis-investor-flows.md)
- 2026-06-11 — **R6 보고 가독성 재설계(결정 우선) + Telegram HTML 서식 통일(보고·P0·P1)** → [archive](archive/2026-06-11-telegram-format.md)
- 2026-06-11 — **첫 자동 사이클 점검 + drill.py + 트리거 아키텍처 3단 진화(절대경로→프롬프트→fire-and-forget/setsid+로컬모델). pm 풀 드릴 10잡 검증** → [archive](archive/2026-06-11-first-auto-cycle-audit.md)
- 2026-06-10 — **M3 완결(alerts·R5·R5.5·R6·R7) + cron 18개 enable** → PROGRESS M3 블록 + archive 5건
- 2026-06-10 — **M2 마무리 슬라이스 4종 + R4 실검증 → 결함 2건 수정·재검증** → [archive](archive/2026-06-10-m2-wrapup-slices.md)
- 2026-06-10 — **M2 GitOps 부트스트랩 · 격리 OpenClaw · R0~R4 실거동 검증** → [archive](archive/2026-06-10-m2-bootstrap-validation.md)
- 2026-06-09 — **P-4 뉴스 촉매 파이프라인 + 보조 슬라이스** → [archive](archive/2026-06-09-news-catalyst-pipeline.md)
- 2026-06-08 — **디스커버리 파이프라인 + 데이터소스 + boot/수집 스킬** → [archive](archive/2026-06-08-discovery-pipeline.md)
- 2026-06-08 — **M1 골격·데이터 계약 5종·journal·리플레이** → [archive](archive/2026-06-08-m1-skeleton.md)

## 다음 후보 (우선순위)

> ❄️ 2026-08-26 v0.3 비준(PIVOT-1)으로 **이 섹션의 스윙 계열 후보 전부 폐기**(가드 감사 A5~D1·
> P-13·drill 감사·M4 잔여·NXT/애프터 실측 등 — 이력은 git·archive 참조). 큰 줄기는 설계서
> v0.3 §10 로드맵.

**운영자 입력 즉시 (blocked 해제)**
1. `gh auth login` → `pivot/v0.3-longterm` 푸시.
2. `.env`(1Password) → `bash ops/bootstrap.sh`(cron sync 제외) → **브로커 대사**
   `poetry run python ops/reconcile_broker.py` → 잔존 조건주문 정리·기존 보유 처리 결정.

**Phase 1 잔여 (.env 후)**
3. DART 연간 백필 실행(`python -m trading.collectors.fins --backfill-years 10`) →
   커버리지 실측으로 PIVOT-3④ 백필 연수 확정 → `python -m trading.valuation` 첫 실산출 →
   Phase 1 AC 검증(전 종목 ValuationRecord + 결측 정직 표기 + 공시 실측 대조 N종목).
4. 환원·거버넌스 수집 — PIVOT-3 ①② API 공식 문서 확정 후 어댑터(그 전 스텁 유지).

**Phase 2 준비 (병행 가능)**
5. 섹터 히스토리 밴드 산출기(연간 시계열 → PBR·마진·매출 밴드 percentile) — 백필 데이터 축적 후.
6. 화이트리스트 큐레이션 + 실물 보강 축 매핑표 초안 → 운영자 결재(PIVOT-5).
   실물 지표는 수동 입력 채널(`python -m trading.manual_input`)로 즉시 가동 가능.

**연 1회 운영 (유지)**
- 2027년 휴장일 갱신 — `krx_holidays.json` `covered_through=2026-12-31` 만료 시 `--check` ⚠️ 경고.
