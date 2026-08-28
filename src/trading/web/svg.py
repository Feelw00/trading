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


def line_chart(
    points: list[float],
    *,
    start_label: str = "",
    end_label: str = "",
    color: str = "#2b6cb0",
    height: int = 160,
    fmt: str = ",.0f",
) -> str:
    """단일 시계열 라인 — 최소/최대/최종값 주석. 점이 2개 미만이면 결측 안내."""
    if len(points) < 2:
        return "<svg viewBox='0 0 600 40' xmlns='http://www.w3.org/2000/svg' font-size='12'><text x='4' y='24' fill='#a0aec0'>관측 부족(2점 미만)</text></svg>"
    width, pad_l, pad_r, pad_y = 640, 8, 90, 16
    lo, hi = min(points), max(points)
    span = (hi - lo) or abs(hi) or 1.0
    n = len(points)

    def xy(i: int, v: float) -> str:
        x = pad_l + (width - pad_l - pad_r) * i / (n - 1)
        y = pad_y + (height - 2 * pad_y) * (1 - (v - lo) / span)
        return f"{x:.1f},{y:.1f}"

    poly = " ".join(xy(i, v) for i, v in enumerate(points))
    last_y = pad_y + (height - 2 * pad_y) * (1 - (points[-1] - lo) / span)
    return (
        f"<svg viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg' "
        "font-family='sans-serif' font-size='11'>"
        f"<polyline points='{poly}' fill='none' stroke='{color}' stroke-width='1.8'/>"
        f"<text x='{pad_l}' y='{height - 3}' fill='#5a6472'>{html.escape(start_label)}</text>"
        f"<text x='{width - pad_r}' y='{height - 3}' text-anchor='end' fill='#5a6472'>{html.escape(end_label)}</text>"
        f"<text x='{width - pad_r + 4}' y='{last_y + 4}' fill='{color}'>{points[-1]:{fmt}}</text>"
        f"<text x='{width - pad_r + 4}' y='{pad_y + 4}' fill='#a0aec0'>{hi:{fmt}}</text>"
        f"<text x='{width - pad_r + 4}' y='{height - pad_y + 4}' fill='#a0aec0'>{lo:{fmt}}</text>"
        "</svg>"
    )


def dual_bar_chart(
    years: list[str],
    a: list[float | None],
    b: list[float | None],
    *,
    label_a: str,
    label_b: str,
    height: int = 180,
) -> str:
    """연도별 이중 바(예: 매출·영업이익) — 음수(적자) 지원, 결측 칸 비움."""
    if not years:
        return "<svg/>"
    width, pad_l, pad_y = 640, 8, 26
    vals = [v for v in (*a, *b) if v is not None]
    if not vals:
        return "<svg viewBox='0 0 600 40' xmlns='http://www.w3.org/2000/svg' font-size='12'><text x='4' y='24' fill='#a0aec0'>재무 관측 없음</text></svg>"
    hi, lo = max(max(vals), 0.0), min(min(vals), 0.0)
    span = (hi - lo) or 1.0
    zero_y = pad_y + (height - 2 * pad_y) * (hi / span)
    group_w = (width - pad_l * 2) / len(years)
    bar_w = group_w * 0.32

    def bar(i: int, v: float | None, offset: float, color: str) -> str:
        if v is None:
            return ""
        h = abs(v) / span * (height - 2 * pad_y)
        x = pad_l + i * group_w + offset
        y = zero_y - h if v >= 0 else zero_y
        return f"<rect x='{x:.1f}' y='{y:.1f}' width='{bar_w:.1f}' height='{max(h, 1):.1f}' fill='{color}' rx='2'/>"

    parts = [
        f"<svg viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg' "
        "font-family='sans-serif' font-size='11'>",
        f"<line x1='{pad_l}' y1='{zero_y:.1f}' x2='{width - pad_l}' y2='{zero_y:.1f}' stroke='#cbd5e0'/>",
        f"<text x='{pad_l}' y='12' fill='#2b6cb0'>■ {html.escape(label_a)}</text>",
        f"<text x='{pad_l + 110}' y='12' fill='#975a16'>■ {html.escape(label_b)}</text>",
    ]
    for i, year in enumerate(years):
        parts.append(bar(i, a[i], group_w * 0.12, "#2b6cb0"))
        parts.append(bar(i, b[i], group_w * 0.12 + bar_w + 2, "#975a16"))
        parts.append(
            f"<text x='{pad_l + i * group_w + group_w / 2:.1f}' y='{height - 6}' "
            f"text-anchor='middle' fill='#5a6472'>{html.escape(year[2:])}</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts)


def phase_strip(items: list[tuple[str, str]]) -> str:
    """as-of 리플레이 국면 타임라인 — (연도, phase.value) 색 띠."""
    if not items:
        return "<svg/>"
    cell_w, h, pad = 84, 46, 4
    width = pad * 2 + cell_w * len(items)
    parts = [
        f"<svg viewBox='0 0 {width} {h}' xmlns='http://www.w3.org/2000/svg' "
        "font-family='sans-serif' font-size='11'>"
    ]
    for i, (year, phase) in enumerate(items):
        x = pad + i * cell_w
        color = PHASE_COLOR.get(phase, "#a0aec0")
        label = _PHASE_SHORT.get(phase, phase[:2])
        parts.append(f"<rect x='{x}' y='{pad}' width='{cell_w - 4}' height='22' rx='4' fill='{color}'/>")
        parts.append(
            f"<text x='{x + (cell_w - 4) / 2}' y='{pad + 15}' text-anchor='middle' fill='#fff' "
            f"font-weight='700'>{html.escape(label)}</text>"
        )
        parts.append(
            f"<text x='{x + (cell_w - 4) / 2}' y='{h - 3}' text-anchor='middle' fill='#5a6472'>{html.escape(year)}</text>"
        )
    parts.append("</svg>")
    return "\n".join(parts)


_PHASE_SHORT = {
    "bottoming": "바닥 통과",
    "recovering": "회복",
    "overheated": "과열",
    "declining": "하강",
    "unknown": "불명",
}


def progress_bar(current: float, target: float) -> str:
    """신선도 스트립용 미니 진행 바(HTML) — 목표 대비 진행률."""
    pct = min(current / target, 1.0) if target > 0 else 0.0
    return (
        "<span class='pbar'><span class='pbar-fill' "
        f"style='width:{pct:.0%}'></span></span> {current:.0f}/{target:.0f}"
    )


__all__ = [
    "BandRow",
    "PHASE_COLOR",
    "band_chart",
    "dual_bar_chart",
    "funnel_chart",
    "line_chart",
    "progress_bar",
]
