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
            from trading.collectors.fins import FinStore
            from trading.collectors.returns import ReturnsStore
            from trading.contracts.longterm import CandidateRecord, phase_ko
            from trading.screen.quality import (
                CORE_INDUSTRY_CAP,
                CORE_TOP_N,
                RETURNS_EXEMPT_INDUSTRIES,
                WATCH_TOP_N,
                dividend_streak,
                earnings_quality_flag,
                has_cancellation,
                is_stable_core,
                meets_returns_core,
                revenue_trend,
                stability_metrics,
            )
            from trading.screen.rank import (
                HIGH_PER_THRESHOLD,
                high_per,
                shortlist,
                value_depth,
            )

            # P-18: 사이클은 도구 — 국면 → 이익 축(고PER 강등) → 가치 깊이(병행 미러)
            # → ROE 품질 순 정렬, 심볼 dedup + 산업별 캡(rank.py). 게이트 불변.
            # v1.8: 안정 코어(우상향·5y무적자·ROE최소>2%) / 관찰 2단 표시(quality.py).
            latest_vals = val_store.all_latest()
            roe_by_symbol = {
                v.symbol: v.roe_median_5y for v in latest_vals if v.roe_median_5y is not None
            }
            per_by_symbol = {v.symbol: v.per for v in latest_vals if v.per is not None}

            passed_syms = {r.symbol for r in records if r.passed}
            fin_store = FinStore()
            try:
                metrics = {}
                eq_flags = {}
                trends = {}
                for sym in passed_syms:
                    series = fin_store.annual_series(sym)
                    metrics[sym] = stability_metrics(series)
                    eq_flags[sym] = earnings_quality_flag(series)  # v2.1 이익 질
                    trends[sym] = revenue_trend(series)            # v2.3 역성장 가드
            finally:
                fin_store.close()
            # v1.9: 환원(연속배당·소각)·분할 이력 — returns.sqlite(수집 선행: collect_returns)
            ret_store = ReturnsStore()
            try:
                streaks = {sym: dividend_streak(ret_store.dividend_series(sym)) for sym in passed_syms}
                cancels = {sym: has_cancellation(ret_store.buyback_series(sym)) for sym in passed_syms}
                splits = {sym: len(ret_store.split_history(sym)) for sym in passed_syms}
            finally:
                ret_store.close()
            stable_syms = {sym for sym, m in metrics.items() if is_stable_core(m)}
            # 코어 = 모든 축 정합 — ⚠고PER(v1.8)·환원 미달(v1.9)·분할 이력(v1.9)·
            # ⚠이익질(v2.1 — 영업외 의존 이익)은 코어 자격 배제, 관찰로 강등.
            # 플래그·사유는 관찰 목록에서 그대로 보인다.
            passed_recs = [r for r in records if r.passed]
            core_pool = [
                r for r in passed_recs
                if r.symbol in stable_syms
                and not high_per(r, per_by_symbol)
                and meets_returns_core(
                    streaks[r.symbol], cancels[r.symbol], industry=r.industry
                )
                and splits[r.symbol] == 0
                and not eq_flags[r.symbol]
                and not trends[r.symbol].consecutive_decline
            ]
            core = shortlist(
                core_pool,
                roe_by_symbol=roe_by_symbol, per_by_symbol=per_by_symbol,
                top_n=CORE_TOP_N, per_industry_cap=CORE_INDUSTRY_CAP,
            )
            core_syms = {r.symbol for r in core_pool}
            watch = shortlist(
                [r for r in passed_recs if r.symbol not in core_syms],
                roe_by_symbol=roe_by_symbol, per_by_symbol=per_by_symbol,
                top_n=WATCH_TOP_N,
            )

            def _line(r: CandidateRecord) -> str:
                ind = f"{r.industry_pbr_pct:.0%}" if r.industry_pbr_pct is not None else "?"
                mkt = f"{r.market_pbr_pct:.0%}" if r.market_pbr_pct is not None else "?"
                warn = " ⚠과열" if r.cycle_caution else ""
                if high_per(r, per_by_symbol):
                    warn += f" ⚠고PER {per_by_symbol[r.symbol]:.0f}"
                if splits.get(r.symbol):
                    warn += f" ⚠분할 {splits[r.symbol]}건"
                if eq_flags.get(r.symbol):
                    warn += " ⚠이익질"
                tr = trends.get(r.symbol)
                if tr is not None and tr.consecutive_decline:
                    warn += " ⚠역성장"
                elif tr is not None and tr.sharp_drop:
                    warn += f" ⚠매출급감 {tr.yoy_latest:+.0%}"
                return (
                    f"  · {r.symbol} [{r.industry}] 국면={phase_ko(r.phase)}{warn} "
                    f"깊이 {value_depth(r):.0%}(산업내 {ind}·시장 {mkt})"
                )

            def _returns_tag(r: CandidateRecord) -> str:
                if r.industry in RETURNS_EXEMPT_INDUSTRIES:
                    return "환원 면제(리츠 — COLLECT-5 확인 중)"
                parts = [f"배당 {streaks[r.symbol]}y 연속"] if streaks[r.symbol] else []
                if cancels[r.symbol]:
                    parts.append("소각")
                return " · ".join(parts) if parts else "환원 없음"

            n_core_pool = len({r.symbol for r in core_pool})
            print(
                f"\n안정 코어(매출CAGR>0 · 5y무적자 · ROE최소>2% · 환원(3y+배당∨소각) · "
                f"분할 무이력 · 이익질 정상 · 非역성장 — 산업 캡 {CORE_INDUSTRY_CAP}종, 상위 "
                f"{CORE_TOP_N} / 코어 자격 {n_core_pool}종):"
            )
            for r in core:
                m = metrics[r.symbol]
                cagr = f"{m.revenue_cagr:+.1%}" if m.revenue_cagr is not None else "?"
                rmin = f"{m.roe_min:+.1%}" if m.roe_min is not None else "?"
                print(_line(r) + f" · 매출CAGR {cagr} · ROE최소 {rmin} · {_returns_tag(r)}")
            print(
                f"\n관찰(코어 미달 — PER>{HIGH_PER_THRESHOLD:.0f}·환원 미달·분할 이력 "
                f"강등 포함, 상위 {WATCH_TOP_N}):"
            )
            for r in watch:
                print(_line(r))
            n_passed_symbols = len(passed_syms)
            rest = n_passed_symbols - len(core) - len(watch)
            if rest > 0:
                print(f"  … 외 {rest}종(전수는 DB·웹)")
            print(
                "\n환원·분할 축 주의(COLLECT-5): 리츠 분배금 미관측 의심(면제 처리) · "
                "분할은 인적/물적 미구분(배제 아닌 강등)"
            )
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
