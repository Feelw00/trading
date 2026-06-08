# PROGRESS

## M1 — 골격 · 데이터 계약 · 리플레이 하네스 ✅ (2026-06-08)

**완료**
- 프로젝트 골격: Poetry(src-layout `src/trading`, Python 3.13, in-project venv), pydantic v2, pytest, mypy strict(+pydantic plugin), `docker-compose`(PostgreSQL).
- 데이터 계약 5종(`src/trading/contracts`): FactRecord, EventRecord, ThesisRecord, Playbook, OrderDraft.
  - 전 레코드: as_of/fetched_at/source 필수 + timezone-aware(`AwareDatetime`, naive 거부), `extra` 금지, `frozen`(불변).
  - ThesisRecord: `invalidation` 비/공백이면 ValidationError.
  - OrderDraft: `stop`·`time_stop_days` 둘 다 없으면 ValidationError, `created_when_market`=closed만, 시장가 `OrderType` 부재.
- journal(`src/trading/journal`): in-memory append-only + 버전 패턴 + 스키마 위반 알림 훅(로그). PostgreSQL 백엔드는 M2.
- 리플레이 하네스(`src/trading/replay`) + 샘플 픽스처 2일치(`fixtures/replay/sample`, 전부 가짜값 `sample_fake`).
- 엔트리포인트 스텁: `trading.run`(라운드 디스패치), `trading.watch`(감시 틱).

**검증 (AC)**
- `poetry run pytest` → **19 passed**. `poetry run mypy` → **27 files, no issues**.
- invalidation 없는/빈 ThesisRecord → ValidationError (`test_thesis_invalidation`).
- 리플레이 러너가 샘플 2일치를 `as_of` 시간순 주입 + 저널 기록 (`test_replay`).

**미해결 / 다음**
- `ops/openclaw`(cron/heartbeat 선언적 정의) + `ops/bootstrap.sh`(GitOps 프로비저닝) — INFRA-2. 라운드가 생기는 M2/M3에서 cron 잡 구체화.
- 데모 cron 2건 삭제(gateway 기동 시): `07b64b92…`, `b5fb854c…`.
- M2: R0 수집기 + R1 게이트(스케줄은 openclaw cron). 외부 소스 스펙은 OPEN_QUESTIONS(KRX/NXT/증권사) 우선 해소.
- 모델 라우팅 키: `OPENAI_API_KEY`(R2/R3), `claude -p`(로컬 Claude Code 인증).
