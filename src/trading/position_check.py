"""보유 포지션 점검 — 순수 코드 (P-8). arm-check·저녁 보고·positions CLI가 공유.

각 open 포지션에 대해:
- 현재가: KIS 실시간(quote_ccnl) → 실패·미설정이면 시세 DB 최신 종가 폴백(as_of 표기).
- 손익%·가격 스탑 잔여 거리%(스탑 이탈=정리 검토 플래그).
- 시간손절: 진입일 + time_stop_days 거래일 → 도래일·잔여 거래일(도래=정리 검토 플래그).
- 자유문 무효화 조건은 **코드가 평가하지 않는다** — 그대로 표시(해석=스킬, 판단=운영자).

판단(정리 여부)은 내리지 않는다 — 관측과 계획 대비 거리만 계산한다(절대금지 #2 정합).
"""

from dataclasses import dataclass
from datetime import date, datetime

from trading.collectors.base import KST, now_kst
from trading.collectors.kis import KisClient
from trading.collectors.kis import client_from_env as kis_from_env
from trading.collectors.market import MarketStore
from trading.contracts.position import PositionRecord
from trading.journal.positions import PositionStore
from trading.market_calendar.calendar import MarketCalendar


@dataclass(frozen=True)
class PositionView:
    position: PositionRecord
    headline: str               # '테스(095610) 10주 @196,300'
    current: float | None       # 현재가(관측 실패 시 None)
    price_as_of: str            # '실시간' | 'EOD <bas_dt>' | '미관측'
    pnl_pct: float | None
    stop_distance_pct: float | None   # 현재가→스탑 거리(음수=이미 이탈)
    stop_breached: bool
    time_stop_expiry: date | None     # 시간손절 도래일(거래일 계산)
    trading_days_left: int | None     # 도래까지 잔여 거래일(0=오늘, 음수 없음)
    time_stop_due: bool
    review_needed: bool               # 정리 검토 플래그(스탑 이탈 또는 시간손절 도래)


def _f(v: object) -> float | None:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _current_price(
    symbol: str, kis: KisClient | None, store: MarketStore
) -> tuple[float | None, str]:
    if kis is not None:
        try:
            cur = _f(kis.quote_ccnl(symbol).get("stck_prpr"))
            if cur is not None:
                return cur, "실시간"
        except Exception:  # noqa: BLE001 — 실시간 실패는 EOD 폴백(결측 흡수)
            pass
    cutoff = store.nth_recent_date(2) or ""
    rows = store.series_for(symbol, cutoff)
    if rows:
        cur = _f(rows[-1][4])  # clpr
        if cur is not None:
            return cur, f"EOD {rows[-1][3]}"
    return None, "미관측"


def _trading_days_until(cal: MarketCalendar, start: date, end: date) -> int:
    """start 다음 거래일부터 end까지 거래일 수(end<start면 0)."""
    if end <= start:
        return 0
    n, cur = 0, start
    while cur < end:
        cur = cal.next_trading_day(cur)
        n += 1
    return n


def check_positions(
    *,
    now: datetime | None = None,
    position_store: PositionStore | None = None,
    kis_client: KisClient | None = None,
    market_store: MarketStore | None = None,
    calendar: MarketCalendar | None = None,
) -> list[PositionView]:
    """open 포지션 전수 점검 — 관측·거리 계산만(정리 판단은 운영자)."""
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    today = resolved.date()
    cal = calendar if calendar is not None else MarketCalendar.default()

    ps = position_store if position_store is not None else PositionStore()
    open_pos = ps.open_positions()
    if position_store is None:
        ps.close()
    if not open_pos:
        return []

    kis = kis_client if kis_client is not None else kis_from_env()
    ms = market_store if market_store is not None else MarketStore()
    names = _symbol_names_safe([p.symbol for p in open_pos])

    views: list[PositionView] = []
    for pos in open_pos:
        current, as_of = _current_price(pos.symbol, kis, ms)
        pnl = (current / pos.avg_price - 1) * 100 if current is not None else None
        stop_dist: float | None = None
        breached = False
        if current is not None and pos.stop_level is not None:
            stop_dist = (current / pos.stop_level - 1) * 100
            breached = current <= pos.stop_level
        expiry: date | None = None
        days_left: int | None = None
        due = False
        if pos.time_stop_days is not None:
            entered = pos.as_of.astimezone(KST).date()
            expiry = cal.add_trading_days(entered, pos.time_stop_days)
            days_left = _trading_days_until(cal, today, expiry)
            due = today >= expiry
        name = names.get(pos.symbol)
        label = f"{name}({pos.symbol})" if name else pos.symbol
        views.append(
            PositionView(
                position=pos,
                headline=f"{label} {pos.qty}주 @{pos.avg_price:,.0f}",
                current=current, price_as_of=as_of, pnl_pct=pnl,
                stop_distance_pct=stop_dist, stop_breached=breached,
                time_stop_expiry=expiry, trading_days_left=days_left, time_stop_due=due,
                review_needed=breached or due,
            )
        )
    if market_store is None:
        ms.close()
    return views


def _symbol_names_safe(srtns: list[str]) -> dict[str, str]:
    from trading.reports.render import _symbol_names

    return _symbol_names(srtns)


def render_lines(views: list[PositionView]) -> list[str]:
    """포지션 점검 → 보고용 라인(마크다운 불릿 텍스트, 섹션 헤더 없이)."""
    out: list[str] = []
    for v in views:
        pos = v.position
        mark = "[정리 검토]" if v.review_needed else "유지 조건 내"
        cur = (
            f"{v.current:,.0f}({v.price_as_of}) {v.pnl_pct:+.1f}%"
            if v.current is not None and v.pnl_pct is not None
            else "현재가 미관측"
        )
        out.append(f"**{v.headline}** — {cur} | {mark}")
        if pos.stop_level is not None:
            sd = f" (여유 {v.stop_distance_pct:+.1f}%)" if v.stop_distance_pct is not None else ""
            flag = " **이탈**" if v.stop_breached else ""
            out.append(f"  스탑 {pos.stop_level:,.0f}{sd}{flag}")
        if v.time_stop_expiry is not None:
            t = "**도래**" if v.time_stop_due else f"잔여 {v.trading_days_left}거래일"
            out.append(f"  시간손절 {v.time_stop_expiry.isoformat()} — {t}")
        if pos.invalidation_text:
            out.append(f"  무효화(운영자 확인): {pos.invalidation_text}")
        if pos.hypothesis:
            out.append(f"  가설: {pos.hypothesis}")
    return out


__all__ = ["PositionView", "check_positions", "render_lines"]
