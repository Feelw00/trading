"""매매 가이드 페이지(/paper) — 최종 선정 종목을 언제까지 사고 언제 팔지.

실주문 없음 — 가이드는 페이퍼 원장(EOD 결정론 시뮬레이션)에서 파생되고, 성과
카드는 같은 원장의 검증 지표다. 운영자 지시(2026-09-02): "테스트"가 아니라
가이드로 — 표는 지시형 문구, 가이드 표가 주인공이고 성과는 아래에 병기.
"""

import html
from datetime import date, timedelta

from trading.collectors.base import now_kst
from trading.paper import PaperStore, mark
from trading.web.layout import page


def _d(bas_dt: str) -> date:
    return date(int(bas_dt[:4]), int(bas_dt[4:6]), int(bas_dt[6:8]))


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
    last_dt = max((v.last_dt for v in views if v.last_dt), default="—")
    from trading.guide_orders import account_view, short_label

    acct, plans = account_view()  # EXEC-12: 토스 실보유 스냅샷 + 가이드 매도 예약(08:40 갱신)
    parts.append(
        f"<div class='card scroll'><table><tr><th>종목</th><th>시작가</th>"
        f"<th>현재가 <span class='meta'>{last_dt}</span></th><th>수익률</th>"
        "<th class='hl'>매수</th><th class='hl'>매도</th><th>정리</th>"
        "<th>실보유</th><th>예약</th></tr>"
    )
    for v in sorted(views, key=lambda x: x.symbol):
        if v.status != "open" or v.buy_ceiling is None:
            buy = "—"
        elif v.in_buy_zone:
            buy = f"<b>{v.buy_ceiling:,.0f}까지 매수</b>"
        else:
            buy = f"중단 — 상한 {v.buy_ceiling:,.0f}"
        if v.sell_plan and v.total_bought > 0:
            def _pct(q: float) -> str:
                return f"{q / v.total_bought:.0%}"
            nxt_p, nxt_q = v.sell_plan[0]
            full = " → ".join(f"{p:,.0f}에 {_pct(q)}" for p, q in v.sell_plan)
            sell = (f"<span class='tip' data-tip='전체 계획: {full}'>"
                    f"<b>{nxt_p:,.0f}</b>부터 {_pct(nxt_q)}</span>")
        else:
            sell = "완료"
        deadline = _d(v.opened) + timedelta(days=1095)
        warn = (" <span class='pill warn'>2년 경과</span>"
                if v.status == "open" and (today - _d(v.opened)).days > 730 else "")
        fin = f"{v.final_exit_price:,.0f} <span class='meta'>· ~{deadline:%Y.%m}</span>{warn}"
        pr = f"{v.pnl_pct:+.1%}" if v.pnl_pct is not None else "—"
        name = html.escape(names.get(v.symbol, v.symbol))
        tag = "" if v.status == "open" else " <span class='meta'>청산</span>"
        if v.cycle:
            tag += f" <span class='meta'>사이클 {v.cycle + 1}</span>"
        hold = acct.get(v.symbol)
        if hold is None:
            real = "—"
        elif hold.avg_price:
            real = f"{hold.quantity}주 <span class='meta'>평단 {hold.avg_price:,.0f}</span>"
        else:
            real = f"{hold.quantity}주"
        pl = plans.get(v.symbol)
        if pl is None:
            resv = "—"
        elif pl.event == "skip":
            resv = "없음 <span class='meta'>매도선 소진</span>"
        else:
            state = {"intent": "dry-run", "sent": "등록", "keep": "유지"}.get(pl.event, pl.event)
            if pl.event == "keep" and pl.mode != "live":
                state = "dry-run"
            resv = (f"{short_label(pl.leg_label)} {pl.quantity or 0}주 @{(pl.trigger_price or 0):,} "
                    f"<span class='meta'>{state}</span>")
        parts.append(
            f"<tr><td><a href='/stocks/{v.symbol}'>{name}</a>{tag}</td>"
            f"<td>{v.base_price:,.0f}</td>"
            f"<td>{f'{v.last_price:,.0f}' if v.last_price else '—'}</td>"
            f"<td><b>{pr}</b></td>"
            f"<td class='hl'>{buy}</td><td class='hl'>{sell}</td>"
            f"<td>{fin}</td><td>{real}</td><td>{resv}</td></tr>"
        )
    parts.append("</table></div>")

    pnls = [v.pnl_pct for v in views if v.pnl_pct is not None]
    avg = sum(pnls) / len(pnls) if pnls else 0.0
    # 헤드라인 = 종목 균등가중 평균만(운영자 2026-09-02: 총액 기준 폐지 — 원장 100단위는
    # 정규화 단위라 총액 %는 고가주 편중 지표)
    parts.append(
        f"<div class='card hero'><span class='big'>평균 수익률 {avg:+.2%}</span> "
        f"<span class='meta'>(종목 균등가중 · {len(views)}종목)</span></div>"
    )
    parts.append(
        "<div class='meta'>시작가 = 실제 투자 시작 가격(기준가) — 수익률·매수 상한의 앵커. "
        "매도 열에 마우스를 올리면 남은 매도 계획 전체가 보인다"
        "(비중은 전체 포지션 대비 — 추가 매수 시 갱신). 정리 = 목표가 150% 도달가, "
        "~연월은 등록일+3년 시한(미수렴 청산). 수익률 = (실현+평가) ÷ 투입 − 1"
        "(정규화 단위 기준 — 시작가 대비, 실투자 수량과 무관) · "
        "체결 내역은 CLI(python -m trading.paper) 참조. "
        "실보유·예약 = 토스 실계좌 스냅샷과 가이드 매도 조건주문(EXEC-12, 평일 08:40 갱신 — "
        "수량 변경 시에만 재등록). 시작가는 추가 매수로 평단이 낮아져도 불변.</div>"
    )
    return page("매매 가이드", "".join(parts), active="/paper")


__all__ = ["render_paper"]
