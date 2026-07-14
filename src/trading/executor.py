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
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
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

    def has(self, draft_id: str, kinds: tuple[str, ...], *, mode: str | None = None) -> bool:
        """mode='live'면 live 행만 본다 — dry-run 잔재가 live 판단(dedup 등)을 오염시키지 않게
        (2026-07-14 전환 사고: dry-run order_intent가 live 재진입을 차단)."""
        q = ",".join("?" for _ in kinds)
        sql = f"SELECT 1 FROM exec_log WHERE draft_id=? AND kind IN ({q})"
        params: tuple[str, ...] = (draft_id, *kinds)
        if mode:
            sql += " AND mode=?"
            params = (*params, mode)
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

    def committed_krw(self) -> int:
        """진입 시도액 − 교체 매도액(dry-run 가용 잔고 근사 — 레그·스탑 청산은 보수적 미반영)."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN kind IN ('order_intent','order_sent') THEN qty*price"
            "                          WHEN kind IN ('rotation_sell','trim_sell') THEN -(qty*price)"
            "                          ELSE 0 END),0)"
            " FROM exec_log"
        ).fetchone()
        return int(row[0]) if row else 0

    def cash_skips_today(self, day: str) -> list[str]:
        """오늘 '잔고 부족'으로 스킵됐고 아직 미집행인 초안 — 매 패스 재시도 대상(EXEC-4)."""
        cur = self._conn.execute(
            "SELECT DISTINCT draft_id FROM exec_log e WHERE day=? AND kind='skip'"
            " AND detail LIKE '잔고 부족%'"
            " AND NOT EXISTS (SELECT 1 FROM exec_log o WHERE o.draft_id=e.draft_id"
            "                 AND o.kind IN ('order_intent','order_sent'))",
            (day,),
        )
        return [str(r[0]) for r in cur]

    def rotations_today(self, day: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM exec_log WHERE day=? AND kind='rotation_sell'", (day,)
        ).fetchone()
        return int(row[0]) if row else 0

    def open_symbols(self) -> set[str]:
        cur = self._conn.execute(
            "SELECT DISTINCT symbol FROM exec_log WHERE kind IN ('order_intent','order_sent')"
        )
        return {str(r[0]) for r in cur}

    def pending_fills(self) -> list[tuple[str, str, str, int, int]]:
        """스탑 미등록 주문 — (draft_id, symbol, order_id, qty, price). dry-run 포함."""
        cur = self._conn.execute(
            "SELECT draft_id, symbol, COALESCE(order_id,''), qty, price FROM exec_log"
            " WHERE kind IN ('order_intent','order_sent')"
            " AND draft_id NOT IN (SELECT draft_id FROM exec_log WHERE kind IN ('stop_intent','stop_sent','skip_stop'))"
        )
        return [(str(r[0]), str(r[1]), str(r[2]), int(r[3]), int(r[4])) for r in cur]

    def pending_leg_orders(self) -> list[tuple[str, str, str, str, int, int]]:
        """미확인 레그 매도 — (draft_id, symbol, leg_kind, order_id, qty, price).

        레그별 최신 주문 행 기준, 'leg_fill'(detail=leg_kind) 해소 전까지 추적."""
        cur = self._conn.execute(
            "SELECT draft_id, symbol, kind, order_id, qty, price FROM exec_log e"
            " WHERE kind LIKE 'leg_%' AND kind != 'leg_fill' AND order_id IS NOT NULL AND order_id != ''"
            " AND row_id = (SELECT MAX(row_id) FROM exec_log e2"
            "               WHERE e2.draft_id = e.draft_id AND e2.kind = e.kind AND e2.order_id IS NOT NULL)"
            " AND NOT EXISTS (SELECT 1 FROM exec_log f"
            "                 WHERE f.draft_id = e.draft_id AND f.kind = 'leg_fill' AND f.detail = e.kind)"
        )
        return [
            (str(r[0]), str(r[1]), str(r[2]), str(r[3]), int(r[4]), int(r[5])) for r in cur
        ]

    def latest_bracket(self, draft_id: str) -> tuple[str, int, int] | None:
        """현재 브래킷 상태 — (조건주문 id, 잔량, 손절 트리거). 미등록이면 None."""
        row = self._conn.execute(
            "SELECT COALESCE(order_id,''), qty, price FROM exec_log"
            " WHERE draft_id=? AND kind IN ('stop_intent','stop_sent')"
            " ORDER BY row_id DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
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
    # live는 live 기록만 dedup — dry-run 흔적(intent)이 실진입을 막으면 안 된다(7/14 전환 사고)
    if store.has(draft.id, ("order_intent", "order_sent"),
                 mode="live" if mode == "live" else None):
        return ExecResult("skipped", "이미 집행됨(초안당 1회)")

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
    available = policy.account_krw - store.committed_krw()
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
    # 셋업 붕괴 가드: 현재가가 이미 손절 레벨 이하 — 사자마자 스탑 트리거되는 진입 차단
    # (R5는 T-1 EOD 기준 계획 — 익일 급락 시 계획 전제가 깨진 상태)
    if draft.stop and draft.stop.level and limit_price <= draft.stop.level:
        return _skip(
            f"셋업 붕괴(현재가 {limit_price:,} ≤ 손절 {draft.stop.level:,.0f}) — 진입 금지"
        )
    # 셋업 소진 가드(2026-07-13 폭락일 실사례: 한국콜마 현재가 > 익절 레벨): 이미 1차
    # 익절 레벨 이상이면 계획된 상승분이 소진된 것 — 사자마자 익절 트리거·기대 소멸 차단
    if draft.targets and limit_price >= draft.targets[0].level:
        return _skip(
            f"셋업 소진(현재가 {limit_price:,} ≥ 익절1 {draft.targets[0].level:,.0f}) — 진입 금지"
        )
    # 잔여 R:R 가드(운영자 지적 2026-07-14: "9,999에 사서 10,000에 파는" 진입 차단):
    # 소진 가드는 이진 판정이라 익절 직전 진입을 못 막는다 — 진입가 기준 최종 타깃까지의
    # 보상이 손절까지의 위험 대비 EXEC_MIN_RR(기본 1.0) 미만이면 스킵
    if draft.stop and draft.stop.level and limit_price > draft.stop.level:
        up = planned_upside_pct(draft, float(limit_price))
        dn = (limit_price - draft.stop.level) / limit_price
        if dn > 0 and up / dn < _min_rr():
            return _skip(
                f"잔여 R:R 부족({up / dn:.2f} < {_min_rr():g}) — "
                f"익절 근접 진입 차단(현재가 {limit_price:,})"
            )
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
) -> list[str]:
    """미체결 추적 → 체결 시 손절 조건주문 등록 + 포지션 박제. 반환=처리된 draft_id."""
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    day = resolved.strftime("%Y%m%d")
    d = dispatcher if dispatcher is not None else AlertDispatcher()
    cal = calendar if calendar is not None else MarketCalendar.default()
    done: list[str] = []
    for draft_id, symbol, order_id, qty, price in store.pending_fills():
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
            if status not in ("FILLED", "PARTIAL_FILLED"):
                continue
            ex = o.get("execution") or {}
            try:
                filled_qty = int(float(str(ex.get("filledQuantity") or 0)))
                avg = float(str(ex.get("averagePrice") or price))
            except ValueError:
                filled_qty, avg = qty, float(price)
            if filled_qty < 1:
                continue
        # dry-run은 발동가 체결 가정 — 즉시 청산 조건 등록 시뮬레이션
        stop_level = draft.stop.level if draft.stop else None
        target_txt = ""
        if stop_level:
            trigger = round_down_to_tick(stop_level)
            order_price = round_down_to_tick(trigger - 2 * tick_size(trigger))  # 체결 확률용 2틱 아래
            expiry = cal.add_trading_days(resolved.date(), draft.time_stop_days or 20)
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
    if position_store is None or store.rotations_today(day) >= 1:
        return False
    new_up = planned_upside_pct(new_draft, new_price)
    if new_up <= 0.0:
        return False
    scored: list[tuple[float, Any, OrderDraft, float]] = []
    for pos in position_store.open_positions():
        old = drafts_by_id.get(pos.source_ref)
        if old is None or old.id == new_draft.id:
            continue
        if store.has(old.id, ("leg_t1",)):
            continue  # 러너 보호
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
    bracket = store.latest_bracket(old.id)
    try:
        if mode == "live" and toss is not None:
            if bracket and bracket[0]:
                toss.cancel_conditional(bracket[0])  # 브래킷 해제 후 전량 매도
            toss.place_limit_order(
                pos.symbol, "SELL", pos.qty, sell_price,
                client_order_id=f"rot-{old.id}"[:36].replace(".", "-"),
            )
    except Exception as exc:  # noqa: BLE001 — 실패 시 교체 중단(기존 보호 유지 시도)
        store.log(day=day, draft_id=old.id, symbol=pos.symbol, kind="error", mode=mode,
                  detail=f"교체 매도 실패: {exc}"[:200], at=resolved.isoformat())
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
    ranked: list[tuple[float, Any, OrderDraft, float]] = []
    for pos in position_store.open_positions():
        old = drafts_by_id.get(pos.source_ref)
        if old is None or store.has(old.id, ("leg_t1",)) or pos.qty < 2:
            continue  # 러너 보호 · 1주 포지션 트림 불가
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
        bracket = store.latest_bracket(old.id)
        try:
            if mode == "live" and toss is not None:
                toss.place_limit_order(
                    pos.symbol, "SELL", trim_qty, sell_price,
                    client_order_id=f"trim-{old.id}"[:36].replace(".", "-"),
                )
            expiry = cal.add_trading_days(resolved.date(), old.time_stop_days or 20)
            stop_trigger = round_down_to_tick(old.stop.level) if old.stop and old.stop.level else 0
            final_t = round_down_to_tick(old.targets[-1].level) if old.targets else 0
            if bracket and stop_trigger:
                _rebracket(
                    toss=toss, mode=mode, symbol=pos.symbol, draft_id=old.id,
                    old_cond_id=bracket[0], qty=pos.qty - trim_qty,
                    stop_trigger=stop_trigger, final_target=final_t,
                    expire_date=expiry.isoformat(), tag="trim",
                )
        except Exception as exc:  # noqa: BLE001 — 실패는 다음 후보로(기존 보호 유지)
            store.log(day=day, draft_id=old.id, symbol=pos.symbol, kind="error", mode=mode,
                      detail=f"트림 실패: {exc}"[:200], at=resolved.isoformat())
            continue
        store.log(day=day, draft_id=old.id, symbol=pos.symbol, kind="trim_sell", mode=mode,
                  qty=trim_qty, price=sell_price, at=resolved.isoformat(),
                  detail=f"부분 회수(잔여 여력 {rem_up:.1%}) — 신규 트리거 자금")
        position_store.append(pos.model_copy(update={"qty": pos.qty - trim_qty}))
        freed += trim_qty * sell_price
        tag = "부분 회수" if mode == "live" else "부분 회수 (dry-run)"
        d.notify(Alert(severity=Severity.P0,
                       what=f"{tag} — {pos.symbol} {trim_qty}주 @{sell_price:,} (잔량 {pos.qty - trim_qty}주)",
                       rule="부분 트림(EXEC-6): 새 트리거 자금 확보 — 포지션당 최대 50%",
                       action="개입 불필요",
                       deadline="-", created_at=resolved))
    return freed


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
    """브래킷 교체(취소→재등록) — 잔량·본전 상향 반영. 반환=새 조건주문 id(dry-run은 '')."""
    if mode != "live" or toss is None:
        return ""
    if old_cond_id:
        try:
            toss.cancel_conditional(old_cond_id)
        except Exception:  # noqa: BLE001 — 취소 실패 시 이중 등록 위험 → 재등록 중단(기존 보호 유지)
            raise
    order_price = round_down_to_tick(stop_trigger - 2 * tick_size(stop_trigger))
    if final_target > stop_trigger:
        res = toss.place_oco_sell(
            symbol, qty, stop_trigger=stop_trigger, stop_price=order_price,
            target_trigger=final_target, target_price=final_target,
            expire_date=expire_date,
            client_order_id=f"{tag}-{draft_id}"[:36].replace(".", "-"),
        )
    else:
        res = toss.place_stop_sell_conditional(
            symbol, qty, trigger_price=stop_trigger, order_price=order_price,
            expire_date=expire_date,
            client_order_id=f"{tag}-{draft_id}"[:36].replace(".", "-"),
        )
    return str(res.get("conditionalOrderId") or "")


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
    # 0) 레그 매도 체결 확인(v1.1) — 미체결이면 취소 후 현재가로 재호가(패스당 1회씩 수렴)
    if mode == "live" and toss is not None:
        for l_draft, l_symbol, l_kind, l_oid, l_qty, l_price in store.pending_leg_orders():
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
        partial_targets = draft.targets[:-1]  # 최종 타깃은 브래킷 몫
        soft = draft.soft_stop
        if not partial_targets and soft is None:
            continue
        bracket = store.latest_bracket(draft.id)
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
        expiry = cal.add_trading_days(resolved.date(), draft.time_stop_days or 20)

        leg_key = ""
        leg_qty = 0
        new_trigger = cur_trigger
        reason = ""
        for i, t in enumerate(partial_targets):
            key = f"leg_t{i + 1}"
            if store.has(draft.id, (key,)):
                continue
            if price >= t.level:
                leg_key = key
                leg_qty = min(max(pos.qty * t.pct // 100, 1), rem_qty - 1)
                # 본전 상향(결정론): 하드스탑 → max(기존, 체결가 틱 절사)
                new_trigger = max(cur_trigger, round_down_to_tick(pos.avg_price))
                reason = f"익절{i + 1}({t.level:,.0f} 도달) — 잔량 손절 본전 상향"
            break  # 패스당 1레그
        if not leg_key and soft is not None and not store.has(draft.id, ("leg_soft",)):
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
            new_cond = _rebracket(
                toss=toss, mode=mode, symbol=pos.symbol, draft_id=draft.id,
                old_cond_id=cond_id, qty=rem_qty - leg_qty,
                stop_trigger=new_trigger, final_target=final_target,
                expire_date=expiry.isoformat(), tag=leg_key,
            )
        except Exception as exc:  # noqa: BLE001 — 실패는 기록+보고, 기존 브래킷 보호 유지
            store.log(day=day, draft_id=draft.id, symbol=pos.symbol, kind="error", mode=mode,
                      detail=f"{leg_key} 집행 실패: {exc}"[:300], at=resolved.isoformat())
            d.notify(Alert(severity=Severity.P1,
                           what=f"청산 레그 실패 — {pos.symbol} {leg_key} {leg_qty}주",
                           rule="계단식 청산(EXEC-2): 레그 주문/브래킷 교체 오류",
                           action="토스 앱에서 조건주문 상태 확인",
                           deadline="당일", created_at=resolved))
            continue
        store.log(day=day, draft_id=draft.id, symbol=pos.symbol, kind=leg_key, mode=mode,
                  qty=leg_qty, price=sell_price, order_id=leg_order_id or None,
                  at=resolved.isoformat(), detail=reason)
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
    "DEFAULT_DB", "KILL_FILE", "ExecPolicy", "ExecResult", "ExecStore",
    "cap_fraction", "consider_rotation", "exec_mode", "execute_armed",
    "manage_exits", "planned_upside_pct", "reconcile",
    "round_down_to_tick", "tick_size", "trim_for_shortfall",
]
