"""`python -m trading.collect_owner_equity` — 지배기업 소유주지분 수집(COLLECT-6).

대상: fins에 재무가 있는 **전 종목**(밸류에이션 유니버스와 동일 — percentile 기준 일관성).
각 종목의 최신 BS 스냅샷과 같은 (연도, 보고서)의 CFS 전체 재무제표에서 1계정만 추출.
멱등 — 기시도(ok/ofs-skip/no-account)는 재호출 없음. 수집 후 밸류에이션 재산출 필요.
"""

import os

from trading.collectors.dart import DartClient
from trading.collectors.fins import FinStore, collect_owner_equity


def main() -> int:
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        print("DART_API_KEY 미설정 — 수집 불가(blocked)")
        return 1
    dart = DartClient(key)
    corp_map = dart.corp_code_map()
    store = FinStore()
    try:
        syms = store.symbols()
        stocks = [(s, corp_map.get(s, ("", s))[1] or s) for s in syms]
        print(f"지배주주지분 수집(COLLECT-6) — fins 유니버스 {len(stocks)}종목")
        loaded, skipped, errors = collect_owner_equity(dart, store, corp_map, stocks)
        print(f"적재 {loaded} · 스킵(OFS·계정부재·매핑없음) {skipped} · 오류 {len(errors)}")
        for e in errors[:8]:
            print(f"  오류: {e}")
        print("→ data/fins.sqlite (다음: python -m trading.valuation 재산출)")
        return 0 if not errors else 1
    finally:
        store.close()


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
