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

from trading.contracts.longterm import CandidateRecord

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
            f"| {a.sector} | {a.phase.value} | {_fmt(a.temperature, '')} "
            f"| {_fmt(a.pbr_band_pct, '.0%')} | {_fmt(a.margin_band_pct, '.0%')} "
            f"| {'예' if a.improving else '—' if a.improving is not None else '?'} "
            f"| {'⚠' if a.secular_decline else '—' if a.secular_decline is not None else '?'} "
            f"| {'✓' if a.sector in wl_groups else ''} |"
        )
    passed = [c for c in candidates if c.passed]
    lines += ["", f"## R4 페이퍼 후보 — 평가 {summary.evaluated} · 통과 {len(passed)}", ""]
    if passed:
        for c in passed:
            packet = (dossiers or {}).get(c.symbol)
            suffix = f" · 심사 패킷: {packet}" if packet else " · 심사 패킷 미작성"
            lines.append(
                f"- **{c.symbol}** [{c.industry}] 국면={c.phase.value}, "
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
    finally:
        fins.close()
        market.close()
        val_store.close()
        cycle_store.close()
        cand_store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
