"""종목 페이지 렌더 — 목록(정렬·존 필터) + 상세(차트·R4 사유·심사 패킷). W2."""

import html

from trading.contracts.longterm import PHASE_LEGEND_KO, phase_ko
from trading.web.glossary import phase_pill, tip
from trading.web.layout import page
from trading.web.stocks_data import StockDetail, StockRow, stock_detail, stock_rows
from trading.web.svg import dual_bar_chart, line_chart

_SORT_JS = """
<script>
function sortBy(th) {
  const table = th.closest('table'), idx = [...th.parentNode.children].indexOf(th);
  const dir = th.dataset.dir === 'a' ? -1 : 1; th.dataset.dir = dir === 1 ? 'a' : 'd';
  const rows = [...table.tBodies[0].rows];
  rows.sort((r1, r2) => {
    const a = r1.cells[idx].dataset.v, b = r2.cells[idx].dataset.v;
    if (a === '' && b === '') return 0; if (a === '') return 1; if (b === '') return -1;
    const na = parseFloat(a), nb = parseFloat(b);
    if (!isNaN(na) && !isNaN(nb)) return (na - nb) * dir;
    return a.localeCompare(b) * dir;
  });
  rows.forEach(r => table.tBodies[0].appendChild(r));
}
function applyFilters() {
  const wlOnly = document.getElementById('wl-only').checked;
  const ind = document.getElementById('ind-filter').value;
  document.querySelectorAll('#all-table tbody tr').forEach(tr => {
    const okWl = !wlOnly || tr.dataset.wl === '1';
    const okInd = !ind || tr.dataset.ind === ind;
    tr.style.display = (okWl && okInd) ? '' : 'none';
  });
}
</script>
"""


def _fmt(v: float | None, spec: str = ".2f") -> str:
    return f"{v:{spec}}" if v is not None else "—"


def _num_td(v: float | None, spec: str = ".2f") -> str:
    raw = f"{v}" if v is not None else ""
    return f"<td data-v='{raw}'>{_fmt(v, spec)}</td>"


_HEADS = [
    (None, "종목"), (None, "산업/업종"), ("pbr", None), ("per", None), ("psr", None),
    ("roe", None), ("roe_median", None), ("debt", None), ("sector_pct", None),
    ("loss5y", None), ("r4", None),
]


def _head_row() -> str:
    cells = []
    for key, label in _HEADS:
        inner = tip(key) if key else html.escape(label or "")
        cells.append(f"<th onclick='sortBy(this)' style='cursor:pointer'>{inner}</th>")
    return "<tr>" + "".join(cells) + "</tr>"


def _stock_tr(r: StockRow) -> str:
    v = r.val
    wl = "1" if r.industry else "0"
    label = r.industry or r.val.sector_krx or "—"
    if r.r4 == "통과":
        r4_cell, r4_sort, row_cls = "<span class='ph ph-bott'>통과</span>", "통과", " class='passed-row'"
    else:
        r4_cell = r4_sort = html.escape(r.r4.split("(")[0])
        row_cls = ""
    return (
        f"<tr data-wl='{wl}' data-ind='{html.escape(label)}'{row_cls}>"
        f"<td data-v='{r.name}'><a href='/stocks/{r.symbol}'>{html.escape(r.name)}</a> "
        f"<span class='meta'>{r.symbol}</span></td>"
        f"<td data-v='{html.escape(label)}'>{html.escape(label)}{' ✓' if r.industry else ''}</td>"
        f"{_num_td(v.pbr)}{_num_td(v.per, '.1f')}{_num_td(v.psr)}"
        f"{_num_td(v.roe, '+.1%')}{_num_td(v.roe_median_5y, '+.1%')}{_num_td(v.debt_ratio)}"
        f"{_num_td(v.sector_pbr_pct, '.0%')}"
        f"<td data-v='{v.loss_years_5y if v.loss_years_5y is not None else ''}'>"
        f"{v.loss_years_5y if v.loss_years_5y is not None else '—'}년/{v.loss_years_observed or 0}년</td>"
        f"<td data-v='{r4_sort}'>{r4_cell}</td></tr>"
    )


def render_list(rows: list[StockRow] | None = None) -> str:
    rows = stock_rows() if rows is None else rows
    passed = [r for r in rows if r.r4 == "통과"]
    industries = sorted({r.industry or r.val.sector_krx or "—" for r in rows})
    options = "".join(f"<option value='{html.escape(i)}'>{html.escape(i)}</option>" for i in industries)

    parts = [
        "<h1>종목</h1>",
        f"<div class='meta'>밸류에이션 산출 {len(rows)}종목. 열 제목 클릭=정렬, "
        "지표 위에 마우스를 올리면 설명이 나옵니다.</div>",
        f"<h2>{tip('r4', 'R4 통과')} ({len(passed)}) — 관찰 후보</h2>",
    ]
    if passed:
        parts.append("<div class='card hero scroll'><table><thead>")
        parts.append(_head_row())
        parts.append("</thead><tbody>")
        parts += [_stock_tr(r) for r in passed]
        parts.append("</tbody></table></div>")
    else:
        parts.append("<div class='card meta'>통과 종목 없음 — 정상 상태일 수 있습니다</div>")

    parts += [
        f"<h2>전체 평가 유니버스 ({len(rows)})</h2>",
        "<div class='meta'><label><input type='checkbox' id='wl-only' "
        "onchange='applyFilters()'> 화이트리스트만</label> &nbsp; 산업: "
        "<select id='ind-filter' onchange='applyFilters()'>"
        f"<option value=''>전체</option>{options}</select></div>",
        "<div class='card scroll'><table id='all-table'><thead>",
        _head_row(),
        "</thead><tbody>",
    ]
    parts += [_stock_tr(r) for r in rows]
    parts.append("</tbody></table></div>")
    parts.append(_SORT_JS)
    return page("종목 — 트레이딩 v0.3", "\n".join(parts), active="/stocks")


def _downsample(pairs: list[tuple[str, float]], limit: int = 130) -> list[tuple[str, float]]:
    if len(pairs) <= limit:
        return pairs
    step = len(pairs) / limit
    return [pairs[int(i * step)] for i in range(limit - 1)] + [pairs[-1]]


def render_detail(symbol: str) -> str | None:
    d: StockDetail | None = stock_detail(symbol)
    if d is None:
        return None
    v = d.row.val
    title = f"{d.row.name} ({symbol})"
    parts = [
        f"<h1>{html.escape(title)}</h1>",
        f"<div class='meta'>{html.escape(d.row.industry or d.row.val.sector_krx or '')} · "
        f"재무 기준 {html.escape(v.fin_basis or '—')} · as_of {str(v.as_of)[:10]}</div>",
        "<h2>밸류에이션</h2><div class='card'><table><tr>",
        f"<th>{tip('pbr')}</th><td>{_fmt(v.pbr)}</td><th>{tip('per')}</th><td>{_fmt(v.per, '.1f')}</td>"
        f"<th>{tip('psr')}</th><td>{_fmt(v.psr)}</td><th>{tip('roe')}</th><td>{_fmt(v.roe, '+.1%')}</td></tr><tr>"
        f"<th>{tip('roe_median')}</th><td>{_fmt(v.roe_median_5y, '+.1%')}</td>"
        f"<th>{tip('debt')}</th><td>{_fmt(v.debt_ratio)}</td>"
        f"<th>{tip('sector_pct')}</th><td>하위 {_fmt(v.sector_pbr_pct, '.0%')}</td>"
        f"<th>{tip('loss5y')}</th><td>{v.loss_years_5y if v.loss_years_5y is not None else '—'}년"
        f"/{v.loss_years_observed or 0}년 관측</td></tr></table></div>",
    ]

    if d.closes:
        pairs = _downsample(d.closes)
        parts += [
            "<h2>주가 (2021~)</h2>",
            f"<div class='card scroll'>{line_chart([c for _dt, c in pairs], labels=[dt for dt, _c in pairs], start_label=d.closes[0][0], end_label=d.closes[-1][0])}</div>",
        ]

    if d.annual:
        years = [y for y, _v in reversed(d.annual)]
        rev = [vals['revenue'] for _y, vals in reversed(d.annual)]
        op = [vals['op_income'] for _y, vals in reversed(d.annual)]
        parts += [
            "<h2>연간 재무 (10년) — 종목 레벨 사이클 모양</h2>",
            f"<div class='card scroll'>{dual_bar_chart(years, rev, op, label_a='매출', label_b='영업이익')}</div>",
        ]

    if d.band is not None:
        b = d.band
        cyc_line = (
            f"{tip('phase', '국면')} {phase_pill(b.cycle.phase)} 온도 "
            f"{b.cycle.temperature if b.cycle.temperature is not None else '—'}"
            if b.cycle
            else "국면 미산출"
        )
        amp = f"{b.amplitude:.1f}배" if b.amplitude is not None else "—"
        parts += [
            f"<h2>산업 컨텍스트 — {html.escape(b.group)}</h2>",
            f"<div class='card'>{cyc_line}<br>"
            f"산업 PBR 밴드 {_fmt(b.pbr_lo)}~{_fmt(b.pbr_hi)} (현재 {_fmt(b.pbr_now)}) — "
            f"<b>{tip('amplitude', '사이클 진폭')} {amp}</b><br>"
            f"산업 마진 범위 {_fmt(b.margin_lo, '.1%')}~{_fmt(b.margin_hi, '.1%')}"
            f"<div class='meta'>{html.escape(PHASE_LEGEND_KO)}</div></div>",
        ]

    if d.flows:
        cum_f, cum_o, acc_f, acc_o = [], [], 0.0, 0.0
        flow_dates = [dt for dt, _p, _f, _o in d.flows]
        for _dt, _p, f, o in d.flows:
            acc_f += f or 0.0
            acc_o += o or 0.0
            cum_f.append(acc_f)
            cum_o.append(acc_o)
        parts += [
            f"<h2>수급 누적 순매수 (백만원, 창 {len(d.flows)}거래일)</h2>",
            "<div class='grid2'>",
            f"<div class='card scroll'><div class='meta'>외국인</div>{line_chart(cum_f, labels=flow_dates, color='#2b6cb0')}</div>",
            f"<div class='card scroll'><div class='meta'>기관</div>{line_chart(cum_o, labels=flow_dates, color='#975a16')}</div>",
            "</div>",
        ]

    if d.short_rates or d.lending_balance:
        parts.append("<h2>공매도·대차 (토스 공식, 창 축적 중)</h2><div class='grid2'>")
        if d.short_rates:
            parts.append(
                f"<div class='card scroll'><div class='meta'>공매도 거래 비중</div>"
                f"{line_chart([r for _d, r in d.short_rates], labels=[dt for dt, _r in d.short_rates], color='#c53030', fmt='.1%')}</div>"
            )
        if d.lending_balance:
            parts.append(
                f"<div class='card scroll'><div class='meta'>대차잔고 수량</div>"
                f"{line_chart([q for _d, q in d.lending_balance], labels=[dt for dt, _q in d.lending_balance], color='#6b46c1')}</div>"
            )
        parts.append("</div>")

    parts.append("<h2>R4 판정</h2>")
    if d.candidate is None:
        parts.append("<div class='card meta'>평가 대상 아님(화이트리스트 밖)</div>")
    elif d.candidate.passed:
        parts.append("<div class='card'><span class='pill'>통과</span> "
                     f"미적용 필터: {html.escape(', '.join(d.candidate.unapplied) or '없음')}</div>")
    else:
        items = "".join(f"<li>{html.escape(r)}</li>" for r in d.candidate.reject_reasons)
        parts.append(f"<div class='card'><b>탈락 사유(전수)</b><ul>{items}</ul></div>")

    if d.dossier is not None:
        bull = "".join(f"<li>{html.escape(x)}</li>" for x in d.dossier.bull_case)
        bear = "".join(f"<li>{html.escape(x)}</li>" for x in d.dossier.bear_case)
        parts.append(
            f"<h2>심사 패킷 (서술 {html.escape(d.dossier.model)} — 판정 미입력 참고 기록)</h2>"
            f"<div class='card'><b>긍정 논거</b><ul>{bull}</ul>"
            f"<b>반박 논거(의무)</b><ul>{bear}</ul></div>"
        )

    return page(f"{title} — 트레이딩 v0.3", "\n".join(parts), active="/stocks")


__all__ = ["render_detail", "render_list"]
