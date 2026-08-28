"""R6 주간 다이제스트(v0.3 §8 — 읽기 전용) — `python -m trading.weekly_digest`.

온도계·R4 페이퍼 결과·데이터 결측 고지를 한 문서로. `.runtime/reports/weekly-<일자>.md` 저장
(+콘솔). 대부분의 주의 결론은 "변화 없음"이어야 정상이다. Telegram 발송은 알림 채널 복구 후.
"""

from pathlib import Path

from trading.collectors.base import now_kst
from trading.collectors.fins import FinStore
from trading.collectors.market import MarketStore
from trading.cycle.bands import build_sector_years, discover_year_ends
from trading.cycle.engine import PROPOSED_PARAMS, Assessment, assess_all
from trading.cycle.policy import CURATED_GROUPS, POLICY_VERSION, WHITELIST
from trading.cycle.store import CycleStore
from trading.screen.rules import PROPOSED_R4, UNAPPLIED_V1
from trading.screen.run import ScreenSummary, run_screen
from trading.screen.store import CandidateStore
from trading.valuation.store import ValuationStore

from trading.contracts.longterm import PHASE_LEGEND_KO, CandidateRecord, phase_ko

REPORT_DIR = Path(".runtime") / "reports"


def _fmt(v: float | int | None, spec: str) -> str:
    return f"{v:{spec}}" if v is not None else "결측"


def render(
    assessments: list[Assessment],
    candidates: list[CandidateRecord],
    summary: ScreenSummary,
    *,
    basis_date: str,
    dossiers: dict[str, str] | None = None,
) -> str:
    lines = [
        f"# 주간 다이제스트 — {basis_date} 기준 ({POLICY_VERSION})",
        "",
        "> 읽기 전용. 편입·집행 없음(§5 결재 전 페이퍼). 대부분의 주는 '변화 없음'이 정상.",
        "",
        "## R3 산업 온도계",
        "",
        "| 산업 그룹 | 국면 | 온도 | PBR밴드 | 마진밴드 | 개선 | 사양 | WL |",
        "|---|---|---|---|---|---|---|---|",
    ]
    wl_groups = set(WHITELIST.values())
    for a in assessments:
        lines.append(
            f"| {a.sector} | {phase_ko(a.phase)} | {_fmt(a.temperature, '')} "
            f"| {_fmt(a.pbr_band_pct, '.0%')} | {_fmt(a.margin_band_pct, '.0%')} "
            f"| {'예' if a.improving else '—' if a.improving is not None else '?'} "
            f"| {'⚠' if a.secular_decline else '—' if a.secular_decline is not None else '?'} "
            f"| {'✓' if a.sector in wl_groups else ''} |"
        )
    lines += ["", f"> {PHASE_LEGEND_KO}"]
    passed = [c for c in candidates if c.passed]
    lines += ["", f"## R4 페이퍼 후보 — 평가 {summary.evaluated} · 통과 {len(passed)}", ""]
    if passed:
        for c in passed:
            packet = (dossiers or {}).get(c.symbol)
            suffix = f" · 심사 패킷: {packet}" if packet else " · 심사 패킷 미작성"
            lines.append(
                f"- **{c.symbol}** [{c.industry}] 국면={phase_ko(c.phase)}, "
                f"산업내 PBR 하위 {_fmt(c.industry_pbr_pct, '.0%')}{suffix}"
            )
    else:
        lines.append("- 없음(발동 존 산업 부재 또는 필터 전체 탈락 — 정상 상태일 수 있음)")
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
  .meta { color:#5a6472; font-size:12.5px; margin-bottom:18px; }
  .pill { display:inline-block; font-size:11px; font-weight:700; padding:2px 8px;
          border-radius:999px; margin-left:4px; background:#f0fff4; color:#276749; }
  table { width:100%; border-collapse:collapse; font-size:12.5px; margin:8px 0; }
  th { background:#ebf4ff; text-align:left; padding:6px 8px; border:1px solid #e2e8f0; }
  td { padding:5px 8px; border:1px solid #e2e8f0; }
  .card { background:#fff; border:1px solid #e2e8f0; border-radius:10px; padding:14px 18px; margin:10px 0; }
  .bear { color:#9b2c2c; } .bull { color:#276749; }
  ul { margin:4px 0; padding-left:20px; } li { margin:2px 0; }
  .scroll { overflow-x:auto; }
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
) -> str:
    """후보 선정과 심사를 한 문서로 — 운영자 수신용 HTML(v0.3 §8 주간 보고의 확장 렌더)."""
    from trading.contracts.longterm import DossierRecord

    wl_groups = set(WHITELIST.values())
    passed = [c for c in candidates if c.passed]
    parts = [
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        f"<title>주간 보고서 {basis_date}</title><style>{_HTML_CSS}</style></head><body><div class='wrap'>",
        f"<h1>주간 보고서: 후보 선정과 심사</h1>",
        f"<div class='meta'>기준일 {basis_date}, {policy_version}"
        "<span class='pill'>페이퍼 모드</span><span class='pill'>집행 없음</span></div>",
        "<h2>R3 산업 온도계</h2><div class='card scroll'><table>",
        "<tr><th>산업 그룹</th><th>국면</th><th>온도</th><th>PBR밴드</th><th>마진밴드</th>"
        "<th>개선</th><th>사양</th><th>WL</th></tr>",
    ]
    for a in assessments:
        parts.append(
            f"<tr><td>{a.sector}</td><td>{phase_ko(a.phase)}</td><td>{_fmt(a.temperature, '')}</td>"
            f"<td>{_fmt(a.pbr_band_pct, '.0%')}</td><td>{_fmt(a.margin_band_pct, '.0%')}</td>"
            f"<td>{'예' if a.improving else '아니오' if a.improving is not None else '?'}</td>"
            f"<td>{'경고' if a.secular_decline else '아니오' if a.secular_decline is not None else '?'}</td>"
            f"<td>{'✓' if a.sector in wl_groups else ''}</td></tr>"
        )
    parts.append(f"</table><p class='meta'>{PHASE_LEGEND_KO}</p></div>")

    parts.append(f"<h2>R4 통과 후보와 심사 패킷 (평가 {summary.evaluated}건, 통과 {len(passed)}건)</h2>")
    if not passed:
        parts.append("<div class='card'>통과 후보가 없습니다. 발동 존 산업 부재 또는 필터 전체 탈락으로, 정상 상태일 수 있습니다.</div>")
    for c in passed:
        parts.append(
            f"<div class='card'><b>{c.symbol}</b> [{c.industry}] 국면 {phase_ko(c.phase)}, "
            f"산업 내 PBR 하위 {_fmt(c.industry_pbr_pct, '.0%')}"
        )
        d = dossier_by_symbol.get(c.symbol)
        if isinstance(d, DossierRecord):
            parts.append(f"<p class='meta'>심사 패킷(서술 {d.model}) — 판정에 입력되지 않는 참고 기록입니다.</p>")
            parts.append("<p class='bull'><b>긍정 논거</b></p><ul>")
            parts += [f"<li>{x}</li>" for x in d.bull_case]
            parts.append("</ul><p class='bear'><b>반박 논거(의무)</b></p><ul>")
            parts += [f"<li>{x}</li>" for x in d.bear_case]
            parts.append("</ul>")
            if d.risks:
                parts.append("<p><b>리스크</b></p><ul>")
                parts += [f"<li>{x}</li>" for x in d.risks]
                parts.append("</ul>")
        else:
            parts.append("<p class='meta'>심사 패킷 미작성 — python -m trading.dossier 로 생성합니다.</p>")
        parts.append("</div>")

    parts.append("<h2>탈락 사유 분포</h2><div class='card scroll'><table><tr><th>건수</th><th>사유</th></tr>")
    parts += [f"<tr><td>{n}</td><td>{reason}</td></tr>" for reason, n in summary.reject_counts.items()]
    parts.append("</table></div>")

    parts.append("<h2>미적용 필터(데이터 미확보)</h2><div class='card'><ul>")
    parts += [f"<li>{u}</li>" for u in UNAPPLIED_V1]
    parts.append("</ul><p class='note'>통과가 전 필터 통과를 뜻하지 않습니다. 판단 전 과정은 순수 코드이며 결측은 결측으로 표기됩니다.</p></div>")
    parts.append("</div></body></html>")
    return "\n".join(parts)


def main() -> int:
    fins, market = FinStore(), MarketStore()
    val_store, cycle_store, cand_store = ValuationStore(), CycleStore(), CandidateStore()
    try:
        year_ends = discover_year_ends(market.dates())
        sector_years = build_sector_years(
            fins, market, year_end_dates=year_ends, extra_groups=CURATED_GROUPS
        )
        assessments = assess_all(sector_years, at="current", params=PROPOSED_PARAMS)
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

        # 운영자 수신용 HTML(후보 선정 + 심사 패킷 통합 — 운영자 요청 2026-08-28)
        from trading.cycle.engine import PROPOSED_PARAMS as _pp  # noqa: F401 — 정책 버전 표기용
        from trading.dossier import DossierStore

        dstore = DossierStore()
        try:
            dossier_records = {
                c.symbol: rec
                for c in candidates
                if c.passed and (rec := dstore.latest_for_symbol(c.symbol)) is not None
            }
        finally:
            dstore.close()
        html = render_html(
            assessments, candidates, summary, dict(dossier_records),
            basis_date=basis, policy_version="policy-v1.2",
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
