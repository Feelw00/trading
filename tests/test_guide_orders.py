"""EXEC-12 — 가이드 매도 예약: 계획(내림·호가단위 올림)·저널·모드·정산(체결→사다리 진행)."""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from trading import guide_orders as go
from trading.guide_orders import BrokerStore, ladder_of, plan_next_leg, round_up_to_tick, run
from trading.paper import PROPOSED_PAPER, PaperStore

KST = ZoneInfo("Asia/Seoul")
T0 = datetime(2026, 9, 3, 8, 40, tzinfo=KST)


class FakeBroker:
    def __init__(self, items: list[dict[str, Any]]) -> None:
        self.items = items
        self.open: list[dict[str, Any]] = []
        self.details: dict[str, dict[str, Any]] = {}
        self.orders_: dict[str, dict[str, Any]] = {}
        self.canceled: list[str] = []
        self.placed: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.reject = False
        self._seq = 0

    def holdings(self) -> dict[str, Any]:
        self.calls.append("holdings")
        return {"items": self.items}

    def conditional_orders(self, status: str = "OPEN") -> Any:
        self.calls.append(f"conds:{status}")
        return {"conditionalOrders": self.open}

    def conditional_order(self, conditional_order_id: str) -> dict[str, Any]:
        return self.details.get(conditional_order_id, {})

    def cancel_conditional(self, conditional_order_id: str) -> None:
        self.canceled.append(conditional_order_id)
        self.open = [o for o in self.open if o.get("conditionalOrderId") != conditional_order_id]

    def place_sell_conditional(
        self, symbol: str, quantity: int, *, trigger_price: int, order_price: int,
        expire_date: str, client_order_id: str,
    ) -> dict[str, Any]:
        if self.reject:
            raise RuntimeError("400 invalid-request")
        self._seq += 1
        cid = f"c{self._seq}"
        self.placed.append({
            "symbol": symbol, "quantity": quantity, "trigger_price": trigger_price,
            "order_price": order_price, "expire_date": expire_date, "client_order_id": client_order_id,
            "cond_id": cid,
        })
        self.open.append({"conditionalOrderId": cid, "symbol": symbol, "status": "WATCHING"})
        return {"conditionalOrderId": cid, "clientOrderId": client_order_id}

    def order(self, order_id: str) -> dict[str, Any]:
        return self.orders_.get(order_id, {})


def _item(symbol: str, name: str, qty: int, avg: int, last: int) -> dict[str, Any]:
    return {"symbol": symbol, "name": name, "quantity": str(qty),
            "averagePurchasePrice": str(avg), "lastPrice": str(last)}


def _paper(tmp_path: Path, *symbols: str) -> PaperStore:
    p = PaperStore(tmp_path / "paper.sqlite")
    for sym in symbols:
        p.open_position(sym, "20260902", 8000.0, 10000.0, PROPOSED_PAPER)  # 목표가 10,000
    return p


# --- 순수 계산 ---


def test_round_up_to_tick() -> None:
    assert round_up_to_tick(18589) == 18590
    assert round_up_to_tick(4472) == 4475
    assert round_up_to_tick(47308) == 47350
    assert round_up_to_tick(27884) == 27900
    assert round_up_to_tick(2770) == 2770   # 이미 단위 위
    assert round_up_to_tick(1999.5) == 2000


def test_plan_next_leg_floor_and_skip(tmp_path: Path) -> None:
    p = _paper(tmp_path, "000001")
    pos = p.latest_positions()[0]
    ladder = ladder_of(pos)
    assert len(ladder) == len(PROPOSED_PAPER.sell_levels) + 1 and ladder[-1][2] == 1.0
    leg = plan_next_leg(13, ladder, 0)
    assert leg is not None and leg.index == 0 and leg.quantity == int(13 * ladder[0][2])
    assert leg.trigger_price == round_up_to_tick(ladder[0][1]) == leg.order_price
    # 2주: 앞선 비중이 0이 되는 선은 건너뛰고 첫 성립 선으로
    leg2 = plan_next_leg(2, ladder, 0)
    assert leg2 is not None and leg2.quantity == 1 and leg2.index > 0
    assert all(int(2 * ladder[i][2]) == 0 for i in range(leg2.index))
    # 1주: 정리(비중 1.0)에서만 성립
    leg1 = plan_next_leg(1, ladder, 0)
    assert leg1 is not None and leg1.index == len(ladder) - 1 and leg1.quantity == 1
    assert plan_next_leg(0, ladder, 0) is None
    assert plan_next_leg(13, ladder, len(ladder)) is None          # 사다리 소진
    nxt = plan_next_leg(11, ladder, 1)
    assert nxt is not None and nxt.index == 1 and nxt.quantity == int(11 * ladder[1][2])
    p.close()


# --- 실행 ---


def test_run_off_touches_nothing(tmp_path: Path) -> None:
    b = FakeBroker([_item("000001", "A", 13, 2720, 2770)])
    s = run(b, mode="off", store=BrokerStore(tmp_path / "b.sqlite"), paper=_paper(tmp_path), now=T0)
    assert b.calls == [] and s.lines and "[off]" in s.lines[0]


def test_run_dry_run_plans_without_broker_writes(tmp_path: Path) -> None:
    b = FakeBroker([_item("000001", "A", 13, 2720, 2770), _item("999999", "Z", 5, 100, 100)])
    store = BrokerStore(tmp_path / "b.sqlite")
    s = run(b, mode="dry-run", store=store, paper=_paper(tmp_path, "000001"), now=T0)
    assert b.placed == [] and b.canceled == []
    assert s.placed == 1 and s.holdings == 2 and s.guided == 1
    assert any("[dry-run] 000001" in ln for ln in s.lines or [])
    assert any("가이드 밖 보유" in a for a in s.anomalies or [])       # Z는 미등록 종목
    assert store.previous_snapshot() == {"000001": 13, "999999": 5}
    assert store.latest_plan()["000001"]["event"] == "intent"
    assert store.open_orders() == []                                  # intent는 open 아님


def test_live_cancel_reregister_and_fill_advances_ladder(tmp_path: Path) -> None:
    b = FakeBroker([_item("000001", "A", 13, 2720, 2770)])
    store = BrokerStore(tmp_path / "b.sqlite")
    paper = _paper(tmp_path, "000001")
    ladder = ladder_of(paper.latest_positions()[0])
    # 1회차: 등록
    s1 = run(b, mode="live", store=store, paper=paper, now=T0)
    assert s1.placed == 1 and b.placed[0]["quantity"] == int(13 * ladder[0][2])
    assert b.placed[0]["client_order_id"].startswith("guide-000001-0-0-")
    assert b.placed[0]["expire_date"] == "2026-09-10"
    assert [o.cond_id for o in store.open_orders()] == ["c1"]
    # 2회차: 브로커에 살아 있음 → 취소 후 재등록(같은 선)
    s2 = run(b, mode="live", store=store, paper=paper, now=T0)
    assert b.canceled == ["c1"] and s2.canceled == 1 and s2.placed == 1
    assert [o.cond_id for o in store.open_orders()] == ["c2"]
    # 3회차: c2 체결(COMPLETED) + 보유 11 → filled 정산, 다음 선(leg1) 등록
    b.open = []
    b.details["c2"] = {"status": "COMPLETED", "first": {"triggeredOrderId": "o9"}}
    b.orders_["o9"] = {"execution": {"filledQuantity": "2"}}
    b.items = [_item("000001", "A", 11, 2720, 8100)]
    s3 = run(b, mode="live", store=store, paper=paper, now=T0)
    assert s3.filled == 1 and store.done_legs("000001", 0) == 1
    assert b.placed[-1]["quantity"] == int(11 * ladder[1][2])
    assert b.placed[-1]["trigger_price"] == round_up_to_tick(ladder[1][1])
    assert s3.anomalies == []                                         # 체결에 의한 감소 = 정상


def test_manual_sell_detected_not_advancing(tmp_path: Path) -> None:
    b = FakeBroker([_item("000001", "A", 13, 2720, 2770)])
    store = BrokerStore(tmp_path / "b.sqlite")
    paper = _paper(tmp_path, "000001")
    run(b, mode="dry-run", store=store, paper=paper, now=T0)
    b.items = [_item("000001", "A", 10, 2720, 2770)]
    s = run(b, mode="dry-run", store=store, paper=paper, now=T0)
    assert any("수동 매도 감지" in a for a in s.anomalies or [])
    assert store.done_legs("000001", 0) == 0


def test_expired_and_external_close_are_journaled(tmp_path: Path) -> None:
    b = FakeBroker([_item("000001", "A", 13, 2720, 2770)])
    store = BrokerStore(tmp_path / "b.sqlite")
    paper = _paper(tmp_path, "000001")
    run(b, mode="live", store=store, paper=paper, now=T0)
    b.open = []
    b.details["c1"] = {"status": "EXPIRED", "first": {}}
    s = run(b, mode="live", store=store, paper=paper, now=T0)
    assert s.filled == 0 and s.placed == 1 and store.done_legs("000001", 0) == 0
    b.open = []
    b.details["c2"] = {"status": "CANCELED", "first": {}}
    s2 = run(b, mode="live", store=store, paper=paper, now=T0)
    assert any("외부에서 종료" in a for a in s2.anomalies or [])


def test_rejected_order_journaled_and_continues(tmp_path: Path) -> None:
    b = FakeBroker([_item("000001", "A", 13, 2720, 2770), _item("000002", "B", 13, 100, 100)])
    b.reject = True
    store = BrokerStore(tmp_path / "b.sqlite")
    s = run(b, mode="live", store=store, paper=_paper(tmp_path, "000001", "000002"), now=T0)
    assert s.placed == 0 and len([a for a in s.anomalies or [] if "등록 거부" in a]) == 2
    assert store.open_orders() == []


def test_foreign_conditional_reported_not_cancelled(tmp_path: Path) -> None:
    b = FakeBroker([_item("000001", "A", 13, 2720, 2770)])
    b.open = [{"conditionalOrderId": "x-operator", "symbol": "000001", "status": "WATCHING"}]
    s = run(b, mode="live", store=BrokerStore(tmp_path / "b.sqlite"), paper=_paper(tmp_path, "000001"), now=T0)
    assert b.canceled == [] and any("외부 조건주문 1건" in ln for ln in s.lines or [])


def test_mode_env_and_kill(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("GUIDE_ORDERS_MODE", raising=False)
    assert go.guide_orders_mode(kill_file=tmp_path / "KILL") == "dry-run"
    monkeypatch.setenv("GUIDE_ORDERS_MODE", "live")
    assert go.guide_orders_mode(kill_file=tmp_path / "KILL") == "live"
    (tmp_path / "KILL").write_text("")
    assert go.guide_orders_mode(kill_file=tmp_path / "KILL") == "off"
    monkeypatch.setenv("GUIDE_ORDERS_MODE", "bogus")
    assert go.guide_orders_mode(kill_file=tmp_path / "nokill") == "dry-run"
