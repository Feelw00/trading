"""자동 집행기 (EXEC-1, 운영자 결정 2026-07-13) — armed 초안 → 지정가 매수 → 체결 시 조건부 손절.

**운영자는 보고만 받는다**: arm-watch가 발동을 감지하면 이 모듈이 주문까지 잇는다.
판단은 없다 — R5가 만든 초안(승인풀)의 발동 사실과 하드캡만 기계 적용(절대금지 #2).

사이징 (EXEC-1 개정 — 운영자 2차 결정 2026-07-13: 투자 정책 캡 제거):
- **계좌가 상한이다**: 종목당·총액 하드캡 없음. 계좌 고정 예치금(``EXEC_ACCOUNT_KRW``,
  기본 500만원)이 normal_unit 기준액이고, live는 실 매수가능금액(``buying_power_krw``)과
  min. 포지션 예산 = 가용액 × R5 초안의 total_size_cap 계수("0.5 * normal_unit"의 0.5)
  × 즉시 트랜치 비중. **분석(R5)이 사이즈를 정한다.**
- **최소 1주 보장**: 계산 수량이 0이라도 1주 가격이 가용액 이내면 1주 매수 —
  "고가 종목이 제한 때문에 못 사지는 상황 방지"(운영자).
- **계단식(같은 종목 재진입) 허용**: 초안 단위 1회 dedup만. 새 초안이 같은 종목이면
  분석이 통과시킨 피라미딩으로 본다(운영자).

오작동 방어 (투자 정책이 아닌 폭주 가드 — 유지):
- **일일 신규 상한**(기본 5건, ``EXEC_MAX_NEW_PER_DAY``) — 루프 버그·초안 폭주 방어.
- **모드**: ``EXEC_MODE`` = off | dry-run(기본) | live. dry-run은 주문 직전까지 전 과정을
  수행하되 전송하지 않는다(체결은 발동가로 가정, 잔고는 기준액−기집행 보수 근사).
  **live 전환은 dry-run 5거래일 검증 후 운영자가 .env를 직접 바꾼다**(EXEC-1).
- **킬 스위치**: ``.runtime/exec/KILL`` 파일이 존재하면 모드 무관 전량 정지.
- **주문은 지정가만**: TossClient에 시장가 경로 자체가 없다(절대금지 #3).
- **스탑 의무**: 체결 확인 즉시 조건부 손절(SINGLE·SELL)을 등록. stop.level 없는 초안
  (시간손절 전용)은 시간손절이 TTL·포지션 점검에서 집행된다.
- **append-only 저널**(``data/exec.sqlite``): intent/sent/fill/stop/skip 전부 기록,
  초안·일자당 1회 dedup(재기동 무해).

흐름: armed(arm-watch) → ``execute_armed``(캡 검사→지정가 매수[dry/live]→저널+P0 보고)
→ 이후 감시 루프 패스마다 ``reconcile``(주문 체결 폴링→체결 시 손절 등록+포지션 박제+P0).
"""

import os
import re
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from math import floor
from pathlib import Path

from typing import Any

from trading.alerts import Alert, AlertDispatcher, Severity
from trading.collectors.base import KST, now_kst
from trading.collectors.toss import TossClient
from trading.contracts.order import OrderDraft
from trading.contracts.position import PositionRecord, PositionStatus
from trading.journal.positions import PositionStore
from trading.market_calendar.calendar import MarketCalendar
from trading.regime import Regime

DEFAULT_DB = Path("data") / "exec.sqlite"
KILL_FILE = Path(".runtime") / "exec" / "KILL"

# KRX 호가가격단위(원) — 2023-01 개정 표준(공개 규정). 지정가 산출에 사용.
_TICKS: tuple[tuple[int, int], ...] = (
    (2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50),
    (200_000, 100), (500_000, 500),
)


def tick_size(price: float) -> int:
    for bound, tick in _TICKS:
        if price < bound:
            return tick
    return 1_000


def round_down_to_tick(price: float) -> int:
    t = tick_size(price)
    return int(price // t) * t


@dataclass(frozen=True)
class ExecPolicy:
    """사이징 기준액 + 폭주 가드 (투자 정책 캡 없음 — 계좌가 상한, EXEC-1 개정)."""

    account_krw: int = 5_000_000   # 계좌 고정 예치금(운영자) = normal_unit 기준액
    max_new_per_day: int = 5       # 오작동 방어(정상 R5 산출은 하루 수 건 이하)
    max_price_krw: int = 2_000_000  # 주당 가격 상한(EXEC-4, 운영자) — 계단 청산 가능 종목만

    @classmethod
    def from_env(cls) -> "ExecPolicy":
        base = cls()

        def _pos(env: str, default: int) -> int:
            try:
                v = int(os.environ.get(env, ""))
            except ValueError:
                return default
            return v if v > 0 else default

        return cls(
            account_krw=_pos("EXEC_ACCOUNT_KRW", base.account_krw),
            max_new_per_day=_pos("EXEC_MAX_NEW_PER_DAY", base.max_new_per_day),
            max_price_krw=_pos("EXEC_MAX_PRICE_KRW", base.max_price_krw),
        )


def exec_mode(*, kill_file: Path = KILL_FILE) -> str:
    """off | dry-run | live. KILL 파일 존재 시 무조건 off."""
    if kill_file.exists():
        return "off"
    mode = os.environ.get("EXEC_MODE", "dry-run").strip().lower()
    return mode if mode in ("off", "dry-run", "live") else "dry-run"


def _min_rr() -> float:
    """진입 최소 잔여 R:R(EXEC_MIN_RR, 기본 1.0) — 보상이 위험 이상일 때만 신규 진입."""
    try:
        v = float(os.environ.get("EXEC_MIN_RR", ""))
    except ValueError:
        return 1.0
    return v if 0.0 <= v <= 5.0 else 1.0


def _rr_ratio() -> float:
    """익절 R:R 비율(EXEC_RR, 기본 1.5) — 익절가 = 체결가 + R:R × (체결가 − 손절 트리거)."""
    try:
        v = float(os.environ.get("EXEC_RR", ""))
    except ValueError:
        return 1.5
    return v if 0.5 <= v <= 5.0 else 1.5


def _entry_gap_pct() -> float:
    """진입 밴드 이격(EXEC_ENTRY_GAP_PCT, 기본 1%) — 손절·익절 라인과의 최소 거리(수수료·잡음 마진)."""
    try:
        v = float(os.environ.get("EXEC_ENTRY_GAP_PCT", ""))
    except ValueError:
        return 0.01
    return v if 0.0 < v <= 0.1 else 0.01


def _surge_max_pct() -> float:
    """급등 추격 금지 기준(EXEC_SURGE_MAX_PCT, 기본 +5%) — 당일 등락률이 이 이상이면 진입 금지
    (운영자 2026-07-15: 'N% 이상 급증하면 수익을 보기 어렵다')."""
    try:
        v = float(os.environ.get("EXEC_SURGE_MAX_PCT", ""))
    except ValueError:
        return 5.0
    return v if 0.5 <= v <= 29.0 else 5.0


# 급락 회복 공식(운영자 2026-07-15): 당일 저점 등락률 ≤ -5%면 급락 — 저점 낙폭의 40%를
# 회복해야 진입(기준 = 저점 등락률 × 0.6). 예: 저점 -5% → -3%부터, -10% → -6%부터.
PLUNGE_TRIGGER_PCT = -5.0
PLUNGE_RECOVERY_FACTOR = 0.6


def min_alloc_fraction(pool_unfilled_count: int) -> float:
    """진입당 최소 배분 비율(EXEC-10, 운영자 2026-07-15) — '1주씩만 사는' 초소액 배분 방지.

    기본 15%, 감시 종목이 적으면 상향: min(25%, max(15%, 50%/종목수)) —
    2종목 이하면 종목당 25%(잔고 최소 50% 사용 보장), 3종목 16.7%, 4종목 이상 15%."""
    n = max(pool_unfilled_count, 1)
    return min(0.25, max(0.15, 0.5 / n))


def _stop_floor_pct() -> float:
    """손절 지정가 플로어(EXEC_STOP_FLOOR_PCT, 기본 1%) — 급락 관통 체결용 하한 폭.

    지정가 매도는 '그 가격 밑으론 안 판다'이므로, 트리거보다 충분히 낮은 플로어가
    급락을 관통해도 체결되게 한다(2틱 고정은 급락 방어에 너무 얇음 — 운영자 지적 2026-07-14)."""
    try:
        v = float(os.environ.get("EXEC_STOP_FLOOR_PCT", ""))
    except ValueError:
        return 0.01
    return v if 0.0 < v <= 0.1 else 0.01


def stop_order_price(trigger: int) -> int:
    """손절 조건주문의 지정가 = 트리거 − max(2틱, 플로어%) — 틱 절사."""
    floor = max(2 * tick_size(trigger), trigger * _stop_floor_pct())
    return round_down_to_tick(trigger - floor)


def derive_entry_band(draft: OrderDraft, *, min_rr: float | None = None) -> tuple[float, float] | None:
    """진입 유효 가격 범위 산출(EXEC-8, 결정론) — 가격 스탑 없는 초안은 None.

    - 하한 = max(하드 스탑, 경고 soft_stop) × (1 + 이격%) — 스탑 잡음·즉시 경고 축소 진입 차단.
    - 상한 = min( (가중 익절합 + rr×스탑) / (가중치합 + rr),   ← 가중 보상/위험 ≥ rr (C4 해소)
                 익절1 × (1 − 이격%) )                        ← 익절 근접·수수료 마진
      (targets 없으면 R:R 폴백 공식이 자기충족이라 상한은 무한 — 하한만 적용.)
    - 상한 ≤ 하한이면 밴드 공집합 = 그 초안은 구조적으로 진입 불가(호출측 스킵)."""
    if not (draft.stop and draft.stop.level):
        return None
    gap = _entry_gap_pct()
    s = float(draft.stop.level)
    floor_ref = max(s, float(draft.soft_stop.level) if draft.soft_stop else s)
    low = floor_ref * (1 + gap)
    if not draft.targets:
        return (low, float("inf"))
    rr = min_rr if min_rr is not None else _min_rr()
    w_sum = sum(t.pct for t in draft.targets) / 100.0
    wt_sum = sum(t.pct / 100.0 * t.level for t in draft.targets)
    e_max_rr = (wt_sum + rr * s) / (w_sum + rr) if (w_sum + rr) > 0 else low
    e_max_tp = draft.targets[0].level * (1 - gap)
    return (low, min(e_max_rr, e_max_tp))


_DDL = """
CREATE TABLE IF NOT EXISTS exec_log (
  row_id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL, draft_id TEXT NOT NULL, symbol TEXT NOT NULL,
  kind TEXT NOT NULL,            -- order_intent|order_sent|fill|stop_intent|stop_sent|skip|error
  mode TEXT NOT NULL,            -- dry-run|live
  qty INTEGER, price INTEGER, order_id TEXT, detail TEXT,
  created_at TEXT NOT NULL
);
"""


class ExecStore:
    """집행 저널(append-only). dedup·오픈 심볼 계수·미체결 추적의 단일 근거."""

    def __init__(self, db_path: Path | None = None) -> None:
        resolved = db_path if db_path is not None else DEFAULT_DB
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(resolved))
        self._conn.executescript(_DDL)

    def log(
        self, *, day: str, draft_id: str, symbol: str, kind: str, mode: str,
        qty: int | None = None, price: int | None = None,
        order_id: str | None = None, detail: str = "", at: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO exec_log (day,draft_id,symbol,kind,mode,qty,price,order_id,detail,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (day, draft_id, symbol, kind, mode, qty, price, order_id, detail, at),
        )
        self._conn.commit()

    def has(
        self, draft_id: str, kinds: tuple[str, ...], *,
        mode: str | None = None, day: str | None = None,
    ) -> bool:
        """mode='live'면 live 행만 본다 — dry-run 잔재가 live 판단(dedup 등)을 오염시키지 않게
        (2026-07-14 전환 사고: dry-run order_intent가 live 재진입을 차단).
        day를 주면 그날 행만 — 일자 무구분 dedup이 알림을 영구 침묵시키는 것 방지(가드 감사 B7)."""
        q = ",".join("?" for _ in kinds)
        sql = f"SELECT 1 FROM exec_log WHERE draft_id=? AND kind IN ({q})"
        params: tuple[str, ...] = (draft_id, *kinds)
        if mode:
            sql += " AND mode=?"
            params = (*params, mode)
        if day:
            sql += " AND day=?"
            params = (*params, day)
        row = self._conn.execute(sql + " LIMIT 1", params).fetchone()
        return row is not None

    def new_orders_today(self, day: str, *, mode: str | None = None) -> int:
        sql = (
            "SELECT COUNT(DISTINCT draft_id) FROM exec_log"
            " WHERE day=? AND kind IN ('order_intent','order_sent')"
        )
        params: tuple[str, ...] = (day,)
        if mode:
            sql += " AND mode=?"
            params = (day, mode)
        row = self._conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    def committed_krw(self, *, mode: str | None = None) -> int:
        """진입 시도액 − 교체 매도액(dry-run 가용 잔고 근사 — 레그·스탑 청산은 보수적 미반영).

        mode='live'면 live 행만 — dry-run 흔적이 live 폴백 가용액을 깎으면 안 된다(가드 감사 B1)."""
        sql = (
            "SELECT COALESCE(SUM(CASE WHEN kind IN ('order_intent','order_sent') THEN qty*price"
            "                          WHEN kind IN ('rotation_sell','trim_sell') THEN -(qty*price)"
            "                          ELSE 0 END),0)"
            " FROM exec_log"
        )
        params: tuple[str, ...] = ()
        if mode:
            sql += " WHERE mode=?"
            params = (mode,)
        row = self._conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    def cash_skips_today(self, day: str, *, mode: str | None = None) -> list[str]:
        """오늘 '잔고 부족'으로 스킵됐고 아직 미집행인 초안 — 매 패스 재시도 대상(EXEC-4).

        mode='live'면 live의 skip·집행만 본다 — dry-run skip이 live 재시도를 만들면 안 된다(B2)."""
        sql = (
            "SELECT DISTINCT draft_id FROM exec_log e WHERE day=? AND kind='skip'"
            " AND detail LIKE '잔고 부족%'"
        )
        params: tuple[str, ...] = (day,)
        if mode:
            sql += " AND mode=?"
            params = (*params, mode)
        sql += (
            " AND NOT EXISTS (SELECT 1 FROM exec_log o WHERE o.draft_id=e.draft_id"
            "                 AND o.kind IN ('order_intent','order_sent')"
        )
        if mode:
            sql += " AND o.mode=?"
            params = (*params, mode)
        sql += ")"
        cur = self._conn.execute(sql, params)
        return [str(r[0]) for r in cur]

    def retryable_skips_today(self, day: str, *, mode: str | None = None) -> list[str]:
        """매 패스 재시도 대상 skip — 잔고 부족(EXEC-4)·모멘텀 보류(EXEC-10). 미집행 초안만.

        모멘텀 보류는 급등 진정·급락 회복이 확인되는 패스에 진입해야 하므로 armed 1회
        소비 후에도 계속 재평가한다(운영자 2026-07-15)."""
        sql = (
            "SELECT DISTINCT draft_id FROM exec_log e WHERE day=? AND kind='skip'"
            " AND (detail LIKE '잔고 부족%' OR detail LIKE '모멘텀 보류%')"
        )
        params: tuple[str, ...] = (day,)
        if mode:
            sql += " AND mode=?"
            params = (*params, mode)
        sql += (
            " AND NOT EXISTS (SELECT 1 FROM exec_log o WHERE o.draft_id=e.draft_id"
            "                 AND o.kind IN ('order_intent','order_sent')"
        )
        if mode:
            sql += " AND o.mode=?"
            params = (*params, mode)
        sql += ")"
        cur = self._conn.execute(sql, params)
        return [str(r[0]) for r in cur]

    def entries_count(self, draft_id: str, *, mode: str | None = None) -> int:
        """이 초안의 진입 횟수(order_intent/order_sent 행 수) — 재진입 판정(EXEC-8)."""
        sql = ("SELECT COUNT(*) FROM exec_log WHERE draft_id=?"
               " AND kind IN ('order_intent','order_sent')")
        params: tuple[str, ...] = (draft_id,)
        if mode:
            sql += " AND mode=?"
            params = (*params, mode)
        row = self._conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    def rotations_today(self, day: str, *, mode: str | None = None) -> int:
        """mode='live'면 live 교체만 계수 — dry-run 교체가 live 일1회 예산을 소모하면 안 된다(B3)."""
        sql = "SELECT COUNT(*) FROM exec_log WHERE day=? AND kind='rotation_sell'"
        params: tuple[str, ...] = (day,)
        if mode:
            sql += " AND mode=?"
            params = (*params, mode)
        row = self._conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    def pending_fills(self, *, mode: str | None = None) -> list[tuple[str, str, str, int, int]]:
        """스탑 미등록 주문 — (draft_id, symbol, order_id, qty, price).

        mode를 주면 그 모드 행만 — 교차 모드 세션이 상대 모드 주문을 체결 처리하면 안 된다(B6)."""
        sql = (
            "SELECT draft_id, symbol, COALESCE(order_id,''), qty, price FROM exec_log e"
            " WHERE kind IN ('order_intent','order_sent')"
        )
        params: tuple[str, ...] = ()
        if mode:
            sql += " AND mode=?"
            params = (mode,)
        sql += (
            " AND draft_id NOT IN (SELECT draft_id FROM exec_log"
            "                      WHERE kind IN ('stop_intent','stop_sent','skip_stop','buy_cancel')"
        )
        if mode:
            sql += " AND mode=?"
            params = (*params, mode)
        sql += ")"
        cur = self._conn.execute(sql, params)
        return [(str(r[0]), str(r[1]), str(r[2]), int(r[3]), int(r[4])) for r in cur]

    def pending_leg_orders(self, *, mode: str | None = None) -> list[tuple[str, str, str, str, int, int]]:
        """미확인 레그 매도 — (draft_id, symbol, leg_kind, order_id, qty, price).

        레그별 최신 주문 행 기준, 'leg_fill'(detail=leg_kind) 해소 전까지 추적."""
        sql = (
            "SELECT draft_id, symbol, kind, order_id, qty, price FROM exec_log e"
            " WHERE kind LIKE 'leg_%' AND kind != 'leg_fill' AND order_id IS NOT NULL AND order_id != ''"
        )
        params: tuple[str, ...] = ()
        if mode:
            sql += " AND mode=?"
            params = (mode,)
        sql += (
            " AND row_id = (SELECT MAX(row_id) FROM exec_log e2"
            "               WHERE e2.draft_id = e.draft_id AND e2.kind = e.kind AND e2.order_id IS NOT NULL)"
            " AND NOT EXISTS (SELECT 1 FROM exec_log f"
            "                 WHERE f.draft_id = e.draft_id AND f.kind = 'leg_fill' AND f.detail = e.kind)"
        )
        cur = self._conn.execute(sql, params)
        return [
            (str(r[0]), str(r[1]), str(r[2]), str(r[3]), int(r[4]), int(r[5])) for r in cur
        ]

    def latest_bracket(self, draft_id: str, *, mode: str | None = None) -> tuple[str, int, int] | None:
        """현재 브래킷 상태 — (조건주문 id, 잔량, 손절 트리거). 미등록이면 None.

        mode를 주면 그 모드 행만 — dry-run 브래킷을 live 잔량으로 오인하면 안 된다(B4)."""
        sql = (
            "SELECT COALESCE(order_id,''), qty, price FROM exec_log"
            " WHERE draft_id=? AND kind IN ('stop_intent','stop_sent')"
        )
        params: tuple[str, ...] = (draft_id,)
        if mode:
            sql += " AND mode=?"
            params = (*params, mode)
        row = self._conn.execute(sql + " ORDER BY row_id DESC LIMIT 1", params).fetchone()
        if row is None or row[1] is None:
            return None
        return (str(row[0]), int(row[1]), int(row[2]))

    def close(self) -> None:
        self._conn.close()


@dataclass(frozen=True)
class ExecResult:
    action: str          # ordered|skipped|off
    detail: str = ""


def _immediate_pct(draft: OrderDraft) -> int:
    """즉시 지정가 트랜치 비중 합(%). 확인(condition) 트랜치는 v1 미집행 — 저널에 고지."""
    return sum(t.pct_of_plan for t in draft.tranches if t.order_type is not None)


_CAP_RE = re.compile(r"\s*([0-9]*\.?[0-9]+)")


def cap_fraction(expr: str) -> float:
    """R5 total_size_cap("0.5 * normal_unit")의 계수. 해석 불가·범위 밖은 0.5(보수)."""
    m = _CAP_RE.match(expr or "")
    if not m:
        return 0.5
    try:
        v = float(m.group(1))
    except ValueError:
        return 0.5
    if not 0.0 < v <= 1.0:
        return 0.5
    return v


def execute_armed(
    draft: OrderDraft,
    *,
    price: float,
    store: ExecStore,
    policy: ExecPolicy,
    mode: str,
    toss: TossClient | None,
    dispatcher: AlertDispatcher | None = None,
    now: datetime | None = None,
    pool_weight_total: float | None = None,
    regime: Regime = Regime.NORMAL,
    test_entry: bool = False,
    position_store: PositionStore | None = None,
    momentum_fn: Callable[[str], tuple[float, float] | None] | None = None,
    pool_unfilled_count: int | None = None,
) -> ExecResult:
    """발동 초안 1건 집행 — 배분은 **풀 비례 공정 분할**(EXEC-5, 발동 순서 무관).

    ``pool_weight_total`` = 아직 미집행인 활성 초안들(자신 포함)의 cap 계수 합.
    배분 = 가용액 × (자기 계수 / 계수 합) — 어떤 순서로 발동해도 각 초안의 몫이 동일
    (비례 분할의 순서 불변성). 미전달(None)이면 구식(가용액 × 자기 계수) 폴백."""
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    day = resolved.strftime("%Y%m%d")
    d = dispatcher if dispatcher is not None else AlertDispatcher()

    if mode == "off":
        return ExecResult("off", "EXEC_MODE=off 또는 KILL 파일")
    if draft.side.value != "buy":
        return ExecResult("skipped", "매수 초안만 자동 집행(현물)")
    # 진입 한도(EXEC-8): max_entries(기본 1 — 재진입 불허)까지만. live는 live 기록만 계수
    # (dry-run 흔적이 실진입을 막으면 안 된다 — 7/14 전환 사고)
    entries_used = store.entries_count(draft.id, mode="live" if mode == "live" else None)
    if entries_used >= max(draft.max_entries, 1):
        return ExecResult("skipped", "이미 집행됨(진입 한도 소진)")
    reentry = entries_used >= 1
    if reentry:
        # 재진입 가드(EXEC-8, 운영자 결정 2026-07-14) — 전부 비저널(매 패스 반복 무해):
        # 기보유 금지 · 1차 청산 확정 필수 · 하드 스탑 청산 후 금지(무효화) · 쿨다운 30분
        if position_store is None:
            return ExecResult("skipped", "재진입 보류(포지션 장부 미제공)")
        if any(p.symbol == draft.symbol for p in position_store.open_positions()):
            return ExecResult("skipped", "재진입 보류(동일 종목 기보유)")
        last = position_store.latest_for_source(draft.id)
        if last is None or last.status is not PositionStatus.CLOSED:
            return ExecResult("skipped", "재진입 보류(1차 진입 청산 미확정)")
        why = (last.close_reason or "").lower()
        if "스탑" in why or "손절" in why or "stop" in why:
            return ExecResult("skipped", "재진입 금지(하드 스탑 청산 — 무효화 규율)")
        if (resolved - last.as_of.astimezone(KST)).total_seconds() < 30 * 60:
            return ExecResult("skipped", "재진입 보류(청산 후 쿨다운 30분)")

    def _skip(reason: str) -> ExecResult:
        store.log(day=day, draft_id=draft.id, symbol=draft.symbol, kind="skip",
                  mode=mode, detail=reason, at=resolved.isoformat())
        return ExecResult("skipped", reason)

    if store.new_orders_today(day, mode="live" if mode == "live" else None) >= policy.max_new_per_day:
        return _skip(f"일일 신규 상한({policy.max_new_per_day}건) — 폭주 가드")
    # 레짐 게이트(EXEC-7): 지수 급락일 신규 진입 보수화 — 청산 관리는 이 함수 밖에서 계속
    if regime is Regime.RISK_OFF:
        return _skip("레짐 RISK_OFF(코스피 -5% 이하) — 신규 진입 중단")
    # 가용액: live=실 매수가능금액(브로커), dry-run=기준액−기집행(보수 근사, 청산 미반영)
    # live 폴백은 live 기록만 차감(B1) — dry-run은 live 실약정도 함께 차감(보수)
    available = policy.account_krw - store.committed_krw(
        mode="live" if mode == "live" else None
    )
    if mode == "live" and toss is not None:
        bp = toss.buying_power_krw()
        if bp is not None:
            available = bp
    w = cap_fraction(draft.total_size_cap)
    if pool_weight_total is not None and pool_weight_total >= w > 0:
        # 동적 풀 비례(EXEC-5 개정 — 운영자 2026-07-13 밤 5): 분모 = **미집행 잔여 풀**.
        # 남은 트리거가 줄면 몫이 자동으로 커진다(자본 가동률 우선 — 순서 영향은 ±소폭 잔존,
        # 부족분은 회수 사다리(EXEC-6: 갈아타기→부분 트림)가 채운다).
        alloc = available * (w / pool_weight_total)
    else:
        alloc = available * w  # 폴백(풀 정보 없음 — 단독 호출·구식 경로)
    # 최소 배분 하한(EXEC-10, 운영자 2026-07-15): 소액 계좌+큰 풀에서 배분이 초소액으로
    # 쪼개져 '1주씩만 사는' 문제 방지 — min(25%, max(15%, 50%/감시종목수)) × 가용액.
    if pool_unfilled_count is not None and pool_unfilled_count > 0:
        alloc = max(alloc, available * min_alloc_fraction(pool_unfilled_count))
    alloc = min(alloc, available)
    if regime in (Regime.CAUTION, Regime.UNKNOWN):
        alloc *= 0.5  # 급락 경계·관측 불가 — 배분 절반(보수)
    budget = alloc * _immediate_pct(draft) / 100
    limit_price = round_down_to_tick(price)
    if limit_price <= 0:
        return _skip("유효 가격 없음")
    if limit_price > policy.max_price_krw:
        return _skip(
            f"주당 가격 상한 초과({limit_price:,} > {policy.max_price_krw:,}) — "
            "계단 청산 불가 종목 배제(EXEC-4)"
        )
    # 진입 밴드(EXEC-8, 운영자 결정 2026-07-14) — 붕괴·소진·R:R 가드 3종을 밴드 1개로 통합:
    # 하한=손절·경고 이격(스탑 잡음 진입 차단), 상한=가중 보상 R:R+익절1 이격(익절 근접 차단).
    # 코드 산출 밴드 ∩ R5 조임(entry_band 계약 필드 — R5 확장은 계약단에서 폐기됨)
    band = derive_entry_band(draft)
    if band is not None:
        b_low, b_high = band
        if draft.entry_band is not None:
            b_low = max(b_low, draft.entry_band.low)
            b_high = min(b_high, draft.entry_band.high)
        if b_high <= b_low:
            return _skip(
                f"진입 밴드 공집합(하한 {b_low:,.0f} ≥ 상한 {b_high:,.0f}) — 구조적 진입 불가"
            )
        if limit_price < b_low:
            return _skip(
                f"진입 밴드 하한 미달(현재가 {limit_price:,} < {b_low:,.0f}) — 손절·경고 근접"
            )
        if limit_price > b_high:
            return _skip(
                f"진입 밴드 상한 초과(현재가 {limit_price:,} > {b_high:,.0f}) — 잔여 보상 부족"
            )
    # 모멘텀 가드(운영자 2026-07-15): 급등 추격·떨어지는 칼 진입 차단. '모멘텀 보류' skip은
    # 매 패스 재시도 대상(retryable) — 회복이 확인되는 순간 진입한다.
    if momentum_fn is not None:
        m = momentum_fn(draft.symbol)
        if m is None:
            return _skip("모멘텀 보류(관측 불가) — 급등·급락 판정 전 보수 대기")
        cur_pct, low_pct = m
        if cur_pct >= _surge_max_pct():
            return _skip(
                f"모멘텀 보류(급등 추격 금지 — 당일 {cur_pct:+.1f}% ≥ +{_surge_max_pct():g}%)"
            )
        if low_pct <= PLUNGE_TRIGGER_PCT and cur_pct < low_pct * PLUNGE_RECOVERY_FACTOR:
            return _skip(
                f"모멘텀 보류(급락 회복 미확인 — 저점 {low_pct:+.1f}%, "
                f"현재 {cur_pct:+.1f}% < 기준 {low_pct * PLUNGE_RECOVERY_FACTOR:+.1f}%)"
            )
    if reentry:
        budget *= 0.5  # 재진입 체감 50%(운영자) — 같은 셋업 2회차는 신뢰도 하향
    qty = floor(budget / limit_price)
    if qty < 1 and limit_price <= available:
        qty = 1  # 최소 1주 보장 — 상한 이내 고가 종목이 계수 때문에 못 사지는 상황 방지
    if test_entry:
        qty = 1  # 테스트 진입(D1 계측): 파이프라인 관통 확인용 최소 수량
    if qty < 1:
        return _skip(f"잔고 부족(가용 {available:,.0f}원 < 1주 {limit_price:,}원)")

    order_id: str | None = None
    if mode == "live":
        if toss is None:
            return _skip("live인데 토스 클라이언트 없음(키 미설정)")
        try:
            res = toss.place_limit_order(
                draft.symbol, "BUY", qty, limit_price,
                client_order_id=f"exec-{draft.id}"[:36].replace(".", "-"),
            )
        except Exception as exc:  # noqa: BLE001 — 주문 실패는 기록+P1급 보고, 재시도 안 함(중복 방지)
            store.log(day=day, draft_id=draft.id, symbol=draft.symbol, kind="error",
                      mode=mode, qty=qty, price=limit_price, detail=str(exc)[:300],
                      at=resolved.isoformat())
            d.notify(Alert(severity=Severity.P1,
                           what=f"주문 실패 — {draft.symbol} {qty}주 @{limit_price:,}",
                           rule="자동 집행(EXEC-1): 지정가 매수 전송 오류",
                           action="토스 앱에서 상태 확인 · 로그 exec.sqlite 참조",
                           deadline="당일", created_at=resolved))
            return ExecResult("skipped", f"주문 전송 실패: {exc}")
        order_id = str(res.get("orderId") or "")
    kind = "order_sent" if mode == "live" else "order_intent"
    detail_tag = "테스트 진입(D1 계측 — 최소 수량)" if test_entry else (
        f"즉시 트랜치 {_immediate_pct(draft)}% (확인 트랜치는 v1 미집행)"
    )
    if reentry:
        detail_tag = f"재진입 2회차(체감 50%, EXEC-8) · {detail_tag}"
    store.log(day=day, draft_id=draft.id, symbol=draft.symbol, kind=kind, mode=mode,
              qty=qty, price=limit_price, order_id=order_id, at=resolved.isoformat(),
              detail=detail_tag)
    tag = "매수 주문" if mode == "live" else "매수 (dry-run — 전송 안 함)"
    if test_entry:
        tag += " · 테스트 진입"
    stop_txt = f"손절 {draft.stop.level:,.0f}" if draft.stop and draft.stop.level else f"시간손절 {draft.time_stop_days}일"
    d.notify(Alert(severity=Severity.P0,
                   what=f"{tag} — {draft.symbol} {qty}주 @{limit_price:,}원 ({stop_txt})",
                   rule="자동 집행(EXEC-1): approved 초안 발동 → 지정가 매수",
                   action="개입 불필요 — 취소하려면 토스 앱 또는 .runtime/exec/KILL 생성",
                   deadline="체결 후 손절 자동 등록", created_at=resolved))
    return ExecResult("ordered", f"{qty}주 @{limit_price:,} ({kind})")


def reconcile(
    *,
    store: ExecStore,
    mode: str,
    toss: TossClient | None,
    drafts_by_id: dict[str, OrderDraft],
    dispatcher: AlertDispatcher | None = None,
    position_store: PositionStore | None = None,
    calendar: MarketCalendar | None = None,
    now: datetime | None = None,
    price_fn: Callable[[str], float | None] | None = None,
) -> list[str]:
    """미체결 추적 → 체결 시 손절 조건주문 등록 + 포지션 박제. 반환=처리된 draft_id.

    ``price_fn`` 이 주어지면(운영 루프) 미체결 매수의 역선택 정리(A7)도 수행 —
    셋업 붕괴(현재가≤손절)·소진(현재가≥익절1)·마감 정리 창(14:40~)이면 매수를 취소한다."""
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    day = resolved.strftime("%Y%m%d")
    d = dispatcher if dispatcher is not None else AlertDispatcher()
    cal = calendar if calendar is not None else MarketCalendar.default()
    done: list[str] = []
    for draft_id, symbol, order_id, qty, price in store.pending_fills(mode=mode):
        draft = drafts_by_id.get(draft_id)
        if draft is None:
            continue
        filled_qty, avg = qty, float(price)
        if mode == "live":
            if toss is None or not order_id:
                continue
            try:
                o = toss.order(order_id)
            except Exception:  # noqa: BLE001 — 조회 실패는 다음 패스 재시도
                continue
            status = str(o.get("status") or "")
            ex = o.get("execution") or {}
            try:
                filled_from_ex = int(float(str(ex.get("filledQuantity") or 0)))
            except ValueError:
                filled_from_ex = 0
            if status in ("CANCELED", "REJECTED") and filled_from_ex < 1:
                # 체결 없이 종결 — 미체결 좀비 추적 중단(A7). 부분 체결분이 있으면 아래
                # 체결 경로로 넘어가 스탑 등록을 계속 재시도한다(무방비 금지).
                store.log(day=day, draft_id=draft_id, symbol=symbol, kind="buy_cancel",
                          mode=mode, detail=f"매수 {status} — 추적 종료", at=resolved.isoformat())
                continue
            if status not in ("FILLED", "PARTIAL_FILLED", "CANCELED", "REJECTED"):
                # A7: 미체결 매수 역선택 정리 — 셋업이 깨졌거나 마감 정리 창이면 취소
                cur = price_fn(symbol) if price_fn is not None else None
                broken = bool(draft.stop and draft.stop.level and cur is not None
                              and cur <= draft.stop.level)
                exhausted = bool(draft.targets and cur is not None
                                 and cur >= draft.targets[0].level)
                closeout = resolved.time() >= dt_time(14, 40)
                if broken or exhausted or closeout:
                    why = ("셋업 붕괴" if broken else "셋업 소진" if exhausted else "마감 정리")
                    try:
                        toss.cancel_order(order_id)
                    except Exception as exc:  # noqa: BLE001 — 취소 실패는 다음 패스 재시도
                        store.log(day=day, draft_id=draft_id, symbol=symbol, kind="error",
                                  mode=mode, detail=f"미체결 매수 취소 실패: {exc}"[:200],
                                  at=resolved.isoformat())
                        continue
                    store.log(day=day, draft_id=draft_id, symbol=symbol, kind="buy_cancel",
                              mode=mode, qty=qty, price=price,
                              detail=f"미체결 매수 취소({why}, A7)", at=resolved.isoformat())
                    d.notify(Alert(severity=Severity.P1,
                                   what=f"미체결 매수 취소 — {symbol} {qty}주 @{price:,} ({why})",
                                   rule="집행 정리(A7): 깨진 셋업의 지정가가 되돌림에서 체결되는 것 방지",
                                   action="개입 불필요", deadline="-", created_at=resolved))
                continue
            try:
                filled_qty = filled_from_ex if filled_from_ex > 0 else qty
                avg = float(str(ex.get("averagePrice") or price))
            except ValueError:
                filled_qty, avg = qty, float(price)
            if filled_qty < 1:
                continue
            if status == "PARTIAL_FILLED":
                # A2: 잔여 매수 즉시 취소 — 스탑 등록 후 추가 체결분이 무방비로 남는 것 방지.
                # 기록은 buy_cancel_rest(추적 비종결) — 스탑 등록 실패 시 다음 패스 재시도 유지
                try:
                    toss.cancel_order(order_id)
                    store.log(day=day, draft_id=draft_id, symbol=symbol, kind="buy_cancel_rest",
                              mode=mode, qty=qty - filled_qty, price=price,
                              detail="부분 체결 — 잔여 매수 취소(A2)", at=resolved.isoformat())
                except Exception as exc:  # noqa: BLE001 — 취소 실패 = 추가 체결 위험, 수동 개입 요청
                    d.notify(Alert(severity=Severity.P1,
                                   what=f"부분 체결 잔여 취소 실패 — {symbol} 잔여 {qty - filled_qty}주",
                                   rule="자동 집행(A2): 이후 체결분은 손절 미등록 상태",
                                   action="토스 앱에서 미체결 매수 수동 취소",
                                   deadline="즉시", created_at=resolved))
                    store.log(day=day, draft_id=draft_id, symbol=symbol, kind="error",
                              mode=mode, detail=f"부분 체결 잔여 취소 실패: {exc}"[:200],
                              at=resolved.isoformat())
        # dry-run은 발동가 체결 가정 — 즉시 청산 조건 등록 시뮬레이션
        stop_level = draft.stop.level if draft.stop else None
        target_txt = ""
        if stop_level:
            trigger = round_down_to_tick(stop_level)
            order_price = stop_order_price(trigger)  # 플로어 = max(2틱, 1%) — 급락 관통 체결(EXEC-8)
            # 브래킷 만료 = 시간손절 +1거래일(EXEC-9) — 만료 시각 미확정이라 집행 창을 덮는다
            expiry = cal.add_trading_days(resolved.date(), (draft.time_stop_days or 20) + 1)
            # 브래킷 상단(EXEC-2): R5가 targets를 지정했으면 **최종 타깃**(부분 레그는 감시
            # 루프의 manage_exits 몫), 없으면 R:R 비율(EXEC_RR, 기본 1.5) 폴백.
            if draft.targets:
                target = round_down_to_tick(draft.targets[-1].level)
            else:
                target = round_down_to_tick(avg + _rr_ratio() * (avg - trigger)) if avg > trigger else 0
            use_oco = target > trigger
            cond_id = ""
            if mode == "live" and toss is not None:
                try:
                    if use_oco:
                        res_c = toss.place_oco_sell(
                            symbol, filled_qty,
                            stop_trigger=trigger, stop_price=order_price,
                            target_trigger=target, target_price=target,
                            expire_date=expiry.isoformat(),
                            client_order_id=f"oco-{draft_id}"[:36].replace(".", "-"),
                        )
                    else:  # 익절가 산출 불가(체결가≤손절) — 손절 단독
                        res_c = toss.place_stop_sell_conditional(
                            symbol, filled_qty, trigger_price=trigger, order_price=order_price,
                            expire_date=expiry.isoformat(),
                            client_order_id=f"stop-{draft_id}"[:36].replace(".", "-"),
                        )
                    cond_id = str(res_c.get("conditionalOrderId") or "")
                except Exception as exc:  # noqa: BLE001 — 청산 등록 실패는 치명 — P0 즉시
                    d.notify(Alert(severity=Severity.P0,
                                   what=f"청산 조건 등록 실패 — {symbol} {filled_qty}주 (손절 {trigger:,})",
                                   rule="자동 집행(EXEC-1): 체결분 무방비 상태",
                                   action="토스 앱에서 수동으로 손절 조건주문 등록",
                                   deadline="즉시", created_at=resolved))
                    store.log(day=day, draft_id=draft_id, symbol=symbol, kind="error",
                              mode=mode, detail=f"청산 등록 실패: {exc}"[:300], at=resolved.isoformat())
                    continue
            target_txt = f" · 익절 {target:,}(OCO)" if use_oco else ""
            store.log(day=day, draft_id=draft_id, symbol=symbol,
                      kind="stop_sent" if mode == "live" else "stop_intent", mode=mode,
                      qty=filled_qty, price=trigger, order_id=cond_id or None,
                      at=resolved.isoformat(),
                      detail=f"지정가 {order_price:,}{target_txt} · 만료 {expiry.isoformat()}")
        else:
            store.log(day=day, draft_id=draft_id, symbol=symbol, kind="skip_stop", mode=mode,
                      detail="가격 스탑 없음 — 시간손절 전용 초안", at=resolved.isoformat())
        if position_store is not None:
            position_store.append(PositionRecord(
                id=f"pos.{day}.{symbol}", as_of=resolved, fetched_at=resolved,
                source="executor" if mode == "live" else "executor:dry-run",
                symbol=symbol, qty=filled_qty, avg_price=avg,
                hypothesis=f"자동 집행 — 초안 {draft_id}",
                trigger_text="arm-watch 발동(조건 전원 충족)",
                stop_level=stop_level, time_stop_days=draft.time_stop_days,
                source_ref=draft_id,
            ))
        stop_txt = (
            f"손절 {round_down_to_tick(stop_level):,}원{target_txt} 등록" if stop_level else "시간손절 전용"
        )
        tag = "체결" if mode == "live" else "체결 가정(dry-run)"
        d.notify(Alert(severity=Severity.P0,
                       what=f"{tag} — {symbol} {filled_qty}주 @{avg:,.0f}원 → {stop_txt}",
                       rule="자동 집행(EXEC-1): 체결 → 청산 조건(손절/OCO) + 포지션 박제",
                       action="개입 불필요 — /positions로 상시 점검 가능",
                       deadline="-", created_at=resolved))
        done.append(draft_id)
    return done


def planned_upside_pct(draft: OrderDraft, entry_price: float) -> float:
    """계획 상승여력(결정론) — 최종 타깃 기준. 타깃 없으면 R:R 폴백으로 산출."""
    if entry_price <= 0:
        return 0.0
    if draft.targets:
        final = draft.targets[-1].level
    elif draft.stop and draft.stop.level and entry_price > draft.stop.level:
        final = entry_price + _rr_ratio() * (entry_price - draft.stop.level)
    else:
        return 0.0
    return max((final - entry_price) / entry_price, 0.0)


def consider_rotation(
    new_draft: OrderDraft,
    new_price: float,
    *,
    store: ExecStore,
    mode: str,
    toss: TossClient | None,
    drafts_by_id: dict[str, OrderDraft],
    price_fn: Callable[[str], float | None],
    position_store: PositionStore | None,
    dispatcher: AlertDispatcher | None = None,
    now: datetime | None = None,
) -> bool:
    """갈아타기(EXEC-4, 운영자 결정) — 잔고 부족 시 열등 포지션 매각 후 신규 진입 자금 확보.

    가드레일(전부 결정론):
    - **러너 보호**: 첫 익절 체결(본전 상향)된 포지션은 교체 불가 — 손실 불가 자산.
    - **매도 우선순위**: 손절 2% 이내 근접 = 잔여 가치 0(운영자 기준 — 죽어가는 셋업),
      그 외엔 잔여 상승여력((최종타깃−현재가)/현재가) 최저.
    - **교체 마진**: 신규 계획 상승여력 ≥ max(매도 대상 잔여 여력 × 2, +2%p) — 왕복 비용·소음 교체 차단.
    - **일 1회** — 회전 폭주 방지.
    성공(매도 주문까지) 시 True — 호출측이 같은 패스에서 신규 진입을 재시도한다.
    """
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    day = resolved.strftime("%Y%m%d")
    d = dispatcher if dispatcher is not None else AlertDispatcher()
    live_only = "live" if mode == "live" else None
    if position_store is None or store.rotations_today(day, mode=live_only) >= 1:
        return False
    new_up = planned_upside_pct(new_draft, new_price)
    if new_up <= 0.0:
        return False
    scored: list[tuple[float, Any, OrderDraft, float]] = []
    for pos in position_store.open_positions():
        old = drafts_by_id.get(pos.source_ref)
        if old is None or old.id == new_draft.id:
            continue
        if store.has(old.id, ("leg_t1",), mode=live_only):
            continue  # 러너 보호
        if store.has(old.id, ("bracket_gone",), mode=live_only):
            continue  # 보유 상태 불명(A6) — 확인 전 매각 대상 제외
        cur = price_fn(pos.symbol)
        if cur is None:
            continue
        final = old.targets[-1].level if old.targets else None
        rem_up = max((final - cur) / cur, 0.0) if final else 0.0
        stop_lvl = old.stop.level if old.stop else None
        if stop_lvl is not None and cur <= stop_lvl * 1.02:
            rem_up = 0.0  # 손절 근접 — 잔여 가치 0(운영자 기준)
        scored.append((rem_up, pos, old, cur))
    if not scored:
        return False
    rem_up, pos, old, cur = min(scored, key=lambda x: x[0])
    if new_up < max(rem_up * 2.0, 0.02):
        return False  # 교체 마진 미달 — 유지
    sell_price = round_down_to_tick(cur)
    bracket = store.latest_bracket(old.id, mode=mode)
    if mode == "live" and toss is not None:
        if bracket and bracket[0]:
            try:
                toss.cancel_conditional(bracket[0])  # 브래킷 해제 후 전량 매도
            except Exception as exc:  # noqa: BLE001 — 취소 실패: 기존 보호 잔존, 교체 중단(A1)
                store.log(day=day, draft_id=old.id, symbol=pos.symbol, kind="error", mode=mode,
                          detail=f"교체 중단(브래킷 취소 실패 — 기존 보호 유지): {exc}"[:200],
                          at=resolved.isoformat())
                return False
        try:
            toss.place_limit_order(
                pos.symbol, "SELL", pos.qty, sell_price,
                client_order_id=f"rot-{old.id}"[:36].replace(".", "-"),
            )
        except Exception as exc:  # noqa: BLE001 — 매도 실패인데 브래킷은 이미 취소됨 → 원복(A1)
            store.log(day=day, draft_id=old.id, symbol=pos.symbol, kind="error", mode=mode,
                      detail=f"교체 매도 실패: {exc}"[:200], at=resolved.isoformat())
            if bracket and bracket[0]:
                from trading.market_calendar.calendar import MarketCalendar as _Cal

                expiry_r = _Cal.default().add_trading_days(
                    resolved.date(), old.time_stop_days or 20
                )
                restored = ""
                try:
                    restored = _place_bracket(
                        toss, symbol=pos.symbol, qty=bracket[1], stop_trigger=bracket[2],
                        final_target=round_down_to_tick(old.targets[-1].level) if old.targets else 0,
                        expire_date=expiry_r.isoformat(),
                        client_order_id=f"restore-{old.id}"[:36].replace(".", "-"),
                    )
                except Exception:  # noqa: BLE001 — 원복까지 실패 = 무방비
                    restored = ""
                if restored:
                    store.log(day=day, draft_id=old.id, symbol=pos.symbol, kind="stop_sent",
                              mode=mode, qty=bracket[1], price=bracket[2], order_id=restored,
                              detail="교체 매도 실패 — 브래킷 원복(A1)", at=resolved.isoformat())
                    d.notify(Alert(severity=Severity.P1,
                                   what=f"교체 실패·브래킷 원복 — {pos.symbol} {bracket[1]}주 (손절 {bracket[2]:,})",
                                   rule="갈아타기(EXEC-4): 매도 오류 → 기존 보호 재등록",
                                   action="개입 불필요 — 교체는 다음 기회에",
                                   deadline="-", created_at=resolved))
                else:
                    store.log(day=day, draft_id=old.id, symbol=pos.symbol, kind="bracket_gone",
                              mode=mode, detail="교체 중 무방비(매도·원복 모두 실패)",
                              at=resolved.isoformat())
                    d.notify(Alert(severity=Severity.P0,
                                   what=f"브래킷 무방비 — {pos.symbol} {pos.qty}주 보호 없음(교체 실패)",
                                   rule="갈아타기(EXEC-4): 브래킷 취소 후 매도·원복 실패(A1)",
                                   action="토스 앱에서 손절 조건주문 즉시 수동 등록",
                                   deadline="즉시", created_at=resolved))
            return False
    store.log(day=day, draft_id=old.id, symbol=pos.symbol, kind="rotation_sell", mode=mode,
              qty=pos.qty, price=sell_price, at=resolved.isoformat(),
              detail=f"→ {new_draft.symbol} (신규 여력 {new_up:.1%} vs 잔여 {rem_up:.1%})")
    position_store.append(
        pos.model_copy(update={"status": PositionStatus.CLOSED,
                               "close_reason": f"교체(rotation) → {new_draft.symbol}"})
    )
    tag = "교체 매도" if mode == "live" else "교체 매도 (dry-run)"
    d.notify(Alert(severity=Severity.P0,
                   what=f"{tag} — {pos.symbol} {pos.qty}주 @{sell_price:,} → {new_draft.symbol} 진입 자금",
                   rule="갈아타기(EXEC-4): 신규 여력이 잔여 여력 2배 이상 + 러너 보호",
                   action="개입 불필요 — 같은 패스에서 신규 진입 재시도",
                   deadline="-", created_at=resolved))
    return True


def trim_for_shortfall(
    needed_krw: float,
    *,
    store: ExecStore,
    mode: str,
    toss: TossClient | None,
    drafts_by_id: dict[str, OrderDraft],
    price_fn: Callable[[str], float | None],
    position_store: PositionStore | None,
    dispatcher: AlertDispatcher | None = None,
    calendar: MarketCalendar | None = None,
    now: datetime | None = None,
) -> int:
    """부분 트림(EXEC-6) — 새 트리거 자금 부족분만 기보유 포지션에서 부분 매도로 회수.

    운영자 설계(2026-07-13): "앞 항목에서 일부를 정리해 뒤 트리거도 투자 가능하게".
    - 대상: 잔여 상승여력 최저 포지션부터. **러너(본전 상향) 제외**, 손절 2% 근접 제외
      (그건 갈아타기 전량 경로 몫).
    - 포지션당 **최대 50%까지만** 트림(포지션 훼손 방지) · 부족분 충족 시 즉시 중단.
    - 트림 후 브래킷을 잔량으로 교체. 반환 = 확보 금액(원, 매도가 기준 추정).
    """
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    day = resolved.strftime("%Y%m%d")
    d = dispatcher if dispatcher is not None else AlertDispatcher()
    cal = calendar if calendar is not None else MarketCalendar.default()
    if position_store is None or needed_krw <= 0:
        return 0
    live_only = "live" if mode == "live" else None
    ranked: list[tuple[float, Any, OrderDraft, float]] = []
    for pos in position_store.open_positions():
        old = drafts_by_id.get(pos.source_ref)
        if old is None or store.has(old.id, ("leg_t1",), mode=live_only) or pos.qty < 2:
            continue  # 러너 보호 · 1주 포지션 트림 불가
        if store.has(old.id, ("bracket_gone",), mode=live_only):
            continue  # 보유 상태 불명(A6) — 확인 전 트림 제외
        cur = price_fn(pos.symbol)
        if cur is None:
            continue
        stop_lvl = old.stop.level if old.stop else None
        if stop_lvl is not None and cur <= stop_lvl * 1.02:
            continue  # 손절 근접 — 부분이 아니라 갈아타기(전량) 판단 대상
        final = old.targets[-1].level if old.targets else None
        rem_up = max((final - cur) / cur, 0.0) if final else 0.0
        ranked.append((rem_up, pos, old, cur))
    freed = 0
    for rem_up, pos, old, cur in sorted(ranked, key=lambda x: x[0]):
        if freed >= needed_krw:
            break
        sell_price = round_down_to_tick(cur)
        max_trim = pos.qty // 2  # 포지션당 50% 상한
        want = int((needed_krw - freed) // sell_price) + 1
        trim_qty = min(max_trim, want)
        if trim_qty < 1:
            continue
        bracket = store.latest_bracket(old.id, mode=mode)
        try:
            if mode == "live" and toss is not None:
                toss.place_limit_order(
                    pos.symbol, "SELL", trim_qty, sell_price,
                    client_order_id=f"trim-{old.id}"[:36].replace(".", "-"),
                )
        except Exception as exc:  # noqa: BLE001 — 매도 실패: 브래킷 미접촉, 다음 후보로
            store.log(day=day, draft_id=old.id, symbol=pos.symbol, kind="error", mode=mode,
                      detail=f"트림 매도 실패: {exc}"[:200], at=resolved.isoformat())
            continue
        # 트림 매도는 전송 즉시 박제(A1 부수) — 브래킷 교체 실패가 재트림(이중 매도)을 만들지 않게
        store.log(day=day, draft_id=old.id, symbol=pos.symbol, kind="trim_sell", mode=mode,
                  qty=trim_qty, price=sell_price, at=resolved.isoformat(),
                  detail=f"부분 회수(잔여 여력 {rem_up:.1%}) — 신규 트리거 자금")
        expiry = cal.add_trading_days(resolved.date(), (old.time_stop_days or 20) + 1)  # EXEC-9 +1
        stop_trigger = round_down_to_tick(old.stop.level) if old.stop and old.stop.level else 0
        final_t = round_down_to_tick(old.targets[-1].level) if old.targets else 0
        if bracket and stop_trigger:
            try:
                _rebracket(
                    toss=toss, mode=mode, symbol=pos.symbol, draft_id=old.id,
                    old_cond_id=bracket[0], qty=pos.qty - trim_qty,
                    stop_trigger=stop_trigger, final_target=final_t,
                    expire_date=expiry.isoformat(), tag="trim",
                )
            except BracketGapError as exc:  # A1: 무방비 확정 — P0 + 박제
                store.log(day=day, draft_id=old.id, symbol=pos.symbol, kind="bracket_gone",
                          mode=mode, detail=f"트림 교체 중 무방비: {exc}"[:200],
                          at=resolved.isoformat())
                d.notify(Alert(severity=Severity.P0,
                               what=f"브래킷 무방비 — {pos.symbol} 잔량 {pos.qty - trim_qty}주 보호 없음",
                               rule="부분 트림(EXEC-6): 브래킷 취소 후 재등록 실패(A1)",
                               action="토스 앱에서 손절 조건주문 즉시 수동 등록",
                               deadline="즉시", created_at=resolved))
            except Exception as exc:  # noqa: BLE001 — 취소 실패: 기존 브래킷 잔존(수량만 불일치)
                store.log(day=day, draft_id=old.id, symbol=pos.symbol, kind="error", mode=mode,
                          detail=f"트림 브래킷 교체 실패(기존 잔존): {exc}"[:200],
                          at=resolved.isoformat())
        position_store.append(pos.model_copy(update={"qty": pos.qty - trim_qty}))
        freed += trim_qty * sell_price
        tag = "부분 회수" if mode == "live" else "부분 회수 (dry-run)"
        d.notify(Alert(severity=Severity.P0,
                       what=f"{tag} — {pos.symbol} {trim_qty}주 @{sell_price:,} (잔량 {pos.qty - trim_qty}주)",
                       rule="부분 트림(EXEC-6): 새 트리거 자금 확보 — 포지션당 최대 50%",
                       action="개입 불필요",
                       deadline="-", created_at=resolved))
    return freed


class BracketGapError(RuntimeError):
    """브래킷 취소는 됐는데 재등록(재시도 포함)까지 실패 — 체결분 무방비(P0 대상, 가드 감사 A1)."""


def _place_bracket(
    toss: TossClient, *, symbol: str, qty: int, stop_trigger: int,
    final_target: int, expire_date: str, client_order_id: str,
) -> str:
    """보호 브래킷 등록(OCO 또는 손절 단독) — 반환=조건주문 id."""
    order_price = stop_order_price(stop_trigger)  # 플로어 = max(2틱, 1%) — 급락 관통 체결(EXEC-8)
    if final_target > stop_trigger:
        res = toss.place_oco_sell(
            symbol, qty, stop_trigger=stop_trigger, stop_price=order_price,
            target_trigger=final_target, target_price=final_target,
            expire_date=expire_date, client_order_id=client_order_id,
        )
    else:
        res = toss.place_stop_sell_conditional(
            symbol, qty, trigger_price=stop_trigger, order_price=order_price,
            expire_date=expire_date, client_order_id=client_order_id,
        )
    return str(res.get("conditionalOrderId") or "")


def _rebracket(
    *,
    toss: TossClient | None,
    mode: str,
    symbol: str,
    draft_id: str,
    old_cond_id: str,
    qty: int,
    stop_trigger: int,
    final_target: int,
    expire_date: str,
    tag: str,
) -> str:
    """브래킷 교체(취소→재등록) — 잔량·본전 상향 반영. 반환=새 조건주문 id(dry-run은 '').

    원자성(가드 감사 A1): 취소 실패는 그대로 예외(기존 보호 잔존 — 이중 등록 금지).
    취소 성공 후 재등록 실패는 1회 즉시 재시도, 그래도 실패면 ``BracketGapError``
    (체결분 무방비 — 호출측이 P0 승격 + bracket_gone 박제)."""
    if mode != "live" or toss is None:
        return ""
    if old_cond_id:
        toss.cancel_conditional(old_cond_id)  # 실패 시 raise — 기존 보호 잔존(이중 등록 금지)
    last: Exception | None = None
    for _attempt in range(2):  # 재등록 1회 즉시 재시도(일시 오류 흡수)
        try:
            return _place_bracket(
                toss, symbol=symbol, qty=qty, stop_trigger=stop_trigger,
                final_target=final_target, expire_date=expire_date,
                client_order_id=f"{tag}-{draft_id}"[:36].replace(".", "-"),
            )
        except Exception as exc:  # noqa: BLE001 — 마지막 실패는 BracketGapError로 승격
            last = exc
    raise BracketGapError(f"브래킷 재등록 실패(취소는 완료 — 무방비): {last}")


def _extract_conditional_ids(raw: object) -> set[str] | None:
    """브로커 조건주문 응답에서 conditionalOrderId 집합 추출(관측 확정 필드만).

    2026-07-14 실호출 관측: 응답 키는 ``conditionalOrders``. 예상 구조(리스트 또는
    {"conditionalOrders"|"items": 리스트})와 다르면 **None(판정 보류)** —
    스키마를 추측해 '브래킷이 사라졌다'고 단정하지 않는다(절대금지 #1)."""
    items: object = raw
    if isinstance(raw, dict):
        items = raw.get("conditionalOrders", raw.get("items"))
    if not isinstance(items, list):
        return None
    ids: set[str] = set()
    for it in items:
        if isinstance(it, dict):
            v = it.get("conditionalOrderId")
            if v is not None:
                ids.add(str(v))
    return ids


def sync_brackets(
    *,
    store: ExecStore,
    mode: str,
    toss: TossClient | None,
    position_store: PositionStore | None,
    dispatcher: AlertDispatcher | None = None,
    now: datetime | None = None,
) -> list[str]:
    """브래킷 생존 대조(가드 감사 A6) — 내부 장부의 브래킷 id가 브로커 조건주문 목록에
    없으면 체결(익절/손절)·취소·만료로 판단하고 ``bracket_gone`` 박제 + P0.

    박제된 초안은 레그·갈아타기·트림 대상에서 제외 — 유령 보유에 매도를 시도하는
    사고(2026-07-14 시나리오 검토에서 적발) 방지. 포지션 자동 마감은 하지 않는다:
    보유 수량 확인(holdings 스키마)이 미확정이라 **알림+중단까지만**(보수). 반환=박제 draft_id."""
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    day = resolved.strftime("%Y%m%d")
    d = dispatcher if dispatcher is not None else AlertDispatcher()
    acted: list[str] = []
    if mode != "live" or toss is None or position_store is None:
        return acted
    open_pos = [p for p in position_store.open_positions() if p.source_ref]
    if not open_pos:
        return acted
    try:
        raw = toss.conditional_orders()
    except Exception:  # noqa: BLE001 — 조회 실패는 다음 패스(판정 보류)
        return acted
    ids = _extract_conditional_ids(raw)
    if ids is None:
        return acted  # 스키마 불명 — 지어내지 않는다
    for pos in open_pos:
        bracket = store.latest_bracket(pos.source_ref, mode=mode)
        if bracket is None or not bracket[0]:
            continue  # 브래킷 미등록(체결 전) — 대조 대상 아님
        if bracket[0] in ids:
            continue  # 생존
        if store.has(pos.source_ref, ("bracket_gone",), mode=mode, day=day):
            continue  # 오늘 이미 박제
        store.log(day=day, draft_id=pos.source_ref, symbol=pos.symbol, kind="bracket_gone",
                  mode=mode, qty=bracket[1], price=bracket[2],
                  detail="브로커 조건주문 목록에 브래킷 부재 — 체결(익절/손절)·취소·만료 추정",
                  at=resolved.isoformat())
        d.notify(Alert(severity=Severity.P0,
                       what=f"브래킷 부재 감지 — {pos.symbol} {bracket[1]}주 (내부 장부는 보유 중)",
                       rule="브래킷 동기화(A6): 익절/손절 체결 또는 취소·만료로 추정",
                       action="토스 앱에서 체결 확인 → 청산 확정 시 /positions 로 정리",
                       deadline="당일", created_at=resolved))
        acted.append(pos.source_ref)
    return acted


# 시간손절 자동 집행 창(운영자 결정 2026-07-14): 14:30~14:50 — 15:00 직전 단타 변수 회피
TIME_STOP_WINDOW: tuple[dt_time, dt_time] = (dt_time(14, 30), dt_time(14, 50))

# 운영자 지시 청산 큐(EXEC-1 잔여 '임의 청산 자동화 없음' 해소, 2026-07-14 밤) —
# CLI(python -m trading.liquidate)로 등록하면 감시기가 세션 창에서 처리.
# 처리 시작은 09:30부터(운영자: "9시에 바로 던지면 변수가 너무 크다" — 시초 변동성 회피).
LIQUIDATE_QUEUE = Path(".runtime") / "exec" / "liquidate.queue"
LIQUIDATE_FROM: dt_time = dt_time(9, 30)


def queue_liquidation(
    draft_ids: Sequence[str], *, queue_file: Path = LIQUIDATE_QUEUE
) -> list[str]:
    """청산 큐 등록(중복 제거) — 반환=새로 등록된 id."""
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    existing: set[str] = set()
    if queue_file.exists():
        existing = {ln.strip() for ln in queue_file.read_text().splitlines() if ln.strip()}
    added = [d for d in draft_ids if d and d not in existing]
    if added:
        with queue_file.open("a", encoding="utf-8") as f:
            for d in added:
                f.write(d + "\n")
    return added


def process_liquidation_queue(
    *,
    store: ExecStore,
    mode: str,
    toss: TossClient | None,
    price_fn: Callable[[str], float | None],
    position_store: PositionStore | None,
    dispatcher: AlertDispatcher | None = None,
    queue_file: Path = LIQUIDATE_QUEUE,
    now: datetime | None = None,
) -> list[str]:
    """청산 큐 처리 — 큐의 초안(source_ref)에 연결된 보유를 전량 지정가 매도.

    시간손절과 같은 A1 규율: 브래킷 취소 실패=중단(보호 유지, 큐 잔류·재시도),
    매도 실패=손절 단독 원복(큐 잔류), 원복 실패=무방비 P0(큐 제거 — 수동 전환).
    미체결 추격은 레그 재호가 루프(kind ``leg_liquidate``). 성공·보유 없음은 큐에서 제거."""
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    day = resolved.strftime("%Y%m%d")
    d = dispatcher if dispatcher is not None else AlertDispatcher()
    acted: list[str] = []
    if mode == "off" or position_store is None or not queue_file.exists():
        return acted
    if resolved.time() < LIQUIDATE_FROM:
        return acted  # 시초 변동성 회피(운영자) — 09:30부터 처리
    ids = [ln.strip() for ln in queue_file.read_text().splitlines() if ln.strip()]
    if not ids:
        return acted
    open_by_ref = {p.source_ref: p for p in position_store.open_positions() if p.source_ref}
    remaining = list(ids)
    live_only = "live" if mode == "live" else None
    for did in ids:
        if store.has(did, ("leg_liquidate",), mode=live_only):
            remaining.remove(did)  # 이미 매도 전송됨(재기동 등) — 추격은 레그 루프 몫
            continue
        pos = open_by_ref.get(did)
        if pos is None:
            remaining.remove(did)  # 보유 없음(기청산·오기입) — 큐 정리
            store.log(day=day, draft_id=did, symbol="-", kind="skip", mode=mode,
                      detail="지시 청산 스킵 — 보유 없음(큐 제거)", at=resolved.isoformat())
            continue
        if store.has(did, ("bracket_gone",), mode=live_only):
            remaining.remove(did)  # 보유 상태 불명 — 자동 매도 금지, 수동 전환(P0 기발송)
            d.notify(Alert(severity=Severity.P1,
                           what=f"지시 청산 보류 — {pos.symbol} (브래킷 상태 불명)",
                           rule="지시 청산: A6 무방비/부재 감지 초안은 자동 매도 금지",
                           action="토스 앱에서 보유·조건주문 확인 후 수동 매도",
                           deadline="당일", created_at=resolved))
            continue
        price = price_fn(pos.symbol)
        if price is None:
            continue  # 관측 불가 — 큐 잔류, 다음 패스
        sell_price = round_down_to_tick(price)
        bracket = store.latest_bracket(did, mode=mode)
        leg_oid = ""
        if mode == "live" and toss is not None:
            if bracket and bracket[0]:
                try:
                    toss.cancel_conditional(bracket[0])
                except Exception as exc:  # noqa: BLE001 — 보호 잔존, 큐 잔류·재시도
                    store.log(day=day, draft_id=did, symbol=pos.symbol, kind="error",
                              mode=mode, detail=f"지시 청산 중단(브래킷 취소 실패): {exc}"[:200],
                              at=resolved.isoformat())
                    continue
            try:
                res = toss.place_limit_order(
                    pos.symbol, "SELL", pos.qty, sell_price,
                    client_order_id=f"liq-{did}"[:36].replace(".", "-"),
                )
                leg_oid = str(res.get("orderId") or "")
            except Exception as exc:  # noqa: BLE001 — 매도 실패: 원복(A1)
                store.log(day=day, draft_id=did, symbol=pos.symbol, kind="error", mode=mode,
                          detail=f"지시 청산 매도 실패: {exc}"[:200], at=resolved.isoformat())
                restored = ""
                if bracket and bracket[0]:
                    try:
                        restored = _place_bracket(
                            toss, symbol=pos.symbol, qty=bracket[1], stop_trigger=bracket[2],
                            final_target=0,
                            expire_date=MarketCalendar.default()
                            .add_trading_days(resolved.date(), 1).isoformat(),
                            client_order_id=f"restore-{did}"[:36].replace(".", "-"),
                        )
                    except Exception:  # noqa: BLE001
                        restored = ""
                if bracket and bracket[0] and restored:
                    store.log(day=day, draft_id=did, symbol=pos.symbol, kind="stop_sent",
                              mode=mode, qty=bracket[1], price=bracket[2], order_id=restored,
                              detail="지시 청산 실패 — 손절 단독 원복(A1)", at=resolved.isoformat())
                elif bracket and bracket[0]:
                    remaining.remove(did)
                    store.log(day=day, draft_id=did, symbol=pos.symbol, kind="bracket_gone",
                              mode=mode, detail="지시 청산 중 무방비(매도·원복 실패)",
                              at=resolved.isoformat())
                    d.notify(Alert(severity=Severity.P0,
                                   what=f"브래킷 무방비 — {pos.symbol} {pos.qty}주 (지시 청산 실패)",
                                   rule="지시 청산: 취소 후 매도·원복 실패(A1)",
                                   action="토스 앱에서 수동 매도 또는 손절 재등록",
                                   deadline="즉시", created_at=resolved))
                continue
        store.log(day=day, draft_id=did, symbol=pos.symbol, kind="leg_liquidate", mode=mode,
                  qty=pos.qty, price=sell_price, order_id=leg_oid or None,
                  at=resolved.isoformat(), detail="운영자 지시 청산(큐)")
        position_store.append(pos.model_copy(update={
            "status": PositionStatus.CLOSED, "close_reason": "운영자 지시 청산(큐)",
        }))
        remaining.remove(did)
        tag = "지시 청산 매도" if mode == "live" else "지시 청산 매도 (dry-run)"
        d.notify(Alert(severity=Severity.P0,
                       what=f"{tag} — {pos.symbol} {pos.qty}주 @{sell_price:,}",
                       rule="지시 청산: 운영자 큐 등록분 자동 매도(브래킷 해제 포함)",
                       action="개입 불필요 — 미체결 시 자동 재호가",
                       deadline="-", created_at=resolved))
        acted.append(did)
    if len(remaining) != len(ids):
        queue_file.write_text("".join(f"{d_}\n" for d_ in remaining), encoding="utf-8")
    return acted


def manage_time_stops(
    *,
    store: ExecStore,
    mode: str,
    toss: TossClient | None,
    price_fn: Callable[[str], float | None],
    position_store: PositionStore | None,
    dispatcher: AlertDispatcher | None = None,
    calendar: MarketCalendar | None = None,
    now: datetime | None = None,
) -> list[str]:
    """시간손절 자동 집행(EXEC-9, 운영자 결정 2026-07-14) — 도래일 14:30~14:50 창에서 잔량 정리.

    기존엔 [정리 검토] 플래그만 뜨고 매도는 수동이었다(가드 감사 D2: 브래킷 만료 후 무방비).
    이제 도래일(놓쳤으면 그 이후 첫 거래일)의 창에서 브래킷 취소→지정가 전량 매도.
    - 취소 실패 = 집행 중단(기존 보호 유지, 다음 패스 재시도) · 매도 실패 = 브래킷 원복(A1).
    - 미체결 추격은 레그 재호가 루프가 담당(kind ``leg_timestop`` — pending_leg_orders 규약).
    반환 = 집행한 draft_id."""
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    day = resolved.strftime("%Y%m%d")
    d = dispatcher if dispatcher is not None else AlertDispatcher()
    acted: list[str] = []
    if mode == "off" or position_store is None:
        return acted
    if not (TIME_STOP_WINDOW[0] <= resolved.time() < TIME_STOP_WINDOW[1]):
        return acted
    cal = calendar if calendar is not None else MarketCalendar.default()
    live_only = "live" if mode == "live" else None
    for pos in position_store.open_positions():
        if not pos.time_stop_days or not pos.source_ref:
            continue
        expiry = cal.add_trading_days(pos.as_of.astimezone(KST).date(), pos.time_stop_days)
        if resolved.date() < expiry:
            continue
        draft_id = pos.source_ref
        if store.has(draft_id, ("leg_timestop",), mode=live_only):
            continue  # 이미 집행(추격은 레그 루프 몫)
        if store.has(draft_id, ("bracket_gone",), mode=live_only):
            continue  # 보유 상태 불명(A6) — 자동 매도 금지, P0로 수동 유도됨
        price = price_fn(pos.symbol)
        if price is None:
            continue  # 관측 불가 — 값을 지어내지 않는다, 다음 패스
        sell_price = round_down_to_tick(price)
        bracket = store.latest_bracket(draft_id, mode=mode)
        leg_oid = ""
        if mode == "live" and toss is not None:
            if bracket and bracket[0]:
                try:
                    toss.cancel_conditional(bracket[0])
                except Exception as exc:  # noqa: BLE001 — 보호 잔존, 다음 패스 재시도
                    store.log(day=day, draft_id=draft_id, symbol=pos.symbol, kind="error",
                              mode=mode, detail=f"시간손절 중단(브래킷 취소 실패): {exc}"[:200],
                              at=resolved.isoformat())
                    continue
            try:
                res = toss.place_limit_order(
                    pos.symbol, "SELL", pos.qty, sell_price,
                    client_order_id=f"tstop-{draft_id}"[:36].replace(".", "-"),
                )
                leg_oid = str(res.get("orderId") or "")
            except Exception as exc:  # noqa: BLE001 — 매도 실패: 브래킷 원복(A1)
                store.log(day=day, draft_id=draft_id, symbol=pos.symbol, kind="error",
                          mode=mode, detail=f"시간손절 매도 실패: {exc}"[:200],
                          at=resolved.isoformat())
                if bracket and bracket[0]:
                    try:
                        restored = _place_bracket(
                            toss, symbol=pos.symbol, qty=bracket[1], stop_trigger=bracket[2],
                            final_target=0,
                            expire_date=cal.add_trading_days(resolved.date(), 1).isoformat(),
                            client_order_id=f"restore-{draft_id}"[:36].replace(".", "-"),
                        )
                    except Exception:  # noqa: BLE001
                        restored = ""
                    if restored:
                        store.log(day=day, draft_id=draft_id, symbol=pos.symbol,
                                  kind="stop_sent", mode=mode, qty=bracket[1], price=bracket[2],
                                  order_id=restored, detail="시간손절 실패 — 손절 단독 원복(A1)",
                                  at=resolved.isoformat())
                    else:
                        store.log(day=day, draft_id=draft_id, symbol=pos.symbol,
                                  kind="bracket_gone", mode=mode,
                                  detail="시간손절 중 무방비(매도·원복 실패)",
                                  at=resolved.isoformat())
                        d.notify(Alert(severity=Severity.P0,
                                       what=f"브래킷 무방비 — {pos.symbol} {pos.qty}주 (시간손절 실패)",
                                       rule="시간손절(EXEC-9): 취소 후 매도·원복 실패(A1)",
                                       action="토스 앱에서 수동 매도 또는 손절 재등록",
                                       deadline="즉시", created_at=resolved))
                continue
        store.log(day=day, draft_id=draft_id, symbol=pos.symbol, kind="leg_timestop",
                  mode=mode, qty=pos.qty, price=sell_price, order_id=leg_oid or None,
                  at=resolved.isoformat(),
                  detail=f"시간손절 집행(도래 {expiry.isoformat()}, 14:30~14:50 창)")
        position_store.append(pos.model_copy(update={
            "status": PositionStatus.CLOSED,
            "close_reason": f"시간손절({pos.time_stop_days}거래일) 자동 집행",
        }))
        tag = "시간손절 매도" if mode == "live" else "시간손절 매도 (dry-run)"
        d.notify(Alert(severity=Severity.P0,
                       what=f"{tag} — {pos.symbol} {pos.qty}주 @{sell_price:,} (도래 {expiry.isoformat()})",
                       rule="시간손절(EXEC-9): 도래일 14:30~14:50 창 자동 정리 — 미진행 셋업 회수",
                       action="개입 불필요 — 미체결 시 자동 재호가",
                       deadline="-", created_at=resolved))
        acted.append(draft_id)
    return acted


def manage_exits(
    *,
    store: ExecStore,
    mode: str,
    toss: TossClient | None,
    drafts_by_id: dict[str, OrderDraft],
    price_fn: "Callable[[str], float | None]",
    position_store: PositionStore | None = None,
    dispatcher: AlertDispatcher | None = None,
    calendar: MarketCalendar | None = None,
    now: datetime | None = None,
) -> list[str]:
    """계단식 청산 관리(EXEC-2) — 감시 패스마다 부분 익절·경고 축소·본전 상향을 집행.

    브로커에는 항상 전량 보호 브래킷(하드스탑+최종타깃 OCO)이 상주하고, 이 함수는
    부분 레그(마지막 前 타깃들·soft_stop)를 지정가로 직접 팔며 브래킷을 잔량으로 교체한다.
    - 익절 레그 체결 시 **하드스탑을 본전(체결가, 틱 절사)으로 상향** — 운영자 결정.
    - 레그는 초안·레그당 1회(저널 dedup), 패스당 종목당 1레그(과속 방지).
    - 잔량 2주 미만이면 사다리 강등(브래킷 단독) — 소액 계좌 현실.
    반환 = 이번 패스에 레그를 집행한 draft_id.
    """
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    day = resolved.strftime("%Y%m%d")
    d = dispatcher if dispatcher is not None else AlertDispatcher()
    cal = calendar if calendar is not None else MarketCalendar.default()
    acted: list[str] = []
    if mode == "off" or position_store is None:
        return acted
    live_only = "live" if mode == "live" else None
    # 0) 레그 매도 체결 확인(v1.1) — 미체결이면 취소 후 현재가로 재호가(패스당 1회씩 수렴)
    if mode == "live" and toss is not None:
        for l_draft, l_symbol, l_kind, l_oid, l_qty, l_price in store.pending_leg_orders(mode=mode):
            try:
                o = toss.order(l_oid)
            except Exception:  # noqa: BLE001 — 조회 실패는 다음 패스
                continue
            status = str(o.get("status") or "")
            if status in ("FILLED", "PARTIAL_FILLED"):
                store.log(day=day, draft_id=l_draft, symbol=l_symbol, kind="leg_fill",
                          mode=mode, qty=l_qty, detail=l_kind, at=resolved.isoformat())
                continue
            if status in ("PENDING", "REPLACED"):
                cur = price_fn(l_symbol)
                if cur is None:
                    continue
                new_price = round_down_to_tick(cur)
                if abs(new_price - l_price) < tick_size(l_price):
                    continue  # 호가 근처 — 대기
                try:  # 가격이 벗어남 — 취소 후 현재가 재호가
                    toss.cancel_order(l_oid)
                    res_o = toss.place_limit_order(
                        l_symbol, "SELL", l_qty, new_price,
                        client_order_id=f"rq-{l_kind}-{l_draft}"[:36].replace(".", "-"),
                    )
                    store.log(day=day, draft_id=l_draft, symbol=l_symbol, kind=l_kind,
                              mode=mode, qty=l_qty, price=new_price,
                              order_id=str(res_o.get("orderId") or ""),
                              detail=f"재호가({l_price:,}→{new_price:,})", at=resolved.isoformat())
                except Exception as exc:  # noqa: BLE001 — 재호가 실패는 다음 패스 재시도
                    store.log(day=day, draft_id=l_draft, symbol=l_symbol, kind="error",
                              mode=mode, detail=f"레그 재호가 실패: {exc}"[:200],
                              at=resolved.isoformat())
            elif status in ("CANCELED", "REJECTED"):
                d.notify(Alert(severity=Severity.P1,
                               what=f"레그 매도 {status} — {l_symbol} {l_qty}주 ({l_kind})",
                               rule="계단식 청산(EXEC-2): 부분 매도 미집행 상태",
                               action="토스 앱에서 잔량·조건주문 확인",
                               deadline="당일", created_at=resolved))
                store.log(day=day, draft_id=l_draft, symbol=l_symbol, kind="leg_fill",
                          mode=mode, qty=0, detail=l_kind, at=resolved.isoformat())
    for pos in position_store.open_positions():
        draft = drafts_by_id.get(pos.source_ref)
        if draft is None or draft.side.value != "buy":
            continue
        # 브래킷 부재 확정(체결·취소·무방비) 초안은 레그 중단(A6) — 유령 매도 시도 방지
        if store.has(draft.id, ("bracket_gone",), mode=live_only):
            continue
        partial_targets = draft.targets[:-1]  # 최종 타깃은 브래킷 몫
        soft = draft.soft_stop
        if not partial_targets and soft is None:
            continue
        bracket = store.latest_bracket(draft.id, mode=mode)
        if bracket is None:
            continue  # 브래킷 미등록(체결 전) — reconcile 이후에만 관리
        cond_id, rem_qty, cur_trigger = bracket
        if rem_qty < 2:
            continue  # 사다리 불가 — 브래킷 단독 유지
        price = price_fn(pos.symbol)
        if price is None:
            continue
        final_target = (
            round_down_to_tick(draft.targets[-1].level) if draft.targets else 0
        )
        expiry = cal.add_trading_days(resolved.date(), (draft.time_stop_days or 20) + 1)  # EXEC-9 +1

        leg_key = ""
        leg_qty = 0
        new_trigger = cur_trigger
        reason = ""
        for i, t in enumerate(partial_targets):
            key = f"leg_t{i + 1}"
            if store.has(draft.id, (key,), mode=live_only):
                continue
            if price >= t.level:
                leg_key = key
                leg_qty = min(max(pos.qty * t.pct // 100, 1), rem_qty - 1)
                # 본전 상향(결정론): 하드스탑 → max(기존, 체결가 틱 절사)
                new_trigger = max(cur_trigger, round_down_to_tick(pos.avg_price))
                reason = f"익절{i + 1}({t.level:,.0f} 도달) — 잔량 손절 본전 상향"
            break  # 패스당 1레그
        if not leg_key and soft is not None and not store.has(draft.id, ("leg_soft",), mode=live_only):
            if price <= soft.level:
                leg_key = "leg_soft"
                leg_qty = min(max(pos.qty * soft.pct // 100, 1), rem_qty - 1)
                reason = f"경고 축소({soft.level:,.0f} 이탈) — 하드스탑 전 선제 감축"
        if not leg_key or leg_qty < 1:
            continue

        sell_price = round_down_to_tick(price)
        leg_order_id = ""
        try:
            if mode == "live" and toss is not None:
                res_leg = toss.place_limit_order(
                    pos.symbol, "SELL", leg_qty, sell_price,
                    client_order_id=f"{leg_key}-{draft.id}"[:36].replace(".", "-"),
                )
                leg_order_id = str(res_leg.get("orderId") or "")
        except Exception as exc:  # noqa: BLE001 — 레그 주문 실패: 브래킷 미접촉(보호 유지), 다음 패스 재시도
            store.log(day=day, draft_id=draft.id, symbol=pos.symbol, kind="error", mode=mode,
                      detail=f"{leg_key} 레그 주문 실패: {exc}"[:300], at=resolved.isoformat())
            d.notify(Alert(severity=Severity.P1,
                           what=f"청산 레그 실패 — {pos.symbol} {leg_key} {leg_qty}주",
                           rule="계단식 청산(EXEC-2): 부분 매도 주문 오류",
                           action="토스 앱에서 조건주문 상태 확인",
                           deadline="당일", created_at=resolved))
            continue
        # 레그는 전송 즉시 박제 — 이후 브래킷 교체가 실패해도 다음 패스 이중 매도 금지(A1 부수 결함)
        store.log(day=day, draft_id=draft.id, symbol=pos.symbol, kind=leg_key, mode=mode,
                  qty=leg_qty, price=sell_price, order_id=leg_order_id or None,
                  at=resolved.isoformat(), detail=reason)
        try:
            new_cond = _rebracket(
                toss=toss, mode=mode, symbol=pos.symbol, draft_id=draft.id,
                old_cond_id=cond_id, qty=rem_qty - leg_qty,
                stop_trigger=new_trigger, final_target=final_target,
                expire_date=expiry.isoformat(), tag=leg_key,
            )
        except BracketGapError as exc:  # A1: 취소 후 재등록 실패 — 무방비 확정, P0 + 박제
            store.log(day=day, draft_id=draft.id, symbol=pos.symbol, kind="bracket_gone",
                      mode=mode, detail=f"{leg_key} 교체 중 무방비: {exc}"[:300],
                      at=resolved.isoformat())
            d.notify(Alert(severity=Severity.P0,
                           what=f"브래킷 무방비 — {pos.symbol} 잔량 {rem_qty - leg_qty}주 보호 없음",
                           rule="계단식 청산(EXEC-2): 브래킷 취소 후 재등록 실패(A1)",
                           action="토스 앱에서 손절 조건주문 즉시 수동 등록",
                           deadline="즉시", created_at=resolved))
            continue
        except Exception as exc:  # noqa: BLE001 — 취소 실패: 기존 브래킷 잔존(보호 유지)
            store.log(day=day, draft_id=draft.id, symbol=pos.symbol, kind="error", mode=mode,
                      detail=f"{leg_key} 브래킷 교체 실패(기존 잔존): {exc}"[:300],
                      at=resolved.isoformat())
            d.notify(Alert(severity=Severity.P1,
                           what=f"브래킷 교체 실패 — {pos.symbol} (기존 브래킷 유지 추정, 잔량 불일치)",
                           rule="계단식 청산(EXEC-2): 조건주문 취소 오류",
                           action="토스 앱에서 조건주문 수량·레벨 확인",
                           deadline="당일", created_at=resolved))
            continue
        store.log(day=day, draft_id=draft.id, symbol=pos.symbol,
                  kind="stop_sent" if mode == "live" else "stop_intent", mode=mode,
                  qty=rem_qty - leg_qty, price=new_trigger, order_id=new_cond or None,
                  at=resolved.isoformat(),
                  detail=f"브래킷 교체({leg_key}) · 최종타깃 {final_target:,}" if final_target else f"브래킷 교체({leg_key})")
        tag_txt = "매도" if mode == "live" else "매도 (dry-run)"
        d.notify(Alert(severity=Severity.P0,
                       what=f"{tag_txt} — {pos.symbol} {leg_qty}주 @{sell_price:,} ({reason})",
                       rule="계단식 청산(EXEC-2): 부분 레그 + 브래킷 재구성",
                       action=f"개입 불필요 — 잔량 {rem_qty - leg_qty}주, 손절 {new_trigger:,}",
                       deadline="-", created_at=resolved))
        acted.append(draft.id)
    return acted


__all__ = [
    "BracketGapError", "DEFAULT_DB", "KILL_FILE", "ExecPolicy", "ExecResult", "ExecStore",
    "cap_fraction", "consider_rotation", "derive_entry_band", "exec_mode", "execute_armed",
    "manage_exits", "manage_time_stops", "planned_upside_pct",
    "process_liquidation_queue", "queue_liquidation", "reconcile",
    "round_down_to_tick", "stop_order_price", "sync_brackets", "tick_size", "trim_for_shortfall",
]
