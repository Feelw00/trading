"""보유 종목 상태 전이 감시 — SCREEN-1 후속(운영자 결정 2026-09-04: **P1**, veto 창 P0 아님).

실보유(브로커 스냅샷 최신, `guide_orders.BrokerStore`)가 **정상 → 관리종목·매매거래정지·상장폐지 의심**(KIS,
`status-v3` 일일 스냅샷) 또는 **감사의견 비적정**(DART, weekly `audit`)으로 바뀌면 P1 1회를 적재한다 —
ALERT-1에 따라 다음 실행 보고 꼬리에 동봉된다. **자동 청산은 없다**: 매도는 EXEC-12 가이드 매도 예약뿐이고
전량 정리는 운영자가 앱에서 직접(policy v2.15 ⑥). 청산 경로 연결은 §6 결재와 함께(OPEN_QUESTIONS SCREEN-1).

판정은 순수 함수(`kis_transitions`·`audit_adverse`)로, 배선(`check_kis`·`check_audit`)은 저장소·알림만 잇는다.
- KIS: 직전 스냅샷(as_of < 최신)이 정상이고 최신이 플래그면 전이. **직전 스냅샷이 없으면 침묵**(전이 증거 없음 —
  이미 플래그된 종목을 새로 샀다면 그것은 심사(R4 탈락 → 승인 없음 → 편입 보류)의 몫).
- DART: 보유 종목의 최신 접수분이 비적정이면 접수번호당 1회. 주간 재수집은 같은 접수분을 무시해 "직전"이 없으므로
  전이 대신 `holding_status_alerts`(symbol, kind, key) 로그로 1회를 보장한다. KIS도 같은 로그로 같은 날 재실행 시
  중복을 막는다.
- KIS 해제(v2.21, 운영자 위임 2026-09-07): 직전 플래그 → 최신 정상은 **P2**(정보 — 행동 없음, 푸시 없음). P2는 꼬리 경로가
  없으므로 배선(`run._holding_status_check`)이 최상위 줄로 출력해 실행 보고 요약에 싣는다. 같은 (symbol, 'kis-clear', as_of)
  1회. 감사의견 해제는 없다(다음 FY 적정 접수분이 자연 해소 — 비적정 P1은 접수분당이라 재발 시 다시 울린다). 자동 재편입 없음.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from trading.collectors.audit import AuditVerdict
from trading.collectors.status import KisStatusRow, StatusStore, classify_kis

KIND_KIS = "kis"
KIND_AUDIT = "audit"
KIND_KIS_CLEAR = "kis-clear"  # v2.21 해제(플래그 → 정상) — P2
RULE = "SCREEN-1 보유 종목 상태 전이(운영자 결정 2026-09-04: P1 — 실행 보고 꼬리 동봉)"
RULE_CLEAR = "SCREEN-1 보유 종목 상태 해제(운영자 위임 2026-09-07: P2 — 정보, 실행 보고 요약 포함)"
ACTION = "자동 청산 없음 — 정리 여부는 운영자 판단(앱에서 직접, v2.15 ⑥). 가이드 매도 예약은 그대로 유지"
DEADLINE = "다음 거래일 08:40 guide-orders 전"


@dataclass(frozen=True)
class Transition:
    symbol: str
    kind: str      # kis | audit
    key: str       # kis: 최신 as_of · audit: 접수번호 — 중복 방지 키
    as_of: str
    reasons: str


def kis_transitions(
    held: Iterable[str],
    latest: Mapping[str, KisStatusRow],
    previous: Mapping[str, KisStatusRow],
) -> list[Transition]:
    """정상(직전) → 플래그(최신) 전이만 — 순수. 직전 없음·직전도 플래그·최신 정상은 제외."""
    out: list[Transition] = []
    for sym in sorted(set(held)):
        cur, prev = latest.get(sym), previous.get(sym)
        if cur is None or prev is None or prev.as_of >= cur.as_of:
            continue
        cur_reasons = classify_kis(cur).reasons
        if cur_reasons and not classify_kis(prev).reasons:
            out.append(Transition(sym, KIND_KIS, cur.as_of, cur.as_of, " · ".join(cur_reasons)))
    return out


def kis_clearances(
    held: Iterable[str],
    latest: Mapping[str, KisStatusRow],
    previous: Mapping[str, KisStatusRow],
) -> list[Transition]:
    """플래그(직전) → 정상(최신) 해제만 — 순수(v2.21, P2). 직전 없음·직전도 정상·최신도 플래그는 제외."""
    out: list[Transition] = []
    for sym in sorted(set(held)):
        cur, prev = latest.get(sym), previous.get(sym)
        if cur is None or prev is None or prev.as_of >= cur.as_of:
            continue
        prev_reasons = classify_kis(prev).reasons
        if prev_reasons and not classify_kis(cur).reasons:
            out.append(Transition(sym, KIND_KIS_CLEAR, cur.as_of, cur.as_of, " · ".join(prev_reasons)))
    return out


def audit_adverse(held: Iterable[str], verdicts: Mapping[str, AuditVerdict], fy: str) -> list[Transition]:
    """보유 종목 중 최신 접수분 감사의견 비적정 — 순수. 접수번호가 키(정정 공시는 새 접수번호 = 새 알림)."""
    out: list[Transition] = []
    for sym in sorted(set(held)):
        v = verdicts.get(sym)
        if v is None or not v.adverse or not v.rcept_no:
            continue
        out.append(Transition(sym, KIND_AUDIT, v.rcept_no, fy, f"감사의견 {v.opinion}(FY{fy}, 접수 {v.rcept_no})"))
    return out


def _emit(
    store: StatusStore, transitions: Iterable[Transition], names: Mapping[str, str], *,
    now_iso: str, notify: Callable[[str], None] | None, prefix: str,
) -> list[str]:
    lines: list[str] = []
    for t in transitions:
        what = f"{prefix}: {t.symbol} {names.get(t.symbol, '')} — {t.reasons}".strip()
        if not store.mark_holding_alert(t.symbol, t.kind, t.key, as_of=t.as_of, note=what, created_at=now_iso):
            continue  # 같은 전이·접수분은 1회만
        if notify is not None:
            notify(what)
        lines.append(what)
    return lines


def _kis_latest_previous(
    store: StatusStore, held: Mapping[str, str],
) -> tuple[dict[str, KisStatusRow], dict[str, KisStatusRow]]:
    """실보유의 최신 KIS 스냅샷과 그 직전 스냅샷(있는 것만)."""
    latest = {s: r for s, r in store.latest_kis_all().items() if s in held}
    previous = {s: p for s, r in latest.items() if (p := store.kis_previous(s, r.as_of)) is not None}
    return latest, previous


def check_kis(
    store: StatusStore, held: Mapping[str, str], *, now_iso: str,
    notify: Callable[[str], None] | None = None,
) -> list[str]:
    """실보유 {symbol: name}의 KIS 상태 전이 → P1(콜백) + 로그. 반환 = 새로 울린 문구."""
    latest, previous = _kis_latest_previous(store, held)
    out: list[str] = []
    for t in kis_transitions(held, latest, previous):
        prev = previous[t.symbol]
        tagged = Transition(t.symbol, t.kind, t.key, t.as_of, f"{t.reasons} ({prev.as_of} 정상 → {t.as_of})")
        out.extend(_emit(store, [tagged], held, now_iso=now_iso, notify=notify, prefix="보유 종목 상태 전이"))
    return out


def check_kis_clear(
    store: StatusStore, held: Mapping[str, str], *, now_iso: str,
    notify: Callable[[str], None] | None = None,
) -> list[str]:
    """실보유의 KIS 플래그 해제(직전 플래그 → 최신 정상) → P2(콜백) + 로그(as_of당 1회). 반환 = 새로 울린 문구."""
    latest, previous = _kis_latest_previous(store, held)
    out: list[str] = []
    for t in kis_clearances(held, latest, previous):
        prev = previous[t.symbol]
        tagged = Transition(t.symbol, t.kind, t.key, t.as_of, f"{t.reasons} 해제 ({prev.as_of} → {t.as_of} 정상)")
        out.extend(_emit(store, [tagged], held, now_iso=now_iso, notify=notify, prefix="보유 종목 상태 해제"))
    return out


def check_audit(
    store: StatusStore, held: Mapping[str, str], *, fy: str, now_iso: str,
    notify: Callable[[str], None] | None = None,
) -> list[str]:
    """실보유의 FY 감사의견 비적정(최신 접수분) → P1(콜백) + 로그(접수번호당 1회)."""
    from trading.collectors.audit import current_opinion

    verdicts = {s: current_opinion(store.audit_rows(s, fy)) for s in held}
    trs = audit_adverse(held, verdicts, fy)
    return _emit(store, trs, held, now_iso=now_iso, notify=notify, prefix="보유 종목 감사의견 비적정")


def held_names() -> dict[str, str]:
    """브로커 최신 스냅샷의 실보유 {symbol: name} — 수량 0 제외. 스냅샷 없음 = 빈 dict(추측 금지)."""
    from trading.guide_orders import BrokerStore

    bs = BrokerStore()
    try:
        return {s: h.name for s, h in bs.latest_holdings().items() if h.quantity > 0}
    finally:
        bs.close()


__all__ = [
    "ACTION",
    "DEADLINE",
    "KIND_AUDIT",
    "KIND_KIS",
    "KIND_KIS_CLEAR",
    "RULE",
    "RULE_CLEAR",
    "Transition",
    "audit_adverse",
    "check_audit",
    "check_kis",
    "check_kis_clear",
    "held_names",
    "kis_clearances",
    "kis_transitions",
]
