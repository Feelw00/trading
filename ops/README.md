# ops — GitOps 프로비저닝 (INFRA-1/2)

repo가 시스템의 **단일 소스**. 다른 기기 이식:
`git clone → ops/bootstrap.sh → 1Password로 .env → 즉시 가동`.

- **`bootstrap.sh`** (예정) — 새 기기 프로비저닝: openclaw 설치(핀 Node 22.22) + `poetry install` + openclaw config 적용 + cron 등록 + `.env`(1Password/op). *현재 스켈레톤 — cron 잡이 생기는 M2/M3에서 채움.*
- **`openclaw/`** — 트레이딩 전용 openclaw 인스턴스(`OPENCLAW_HOME=.runtime/openclaw`)의 **선언적 설정**: cron/heartbeat 잡 정의 + idempotent sync 스크립트. *현재 비어 있음(M2/M3).*

원칙: openclaw 설정을 `~/.openclaw`/런타임에 손으로 두지 말 것. **전부 여기 코드로.**
