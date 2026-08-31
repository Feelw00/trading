"""산업 페이지(W3) — 목록 + 상세(밴드 3축 차트·리플레이 타임라인·구성 종목)."""

import html
from urllib.parse import quote

from trading.collectors.fins import FinStore
from trading.collectors.market import MarketStore
from trading.contracts.longterm import PHASE_LEGEND_KO, phase_ko
from trading.cycle.bands import SectorYear, build_sector_years, discover_year_ends
from trading.cycle.engine import PROPOSED_PARAMS, assess
from trading.cycle.policy import CURATED_GROUPS, FINANCIAL_PROFILE_GROUPS
from trading.cycle.store import CycleStore
from trading.web.glossary import phase_pill, tip
from trading.web.layout import page
from trading.web.stocks_data import GROUP_TO_INDUSTRY, stock_rows
from trading.web.svg import dual_bar_chart, line_chart, phase_strip


def _fmt(v: float | None, spec: str = ".2f") -> str:
    return f"{v:{spec}}" if v is not None else "—"


def _sector_years() -> dict[str, list[SectorYear]]:
    fins, market = FinStore(), MarketStore()
    try:
        year_ends = discover_year_ends(market.dates())
        if not year_ends:
            return {}
        return build_sector_years(
            fins, market, year_end_dates=year_ends, extra_groups=CURATED_GROUPS
        )
    finally:
        fins.close()
        market.close()


def render_industries_list() -> str:
    sector_years = _sector_years()
    cyc_store = CycleStore()
    try:
        latest = {r.industry: r for r in cyc_store.all_latest()}
    finally:
        cyc_store.close()

    entries = []
    for group, rows in sorted(sector_years.items()):
        cyc = latest.get(group)
        hist = [r.pbr for r in rows if r.year != "current" and r.pbr is not None]
        amp = (max(hist) / min(hist)) if hist and min(hist) > 0 else None
        pct = cyc.axes_primary.sector_pbr_band_pct if cyc else None
        entries.append((pct if pct is not None else 2.0, group, cyc, amp, rows))
    entries.sort(key=lambda e: e[0])

    parts = [
        "<h1>산업</h1>",
        f"<div class='meta'>밴드 그룹 {len(entries)}개, PBR 밴드 위치 오름차순. ✓=화이트리스트. "
        f"{html.escape(PHASE_LEGEND_KO)}</div>",
        "<div class='card scroll'><table><thead><tr><th>산업 그룹</th>"
        f"<th>{tip('phase', '국면')}</th><th>{tip('temp', '온도')}</th>"
        f"<th>{tip('band_pct', 'PBR밴드')}</th><th>{tip('margin_band', '마진밴드')}</th>"
        f"<th>{tip('amplitude', '사이클 진폭')}</th><th>구성</th></tr></thead><tbody>",
    ]
    for _key, group, cyc, amp, rows in entries:
        wl = " ✓" if group in GROUP_TO_INDUSTRY else ""
        n_now = next((r.n_pbr for r in rows if r.year == "current"), 0)
        parts.append(
            f"<tr><td><a href='/industries/{quote(group)}'>{html.escape(group)}</a>{wl}</td>"
            f"<td>{phase_pill(cyc.phase) if cyc else '—'}</td>"
            f"<td>{cyc.temperature if cyc and cyc.temperature is not None else '—'}</td>"
            f"<td>{_fmt(cyc.axes_primary.sector_pbr_band_pct if cyc else None, '.0%')}</td>"
            f"<td>{_fmt(cyc.axes_primary.sector_margin_band_pct if cyc else None, '.0%')}</td>"
            f"<td>{f'{amp:.1f}배' if amp else '—'}</td><td>{n_now}종목</td></tr>"
        )
    parts.append("</tbody></table></div>")
    return page("산업 — 트레이딩 v0.3", "\n".join(parts), active="/industries")


def render_industry_detail(group: str) -> str | None:
    sector_years = _sector_years()
    rows = sector_years.get(group)
    if not rows:
        return None

    ann = [r for r in reversed(rows) if r.year != "current"]  # 연도 오름차순
    years = [r.year for r in ann]
    pbr_pts = [r.pbr for r in ann if r.pbr is not None]
    pbr_years = [r.year for r in ann if r.pbr is not None]
    margin_pts = [(r.year, r.margin) for r in ann if r.margin is not None]
    profile = "financial" if group in FINANCIAL_PROFILE_GROUPS else "industrial"
    strip = [
        (year, assess(rows, at=year, sector=group, params=PROPOSED_PARAMS,
                      profile=profile).phase.value)
        for year in years[-6:]
    ]
    now = assess(rows, at="current", sector=group, params=PROPOSED_PARAMS, profile=profile)
    members = [r for r in stock_rows() if r.group == group]

    industry_label = GROUP_TO_INDUSTRY.get(group)
    parts = [
        f"<h1>{html.escape(group)}{' ✓ ' + html.escape(industry_label) if industry_label else ''}</h1>",
        f"<div class='meta'>현재 국면 {phase_pill(now.phase)}"
        f"{f', 온도 {now.temperature}' if now.temperature is not None else ''} · "
        f"PBR밴드 {_fmt(now.pbr_band_pct, '.0%')} · 마진밴드 {_fmt(now.margin_band_pct, '.0%')} · "
        f"매출z {_fmt(now.rev_cycle_z)}</div>",
        "<h2>국면 타임라인 (as-of 리플레이 — 룩어헤드 없음)</h2>",
        f"<div class='card scroll'>{phase_strip([*strip, ('현재', now.phase.value)])}"
        f"<div class='meta'>{html.escape(PHASE_LEGEND_KO)}</div></div>",
    ]

    if pbr_pts:
        cur = next((r.pbr for r in rows if r.year == "current"), None)
        series = [*pbr_pts, *( [cur] if cur is not None else [] )]
        s_labels = [*pbr_years, *(["현재"] if cur is not None else [])]
        amp = f"{max(pbr_pts) / min(pbr_pts):.1f}배" if min(pbr_pts) > 0 else "—"
        parts += [
            f"<h2>섹터 PBR 밴드 (연말 합산, 진폭 {amp})</h2>",
            f"<div class='card scroll'>{line_chart(series, labels=s_labels, start_label=pbr_years[0], end_label='현재', fmt='.2f')}</div>",
        ]
    if margin_pts:
        parts += [
            "<h2>섹터 영업이익률 (연간)</h2>",
            f"<div class='card scroll'>{line_chart([m for _y, m in margin_pts], labels=[y for y, _m in margin_pts], start_label=margin_pts[0][0], end_label=margin_pts[-1][0], color='#975a16', fmt='.1%')}</div>",
        ]
    revs = [r.revenue for r in ann]
    if any(v is not None for v in revs):
        parts += [
            "<h2>섹터 합산 매출 (연간 — 구조적 사양 판정 원료)</h2>",
            f"<div class='card scroll'>{dual_bar_chart(years, revs, [None] * len(years), label_a='매출', label_b='')}</div>",
        ]

    parts.append(f"<h2>구성 종목 ({len(members)}) — 멤버십 감사</h2>")
    if members:
        parts.append("<div class='card scroll'><table><thead><tr><th>종목</th><th>PBR</th>"
                     "<th>PER</th><th>ROE중앙5y</th><th>산업내PBR</th><th>R4</th></tr></thead><tbody>")
        for m in members:
            r4 = "통과" if m.r4 == "통과" else html.escape(m.r4.split("(")[0])
            parts.append(
                f"<tr><td><a href='/stocks/{m.symbol}'>{html.escape(m.name)}</a> "
                f"<span class='meta'>{m.symbol}</span></td>"
                f"<td>{_fmt(m.val.pbr)}</td><td>{_fmt(m.val.per, '.1f')}</td>"
                f"<td>{_fmt(m.val.roe_median_5y, '+.1%')}</td>"
                f"<td>{_fmt(m.val.sector_pbr_pct, '.0%')}</td><td>{r4}</td></tr>"
            )
        parts.append("</tbody></table></div>")
    else:
        parts.append("<div class='card meta'>밸류에이션 산출 구성 종목 없음</div>")

    return page(f"{group} — 산업", "\n".join(parts), active="/industries")


__all__ = ["render_industries_list", "render_industry_detail"]
