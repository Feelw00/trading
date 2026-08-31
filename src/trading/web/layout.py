"""공용 레이아웃 — 네비게이션·CSS 셸. 모든 페이지가 이 셸로 감싸진다."""

import html

NAV = (
    ("/", "대시보드"),
    ("/stocks", "종목"),
    ("/industries", "산업"),
    ("/reports", "보고서"),
    ("/files", "자료실"),
)

CSS = """
  body { margin:0; background:#f7f8fa; color:#1a202c; line-height:1.6; font-size:14px;
         font-family:"Apple SD Gothic Neo","Pretendard","Noto Sans KR",sans-serif; }
  .wrap { max-width:960px; margin:0 auto; padding:20px 18px 60px; }
  nav { background:#1a202c; }
  nav .wrap { padding:0 18px; display:flex; gap:4px; align-items:center; }
  nav a { color:#cbd5e0; text-decoration:none; padding:11px 13px; font-size:13.5px; }
  nav a.on { color:#fff; border-bottom:3px solid #63b3ed; font-weight:700; }
  nav .brand { color:#fff; font-weight:800; margin-right:14px; font-size:14px; }
  h1 { font-size:20px; margin:14px 0 4px; }
  h2 { font-size:16px; margin:26px 0 10px; padding-left:9px; border-left:4px solid #2b6cb0; }
  .meta { color:#5a6472; font-size:12.5px; }
  .card { background:#fff; border:1px solid #e2e8f0; border-radius:10px; padding:14px 18px; margin:10px 0; }
  .grid2 { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
  @media (max-width:720px) { .grid2 { grid-template-columns:1fr; } }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { background:#ebf4ff; text-align:left; padding:6px 9px; border:1px solid #e2e8f0; }
  td { padding:5px 9px; border:1px solid #e2e8f0; }
  a { color:#2b6cb0; text-decoration:none; } a:hover { text-decoration:underline; }
  .pill { display:inline-block; font-size:11px; font-weight:700; padding:2px 8px;
          border-radius:999px; margin-right:4px; background:#ebf4ff; color:#2b6cb0; }
  .pill.warn { background:#fffbeb; color:#975a16; }
  .tiles { display:grid; grid-template-columns:repeat(auto-fill, minmax(160px, 1fr)); gap:8px; }
  a.tile { display:block; border-radius:8px; padding:10px 12px; color:#fff; text-decoration:none; }
  a.tile:hover { filter:brightness(1.12); }
  .tile b { display:block; margin-bottom:2px; }
  .tile small { color:rgba(255,255,255,.88); }
  .pbar { display:inline-block; width:120px; height:9px; background:#e2e8f0;
          border-radius:5px; overflow:hidden; vertical-align:middle; margin-right:6px; }
  .pbar-fill { display:block; height:100%; background:#2b6cb0; }
  /* 용어 툴팁(P-16 V1) — 점선 밑줄 + 호버 설명, JS 없음 */
  .tip { border-bottom:1px dotted #94a3b8; cursor:help; position:relative; }
  .tip:hover::after { content:attr(data-tip); position:absolute; left:0; top:135%; z-index:20;
    background:#1a202c; color:#fff; padding:7px 11px; border-radius:7px; font-size:12px;
    font-weight:400; line-height:1.5; width:max-content; max-width:300px; white-space:normal; }
  /* 국면 5색 배지 — 전 페이지 공통 시각 언어 */
  .ph { display:inline-block; font-size:11.5px; font-weight:700; padding:2px 9px;
        border-radius:999px; color:#fff; }
  .ph-bott { background:#2f855a; } .ph-reco { background:#2b6cb0; }
  .ph-over { background:#c53030; } .ph-decl { background:#975a16; }
  .ph-unkn { background:#a0aec0; }
  .grid3 { display:grid; grid-template-columns:repeat(3, 1fr); gap:10px; }
  @media (max-width:860px) { .grid3 { grid-template-columns:1fr; } }
  .hero { border-left:5px solid #2f855a; }
  .hero .big { font-size:17px; font-weight:800; }
  details { margin:10px 0; } details summary { cursor:pointer; color:#5a6472; font-size:13px; }
  tr.passed-row { background:#f0fff4; }
  svg { max-width:100%; height:auto; }
  .scroll { overflow-x:auto; }
  pre.md { background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:16px;
           white-space:pre-wrap; word-break:break-word; font-size:13px;
           font-family:"SF Mono",Menlo,monospace; }
  ul.files { list-style:none; padding:0; }
  ul.files li { background:#fff; border:1px solid #e2e8f0; border-radius:8px;
                padding:9px 14px; margin:6px 0; }
  ul.files .meta { float:right; }
"""


def page(title: str, body: str, *, active: str) -> str:
    nav_items = "".join(
        f"<a href='{href}' class='{'on' if href == active else ''}'>{label}</a>"
        for href, label in NAV
    )
    return (
        "<!DOCTYPE html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
        f"<nav><div class='wrap'><span class='brand'>트레이딩 v0.3</span>{nav_items}</div></nav>"
        f"<div class='wrap'>{body}</div></body></html>"
    )


def placeholder(title: str, phase_label: str, *, active: str) -> str:
    return page(
        title,
        f"<h1>{html.escape(title)}</h1><div class='card'>{html.escape(phase_label)} 단계에서 "
        "구현 예정입니다(P-15 계획).</div>",
        active=active,
    )


__all__ = ["CSS", "NAV", "page", "placeholder"]
