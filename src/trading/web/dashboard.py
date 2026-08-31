"""대시보드(P-16 V1) — 결정 뷰: 통과 종목·진입 존·주간 변화가 첫 화면에서 즉답된다."""

import html
from urllib.parse import quote

from trading.contracts.longterm import CyclePhase, PHASE_LEGEND_KO
from trading.web.data import (
    freshness_rows,
    industry_rows,
    passed_delta,
    phase_transitions,
    screen_funnel,
    stock_names,
    whitelist_groups,
)
from trading.web.glossary import phase_pill, tip
from trading.web.layout import page
from trading.web.svg import BandRow, band_chart, funnel_chart, progress_bar

ENTRY_PHASES = {CyclePhase.BOTTOMING, CyclePhase.RECOVERING}


def render_dashboard() -> str:
    records = industry_rows()
    wl = whitelist_groups()
    transitions = phase_transitions()
    stages, passed = screen_funnel()
    fresh = freshness_rows()
    names = stock_names()
    new_passed, dropped = passed_delta()

    # --- 핵심 카드 1: 통과 종목 ---
    card1 = ["<div class='card hero'><h2 style='margin-top:0'>통과 종목 "
             f"({len(passed)})</h2>"]
    if passed:
        for c in passed:
            pct = f"{c.industry_pbr_pct:.0%}" if c.industry_pbr_pct is not None else "—"
            name = names.get(c.symbol, c.symbol)
            card1.append(
                f"<div class='big'><a href='/stocks/{c.symbol}'>{html.escape(name)}</a> "
                f"<span class='meta'>{c.symbol}</span></div>"
                f"<div>{html.escape(c.industry)} {phase_pill(c.phase)} · "
                f"{tip('sector_pct', '산업내 PBR')} 하위 {pct}</div>"
                f"<div class='meta'>관찰 후보(매수 결정 아님) — 종목명 클릭 시 근거·심사 패킷</div>"
            )
    else:
        card1.append("<div class='meta'>없음 — 진입 존 산업 부재 또는 필터 전체 탈락"
                     "(이미 오른 곳을 쫓지 않는 규칙의 정상 상태일 수 있음)</div>")
    card1.append("</div>")

    # --- 핵심 카드 2: 진입 존 산업 ---
    entry = [r for r in records if r.phase in ENTRY_PHASES]
    card2 = [f"<div class='card'><h2 style='margin-top:0'>{tip('phase', '진입 존 산업')} "
             f"({len(entry)})</h2>"]
    if entry:
        for r in entry:
            pct = (
                f"{r.axes_primary.sector_pbr_band_pct:.0%}"
                if r.axes_primary.sector_pbr_band_pct is not None
                else "—"
            )
            wl_mark = " ✓" if r.industry in wl else ""
            card2.append(
                f"<div><a href='/industries/{quote(r.industry)}'>"
                f"{html.escape(r.industry)}</a>{wl_mark} {phase_pill(r.phase)} "
                f"<span class='meta'>{tip('band_pct', 'PBR밴드')} {pct}</span></div>"
            )
        card2.append("<div class='meta'>✓=화이트리스트(편입 대상). 나머지는 계측만</div>")
    else:
        card2.append("<div class='meta'>바닥 통과·회복 산업 없음</div>")
    card2.append("</div>")

    # --- 핵심 카드 3: 이번 주 변화 ---
    card3 = ["<div class='card'><h2 style='margin-top:0'>변화 (직전 산출 대비)</h2>"]
    changed = False
    for industry, prev, cur in transitions:
        card3.append(f"<div>{html.escape(industry)}: {prev} → <b>{cur}</b></div>")
        changed = True
    for sym in sorted(new_passed):
        card3.append(f"<div>후보 진입: <a href='/stocks/{sym}'>{html.escape(names.get(sym, sym))}</a></div>")
        changed = True
    for sym in sorted(dropped):
        card3.append(f"<div>후보 이탈: {html.escape(names.get(sym, sym))}</div>")
        changed = True
    if not changed:
        card3.append("<div class='meta'>변화 없음 — 대부분의 주는 이 상태가 정상입니다</div>")
    card3.append("</div>")

    band_rows = [
        BandRow(
            label=r.industry,
            pct=r.axes_primary.sector_pbr_band_pct,
            phase=r.phase.value,
            temperature=r.temperature,
            whitelisted=r.industry in wl,
        )
        for r in records
    ]

    parts = [
        "<h1>대시보드</h1>",
        "<div class='meta'>페이퍼 모드(실집행 없음), 읽기 전용. 일간 축적 평일 18:00, "
        "주간 계측 토 09:30 자동. 지표 위에 마우스를 올리면 설명이 나옵니다.</div>",
        "<div class='grid3'>", *card1, *card2, *card3, "</div>",
        f"<h2>산업 온도 지도 — {tip('band_pct', 'PBR 밴드 위치')} 낮을수록 역사적 저평가</h2>",
        f"<div class='card scroll'>{band_chart(band_rows)}",
        f"<div class='meta'>{html.escape(PHASE_LEGEND_KO)} · ✓=화이트리스트</div></div>",
        f"<h2>{tip('r4', 'R4 스크리너')} 깔때기 — 규칙이 거른 경로</h2>",
        f"<div class='card scroll'>{funnel_chart(stages)}</div>",
        "<details><summary>데이터 신선도 보기</summary><div class='card'><table>",
    ]
    for f in fresh:
        bar = (
            progress_bar(f.window, f.target)
            if f.window is not None and f.target is not None
            else ""
        )
        parts.append(
            f"<tr><th style='width:150px'>{html.escape(f.label)}</th>"
            f"<td>{html.escape(f.detail)} {bar}</td></tr>"
        )
    parts.append("</table></div></details>")
    return page("대시보드 — 트레이딩 v0.3", "\n".join(parts), active="/")


__all__ = ["render_dashboard"]
