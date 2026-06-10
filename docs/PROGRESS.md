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

---

## 미해결 / 다음

**M2 마무리**
- 일반 R1 게이트(FactRecord stale/conflict 플래그) — 뉴스 외 거시·시세에도 적용 (설계서 §3)
- `market_calendar/` 휴장일·DST·장중 게이팅 가드 (SCHED-1)
- R4 threshold 튜닝(기본 0.5는 실데이터 빈도 낮음) + R3 grounding 보강(수급 페르소나 데이터 부재)

**M3 / Phase 1**
- R5 합성·플레이북·주문 초안 (claude -p, 20:30)
- R5.5 아침 플레이북 선택기 (`selector/`, 코드, 흐름 변수만)
- R6 보고 (`reports/`, 정적 렌더)
- R7 평가·캘리브레이션 + 레짐 모니터 (주간)
- `alerts/` P0/P1/P2 어댑터 (Telegram Bot API 직접, 폴링 없음)
- KIS 잔고·체결 어댑터 (`KIS_ENABLE_TRADING=false`)

**OPEN_QUESTIONS 🔴**
- KRX 정보데이터시스템 — 시세·투자자별 매매동향 접근/인증 (R3 supply 페르소나 grounding 동시 해소)
- NXT 프리·애프터마켓 데이터
- 증권사 조건부(청산) 주문 API — KIS REAL 키 확보 완료, 청산 인터페이스 스펙 조사 잔여

**운영**
- cron 11개 일괄 활성화 (M3 + alerts 준비 후)
- R2 모델 `.env` 핀(`R2_MODEL`) + 비용 모니터링
- 1Password 'stock / .env' 동기화: `OPENCLAW_GATEWAY_TOKEN`
