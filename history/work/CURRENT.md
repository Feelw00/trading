# 현재 작업 (CURRENT)

> 슬림 유지. 완료된 작업은 `archive/YYYY-MM-DD-<slug>.md`로 떨구고 여기엔 한 줄 + 링크만.
> 최종 갱신: 2026-07-11 (KST)

## 진행 중
- _(없음 — 다음 세션 진입 시 채움)_

## 운영 상태 (상시 확인)
- **cron 16슬롯 자동 가동 중** (6/10 저녁~, 6/11 macro-am/pm 슬롯 제거 — 거시 수집은 report-am/pm 라운드에 내장): fire-and-forget(setsid)+로컬 트리거(qwen2.5:3b)+잡별 로그(`.runtime/logs/cron/`). 보고·알림은 Telegram HTML.
- 세션 진입 시 점검: `poetry run python ops/openclaw/drill.py --audit` (전일 사이클 PASS/WARN/FAIL) + 라운드 실패 P1 알림 수신 여부.

## 최근 완료
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

마일스톤 큰 줄기는 `docs/PROGRESS.md` "미해결 / 다음" 참조. 작업 단위 후보:

**다음 세션 첫 작업**
1. `drill.py --audit` — 게이트웨이 재기동(7/11 18:18)·codex 플러그인 비활성화 이후 **첫 무인 사이클** 점검.
   (7/11 감사분은 FAIL 14/no-run이었으나 원인은 그 시각까지 게이트웨이 다운 — 아키텍처 결함 아님)
2. **P-2 미분류 LLM 폴백 분류기** — P-9(스윙 스크리너, 방향 합의됨)의 도메인 축 선행 조건 잔여분.
   미분류 151종의 최대 덩어리는 혼재 코드(649 다각화 지주·292 장비·262 전자부품) → LLM 분류 정공법.
   완료 후 P-9 구현 착수(스윙 품질 유니버스 + 기회 트리거).
2. ~~CAL-1 종결~~ → **07-11 완료** (아래 최근 완료). ⚠️ **2026-07-17(금) 제헌절 휴장** — 이번 주 금요일 잡 무동작이 정상.
3. ~~결측기 뉴스·공시·flows 백필~~ → 07-11 실사 완료: **flows 백필 완료**(KIS 30거래일 창), **공시는 온디맨드라 대상 아님**,
   **뉴스는 승인 어댑터로 백필 불가(blocked)** — 2026-06-15~07-10 뉴스 영구 공백.
   ⚠️ 그 구간 뉴스 부재를 "촉매 없음"으로 오독하지 않도록 소비자(R7·discuss) 명시 처리 검토(②와 같은 계열).

**M4 / Phase 1 잔여** (순서대로)
3. 이벤트 감시기(`watch/`) → P0 발화 (서킷브레이커·환율 임계·바이너리 전이·보유 공시) — heartbeat 배선 포함
4. 리플레이 회귀 테스트 (6/2~6/8 주간, M4 프롬프트 §2)
5. 승인 전이(draft→approved) 운영자 도구 — R5가 실제 플레이북을 내기 시작하면 필요해짐
6. KIS 잔고·체결 어댑터 — 저녁 보고 집행·포지션 결측 해소

**빠른 슬라이스 (틈새)**
- **DiscussPack에 주요 공시 원문 요약 포함** — 공정공시·계약 공시 등은 DART `document.xml`로 원문을 받아 팩에 발췌(공시가 뉴스보다 상위 근거 — 네이버 AI 팩토리 사례). 임원·주요주주 보고는 elestock 계열 정형 API 검토.
- R6 보고 `_macro_lines`의 `GROUP BY name` 버그 — 같은 지표 다중 수집분 중 임의 행 선택(6/09 KOSPI가 6/11 보고에 나갈 수 있음) → 지표별 최신 as_of 행 선택으로 수정
- R1 일반 게이트 운영 배선 — landing→FactRecord 변환 계층
- **애프터마켓 시행(2026-09-14) 후 실측**: 애프터마켓 체결이 data.go.kr 일별시세의 **종가·거래량에 포함되는지** 대조 — 포함되면 스크리너 거래대금·모멘텀 의미가 바뀐다(CAL-3 잔여).
- 2027년 휴장일 갱신 — `krx_holidays.json`의 `covered_through=2026-12-31` 만료 시 `--check`가 ⚠️ 경고(연 1회 운영 작업)

**외부 의존 해소** (병행 가능)
- NXT 프리·애프터 데이터 소스 조사 → SEL-1·R7-1 해소 (KRX 수급은 6/11 KIS TR로 해소됨)
- KIS 청산 주문(조건부) 인터페이스 스펙

**빠른 슬라이스 추가**
- R6 저녁 보고 수급 섹션을 flows.sqlite로 채우기(현재 "KRX 미해결" 결측 문구 잔존)
