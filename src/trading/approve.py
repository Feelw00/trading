"""승인 전이 도구 — draft→approved 자동(EXEC-1) + 운영자 거부권(veto).

**EXEC-1(운영자 결정 2026-07-13):** 수동 결재를 자동 승인+거부권으로 전환.
R5 하드게이트를 통과한 당일 초안은 synth 직후 ``auto_approve_pending``이 일괄
approved 전이하고 P0로 통지한다. 운영자는 **다음 거래일 09:00(감시 기동) 전까지**
``--veto <id>`` 로 개별 거부할 수 있다(approved→vetoed, 활성 풀 제외).

전이는 append-only(새 version) — UPDATE 금지(PlaybookStore 규약).

  python -m trading.approve --list                  # 미승인 목록
  python -m trading.approve --pool                  # 활성(approved) 풀 — 오늘 감시 대상
  python -m trading.approve <id> [<id> ...]         # 수동 승인(여전히 가능)
  python -m trading.approve --veto <id> [<id> ...]  # 자동 승인 거부(다음날 09:00 전)
"""

import sys
from collections.abc import Sequence

from trading.contracts.order import OrderStatus
from trading.journal.playbooks import PlaybookStore


def list_pending(*, playbook_store: PlaybookStore | None = None) -> list[str]:
    """미승인(draft) 초안 id 목록. 출력만 — 전이는 하지 않는다."""
    ps = playbook_store if playbook_store is not None else PlaybookStore()
    ids = [d.id for d in ps.pending_drafts()]
    if playbook_store is None:
        ps.close()
    return ids


def approve(
    draft_ids: Sequence[str], *, playbook_store: PlaybookStore | None = None
) -> tuple[list[str], list[str]]:
    """주어진 id를 approved로 전이(새 version). 반환=(승인됨, 건너뜀+사유)."""
    ps = playbook_store if playbook_store is not None else PlaybookStore()
    approved: list[str] = []
    skipped: list[str] = []
    for did in draft_ids:
        draft = ps.draft(did)
        if draft is None:
            skipped.append(f"{did}: 초안 없음")
            continue
        if draft.status is OrderStatus.APPROVED:
            skipped.append(f"{did}: 이미 approved")
            continue
        if draft.status is not OrderStatus.DRAFT:
            skipped.append(f"{did}: status={draft.status.value} (draft만 승인 가능)")
            continue
        ps.append_draft(draft.model_copy(update={"status": OrderStatus.APPROVED}))
        approved.append(did)
    if playbook_store is None:
        ps.close()
    return approved, skipped


def veto(
    draft_ids: Sequence[str], *, playbook_store: PlaybookStore | None = None
) -> tuple[list[str], list[str]]:
    """approved→vetoed 전이(운영자 거부권). 반환=(거부됨, 건너뜀+사유)."""
    ps = playbook_store if playbook_store is not None else PlaybookStore()
    vetoed: list[str] = []
    skipped: list[str] = []
    for did in draft_ids:
        draft = ps.draft(did)
        if draft is None:
            skipped.append(f"{did}: 초안 없음")
            continue
        if draft.status is not OrderStatus.APPROVED:
            skipped.append(f"{did}: status={draft.status.value} (approved만 거부 가능)")
            continue
        ps.append_draft(draft.model_copy(update={"status": OrderStatus.VETOED}))
        vetoed.append(did)
    if playbook_store is None:
        ps.close()
    return vetoed, skipped


def auto_approve_pending(
    *, playbook_store: PlaybookStore | None = None, day: str | None = None
) -> list[str]:
    """**당일 생성분만** 자동 승인(EXEC-1). R5 하드게이트 통과분만 초안이 되므로
    여기서 추가 판단은 없다(절대금지 #2). 반환=승인된 id.

    당일 한정 이유(2026-07-13 첫 가동 관측): 전건 승인 시 과거 미승인 잔재(운영자가
    결재에서 지나친 것)까지 일괄 부활한다 — 옛 초안은 그대로 draft로 남긴다(TTL과 별개).
    """
    from trading.collectors.base import KST as _KST
    from trading.collectors.base import now_kst as _now_kst

    resolved_day = day if day is not None else _now_kst().astimezone(_KST).strftime("%Y%m%d")
    ps = playbook_store if playbook_store is not None else PlaybookStore()
    ids = [
        d.id
        for d in ps.pending_drafts()
        if d.as_of.astimezone(_KST).strftime("%Y%m%d") == resolved_day
    ]
    approved, _ = approve(ids, playbook_store=ps)
    if playbook_store is None:
        ps.close()
    return approved


def _print_pool(*, playbook_store: PlaybookStore | None = None) -> int:
    """활성(approved+TTL) 풀 다이제스트 — 부팅·수시 점검용(오늘 감시 풀). 출력만.

    스크리너는 EOD(+1영업일 공개)라 아침엔 전일 미반영 — "오늘 후보"의 실체는
    이 풀이다(2026-07-14 부팅 보고 오독 재발 방지).
    """
    from trading.collectors.base import now_kst
    from trading.position_check import _symbol_names_safe

    ps = playbook_store if playbook_store is not None else PlaybookStore()
    try:
        pool = ps.active_playbooks(now_kst())
        if not pool:
            print("활성(approved) 풀 없음.")
            return 0
        names = _symbol_names_safe([d.symbol for _, d, _ in pool])
        print(f"활성(approved) 풀 {len(pool)}건 — 오늘 감시 대상:")
        for _pb, d, expiry in pool:
            nm = names.get(d.symbol) or d.symbol
            stop = f"{d.stop.level:,.0f}" if d.stop and d.stop.level else "시간손절"
            soft = f" 경고 {d.soft_stop.level:,.0f} ·" if d.soft_stop else ""
            tgt = "→".join(f"{t.level:,.0f}" for t in d.targets) if d.targets else "R:R 자동"
            exp = expiry.isoformat() if expiry else "-"
            print(f"  {nm}({d.symbol}) — 손절 {stop} ·{soft} 익절 {tgt} · 만료 {exp} · {d.id}")
        return 0
    finally:
        if playbook_store is None:
            ps.close()


def run(argv: Sequence[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(
            "usage: python -m trading.approve --list | --pool | --veto <id> [...] | "
            "<order-id> [<order-id> ...]"
        )
        return 2
    if argv[0] == "--pool":
        return _print_pool()
    if argv[0] == "--veto":
        if len(argv) < 2:
            print("--veto는 id가 필요합니다")
            return 2
        vetoed, skipped = veto(list(argv[1:]))
        for did in vetoed:
            print(f"vetoed: {did}")
        for s in skipped:
            print(f"skip: {s}")
        return 0 if vetoed or not skipped else 1
    if argv[0] == "--list":
        pending = list_pending()
        if not pending:
            print("미승인(draft) 초안 없음.")
            return 0
        print(f"미승인(draft) {len(pending)}건:")
        for did in pending:
            print(f"  {did}")
        return 0
    approved, skipped = approve(list(argv))
    for did in approved:
        print(f"approved: {did}")
    for s in skipped:
        print(f"skip: {s}")
    return 0 if approved or not skipped else 1


def main() -> int:
    return run(sys.argv[1:])


__all__ = ["approve", "auto_approve_pending", "list_pending", "run", "veto"]


if __name__ == "__main__":
    raise SystemExit(main())
