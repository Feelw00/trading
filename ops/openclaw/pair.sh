#!/usr/bin/env bash
# ops/openclaw/pair.sh — 로컬 CLI 디바이스를 트레이딩 게이트웨이에 페어링 (operator.admin scope).
# Idempotent: **현재 기기**가 이미 admin 스코프 보유 시 즉시 종료.
# 전제: 트레이딩 게이트웨이가 ${OPENCLAW_STATE_DIR}에서 실행 중.
#
# 2026-07-11 (Mac mini 이관) 수정 2건:
#   1) has_admin 이 paired 목록 "아무 기기나" 검사 → 다른 기기(구 운영기)가 admin 이면 skip 되어
#      새 기기의 스코프 상승이 영원히 일어나지 않았다. 이제 identity/device.json 의 deviceId 만 본다.
#   2) requestId 는 CLI 가 연결할 때마다 새로 생성된다. devices list 로 읽은 id 는 즉시 낡는다.
#      `devices approve --latest` 가 출력하는 id 를 그 자리에서 승인해야 성립.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

[ -f .env ] || { echo "[pair] .env 없음" >&2; exit 1; }
# shellcheck disable=SC1091
set -a; . "$REPO/.env"; set +a
[ -n "${OPENCLAW_GATEWAY_TOKEN:-}" ] || { echo "[pair] OPENCLAW_GATEWAY_TOKEN 미설정" >&2; exit 1; }

export OPENCLAW_STATE_DIR="$REPO/.runtime/openclaw"
export OPENCLAW_CONFIG_PATH="$REPO/.runtime/openclaw/openclaw.json"
OC="$HOME/.openclaw/bin/openclaw"
[ -x "$OC" ] || { echo "[pair] $OC 없음 — bootstrap.sh 먼저" >&2; exit 1; }

DEVICE_JSON="$OPENCLAW_STATE_DIR/identity/device.json"

self_device_id() {
  [ -f "$DEVICE_JSON" ] || return 1
  python3 -c "
import json
print(json.load(open('$DEVICE_JSON')).get('deviceId', ''))
" 2>/dev/null
}

# **현재 기기**가 operator.admin 을 보유했는지만 확인 (다른 기기의 admin 은 무관).
has_admin() {
  local self
  self="$(self_device_id || true)"
  [ -n "$self" ] || { echo no; return; }
  "$OC" devices list --json 2>/dev/null | python3 -c "
import json, sys
self_id = '$self'
try:
    d = json.load(sys.stdin)
except Exception:
    print('no'); raise SystemExit
ok = any(
    p.get('deviceId') == self_id and 'operator.admin' in (p.get('scopes') or [])
    for p in (d.get('paired') or [])
)
print('yes' if ok else 'no')
" 2>/dev/null || echo no
}

if [ "$(has_admin)" = "yes" ]; then
  echo "[pair] 현재 기기가 이미 operator.admin 보유. skip."
  exit 0
fi

echo "[pair] 디바이스 스코프 상승 진행 (device: $(self_device_id || echo '?'))"

# 스코프는 단계적으로 상승(read → pairing → write → admin).
# write 를 시도해 pending 을 만들고, --latest 가 알려주는 requestId 를 즉시 승인한다.
for attempt in 1 2 3 4 5 6 7 8; do
  "$OC" cron add __pair_probe --cron "0 0 * * *" --tz Asia/Seoul \
    --tools exec --disabled -- "true" >/dev/null 2>&1 || true

  req="$("$OC" devices approve --latest --token "$OPENCLAW_GATEWAY_TOKEN" 2>&1 \
          | grep -oE 'approve [0-9a-f-]{36}' | awk '{print $2}' | head -1)"
  if [ -n "$req" ]; then
    "$OC" devices approve "$req" --token "$OPENCLAW_GATEWAY_TOKEN" >/dev/null 2>&1 || true
    echo "[pair]   approved $req"
  fi

  # 쓰기가 실제로 되면 상승 완료.
  if "$OC" cron add __pair_probe2 --cron "0 0 * * *" --tz Asia/Seoul \
       --tools exec --disabled -- "true" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if [ "$(has_admin)" != "yes" ]; then
  echo "[pair] admin 스코프 획득 실패 — devices list 확인" >&2
  "$OC" devices list 2>&1 | tail -15 >&2
  exit 1
fi

# probe 잡 정리
for name in __pair_probe __pair_probe2; do
  ids="$("$OC" cron list --all --json 2>/dev/null | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    jobs = d.get('jobs') or d.get('items') or []
    print(' '.join(j['id'] for j in jobs if j.get('name') == '$name'))
except Exception:
    pass
" 2>/dev/null || true)"
  for id in $ids; do
    "$OC" cron rm "$id" >/dev/null 2>&1 || true
  done
done

echo "[pair] ✅ operator.admin 페어링 완료"
