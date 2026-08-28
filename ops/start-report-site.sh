#!/usr/bin/env bash
# ops/start-report-site.sh — 보고서 웹 뷰를 tmux에서 기동(게이트웨이와 동일 패턴).
# Tailscale 사설망 IP에만 바인드 — 외부 비노출. 재부팅 후 수동 재기동(게이트웨이와 동일).

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

SESSION="trading-reports"
TS="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
PORT="${REPORT_SITE_PORT:-8787}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[reports] tmux 세션 '$SESSION' 이미 실행 중"
  exit 0
fi

# 테일넷 IP 동적 해석(기기 IP는 바뀔 수 있음 — 하드코딩 금지)
HOST="$("$TS" ip -4 2>/dev/null | head -1)"
[ -n "$HOST" ] || { echo "[reports] tailscale IP 해석 실패 — Tailscale 실행 확인" >&2; exit 1; }

tmux new-session -d -s "$SESSION" -c "$REPO"
tmux send-keys -t "$SESSION" \
  "REPORT_SITE_HOST=$HOST REPORT_SITE_PORT=$PORT $REPO/.venv/bin/python -m trading.report_site" Enter

sleep 1
if curl -sf --max-time 3 "http://$HOST:$PORT/" > /dev/null; then
  echo "[reports] 기동 완료: http://$HOST:$PORT/ (테일넷 전용)"
else
  echo "[reports] ⚠️ 기동 확인 실패 — tmux attach -t $SESSION 로 로그 확인" >&2
  exit 1
fi
