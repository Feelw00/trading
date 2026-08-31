"""`python -m trading.screen` — R4 페이퍼 스크리닝(PROPOSED_R4 = §5 결재 전 계측·보고 전용).

편입 확정·집행 연결은 §5 결재 후. 탈락 사유 분포와 미적용 필터를 함께 출력한다.
"""

from trading.cycle.store import CycleStore
from trading.screen.rules import PROPOSED_R4, UNAPPLIED_V1
from trading.screen.run import run_screen
from trading.screen.store import CandidateStore
from trading.valuation.store import ValuationStore


def main() -> int:
    val_store, cycle_store, cand_store = ValuationStore(), CycleStore(), CandidateStore()
    try:
        records, s = run_screen(val_store, cycle_store, params=PROPOSED_R4)
        print("R4 페이퍼 스크리닝 (PROPOSED_R4 — §5 결재 전 계측·보고 전용)")
        print(f"평가 {s.evaluated}종목 · 통과 {s.passed}종목")
        for r in records:
            cand_store.append(r)
        if s.passed:
            from trading.contracts.longterm import CyclePhase, phase_ko

            # P-18: 사이클은 도구 — 통과 후보를 국면 우선순위(바닥·회복 먼저)로 정렬만 한다
            prio = {
                CyclePhase.BOTTOMING: 0, CyclePhase.RECOVERING: 1, CyclePhase.UNKNOWN: 2,
                CyclePhase.DECLINING: 3, CyclePhase.OVERHEATED: 4,
            }
            passed_recs = sorted(
                (r for r in records if r.passed),
                key=lambda r: (prio.get(r.phase, 9), r.market_pbr_pct if r.market_pbr_pct is not None else 2.0),
            )
            print("\n통과 후보(국면 우선순위 → 시장 percentile 순, 상위 40):")
            for r in passed_recs[:40]:
                ind = f"{r.industry_pbr_pct:.0%}" if r.industry_pbr_pct is not None else "?"
                mkt = f"{r.market_pbr_pct:.0%}" if r.market_pbr_pct is not None else "?"
                warn = " ⚠과열" if r.cycle_caution else ""
                print(
                    f"  · {r.symbol} [{r.industry}] 국면={phase_ko(r.phase)}{warn} "
                    f"산업내 {ind} · 시장 {mkt}"
                )
            if len(passed_recs) > 40:
                print(f"  … 외 {len(passed_recs) - 40}종(전수는 DB·웹)")
        print("\n탈락 사유 분포:")
        for reason, n in s.reject_counts.items():
            print(f"  {n:>4} × {reason}")
        if s.skipped_industries:
            print("판정 불가 산업: " + ", ".join(s.skipped_industries))
        print("\n미적용 필터(데이터 미확보 — 통과≠전 필터 통과):")
        for u in UNAPPLIED_V1:
            print(f"  - {u}")
        print("\n→ data/candidates.sqlite (탈락 포함 전수 박제)")
    finally:
        val_store.close()
        cycle_store.close()
        cand_store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
