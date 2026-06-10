# 2026-06-10 — M3: R5 합성·플레이북·주문 초안 (설계서 §3 R5·§6)

## 산출

### 1. Playbook 흐름 변수 화이트리스트 (`contracts/playbook.py`)
- `FLOW_VARIABLES` 10종 = 설계서 §3 R5 7종(갭·프리마켓 거래량·호가 불균형·체결강도·동시호가 궤적·거래량 클라이맥스·신저가 갱신 실패) + §4/§6 예시 키(new_low_after, prev_day_high_reclaim 등) 1:1. 임의 확장 금지 — 새 변수는 OPEN_QUESTIONS 경유.
- arm/abort 조건에 화이트리스트 외 키(밸류에이션·컨센서스 등) → **로드 시점 ValidationError** (M3 AC). arm 비어 있어도 거부.

### 2. R5 라운드 (`rounds/r5.py`)
- 입력: R3 논제(flat 제외) + 이벤트(R4 생존/기각 라벨 동봉) + factpack 가격 컨텍스트 + 거시.
- 프롬프트: 시나리오 트리·플레이북·체크리스트 JSON. 흐름 변수만, stop은 심리적 합의 레벨(라운드 넘버·전저점·전고점)·논리적 지지선 금지, 역추세는 소진 물리 신호 조건만, **"빈 배열이 정답 — 대부분의 날은 비거래"** 명시. 트랜치·상한은 출력 금지(코드 주입).
- **규율 코드 강제 (LLM 불신, M3 지시)**: 3트랜치 20/50/30(impatience_fee·flush limit + confirmation 조건부) 주입, `total_size_cap="0.5 * normal_unit"` 고정, 손절 2종 모두(가격 스탑 = LLM 제시 레벨만 — **미제공 시 폐기, 코드가 가격 지어내지 않음** / 시간 스탑 = LLM 값 또는 논제 horizon 폴백).
- 추가 거부: thesis_ref 불명, flat 논제, 방향-수단 불일치(long↔buy), 확인 조건 비-흐름변수.

### 3. PlaybookStore (`journal/playbooks.py`) + 러너 + 배선
- `data/playbooks.sqlite` append-only: playbooks·order_drafts(id별 version, status 전이=새 version)·synth_runs(시나리오·체크리스트). `playbooks_for_day`(R5.5 입력)·`draft`·`latest_run`(R6 렌더).
- `synth_playbooks.py` 러너: **장중 실행 거부**(`require_market_closed` — market_calendar 가드 첫 실배선, rc=3) / LLM 장애 시 §9 **P1 알림**("전일 초안 유지/폐기 선택", 21:00 기한) / 논제 없으면 스킵.
- `trading.run synth-playbooks` + cron `synth-pm`(20:30) 등록(14개 잡, disabled 정책 유지).
- OPEN_QUESTIONS **R5-1**(🟡) 등록: 논제 레벨 적대 라운드 부재 — 현 R4는 이벤트 검증. 잠정: non-flat 논제 전부 + R4 라벨 동봉.

## 검증
- pytest **242 passed**(+19: r5 14, 러너·스토어 5), mypy **0 issues (90 files)**.
- **실거동 스모크**(18:23, 논제 3건 — 카카오뱅크): **플레이북 0 = 비거래 선택.** 합성 추론이 설계 의도대로 — short 논제(영업이익 -14%) vs 상방 가격(신고가 0.992)에서 "소진의 물리 신호(volume_climax) 부재, 역추세 진입 근거 없음" 판정. 시나리오 분기 3개 + 관측 체크리스트 5항목 영속 확인.

## 남은 것
- R5.5 아침 선택기(`selector/`) — PlaybookStore.playbooks_for_day 입력, 순수 함수, 기본 비거래.
- R6 보고 — latest_run(시나리오·체크리스트)·OrderDraft 승인 요청·AlertStore P2 렌더.
- 승인 워크플로(draft→approved)는 R6 저녁 결재에서 운영자 수동(§6 의도된 마찰).
