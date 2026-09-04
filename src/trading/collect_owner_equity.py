"""`python -m trading.collect_owner_equity [--years N] [--max-calls N]` — 지배기업 소유주지분·귀속 순이익 수집.

1단계(COLLECT-6, 2026-09-01): 각 종목 **최신 BS 스냅샷**과 같은 (연도, 보고서)의 CFS 전체 재무제표에서
지배주주지분 1계정 — 밸류에이션 레코드 PBR 분모.
2단계(P-20 ④, 2026-09-04): 연간(11011) **과거 연도 백필** — 같은 1콜에서 지배주주지분(BS)·지배주주 귀속
당기순이익(IS)·사업보고서 접수일을 함께 박제 → 자기 역사 PBR 밴드 분모 승격(현재·과거 동시)·PER 분모·as-of
적용일 정밀화. 우선순위 = R4 통과 종목 → 전 종목 최신 연도 → 나머지 연도(DART 일 한도 20,000콜 안에서
``--max-calls`` 예산으로 끊고, attempts 멱등으로 다음 실행이 이어간다). 수집 후 밸류에이션 재산출 필요.
"""

import os
import sys
from collections.abc import Sequence

from trading.collectors.dart import DartClient
from trading.collectors.fins import FinStore, backfill_owner_annuals, collect_owner_equity

DEFAULT_YEARS = 7          # 밴드 5년 창 + as-of 시차(band.BAND_FISCAL_YEARS와 동일)
DEFAULT_MAX_CALLS = 600    # weekly-v3 정기 경로의 회당 예산(≈1.5분) — 수동 실행은 인자로 확대


def _passed_symbols() -> set[str]:
    """R4 통과 종목(밴드 분모 승격·PER 소비 우선) — 후보 DB 없으면 빈 집합(우선순위 없이 진행)."""
    try:
        from trading.screen.store import CandidateStore

        cs = CandidateStore()
        try:
            return {r.symbol for r in cs.latest_passed()}
        finally:
            cs.close()
    except Exception:  # noqa: BLE001
        return set()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    key = os.environ.get("DART_API_KEY", "")
    if not key:
        print("DART_API_KEY 미설정 — 수집 불가(blocked)")
        return 1
    years = int(args[args.index("--years") + 1]) if "--years" in args else DEFAULT_YEARS
    max_calls = int(args[args.index("--max-calls") + 1]) if "--max-calls" in args else DEFAULT_MAX_CALLS
    dart = DartClient(key)
    corp_map = dart.corp_code_map()
    store = FinStore()
    try:
        passed = _passed_symbols()
        syms = sorted(store.symbols())
        syms = [s for s in syms if s in passed] + [s for s in syms if s not in passed]
        stocks = [(s, corp_map.get(s, ("", s))[1] or s) for s in syms]
        print(f"지배주주지분 수집(COLLECT-6) — fins 유니버스 {len(stocks)}종목")
        loaded, skipped, errors = collect_owner_equity(dart, store, corp_map, stocks)
        print(f"적재 {loaded} · 스킵(OFS·계정부재·매핑없음) {skipped} · 오류 {len(errors)}")
        for e in errors[:8]:
            print(f"  오류: {e}")
        res = backfill_owner_annuals(dart, store, corp_map, stocks, years=years, max_calls=max_calls, priority=passed)
        print(
            f"연간 백필(P-20 ④, 최근 {years}개년, 예산 {max_calls}콜): 호출 {res.calls} · 적재 {res.loaded} · "
            f"계정부재 {res.no_account} · CFS없음 {res.empty} · 잔여 {res.remaining} · 오류 {len(res.errors)}"
        )
        for e in res.errors[:8]:
            print(f"  오류: {e}")
        print("→ data/fins.sqlite (다음: python -m trading.valuation 재산출)")
        return 0 if not (errors or res.errors) else 1
    finally:
        store.close()


__all__ = ["DEFAULT_MAX_CALLS", "DEFAULT_YEARS", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
