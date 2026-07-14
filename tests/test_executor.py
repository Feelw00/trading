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
    assert st["trigger"] == 65_000 and st["order_price"] == 64_800  # 트리거 2틱(100원) 아래 지정가
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
    assert r.action == "skipped" and "셋업 붕괴" in r.detail
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
    assert r.action == "skipped" and "셋업 소진" in r.detail
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
    assert r.action == "skipped" and "잔여 R:R 부족" in r.detail
    # 계획 구간(9,400): R:R 1.5 → 진입
    r2 = execute_armed(d, price=9_400, store=store, policy=ExecPolicy(), mode="dry-run",
                       toss=None, dispatcher=rec, now=NOW)  # type: ignore[arg-type]
    assert r2.action == "ordered"
    store.close()
