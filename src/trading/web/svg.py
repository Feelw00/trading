"""서버 생성 SVG 미니 라이브러리 — 무의존·결정론(테스트 가능). P-15 원칙."""

import html
from dataclasses import dataclass

PHASE_COLOR = {
    "bottoming": "#2f855a",
    "recovering": "#2b6cb0",
    "overheated": "#c53030",
    "declining": "#975a16",
    "unknown": "#a0aec0",
}


@dataclass(frozen=True)
class BandRow:
    label: str
    pct: float | None          # PBR 밴드 위치 0~1 (None=결측)
    phase: str                 # CyclePhase.value
    temperature: int | None
    whitelisted: bool


def band_chart(rows: list[BandRow], *, low: float = 0.30, high: float = 0.75) -> str:
    """산업 온도 지도 — 가로 바(밴드 위치), 하단/과열 존 음영, 화이트리스트 마커."""
    row_h, label_w, chart_w, pad = 26, 150, 560, 6
    width = label_w + chart_w + 120
    height = pad * 2 + row_h * len(rows) + 22
    x0 = label_w

    def x(p: float) -> float:
        return x0 + p * chart_w

    parts = [
        f"<svg viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg' "
        "font-family='sans-serif' font-size='12'>",
        # 존 음영: 하단(진입 존)·과열
        f"<rect x='{x(0)}' y='{pad}' width='{low * chart_w}' height='{row_h * len(rows)}' fill='#2f855a' opacity='0.08'/>",
        f"<rect x='{x(high)}' y='{pad}' width='{(1 - high) * chart_w}' height='{row_h * len(rows)}' fill='#c53030' opacity='0.08'/>",
        f"<line x1='{x(low)}' y1='{pad}' x2='{x(low)}' y2='{pad + row_h * len(rows)}' stroke='#2f855a' stroke-dasharray='3,3'/>",
        f"<line x1='{x(high)}' y1='{pad}' x2='{x(high)}' y2='{pad + row_h * len(rows)}' stroke='#c53030' stroke-dasharray='3,3'/>",
    ]
    for i, r in enumerate(rows):
        y = pad + i * row_h
        cy = y + row_h / 2
        color = PHASE_COLOR.get(r.phase, "#a0aec0")
        wl = " ✓" if r.whitelisted else ""
        parts.append(
            f"<text x='{label_w - 8}' y='{cy + 4}' text-anchor='end'>{html.escape(r.label)}{wl}</text>"
        )
        if r.pct is None:
            parts.append(
                f"<text x='{x(0) + 4}' y='{cy + 4}' fill='#a0aec0'>결측(관측 부족)</text>"
            )
        else:
            parts.append(
                f"<rect x='{x0}' y='{y + 6}' width='{max(r.pct * chart_w, 2):.1f}' "
                f"height='{row_h - 12}' rx='3' fill='{color}'/>"
            )
            temp = f" · 온도 {r.temperature}" if r.temperature is not None else ""
            parts.append(
                f"<text x='{x(1) + 8}' y='{cy + 4}' fill='#5a6472'>{r.pct:.0%}{temp}</text>"
            )
    axis_y = pad + row_h * len(rows) + 14
    for p, lab in ((0.0, "0%"), (low, f"{low:.0%} 하단"), (high, f"{high:.0%} 과열"), (1.0, "100%")):
        parts.append(f"<text x='{x(p)}' y='{axis_y}' text-anchor='middle' fill='#5a6472'>{lab}</text>")
    parts.append("</svg>")
    return "\n".join(parts)


def funnel_chart(stages: list[tuple[str, int]]) -> str:
    """R4 깔때기 — 단계별 잔존 수(가로 바, 상대폭)."""
    if not stages:
        return "<svg/>"
    row_h, label_w, chart_w, pad = 30, 190, 460, 6
    width = label_w + chart_w + 70
    height = pad * 2 + row_h * len(stages)
    top = max(count for _label, count in stages) or 1
    parts = [
        f"<svg viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg' "
        "font-family='sans-serif' font-size='12'>"
    ]
    for i, (label, count) in enumerate(stages):
        y = pad + i * row_h
        w = max(chart_w * count / top, 2)
        parts.append(
            f"<text x='{label_w - 8}' y='{y + row_h / 2 + 4}' text-anchor='end'>{html.escape(label)}</text>"
        )
        parts.append(
            f"<rect x='{label_w}' y='{y + 5}' width='{w:.1f}' height='{row_h - 10}' rx='3' "
            f"fill='#2b6cb0' opacity='{0.45 + 0.55 * (i + 1) / len(stages):.2f}'/>"
        )
        parts.append(f"<text x='{label_w + w + 6}' y='{y + row_h / 2 + 4}' fill='#1a202c'>{count}</text>")
    parts.append("</svg>")
    return "\n".join(parts)


def progress_bar(current: float, target: float) -> str:
    """신선도 스트립용 미니 진행 바(HTML) — 목표 대비 진행률."""
    pct = min(current / target, 1.0) if target > 0 else 0.0
    return (
        "<span class='pbar'><span class='pbar-fill' "
        f"style='width:{pct:.0%}'></span></span> {current:.0f}/{target:.0f}"
    )


__all__ = ["BandRow", "PHASE_COLOR", "band_chart", "funnel_chart", "progress_bar"]
