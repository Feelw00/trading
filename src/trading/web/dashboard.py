"""대시보드 페이지 조립 — 산업 온도 지도·국면 전환·R4 깔때기·통과 카드·신선도 스트립."""

import html

from trading.contracts.longterm import PHASE_LEGEND_KO, phase_ko
from trading.web.data import (
    freshness_rows,
    industry_rows,
    phase_transitions,
    screen_funnel,
    whitelist_groups,
)
from trading.web.layout import page
from trading.web.svg import BandRow, band_chart, funnel_chart, progress_bar


def render_dashboard() -> str:
    records = industry_rows()
    wl = whitelist_groups()
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
    transitions = phase_transitions()
    stages, passed = screen_funnel()
    fresh = freshness_rows()

    parts = [
        "<h1>대시보드</h1>",
        "<div class='meta'>페이퍼 모드, 읽기 전용. 일간 축적 평일 18:00, 주간 계측 토 09:30 자동.</div>",
        "<h2>산업 온도 지도 (PBR 밴드 위치 — 낮을수록 역사적 저평가, ✓=화이트리스트)</h2>",
        f"<div class='card scroll'>{band_chart(band_rows)}",
        f"<div class='meta'>{html.escape(PHASE_LEGEND_KO)}</div></div>",
        "<h2>국면 전환 (직전 산출 대비)</h2>",
    ]
    if transitions:
        parts.append("<div class='card'>")
        for industry, prev, cur in transitions:
            parts.append(
                f"<span class='pill warn'>{html.escape(industry)}: {prev} → {cur}</span>"
            )
        parts.append("</div>")
    else:
        parts.append("<div class='card meta'>전환 없음 — 대부분의 주는 변화 없음이 정상입니다.</div>")

    parts += ["<div class='grid2'>", "<div><h2>R4 스크리너 깔때기</h2>",
              f"<div class='card scroll'>{funnel_chart(stages)}</div></div>",
              "<div><h2>통과 후보</h2>"]
    if passed:
        for c in passed:
            pct = (
                f"{c.industry_pbr_pct:.0%}" if c.industry_pbr_pct is not None else "결측"
            )
            parts.append(
                f"<div class='card'><b>{c.symbol}</b> [{html.escape(c.industry)}] "
                f"국면 {phase_ko(c.phase)}, 산업 내 PBR 하위 {pct}<br>"
                f"<span class='meta'>미적용 필터 {len(c.unapplied)}건 — 상세는 "
                f"<a href='/reports'>보고서</a></span></div>"
            )
    else:
        parts.append("<div class='card meta'>통과 후보 없음 — 정상 상태일 수 있습니다.</div>")
    parts.append("</div></div>")

    parts.append("<h2>데이터 신선도</h2><div class='card'><table>")
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
    parts.append("</table></div>")
    return page("대시보드 — 트레이딩 v0.3", "\n".join(parts), active="/")


__all__ = ["render_dashboard"]
