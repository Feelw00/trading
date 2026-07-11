#!/usr/bin/env bash
# ops/openclaw/start-gateway.sh — 트레이딩 전용 openclaw 게이트웨이를 tmux에서 기동.
# 개인 ~/openclaw 인스턴스와 포트·세션·상태 모두 분리.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

SESSION="openclaw-trading"
LOG="/tmp/openclaw-trading.log"
OC="$HOME/.openclaw/bin/openclaw"

[ -x "$OC" ] || { echo "[gateway] $OC 없음 — bootstrap.sh 먼저" >&2; exit 1; }
[ -f .env ] || { echo "[gateway] .env 없음" >&2; exit 1; }
[ -f .runtime/openclaw/openclaw.json ] || { echo "[gateway] .runtime/openclaw/openclaw.json 없음 — bootstrap.sh 먼저" >&2; exit 1; }

# 이미 떠 있으면 skip.
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[gateway] tmux 세션 '$SESSION' 이미 실행 중. 로그: $LOG"
  exit 0
fi

rm -f "$LOG"
tmux new-session -d -s "$SESSION" -c "$REPO"
# `. ./.env` — 슬래시 필수. zsh 는 `. .env` 를 $PATH 에서만 찾고 cwd 를 보지 않는다
# (bash 는 cwd 폴백 → 우분투에선 통과, macOS 기본 zsh 에선 실패했음).
tmux send-keys -t "$SESSION" "set -a && . $REPO/.env && set +a && OPENCLAW_STATE_DIR=$REPO/.runtime/openclaw OPENCLAW_CONFIG_PATH=$REPO/.runtime/openclaw/openclaw.json $OC gateway 2>&1 | tee $LOG" Enter

# 부팅 대기(최대 30초)
for i in $(seq 1 30); do
  if grep -q "gateway.* ready" "$LOG" 2>/dev/null; then
    echo "[gateway] ✅ 기동 완료 (tmux: $SESSION, log: $LOG)"
    exit 0
  fi
  sleep 1
done

echo "[gateway] 기동 확인 실패 — 로그 확인: $LOG" >&2
tail -20 "$LOG" >&2 || true
exit 1
