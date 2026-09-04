"""`python -m trading.collect_returns` — 주주환원·분할 이력 수집(v1.8 ③ 예약분).

대상: 최신 R4 스크리닝 **통과 심볼**(정책 관련 유니버스 — 전 상장은 DART 일 한도 초과).
배당·자기주식 5개 연도 + 분할 주요사항보고 10년 창. 멱등 — 재실행 시 기시도 스킵.
산출 분포는 §5 v1.8 ③ 후속 결재의 원료다(가점·네거티브 편입은 결재 후).
"""

import os
import sys

from trading.collectors.dart import DartClient
from trading.collectors.returns import ReturnsStore, collect_returns, collect_split_decisions, collect_splits
from trading.screen.store import CandidateStore


def main() -> int:
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        print("DART_API_KEY 미설정 — 수집 불가(blocked)")
        return 1
    years = int(sys.argv[1]) if len(sys.argv) > 1 else 5

    cand_store = CandidateStore()
    try:
        passed = sorted({r.symbol for r in cand_store.latest_passed()})
    finally:
        cand_store.close()
    if not passed:
        print("통과 후보 없음 — 스크리닝 선행 필요(python -m trading.screen)")
        return 1

    dart = DartClient(key)
    corp_map = dart.corp_code_map()
    stocks = [(s, corp_map.get(s, ("", s))[1] or s) for s in passed]
    store = ReturnsStore()
    try:
        print(f"주주환원·분할 수집 — 통과 {len(stocks)}종목 × 배당/자기주식 {years}개년 + 분할 10년 창")
        loaded, skipped, errors = collect_returns(dart, store, corp_map, stocks, years=years)
        print(f"[배당·자기주식] 적재 {loaded} · 스킵 {skipped} · 오류 {len(errors)}")
        found, s_skipped, s_errors = collect_splits(dart, store, corp_map, stocks)
        print(f"[분할 이력] 이력 보유 {found} · 스킵 {s_skipped} · 오류 {len(s_errors)}")
        # v2.18(COLLECT-5 ② 결재 2026-09-04): 이력 보유 종목의 구조화 결정(인적/물적) — 새 공시 잡히면 재수집
        d_got, d_skipped, d_errors = collect_split_decisions(dart, store, corp_map, stocks)
        print(f"[분할 결정(인적/물적)] 결정 확보 {d_got} · 무이력/미수록 {d_skipped} · 오류 {len(d_errors)}")
        for e in (errors + s_errors + d_errors)[:8]:
            print(f"  오류: {e}")
        print("→ data/returns.sqlite (attempts 멱등 — 중단 시 재실행으로 이어짐)")
        return 0 if not (errors or s_errors or d_errors) else 1
    finally:
        store.close()


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
