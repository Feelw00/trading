"""매매 가이드 페이지(/paper) — 최종 선정 종목을 언제까지 사고 언제 팔지.

실주문 없음 — 가이드는 페이퍼 원장(EOD 결정론 시뮬레이션)에서 파생되고, 성과
카드는 같은 원장의 검증 지표다. 운영자 지시(2026-09-02): "테스트"가 아니라
가이드로 — 표는 지시형 문구, 가이드 표가 주인공이고 성과는 아래에 병기.

표 구성(운영자 지시 2026-09-02 오후 — "데이터를 마구잡이로 넣지 말 것"):
- 표 1 가이드: 종목 · 시작가 · 현재가 · 수익률 · 매수 상한(숫자만) · 매도선 · 다음 매도선
  (가까운 두 선, 금액 뒤 목표가 대비 %) · 정리(금액만). 전체 사다리는 매도선 호버.
- 표 2 실계좌·예약(EXEC-12): 표 1과 분리. 종목 · 실보유 · 평단 · 예약 매도 · 수량 · 상태
  (신규/유지/거부/없음/가이드 밖). 모드(모의·주문 미전송 / 실주문)는 제목 옆에 한 번.
"""

import html
from datetime import date, datetime

from trading.collectors.base import now_kst
from trading.guide_orders import account_view, short_label
from trading.paper import PaperStore, PositionView, mark
from trading.web.layout import page


def _d(bas_dt: str) -> date:
    return date(int(bas_dt[:4]), int(bas_dt[4:6]), int(bas_dt[6:8]))


def _pct_label(price: float, target: float) -> str:
    """가이드선 → 목표가 대비 %(사다리 정의 그대로: 80·90·100·120·150)."""
    return f"{int(round(price / target * 100))}%" if target else "—"


def _sell_cells(v: PositionView) -> tuple[str, str]:
    """(매도선, 다음 매도선) — 아직 안 판 가까운 두 선. 전체 사다리는 첫 셀 호버."""
    if v.status != "open" or not v.sell_plan:
        return "—", "—"
    lines = [(p, _pct_label(p, v.target_price)) for p, _q in v.sell_plan]
    full = " → ".join(f"{p:,.0f} ({lb})" for p, lb in lines)
    first = (f"<span class='tip' data-tip='전체 사다리: {full}'>"
             f"{lines[0][0]:,.0f} <span class='meta'>({lines[0][1]})</span></span>")
    second = (f"{lines[1][0]:,.0f} <span class='meta'>({lines[1][1]})</span>"
              if len(lines) > 1 else "—")
    return first, second


def _guide_table(views: list[PositionView], names: dict[str, str], today: date) -> str:
    last_dt = max((v.last_dt for v in views if v.last_dt), default="—")
    rows = [
        f"<div class='card scroll'><table><tr><th>종목</th><th>시작가</th>"
        f"<th>현재가 <span class='meta'>{last_dt}</span></th><th>수익률</th>"
        "<th class='hl'>매수 상한</th><th class='hl'>매도선</th><th class='hl'>다음 매도선</th>"
        "<th>정리</th></tr>"
    ]
    for v in sorted(views, key=lambda x: x.symbol):
        if v.status != "open" or v.buy_ceiling is None:
            buy = "—"
        else:
            buy = f"{v.buy_ceiling:,.0f}" + ("" if v.in_buy_zone else " <span class='meta'>초과</span>")
        sell1, sell2 = _sell_cells(v)
        fin = f"{v.final_exit_price:,.0f}" if v.status == "open" else "—"
        pr = f"{v.pnl_pct:+.1%}" if v.pnl_pct is not None else "—"
        name = html.escape(names.get(v.symbol, v.symbol))
        tag = "" if v.status == "open" else " <span class='meta'>청산</span>"
        if v.cycle:
            tag += f" <span class='meta'>사이클 {v.cycle + 1}</span>"
        if v.status == "open" and (today - _d(v.opened)).days > 730:
            tag += " <span class='pill warn'>2년 경과</span>"
        rows.append(
            f"<tr><td><a href='/stocks/{v.symbol}'>{name}</a>{tag}</td>"
            f"<td>{v.base_price:,.0f}</td>"
            f"<td>{f'{v.last_price:,.0f}' if v.last_price else '—'}</td>"
            f"<td><b>{pr}</b></td>"
            f"<td class='hl'>{buy}</td><td class='hl'>{sell1}</td><td class='hl'>{sell2}</td>"
            f"<td>{fin}</td></tr>"
        )
    rows.append("</table></div>")
    return "".join(rows)


_STATE = {"intent": "신규", "sent": "신규", "keep": "유지", "rejected": "거부", "skip": "없음"}


def _account_table(names: dict[str, str], guided: set[str]) -> str:
    """표 2 — 토스 실보유 스냅샷 + 가이드 매도 예약(EXEC-12). 저널 없으면 빈 문자열."""
    acct, plans = account_view()
    if not acct:
        return ""
    modes = {p.mode for p in plans.values()}
    mode_lbl = "실주문" if modes == {"live"} else "모의 · 주문 미전송"
    ts_iso = max((p.ts for p in plans.values()), default="")
    try:
        ts_lbl = f" · 갱신 {datetime.fromisoformat(ts_iso):%m-%d %H:%M}" if ts_iso else ""
    except ValueError:
        ts_lbl = ""
    rows = [
        f"<h2>실계좌 · 예약 <span class='meta'>{mode_lbl}{ts_lbl}</span></h2>",
        "<div class='card scroll'><table><tr><th>종목</th><th>실보유</th><th>평단</th>"
        "<th class='hl'>예약 매도</th><th class='hl'>수량</th><th>상태</th></tr>",
    ]
    for sym, h in sorted(acct.items(), key=lambda kv: names.get(kv[0], kv[1].name or kv[0])):
        pl = plans.get(sym)
        name = html.escape(names.get(sym, h.name or sym))
        link = f"<a href='/stocks/{sym}'>{name}</a>" if sym in guided else name
        avg = f"{h.avg_price:,.0f}" if h.avg_price else "—"
        if sym not in guided:
            resv, qty, state = "—", "—", "가이드 밖"
        elif pl is None or pl.trigger_price is None:
            resv, qty, state = "—", "—", (_STATE.get(pl.event, pl.event) if pl else "—")
        else:
            resv = f"{pl.trigger_price:,} <span class='meta'>({short_label(pl.leg_label)})</span>"
            qty = f"{pl.quantity or 0}주"
            state = _STATE.get(pl.event, pl.event)
        rows.append(
            f"<tr><td>{link}</td><td>{h.quantity}주</td><td>{avg}</td>"
            f"<td class='hl'>{resv}</td><td class='hl'>{qty}</td><td>{state}</td></tr>"
        )
    rows.append("</table></div>")
    return "".join(rows)


def render_paper() -> str:
    store = PaperStore()
    try:
        views = mark(store)
    finally:
        store.close()
    from trading.web.data import stock_names

    names = stock_names()
    parts = [
        "<h1>매매 가이드 — 최종 선정 종목</h1>",
        "<div class='meta'><b>실주문 없음</b> — 심사 승인 종목의 분할 매매 가이드"
        "(가상 원장 기반). 매수는 상한가 아래에서 분할 매수 · 매도는 목표가 "
        "80·90·100·120%에 각 20%, 150%에 잔량 정리 · 90% 이상 매도선 터치 후 직전 선 "
        "이탈 시 잔량 정리(이익 보호) · 가격은 EOD 종가(+1영업일 지연).</div>",
    ]
    if not views:
        parts.append("<div class='card meta'>선정 종목 없음 — "
                     "<code>python -m trading.paper register</code>로 승인 종목 등록</div>")
        return page("매매 가이드", "".join(parts), active="/paper")

    today = now_kst().date()
    parts.append(_guide_table(views, names, today))

    pnls = [v.pnl_pct for v in views if v.pnl_pct is not None]
    avg = sum(pnls) / len(pnls) if pnls else 0.0
    # 헤드라인 = 종목 균등가중 평균만(운영자 2026-09-02: 총액 기준 폐지 — 원장 100단위는
    # 정규화 단위라 총액 %는 고가주 편중 지표)
    parts.append(
        f"<div class='card hero'><span class='big'>평균 수익률 {avg:+.2%}</span> "
        f"<span class='meta'>(종목 균등가중 · {len(views)}종목)</span></div>"
    )
    parts.append(
        "<div class='meta'>시작가 = 실제 투자 시작 가격(불변 — 추가 매수로 평단이 낮아져도 "
        "그대로). 매도선·다음 매도선 = 목표가 대비 % 선(호버: 전체 사다리). "
        "정리 = 목표가 150% 도달가(등록 후 3년 경과 시 시한 청산). "
        "수익률 = (실현+평가) ÷ 투입 − 1(정규화 단위, 시작가 대비).</div>"
    )
    acct_html = _account_table(names, {v.symbol for v in views if v.status == "open"})
    if acct_html:
        parts.append(acct_html)
        parts.append("<div class='meta'>실계좌 · 예약 = 토스 스냅샷과 가이드 매도 조건주문"
                     "(평일 08:40 갱신 — 수량이 바뀐 종목만 취소 후 재등록).</div>")
    return page("매매 가이드", "".join(parts), active="/paper")


__all__ = ["render_paper"]
