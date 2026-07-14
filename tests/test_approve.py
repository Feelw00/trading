"""승인 전이(draft→approved) + 활성 approved 풀(status·TTL·dedup) — P-7."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading import approve
from trading.contracts.order import (
    MarketState,
    OrderDraft,
    OrderStatus,
    OrderType,
    Side,
    Stop,
    StopType,
    Tranche,
)
from trading.contracts.playbook import Playbook
from trading.journal.playbooks import PlaybookStore

KST = ZoneInfo("Asia/Seoul")


def _add(
    ps: PlaybookStore, day: str, srtn: str, *, status: OrderStatus, as_of: datetime,
    time_stop: int | None = 5,
) -> str:
    draft = OrderDraft(
        id=f"order.{day}.{srtn}.buy", as_of=as_of, fetched_at=as_of, source="t",
        symbol=srtn, side=Side.BUY,
        tranches=[Tranche(label="flush", pct_of_plan=100, order_type=OrderType.LIMIT)],
        total_size_cap="0.5 * normal_unit",
        stop=Stop(type=StopType.CONDITIONAL_ORDER_AT_BROKER, level=5000.0),
        time_stop_days=time_stop, created_when_market=MarketState.CLOSED, status=status,
    )
    pb = Playbook(
        id=f"pb.{day}.{srtn}.buy", as_of=as_of, fetched_at=as_of, source="t",
        thesis_ref="t1", arm_conditions={"gap_pct": "<-3.0"}, order_draft_ref=draft.id,
    )
    ps.append_run([pb], [draft], as_of=as_of.date().isoformat(), scenario_tree=[], checklist=[])
    return draft.id


# --- 승인 전이 ---


def test_approve_transitions_draft_to_approved(tmp_path: Path) -> None:
    ps = PlaybookStore(tmp_path / "pb.sqlite")
    did = _add(ps, "20260610", "001740", status=OrderStatus.DRAFT,
               as_of=datetime(2026, 6, 10, 20, 30, tzinfo=KST))
    approved, skipped = approve.approve([did], playbook_store=ps)
    assert approved == [did] and skipped == []
    assert ps.draft(did).status is OrderStatus.APPROVED  # type: ignore[union-attr]
    ps.close()


def test_approve_skips_already_approved_and_unknown(tmp_path: Path) -> None:
    ps = PlaybookStore(tmp_path / "pb.sqlite")
    did = _add(ps, "20260610", "001740", status=OrderStatus.APPROVED,
               as_of=datetime(2026, 6, 10, 20, 30, tzinfo=KST))
    approved, skipped = approve.approve([did, "order.ghost"], playbook_store=ps)
    assert approved == []
    assert any("이미 approved" in s for s in skipped)
    assert any("초안 없음" in s for s in skipped)
    ps.close()


def test_list_pending_only_drafts(tmp_path: Path) -> None:
    ps = PlaybookStore(tmp_path / "pb.sqlite")
    d1 = _add(ps, "20260610", "001740", status=OrderStatus.DRAFT,
              as_of=datetime(2026, 6, 10, 20, 30, tzinfo=KST))
    _add(ps, "20260610", "005930", status=OrderStatus.APPROVED,
         as_of=datetime(2026, 6, 10, 20, 30, tzinfo=KST))
    assert approve.list_pending(playbook_store=ps) == [d1]
    ps.close()


# --- 활성 approved 풀 (status·TTL·dedup) ---


def test_active_pool_only_approved(tmp_path: Path) -> None:
    ps = PlaybookStore(tmp_path / "pb.sqlite")
    _add(ps, "20260610", "001740", status=OrderStatus.DRAFT,
         as_of=datetime(2026, 6, 10, 20, 30, tzinfo=KST))
    _add(ps, "20260610", "005930", status=OrderStatus.APPROVED,
         as_of=datetime(2026, 6, 10, 20, 30, tzinfo=KST))
    active = ps.active_playbooks(datetime(2026, 6, 11, 9, 0, tzinfo=KST))
    assert [d.symbol for _, d, _ in active] == ["005930"]  # draft 제외
    ps.close()


def test_active_pool_ttl_expiry(tmp_path: Path) -> None:
    ps = PlaybookStore(tmp_path / "pb.sqlite")
    _add(ps, "20260610", "005930", status=OrderStatus.APPROVED, time_stop=5,
         as_of=datetime(2026, 6, 10, 20, 30, tzinfo=KST))
    # 6/11(TTL 내)엔 보이고, 7/1(경과)엔 빠진다
    assert len(ps.active_playbooks(datetime(2026, 6, 11, 9, 0, tzinfo=KST))) == 1
    assert ps.active_playbooks(datetime(2026, 7, 1, 9, 0, tzinfo=KST)) == []
    ps.close()


def test_candidate_pool_drafts_only_ttl_ignored(tmp_path: Path) -> None:
    ps = PlaybookStore(tmp_path / "pb.sqlite")
    _add(ps, "20260610", "001740", status=OrderStatus.DRAFT, time_stop=5,
         as_of=datetime(2026, 6, 10, 20, 30, tzinfo=KST))
    _add(ps, "20260610", "005930", status=OrderStatus.APPROVED,
         as_of=datetime(2026, 6, 10, 20, 30, tzinfo=KST))
    # 후보는 draft만 (approved 제외) — 승인 전이라 TTL 한참 지나도 후보로 남아 검토 가능
    cand = ps.candidate_playbooks(datetime(2026, 7, 1, 9, 0, tzinfo=KST))
    assert [d.symbol for _, d, _ in cand] == ["001740"]
    assert cand[0][2] is not None  # 만료일은 "승인 시 유효기간" 참고로 동봉
    ps.close()


def test_active_pool_dedup_latest_per_symbol_side(tmp_path: Path) -> None:
    ps = PlaybookStore(tmp_path / "pb.sqlite")
    # 같은 (005930, buy)을 6/9·6/10 두 번 승인 — 최신(6/10)만 풀에
    _add(ps, "20260609", "005930", status=OrderStatus.APPROVED,
         as_of=datetime(2026, 6, 9, 20, 30, tzinfo=KST))
    _add(ps, "20260610", "005930", status=OrderStatus.APPROVED,
         as_of=datetime(2026, 6, 10, 20, 30, tzinfo=KST))
    active = ps.active_playbooks(datetime(2026, 6, 11, 9, 0, tzinfo=KST))
    assert len(active) == 1
    assert active[0][1].id == "order.20260610.005930.buy"  # 최신 as_of
    ps.close()


# --- 활성 풀 다이제스트 CLI (--pool, boot 배선) ---


def test_print_pool_digest_and_empty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from trading.collectors.base import now_kst

    ps = PlaybookStore(tmp_path / "pb.sqlite")
    assert approve._print_pool(playbook_store=ps) == 0
    assert "활성(approved) 풀 없음" in capsys.readouterr().out

    _add(ps, now_kst().strftime("%Y%m%d"), "001740", status=OrderStatus.APPROVED,
         as_of=now_kst())
    assert approve._print_pool(playbook_store=ps) == 0
    out = capsys.readouterr().out
    assert "활성(approved) 풀 1건" in out and "001740" in out and "손절 5,000" in out
    ps.close()
