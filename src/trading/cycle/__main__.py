"""`python -m trading.cycle` — R3 온도계 산출(DB-first, 제안 파라미터 = 계측·보고 전용).

연말 스냅샷 일자는 DB에서 자동 발견(연도별 12월 최대 적재일). `--replay`는 과거 연도별
as-of 판정 시계열을 출력한다(룩어헤드 금지 — 그 시점 이전 데이터만).
"""

import sys
from collections import Counter

from trading.collectors.base import now_kst
from trading.collectors.fins import FinStore
from trading.collectors.market import MarketStore
from trading.cycle.bands import build_sector_years
from trading.cycle.engine import PROPOSED_PARAMS, assess_all, to_record
from trading.cycle.policy import CURATED_GROUPS, POLICY_VERSION
from trading.cycle.store import CycleStore


def discover_year_ends(dates: list[str]) -> dict[str, str]:
    """적재 일자에서 연도별 연말 스냅샷(12월 최대일) + 최신일(current)을 발견."""
    out: dict[str, str] = {}
    for ymd in dates:
        year, month = ymd[:4], ymd[4:6]
        if month == "12" and (year not in out or ymd > out[year]):
            out[year] = ymd
    if dates:
        out["current"] = max(dates)
    return out


def main() -> int:
    replay = "--replay" in sys.argv[1:]
    fins, market = FinStore(), MarketStore()
    store = CycleStore()
    try:
        year_ends = discover_year_ends(market.dates())
        sector_years = build_sector_years(
            fins, market, year_end_dates=year_ends, extra_groups=CURATED_GROUPS
        )
        now = now_kst()

        if replay:
            years = sorted((y for y in year_ends if y != "current"), reverse=True)[:5]
            print(f"as-of 리플레이 ({POLICY_VERSION}): {', '.join(reversed(years))}")
            for sector in sorted(sector_years):
                cells = []
                for y in reversed(years):
                    a = next(
                        x for x in assess_all(
                            {sector: sector_years[sector]}, at=y, params=PROPOSED_PARAMS
                        )
                    )
                    cells.append(f"{y[2:]}:{a.phase.value[:4]}")
                print(f"{sector:<14} " + " ".join(cells))
            return 0

        assessments = assess_all(sector_years, at="current", params=PROPOSED_PARAMS)
        print(f"R3 온도계 ({POLICY_VERSION}) · 기준일 {year_ends.get('current')}")
        print(f"{'섹터':<14} {'국면':<11} {'온도':>4} {'PBR밴드':>7} {'마진밴드':>7} {'매출z':>6} {'개선':>4} {'사양':>4}")
        def fmt(v: float | int | None, p: str) -> str:
            return f"{v:{p}}" if v is not None else "결측"

        for a in assessments:
            print(
                f"{a.sector:<14} {a.phase.value:<11} {fmt(a.temperature, '>4')} "
                f"{fmt(a.pbr_band_pct, '>7.0%')} {fmt(a.margin_band_pct, '>7.0%')} "
                f"{fmt(a.rev_cycle_z, '>6.2f')} "
                f"{'예' if a.improving else '—' if a.improving is not None else '?':>4} "
                f"{'⚠' if a.secular_decline else '—' if a.secular_decline is not None else '?':>4}"
            )
            store.append(
                to_record(
                    a,
                    as_of=now,
                    fetched_at=now,
                    evidence=[f"bands:{a.sector}:{year_ends.get('current', '?')}"],
                )
            )
        dist = Counter(a.phase.value for a in assessments)
        print(f"\n국면 분포: {dict(dist)} → data/cycle.sqlite ({store.count()}개 산업)")
    finally:
        fins.close()
        market.close()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
