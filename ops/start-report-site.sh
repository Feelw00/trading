#!/usr/bin/env bash
# ops/start-report-site.sh — 보고서 웹 뷰를 tmux에서 기동(게이트웨이와 동일 패턴).
# Tailscale 사설망 IP에만 바인드 — 외부 비노출. 재부팅 후 수동 재기동(게이트웨이와 동일).

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

SESSION="trading-reports"
TS="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
# 2026-09-07: 80 포트는 nginx 가 소유(trading/trand 도메인 Host 라우팅, ops/nginx/). 앱은 8081 로컬 바인드.
PORT="${REPORT_SITE_PORT:-8081}"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "[reports] tmux 세션 '$SESSION' 이미 실행 중"
  exit 0
fi

# 2026-08-31 실측 경로(0.0.0.0:80 직접 바인드)는 2026-09-07 nginx 전환으로 종료 — 기록:
# - macOS는 특정 IP 저포트 바인딩은 거부하지만 와일드카드는 비루트 허용.
# - tailscale serve는 Host 헤더가 ts.net 호스트명일 때만 응답해 운영자 도메인 불가 — 미사용.
# 현재: nginx(0.0.0.0:80, default_server)가 trading.feelw00.com·IP 접속을 127.0.0.1:8081 로 프록시.
# 노출 범위는 종전과 동일(테일넷 + 홈 LAN, 읽기 전용).
HOST="${REPORT_SITE_HOST:-127.0.0.1}"

tmux new-session -d -s "$SESSION" -c "$REPO"
tmux send-keys -t "$SESSION" \
  "REPORT_SITE_HOST=$HOST REPORT_SITE_PORT=$PORT $REPO/.venv/bin/python -m trading.web" Enter

# 대시보드 콜드 렌더가 2~3초(2026-09-07 실측)라 단발 3초 체크는 오탐 — 최대 15초 재시도.
for _ in $(seq 1 15); do
  if curl -sf --max-time 5 "http://$HOST:$PORT/" > /dev/null; then
    echo "[reports] 기동 완료: http://$HOST:$PORT (nginx 뒤 — 외부는 trading.feelw00.com:80)"
    exit 0
  fi
  sleep 1
done
echo "[reports] ⚠️ 기동 확인 실패 — tmux attach -t $SESSION 로 로그 확인" >&2
exit 1
