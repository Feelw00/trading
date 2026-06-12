"""arm-check — 운영자 9~10시 집행 보조 (P-6, 온디맨드, 순수 코드 + 해설).

``python -m trading.arm_check`` — 당일 플레이북의 **발동 조건 충족 여부(순수 코드 판단)** +
흐름변수·트랜치·스탑 **결정론 해설**을 출력한다. LLM 분석은 한 층 위(arm-check 스킬)가
이 산출을 받아 얹는다 — **판단/해설은 코드, 분석은 LLM**(절대금지 #2).

select_playbooks(R5.5 cron)와 달리 **arm하지 않는다** — 집행은 운영자 수동(의도된 마찰).
읽기 전용이라 장중을 거부하지 않고, 오히려 장중이라야 흐름 관측이 의미 있다(가드는 안내만).

대상은 **활성 approved 풀**(``PlaybookStore.active_playbooks``) — 날짜 라벨이 아니라
status=approved + TTL(time_stop_days 거래일) 미경과로 조회한다. 그래서 "어젯밤 승인했지만
오늘 미발동 → 모레 갭 오면 진입" 같은 다일 셋업을 놓치지 않고, R5 생성일/조회일 날짜
어긋남에도 영향받지 않는다.

흐름 스냅샷은 ``flowsnap.build_snapshot``(KIS 실시간 + 주입 파일). 발동 판단은 기존
``selector.select`` 재사용.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from trading.collectors.base import KST, now_kst
from trading.collectors.kis import client_from_env as kis_from_env
from trading.contracts.order import OrderDraft
from trading.flowsnap import build_snapshot
from trading.journal.playbooks import PlaybookStore
from trading.market_calendar.calendar import in_krx_session
from trading.position_check import PositionView, check_positions
from trading.position_check import render_lines as render_position_lines
from trading.reports import explain
from trading.reports.render import _symbol_names  # 종목명 조회(시세 DB) 재사용
from trading.selector import select
from trading.selector.engine import ConditionEval


@dataclass(frozen=True)
class ConditionView:
    cond_ko: str            # '체결강도(...) 110 이상'
    met: bool
    observed: float | None
    note: str               # '관측치 없음' 등 사유


@dataclass(frozen=True)
class ItemView:
    playbook_id: str
    headline: str           # '엘티씨(170920) 매수'
    draft_id: str
    status: str             # 활성 풀이라 항상 approved
    active: bool            # 발동 조건 전부 충족(순수 코드 판단)
    summary: str            # R5 근거 1줄
    conditions: list[ConditionView]
    tranches: list[str]
    stop: str
    cap: str
    expiry: date | None = None   # TTL 만료일(time_stop_days 거래일 후)


@dataclass(frozen=True)
class AssessResult:
    day: str
    now_iso: str
    in_session: bool
    snapshot_notes: list[str]
    items: list[ItemView]                       # 활성 approved 풀(발동 판단)
    candidates: list[ItemView] = field(default_factory=list)  # 미승인 후보(승인 시 발동 미리보기)
    positions: list[PositionView] = field(default_factory=list)  # 보유 포지션 점검(P-8)
    field_notes: list[str] = field(default_factory=list)

    @property
    def active_count(self) -> int:
        return sum(1 for it in self.items if it.active)

    @property
    def pending_count(self) -> int:
        return len(self.candidates)

    @property
    def no_trade(self) -> bool:
        return self.active_count == 0


def _condition_views(evals: tuple[ConditionEval, ...]) -> list[ConditionView]:
    return [
        ConditionView(
            cond_ko=explain.explain_condition(e.var, e.expr),
            met=e.met,
            observed=e.observed,
            note=e.note,
        )
        for e in evals
    ]


def assess(
    *,
    now: datetime | None = None,
    playbook_store: PlaybookStore | None = None,
    kis_client: object | None = None,
) -> AssessResult:
    """활성 approved 풀 발동 판단 + 결정론 해설 조립(순수 코드). arm/발송 없음."""
    resolved = (now if now is not None else now_kst()).astimezone(KST)

    ps = playbook_store if playbook_store is not None else PlaybookStore()
    active = ps.active_playbooks(resolved)        # 승인됨 + TTL
    candidates = ps.candidate_playbooks(resolved)  # 미승인 후보(아침 검토·승인 대상)

    srtns = list(dict.fromkeys(d.symbol for _, d, _ in (*active, *candidates)))
    names = _symbol_names(srtns)
    client = kis_client if kis_client is not None else kis_from_env()
    snapshot, snap_notes = build_snapshot(srtns, kis_client=client, now=resolved)  # type: ignore[arg-type]

    all_playbooks = [pb for pb, _, _ in active] + [pb for pb, _, _ in candidates]
    result = select(all_playbooks, snapshot)
    act_by_id = {a.playbook.id: a for a in result.activations}

    def _items(pairs: list[tuple[Any, OrderDraft, Any]]) -> list[ItemView]:
        out: list[ItemView] = []
        for pb, draft, expiry in pairs:
            act = act_by_id.get(pb.id)
            out.append(
                ItemView(
                    playbook_id=pb.id,
                    headline=explain.draft_headline(draft, name=names.get(draft.symbol)),
                    draft_id=draft.id,
                    status=draft.status.value,
                    active=bool(act and act.active),
                    summary=pb.summary or "",
                    conditions=_condition_views(act.evals) if act else [],
                    tranches=explain.explain_tranches(draft.tranches),
                    stop=explain.explain_stop(draft.stop, draft.time_stop_days),
                    cap=explain.humanize_cap(draft.total_size_cap),
                    expiry=expiry,
                )
            )
        return out

    items = _items(active)
    candidate_items = _items(candidates)

    if playbook_store is None:
        ps.close()

    # 보유 포지션 점검 — 같은 KIS 클라이언트 재사용(스탑 거리·시간손절은 순수 계산)
    position_views = check_positions(now=resolved, kis_client=client)  # type: ignore[arg-type]

    return AssessResult(
        day=resolved.date().isoformat(),
        now_iso=resolved.isoformat(timespec="minutes"),
        in_session=in_krx_session(resolved),
        snapshot_notes=snap_notes,
        items=items,
        candidates=candidate_items,
        positions=position_views,
    )


def _render_item(it: ItemView, *, candidate: bool) -> list[str]:
    """한 셋업 블록 — 활성/후보 공통. 후보는 '승인 시 발동' 미리보기로 읽는다."""
    if candidate:
        mark = "▶ 승인 시 발동" if it.active else "▷ 승인해도 미발동"
        exp = f" · 승인 시 유효 {it.expiry.isoformat()}까지" if it.expiry else ""
    else:
        mark = "● 발동" if it.active else "○ 미발동"
        exp = f" · 만료 {it.expiry.isoformat()}" if it.expiry else ""
    out = [f"\n### {mark} — {it.headline}{exp}"]
    if it.summary:
        out.append(f"근거: {it.summary}")
    out.append("발동 조건(AND):")
    for c in it.conditions:
        obs = f"관측 {c.observed:g}" if c.observed is not None else (c.note or "관측치 없음")
        out.append(f"- {'O' if c.met else 'X'} {c.cond_ko} → {obs}")
    out.append(f"진입(3트랜치): {' / '.join(it.tranches)}")
    out.append(f"방어: {it.stop} | 상한 {it.cap}")
    if candidate:
        out.append(f"승인: `python -m trading.approve {it.draft_id}`")
    return out


def render_text(r: AssessResult) -> str:
    """사람이 읽는(그리고 스킬 LLM이 분석 grounding으로 읽는) 마크다운."""
    lines: list[str] = [f"# arm-check — {r.day} {r.now_iso}"]
    lines.append(
        "장중(흐름 관측 유효)" if r.in_session
        else "장외 — 흐름 관측치가 비거나 stale일 수 있음(집행 전 장중 재확인)"
    )

    if r.items:
        verdict = "비거래(발동 0)" if r.no_trade else f"발동 가능 {r.active_count}/{len(r.items)}"
        lines.append(f"\n## 승인된 셋업: {verdict} / 활성 풀 {len(r.items)}건")
        for it in r.items:
            lines.extend(_render_item(it, candidate=False))
    else:
        lines.append("\n## 승인된 셋업: 없음 — 활성 풀 비어 있음")

    if r.candidates:
        n_arm = sum(1 for it in r.candidates if it.active)
        lines.append(
            f"\n## 승인 후보(미승인 {len(r.candidates)}건, 검토·승인 대상) — "
            f"지금 승인 시 발동 {n_arm}건"
        )
        for it in r.candidates:
            lines.extend(_render_item(it, candidate=True))

    if r.positions:
        n_review = sum(1 for v in r.positions if v.review_needed)
        lines.append(f"\n## 보유 포지션 점검({len(r.positions)}건) — 정리 검토 {n_review}건")
        lines.extend(f"- {ln}" if not ln.startswith("  ") else ln
                     for ln in render_position_lines(r.positions))

    lines.append("\n## 흐름 관측 결측")
    for n in r.snapshot_notes:
        lines.append(f"- {n}")
    return "\n".join(lines)


def run(*, now: datetime | None = None) -> int:
    """CLI — assess 후 마크다운 출력. 스킬(LLM)이 stdout을 분석 grounding으로 읽는다."""
    print(render_text(assess(now=now)))
    return 0


def main() -> int:
    return run()


__all__ = [
    "AssessResult",
    "ConditionView",
    "ItemView",
    "assess",
    "render_text",
    "run",
]


if __name__ == "__main__":
    raise SystemExit(main())
