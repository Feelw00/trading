#!/usr/bin/env bash
# ops/openclaw/pair.sh — 로컬 CLI 디바이스를 트레이딩 게이트웨이에 페어링 (operator.admin scope).
# Idempotent: 이미 admin 스코프 보유 시 즉시 종료.
# 전제: 트레이딩 게이트웨이가 ${OPENCLAW_STATE_DIR}에서 실행 중.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

[ -f .env ] || { echo "[pair] .env 없음" >&2; exit 1; }
# shellcheck disable=SC1091
set -a; . .env; set +a
[ -n "${OPENCLAW_GATEWAY_TOKEN:-}" ] || { echo "[pair] OPENCLAW_GATEWAY_TOKEN 미설정" >&2; exit 1; }

export OPENCLAW_STATE_DIR="$REPO/.runtime/openclaw"
export OPENCLAW_CONFIG_PATH="$REPO/.runtime/openclaw/openclaw.json"
OC="$HOME/.openclaw/bin/openclaw"
[ -x "$OC" ] || { echo "[pair] $OC 없음 — bootstrap.sh 먼저" >&2; exit 1; }

has_admin() {
  "$OC" devices list --json 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('yes' if any('operator.admin' in p.get('scopes', []) for p in d.get('paired', [])) else 'no')
" 2>/dev/null
}

pending_ids() {
  "$OC" devices list --json 2>/dev/null | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(' '.join(r.get('requestId', '') for r in d.get('pending', []) if r.get('requestId')))
" 2>/dev/null
}

# 이미 페어링되어 있으면 끝.
if [ "$(has_admin || echo no)" = "yes" ]; then
  echo "[pair] 디바이스가 이미 operator.admin 스코프 보유. skip."
  exit 0
fi

# 게이트웨이 응답 확인.
if ! "$OC" status >/dev/null 2>&1 && ! "$OC" cron list >/dev/null 2>&1; then
  echo "[pair] 게이트웨이 미응답 — 먼저 기동하세요" >&2
  exit 1
fi

# 스코프는 단계적으로 상승(read → pairing → admin).
# write 동작을 시도해서 pending 생성 → 토큰으로 승인 → 반복.
echo "[pair] 디바이스 스코프 상승 진행"
for attempt in 1 2 3 4 5; do
  # write 시도(실패 OK — pending 생성이 목적)
  "$OC" cron add __pair_probe --cron "0 0 * * *" --tz Asia/Seoul \
    --tools exec --disabled -- "true" >/dev/null 2>&1 || true

  # pending 모두 승인
  for id in $(pending_ids); do
    "$OC" devices approve "$id" --token "$OPENCLAW_GATEWAY_TOKEN" >/dev/null 2>&1 || true
  done

  if [ "$(has_admin || echo no)" = "yes" ]; then
    break
  fi
  sleep 1
done

if [ "$(has_admin || echo no)" != "yes" ]; then
  echo "[pair] admin 스코프 획득 실패 — devices list 확인" >&2
  "$OC" devices list 2>&1 | tail -15 >&2
  exit 1
fi

# probe 잡 정리
probe_ids=$("$OC" cron list --all --json 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    jobs = d.get('jobs') or d.get('items') or []
    print(' '.join(j['id'] for j in jobs if j.get('name') == '__pair_probe'))
except Exception:
    pass
" 2>/dev/null || true)
for id in $probe_ids; do
  "$OC" cron rm "$id" >/dev/null 2>&1 || true
done

echo "[pair] ✅ operator.admin 페어링 완료"
