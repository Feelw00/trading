#!/usr/bin/env bash
# ops/bootstrap.sh — GitOps 프로비저닝 (INFRA-2)
# 새 기기: git clone → ops/bootstrap.sh → 즉시 가동.
# Idempotent — 재실행 안전.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

log()  { printf "\033[1;34m[bootstrap]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[bootstrap]\033[0m %s\n" "$*" >&2; }
die()  { printf "\033[1;31m[bootstrap]\033[0m %s\n" "$*" >&2; exit 1; }

# --- 0. 전제 도구 ---
command -v asdf  >/dev/null 2>&1 || die "asdf 미설치 (https://asdf-vm.com)"
command -v curl  >/dev/null 2>&1 || die "curl 미설치"
command -v jq    >/dev/null 2>&1 || warn "jq 권장(검증용). 계속 진행."

# --- 1. asdf 플러그인 ---
log "asdf 플러그인 확인"
asdf plugin list 2>/dev/null | grep -q "^python$" || asdf plugin add python
asdf plugin list 2>/dev/null | grep -q "^nodejs$" || asdf plugin add nodejs

# --- 2. .tool-versions 적용 (Python / Node 핀) ---
log "asdf install (.tool-versions: $(tr '\n' ' ' < .tool-versions))"
asdf install

# --- 3. Poetry ---
if ! command -v poetry >/dev/null 2>&1; then
  log "Poetry 설치"
  curl -sSL https://install.python-poetry.org | python3 -
  export PATH="$HOME/.local/bin:$PATH"
fi
log "Poetry: $(poetry --version)"

# --- 4. Python 의존성 ---
log "poetry install"
poetry install --no-interaction --no-ansi

# --- 5. .env 점검 ---
[ -f .env ] || die ".env 가 없습니다 — docs/SECRETS.md 참조 (1Password 'stock / .env')"

# OPENCLAW_GATEWAY_TOKEN: 신규 기기는 자동 생성(영속 시크릿). 기존 값 보존.
if ! grep -qE "^OPENCLAW_GATEWAY_TOKEN=." .env; then
  TOKEN="$(openssl rand -hex 32)"
  if grep -q "^OPENCLAW_GATEWAY_TOKEN=" .env; then
    sed -i.bak "s|^OPENCLAW_GATEWAY_TOKEN=.*|OPENCLAW_GATEWAY_TOKEN=$TOKEN|" .env && rm .env.bak
  else
    printf "\n# 게이트웨이 인증(부트스트랩 생성, 1Password 동기화 필요)\nOPENCLAW_GATEWAY_TOKEN=%s\n" "$TOKEN" >> .env
  fi
  warn "OPENCLAW_GATEWAY_TOKEN 신규 생성됨 — 1Password 'stock / .env'에 반영하세요."
fi

# --- 6. OpenClaw 격리 설치 (Node $NODE_VER 번들, ~/.openclaw/) ---
NODE_VER="$(awk '/^nodejs/ {print $2}' .tool-versions)"
[ -n "$NODE_VER" ] || die ".tool-versions에 nodejs 핀 없음"
if [ ! -x "$HOME/.openclaw/bin/openclaw" ]; then
  log "OpenClaw 격리본 설치(--node-version $NODE_VER)"
  curl -fsSL --proto '=https' --tlsv1.2 https://openclaw.ai/install-cli.sh \
    | bash -s -- --node-version "$NODE_VER"
fi
log "OpenClaw: $($HOME/.openclaw/bin/openclaw --version 2>&1 | head -1)"

# --- 6.5. 트리거 모델(로컬 핀) — cron 트리거 턴 전용. 클라우드 쿼터를 크리티컬 패스에서 제거
#     (2026-06-11: minimax 무료 티어 rate limit으로 트리거 전멸 → 로컬 소형 모델 전환).
command -v ollama >/dev/null || die "ollama 미설치 — https://ollama.com/download"
TRIGGER_MODEL="qwen2.5:3b"  # openclaw.template.json agents.defaults.model과 일치 유지
ollama list 2>/dev/null | grep -q "^${TRIGGER_MODEL}" || {
  log "트리거 모델 pull: $TRIGGER_MODEL"
  ollama pull "$TRIGGER_MODEL"
}

# --- 7. .runtime/openclaw 상태 디렉토리 ---
mkdir -p .runtime/openclaw/workspace
chmod 700 .runtime/openclaw

# --- 8. openclaw.json 렌더링 (env 치환) ---
log "openclaw.json 렌더링"
set -a
# shellcheck disable=SC1091
. .env
set +a
export TRADING_OPENCLAW_WORKSPACE="$REPO/.runtime/openclaw/workspace"
poetry run python ops/openclaw/render_config.py

# --- 9. 검증 (M1 baseline 재현) ---
log "pytest"
poetry run pytest -q
log "mypy"
poetry run mypy

# --- 10. 요약 ---
cat <<EOF

✅ Bootstrap complete.
   poetry env:       $(poetry env info --path 2>/dev/null || echo "<not in project>")
   openclaw bin:     $HOME/.openclaw/bin/openclaw
   openclaw state:   $REPO/.runtime/openclaw
   openclaw config:  $REPO/.runtime/openclaw/openclaw.json

다음:
   1. 게이트웨이 기동:    ./ops/openclaw/start-gateway.sh
   2. CLI 페어링:        ./ops/openclaw/pair.sh   (operator.admin 스코프 승인)
   3. cron sync (dry-run): poetry run python ops/openclaw/sync.py
EOF
