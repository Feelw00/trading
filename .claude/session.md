<!--
이 프로젝트의 boot/end 정의. 유저 레벨 /boot·/end가 이 파일을 그대로 수행한다.
기존 이력 컨벤션 매핑: NEXT.md 역할=history/work/CURRENT.md · histories/ 역할=history/work/archive/
(신규 NEXT.md·histories/·aliases/ 생성 금지 — CLAUDE.md 작업 방식이 상위 기준)
-->

## boot

### sync
- `TZ=Asia/Seoul date '+%Y-%m-%d %H:%M (%a)'` — **콘솔 날짜 기준**(모델 추정 날짜 신뢰 금지)
- `collect-macro` 스킬 — 거시 어댑터 적재 + **실시간 오버레이 `poetry run python -m trading.regime`**(코스피·코스닥 헤드라인은 실시간 우선, EOD는 as_of 병기)
- `work-boot` 스킬 — 시세·뉴스 신선도(미수집 자동 수집) + `drill.py --audit` + **감시 풀 `approve --pool` 우선** + 스크리너(stale 라벨 필수)

### state
- 감시기: `arm-watch` 프로세스 + `.runtime/watch-heartbeat` 신선도(장중 필수)
- 게이트웨이: tmux `openclaw-trading` + 포트 18790 (죽었으면 `bash ops/openclaw/start-gateway.sh`)
- `EXEC_MODE`(현재 live)·킬스위치 `.runtime/exec/KILL` 유무 한 줄 보고

### gotchas
- **.env 변경 = 게이트웨이 재기동 필수** — env는 tmux 기동 시점 스냅샷(7/14 사고: 토스 키 없이 감시기 기동)
- 국내 EOD는 +1영업일 공개 — 아침 스크리너는 항상 전일 미반영. 급변 후엔 그 순위 무효 명시
- 휴장일(예: 2026-07-17 제헌절) 잡 무동작이 정상 — `market_calendar`가 기준
- 통합 보고 양식·수집 하네스·절대 금지 사항은 CLAUDE.md·프로젝트 스킬이 상위 기준

### next
- `history/work/CURRENT.md`의 "진행 중"·"다음 후보" 인용 (이 프로젝트의 NEXT.md 역할)

## end

### summary
- `git diff --stat` + 이번 세션 산출·검증 요약
- **검증 게이트**: 코드 변경 시 `poetry run pytest -q` + `poetry run mypy src tests` 통과 확인 — 실패 상태로 세션을 닫지 않는다(불가피하면 운영자 합의 후 미해결 명시 박제)

### handoff
- 세션 상세 → `history/work/archive/YYYY-MM-DD-<slug>.md` / `CURRENT.md`는 한 줄+링크 롤오버·최종 갱신일 (**NEXT.md·histories/ 신규 생성 금지 — 기존 컨벤션 매핑**)
- 마일스톤 완결 → `docs/PROGRESS.md`(coarse), 결정·모호함 → `docs/OPEN_QUESTIONS.md`, 아이디어 → `docs/PROPOSALS.md`
- **지시 에코백(7/14 규칙)**: 운영자 지시를 좁혀/보수화해 인코딩했으면 종료 보고에 **결과를 지시자 언어로** 명시(예: "내일 실제 주문은 나가지 않습니다"). 조용한 격하 금지
- 운영 스냅샷: 감시기·게이트웨이·EXEC_MODE·킬스위치 한 줄 (+이번 세션 .env 변경 시 게이트웨이 재기동 여부)

### commit
- 논리 단위 커밋(feat/fix/docs/ops), 비밀값(.env) 절대 미포함 확인
- 푸시는 운영자 요청·세션 합의 시. main 직접 커밋 금지 — 작업 브랜치에서
