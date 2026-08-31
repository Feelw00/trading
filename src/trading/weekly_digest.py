"""R6 주간 다이제스트(v0.3 §8 — 읽기 전용) — `python -m trading.weekly_digest`.

온도계·R4 페이퍼 결과·데이터 결측 고지를 한 문서로. `.runtime/reports/weekly-<일자>.md` 저장
(+콘솔). 대부분의 주의 결론은 "변화 없음"이어야 정상이다. Telegram 발송은 알림 채널 복구 후.
"""

from pathlib import Path

from trading.collectors.base import now_kst
from trading.collectors.fins import FinStore
from trading.collectors.market import MarketStore
from trading.cycle.bands import build_sector_years, full_year_ends
from trading.cycle.engine import PROPOSED_PARAMS, Assessment, assess_all
from trading.cycle.policy import CURATED_GROUPS, FINANCIAL_PROFILE_GROUPS, POLICY_VERSION, WHITELIST
from trading.cycle.store import CycleStore
from trading.screen.rules import PROPOSED_R4, UNAPPLIED_V1
from trading.screen.run import ScreenSummary, run_screen
from trading.screen.store import CandidateStore
from trading.valuation.store import ValuationStore

from trading.contracts.longterm import PHASE_LEGEND_KO, CandidateRecord, CyclePhase, phase_ko

REPORT_DIR = Path(".runtime") / "reports"


def _fmt(v: float | int | None, spec: str) -> str:
    return f"{v:{spec}}" if v is not None else "결측"


# P-18: 사이클은 도구 — 통과 후보 정렬 기준(바닥·회복 산업 먼저, 그 안에서 시장 PBR 싼 순)
_PHASE_PRIORITY = {
    CyclePhase.BOTTOMING: 0, CyclePhase.RECOVERING: 1, CyclePhase.UNKNOWN: 2,
    CyclePhase.DECLINING: 3, CyclePhase.OVERHEATED: 4,
}


def _candidate_priority(c: CandidateRecord) -> tuple[int, float]:
    return (
        _PHASE_PRIORITY.get(c.phase, 9),
        c.market_pbr_pct if c.market_pbr_pct is not None else 2.0,
    )


def render(
    assessments: list[Assessment],
    candidates: list[CandidateRecord],
    summary: ScreenSummary,
    *,
    basis_date: str,
    dossiers: dict[str, str] | None = None,
) -> str:
    wl_groups = set(WHITELIST.values())
    group_to_industry = {group: industry for industry, group in WHITELIST.items()}

    def _r3_row(a: Assessment, label: str) -> str:
        return (
            f"| {label} | {phase_ko(a.phase)} | {_fmt(a.temperature, '')} "
            f"| {_fmt(a.pbr_band_pct, '.0%')} | {_fmt(a.margin_band_pct, '.0%')} "
            f"| {'예' if a.improving else '—' if a.improving is not None else '?'} "
            f"| {'⚠' if a.secular_decline else '—' if a.secular_decline is not None else '?'} |"
        )

    lines = [
        f"# 주간 다이제스트 — {basis_date} 기준 ({POLICY_VERSION})",
        "",
        "> 읽기 전용. 편입·집행 없음(§5 결재 전 페이퍼). 대부분의 주는 '변화 없음'이 정상.",
        "",
        "## R3 산업 온도계 — 편입 심사 대상 (화이트리스트 큐레이션)",
        "",
        "| 산업 (구성) | 국면 | 온도 | PBR밴드 | 마진밴드 | 개선 | 사양 |",
        "|---|---|---|---|---|---|---|",
    ]
    for a in assessments:
        if a.sector in wl_groups:
            n = len(CURATED_GROUPS.get(a.sector, []))
            lines.append(_r3_row(a, f"{group_to_industry.get(a.sector, a.sector)} ({n}종)"))
    lines += [
        "",
        "### 전 시장 관찰 — KRX 버킷 (판정·편입에 미사용, 참고용)",
        "",
        "| 산업 그룹 | 국면 | 온도 | PBR밴드 | 마진밴드 | 개선 | 사양 |",
        "|---|---|---|---|---|---|---|",
    ]
    for a in assessments:
        if a.sector not in wl_groups:
            lines.append(_r3_row(a, a.sector))
    lines += ["", f"> {PHASE_LEGEND_KO}"]
    passed = sorted((c for c in candidates if c.passed), key=_candidate_priority)
    caution_n = sum(1 for c in passed if c.cycle_caution)
    lines += [
        "",
        f"## R4 가치 후보 — 평가 {summary.evaluated} · 통과 {len(passed)} (과열 ⚠ {caution_n})",
        "",
        "> P-18: 게이트는 가치·건전성만. 국면은 도구 — 바닥·회복 산업이 위, 과열 ⚠는 '천천히' 신호.",
        "",
    ]
    if passed:
        lines += [
            "| 종목 | 산업 | 국면 | 산업내 PBR | 시장 PBR | 심사 패킷 |",
            "|---|---|---|---|---|---|",
        ]
        for c in passed[:40]:
            packet = (dossiers or {}).get(c.symbol)
            warn = " ⚠" if c.cycle_caution else ""
            lines.append(
                f"| {c.symbol} | {c.industry} | {phase_ko(c.phase)}{warn} "
                f"| {_fmt(c.industry_pbr_pct, '.0%')} | {_fmt(c.market_pbr_pct, '.0%')} "
                f"| {packet or '미작성'} |"
            )
        if len(passed) > 40:
            lines.append(f"\n(상위 40만 표시 — 외 {len(passed) - 40}종은 DB·웹 전수)")
    else:
        lines.append("- 없음(가치·건전성 통과 종목 없음)")
    lines += ["", "### 탈락 사유 분포", ""]
    for reason, n in summary.reject_counts.items():
        lines.append(f"- {n} × {reason}")
    if summary.skipped_industries:
        lines += ["", "### 판정 불가 산업", ""]
        lines += [f"- {s}" for s in summary.skipped_industries]
    lines += ["", "### 미적용 필터(데이터 미확보 — 통과≠전 필터 통과)", ""]
    lines += [f"- {u}" for u in UNAPPLIED_V1]
    lines += ["", "---", "판단 전 과정 순수 코드 — LLM·재량 미개입. 결측은 결측으로 표기됨.", ""]
    return "\n".join(lines)


_HTML_CSS = """
  body { margin:0; background:#f7f8fa; color:#1a202c; line-height:1.6; font-size:14px;
         font-family:"Apple SD Gothic Neo","Pretendard","Noto Sans KR",sans-serif; }
  .wrap { max-width:860px; margin:0 auto; padding:28px 18px 60px; }
  h1 { font-size:22px; margin:0 0 4px; } h2 { font-size:17px; margin:28px 0 10px;
       padding-left:9px; border-left:4px solid #2b6cb0; }
  .meta { color:#5a6472; font-size:12.5px; }
  .pill { display:inline-block; font-size:11px; font-weight:700; padding:2px 8px;
          border-radius:999px; margin-left:4px; background:#f0fff4; color:#276749; }
  .ph { display:inline-block; font-size:11.5px; font-weight:700; padding:2px 9px;
        border-radius:999px; color:#fff; }
  .ph-bott { background:#2f855a; } .ph-reco { background:#2b6cb0; }
  .ph-over { background:#c53030; } .ph-decl { background:#975a16; }
  .ph-unkn { background:#a0aec0; }
  .tip { border-bottom:1px dotted #5a6472; cursor:help; position:relative; }
  .tip:hover::after { content:attr(data-tip); position:absolute; left:0; top:130%;
    z-index:9; width:260px; background:#1a202c; color:#fff; font-weight:400;
    font-size:12px; line-height:1.5; padding:8px 10px; border-radius:8px; }
  table { width:100%; border-collapse:collapse; font-size:12.5px; margin:8px 0; }
  th { background:#ebf4ff; text-align:left; padding:6px 8px; border:1px solid #e2e8f0; }
  td { padding:5px 8px; border:1px solid #e2e8f0; }
  tr.zone td { background:#f0fff4; }
  .card { background:#fff; border:1px solid #e2e8f0; border-radius:10px; padding:14px 18px; margin:10px 0; }
  .hero { border-left:5px solid #2f855a; }
  .headline { font-size:19px; font-weight:700; margin:2px 0 6px; }
  .bear { color:#9b2c2c; } .bull { color:#276749; }
  ul { margin:4px 0; padding-left:20px; } li { margin:2px 0; }
  .scroll { overflow-x:auto; }
  details { margin:8px 0; } summary { cursor:pointer; color:#2b6cb0; font-weight:600; }
  .note { font-size:12.5px; color:#5a6472; background:#fffbeb; border-left:3px solid #975a16;
          padding:7px 11px; margin:10px 0; }
"""


def render_html(
    assessments: list[Assessment],
    candidates: list[CandidateRecord],
    summary: ScreenSummary,
    dossier_by_symbol: "dict[str, object]",
    *,
    basis_date: str,
    policy_version: str,
    transitions: list[tuple[str, CyclePhase, CyclePhase]] | None = None,
    new_passed: set[str] | None = None,
    dropped: set[str] | None = None,
    names: dict[str, str] | None = None,
) -> str:
    """운영자 수신용 주간 HTML — 결론·전주 대비 변화가 먼저 온다(P-16 V3)."""
    from trading.contracts.longterm import DossierRecord
    from trading.web.glossary import phase_pill, tip

    wl_groups = set(WHITELIST.values())
    passed = [c for c in candidates if c.passed]
    transitions = transitions or []
    new_passed, dropped, names = new_passed or set(), dropped or set(), names or {}

    def name_of(sym: str) -> str:
        return names.get(sym, sym)

    # --- 1. 결론 헤드라인 ---
    events: list[str] = []
    if new_passed:
        events.append(f"신규 후보 {len(new_passed)}")
    if dropped:
        events.append(f"후보 이탈 {len(dropped)}")
    if transitions:
        events.append(f"국면 전환 {len(transitions)}")
    headline = " · ".join(events) if events else "변화 없음 — 관찰 유지"
    sub = (
        "신규 후보의 심사 패킷(아래)을 확인하십시오. 편입·집행은 없습니다(페이퍼)."
        if new_passed
        else "국면 전환·후보 진입·이탈이 모두 없습니다. 장기 사이클에서 대부분의 주는 이 상태가 정상입니다."
        if not events
        else "세부는 아래 '전주 대비 변화'에 있습니다. 편입·집행은 없습니다(페이퍼)."
    )
    parts = [
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>주간 보고서 {basis_date}</title><style>{_HTML_CSS}</style></head><body><div class='wrap'>",
        "<h1>주간 보고서</h1>",
        f"<div class='meta'>기준일 {basis_date}, {policy_version}"
        "<span class='pill'>페이퍼 모드</span><span class='pill'>집행 없음</span></div>",
        f"<div class='card hero'><div class='headline'>{headline}</div>"
        f"<div>통과 후보 {len(passed)}개 · 진입 존 산업 "
        f"{sum(1 for a in assessments if a.phase in (CyclePhase.BOTTOMING, CyclePhase.RECOVERING))}개 · "
        f"평가 {summary.evaluated}종목</div>"
        f"<div class='meta'>{sub}</div></div>",
    ]

    # --- 2. 전주 대비 변화 ---
    parts.append("<h2>전주 대비 변화</h2>")
    if transitions or new_passed or dropped:
        parts.append("<div class='card'><ul>")
        for industry, prev, cur in transitions:
            parts.append(f"<li>{industry}: {phase_pill(prev)} → {phase_pill(cur)}</li>")
        for sym in sorted(new_passed):
            parts.append(f"<li>후보 진입: <b>{name_of(sym)}</b> <span class='meta'>{sym}</span></li>")
        for sym in sorted(dropped):
            parts.append(f"<li>후보 이탈: {name_of(sym)} <span class='meta'>{sym}</span></li>")
        parts.append("</ul></div>")
    else:
        parts.append("<div class='card meta'>변화 없음</div>")

    # --- 3. 가치 후보 표(P-18 — 국면 우선순위 정렬·과열 ⚠) + 신규 후보만 상세 카드 ---
    passed = sorted(passed, key=_candidate_priority)
    caution_n = sum(1 for c in passed if c.cycle_caution)
    parts.append(
        f"<h2>가치 후보 ({len(passed)}, 과열 ⚠ {caution_n}) — 관찰 후보, 매수 결정 아님</h2>"
    )
    parts.append(
        "<div class='note'>P-18: 게이트는 가치·건전성만. 국면은 도구 — 바닥·회복 산업이 위, "
        "⚠는 과열 산업(천천히) 신호입니다.</div>"
    )
    if not passed:
        parts.append("<div class='card meta'>없음 — 가치·건전성 통과 종목 없음</div>")
    else:
        parts += [
            "<div class='card scroll'><table>",
            f"<tr><th>종목</th><th>산업</th><th>{tip('phase', '국면')}</th>"
            f"<th>{tip('sector_pct', '산업내 PBR')}</th><th>시장 PBR</th><th></th></tr>",
        ]
        show = passed[:60]
        for c in show:
            mark = " <span class='pill'>신규</span>" if c.symbol in new_passed else ""
            warn = " ⚠" if c.cycle_caution else ""
            parts.append(
                f"<tr><td><b>{name_of(c.symbol)}</b> <span class='meta'>{c.symbol}</span></td>"
                f"<td>{c.industry}</td><td>{phase_pill(c.phase)}{warn}</td>"
                f"<td>{_fmt(c.industry_pbr_pct, '.0%')}</td>"
                f"<td>{_fmt(c.market_pbr_pct, '.0%')}</td><td>{mark}</td></tr>"
            )
        parts.append("</table>")
        if len(passed) > len(show):
            parts.append(
                f"<div class='meta'>상위 {len(show)}만 표시 — 외 {len(passed) - len(show)}종은 웹 전수</div>"
            )
        parts.append("</div>")
    for c in (c for c in passed if c.symbol in new_passed):
        parts.append(
            f"<div class='card'><div class='headline'>{name_of(c.symbol)} "
            f"<span class='meta'>{c.symbol}</span> <span class='pill'>신규</span></div>"
            f"<div>{c.industry} {phase_pill(c.phase)}{' ⚠과열' if c.cycle_caution else ''} · "
            f"{tip('sector_pct', '산업내 PBR')} 하위 {_fmt(c.industry_pbr_pct, '.0%')} · "
            f"시장 하위 {_fmt(c.market_pbr_pct, '.0%')}</div>"
        )
        d = dossier_by_symbol.get(c.symbol)
        if isinstance(d, DossierRecord):
            parts.append(
                f"<details><summary>심사 패킷 보기 (서술 {d.model} — 판정 미입력 참고 기록)</summary>"
            )
            parts.append("<p class='bull'><b>긍정 논거</b></p><ul>")
            parts += [f"<li>{x}</li>" for x in d.bull_case]
            parts.append("</ul><p class='bear'><b>반박 논거(의무)</b></p><ul>")
            parts += [f"<li>{x}</li>" for x in d.bear_case]
            parts.append("</ul>")
            if d.risks:
                parts.append("<p><b>리스크</b></p><ul>")
                parts += [f"<li>{x}</li>" for x in d.risks]
                parts.append("</ul>")
            parts.append("</details>")
        else:
            parts.append("<p class='meta'>심사 패킷 미작성 — python -m trading.dossier 로 생성합니다.</p>")
        parts.append("</div>")

    # --- 4. 산업 온도계(밴드 오름차순, 진입 존 하이라이트) ---
    parts += [
        "<h2>산업 온도계 — 싼 곳부터</h2>",
    ]
    group_to_industry = {group: industry for industry, group in WHITELIST.items()}
    ordered = sorted(
        assessments,
        key=lambda a: a.pbr_band_pct if a.pbr_band_pct is not None else 2.0,
    )

    def _thermo_table(items: list[Assessment], caption: str, *, use_industry_name: bool) -> None:
        parts.append(
            f"<div class='card scroll'><div class='meta'><b>{caption}</b></div><table>"
            f"<tr><th>산업</th><th>{tip('phase', '국면')}</th><th>{tip('temp', '온도')}</th>"
            f"<th>{tip('band_pct', 'PBR밴드')}</th><th>{tip('margin_band', '마진밴드')}</th>"
            f"<th>{tip('improving', '개선')}</th><th>{tip('secular', '사양')}</th></tr>"
        )
        for a in items:
            zone = " class='zone'" if a.phase in (CyclePhase.BOTTOMING, CyclePhase.RECOVERING) else ""
            label = group_to_industry.get(a.sector, a.sector) if use_industry_name else a.sector
            parts.append(
                f"<tr{zone}><td>{label}</td><td>{phase_pill(a.phase)}</td><td>{_fmt(a.temperature, '')}</td>"
                f"<td>{_fmt(a.pbr_band_pct, '.0%')}</td><td>{_fmt(a.margin_band_pct, '.0%')}</td>"
                f"<td>{'예' if a.improving else '아니오' if a.improving is not None else '?'}</td>"
                f"<td>{'경고' if a.secular_decline else '아니오' if a.secular_decline is not None else '?'}</td></tr>"
            )
        parts.append("</table></div>")

    _thermo_table(
        [a for a in ordered if a.sector in wl_groups],
        "편입 심사 대상 — 화이트리스트 큐레이션",
        use_industry_name=True,
    )
    _thermo_table(
        [a for a in ordered if a.sector not in wl_groups],
        "전 시장 관찰 — KRX 버킷 (판정·편입 미사용)",
        use_industry_name=False,
    )
    parts.append(
        "<p class='meta'>초록 행 = 바닥 통과·회복(우선순위 존). 지표에 마우스를 올리면 설명이 나옵니다.</p>"
    )

    # --- 5. 탈락 사유(건수 내림차순) + 부속(접힘) ---
    parts.append("<h2>탈락 사유 분포</h2><div class='card scroll'><table><tr><th>건수</th><th>사유</th></tr>")
    parts += [
        f"<tr><td>{n}</td><td>{reason}</td></tr>"
        for reason, n in sorted(summary.reject_counts.items(), key=lambda kv: -kv[1])
    ]
    parts.append("</table>")
    if summary.skipped_industries:
        parts.append(
            "<p class='meta'>판정 불가 산업: " + ", ".join(summary.skipped_industries) + "</p>"
        )
    parts.append("</div>")

    parts.append("<details><summary>미적용 필터(데이터 미확보)와 주석</summary><div class='card'><ul>")
    parts += [f"<li>{u}</li>" for u in UNAPPLIED_V1]
    parts.append(
        "</ul><p class='note'>통과가 전 필터 통과를 뜻하지 않습니다. "
        "판단 전 과정은 순수 코드이며 결측은 결측으로 표기됩니다.</p></div></details>"
    )
    parts.append("</div></body></html>")
    return "\n".join(parts)


def main() -> int:
    fins, market = FinStore(), MarketStore()
    val_store, cycle_store, cand_store = ValuationStore(), CycleStore(), CandidateStore()
    try:
        year_ends = full_year_ends(market)
        sector_years = build_sector_years(
            fins, market, year_end_dates=year_ends, extra_groups=CURATED_GROUPS
        )
        assessments = assess_all(
            sector_years, at="current", params=PROPOSED_PARAMS,
            financial_groups=FINANCIAL_PROFILE_GROUPS,
        )
        candidates, summary = run_screen(val_store, cycle_store, params=PROPOSED_R4)
        basis = year_ends.get("current", "?")
        dossier_dir = REPORT_DIR / "dossiers"
        dossiers = {
            c.symbol: files[-1].name
            for c in candidates
            if c.passed and (files := sorted(dossier_dir.glob(f"*-{c.symbol}.md")))
        }
        text = render(assessments, candidates, summary, basis_date=basis, dossiers=dossiers)

        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        out = REPORT_DIR / f"weekly-{now_kst().strftime('%Y%m%d')}.md"
        out.write_text(text, encoding="utf-8")
        print(text)
        print(f"저장: {out}")

        # 운영자 수신용 HTML(변화 중심 재설계 — 운영자 피드백 2026-08-30, P-16 V3)
        from trading.dossier import DossierStore
        from trading.web.data import passed_delta, stock_names

        dstore = DossierStore()
        try:
            dossier_records = {
                c.symbol: rec
                for c in candidates
                if c.passed and (rec := dstore.latest_for_symbol(c.symbol)) is not None
            }
        finally:
            dstore.close()
        recent = cycle_store.recent_phases(n=2)
        transitions = [
            (ind, CyclePhase(p[1]), CyclePhase(p[0]))
            for ind, p in sorted(recent.items())
            if len(p) == 2 and p[0] != p[1]
        ]
        new_passed, dropped = passed_delta()
        html = render_html(
            assessments, candidates, summary, dict(dossier_records),
            basis_date=basis, policy_version=POLICY_VERSION,
            transitions=transitions, new_passed=new_passed, dropped=dropped,
            names=stock_names(),
        )
        html_out = REPORT_DIR / f"weekly-{now_kst().strftime('%Y%m%d')}.html"
        html_out.write_text(html, encoding="utf-8")
        print(f"저장: {html_out}")
    finally:
        fins.close()
        market.close()
        val_store.close()
        cycle_store.close()
        cand_store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
