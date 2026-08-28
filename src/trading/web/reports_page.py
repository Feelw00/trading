"""보고서 페이지(W3) — 주차 셀렉트박스 + 탭(주간 보고서 / 심사 패킷 / 기타)."""

import html
from pathlib import Path

from trading.report_site import REPORT_DIR
from trading.web.layout import page

TABS = (("weekly", "주간 보고서"), ("dossier", "심사 패킷"), ("etc", "기타 문서"))


def _weeks() -> list[str]:
    return sorted(
        (p.stem.removeprefix("weekly-") for p in REPORT_DIR.glob("weekly-*.html")),
        reverse=True,
    )


def render_reports(*, week: str | None, tab: str) -> str:
    weeks = _weeks()
    selected = week if week in weeks else (weeks[0] if weeks else None)
    tab = tab if tab in {t for t, _l in TABS} else "weekly"

    tab_links = "".join(
        f"<a class='pill{'' if t != tab else ' warn'}' "
        f"href='/reports?tab={t}{f'&week={selected}' if selected else ''}'>{label}</a>"
        for t, label in TABS
    )
    options = "".join(
        f"<option value='{w}' {'selected' if w == selected else ''}>{w[:4]}-{w[4:6]}-{w[6:]}</option>"
        for w in weeks
    )
    parts = [
        "<h1>보고서</h1>",
        "<div class='card'>주차 "
        f"<select onchange=\"location='/reports?tab={tab}&week='+this.value\">{options}</select>"
        f" &nbsp; {tab_links}</div>",
    ]

    if tab == "weekly":
        if selected:
            parts.append(
                f"<div class='card' style='padding:4px'><iframe src='/weekly-{selected}.html' "
                "style='width:100%;height:78vh;border:0'></iframe></div>"
            )
        else:
            parts.append("<div class='card meta'>주간 보고서 없음 — 토 09:30 첫 발행 이후 표시</div>")
    elif tab == "dossier":
        files = sorted((REPORT_DIR / "dossiers").glob("*.md"), reverse=True)
        if selected:
            same_week = [p for p in files if p.name.startswith(selected)]
            files = same_week or files
        if files:
            parts.append("<ul class='files'>")
            for p in files:
                rel = p.relative_to(REPORT_DIR).as_posix()
                parts.append(f"<li><a href='/{rel}'>{html.escape(p.name)}</a></li>")
            parts.append("</ul>")
        else:
            parts.append("<div class='card meta'>심사 패킷 없음(통과 후보 발생 시 생성)</div>")
    else:
        others = sorted(
            (p for p in REPORT_DIR.glob("*.*") if not p.name.startswith("weekly-") and p.is_file()),
            reverse=True,
        )
        parts.append("<ul class='files'>")
        for p in others:
            parts.append(f"<li><a href='/{p.name}'>{html.escape(p.name)}</a></li>")
        parts.append("</ul>")

    return page("보고서 — 트레이딩 v0.3", "\n".join(parts), active="/reports")


__all__ = ["render_reports"]
