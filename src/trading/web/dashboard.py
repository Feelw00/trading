"""대시보드(P-16 V1) — 결정 뷰: 통과 종목·진입 존·주간 변화가 첫 화면에서 즉답된다."""

import html
from collections import Counter
from urllib.parse import quote

from trading.contracts.longterm import CycleRecord, CyclePhase, PHASE_LEGEND_KO, phase_ko
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
from trading.web.svg import (
    PHASE_COLOR,
    BandRow,
    band_chart,
    donut_chart,
    funnel_chart,
    progress_bar,
)

_FUNNEL_TITLES = [
    "화이트리스트 산업의 밸류에이션 산출 종목 전체",
    "산업 국면이 바닥 통과·회복(진입 존)이고 구조적 사양이 아닌 종목",
    "산업 내 PBR 하위 40% 이내(산업 대비 저평가)",
    "적자 2년 미만 · 부채비율 200% 이하(금융 면제) · 최신 연간 흑자 · ROE 5y중앙 3% 이상",
    "전 필터 통과 — 관찰 후보(매수 결정 아님)",
]

ENTRY_PHASES = {CyclePhase.BOTTOMING, CyclePhase.RECOVERING}


def render_dashboard() -> str:
    records = industry_rows()
    wl = whitelist_groups()
    transitions = phase_transitions()
    stages, passed = screen_funnel()
    fresh = freshness_rows()
    names = stock_names()
    new_passed, dropped = passed_delta()

    # --- 핵심 카드 1: 가치 후보(P-18 — 국면 우선순위 정렬, 상위 10 + 전수 카운트) ---
    prio = {
        CyclePhase.BOTTOMING: 0, CyclePhase.RECOVERING: 1, CyclePhase.UNKNOWN: 2,
        CyclePhase.DECLINING: 3, CyclePhase.OVERHEATED: 4,
    }
    passed = sorted(
        passed,
        key=lambda c: (
            prio.get(c.phase, 9),
            c.market_pbr_pct if c.market_pbr_pct is not None else 2.0,
        ),
    )
    caution_n = sum(1 for c in passed if c.cycle_caution)
    card1 = ["<div class='card hero'><h2 style='margin-top:0'>가치 후보 "
             f"({len(passed)}, 과열 ⚠ {caution_n})</h2>"]
    if passed:
        card1.append("<div class='meta'>가치·건전성 게이트 통과(P-18). 바닥·회복 산업 우선, "
                     "⚠=과열 산업(천천히). 관찰 후보 — 매수 결정 아님</div>")
        for c in passed[:10]:
            ind = f"{c.industry_pbr_pct:.0%}" if c.industry_pbr_pct is not None else "—"
            mkt = f"{c.market_pbr_pct:.0%}" if c.market_pbr_pct is not None else "—"
            name = names.get(c.symbol, c.symbol)
            warn = " ⚠" if c.cycle_caution else ""
            card1.append(
                f"<div><a href='/stocks/{c.symbol}'>{html.escape(name)}</a> "
                f"<span class='meta'>{c.symbol}</span> · {html.escape(c.industry)} "
                f"{phase_pill(c.phase)}{warn} <span class='meta'>산업내 {ind} · 시장 {mkt}</span></div>"
            )
        if len(passed) > 10:
            card1.append(f"<div class='meta'>상위 10만 표시 — 전수 {len(passed)}종은 "
                         "<a href='/stocks'>종목</a>에서</div>")
    else:
        card1.append("<div class='meta'>없음 — 가치·건전성 통과 종목 없음</div>")
    card1.append("</div>")

    # --- 핵심 카드 2: 우선순위 존 산업(P-18 — 게이트 아님, 후보 정렬·사이징 도구) ---
    entry = [r for r in records if r.phase in ENTRY_PHASES]
    card2 = [f"<div class='card'><h2 style='margin-top:0'>{tip('phase', '우선순위 존 산업')} "
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
        card2.append("<div class='meta'>바닥·회복 산업의 가치 후보가 우선 정렬됩니다(P-18 — "
                     "게이트 아님). ✓=사이클 계측 신뢰 라벨(구 화이트리스트)</div>")
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
        "<h2>산업 한눈에 — 국면 분포와 히트맵</h2>",
        "<div class='grid2'>",
        f"<div class='card'><div class='meta'>{tip('phase', '국면')} 분포 (계측 산업 전체)</div>"
        f"{donut_chart(_phase_segments(records))}{_phase_legend(records)}</div>",
        f"<div class='card'><div class='meta'><b>편입 심사 대상(화이트리스트)</b> — 색=국면, "
        f"숫자={tip('band_pct', 'PBR밴드')}·{tip('temp', '온도')}, 타일 클릭 시 산업 상세</div>"
        f"{_heat_tiles([r for r in records if r.industry in wl], wl)}"
        f"<div class='meta' style='margin-top:8px'><b>전 시장 관찰(KRX 버킷)</b> — 판정·편입 미사용</div>"
        f"{_heat_tiles([r for r in records if r.industry not in wl], wl)}</div>",
        "</div>",
        f"<h2>산업 온도 지도 — {tip('band_pct', 'PBR 밴드 위치')} 낮을수록 역사적 저평가</h2>",
        f"<div class='card scroll'>{band_chart(band_rows)}",
        f"<div class='meta'>{html.escape(PHASE_LEGEND_KO)} · ✓=화이트리스트</div></div>",
        f"<h2>{tip('r4', 'R4 스크리너')} 깔때기 — 규칙이 거른 경로</h2>",
        f"<div class='card scroll'>{funnel_chart(stages, titles=_FUNNEL_TITLES)}<div class='meta'>단계 바 위에 마우스를 올리면 거르는 기준이 나옵니다</div></div>",
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


def _phase_segments(records: list[CycleRecord]) -> list[tuple[str, int, str]]:
    counts = Counter(r.phase for r in records)
    order = [
        CyclePhase.BOTTOMING, CyclePhase.RECOVERING, CyclePhase.OVERHEATED,
        CyclePhase.DECLINING, CyclePhase.UNKNOWN,
    ]
    return [(phase_ko(ph), counts.get(ph, 0), PHASE_COLOR[ph.value]) for ph in order]


def _phase_legend(records: list[CycleRecord]) -> str:
    counts = Counter(r.phase for r in records)
    items = [
        f"{phase_pill(ph)} {n}"
        for ph, n in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
    return f"<div class='meta' style='margin-top:6px'>{' · '.join(items)}</div>"


def _heat_tiles(records: list[CycleRecord], wl: set[str]) -> str:
    def key(r: CycleRecord) -> float:
        pct = r.axes_primary.sector_pbr_band_pct
        return pct if pct is not None else 2.0

    tiles = ["<div class='tiles'>"]
    for r in sorted(records, key=key):
        pct = r.axes_primary.sector_pbr_band_pct
        pct_s = f"{pct:.0%}" if pct is not None else "결측"
        temp_s = f" · 온도 {r.temperature}" if r.temperature is not None else ""
        mark = " ✓" if r.industry in wl else ""
        tiles.append(
            f"<a class='tile' href='/industries/{quote(r.industry)}' "
            f"style='background:{PHASE_COLOR[r.phase.value]}'>"
            f"<b>{html.escape(r.industry)}{mark}</b>"
            f"<small>{pct_s}{temp_s}</small></a>"
        )
    tiles.append("</div>")
    return "".join(tiles)


__all__ = ["render_dashboard"]
