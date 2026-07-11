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

# --- 6.5. 트리거 모델 — cron 트리거 턴 전용(데이터·판단 미개입, 절대금지 #2).
#     2026-06-11: minimax 무료 티어 rate limit → 로컬 ollama(qwen2.5:3b) 전환.
#     2026-07-11: ollama 폐기 → ChatGPT 구독 인증(openai/oauth). API 키 불필요.
#     모델명은 .env 의 OPENCLAW_TRIGGER_MODEL 로 주입(하드코딩 금지 — 절대금지 #4).
# .env 는 8단계에서 source 되므로 여기서는 파일을 직접 확인한다.
grep -qE "^OPENCLAW_TRIGGER_MODEL=." .env || die ".env 에 OPENCLAW_TRIGGER_MODEL 없음 (예: openai/gpt-5.5)"
if ! OPENCLAW_STATE_DIR="$REPO/.runtime/openclaw" \
     "$HOME/.openclaw/bin/openclaw" models auth list 2>/dev/null | grep -q "openai"; then
  warn "openclaw openai 인증 프로파일 없음 — 트리거 턴이 실패한다."
  warn "  TTY 에서 1회 실행:"
  warn "  OPENCLAW_STATE_DIR=$REPO/.runtime/openclaw \\"
  warn "    $HOME/.openclaw/bin/openclaw models auth login --provider openai --method oauth"
fi

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
