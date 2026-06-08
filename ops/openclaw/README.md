# ops/openclaw — 트레이딩 전용 openclaw 선언적 설정

`OPENCLAW_HOME=.runtime/openclaw`(개인 `~/.openclaw`와 분리)에 적용될 선언적 설정.

예정 내용 (M2/M3, 라운드/cron 잡이 정의되면):
- `cron-jobs.*` — KST 슬롯별 cron 잡 정의(설계서 §5). 순수-코드 경로는 `--tools exec`로 `python -m trading.run <round>` 디스패치, LLM 라운드는 isolated `--message --model`.
- `heartbeat.*` — 장중(activeHours 09:00–15:30) 연속 감시 → `python -m trading.watch`.
- `openclaw.json` 템플릿 — 비밀은 `op://` 참조.
- `sync` 스크립트 — 위 정의를 openclaw에 idempotent하게 등록(`openclaw cron add|edit|rm`).
