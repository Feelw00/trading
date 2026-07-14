---
description: 작업 세션 마무리 — 검증·이력 롤오버·커밋·운영 상태 확인 후 종료 보고.
allowed-tools: Read, Bash, Edit, Write
---

작업 세션을 마무리한다. **고정 순서**:

1. **검증 게이트** — 코드 변경이 있으면 `poetry run pytest -q` + `poetry run mypy src tests` 통과 확인. 실패 상태로 세션을 닫지 않는다(실패면 고치거나, 운영자 합의하에 미해결로 명시 박제).

2. **이력 롤오버(CLAUDE.md 작업 방식)**:
   - 세션 디테일 → `history/work/archive/YYYY-MM-DD-<slug>.md` 신규 파일(산출·메커니즘·검증·잔여).
   - `history/work/CURRENT.md` — "최근 완료"에 1~2문장 + archive 링크, "진행 중" 비우거나 갱신, 최종 갱신일.
   - 마일스톤 단위 완결이 있으면 `docs/PROGRESS.md`에 coarse 요약(세션 로그 금지).
   - 결정·모호함은 `docs/OPEN_QUESTIONS.md`, 신기능 아이디어는 `docs/PROPOSALS.md`.

3. **지시 에코백 확인(2026-07-14 규칙)** — 이 세션에서 운영자 지시를 좁히거나 보수화해 인코딩한 게 있으면, 종료 보고에 **실행 결과를 지시자 언어로** 명시한다(예: "내일 실제 주문은 나가지 않습니다"). 조용한 격하 금지.

4. **커밋** — 논리 단위로 나눠 커밋(feat/fix/docs/ops). 비밀값 포함 여부 확인(.env는 절대 커밋 금지 — gitignored인지 확인). 푸시는 운영자가 요청했거나 이 세션에서 합의된 경우만.

5. **운영 상태 스냅샷** — 세션 중 운영에 손댔으면 종료 전 확인:
   - 감시기: 프로세스 + `.runtime/watch-heartbeat` 신선도 (장중이면 필수)
   - 게이트웨이: tmux `openclaw-trading` + 포트 18790. **⚠️ 이 세션에서 `.env`를 바꿨으면 게이트웨이 재기동 필수**(env는 기동 시점 스냅샷 — 7/14 사고).
   - `EXEC_MODE`·킬스위치(`.runtime/exec/KILL`) 상태를 종료 보고에 한 줄로.

6. **종료 보고** — ① 이 세션이 바꾼 것(운영자 언어로) ② 커밋/푸시 여부 ③ 운영 상태 한 줄 ④ 다음 세션 첫 작업 1~3개.
