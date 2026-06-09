# ops/openclaw — 트레이딩 전용 openclaw 선언적 설정

`OPENCLAW_HOME=.runtime/openclaw`(개인 `~/.openclaw`와 분리)에 적용될 선언적 설정.

## 현재 (cron 디스패치 #3)
- **`cron_jobs.py`** — KST 슬롯별 cron 잡 **선언적 매니페스트**(`CronJob`: name·cron·round·mode·comment). 단일 소스.
  순수-코드는 `mode=exec`(`--tools exec --light-context`로 `python -m trading.run <round>`), LLM 라운드는 `mode=llm`.
- **`sync.py`** — 매니페스트를 openclaw cron에 등록. **기본 dry-run**(명령만 출력) + round 정합성 검증(`trading.run.ROUNDS`).
  `python ops/openclaw/sync.py [--apply]`.
- 라운드 디스패치 엔트리: `src/trading/run.py`(`ROUNDS`). `python -m trading.run --list`로 확인.

> ⚠️ **openclaw cron CLI 정확 구문은 설치본에서 검증 후 확정**(절대금지 #1). `sync.py`의 명령은 문서화된 플래그 기준 템플릿이며 `--apply`도 검증 전까지 출력만(SAFE). bootstrap에서 openclaw 설치 후 활성화.

## 예정 (다음)
- `heartbeat.*` — 장중(activeHours 09:00–15:30) 연속 감시 → `python -m trading.watch`.
- `openclaw.json` 템플릿 — 비밀은 `op://` 참조.
- LLM 라운드(R2/R3 정형화·페르소나) 추가 시 `mode=llm` 잡 등록.
