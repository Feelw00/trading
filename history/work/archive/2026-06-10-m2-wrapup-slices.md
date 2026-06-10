# 2026-06-10 — M2 마무리 슬라이스 4종 (R1 일반 게이트 · market_calendar · R4 튜닝 · score-news 전수)

## 산출

### 1. 일반 R1 게이트 — FactRecord stale/conflict (`src/trading/gates/facts.py`)
- 뉴스 게이트(`gates/news.py`)와 동일 패턴: `(records, now, config)` 결정론 함수, 플래그 비영속(매 조회 재계산), 부착 필요 시 `apply_flags`로 새 버전 레코드(append-only).
- **stale**: 소스별(`per_source`) > 메트릭별(`per_metric`) > 기본(96h) 신선도 허용치. `FactGateConfig.from_file()` JSON 주입(M2 §2 "설정 파일") 지원. 폐기하지 않음.
- **future_dated**: as_of 미래(시계·파싱 오류) — stale 판정 회피 차단. `FactFlag.FUTURE_DATED` 계약 enum 추가(뉴스 게이트 선례).
- **conflict**: 핵심 지표(환율·지수 = AssetClass FX/INDEX) 한정. 동일 metric·동일 as_of(KST 일자)의 이중 소스가 상대 괴리 0.5%(knob) 초과 시 **그룹 전체** 플래그 + P1 알림 훅(`journal.store.AlertHook` 재사용). 평균·임의 선택 금지 — `split_decision_inputs`가 의사결정에서 제외.
- **R5 하드 게이트** `require_decision_grade`: stale/future/conflict 섞인 입력으로 주문 초안 생성 시도 시 `GateError`(조용한 드롭 금지, 위반 id·플래그 박제). M3 R5가 입력 확정 후 호출.
- 비고: landing(SQLite 원시행)→FactRecord 변환은 게이트 소관 아님(collectors.base 규약 유지) — 테스트는 계약 레코드 직구성.
- 테스트 18개(`tests/test_gates_facts.py`): M2 AC(stale R5 차단 / conflict 제외 마킹 / 설정 주입) 전부 증명.

### 2. market_calendar 가드 (`src/trading/market_calendar/calendar.py`) — SCHED-1
- **스케줄러 아님** — 각 잡이 호출하는 가드. openclaw cron이 스케줄 전담.
- 거래일 판정: 주말 + 월-일 고정 공휴일(코드 `_FIXED_CLOSED_MD`: 신정·삼일절·근로자의날·어린이날·현충일·광복절·개천절·한글날·성탄절·12/31 연말휴장) + **운영자 주입 명시 휴장일**(`krx_holidays.json`). 음력·대체공휴일 추측 금지 → **OPEN_QUESTIONS CAL-1 등록**. 미등록 휴장일의 실패 방향은 안전(가드가 LLM을 더 막을 뿐, 장중에 풀리는 일 없음).
- 가드 3종: `require_llm_rounds_allowed`(장중 09:00–15:30 경계 포함 거부 — §5 휴면), `require_market_closed`(R5 주문 설계), `require_trading_day`(수급 수집 등). naive datetime 즉시 거부, 비-KST tz는 변환.
- 미국 DST: 수동 규칙 없이 `zoneinfo America/New_York` 변환(`us_session_kst`) — EDT 마감=익일 05:00 KST, EST=06:00 KST 테스트로 증명.
- **디스패치 배선은 보류** — 수동 실행 차단 여부는 운영자 결정(OPEN_QUESTIONS CAL-2 🟡, cron 활성화 시 결정).
- 테스트 15개(`tests/test_market_calendar.py`): M2 AC "장중 LLM 라운드 거부"를 가드 레벨로 증명.

### 3. score-news 전수 실행 (R2 백필)
- 6/9자 뉴스 395건 → 배치 45개(후보/섹터/테마/매크로) → **EventRecord 131건 적재, 폐기 0, LLM 에러 0**. incremental append 정상 동작.

### 4. R4 threshold 분포 기반 튜닝
- 131건 분포: 질량 0.2~0.5 집중. single_stock(n=49) p50=0.30·p80=0.40·최대 0.50 → **구 기본 0.5는 선별률 4%(2건)로 사실상 비활성**(6/10 검증 세션 진단 확정). scope 무관 ≥0.7은 5건(4%), ≥0.6은 8건(6%).
- 결정: `strength_threshold` 0.5→**0.4**(single_stock p80), `high_strength` 0.7→**0.6**(상위 ~6%). 코드 기본값 변경(커밋·재현 가능) + **`.env` 오버라이드 knob 신설**(`R4Config.from_env`: R4_STRENGTH_THRESHOLD/R4_HIGH_STRENGTH/R4_MIN_SURVIVED/R4_MAX_EVENTS, `.env.example` 문서화, `verify_news` 러너 배선).
- 드라이런 검증(LLM 미호출 `select_events`): 131건 중 **18건 선별(14%, cap 20 이내)** — 서킷브레이커·연준·엔비디아-SK하이닉스 협력·주요 단일종목 공시 등 실제 고임팩트 촉매로 구성. 경계 테스트(single 0.3 제외, broad 0.5 제외)는 신규 기본값에서도 유효.

## 검증 (AC)
- pytest **197 passed** (162 → 197: gates/facts +18, market_calendar +15, R4 from_env +2)
- mypy strict **0 issues (77 files)** (73 → 77)

## 발견된 후속 작업
- R4 실검증 실행(verify-catalysts): 선별 18건 × 3렌즈 ≈ 54 claude 호출 — 비용 승인 후 실행(운영자).
- CAL-1: 2026년 음력·대체공휴일을 KRX 공지로 확인해 `krx_holidays.json` 채우기(운영자 확인 필요).
- CAL-2: 장중 가드의 `trading.run` 배선 범위(cron 한정 vs 수동 포함) — cron 활성화(M3) 시 결정.
- R1 일반 게이트의 운영 배선: landing行→FactRecord 변환 계층(collectors.base 후속) 후 거시·시세에 적용.
