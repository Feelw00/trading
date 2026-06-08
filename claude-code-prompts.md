# Claude Code 투입 프롬프트 패키지

사용법: §1을 레포 루트에 `CLAUDE.md`로 저장하고, `trading-system-design.md`(v0.2)를 `docs/`에 넣은 뒤, §2의 마일스톤 프롬프트를 순서대로 투입한다. 마일스톤 하나당 한 세션을 권장하며, headless 실행 시:

```bash
claude -p "$(cat prompts/m1.md)" --output-format json
```

---

## §1. CLAUDE.md (레포 루트에 저장)

```markdown
# 프로젝트: 트레이딩 리서치·집행 시스템

## 최우선 규칙
docs/trading-system-design.md (v0.2)가 이 프로젝트의 헌법이다. 모든 작업 전에 읽어라.
설계서와 충돌하는 구현은 금지. 설계서의 모호함을 발견하면 임의 해석하지 말고
docs/OPEN_QUESTIONS.md에 항목을 추가하고 보수적인 스텁으로 우회하라.

## 절대 금지 사항
1. 외부 데이터 소스의 API 엔드포인트, 인증 방식, 응답 포맷을 추측해서 구현하지 마라.
   실제 스펙을 모르는 소스는 어댑터 인터페이스 + 스텁 구현 + OPEN_QUESTIONS 등록으로 처리한다.
   존재하지 않는 엔드포인트를 그럴듯하게 지어내는 것이 이 프로젝트 최대 리스크다.
2. LLM 호출을 R0(수집), R1(검증), R5.5(아침 선택기), 이벤트 감시기 경로에 넣지 마라. 해당 경로는 순수 코드다.
3. 시장가 주문 코드를 어떤 형태로도 작성하지 마라. 주문 관련 코드는 지정가·조건부(청산)만 존재한다.
4. 비밀값(API 키, 계좌)을 코드·로그·테스트 픽스처에 넣지 마라. 전부 환경변수 + .env.example 문서화.
5. 시간 처리: 모든 타임스탬프는 timezone-aware(KST 명시). naive datetime 사용 금지.

## 기술 스택 (고정)
- Python 3.11+, Poetry. 타입힌트 필수, mypy strict 통과.
- 스키마: pydantic v2 (설계서 §4의 데이터 계약을 1:1 구현)
- DB: PostgreSQL (docker-compose 제공), append-only — UPDATE/DELETE 금지, 새 버전 레코드로만 수정
- 스케줄러: 시장 시간 인지형 자체 구현 (단순 cron 금지, KRX/미국 휴장일 캘린더 내장)
- 테스트: pytest. 모든 데이터 계약에 스키마 테스트, 모든 게이트(R1, R5.5)에 단위 테스트

## 작업 방식
- 마일스톤 프롬프트의 수용 기준(AC)을 충족하기 전에 다음 작업으로 넘어가지 마라.
- 각 마일스톤 종료 시: 전체 테스트 통과 확인 → docs/PROGRESS.md에 완료 항목·미해결 항목 기록.
- 설계서에 없는 기능을 추가하고 싶으면 구현하지 말고 docs/PROPOSALS.md에 적어라.
- 외부 의존(증권사 API, KRX 인증 등)이 필요한 지점은 막히지 말고 인터페이스로 추상화 후 진행.

## 디렉터리 구조
src/contracts/   # pydantic 스키마 (FactRecord, EventRecord, ThesisRecord, Playbook, OrderDraft)
src/collectors/  # R0 — 소스별 클라이언트 (공통: 멱등성, 백오프, as_of/fetched_at/source 필수)
src/gates/       # R1 — 신선도·정합성 게이트
src/rounds/      # R2~R5 오케스트레이션 (LLM 호출 래퍼 포함)
src/selector/    # R5.5 — 아침 플레이북 선택기 (순수 함수로 작성, 입력→활성화 결정)
src/watch/       # 이벤트 감시기 + 알림 (P0/P1/P2)
src/reports/     # R6 — 모닝/저녁 보고 렌더링 (Jinja2 템플릿)
src/scheduler/   # 시장 시간 인지 스케줄러
src/journal/     # 저널 DB 접근 계층
tests/
fixtures/replay/ # 6/2~6/8 주간 리플레이 픽스처
docs/
```

---

## §2. 마일스톤 프롬프트

### M1 — 골격, 데이터 계약, 리플레이 하네스

```
docs/trading-system-design.md와 CLAUDE.md를 읽어라.

작업:
1. CLAUDE.md의 디렉터리 구조로 프로젝트 골격 생성 (Poetry, docker-compose의 PostgreSQL,
   pytest, mypy strict, .env.example).
2. src/contracts/에 설계서 §4의 다섯 스키마(FactRecord, EventRecord, ThesisRecord,
   Playbook, OrderDraft)를 pydantic v2로 구현. 설계서의 모든 필수 필드와 제약을 반영:
   - ThesisRecord: invalidation 필드가 비어 있으면 ValidationError
   - OrderDraft: stop과 time_stop_days 둘 다 없으면 ValidationError,
     created_when_market != "closed"면 ValidationError, 시장가 타입 자체가 스키마에 없음
   - 모든 레코드: as_of/fetched_at/source 필수, timezone-aware 검증
3. journal 계층: append-only 저장, 버전 레코드 패턴, 스키마 위반 시 알림 훅(지금은 로그).
4. 리플레이 하네스: fixtures/replay/ 아래의 날짜별 FactRecord/EventRecord JSON을 읽어
   파이프라인에 시간 순서대로 주입하는 러너. 픽스처 데이터 자체는 내가 나중에 채운다 —
   포맷 정의와 러너만 만들고, 형식을 보여주는 최소 샘플(가짜 값 명시)을 2일치 생성하라.

수용 기준(AC):
- pytest 전체 통과, mypy strict 통과
- "invalidation 없는 ThesisRecord 생성 시도"가 테스트로 실패를 증명
- 리플레이 러너가 샘플 픽스처 2일치를 시간 순으로 주입하고 저널에 기록
- docs/PROGRESS.md 갱신
```

### M2 — R0 수집기 + R1 게이트 + 스케줄러

```
docs/trading-system-design.md §3(R0, R1), §5, 부록 A와 CLAUDE.md를 읽어라.

작업:
1. 수집기 공통 베이스: 멱등 upsert(동일 as_of 재수집), 지수 백오프, 레이트리밋,
   실패 시 stale로 자연 강등. 모든 수집기는 이 베이스를 상속.
2. 부록 A의 소스별 수집기. 실제 API 스펙을 아는 소스만 실구현하고
   (예: DART OpenAPI, ECOS API는 공개 문서 기반 구현 가능 — 단, 문서에서 확인한
   엔드포인트만 사용하고 확인 불가능하면 스텁), 나머지(KRX 상세, NXT, 증권사)는
   어댑터 인터페이스 + 스텁 + OPEN_QUESTIONS 등록. 스텁은 리플레이 픽스처를
   데이터 소스로 사용할 수 있어야 한다(백테스트·리플레이와 동일 경로).
3. R1 게이트: 소스별 신선도 허용치 설정 파일, stale 플래그, 이중 소스 충돌 감지
   (환율·지수), 충돌 시 의사결정 제외 마킹 + 알림 훅.
4. 시장 시간 인지 스케줄러: KST 기준 설계서 §5의 슬롯 구현, KRX 휴장일 캘린더
   (설정 파일로 주입), 미국 서머타임 처리. "장중(09:00-15:30)에는 LLM 라운드 등록
   자체가 불가능"을 스케줄러 레벨에서 강제하고 이를 테스트로 증명.

AC:
- 스텁 수집기가 리플레이 픽스처로 R1까지 통과하는 엔드투엔드 테스트
- stale 입력이 R5 입력 게이트에서 차단되는 테스트
- 충돌 데이터가 평균·임의 선택되지 않고 제외 마킹되는 테스트
- 장중 시각에 LLM 라운드 스케줄 시도 시 거부되는 테스트
```

### M3 — LLM 라운드 (R2, R3, R4, R5) + R5.5

```
docs/trading-system-design.md §3(R2~R5.5), 부록 B와 CLAUDE.md를 읽어라.

작업:
1. LLM 클라이언트 래퍼 2종: (a) GPT-5.5 API — 라운드별 토큰 예산, 스키마 강제
   (구조화 출력), 위반 시 1회 재생성 후 폐기+알림. (b) claude -p 서브프로세스 호출 —
   --output-format json, 타임아웃, 산출물을 artifacts/날짜/에 저장.
   API 파라미터는 .env로 주입, 모델명 하드코딩 금지.
2. R2: 뉴스 텍스트 → EventRecord 변환 프롬프트 + 바이너리 상태 머신(상태 enum 전이만
   허용, 형용사 출력 거부). R3: 페르소나 3종 프롬프트를 부록 B 골격으로 작성하되
   입력 슬라이스 격리를 코드 레벨에서 강제(페르소나에 허용 외 데이터가 전달되면 예외).
3. R4 프롬프트: 표준 공격 벡터 체크리스트 내장, 입력은 전체 논제+전체 팩트,
   출력 {생존(수정안), 기각, 사유}. R5 프롬프트: 시나리오 트리, PlaybookSet,
   OrderDraft 생성 — 단 OrderDraft의 규율 파라미터(3트랜치, 총량 상한, 손절 2종)는
   LLM 출력을 신뢰하지 말고 코드 레벨 후처리로 강제 주입·검증하라.
4. R5.5: 순수 함수. 입력(갭, 프리마켓 거래량, 미국 마감, 환율 개장가) →
   PlaybookSet 중 arm_conditions 일치 항목 활성화. 기본 반환값은 "비거래".
   Playbook의 arm/abort 조건은 흐름 변수 화이트리스트만 허용 — 화이트리스트 외
   변수(밸류에이션, 컨센서스 등)가 조건에 들어오면 Playbook 로드 시점에 거부.

AC:
- 페르소나 입력 격리 위반이 예외를 던지는 테스트
- invalidation 없는 LLM 출력이 재생성→폐기 경로를 타는 테스트 (LLM은 mock)
- R5.5가 조건 불일치 시 "비거래"를 반환하는 테스트 + 흐름 변수 화이트리스트 테스트
- claude -p 래퍼가 mock 바이너리로 타임아웃·JSON 파싱을 처리하는 테스트
```

### M4 — 알림·보고 + 리플레이 회귀 테스트

```
docs/trading-system-design.md §8, §10(Phase 1 완료 기준)과 CLAUDE.md를 읽어라.

작업:
1. 알림: P0/P1/P2 등급, 페이로드 4요소({무엇이, 어느 규칙을, 약속된 행동, 기한}) 강제.
   채널은 어댑터 패턴(우선 텔레그램 봇 + 로컬 로그 폴백). 행동 매핑 없는 알림 생성
   시도는 코드 레벨에서 거부. 이벤트 감시기: 서킷브레이커/사이드카, 환율 임계,
   바이너리 전이, 보유 종목 공시 → P0. 알림은 주문 초안을 생성하지 않는다.
2. 보고: 06:50 모닝(읽기 전용), 21:00 저녁 결재(OrderDraft 승인 요청 포함).
   Jinja2 템플릿, 모든 수치에 as_of 병기, 분량 가드(초과 시 자동 축약이 아니라
   생성 실패+알림 — 분량 초과는 상류 설계 문제의 신호다).
3. 리플레이 회귀 테스트: fixtures/replay/에 6/2~6/8 주간의 실제 데이터를 내가 채우면
   실행할 회귀 시나리오를 코드로 작성. 검증 명제: 해당 주간 입력에서 시스템이
   (a) 종전 트리거 미충족으로 인버스 계열 OrderDraft를 생성하지 않고,
   (b) 6/8(서킷브레이커일) 저녁 R5가 익일 플러시 플레이북을 생성하며,
   (c) stale/충돌 게이트가 최소 1회 작동하는 것. LLM 라운드는 기록된 산출물로
   re-play 가능하게(LLM 호출 캐싱 레이어) 설계하라.

AC:
- 행동 매핑 없는 알림이 거부되는 테스트
- 보고 분량 가드 테스트
- 리플레이 회귀 시나리오가 샘플 픽스처에서 구동(실데이터는 추후 주입)
- docs/PROGRESS.md, OPEN_QUESTIONS.md 최종 정리 — 특히 외부 의존(KRX 인증,
  NXT 데이터 접근, 증권사 조건부 주문 API)의 미해결 목록을 우선순위와 함께
```

---

## §3. 운영 팁

마일스톤 사이에 OPEN_QUESTIONS.md를 직접 검토하고 답을 채워서 다음 세션에 넘겨라 — 특히 증권사 API(청산 자동화)는 네가 계좌 보유한 증권사의 스펙 확인이 선행돼야 하는 항목이다. M3의 LLM 프롬프트들은 Claude Code가 초안을 만들지만, 페르소나·적대 프롬프트의 품질은 R7 평가 루프가 돌기 전까지 측정 불가능하므로 초기엔 "스키마를 지키는가"만 검수하고 내용 튜닝은 Phase 2 이후로 미뤄라.
