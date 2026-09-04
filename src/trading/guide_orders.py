"""가이드 매도 예약(EXEC-12, 운영자 결정 2026-09-02) — 실계좌 감시 + 조건주문 관리.

`python -m trading.guide_orders [--mode off|dry-run|live]` · cron 라운드 ``guide-orders``.

- **매수는 운영자 수동.** 이 모듈은 토스 실보유를 가이드(`trading.paper`)에 편입(실평단=시작가,
  운영자 지시 2026-09-02)하고, open 종목의
  **다음 매도선**에 실보유 기준 수량을 **조건주문(SINGLE·SELL·지정가)** 으로 건다.
- **재등록은 변경이 있을 때만**(운영자 2차 지시 2026-09-02): 살아 있는 우리 조건주문이 현재
  계획(매도선·수량·감시가)과 같고 등록 당시 보유 수량과 지금 보유가 같으면 **유지**. 추가
  매수·예약 체결·수동 매도로 수량이 바뀌었거나 주문이 소멸(체결·만료·외부 취소)했거나 만료
  ``RENEW_WITHIN_DAYS`` 이내면 기존 것을 취소하고 다시 건다.
- 수량 = int(실보유 × 사다리 비중) — 가이드 엔진(`paper.mark`)과 동일한 정수 내림.
  0이면 그 선은 건너뛰고 다음 선으로(1~2주 보유는 120%/150%선에서만 매도가 성립).
- 사다리 진행 = **우리 조건주문의 실체결**(COMPLETED, triggeredOrderId) 누적 — 페이퍼
  시뮬레이션(종가 교차)과 분리. 운영자 수동 매도는 진행으로 치지 않고 이벤트로 남긴다.
- 가격: 감시가 = 주문가 = 가이드 매도선을 **호가단위로 올림**(가이드선 아래로 팔지 않는다).
- **시작가(기준가)는 불변**(운영자 2026-09-02): 추가 매수로 평단이 낮아져도 가이드 시작가를
  옮기지 않는다 — 손실도 데이터. 이 모듈은 페이퍼 원장에 쓰지 않는다.
- 모드 ``GUIDE_ORDERS_MODE`` = off | dry-run(기본 — 조회·계획·저널만, 브로커 쓰기 없음) | live.
  킬 스위치 ``.runtime/exec/KILL`` 공유(EXEC-1). live 전환은 dry-run 5거래일 후 운영자가 .env.
- 저널 ``data/broker.sqlite`` append-only: 보유 스냅샷 · 조건주문 이벤트(intent/sent/keep/
  cancel/filled/expired/canceled/rejected/triggered_unfilled/skip) · 계좌 이벤트(신규 보유·수량
  증감·소멸·수동 매도). 행 UPDATE/DELETE 없음(스키마 컬럼 추가만 마이그레이션).
- 절대금지 #3: 지정가만 — 브로커 어댑터가 orderType=LIMIT을 하드코딩한다. 시장가 경로 없음.
- **이익 보호·3년 시한·심사 veto 청산은 여기서 자동화하지 않는다**(하락 시 매도 = 스탑 —
  운영자 결정: P0 알림 후 수동).
"""

import math
import os
import sqlite3
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from trading.collectors.base import CollectError, now_kst
from trading.paper import EnrollBlocked, PaperStore, PositionRow, enroll_holding

DEFAULT_DB = Path("data") / "broker.sqlite"
KILL_FILE = Path(".runtime") / "exec" / "KILL"   # EXEC-1 킬 스위치 공유
CLIENT_PREFIX = "guide-"
EXPIRE_DAYS = 7          # 조건주문 만료 — 스케줄이 죽어도 7일 뒤 자동 소멸
RENEW_WITHIN_DAYS = 2    # 만료가 이 안이면 변경 없어도 갱신(취소 후 재등록)
MODES = ("off", "dry-run", "live")

# KRX 호가가격단위(원) — 2023-01 개정 공개 규정(executor.tick_size와 동일 표, 동결 모듈 미import).
_TICKS: tuple[tuple[int, int], ...] = (
    (2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500),
)


def tick_size(price: float) -> int:
    for bound, tick in _TICKS:
        if price < bound:
            return tick
    return 1_000


def round_up_to_tick(price: float) -> int:
    """가이드선을 호가단위로 **올림** — 가이드선 아래로 파는 주문을 만들지 않는다."""
    t = tick_size(price)
    return int(math.ceil(price / t - 1e-9)) * t


def guide_orders_mode(*, kill_file: Path = KILL_FILE) -> str:
    if kill_file.exists():
        return "off"
    mode = os.environ.get("GUIDE_ORDERS_MODE", "dry-run").strip().lower()
    return mode if mode in MODES else "dry-run"


# --- 브로커 인터페이스(주입) ------------------------------------------------------------------


class BrokerClient(Protocol):
    def holdings(self) -> dict[str, Any]: ...
    def conditional_orders(self, status: str = "OPEN") -> Any: ...
    def conditional_order(self, conditional_order_id: str) -> dict[str, Any]: ...
    def cancel_conditional(self, conditional_order_id: str) -> None: ...
    def place_sell_conditional(
        self, symbol: str, quantity: int, *, trigger_price: int, order_price: int,
        expire_date: str, client_order_id: str,
    ) -> dict[str, Any]: ...
    def order(self, order_id: str) -> dict[str, Any]: ...


# --- 계획(순수 함수) ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Holding:
    symbol: str
    name: str
    quantity: int
    avg_price: float | None
    last_price: float | None


@dataclass(frozen=True)
class Leg:
    index: int          # 사다리 인덱스(0=첫 매도선 … 마지막=정리)
    label: str
    line: float         # 가이드 매도선(원가)
    trigger_price: int  # 호가단위 올림
    order_price: int
    quantity: int


def ladder_of(pos: PositionRow) -> list[tuple[str, float, float]]:
    """(라벨, 가이드선, 보유 대비 비중) — `paper.mark`의 sell_ladder + 정리(비중 1.0)."""
    p = pos.params
    legs = [
        (f"목표가 {int(mult * 100)}% 매도", pos.target_price * mult, por)
        for mult, por in p.sell_levels
    ]
    legs.append((
        f"정리(목표가 {int(round(p.final_exit_multiple * 100))}%)",
        pos.target_price * p.final_exit_multiple, 1.0,
    ))
    return legs


def plan_next_leg(
    quantity: int, ladder: Sequence[tuple[str, float, float]], done_legs: int
) -> Leg | None:
    """다음 매도선 1개 — 수량 0인 선은 건너뛴다(가이드 엔진과 같은 내림). 없으면 None."""
    if quantity <= 0:
        return None
    for i in range(max(0, done_legs), len(ladder)):
        label, line, portion = ladder[i]
        q = quantity if portion >= 1.0 else int(quantity * portion)
        if q >= 1:
            px = round_up_to_tick(line)
            return Leg(i, label, line, px, px, q)
    return None


def parse_holdings(raw: dict[str, Any]) -> list[Holding]:
    """토스 HoldingsOverview.items → Holding. 비수치는 None/0(추측 금지)."""
    out: list[Holding] = []
    items = raw.get("items") if isinstance(raw, dict) else None
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or not it.get("symbol"):
            continue
        out.append(Holding(
            symbol=str(it["symbol"]), name=str(it.get("name") or ""),
            quantity=_int(it.get("quantity")), avg_price=_float(it.get("averagePurchasePrice")),
            last_price=_float(it.get("lastPrice")),
        ))
    return out


def _int(v: Any) -> int:
    try:
        return int(float(str(v)))
    except (TypeError, ValueError):
        return 0


def _float(v: Any) -> float | None:
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return None


def short_label(label: str | None) -> str:
    """'목표가 80% 매도' → '80%', '정리(목표가 150%)' → '정리 150%' — 표시용."""
    if not label:
        return "—"
    s = label.replace("목표가 ", "").replace(" 매도", "")
    return s.replace("정리(", "정리 ").replace(")", "")


# --- 저널(append-only) -------------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS holdings_snapshots (
  row_id INTEGER PRIMARY KEY AUTOINCREMENT,
  as_of TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT, quantity INTEGER NOT NULL,
  avg_price REAL, last_price REAL
);
CREATE TABLE IF NOT EXISTS guide_orders (
  row_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, event TEXT NOT NULL, symbol TEXT NOT NULL, cycle INTEGER NOT NULL,
  cond_id TEXT, client_order_id TEXT, leg_index INTEGER, leg_label TEXT,
  trigger_price INTEGER, order_price INTEGER, quantity INTEGER, expire_date TEXT,
  mode TEXT NOT NULL, note TEXT, holding_qty INTEGER
);
CREATE TABLE IF NOT EXISTS events (
  row_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, kind TEXT NOT NULL, symbol TEXT, detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_hs_asof ON holdings_snapshots(as_of);
CREATE INDEX IF NOT EXISTS idx_go_cond ON guide_orders(cond_id);
CREATE INDEX IF NOT EXISTS idx_go_sym ON guide_orders(symbol, cycle);
"""

_CLOSING_EVENTS = ("cancel", "filled", "expired", "canceled", "triggered_unfilled", "lost")


@dataclass(frozen=True)
class OpenOrder:
    """우리가 보낸 조건주문(저널 기준 미종결)."""

    cond_id: str
    symbol: str
    cycle: int
    leg_index: int
    quantity: int
    trigger_price: int
    holding_qty: int
    expire_date: str


@dataclass(frozen=True)
class PlanRow:
    """종목별 마지막 계획 행(intent/sent/keep/skip) — 유지 판정·표시용."""

    event: str
    leg_index: int | None
    leg_label: str | None
    trigger_price: int | None
    quantity: int | None
    holding_qty: int | None
    expire_date: str | None
    cond_id: str | None
    mode: str
    ts: str


class BrokerStore:
    def __init__(self, db_path: Path | None = None) -> None:
        path = db_path if db_path is not None else DEFAULT_DB
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(DDL)
        # 컬럼 추가 마이그레이션(행 무변경) — 2026-09-02 holding_qty 도입
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(guide_orders)")}
        if "holding_qty" not in cols:
            self._conn.execute("ALTER TABLE guide_orders ADD COLUMN holding_qty INTEGER")
            self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # 보유 스냅샷
    def snapshot(self, as_of: datetime, holdings: Sequence[Holding]) -> None:
        ts = as_of.isoformat()
        self._conn.executemany(
            "INSERT INTO holdings_snapshots (as_of, symbol, name, quantity, avg_price, last_price)"
            " VALUES (?,?,?,?,?,?)",
            [(ts, h.symbol, h.name, h.quantity, h.avg_price, h.last_price) for h in holdings],
        )
        if not holdings:  # 빈 계좌도 사실 — 스냅샷 시각만 남긴다
            self._conn.execute(
                "INSERT INTO holdings_snapshots (as_of, symbol, name, quantity) VALUES (?,?,?,?)",
                (ts, "", "(empty)", 0),
            )
        self._conn.commit()

    def previous_snapshot(self) -> dict[str, int]:
        """직전 스냅샷의 {symbol: qty} (빈 계좌 마커 제외). 스냅샷 없으면 {}."""
        row = self._conn.execute("SELECT MAX(as_of) FROM holdings_snapshots").fetchone()
        if row is None or row[0] is None:
            return {}
        rows = self._conn.execute(
            "SELECT symbol, quantity FROM holdings_snapshots WHERE as_of = ? AND symbol != ''",
            (row[0],),
        ).fetchall()
        return {str(s): int(q) for s, q in rows}

    def latest_holdings(self) -> dict[str, Holding]:
        """최신 스냅샷 전체(웹 표시용)."""
        row = self._conn.execute("SELECT MAX(as_of) FROM holdings_snapshots").fetchone()
        if row is None or row[0] is None:
            return {}
        rows = self._conn.execute(
            "SELECT symbol, name, quantity, avg_price, last_price FROM holdings_snapshots "
            "WHERE as_of = ? AND symbol != ''",
            (row[0],),
        ).fetchall()
        return {str(s): Holding(str(s), str(n or ""), int(q), a, lp) for s, n, q, a, lp in rows}

    # 조건주문 이벤트
    def append_order(
        self, *, ts: datetime, event: str, symbol: str, cycle: int, mode: str,
        cond_id: str | None = None, client_order_id: str | None = None,
        leg: Leg | None = None, expire_date: str | None = None, note: str = "",
        holding_qty: int | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO guide_orders (ts, event, symbol, cycle, cond_id, client_order_id, "
            "leg_index, leg_label, trigger_price, order_price, quantity, expire_date, mode, note, "
            "holding_qty) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                ts.isoformat(), event, symbol, cycle, cond_id, client_order_id,
                leg.index if leg else None, leg.label if leg else None,
                leg.trigger_price if leg else None, leg.order_price if leg else None,
                leg.quantity if leg else None, expire_date, mode, note, holding_qty,
            ),
        )
        self._conn.commit()

    def open_orders(self) -> list[OpenOrder]:
        """우리가 보낸(sent) 조건주문 중 종결 이벤트가 없는 것.

        실사고(2026-09-03): live 주문이 살아 있는 상태에서 `--mode dry-run`을 돌리면 취소
        **의도**가 `cancel`(mode=dry-run)로 저널에 남는데, 이를 종결로 치면 다음 live가 기존
        주문을 못 보고 새로 등록해 **중복 조건주문**이 생긴다. `cancel`은 실제로 브로커에 보낸
        live 행만 종결로 인정한다. 브로커 상태 대사 이벤트(filled/expired/canceled/…)는 관측
        사실이라 모드 무관.
        """
        marks = ",".join("?" for _ in _CLOSING_EVENTS)
        rows = self._conn.execute(
            "SELECT cond_id, symbol, cycle, leg_index, quantity, trigger_price, holding_qty, "
            "expire_date FROM guide_orders WHERE event = 'sent' AND cond_id IS NOT NULL "
            f"AND cond_id NOT IN (SELECT cond_id FROM guide_orders WHERE cond_id IS NOT NULL "
            f"AND event IN ({marks}) AND NOT (event = 'cancel' AND mode <> 'live')) "
            "ORDER BY row_id",
            _CLOSING_EVENTS,
        ).fetchall()
        return [
            OpenOrder(str(c), str(s), int(cy), int(li), int(q), int(tp or 0), int(hq or 0), str(ex or ""))
            for c, s, cy, li, q, tp, hq, ex in rows
        ]

    def done_legs(self, symbol: str, cycle: int) -> int:
        """이 사이클에서 실체결된 마지막 사다리 인덱스 + 1 (없으면 0)."""
        row = self._conn.execute(
            "SELECT MAX(leg_index) FROM guide_orders WHERE symbol = ? AND cycle = ? "
            "AND event = 'filled'",
            (symbol, cycle),
        ).fetchone()
        return 0 if row is None or row[0] is None else int(row[0]) + 1

    def last_plan(self, symbol: str) -> PlanRow | None:
        """종목의 마지막 계획 행(intent/sent/keep/skip)."""
        row = self._conn.execute(
            "SELECT event, leg_index, leg_label, trigger_price, quantity, holding_qty, "
            "expire_date, cond_id, mode, ts FROM guide_orders WHERE symbol = ? "
            "AND event IN ('intent', 'sent', 'keep', 'skip', 'rejected') "
            "ORDER BY row_id DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if row is None:
            return None
        ev, li, ll, tp, q, hq, ex, cid, mode, ts = row
        return PlanRow(
            str(ev), None if li is None else int(li), ll, None if tp is None else int(tp),
            None if q is None else int(q), None if hq is None else int(hq), ex, cid, str(mode), str(ts),
        )

    def latest_plans(self) -> dict[str, PlanRow]:
        syms = [str(r[0]) for r in self._conn.execute("SELECT DISTINCT symbol FROM guide_orders")]
        out: dict[str, PlanRow] = {}
        for s in syms:
            p = self.last_plan(s)
            if p is not None:
                out[s] = p
        return out

    def append_event(self, ts: datetime, kind: str, symbol: str | None, detail: str) -> None:
        self._conn.execute(
            "INSERT INTO events (ts, kind, symbol, detail) VALUES (?,?,?,?)",
            (ts.isoformat(), kind, symbol, detail),
        )
        self._conn.commit()


def account_view(db_path: Path | None = None) -> tuple[dict[str, Holding], dict[str, PlanRow]]:
    """웹/CLI 표시용 — 저널이 없으면 빈 값(파일을 만들지 않는다)."""
    path = db_path if db_path is not None else DEFAULT_DB
    if not path.exists():
        return {}, {}
    store = BrokerStore(path)
    try:
        return store.latest_holdings(), store.latest_plans()
    finally:
        store.close()


# --- 실행 ------------------------------------------------------------------------------------


@dataclass
class RunSummary:
    mode: str
    holdings: int = 0
    guided: int = 0
    placed: int = 0
    kept: int = 0
    skipped: int = 0
    canceled: int = 0
    filled: int = 0
    anomalies: list[str] | None = None
    lines: list[str] | None = None


def _cond_list(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        items = raw.get("conditionalOrders")
        return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []
    return [x for x in raw if isinstance(x, dict)] if isinstance(raw, list) else []


def _days_left(expire_date: str | None, today: date) -> int:
    try:
        return (date.fromisoformat(str(expire_date)) - today).days
    except ValueError:
        return -1


def _same_plan(
    prior: PlanRow | OpenOrder, leg: Leg, holding_qty: int, today: date
) -> bool:
    """유지 조건: 매도선·수량·감시가 동일 + 등록 당시 보유 = 현재 보유 + 만료 여유."""
    same = (
        prior.leg_index == leg.index and prior.quantity == leg.quantity
        and prior.trigger_price == leg.trigger_price and prior.holding_qty == holding_qty
    )
    return same and _days_left(prior.expire_date, today) > RENEW_WITHIN_DAYS


def run(
    client: BrokerClient,
    *,
    mode: str,
    store: BrokerStore,
    paper: PaperStore,
    now: datetime | None = None,
    alert: Any | None = None,
    enroll: Callable[[str, str, float | None], str | EnrollBlocked | None] | None = None,
) -> RunSummary:
    """1회 실행: 보유 스냅샷·이상 감지 → 우리 조건주문 정산 → 변경 시에만 취소·재등록.

    ``alert``: P1 적재 콜백(what) — 라운드 보고 꼬리에 실린다. None이면 print만.
    ``enroll``: 가이드 밖 실보유 편입 콜백(symbol, name, avg_price) → 편입 줄 · None(결측 불가) ·
    ``EnrollBlocked``(정책 보류 — GUIDE-1 ③ 심사 승인 없음: 매도 예약 없이 ⚠ 표기, 신규면 P1).
    운영자 지시(2026-09-02): 페이퍼는 실투자·명시 이동만 — 실보유가 곧 편입 사유다.
    """
    ts = now or now_kst()
    today = ts.date()
    s = RunSummary(mode=mode, anomalies=[], lines=[])
    assert s.anomalies is not None and s.lines is not None
    if mode == "off":
        s.lines.append("가이드 매도 예약 [off] — 킬 스위치/모드 off, 브로커 미접촉")
        return s

    def _p1(what: str) -> None:
        s.anomalies.append(what)  # type: ignore[union-attr]
        if alert is not None:
            alert(what)

    # 1. 보유 스냅샷 + 직전 대비 변동
    holdings = parse_holdings(client.holdings())
    prev = store.previous_snapshot()
    store.snapshot(ts, holdings)
    s.holdings = len(holdings)
    held = {h.symbol: h for h in holdings}
    for sym, h in held.items():
        if sym not in prev:
            store.append_event(ts, "new_holding", sym, f"{h.name} {h.quantity}주")
        elif h.quantity > prev[sym]:
            store.append_event(ts, "qty_up", sym, f"{prev[sym]}→{h.quantity}주")
    for sym, q in prev.items():
        if sym not in held:
            store.append_event(ts, "holding_gone", sym, f"{q}주 → 0")

    # 2. 우리 조건주문 정산 — 브로커에 없는 것은 상태 조회(체결/만료/외부 취소/발동 미체결).
    #    살아 있는 것은 4단계에서 유지/교체를 정한다(매번 취소하지 않는다 — 운영자 2차 지시).
    broker_open = {
        str(o.get("conditionalOrderId")): o for o in _cond_list(client.conditional_orders("OPEN"))
    }
    ours = store.open_orders()
    our_ids = {o.cond_id for o in ours}
    alive: dict[str, list[OpenOrder]] = {}
    filled_syms: set[str] = set()
    for o in ours:
        if o.cond_id in broker_open:
            alive.setdefault(o.symbol, []).append(o)
            continue
        try:
            detail = client.conditional_order(o.cond_id)
        except CollectError as exc:
            # 실측(2026-09-03): 운영자가 앱에서 취소한 조건주문은 상세 조회가 404 — 예외로 라운드 전체가
            # 죽으면 나머지 종목 등록까지 막힌다. 외부 종료로 저널링(종결 이벤트)하고 계속 간다.
            store.append_order(
                ts=ts, event="canceled", symbol=o.symbol, cycle=o.cycle, mode=mode, cond_id=o.cond_id,
                note=f"상세 조회 실패 — 외부 취소(앱) 추정: {exc!r}"[:200],
            )
            _p1(f"가이드 조건주문이 외부에서 종료됨(조회 실패): {o.symbol} leg{o.leg_index}")
            continue
        status = str(detail.get("status") or "")
        first = detail.get("first") if isinstance(detail.get("first"), dict) else {}
        trig_id = first.get("triggeredOrderId") if isinstance(first, dict) else None
        if status == "COMPLETED":
            fq = o.quantity
            if trig_id:
                od = client.order(str(trig_id))
                ex = od.get("execution") if isinstance(od, dict) else None
                if isinstance(ex, dict) and ex.get("filledQuantity") is not None:
                    fq = _int(ex.get("filledQuantity"))
            store.append_order(
                ts=ts, event="filled", symbol=o.symbol, cycle=o.cycle, mode=mode, cond_id=o.cond_id,
                note=f"triggeredOrderId={trig_id} filled={fq}", leg=Leg(o.leg_index, "", 0.0, 0, 0, fq),
            )
            store.append_event(ts, "filled", o.symbol, f"leg{o.leg_index} {fq}주")
            filled_syms.add(o.symbol)
            s.filled += 1
        elif status in ("ORDERED", "ORDERING"):
            store.append_order(
                ts=ts, event="triggered_unfilled", symbol=o.symbol, cycle=o.cycle, mode=mode,
                cond_id=o.cond_id, note=f"status={status} triggeredOrderId={trig_id}",
            )
            _p1(f"가이드 매도 발동 후 미체결: {o.symbol} leg{o.leg_index} — 재등록됨(status={status})")
        elif status == "EXPIRED":
            store.append_order(ts=ts, event="expired", symbol=o.symbol, cycle=o.cycle, mode=mode, cond_id=o.cond_id)
        elif status:
            store.append_order(
                ts=ts, event="canceled", symbol=o.symbol, cycle=o.cycle, mode=mode, cond_id=o.cond_id,
                note=f"status={status}",
            )
            _p1(f"가이드 조건주문이 외부에서 종료됨: {o.symbol} leg{o.leg_index} status={status}")
        else:
            store.append_order(
                ts=ts, event="lost", symbol=o.symbol, cycle=o.cycle, mode=mode, cond_id=o.cond_id,
                note="상세 조회 실패/빈 응답",
            )
            _p1(f"가이드 조건주문 상태 불명: {o.symbol} {o.cond_id}")
    foreign = [o for cid, o in broker_open.items() if cid not in our_ids]
    if foreign:
        syms = ", ".join(sorted({str(o.get("symbol")) for o in foreign}))
        s.lines.append(f"외부 조건주문 {len(foreign)}건(미접촉): {syms}")

    # 3. 수동 매도 감지 — 수량 감소인데 우리 체결이 없는 종목
    for sym, q in prev.items():
        cur = held.get(sym)
        if cur is not None and cur.quantity < q and sym not in filled_syms:
            store.append_event(ts, "manual_sell", sym, f"{q}→{cur.quantity}주 (가이드 체결 없음)")
            _p1(f"수동 매도 감지: {sym} {q}→{cur.quantity}주 — 사다리 진행에 미반영")

    # 4. 가이드 open 종목별 — 계획 산출 → 유지 / (취소 후) 등록
    positions = {p.symbol: p for p in paper.latest_positions() if p.status == "open"}
    # 4a. 실보유 자동 편입 — 가이드 밖 실보유는 실평단을 시작가로 편입해 같은 실행에서 매도
    #     예약까지 잇는다. 편입 불가(평단·밸류에이션·시세 결측)는 신규 보유일 때만 P1.
    for sym, h in sorted(held.items()):
        if sym in positions or h.quantity <= 0:
            continue
        outcome = enroll(sym, h.name, h.avg_price) if enroll is not None else None
        if isinstance(outcome, EnrollBlocked):
            # GUIDE-1 ③(운영자 결정 2026-09-03): 심사 승인 없는 실보유는 편입 보류 — 목표가·매도
            # 사다리·조건주문 없음(심사 우회 방지). 승인되면 다음 실행에서 자동 편입.
            if sym not in prev:
                store.append_event(ts, "enroll_blocked", sym, outcome.reason)
                _p1(f"심사 승인 없는 실보유: {sym} {h.name} {h.quantity}주 — 편입 보류"
                    f"({outcome.reason}) · 매도 예약 없음 — 심사 승인 시 자동 편입(GUIDE-1 ③)")
            s.lines.append(f"⚠ {sym} {h.name} {h.quantity}주 — 가이드 밖(편입 보류: {outcome.reason} · 매도 예약 없음)")
            continue
        line = outcome
        if line:
            store.append_event(ts, "enrolled", sym, line)
            s.lines.append(line)
            continue
        if sym not in prev:
            _p1(f"가이드 밖 보유 종목: {sym} {h.name} {h.quantity}주 — 편입 불가"
                "(평단·밸류에이션·시세 결측) 확인")
        s.lines.append(f"{sym} {h.name} {h.quantity}주 — 가이드 밖(미등록)")
    if enroll is not None:
        positions = {p.symbol: p for p in paper.latest_positions() if p.status == "open"}
    expire = (ts + timedelta(days=EXPIRE_DAYS)).strftime("%Y-%m-%d")

    def _cancel_alive(sym: str, cycle: int, why: str) -> None:
        for o in alive.get(sym, []):
            if mode == "live":
                client.cancel_conditional(o.cond_id)
            store.append_order(
                ts=ts, event="cancel", symbol=sym, cycle=cycle, mode=mode, cond_id=o.cond_id, note=why,
            )
            s.canceled += 1

    for sym in sorted(positions):
        pos = positions[sym]
        hold = held.get(sym)
        if hold is None or hold.quantity <= 0:
            if alive.get(sym):
                _cancel_alive(sym, pos.cycle, "보유 0")
            s.lines.append(f"{sym} 보유 0 — 등록 없음")
            continue
        s.guided += 1
        done = store.done_legs(sym, pos.cycle)
        leg = plan_next_leg(hold.quantity, ladder_of(pos), done)
        if leg is None:
            _cancel_alive(sym, pos.cycle, "남은 매도선 없음")
            store.append_order(ts=ts, event="skip", symbol=sym, cycle=pos.cycle, mode=mode, note="사다리 소진/수량 0", holding_qty=hold.quantity)
            s.skipped += 1
            s.lines.append(f"{sym} {hold.name} {hold.quantity}주 — 남은 매도선 없음(건너뜀)")
            continue
        desc = f"{short_label(leg.label)} {leg.quantity}주 @{leg.trigger_price:,}"

        # 유지 판정 — live: 살아 있는 우리 주문 1건이 계획과 동일 · dry-run: 마지막 intent/keep 동일
        prior: OpenOrder | PlanRow | None
        if mode == "live":
            live_alive = alive.get(sym, [])
            prior = live_alive[0] if len(live_alive) == 1 else None
        else:
            lp = store.last_plan(sym)
            prior = lp if lp is not None and lp.event in ("intent", "keep") and lp.mode != "live" else None
        if prior is not None and _same_plan(prior, leg, hold.quantity, today):
            ex_date = prior.expire_date
            cid = prior.cond_id
            store.append_order(
                ts=ts, event="keep", symbol=sym, cycle=pos.cycle, mode=mode, cond_id=cid, leg=leg,
                expire_date=ex_date, holding_qty=hold.quantity, note="변경 없음",
            )
            s.kept += 1
            s.lines.append(f"= {sym} {hold.name} {hold.quantity}주 — 유지({desc}, 만료 {ex_date})")
            continue

        why = "변경" if (alive.get(sym) or (mode != "live" and prior is not None)) else "신규"
        if alive.get(sym):
            _cancel_alive(sym, pos.cycle, f"재등록({why}: 보유 {hold.quantity}주 / {desc})")
        coid = f"{CLIENT_PREFIX}{sym}-{leg.index}-{pos.cycle}-{ts:%Y%m%d}"[:36]
        tag = f"{sym} {hold.name} {hold.quantity}주 → {desc} (만료 {expire})"
        if mode == "live":
            try:
                res = client.place_sell_conditional(
                    sym, leg.quantity, trigger_price=leg.trigger_price, order_price=leg.order_price,
                    expire_date=expire, client_order_id=coid,
                )
            except Exception as exc:  # noqa: BLE001 — 거부는 저널+P1, 다음 종목 계속
                store.append_order(
                    ts=ts, event="rejected", symbol=sym, cycle=pos.cycle, mode=mode, client_order_id=coid,
                    leg=leg, expire_date=expire, note=repr(exc)[:200], holding_qty=hold.quantity,
                )
                _p1(f"가이드 매도 등록 거부: {tag} — {exc!r}"[:300])
                s.lines.append(f"❌ {tag} — 거부")
                continue
            cid_new = str(res.get("conditionalOrderId") or "") if isinstance(res, dict) else ""
            store.append_order(
                ts=ts, event="sent", symbol=sym, cycle=pos.cycle, mode=mode, cond_id=cid_new or None,
                client_order_id=coid, leg=leg, expire_date=expire, holding_qty=hold.quantity,
            )
            if not cid_new:
                _p1(f"가이드 매도 등록 응답에 conditionalOrderId 없음: {tag}")
            s.placed += 1
            s.lines.append(f"✅ {tag} [{why}]")
        else:
            store.append_order(
                ts=ts, event="intent", symbol=sym, cycle=pos.cycle, mode=mode, client_order_id=coid,
                leg=leg, expire_date=expire, holding_qty=hold.quantity,
            )
            s.placed += 1
            s.lines.append(f"[dry-run] {tag} [{why}]")
    # 4. 가이드가 닫힌(open 포지션 없음) 종목의 잔존 조건주문 — 고아 취소.
    # 실사고(2026-09-03): 운영자가 실정리 후 `paper close`하면 위 루프(open 포지션)에서 빠져
    # 살아 있는 매도 예약이 만료(최대 7일)까지 남고, 보유 없는 매도가 발동하면 거부만 쌓인다.
    for sym, orders in sorted(alive.items()):
        if sym in positions or not orders:
            continue
        _cancel_alive(sym, orders[0].cycle, "가이드 종료(open 포지션 없음) — 잔존 주문 취소")
        s.lines.append(f"{sym} 가이드 종료 — 잔존 조건주문 {len(orders)}건 취소")
    s.lines.insert(0, (
        f"가이드 매도 예약 [{mode}] 보유 {s.holdings}종목 · 가이드 {s.guided} · 등록 {s.placed} · "
        f"유지 {s.kept} · 건너뜀 {s.skipped} · 취소 {s.canceled} · 체결 정산 {s.filled}"
    ))
    return s


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    mode = guide_orders_mode()
    if "--mode" in args:
        m = args[args.index("--mode") + 1]
        if m not in MODES:
            print(f"--mode는 {'|'.join(MODES)}", file=sys.stderr)
            return 2
        mode = "off" if KILL_FILE.exists() else m
    from trading.collectors.toss import client_from_env

    client = client_from_env()
    if client is None:
        print("TOSS 키 미설정 — 가이드 매도 예약 불가(blocked)")
        return 1
    alert_fn: Any = None
    d: Any = None
    try:
        from trading.alerts import Alert, AlertDispatcher, Severity

        d = AlertDispatcher()

        def _alert(what: str) -> None:
            d.notify(Alert(
                severity=Severity.P1, what=what,
                rule="EXEC-12 가이드 매도 예약(운영자 결정 2026-09-02)",
                action="계좌·가이드 원장 대조 후 필요 시 수동 조치(python -m trading.guide_orders)",
                deadline="다음 슬롯 전",
            ))
        alert_fn = _alert
    except Exception:  # noqa: BLE001 — 알림 경로 실패가 예약 등록을 막지 않는다
        d = None
    store, paper = BrokerStore(), PaperStore()
    try:
        summary = run(
            client, mode=mode, store=store, paper=paper, alert=alert_fn,
            enroll=lambda sym, _name, avg: enroll_holding(paper, sym, avg),
        )
    finally:
        store.close()
        paper.close()
        if d is not None:
            d.store.close()
    for line in summary.lines or []:
        print(line)
    return 0


__all__ = [
    "BrokerClient", "BrokerStore", "Holding", "Leg", "PlanRow", "RunSummary", "account_view",
    "guide_orders_mode", "ladder_of", "parse_holdings", "plan_next_leg", "round_up_to_tick",
    "run", "short_label", "tick_size",
]


if __name__ == "__main__":
    raise SystemExit(main())
