"""자료실(W3) — 보고서 원본 다운로드 + CSV 내보내기 + DB 스냅샷(스트리밍)."""

import csv
import html
import io
from pathlib import Path

from trading.contracts.longterm import phase_ko
from trading.report_site import REPORT_DIR
from trading.web.layout import page

DATA_DIR = Path("data")

CSV_EXPORTS = (
    ("valuation.csv", "밸류에이션 전 종목(최신 회차) — PER 직접 확인용"),
    ("cycle.csv", "산업 온도계(최신 회차)"),
    ("candidates.csv", "R4 판정 전수(통과·탈락 사유 포함)"),
)


def _size(p: Path) -> str:
    n = float(p.stat().st_size)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.0f}TB"


def render_files() -> str:
    parts = [
        "<h1>자료실</h1>",
        "<h2>CSV 내보내기 (요청 시점 최신 데이터)</h2><ul class='files'>",
        *(
            f"<li><a href='/files/{name}'>{name}</a> <span class='meta'>{html.escape(desc)}</span></li>"
            for name, desc in CSV_EXPORTS
        ),
        "</ul><h2>보고서 원본</h2><ul class='files'>",
    ]
    for p in sorted(REPORT_DIR.rglob("*.*"), reverse=True):
        if not p.is_file():
            continue
        rel = p.relative_to(REPORT_DIR).as_posix()
        parts.append(
            f"<li><a href='/files/raw/{rel}'>{html.escape(rel)}</a>"
            f"<span class='meta'>{_size(p)}</span></li>"
        )
    parts.append("</ul><h2>DB 스냅샷 (SQLite 원본 — 백업 겸용)</h2><ul class='files'>")
    for p in sorted(DATA_DIR.glob("*.sqlite")):
        parts.append(
            f"<li><a href='/files/db/{p.name}'>{p.name}</a>"
            f"<span class='meta'>{_size(p)}</span></li>"
        )
    parts.append("</ul>")
    return page("자료실 — 트레이딩 v0.3", "\n".join(parts), active="/files")


def valuation_csv() -> str:
    from trading.web.stocks_data import stock_rows

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        ["symbol", "name", "group", "industry", "pbr", "per", "psr", "roe",
         "roe_median_5y", "debt_ratio", "sector_pbr_pct", "loss_years_5y",
         "loss_observed", "r4", "fin_basis", "as_of"]
    )
    for r in stock_rows():
        v = r.val
        w.writerow(
            [r.symbol, r.name, r.group or "", r.industry or "", v.pbr, v.per, v.psr, v.roe,
             v.roe_median_5y, v.debt_ratio, v.sector_pbr_pct, v.loss_years_5y,
             v.loss_years_observed, r.r4, v.fin_basis or "", str(v.as_of)[:10]]
        )
    return buf.getvalue()


def cycle_csv() -> str:
    from trading.cycle.store import CycleStore

    store = CycleStore()
    try:
        records = store.all_latest()
    finally:
        store.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["industry", "phase", "phase_ko", "temperature", "pbr_band_pct",
                "margin_band_pct", "rev_cycle_z", "secular_decline", "as_of"])
    for r in sorted(records, key=lambda x: x.industry):
        ax = r.axes_primary
        w.writerow([r.industry, r.phase.value, phase_ko(r.phase), r.temperature,
                    ax.sector_pbr_band_pct, ax.sector_margin_band_pct, ax.sector_rev_cycle_z,
                    r.secular_decline, str(r.as_of)[:10]])
    return buf.getvalue()


def candidates_csv() -> str:
    from trading.screen.store import CandidateStore

    store = CandidateStore()
    try:
        records = store.latest_run()
    finally:
        store.close()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["symbol", "industry", "phase", "passed", "industry_pbr_pct",
                "reject_reasons", "unapplied", "as_of"])
    for r in sorted(records, key=lambda x: (not x.passed, x.symbol)):
        w.writerow([r.symbol, r.industry, r.phase.value, r.passed, r.industry_pbr_pct,
                    " | ".join(r.reject_reasons), " | ".join(r.unapplied), str(r.as_of)[:10]])
    return buf.getvalue()


CSV_BUILDERS = {
    "valuation.csv": valuation_csv,
    "cycle.csv": cycle_csv,
    "candidates.csv": candidates_csv,
}

__all__ = ["CSV_BUILDERS", "DATA_DIR", "render_files"]
