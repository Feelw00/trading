"""Phase 0 브로커 대사(PIVOT-6) — 읽기 전용.

7/15 이후 방치된 브로커 상태(보유·미체결 조건주문·매수가능금액)를 실측해 원문 그대로 출력한다.
주문·취소 경로 없음. 응답 필드는 해석하지 않고 raw JSON으로 박제(스키마 추측 금지 — 절대금지 #1).

사용: poetry run python ops/reconcile_broker.py
전제: .env에 TOSS_CLIENT_ID/SECRET/ACCOUNT_SEQ (1Password 'stock / .env').
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from trading.collectors.toss import client_from_env


def _dump(title: str, payload: object) -> None:
    print(f"\n===== {title} =====")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main() -> int:
    client = client_from_env()
    if client is None:
        print("토스 클라이언트 미설정 — .env의 TOSS_* 값을 확인하라 (docs/SECRETS.md).", file=sys.stderr)
        return 2

    now = datetime.now(ZoneInfo("Asia/Seoul")).isoformat()
    print(f"브로커 대사 (읽기 전용) — fetched_at={now}")

    _dump("accounts", client.accounts())
    _dump("holdings (보유 — 7/15 원장: 피에스케이 5주·S-Oil 4주와 대조)", client.holdings())
    _dump("conditional_orders OPEN (잔존 브래킷 — 있으면 정리 대상)", client.conditional_orders(status="OPEN"))
    _dump("buying_power_krw", client.buying_power_krw())

    out_dir = Path(".runtime/reconcile")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{now[:10]}-broker.json"
    out_path.write_text(
        json.dumps(
            {
                "fetched_at": now,
                "accounts": client.accounts(),
                "holdings": client.holdings(),
                "conditional_orders_open": client.conditional_orders(status="OPEN"),
                "buying_power_krw": client.buying_power_krw(),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    print(f"\n박제: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
