"""승인 전이 도구 — OrderDraft draft→approved (운영자 수동, 의도된 마찰. 설계서 §6).

저녁 결재 보고에서 검토한 OrderDraft를 운영자가 명시적으로 승인한다. 승인된 초안만
다음 거래일 R5.5/arm-check의 **활성 풀**에 들어가고(time_stop_days 거래일 TTL),
조건 일치 시 arm 대상이 된다.

전이는 append-only(새 version) — UPDATE 금지(PlaybookStore 규약). 자동 승인 없음:
``--list``로 미승인 목록을 확인하고 **id를 명시**해 승인한다(마찰은 의도다).

  python -m trading.approve --list
  python -m trading.approve order.20260611.170920.buy order.20260611.219130.buy
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


def run(argv: Sequence[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python -m trading.approve --list | <order-id> [<order-id> ...]")
        return 2
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


__all__ = ["approve", "list_pending", "run"]


if __name__ == "__main__":
    raise SystemExit(main())
