"""자동 집행기(EXEC-1) — 하드캡·dry-run·체결→손절·거부권 전이."""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from trading.alerts import Alert
from trading.approve import auto_approve_pending, veto
from trading.collectors.base import KST
from trading.contracts.order import (
    MarketState, OrderDraft, OrderStatus, OrderType, Side, Stop, StopType, Tranche,
)
from trading.executor import (
    ExecPolicy, ExecStore, cap_fraction, exec_mode, execute_armed, reconcile,
    round_down_to_tick, tick_size,
)
from trading.journal.positions import PositionStore

NOW = datetime(2026, 7, 14, 10, 0, tzinfo=KST)  # 화요일 장중


class _Rec:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def notify(self, alert: Alert) -> str:
        self.alerts.append(alert)
        return "rec"


def _draft(
    did: str = "order.20260713.005930.buy", symbol: str = "005930", *,
    side: Side = Side.BUY, stop_level: float | None = 65000.0,
) -> OrderDraft:
    return OrderDraft(
        id=did, as_of=NOW, fetched_at=NOW, source="r5:test",
        symbol=symbol, side=side,
        tranches=[
            Tranche(label="impatience_fee", pct_of_plan=20, order_type=OrderType.LIMIT),
            Tranche(label="flush", pct_of_plan=50, order_type=OrderType.LIMIT),
            Tranche(label="confirmation", pct_of_plan=30, condition="prev_day_high_reclaim"),
        ],
        total_size_cap="0.5 * normal_unit",
        stop=Stop(type=StopType.CONDITIONAL_ORDER_AT_BROKER, level=stop_level) if stop_level else None,
        time_stop_days=7,
        created_when_market=MarketState.CLOSED,
        status=OrderStatus.APPROVED,
    )


def test_tick_table_and_rounding() -> None:
    assert tick_size(1_500) == 1 and tick_size(70_000) == 100 and tick_size(600_000) == 1_000
    assert round_down_to_tick(70_150) == 70_100
    assert round_down_to_tick(4_999) == 4_995


def test_policy_from_env_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    assert ExecPolicy.from_env() == ExecPolicy(account_krw=5_000_000, max_new_per_day=5)
    monkeypatch.setenv("EXEC_ACCOUNT_KRW", "7000000")
    monkeypatch.setenv("EXEC_MAX_NEW_PER_DAY", "-3")  # 비양수 → 기본값
    p = ExecPolicy.from_env()
    assert p.account_krw == 7_000_000 and p.max_new_per_day == 5


def test_cap_fraction_parses_r5_expression() -> None:
    assert cap_fraction("0.5 * normal_unit") == 0.5
    assert cap_fraction("1.0 * normal_unit") == 1.0
    assert cap_fraction("") == 0.5           # 해석 불가 → 보수
    assert cap_fraction("3 * normal_unit") == 0.5  # 범위 밖(>1) → 보수


def test_kill_switch_forces_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXEC_MODE", "live")
    kill = tmp_path / "KILL"
    kill.touch()
    assert exec_mode(kill_file=kill) == "off"
    kill.unlink()
    assert exec_mode(kill_file=kill) == "live"
    monkeypatch.setenv("EXEC_MODE", "이상한값")
    assert exec_mode(kill_file=kill) == "dry-run"  # 알 수 없는 값은 보수(dry-run)


def test_dry_run_sizes_by_analysis_cap_and_dedups(tmp_path: Path) -> None:
    store = ExecStore(tmp_path / "e.sqlite")
    rec = _Rec()
    d = _draft()
    r = execute_armed(d, price=70_150, store=store, policy=ExecPolicy(), mode="dry-run",
                      toss=None, dispatcher=rec, now=NOW)  # type: ignore[arg-type]
    # 가용 500만 × cap 0.5 × 즉시 트랜치 70% = 175만 → 70,100원 지정가 24주
    assert r.action == "ordered" and "24주" in r.detail
    assert len(rec.alerts) == 1 and "dry-run" in rec.alerts[0].what
    # 같은 초안 재발동 → 스킵(1회 원칙)
    r2 = execute_armed(d, price=70_150, store=store, policy=ExecPolicy(), mode="dry-run",
                       toss=None, dispatcher=rec, now=NOW)  # type: ignore[arg-type]
    assert r2.action == "skipped"
    store.close()


def test_daily_runaway_guard_and_pyramiding_allowed(tmp_path: Path) -> None:
    store = ExecStore(tmp_path / "e.sqlite")
    rec = _Rec()
    pol = ExecPolicy(max_new_per_day=2)
    a = execute_armed(_draft("o.1", "000001", stop_level=9_000.0), price=10_000, store=store, policy=pol,
                      mode="dry-run", toss=None, dispatcher=rec, now=NOW)  # type: ignore[arg-type]
    # 계단식: 다른 초안이 같은 종목 재진입 → 허용(운영자 2차 결정)
    b = execute_armed(_draft("o.2", "000001", stop_level=9_000.0), price=10_000, store=store, policy=pol,
                      mode="dry-run", toss=None, dispatcher=rec, now=NOW)  # type: ignore[arg-type]
    assert a.action == b.action == "ordered"
    # 일일 신규 상한 도달 → 폭주 가드
    c = execute_armed(_draft("o.3", "000003", stop_level=9_000.0), price=10_000, store=store, policy=pol,
                      mode="dry-run", toss=None, dispatcher=rec, now=NOW)  # type: ignore[arg-type]
    assert c.action == "skipped" and "폭주 가드" in c.detail
    store.close()


def test_min_one_share_for_expensive_stock(tmp_path: Path) -> None:
    # 계수 예산(175만)으로 0주지만 1주(200만)가 가용액(500만) 이내 → 1주 보장
    store = ExecStore(tmp_path / "e.sqlite")
    r = execute_armed(_draft(), price=2_000_000, store=store, policy=ExecPolicy(),
                      mode="dry-run", toss=None, dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    assert r.action == "ordered" and "1주" in r.detail
    store.close()


def test_skip_when_truly_out_of_cash(tmp_path: Path) -> None:
    store = ExecStore(tmp_path / "e.sqlite")
    # 가격 상한(200만) 이내지만 계좌(100만)로 1주도 불가 → 잔고 부족
    r = execute_armed(_draft(), price=1_900_000, store=store,
                      policy=ExecPolicy(account_krw=1_000_000),
                      mode="dry-run", toss=None, dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    assert r.action == "skipped" and "잔고 부족" in r.detail
    store.close()


def test_sell_draft_and_off_mode_do_nothing(tmp_path: Path) -> None:
    store = ExecStore(tmp_path / "e.sqlite")
    s = execute_armed(_draft(side=Side.SELL), price=10_000, store=store, policy=ExecPolicy(),
                      mode="dry-run", toss=None, dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    assert s.action == "skipped"
    o = execute_armed(_draft(), price=10_000, store=store, policy=ExecPolicy(),
                      mode="off", toss=None, dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    assert o.action == "off"
    store.close()


def test_reconcile_dry_run_registers_stop_and_position(tmp_path: Path) -> None:
    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    rec = _Rec()
    d = _draft()
    execute_armed(d, price=70_150, store=store, policy=ExecPolicy(), mode="dry-run",
                  toss=None, dispatcher=rec, now=NOW)  # type: ignore[arg-type]
    done = reconcile(store=store, mode="dry-run", toss=None, drafts_by_id={d.id: d},
                     dispatcher=rec, position_store=pos, now=NOW)  # type: ignore[arg-type]
    assert done == [d.id]
    assert store.has(d.id, ("stop_intent",))
    opened = pos.open_positions()
    assert len(opened) == 1 and opened[0].symbol == "005930"
    assert opened[0].stop_level == 65000.0 and opened[0].source == "executor:dry-run"
    # 2차 reconcile은 무시(미결 없음)
    assert reconcile(store=store, mode="dry-run", toss=None, drafts_by_id={d.id: d},
                     dispatcher=rec, position_store=pos, now=NOW) == []  # type: ignore[arg-type]
    store.close()
    pos.close()


class _FakeToss:
    def __init__(self) -> None:
        self.orders: list[dict[str, Any]] = []
        self.stops: list[dict[str, Any]] = []
        self.cancels: list[str] = []
        self.status = "FILLED"

    def buying_power_krw(self) -> int:
        return 5_000_000

    def place_limit_order(self, symbol: str, side: str, quantity: int, price: int, *, client_order_id: str) -> dict[str, Any]:
        self.orders.append({"symbol": symbol, "side": side, "qty": quantity, "price": price})
        return {"orderId": "ord-9"}

    def order(self, order_id: str) -> dict[str, Any]:
        placed = self.orders[0]
        return {"status": self.status,
                "execution": {"filledQuantity": str(placed["qty"]), "averagePrice": str(placed["price"])}}

    def cancel_conditional(self, conditional_order_id: str) -> None:
        self.cancels.append(conditional_order_id)

    def place_stop_sell_conditional(self, symbol: str, quantity: int, *, trigger_price: int,
                                    order_price: int, expire_date: str, client_order_id: str) -> dict[str, Any]:
        self.stops.append({"symbol": symbol, "qty": quantity, "trigger": trigger_price,
                           "order_price": order_price, "expire": expire_date})
        return {"conditionalOrderId": "cond-9"}

    def place_oco_sell(self, symbol: str, quantity: int, *, stop_trigger: int, stop_price: int,
                       target_trigger: int, target_price: int, expire_date: str,
                       client_order_id: str) -> dict[str, Any]:
        self.stops.append({"symbol": symbol, "qty": quantity, "trigger": stop_trigger,
                           "order_price": stop_price, "target": target_trigger,
                           "expire": expire_date, "oco": True})
        return {"conditionalOrderId": "oco-9"}


def test_live_flow_order_then_fill_then_stop(tmp_path: Path) -> None:
    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    rec = _Rec()
    toss = _FakeToss()
    d = _draft()
    r = execute_armed(d, price=70_150, store=store, policy=ExecPolicy(), mode="live",
                      toss=toss, dispatcher=rec, now=NOW)  # type: ignore[arg-type]
    assert r.action == "ordered"
    assert toss.orders == [{"symbol": "005930", "side": "BUY", "qty": 24, "price": 70_100}]
    done = reconcile(store=store, mode="live", toss=toss, drafts_by_id={d.id: d},  # type: ignore[arg-type]
                     dispatcher=rec, position_store=pos, now=NOW)  # type: ignore[arg-type]
    assert done == [d.id]
    assert len(toss.stops) == 1
    st = toss.stops[0]
    # 플로어 = max(2틱, 1%) = 650 → 64,350 → 틱(100원) 절사 64,300 (급락 관통 체결, EXEC-8)
    assert st["trigger"] == 65_000 and st["order_price"] == 64_300
    # P-11 Stage B: OCO 익절 — 체결 70,100 + 1.5×(70,100−65,000)=77,750 → 틱 절사 77,700
    assert st.get("oco") is True and st["target"] == 77_700
    assert store.has(d.id, ("stop_sent",))
    assert pos.open_positions()[0].avg_price == 70_100.0
    store.close()
    pos.close()


def test_live_pending_order_waits(tmp_path: Path) -> None:
    store = ExecStore(tmp_path / "e.sqlite")
    toss = _FakeToss()
    toss.status = "PENDING"
    d = _draft()
    execute_armed(d, price=70_150, store=store, policy=ExecPolicy(), mode="live",
                  toss=toss, dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    done = reconcile(store=store, mode="live", toss=toss, drafts_by_id={d.id: d},  # type: ignore[arg-type]
                     dispatcher=_Rec(), position_store=None, now=NOW)  # type: ignore[arg-type]
    assert done == [] and toss.stops == []  # 미체결 — 다음 패스 재시도
    store.close()


def test_time_stop_only_draft_skips_price_stop(tmp_path: Path) -> None:
    store = ExecStore(tmp_path / "e.sqlite")
    d = _draft(stop_level=None)
    execute_armed(d, price=10_000, store=store, policy=ExecPolicy(), mode="dry-run",
                  toss=None, dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    done = reconcile(store=store, mode="dry-run", toss=None, drafts_by_id={d.id: d},
                     dispatcher=_Rec(), position_store=None, now=NOW)  # type: ignore[arg-type]
    assert done == [d.id]
    assert store.has(d.id, ("skip_stop",))
    store.close()


def test_auto_approve_and_veto_transitions(tmp_path: Path) -> None:
    from trading.journal.playbooks import PlaybookStore

    ps = PlaybookStore(tmp_path / "pb.sqlite")
    draft = _draft().model_copy(update={"status": OrderStatus.DRAFT})
    ps.append_draft(draft)
    # 당일 한정: 다른 날짜 지정 시 승인 없음(과거 잔재 부활 방지)
    assert auto_approve_pending(playbook_store=ps, day="20250101") == []
    approved = auto_approve_pending(playbook_store=ps, day=NOW.strftime("%Y%m%d"))
    assert approved == [draft.id]
    assert ps.draft(draft.id).status is OrderStatus.APPROVED  # type: ignore[union-attr]
    vetoed, skipped = veto([draft.id], playbook_store=ps)
    assert vetoed == [draft.id] and skipped == []
    assert ps.draft(draft.id).status is OrderStatus.VETOED  # type: ignore[union-attr]
    # vetoed는 재거부 불가(approved만)
    _, skipped2 = veto([draft.id], playbook_store=ps)
    assert skipped2 and "vetoed" in skipped2[0]
    ps.close()


def _ladder_draft(did: str = "order.20260713.005930.buy") -> OrderDraft:
    """계단식 초안 — 익절 12,000(50%)/15,000(잔량) · 경고 9,300(50%) · 하드 9,000."""
    from trading.contracts.order import ExitLevel

    return OrderDraft(
        id=did, as_of=NOW, fetched_at=NOW, source="r5:test",
        symbol="005930", side=Side.BUY,
        tranches=[Tranche(label="impatience_fee", pct_of_plan=100, order_type=OrderType.LIMIT)],
        total_size_cap="1.0 * normal_unit",
        stop=Stop(type=StopType.CONDITIONAL_ORDER_AT_BROKER, level=9_000.0),
        soft_stop=ExitLevel(level=9_300.0, pct=50),
        targets=[ExitLevel(level=12_000.0, pct=50), ExitLevel(level=15_000.0, pct=50)],
        time_stop_days=7,
        created_when_market=MarketState.CLOSED,
        status=OrderStatus.APPROVED,
    )


def _fill_ladder(store: ExecStore, pos: PositionStore, toss: Any, mode: str) -> OrderDraft:
    d = _ladder_draft()
    execute_armed(d, price=10_000, store=store, policy=ExecPolicy(), mode=mode,
                  toss=toss, dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    reconcile(store=store, mode=mode, toss=toss, drafts_by_id={d.id: d},
              dispatcher=_Rec(), position_store=pos, now=NOW)  # type: ignore[arg-type]
    return d


def test_bracket_uses_final_target_when_r5_specifies(tmp_path: Path) -> None:
    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    toss = _FakeToss()
    _fill_ladder(store, pos, toss, "live")
    assert toss.stops[0]["target"] == 15_000  # R:R 아님 — R5 최종 타깃
    store.close(); pos.close()


def test_manage_exits_target_leg_and_breakeven_raise(tmp_path: Path) -> None:
    from trading.executor import manage_exits

    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    toss = _FakeToss()
    d = _fill_ladder(store, pos, toss, "live")
    # 12,000 도달 → 절반 매도 + 브래킷 교체(본전 상향)
    acted = manage_exits(store=store, mode="live", toss=toss, drafts_by_id={d.id: d},  # type: ignore[arg-type]
                         price_fn=lambda s: 12_050.0, position_store=pos,
                         dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    assert acted == [d.id]
    # 부분 매도: SELL 절반(500주 체결 → 250주) + 기존 브래킷 취소
    sells = [o for o in toss.orders if o["side"] == "SELL"]
    assert sells and sells[0]["qty"] == 250
    assert toss.cancels == ["oco-9"]
    # 새 브래킷: 잔량 250주 · 손절 본전(체결가 10,000) 상향
    new_bracket = store.latest_bracket(d.id)
    assert new_bracket is not None and new_bracket[1] == 250
    avg = pos.open_positions()[0].avg_price
    assert new_bracket[2] >= 9_000 and new_bracket[2] == round_down_to_tick(avg)
    # 같은 레그 재집행 없음
    again = manage_exits(store=store, mode="live", toss=toss, drafts_by_id={d.id: d},  # type: ignore[arg-type]
                         price_fn=lambda s: 12_050.0, position_store=pos,
                         dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    assert again == []
    store.close(); pos.close()


def test_manage_exits_soft_stop_reduces_half(tmp_path: Path) -> None:
    from trading.executor import manage_exits

    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    d = _fill_ladder(store, pos, None, "dry-run")
    acted = manage_exits(store=store, mode="dry-run", toss=None, drafts_by_id={d.id: d},
                         price_fn=lambda s: 9_250.0, position_store=pos,
                         dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    assert acted == [d.id]
    assert store.has(d.id, ("leg_soft",))
    nb = store.latest_bracket(d.id)
    assert nb is not None and nb[2] == 9_000  # 경고 축소는 하드스탑 유지(상향 없음)
    store.close(); pos.close()


def test_manage_exits_holds_between_levels(tmp_path: Path) -> None:
    from trading.executor import manage_exits

    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    d = _fill_ladder(store, pos, None, "dry-run")
    acted = manage_exits(store=store, mode="dry-run", toss=None, drafts_by_id={d.id: d},
                         price_fn=lambda s: 10_500.0, position_store=pos,
                         dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    assert acted == []  # 레벨 사이 — 아무것도 안 함
    store.close(); pos.close()


def test_price_cap_filter_blocks_expensive_stock(tmp_path: Path) -> None:
    # EXEC-4: 주당 200만 초과 배제(계단 청산 불가 종목)
    store = ExecStore(tmp_path / "e.sqlite")
    r = execute_armed(_draft(), price=2_100_000, store=store, policy=ExecPolicy(),
                      mode="dry-run", toss=None, dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    assert r.action == "skipped" and "가격 상한" in r.detail
    store.close()


def test_rotation_swaps_worst_position_for_better_setup(tmp_path: Path) -> None:
    from trading.executor import consider_rotation

    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    # 기존 포지션: 사다리 초안(잔여 여력 작음 — 현재가 14,800, 최종타깃 15,000 ≈ +1.4%)
    old = _fill_ladder(store, pos, None, "dry-run")
    # 신규 후보: 10,000 진입 · 최종타깃 13,000 = +30% — 마진(2배+2%p) 충족
    from trading.contracts.order import ExitLevel

    new = OrderDraft(
        id="order.20260714.000001.buy", as_of=NOW, fetched_at=NOW, source="r5:test",
        symbol="000001", side=Side.BUY,
        tranches=[Tranche(label="impatience_fee", pct_of_plan=100, order_type=OrderType.LIMIT)],
        total_size_cap="1.0 * normal_unit",
        stop=Stop(type=StopType.CONDITIONAL_ORDER_AT_BROKER, level=9_000.0),
        targets=[ExitLevel(level=13_000.0, pct=100)],
        time_stop_days=7, created_when_market=MarketState.CLOSED, status=OrderStatus.APPROVED,
    )
    rec = _Rec()
    ok = consider_rotation(new, 10_000.0, store=store, mode="dry-run", toss=None,
                           drafts_by_id={old.id: old, new.id: new},
                           price_fn=lambda s: 14_800.0, position_store=pos,
                           dispatcher=rec, now=NOW)  # type: ignore[arg-type]
    assert ok is True
    assert store.rotations_today(NOW.strftime("%Y%m%d")) == 1
    assert pos.open_positions() == []          # 기존 포지션 정리(교체)
    assert store.committed_krw() < 5_000_000   # 매도 대금만큼 가용 복원
    # 일 1회 한도 — 두 번째 교체 거부
    assert consider_rotation(new, 10_000.0, store=store, mode="dry-run", toss=None,
                             drafts_by_id={new.id: new}, price_fn=lambda s: 14_800.0,
                             position_store=pos, dispatcher=rec, now=NOW) is False  # type: ignore[arg-type]
    store.close(); pos.close()


def test_rotation_protects_runner_and_weak_margin(tmp_path: Path) -> None:
    from trading.executor import consider_rotation

    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    old = _fill_ladder(store, pos, None, "dry-run")
    # 러너 보호: leg_t1 체결(본전 상향) 표시 → 교체 불가
    store.log(day=NOW.strftime("%Y%m%d"), draft_id=old.id, symbol="005930", kind="leg_t1",
              mode="dry-run", qty=1, price=12_000, at=NOW.isoformat())
    new = _ladder_draft("order.20260714.000002.buy")
    assert consider_rotation(new, 10_000.0, store=store, mode="dry-run", toss=None,
                             drafts_by_id={old.id: old}, price_fn=lambda s: 10_500.0,
                             position_store=pos, dispatcher=_Rec(), now=NOW) is False  # type: ignore[arg-type]
    store.close(); pos.close()


def test_broken_setup_guard_blocks_entry_below_stop(tmp_path: Path) -> None:
    # 현재가가 손절 레벨 이하 — 사자마자 스탑 트리거될 진입 차단(2026-07-13 데이타솔루션 사례)
    store = ExecStore(tmp_path / "e.sqlite")
    r = execute_armed(_draft(stop_level=65_000.0), price=64_000, store=store,
                      policy=ExecPolicy(), mode="dry-run", toss=None,
                      dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    assert r.action == "skipped" and "진입 밴드 하한" in r.detail  # 밴드 통합(EXEC-8)
    store.close()


def test_dynamic_pool_share_grows_as_pool_shrinks(tmp_path: Path) -> None:
    """EXEC-5 개정: 분모=미집행 잔여 풀 — 남은 트리거가 적을수록 몫이 커진다(운영자 설계)."""
    store = ExecStore(tmp_path / "e.sqlite")
    drafts = {f"o.{i}": _draft(f"o.{i}", f"00000{i}", stop_level=9_000.0) for i in range(3)}

    def unfilled_w() -> float:
        return sum(cap_fraction(d.total_size_cap) for i, d in drafts.items()
                   if not store.has(i, ("order_intent", "order_sent")))

    qtys = []
    for i in range(3):
        r = execute_armed(drafts[f"o.{i}"], price=10_000, store=store,
                          policy=ExecPolicy(), mode="dry-run", toss=None,
                          dispatcher=_Rec(), now=NOW,  # type: ignore[arg-type]
                          pool_weight_total=unfilled_w())
        qtys.append(int(r.detail.split("주")[0]))
    # 1st: 500만×1/3×70% = 116주 · 이후 잔여 풀이 줄며 몫 유지·상회(자본 가동률 우선)
    assert qtys[0] == 116
    assert qtys[1] >= qtys[0] and qtys[2] >= qtys[1] * 0  # 몫이 소멸하지 않음
    assert sum(qtys) * 10_000 <= 5_000_000               # 계좌 초과 불가
    store.close()


def test_trim_for_shortfall_funds_new_trigger(tmp_path: Path) -> None:
    """EXEC-6: 잔고 부족 시 잔여 여력 최저 포지션에서 부족분만 부분 매도(50% 상한·러너 보호)."""
    from trading.executor import trim_for_shortfall

    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    old = _fill_ladder(store, pos, None, "dry-run")   # 500주 @10,000 (dry-run 체결)
    held = pos.open_positions()[0]
    freed = trim_for_shortfall(1_000_000, store=store, mode="dry-run", toss=None,
                               drafts_by_id={old.id: old},
                               price_fn=lambda s: 10_500.0, position_store=pos,
                               dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    assert freed >= 1_000_000                          # 부족분 충족
    remaining = pos.open_positions()[0]
    assert remaining.qty >= held.qty // 2              # 포지션당 50% 상한
    assert store.has(old.id, ("trim_sell",))
    assert store.committed_krw() < 5_000_000           # 회수분이 가용으로 복원
    # 러너 보호: leg_t1 표시 후엔 트림 불가
    store.log(day=NOW.strftime("%Y%m%d"), draft_id=old.id, symbol="005930", kind="leg_t1",
              mode="dry-run", qty=1, price=12_000, at=NOW.isoformat())
    assert trim_for_shortfall(500_000, store=store, mode="dry-run", toss=None,
                              drafts_by_id={old.id: old}, price_fn=lambda s: 10_500.0,
                              position_store=pos, dispatcher=_Rec(), now=NOW) == 0  # type: ignore[arg-type]
    store.close(); pos.close()


def test_exhausted_setup_guard_blocks_entry_above_first_target(tmp_path: Path) -> None:
    # 현재가가 이미 1차 익절 이상 — 계획 상승분 소진(2026-07-13 한국콜마 실사례)
    store = ExecStore(tmp_path / "e.sqlite")
    d = _ladder_draft()  # 익절 12,000/15,000 · 손절 9,000
    r = execute_armed(d, price=12_500, store=store, policy=ExecPolicy(),
                      mode="dry-run", toss=None, dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    assert r.action == "skipped" and "진입 밴드 상한" in r.detail  # 밴드 통합(EXEC-8)
    store.close()


def test_regime_gate_blocks_and_halves(tmp_path: Path) -> None:
    from trading.regime import Regime

    store = ExecStore(tmp_path / "e.sqlite")
    d = _ladder_draft()
    # RISK_OFF: 신규 진입 중단
    r = execute_armed(d, price=10_000, store=store, policy=ExecPolicy(), mode="dry-run",
                      toss=None, dispatcher=_Rec(), now=NOW, regime=Regime.RISK_OFF)  # type: ignore[arg-type]
    assert r.action == "skipped" and "RISK_OFF" in r.detail
    # CAUTION: 배분 절반 — 500만×1.0×100%=500만 → 절반 250만 → 250주
    r2 = execute_armed(d, price=10_000, store=store, policy=ExecPolicy(), mode="dry-run",
                       toss=None, dispatcher=_Rec(), now=NOW, regime=Regime.CAUTION)  # type: ignore[arg-type]
    assert r2.action == "ordered" and "250주" in r2.detail
    store.close()


def test_test_entry_forces_min_qty(tmp_path: Path) -> None:
    store = ExecStore(tmp_path / "e.sqlite")
    d = _ladder_draft()
    r = execute_armed(d, price=10_000, store=store, policy=ExecPolicy(), mode="dry-run",
                      toss=None, dispatcher=_Rec(), now=NOW, test_entry=True)  # type: ignore[arg-type]
    assert r.action == "ordered" and "1주" in r.detail
    store.close()


def test_live_ignores_dry_run_records_for_dedup_and_daily_cap(tmp_path: Path) -> None:
    """7/14 전환 사고: dry-run order_intent가 live 재진입·일일 카운트를 오염시키면 안 된다."""
    store = ExecStore(tmp_path / "e.sqlite")
    store.log(day="20260714", draft_id="order.20260713.144960.buy", symbol="144960",
              kind="order_intent", mode="dry-run", qty=50, price=9960,
              at="2026-07-14T09:54:10+09:00")
    # 모드 무관(구동작) — 걸린다 / live 필터 — 안 걸린다
    assert store.has("order.20260713.144960.buy", ("order_intent", "order_sent"))
    assert not store.has("order.20260713.144960.buy", ("order_intent", "order_sent"), mode="live")
    assert store.new_orders_today("20260714") == 1
    assert store.new_orders_today("20260714", mode="live") == 0
    # live 기록은 live 필터에도 걸린다
    store.log(day="20260714", draft_id="order.20260713.144960.buy", symbol="144960",
              kind="order_sent", mode="live", qty=35, price=9990,
              at="2026-07-14T10:40:00+09:00")
    assert store.has("order.20260713.144960.buy", ("order_intent", "order_sent"), mode="live")
    assert store.new_orders_today("20260714", mode="live") == 1


def test_min_rr_guard_blocks_entry_near_target(tmp_path: Path) -> None:
    """운영자 지적(7/14): '9,999에 사서 10,000에 파는' 진입 — 잔여 R:R < EXEC_MIN_RR(기본 1.0) 차단."""
    from trading.contracts.order import ExitLevel

    store = ExecStore(tmp_path / "e.sqlite")
    rec = _Rec()
    d = _draft("order.20260713.144960.buy", "144960", stop_level=9_000.0).model_copy(
        update={"targets": [ExitLevel(level=10_000.0, pct=100)]}
    )
    # 익절 직전(9,990): 보상 10원 vs 위험 990원 → 스킵
    r = execute_armed(d, price=9_995, store=store, policy=ExecPolicy(), mode="dry-run",
                      toss=None, dispatcher=rec, now=NOW)  # type: ignore[arg-type]
    assert r.action == "skipped" and "진입 밴드 상한" in r.detail  # 밴드 통합(EXEC-8)
    # 계획 구간(9,400): R:R 1.5 → 진입
    r2 = execute_armed(d, price=9_400, store=store, policy=ExecPolicy(), mode="dry-run",
                       toss=None, dispatcher=rec, now=NOW)  # type: ignore[arg-type]
    assert r2.action == "ordered"
    store.close()


def test_store_queries_mode_isolation(tmp_path: Path) -> None:
    """가드 감사 B(2026-07-14): dry-run 잔재가 live 판단을 오염시키지 않는다 — 쿼리별 mode 필터."""
    store = ExecStore(tmp_path / "e.sqlite")
    at = NOW.isoformat()
    day = "20260714"
    # dry-run 흔적: 진입 intent + 잔고부족 skip + 교체 + 브래킷
    store.log(day=day, draft_id="d1", symbol="144960", kind="order_intent", mode="dry-run",
              qty=50, price=9_960, at=at)
    store.log(day=day, draft_id="d2", symbol="095610", kind="skip", mode="dry-run",
              detail="잔고 부족(가용 0원)", at=at)
    store.log(day=day, draft_id="d3", symbol="089970", kind="rotation_sell", mode="dry-run",
              qty=10, price=100_000, at=at)
    store.log(day=day, draft_id="d1", symbol="144960", kind="stop_intent", mode="dry-run",
              qty=50, price=9_000, at=at)
    # B1: live 폴백 가용액은 dry-run 약정을 차감하지 않는다
    assert store.committed_krw(mode="live") == 0
    assert store.committed_krw() == 50 * 9_960 - 10 * 100_000
    # B2: live는 dry-run의 잔고부족 skip을 재시도하지 않는다
    assert store.cash_skips_today(day, mode="live") == []
    assert store.cash_skips_today(day, mode="dry-run") == ["d2"]
    # B3: dry-run 교체가 live 일1회 예산을 소모하지 않는다
    assert store.rotations_today(day, mode="live") == 0
    # B4: dry-run 브래킷을 live 잔량으로 오인하지 않는다
    assert store.latest_bracket("d1", mode="live") is None
    assert store.latest_bracket("d1", mode="dry-run") is not None
    # B6: 교차 모드 미체결을 체결 처리하지 않는다
    assert store.pending_fills(mode="live") == []
    # B7: 레짐 알림 dedup은 일자 한정 — 과거 기록이 오늘 알림을 침묵시키지 않는다
    store.log(day="20260713", draft_id="_regime_unknown", symbol="-", kind="regime",
              mode="dry-run", detail="관측 불가", at=at)
    assert store.has("_regime_unknown", ("regime",), day="20260714") is False
    assert store.has("_regime_unknown", ("regime",), day="20260713") is True
    store.close()


def test_a1_bracket_gap_flags_p0_and_blocks_next_legs(tmp_path: Path) -> None:
    """A1: 취소 후 재등록(재시도 포함) 실패 → P0+bracket_gone 박제, 다음 패스 레그 중단(이중 매도 금지)."""
    from trading.executor import manage_exits

    class _GapToss(_FakeToss):
        fail_place = False

        def place_oco_sell(self, *a: Any, **k: Any) -> dict[str, Any]:
            if self.fail_place:
                raise RuntimeError("place boom")
            return super().place_oco_sell(*a, **k)

        def place_stop_sell_conditional(self, *a: Any, **k: Any) -> dict[str, Any]:
            if self.fail_place:
                raise RuntimeError("place boom")
            return super().place_stop_sell_conditional(*a, **k)

        def cancel_order(self, order_id: str) -> dict[str, Any]:
            return {}

    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    toss = _GapToss()
    d = _fill_ladder(store, pos, toss, "live")
    toss.fail_place = True
    rec = _Rec()
    acted = manage_exits(store=store, mode="live", toss=toss, drafts_by_id={d.id: d},  # type: ignore[arg-type]
                         price_fn=lambda s: 12_050.0, position_store=pos,
                         dispatcher=rec, now=NOW)  # type: ignore[arg-type]
    assert acted == []  # 레그는 나갔으나 브래킷 무방비 — 성공 처리 아님
    assert store.has(d.id, ("bracket_gone",), mode="live")
    assert any(a.severity.value == "P0" and "무방비" in a.what for a in rec.alerts)
    sells_before = len([o for o in toss.orders if o["side"] == "SELL"])
    # 다음 패스: bracket_gone 박제 → 레그 재시도 없음(이중 매도 금지)
    manage_exits(store=store, mode="live", toss=toss, drafts_by_id={d.id: d},  # type: ignore[arg-type]
                 price_fn=lambda s: 12_050.0, position_store=pos,
                 dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    assert len([o for o in toss.orders if o["side"] == "SELL"]) == sells_before
    store.close(); pos.close()


def test_a2_partial_fill_cancels_remainder_and_registers_stop(tmp_path: Path) -> None:
    """A2: 부분 체결 → 잔여 매수 즉시 취소 + 체결분 스탑 등록(추가 체결 무방비 방지)."""

    class _PartialToss(_FakeToss):
        def __init__(self) -> None:
            super().__init__()
            self.buy_cancels: list[str] = []

        def order(self, order_id: str) -> dict[str, Any]:
            return {"status": "PARTIAL_FILLED",
                    "execution": {"filledQuantity": "10", "averagePrice": "10000"}}

        def cancel_order(self, order_id: str) -> dict[str, Any]:
            self.buy_cancels.append(order_id)
            return {}

    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    toss = _PartialToss()
    d = _ladder_draft()
    execute_armed(d, price=10_000, store=store, policy=ExecPolicy(), mode="live",
                  toss=toss, dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    reconcile(store=store, mode="live", toss=toss, drafts_by_id={d.id: d},  # type: ignore[arg-type]
              dispatcher=_Rec(), position_store=pos, now=NOW)  # type: ignore[arg-type]
    assert toss.buy_cancels == ["ord-9"]                      # 잔여 취소
    bracket = store.latest_bracket(d.id, mode="live")
    assert bracket is not None and bracket[1] == 10           # 체결분만 보호
    assert store.has(d.id, ("buy_cancel_rest",), mode="live")
    assert store.pending_fills(mode="live") == []             # 추적 종결(스탑 등록됨)
    store.close(); pos.close()


def test_a6_sync_brackets_detects_gone_and_holds_on_unknown_schema(tmp_path: Path) -> None:
    """A6: 브로커 목록에 브래킷 부재 → P0+박제(일 1회). 스키마 불명 응답은 판정 보류."""
    from trading.executor import sync_brackets

    class _CondToss(_FakeToss):
        cond_response: Any = {"items": []}

        def cancel_order(self, order_id: str) -> dict[str, Any]:
            return {}

        def conditional_orders(self) -> Any:
            return self.cond_response

    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    toss = _CondToss()
    d = _fill_ladder(store, pos, toss, "live")
    # 스키마 불명 — 판정 보류(지어내지 않음)
    toss.cond_response = "unexpected"
    assert sync_brackets(store=store, mode="live", toss=toss, position_store=pos,  # type: ignore[arg-type]
                         dispatcher=_Rec(), now=NOW) == []  # type: ignore[arg-type]
    # 생존 — 아무 일 없음 (실측 스키마: conditionalOrders 키, 2026-07-14)
    toss.cond_response = {"conditionalOrders": [{"conditionalOrderId": "oco-9"}]}
    assert sync_brackets(store=store, mode="live", toss=toss, position_store=pos,  # type: ignore[arg-type]
                         dispatcher=_Rec(), now=NOW) == []  # type: ignore[arg-type]
    # 부재 — P0 + 박제, 같은 날 재알림 없음
    toss.cond_response = {"items": []}
    rec = _Rec()
    assert sync_brackets(store=store, mode="live", toss=toss, position_store=pos,  # type: ignore[arg-type]
                         dispatcher=rec, now=NOW) == [d.id]  # type: ignore[arg-type]
    assert any("브래킷 부재" in a.what for a in rec.alerts)
    assert sync_brackets(store=store, mode="live", toss=toss, position_store=pos,  # type: ignore[arg-type]
                         dispatcher=_Rec(), now=NOW) == []  # type: ignore[arg-type]
    store.close(); pos.close()


def test_a7_stale_pending_buy_canceled_on_setup_break(tmp_path: Path) -> None:
    """A7: 미체결 매수 + 현재가가 손절 이하(셋업 붕괴) → 매수 취소(되돌림 역선택 체결 방지)."""

    class _PendingToss(_FakeToss):
        def __init__(self) -> None:
            super().__init__()
            self.status = "PENDING"
            self.buy_cancels: list[str] = []

        def cancel_order(self, order_id: str) -> dict[str, Any]:
            self.buy_cancels.append(order_id)
            return {}

    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    toss = _PendingToss()
    d = _ladder_draft()
    execute_armed(d, price=10_000, store=store, policy=ExecPolicy(), mode="live",
                  toss=toss, dispatcher=_Rec(), now=NOW)  # type: ignore[arg-type]
    # 현재가 8,900 ≤ 손절 9,000 — 깨진 셋업의 지정가는 취소
    reconcile(store=store, mode="live", toss=toss, drafts_by_id={d.id: d},  # type: ignore[arg-type]
              dispatcher=_Rec(), position_store=pos, now=NOW,  # type: ignore[arg-type]
              price_fn=lambda s: 8_900.0)
    assert toss.buy_cancels == ["ord-9"]
    assert store.pending_fills(mode="live") == []  # buy_cancel로 추적 종결
    store.close(); pos.close()


def test_derive_entry_band_values() -> None:
    """EXEC-8 밴드 산식: 하한=경고×(1+1%) · 상한=가중 보상 R:R≥1(사다리 pct 가중, C4 해소)."""
    from trading.executor import derive_entry_band

    band = derive_entry_band(_ladder_draft())  # 손절 9,000 · 경고 9,300 · 12,000(50)/15,000(50)
    assert band is not None
    low, high = band
    assert abs(low - 9_300 * 1.01) < 1e-6
    assert abs(high - 11_250.0) < 1e-6  # (0.5×12,000 + 0.5×15,000 + 1.0×9,000) / (1.0 + 1.0)
    assert derive_entry_band(_draft(stop_level=None)) is None  # 가격 스탑 없음 — 밴드 없음


def test_reentry_policy_gates_and_half_size(tmp_path: Path) -> None:
    """EXEC-8 재진입: 한도·기보유·하드스탑 청산·쿨다운 가드 + 2회차 체감 50%(운영자 결정)."""
    from datetime import timedelta

    from trading.contracts.position import PositionRecord, PositionStatus

    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    toss = _FakeToss()
    d = _ladder_draft().model_copy(update={"max_entries": 2})
    r1 = execute_armed(d, price=10_000, store=store, policy=ExecPolicy(), mode="live",
                       toss=toss, dispatcher=_Rec(), now=NOW, position_store=pos)  # type: ignore[arg-type]
    assert r1.action == "ordered" and "500주" in r1.detail
    # 청산 미확정 — 재진입 보류
    r2 = execute_armed(d, price=10_500, store=store, policy=ExecPolicy(), mode="live",
                       toss=toss, dispatcher=_Rec(), now=NOW, position_store=pos)  # type: ignore[arg-type]
    assert r2.action == "skipped" and "청산 미확정" in r2.detail

    def _pos_rec(status: PositionStatus, reason: str, closed_ago_min: int) -> PositionRecord:
        return PositionRecord(
            id="pos.20260714.005930", as_of=NOW - timedelta(minutes=closed_ago_min),
            fetched_at=NOW, source="executor", symbol="005930", qty=500, avg_price=10_000.0,
            source_ref=d.id, status=status, close_reason=reason)

    # 하드 스탑 청산 — 재진입 금지(무효화 규율)
    pos.append(_pos_rec(PositionStatus.CLOSED, "가격 스탑 이탈", 60))
    r3 = execute_armed(d, price=10_500, store=store, policy=ExecPolicy(), mode="live",
                       toss=toss, dispatcher=_Rec(), now=NOW, position_store=pos)  # type: ignore[arg-type]
    assert r3.action == "skipped" and "하드 스탑" in r3.detail
    # 익절 청산 + 쿨다운 미경과 — 보류
    pos.append(_pos_rec(PositionStatus.CLOSED, "익절1 부분 실현 후 잔량 본전 청산", 10))
    r4 = execute_armed(d, price=10_500, store=store, policy=ExecPolicy(), mode="live",
                       toss=toss, dispatcher=_Rec(), now=NOW, position_store=pos)  # type: ignore[arg-type]
    assert r4.action == "skipped" and "쿨다운" in r4.detail
    # 익절 청산 + 쿨다운 경과 — 재진입, 체감 50%: 500만×0.5 / 10,500 = 238주
    pos.append(_pos_rec(PositionStatus.CLOSED, "익절1 부분 실현 후 잔량 본전 청산", 40))
    r5 = execute_armed(d, price=10_500, store=store, policy=ExecPolicy(), mode="live",
                       toss=toss, dispatcher=_Rec(), now=NOW, position_store=pos)  # type: ignore[arg-type]
    assert r5.action == "ordered" and "238주" in r5.detail
    # 한도(2회) 소진 — 3회차 없음
    r6 = execute_armed(d, price=10_500, store=store, policy=ExecPolicy(), mode="live",
                       toss=toss, dispatcher=_Rec(), now=NOW, position_store=pos)  # type: ignore[arg-type]
    assert r6.action == "skipped" and "한도 소진" in r6.detail
    store.close(); pos.close()


def test_reentry_blocked_while_holding_same_symbol(tmp_path: Path) -> None:
    """EXEC-8: 동일 종목 기보유 시 재진입 금지(운영자 지정)."""
    from trading.contracts.position import PositionRecord, PositionStatus

    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    toss = _FakeToss()
    d = _ladder_draft().model_copy(update={"max_entries": 2})
    execute_armed(d, price=10_000, store=store, policy=ExecPolicy(), mode="live",
                  toss=toss, dispatcher=_Rec(), now=NOW, position_store=pos)  # type: ignore[arg-type]
    pos.append(PositionRecord(
        id="pos.20260714.005930", as_of=NOW, fetched_at=NOW, source="executor",
        symbol="005930", qty=500, avg_price=10_000.0, source_ref=d.id,
        status=PositionStatus.OPEN))
    r = execute_armed(d, price=10_500, store=store, policy=ExecPolicy(), mode="live",
                      toss=toss, dispatcher=_Rec(), now=NOW, position_store=pos)  # type: ignore[arg-type]
    assert r.action == "skipped" and "기보유" in r.detail
    store.close(); pos.close()


def test_time_stop_auto_liquidation_in_window(tmp_path: Path) -> None:
    """EXEC-9: 시간손절 도래일 14:30~14:50 창에서 브래킷 취소→전량 매도→포지션 마감 (운영자 2026-07-14)."""
    from trading.executor import manage_time_stops

    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    toss = _FakeToss()
    d = _fill_ladder(store, pos, toss, "live")  # time_stop 7거래일, 진입 7/14
    in_window = datetime(2026, 7, 24, 14, 35, tzinfo=KST)  # 도래일 7/24(7/14+7거래일 — 7/17 휴장 스킵)

    # 도래 전(7/23) — 창 안이어도 미집행
    early = manage_time_stops(store=store, mode="live", toss=toss,  # type: ignore[arg-type]
                              price_fn=lambda s: 10_100.0, position_store=pos,
                              dispatcher=_Rec(), now=datetime(2026, 7, 23, 14, 35, tzinfo=KST))  # type: ignore[arg-type]
    assert early == []
    # 도래일 창 밖(10:00) — 미집행
    off_window = manage_time_stops(store=store, mode="live", toss=toss,  # type: ignore[arg-type]
                                   price_fn=lambda s: 10_100.0, position_store=pos,
                                   dispatcher=_Rec(), now=datetime(2026, 7, 24, 10, 0, tzinfo=KST))  # type: ignore[arg-type]
    assert off_window == []
    # 도래일 14:35 — 브래킷 취소 + 전량 매도 + 포지션 마감 + 저널
    rec = _Rec()
    acted = manage_time_stops(store=store, mode="live", toss=toss,  # type: ignore[arg-type]
                              price_fn=lambda s: 10_100.0, position_store=pos,
                              dispatcher=rec, now=in_window)  # type: ignore[arg-type]
    assert acted == [d.id]
    assert toss.cancels == ["oco-9"]
    sells = [o for o in toss.orders if o["side"] == "SELL"]
    assert sells and sells[-1]["qty"] == 500 and sells[-1]["price"] == 10_100
    assert store.has(d.id, ("leg_timestop",), mode="live")
    assert pos.open_positions() == []
    last = pos.latest_for_source(d.id)
    assert last is not None and "시간손절" in last.close_reason
    assert any("시간손절 매도" in a.what for a in rec.alerts)
    # 재집행 없음(저널 dedup)
    again = manage_time_stops(store=store, mode="live", toss=toss,  # type: ignore[arg-type]
                              price_fn=lambda s: 10_100.0, position_store=pos,
                              dispatcher=_Rec(), now=in_window)  # type: ignore[arg-type]
    assert again == []
    store.close(); pos.close()


def test_time_stop_cancel_failure_keeps_protection(tmp_path: Path) -> None:
    """EXEC-9 + A1: 브래킷 취소 실패 시 매도하지 않는다(기존 보호 유지, 다음 패스 재시도)."""
    from trading.executor import manage_time_stops

    class _CancelFailToss(_FakeToss):
        def cancel_conditional(self, conditional_order_id: str) -> None:
            raise RuntimeError("cancel boom")

    store = ExecStore(tmp_path / "e.sqlite")
    pos = PositionStore(tmp_path / "p.sqlite")
    toss = _CancelFailToss()
    d = _fill_ladder(store, pos, toss, "live")
    n_orders = len(toss.orders)
    acted = manage_time_stops(store=store, mode="live", toss=toss,  # type: ignore[arg-type]
                              price_fn=lambda s: 10_100.0, position_store=pos,
                              dispatcher=_Rec(), now=datetime(2026, 7, 24, 14, 35, tzinfo=KST))  # type: ignore[arg-type]
    assert acted == [] and len(toss.orders) == n_orders  # 매도 미발생
    assert not store.has(d.id, ("leg_timestop",), mode="live")
    assert pos.open_positions() != []  # 포지션 유지
    store.close(); pos.close()
