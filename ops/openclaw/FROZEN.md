# ⚠️ 동결 고지 (2026-08-26, v0.3 비준 — OPEN_QUESTIONS PIVOT-1)

이 디렉터리의 **cron 정의(`cron_jobs.py`)와 드릴(`drill.py`)은 v0.2 스윙 슬롯 전제**다.
v0.3(장기 사이클·가치 투자) 체제에서 **sync·cron 등록을 실행하지 마라** — 스윙 잡(장중 감시기·
LLM 라운드 체인)이 되살아난다.

- v0.3 슬롯(일간 EOD 18:00 · 주간 토 · 월간 첫 토+집행 · 분기)은 설계서 §5 기준으로
  Phase 1~3에서 `cron_jobs.py`를 재작성한 뒤에만 등록한다.
- 게이트웨이(`start-gateway.sh`)·설정 렌더링(`render_config.py`)·bootstrap 연동은 재사용 가능
  (스케줄 정의와 무관한 인프라 계층).
