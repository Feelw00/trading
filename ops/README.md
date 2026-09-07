# ops — GitOps 프로비저닝 (INFRA-1/2)

repo가 시스템의 **단일 소스**. 다른 기기 이식:
`git clone → ops/bootstrap.sh → 1Password로 .env → 즉시 가동`.

- **`bootstrap.sh`** (예정) — 새 기기 프로비저닝: openclaw 설치(핀 Node 22.22) + `poetry install` + openclaw config 적용 + cron 등록 + `.env`(1Password/op). *현재 스켈레톤 — cron 잡이 생기는 M2/M3에서 채움.*
- **`openclaw/`** — 트레이딩 전용 openclaw 인스턴스(`OPENCLAW_HOME=.runtime/openclaw`)의 **선언적 설정**: cron/heartbeat 잡 정의 + idempotent sync 스크립트. *현재 비어 있음(M2/M3).*

원칙: openclaw 설정을 `~/.openclaw`/런타임에 손으로 두지 말 것. **전부 여기 코드로.**

## nginx 도메인 라우팅 (2026-09-07)
80 포트는 brew nginx 가 소유하고 Host 헤더로 프록시한다: `trading.feelw00.com`(+IP 직접 접속, default_server)
→ 127.0.0.1:8081(`python -m trading.web`), `trand.feelw00.com` → 127.0.0.1:8082(trand 리포). 서버 블록 원본은
`ops/nginx/trading.feelw00.com.conf`; 반영은 trand 리포 `ops/nginx/sync.sh`(심링크 + `nginx -t` + brew services).
`start-report-site.sh` 기본값이 127.0.0.1:8081 로 바뀌었다 — 재기동 절차는 아래와 동일.

## 계약(contracts) 변경 시 웹 서버 재기동 (2026-09-01 사고 교훈)
`src/trading/contracts/*` 스키마를 바꾸면 보고서 웹 서버(tmux `trading-reports`)를
재기동할 것 — 장기 실행 프로세스가 구 모델을 메모리에 들고 있어, 새 필드가 박제된
레코드를 읽는 순간 `extra_forbidden` 500이 난다(CycleRecord phase_raw 사례).
재기동: `tmux kill-session -t trading-reports && ops/start-report-site.sh`
