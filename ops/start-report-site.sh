#!/usr/bin/env bash
# ops/start-report-site.sh — 보고서 웹 뷰를 tmux에서 기동(게이트웨이와 동일 패턴).
# Tailscale 사설망 IP에만 바인드 — 외부 비노출. 재부팅 후 수동 재기동(게이트웨이와 동일).

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

SESSION="trading-reports"
TS="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
PORT="${REPORT_SITE_PORT:-80}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[reports] tmux 세션 '$SESSION' 이미 실행 중"
  exit 0
fi

# 0.0.0.0:80 와일드카드 바인딩(2026-08-31 실측 확정 경로):
# - macOS는 특정 IP 저포트 바인딩은 거부하지만 와일드카드는 비루트 허용.
# - tailscale serve는 Host 헤더가 ts.net 호스트명일 때만 응답해 운영자 도메인 불가 — 미사용.
# - 노출 범위: 테일넷 + 홈 LAN(읽기 전용 대시보드 — 공유기 포트포워딩 없는 한 인터넷 비노출).
HOST="0.0.0.0"

tmux new-session -d -s "$SESSION" -c "$REPO"
tmux send-keys -t "$SESSION" \
  "REPORT_SITE_HOST=$HOST REPORT_SITE_PORT=$PORT $REPO/.venv/bin/python -m trading.web" Enter

sleep 1
if curl -sf --max-time 3 "http://$HOST:$PORT/" > /dev/null; then
  echo "[reports] 기동 완료: 포트 $PORT (테일넷+LAN, 읽기 전용)"
else
  echo "[reports] ⚠️ 기동 확인 실패 — tmux attach -t $SESSION 로 로그 확인" >&2
  exit 1
fi
