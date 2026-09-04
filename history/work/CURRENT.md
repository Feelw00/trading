# 현재 작업 (CURRENT)

> 슬림 유지. 완료된 작업은 `archive/YYYY-MM-DD-<slug>.md`로 떨구고 여기엔 한 줄 + 링크만.
> 최종 갱신: 2026-09-04 10:50 (KST)

## 진행 중
- **실투자·예약 현황(9/3 15:25 live 재등록, 토스 실측)**: 실보유 7 = 가이드 7 — 아이퀘스트 38(2,718) ·
  신세계I&C 8(13,008) · 동성케미컬 25(3,760) · 메타바이오메드 20(3,880) · 케이씨피드 32(2,350) · CJ대한통운 2(73,634) ·
  케이디켐 1(9,280), 평가 60.9만원. 토스 OPEN 조건주문 7건 = 저널 alive 7건(다음 매도선, 만료 9/10). 15:23 1차 실행은
  앱 취소 주문 404로 실패(rc=1) → EXEC-12 실사고 ③ 교정 후 15:25 재실행 성공(외부 취소 3건 저널링·P1 3건 적체).
  회귀 여력 산식 v2.14(자기 역사 5년 PBR 밴드 중앙 + 정당 PBR 캡), 심사 원장 159(승인 23·조건부 130·veto 6), 승인
  노출 20. 상세 → [archive](archive/2026-09-03-own-history-pbr-band.md).
- **SCREEN-1 완료(9/3 오후~9/4, policy v2.16, 운영자 결재 (a))**: 관리종목·거래정지·감사의견 비적정 = R4 하드 탈락.
  소스 실측(KIS `inquire-price` 상태 필드 — `mang_issu_cls_code` Y·상태 51/58, `temp_stop_yn`은 지표 아님 · DART
  `accnutAdtorNmNdAdtOpinion` 당기 행 전부·≠적정의견) → 수집기 2종(`collectors/status.py` eod-v3 `status-v3` 4스레드·
  12분 예산 · `collectors/audit.py` weekly-v3 `audit`) → `data/status.sqlite` → `screen.rules.status_filter` +
  `screen.run.load_status_inputs`(신선도 7일, 스크리너 CLI·다이제스트 동일 입력). **전수 실측(9/3)**: KIS 2,670종 — 관리
  167·정지 127·상폐 의심 2 · DART 2,669종 FY2025 — 적정 2,525·비적정 49(의견거절·한정의견)·당기 없음 68(미제출).
  파서 갭 교정: "(당기)" 표기 없는 라벨(샘표식품 "제10기") → 최상위 기수 = 당기, 읽기 시 재판독(재수집 불요).
  **R4 효과**: 다른 게이트 전부 통과했는데 SCREEN-1만으로 탈락 10종(SHD·조일알미늄·원티드랩(한정) 등, 원장 판정 없음) ·
  통과+당기 결측 ⚠ 8종(원장 hold 1: 삼원강재). 실보유 7종 전부 정상·적정. 테스트 +10(수집기 7·필터 3), pytest·mypy 통과.
  9/3 18:00 eod-v3(13분, status-v3 무호출 스킵)·9/4 08:40 guide-orders "유지 7" 정상. 상세 →
  `docs/research/2026-09-03-screen1-status-audit-sources.md` · OPEN_QUESTIONS SCREEN-1 🟢 · POLICY_PARAMS §5 v2.16.
- **관찰 대기(자동 체인, 개입 없이 확인만)**: ① 9/4 18:00 eod-v3 — `status-v3` 첫 실호출 실행(4스레드, 예상 2~3분,
  체인 총 ≤16분 → 18:30 감시 전 종료) ② 9/5(토) 09:30 weekly-v3 — `audit` 첫 cron 실행(DART 2,669콜 ≈ 6분, 같은 접수분은
  무시) · 스크리너 v2.16 필터 첫 cron 적용 · 코어 v2(8축) 첫 cron 산출 · 3차 큐 12 자동 심사(신규 진입분) · 승인 하한
  (여력 <30% → hold) 첫 발동 ③ 이익 보호·사이클 재등록 첫 실발동 · 태깅 스킵 재시도 소진.
- **9/3 오후 결재 6건(v2.15) + SCREEN-1 관리종목·거래정지·감사의견 하드 필터(v2.16) 완료(9/3~9/4)**: 과열 산업
  `paper register` 불가 · 승인 없는 실보유 편입 보류(`EnrollBlocked`) · 매수 운영자 재량(EXEC-14) · KIS 상태·DART 감사의견
  수집기 2종(`data/status.sqlite`, eod `status-v3`·weekly `audit`) · `status_filter` R4 배선. 전수 실측 관리 167·정지 127·
  비적정 49, R4 단독 탈락 10(원장 무판정), 보유 7종 정상. 상세 → [archive](archive/2026-09-04-decisions-v215-screen1-v216.md).
- **관찰 대기(자동 체인, 개입 없이 확인만)**: ① 9/4 18:00 eod-v3 — `status-v3` 첫 실호출 실행(4스레드, 예상 2~3분,
  체인 총 ≤16분 → 18:30 감시 전 종료) ② 9/5(토) 09:30 weekly-v3 — `audit` 첫 cron 실행(DART 2,669콜 ≈ 6분, 같은 접수분은
  무시) · 스크리너 v2.16 필터 첫 cron 적용 · 코어 v2(8축) 첫 cron 산출 · 3차 큐 12 자동 심사(신규 진입분) · 승인 하한
  (여력 <30% → hold) 첫 발동 ③ 이익 보호·사이클 재등록 첫 실발동 · 태깅 스킵 재시도 소진.
- **9/3 오후 결재 6건 반영(policy v2.15)**: ① 과열 산업 = `paper register` 불가(승인 노출·자동 심사 불변,
  `register_block_reason`) ② 심사 승인 없는 실보유 = 편입 보류(포지션·매도 예약 없음, ⚠ 표기 + 신규 P1 1회,
  `EnrollBlocked`) ③ 목표가 자동 반영 없음 확정 ④ 매수는 운영자 재량(EXEC-14, 자동 DCA 보류) ⑤ 매도 예약 다음 선
  1개만 = 의도(P-19 ① 기각) ⑥ 전량 정리는 앱 직접(P-20 ⑤ 기각). 테스트 +3, pytest·mypy 통과. 현 실보유 7종은 전부
  승인·편입 완료라 ②의 행동 변화 없음(다음 08:40 "유지 7" 예상 불변).

## 운영 상태 (상시 확인)
- **🔄 2026-08-26 전략 전면 피벗(P-14) — 설계서 v0.3 비준**: 단타·스윙 폐기 → 장기 사이클·
  가치 투자. 판단=순수 코드(운영자·LLM 종목 심사 배제), 결정 원장은 OPEN_QUESTIONS PIVOT-1~8.
- **❄️ v0.2 스윙 계열 전면 동결**: 감시기·브래킷·EXEC 규율·cron 슬롯 재가동 금지.
  스킬 5종 → `.claude/skills-frozen/`, `ops/openclaw/FROZEN.md` 참조. 자동 매매 없음.
- **환경(8/27 복구 완료)**: python 3.13.13·poetry·openclaw(격리, ChatGPT OAuth)·`.env` 가동.
  PostgreSQL/docker 폐기(PIVOT-8 — SQLite). 상주: tmux `openclaw-trading`(게이트웨이) +
  `trading-reports`(웹 :80) — **재부팅 시 두 스크립트 수동 재기동**(start-gateway·
  start-report-site). ⚠️ .env 변경 = 게이트웨이 재기동.
- **⏳ 운영자 대기**: PIVOT-10 잔여 구성 목록(원유정제·종합반도체 — 토스 해상도 한계, 조선·은행은 v1.0·v1.5로 해소) ·
  §6 결재(비중·DCA·거부권 창 — 페이퍼 관찰 후 세션이 제안). (텔레그램은 9/2 실발송 가동.)
- **⚠️ 실주문 경로 가동(EXEC-12 live, 9/2 14:57)**: `guide-orders` 평일 08:40이 토스 조건주문
  (SELL·지정가)을 실등록한다. 긴급 정지 = `.runtime/exec/KILL` 생성 또는 `.env` GUIDE_ORDERS_MODE=off
  + 게이트웨이 재기동. 매수는 여전히 운영자 수동.

## 최근 완료
- 2026-09-03~04 — **결재 6건 반영(policy v2.15: 과열 등록 제외·승인 없는 실보유 편입 보류·매수 재량 EXEC-14·P-19 ①/P-20 ⑤
  기각) + SCREEN-1 소스 실측(KIS 상태 필드·DART 감사의견, 양성 대조군 확정)·수집기 2종·R4 하드 필터 v2.16(단독 탈락 10)·
  당기 라벨 파서 교정** → [archive](archive/2026-09-04-decisions-v215-screen1-v216.md)
- 2026-09-03 — **회귀 여력 산식 교체 v2.13(자기 역사 PBR 밴드)·v2.14(정당 PBR 캡) + 원장 초기화·전량 재심사(승인 20)
  + 가이드 재편(7종=실보유)·조건주문 live 재등록 + EXEC-12 실사고 3건 교정 + 업계 목표주가 관행·화신·샘표/케이디켐 조사** →
  [archive](archive/2026-09-03-own-history-pbr-band.md)
- 2026-09-02 — **승인 노출 하한(여력 ≥ +30%, LF 보류)·페이퍼 편입 원칙(실보유 자동 편입·자동 등록
  폐지)·목표가 괴리 표기(⚠ ±15%·retarget)·/picks 7열** → [archive](archive/2026-09-02-approval-upside-gate-paper-enroll.md)
- 2026-09-02 — **ALERT-1 실행 보고·미발화 감시(cron 4잡) + EXEC-12 가이드 매도 예약 live(조건주문
  5건 실등록·수량 변경 시에만 재등록·시작가 불변) + 페이퍼=가이드 정합 + /paper·/picks 재구성
  (원장·대기 큐 분리, 대한약품 캡 결함 해소)** → [archive](archive/2026-09-02-alert1-exec12-guide-orders.md)
- 2026-09-01~02 — **코어 v2 결재 체인 완결(policy v1.7~v2.10): 스크리너 8축(안정·환원·
  이익질·이익방향·역성장)·국면 v2(SLOWING·전이 규율)·지배주주 PBR(COLLECT-6)·DART 환원
  수집기·/picks 기대 분해·심사 원장(만료·태그 승격)·전면 자동화·페이퍼 100주 5단 매도·
  이익 보호·사이클 재등록·매매 가이드 전환 + 실투자 개시(5종)** →
  [archive](archive/2026-09-02-core-v2-review-paper-guide.md)
- 2026-08-31 — **P-18 구현(우선순위 반전, PIVOT-11 🟢): 가치 코어 게이트(발동 존 폐지·
  병행 가치 기준 OR)·사이클은 도구(과열 ⚠ 플래그·우선순위 정렬)·유니버스 전 상장(재무 DB
  420→2,669종목·10년 백필 17,721건·KIS 업종 태깅 2,465종)·R4 평가 2,672 통과 546(⚠235)·
  R3 38개 산업·보고/웹 재편** → [archive](archive/2026-08-31-p18-value-core.md)
- 2026-08-31 — **PIVOT-10 멤버십 매핑(policy-v1.5): 운영자 토스 테마 42파일(1,039행) →
  화이트리스트 9개 산업 전면 큐레이션(고유 184종목)·다중 소속 자연 허용·v1.3 확정 오버라이드
  보존(5건)·P-17 A항 순도 교정 이행(LIG넥스원·한미반도체·두산에너빌리티·대한항공 이탈)·
  신규 141종목 재무 10년 백필(DB 420종목)·weekly-v3 재산출(화학(큐) 하강 전환 — R4 통과 1→0)** → [archive](archive/2026-08-31-pivot10-membership-mapping.md)
- 2026-08-29~31 — **P-16 웹 가시성 개편 V1~V3: 용어 사전 호버·국면 5색 배지·대시보드 결정 카드 3장·통과 테이블 분리·전 차트 SVG 툴팁·도넛/히트맵·주간 보고서 변화 중심 재설계 + 포트 80 도메인 접속** → [archive](archive/2026-08-31-p16-web-visibility.md)
- 2026-08-26~28 — **v0.3 전면 피벗: 비준·마이그레이션 → Phase 0(복구·대사: 계좌 비어 있음)
  → Phase 1(시세 1,385일·DART 10년·밸류에이션 222) → Phase 2(온도계·리플레이 재현·검증 PASS·
  policy-v1.2) → Phase 3(cron 2잡 무인 가동·R4 페이퍼 통과 1·심사 패킷·공매도/대차/신용 축적)
  + 테일넷 웹(대시보드·종목·산업·보고서·자료실). 실사고 교정 3건(빈 ACCOUNT_TYPE·pytest 실키
  행·드릴 T+1 오탐)** → [archive](archive/2026-08-28-v03-pivot-phase3-web.md)
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

> 2026-09-03 15:40 재검토·갱신. Phase 2·3 완료 항목(R3 엔진·policy-v1.0·R4 페이퍼 실측·§5 결재·cron 가동·R4.5 심사 패킷·
> PBR 밴드 일별 정밀화 = v2.13 일별 밴드로 해소)은 제거. 근거: `docs/PROPOSALS.md` P-19·P-20, `docs/OPEN_QUESTIONS.md`
> 🟡 항목, 9/3 아카이브 "남은 약점·후속". 스윙 계열 후보는 PIVOT-1로 전부 폐기(이력은 git·archive).

**A. 운영자 결재 대기 — 세션은 결정 전 인코딩 금지, 판정·대안 제시 후 반영**
> 9/3 15:40 결재 6건 반영 완료(policy v2.15 · OPEN_QUESTIONS GUIDE-1 🟢·EXEC-14 🟢 · PROPOSALS P-19 ① 기각·P-20 ① 채택·⑤ 기각).
1. **COLLECT-5 후속 결재** — ①② 실측(B-3) 후 리츠 면제 해제 · 분할(물적) 배제 승격 여부. 실측 전 결정 불가.
2. **P-20 ③ COE 10%·g 1% 재캘리브** — 보류. 도달률 데이터(C-1) 축적 후.
3. **PIVOT-10 잔여** — 원유정제·종합반도체 구성 목록(토스 해상도 한계로 v1.3 구성 유지 중). 분기 스냅샷 교체 시 재검.
   v1.3 유지 확정으로 닫아도 됨.
4. **SCREEN-1 후속 — 보유 종목 상태 전이 알림** — 실보유가 정상→관리·정지·감사의견 비적정으로 바뀔 때 §7 P0(veto 창)로
   올릴지, P1(실행 보고 꼬리)로 둘지. 현재는 스크리너 탈락 사유·guide-orders 보고에만 나타남(자동 청산 없음). 세션 권고:
   P1로 시작(보유 7종 일일 스냅샷은 이미 수집), 청산 경로 연결은 §6 결재와 함께.

**B. 세션 작업 후보 — 결재 불요(데이터 교정·수집·계측), 우선순위순**
1. ~~SCREEN-1 관리종목·감사의견·거래정지 하드 필터~~ → **완료(9/3~9/4, v2.16)**. 잔여: KIND(지정 사유·일자) 실측은
   필요 시 · `mrkt_warn_cls_code` '00' 외 값(투자주의·경고·위험)은 관측되면 어휘 박제 후 표기 검토.
2. **P-20 ⑥ 배당수익률 오염 교정** — DART 액면 배당률이 시가 수익률 자리(흥국 56%). `yield_pct > 20%` 전수 스캔 →
   파서 행 선택 교정. 판정 미개입, /picks 표시만 오염.
3. **COLLECT-5 ①② 실측** — 리츠 분배금이 `alotMatter`에 잡히는지 · 분할 인적/물적 구분(공시 본문 또는 별도 API).
4. **P-20 ④ + COLLECT-6 후속** — 지배주주지분 10년 백필 후 밴드 분모 현재·과거 동시 승격 · PER 분모 지배주주 귀속
   순이익 · 비12월 결산 4/1 적용 규칙 정밀화(결산월 수집).
5. **P-19 ④ 태깅 영구 스킵 129종 박제** — `source="none"`로 일 129콜 절감. 재시도 소진 확인과 묶음.
6. **P-19 ②③⑤** — 실체결 이력 가이드 표기 · 큐 산업 비례 캡(순환 1~2주 관찰 후) · 금융 몰림이 COLLECT-6 효과인지 추적.
7. **PIVOT-3 잔여** — 자기주식 취득≠소각 구분 API · 밸류업 공시 채널(KIND?) 확정 후 어댑터. 그 전 스텁 유지.
8. /paper 다음 매도선(페이퍼 재생)과 예약 열(브로커 실체결) 표기 혼동 — 관찰 후 필요 시 보강.

**C. R7·장기(데이터 축적 후)**
1. **P-20 ② 목표가 도달률 계측(R7)** — 페이퍼 매도선 터치 원료. 도달 여부·소요일·최대 접근률 박제, 벤치마크(미 38%·
   한 19%) 비교 — v2.14 산식 효과의 유일한 검증. 분기 R7 설계 시 포함.
2. veto 태그 규칙 승격 후보(이익붕괴 ×2·데이터이상 ×2) — R7 데이터 후.
3. 월간 결재 보고·분기 R7 cron 슬롯 — §6 결재·첫 분기 도래 시 추가.
4. PIVOT-2 🔴 사이클 실물 지표 소스 — 보강 축으로 강등(PIVOT-7 ②), 급하지 않음.

**D. 운영자 몫·연 1회**
- 맥북 `~/Downloads/env.example.txt` 삭제(키 평문 잔존) — 이 기기(Lucas-mini)엔 없음, 맥북은 미확인.
- 2027년 휴장일 갱신 — `krx_holidays.json` `covered_through=2026-12-31` 만료 시 `--check` ⚠️ 경고.
- 재부팅 시 tmux 2 스크립트 수동 재기동(start-gateway·start-report-site) — 운영 메모.
