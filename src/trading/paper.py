"""페이퍼 투자(v2.5 — 운영자 지시 2026-09-01) — `python -m trading.paper`.

실제 투자 전 테스트: 심사 승인 종목을 100주 일괄 보유로 등록하고(v2.8), 분할
매도(상승 시)·정리 규칙을 EOD 종가로 결정론 시뮬레이션한다.

- **실주문 없음** — EXEC와 완전 무관한 페이퍼 원장(절대금지 3과 무관). 계측·학습 전용.
- 규칙(PaperParams)은 가치투자·사이클투자 문헌 조사로 캘리브레이션(POLICY §7 v2.5~).
- 트리거는 **등록 시점에 전부 박제**된다: 매수 상한 = 기준가→정리가의 1/3 지점
  (실매수 규율 지표), 매도 가이드 = 목표가(등록 시점 섹터 중앙 PBR 도달가 = 회귀
  여력 소진점) 배수 사다리, 정리 = 목표가 150% 도달 또는 이익 보호(90% 이상 매도선
  터치 후 직전 선 이탈 — v2.9) 또는 시간 상한 또는 심사 veto.
- 체결은 일별 EOD 종가가 트리거를 넘은 첫날 그 종가로 기록(장중가 없음 — 정직한 근사).
- append-only: 포지션·체결 모두 새 행으로만. 수익률 = (실현 현금 + 평가액) / 투입 원금.
"""

import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from trading.collectors.base import now_kst

DEFAULT_DB = Path("data") / "paper.sqlite"

# 등록 자격 하한(운영자 지적 2026-09-01: "1% 먹으려고 하락 리스크를 지는 건 아니다"):
# 회귀 여력 ≥ +30% — 안전마진 원칙(Tweedy Browne는 내재가치 60~70%에서 매수 = 여력
# 43%+, 실무 플레이북 -30% 기준의 보수적 하한).
MIN_UPSIDE_PCT = 30.0


@dataclass(frozen=True)
class PaperParams:
    """분할 매매 규칙 — 문헌 캘리브레이션(v2.5, docs/POLICY_PARAMS.md §7).

    capital: 종목당 가상 원금(원). initial_pct: 등록 즉시 투입 비중.
    add_levels: (기준가 대비 하락률, 투입 비중) 사다리 — 하락할수록 싸게 더 산다.
    sell_levels: (목표가까지 갭 충족률, 보유 수량 대비 매도 비중) 사다리.
    time_horizon_days: 가치 실현 대기 상한 — 초과 시 정리 표기(강제 청산은 운영자 몫).
    """

    # v2.8(운영자 지시 2026-09-02): 등록 시 100주 일괄 보유 — 분할 매수 사다리 폐지.
    # 매수 상한(진행률 1/3 지점)은 실매수 규율 지표로 유지(가격 < 상한 = 추가 매수 가능).
    initial_qty: float = 100.0
    # (기준가 대비 하락률, 매수 주수) — v2.8: 빈 사다리(일괄 보유)
    add_levels: tuple[tuple[float, float], ...] = ()
    # 매수 상한 = min(기준가→**목표가**의 1/3 지점, 첫 매도가) — 운영자 확정(2026-09-02):
    # 정리가 앵커는 정리가를 올릴수록 상한이 따라 올라가는 결합이 있어 목표가 앵커로.
    # min 가드는 저여력(+30~43%) 포지션에서 첫 매도선(80%)이 1/3 지점 아래로 오는
    # 존 겹침을 막는다.
    time_tranche_days: int = 56           # 8주 — v2.8 이후 사다리 없음(미사용 보존)
    buy_zone_progress: float = 1 / 3      # 매수 존 상한(기준가→목표가 여정 기준)
    # (목표가 대비 배수, 보유 수량 대비 매도 비중 — 정수 주로 내림)
    # 운영자 확정(2026-09-02): 80·90·100·120·150에서 각 20주씩 정리.
    # 비중은 "그 시점 보유 대비"라 20주 등가가 되도록 0.20 → 0.25 → 1/3 → 0.50,
    # 150%는 잔량 전량(=20주). 초반 촘촘·후반 성김 — 과열 시 이익 보전.
    sell_levels: tuple[tuple[float, float], ...] = (
        (0.80, 0.20), (0.90, 0.25), (1.00, 1 / 3), (1.20, 0.50),
    )
    final_exit_multiple: float = 1.50     # 잔량 전량 정리 지점(목표가 대비)
    warn_days: int = 730                  # 2년 정체 경고(그레이엄)
    time_horizon_days: int = 1095         # 3년 미수렴 청산(파브라이·드레먼·LSV 실증)


PROPOSED_PAPER = PaperParams()


@dataclass(frozen=True)
class Fill:
    trigger: str
    side: str
    bas_dt: str
    price: float
    qty: float
    amount: float


@dataclass(frozen=True)
class PositionRow:
    symbol: str
    opened_bas_dt: str
    base_price: float
    target_price: float
    params: PaperParams
    status: str
    closed_reason: str | None
    cycle: int = 0                        # 사이클 = 이전 청산 횟수(v2.10)


def _params_from_json(raw: str) -> PaperParams:
    d = json.loads(raw)
    return PaperParams(
        initial_qty=float(d["initial_qty"]),
        add_levels=tuple((float(a), float(b)) for a, b in d["add_levels"]),
        sell_levels=tuple((float(a), float(b)) for a, b in d["sell_levels"]),
        final_exit_multiple=float(d.get("final_exit_multiple", 1.15)),
        warn_days=int(d.get("warn_days", 730)),
        time_horizon_days=int(d["time_horizon_days"]),
        time_tranche_days=int(d.get("time_tranche_days", 56)),
        buy_zone_progress=float(d.get("buy_zone_progress", 1 / 3)),
    )

_DDL = """
CREATE TABLE IF NOT EXISTS positions (
  symbol TEXT NOT NULL, version INTEGER NOT NULL,
  opened_bas_dt TEXT NOT NULL, base_price REAL NOT NULL, target_price REAL NOT NULL,
  params TEXT NOT NULL, status TEXT NOT NULL, closed_reason TEXT,
  appended_at TEXT NOT NULL,
  UNIQUE(symbol, version)
);
CREATE TABLE IF NOT EXISTS fills (
  symbol TEXT NOT NULL, cycle INTEGER NOT NULL DEFAULT 0,
  trigger TEXT NOT NULL, side TEXT NOT NULL,
  bas_dt TEXT NOT NULL, price REAL NOT NULL, qty REAL NOT NULL, amount REAL NOT NULL,
  appended_at TEXT NOT NULL,
  UNIQUE(symbol, cycle, trigger)
);
"""

# v2.10: 구 스키마(UNIQUE(symbol, trigger))의 fills를 사이클 원장으로 재구축.
# fills는 파생 캐시(관측 데이터 아님)라 테이블 재구축이 정당하다 — 기존 행은 cycle 0.
_MIGRATE_FILLS_CYCLE = """
ALTER TABLE fills RENAME TO fills_old;
CREATE TABLE fills (
  symbol TEXT NOT NULL, cycle INTEGER NOT NULL DEFAULT 0,
  trigger TEXT NOT NULL, side TEXT NOT NULL,
  bas_dt TEXT NOT NULL, price REAL NOT NULL, qty REAL NOT NULL, amount REAL NOT NULL,
  appended_at TEXT NOT NULL,
  UNIQUE(symbol, cycle, trigger)
);
INSERT INTO fills (symbol, cycle, trigger, side, bas_dt, price, qty, amount, appended_at)
  SELECT symbol, 0, trigger, side, bas_dt, price, qty, amount, appended_at FROM fills_old;
DROP TABLE fills_old;
"""


class PaperStore:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(_DDL)
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(fills)")]
        if "cycle" not in cols:
            self._conn.executescript(_MIGRATE_FILLS_CYCLE)

    def close(self) -> None:
        self._conn.close()

    def open_position(
        self, symbol: str, bas_dt: str, base_price: float, target_price: float,
        params: PaperParams,
    ) -> None:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version),0) FROM positions WHERE symbol=?", (symbol,)
        ).fetchone()
        self._conn.execute(
            "INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?)",
            (
                symbol, int(row[0]) + 1, bas_dt, base_price, target_price,
                json.dumps(asdict(params)), "open", None, now_kst().isoformat(),
            ),
        )
        self._conn.commit()

    def latest_positions(self) -> list[PositionRow]:
        rows = self._conn.execute(
            "SELECT symbol, opened_bas_dt, base_price, target_price, params, status, "
            "closed_reason, "
            "(SELECT COUNT(*) FROM positions q WHERE q.symbol = p.symbol "
            " AND q.status = 'closed' AND q.version < p.version) AS cycle "
            "FROM positions p WHERE version = "
            "(SELECT MAX(version) FROM positions WHERE symbol = p.symbol)"
        ).fetchall()
        return [
            PositionRow(
                symbol=str(r[0]), opened_bas_dt=str(r[1]), base_price=float(r[2]),
                target_price=float(r[3]), params=_params_from_json(str(r[4])),
                status=str(r[5]), closed_reason=(str(r[6]) if r[6] is not None else None),
                cycle=int(r[7]),
            )
            for r in rows
        ]

    def close_position(self, symbol: str, reason: str) -> None:
        pos = next((p for p in self.latest_positions() if p.symbol == symbol), None)
        if pos is None or pos.status == "closed":
            return
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version),0) FROM positions WHERE symbol=?", (symbol,)
        ).fetchone()
        self._conn.execute(
            "INSERT INTO positions VALUES (?,?,?,?,?,?,?,?,?)",
            (
                symbol, int(row[0]) + 1, pos.opened_bas_dt, pos.base_price,
                pos.target_price, json.dumps(asdict(pos.params)), "closed", reason,
                now_kst().isoformat(),
            ),
        )
        self._conn.commit()

    def add_fill(
        self, symbol: str, cycle: int, trigger: str, side: str, bas_dt: str,
        price: float, qty: float, amount: float,
    ) -> bool:
        before = self._conn.total_changes
        self._conn.execute(
            "INSERT OR IGNORE INTO fills VALUES (?,?,?,?,?,?,?,?,?)",
            (symbol, cycle, trigger, side, bas_dt, price, qty, amount,
             now_kst().isoformat()),
        )
        self._conn.commit()
        return self._conn.total_changes > before

    def reset_fills(self, symbol: str, cycle: int | None = None) -> None:
        """체결 원장 리셋 — fills는 quotes×params에서 결정론 재생되는 **파생 캐시**라
        기준가 정정(rebase) 시 삭제·재계산이 정당하다(관측 데이터 아님).
        cycle 지정 시 해당 사이클만 — 과거 사이클 실현 이력은 보존한다(v2.10)."""
        if cycle is None:
            self._conn.execute("DELETE FROM fills WHERE symbol=?", (symbol,))
        else:
            self._conn.execute(
                "DELETE FROM fills WHERE symbol=? AND cycle=?", (symbol, cycle)
            )
        self._conn.commit()

    def fills(self, symbol: str, cycle: int = 0) -> list[Fill]:
        rows = self._conn.execute(
            "SELECT trigger, side, bas_dt, price, qty, amount FROM fills "
            "WHERE symbol=? AND cycle=? ORDER BY bas_dt, appended_at",
            (symbol, cycle),
        ).fetchall()
        return [
            Fill(str(r[0]), str(r[1]), str(r[2]), float(r[3]), float(r[4]), float(r[5]))
            for r in rows
        ]


@dataclass
class PositionView:
    """마킹 결과 뷰 — 페이지·CLI 공용(순수 계산 산출물)."""

    symbol: str
    status: str
    opened: str
    base_price: float
    target_price: float
    last_price: float | None
    last_dt: str | None
    invested: float                      # 투입 원금(매수 합)
    realized: float                      # 실현 현금(매도 합)
    qty: float                           # 보유 수량
    next_buy: tuple[str, float] | None   # (트리거 라벨, 가이드가)
    next_sell: tuple[str, float] | None
    exit_note: str                       # 정리 타이밍 표기
    next_sell_qty: float | None = None   # 다음 매도 시 매도 수량(현재 보유 기준, 정수 주)
    next_buy_qty: float | None = None    # 다음 매수 주수
    in_buy_zone: bool = True             # 현재가가 매수 존(진행률 < 1/3) 안인가
    buy_ceiling: float | None = None     # 매수 상한가(기준가→정리가의 1/3 지점)
    buy_remaining: float = 0.0           # 미체결 매수 잔여 주수(트랜치 합)
    final_exit_price: float = 0.0        # 정리가(목표가 × 정리 배수)
    cycle: int = 0                       # 사이클(이전 청산 횟수) — v2.10
    # 남은 매도 계획 전체 [(가격, 주수)] — 현 보유·추가 매수 없음 가정의 결정론 전개
    sell_plan: tuple[tuple[float, float], ...] = ()
    total_bought: float = 0.0            # 누적 매수 주수 — 가이드의 비중(%) 분모
    closed_reason: str | None = None

    @property
    def value(self) -> float:
        return self.qty * (self.last_price or 0.0)

    @property
    def pnl_pct(self) -> float | None:
        if self.invested <= 0:
            return None
        return (self.realized + self.value) / self.invested - 1


def _quotes_since(conn: sqlite3.Connection, symbol: str, bas_dt: str) -> list[tuple[str, float]]:
    out = []
    for d, c in conn.execute(
        "SELECT bas_dt, clpr FROM daily_quotes WHERE srtn_cd=? AND bas_dt>=? ORDER BY bas_dt",
        (symbol, bas_dt),
    ):
        try:
            out.append((str(d), float(c)))
        except (TypeError, ValueError):
            continue
    return out


def mark(store: PaperStore, market_db: Path = Path("data") / "market.sqlite") -> list[PositionView]:
    """EOD 종가로 전 포지션 트리거 재생(멱등) → 뷰 반환. 실주문 없음 — 페이퍼 체결 기록만."""
    mconn = sqlite3.connect(f"file:{market_db}?mode=ro", uri=True)
    views: list[PositionView] = []
    try:
        for pos in store.latest_positions():
            sym, params, cy = pos.symbol, pos.params, pos.cycle
            base, target = pos.base_price, pos.target_price
            quotes = _quotes_since(mconn, sym, pos.opened_bas_dt)
            final_exit = target * params.final_exit_multiple

            # 트리거 사다리 — 등록 시점 기준 고정(가이드가는 언제나 재현 가능)
            buy_ladder: list[tuple[str, float, float]] = [
                ("1차 매수(초기)", base, params.initial_qty)
            ] + [
                (f"{i + 2}차 매수(-{int(lvl * -100)}%)", base * (1 + lvl), q)
                for i, (lvl, q) in enumerate(params.add_levels)
            ]
            sell_ladder: list[tuple[str, float, float]] = [
                (f"목표가 {int(mult * 100)}% 매도", target * mult, por)
                for mult, por in params.sell_levels
            ]
            exit_label = f"정리(목표가 {int(round(params.final_exit_multiple * 100))}%)"

            if pos.status == "open" and quotes:
                from datetime import date as _date

                def _d(s: str) -> _date:
                    return _date(int(s[:4]), int(s[4:6]), int(s[6:8]))

                # 초기 체결가 = 기준가 — 자동 등록은 등록일 종가와 동일하고,
                # rebase로 실제 매수가를 입력하면 수익률이 실투자 원금 기준이 된다
                d0 = quotes[0][0]
                store.add_fill(sym, cy, "1차 매수(초기)", "buy", d0, base,
                               params.initial_qty, params.initial_qty * base)
                max_touch = -1  # v2.9: 종가가 터치한 최고 매도선 인덱스(이력)
                for d, c in quotes:
                    # 가격 사다리 — 하락 시 가속 매수(시계 전체 유효)
                    for label, guide, q in buy_ladder[1:]:
                        if c <= guide:
                            store.add_fill(sym, cy, label, "buy", d, c, q, q * c)
                    # v2.7.1 시간 사다리 — 8주 경과 ∧ 매수 존(진행률 < 1/3)이면 다음 트랜치
                    cur = _d(d)
                    progress = (c - base) / (final_exit - base) if final_exit > base else 1.0
                    if progress < params.buy_zone_progress:
                        buys = [f for f in store.fills(sym, cy) if f.side == "buy"]
                        last_buy = max(_d(f.bas_dt) for f in buys)
                        if (cur - last_buy).days >= params.time_tranche_days:
                            done_now = {f.trigger for f in buys}
                            nxt = next(
                                ((lb, q) for lb, _g, q in buy_ladder[1:] if lb not in done_now),
                                None,
                            )
                            if nxt is not None:
                                store.add_fill(sym, cy, nxt[0], "buy", d, c,
                                               nxt[1], nxt[1] * c)
                    qty_now = sum(
                        f.qty if f.side == "buy" else -f.qty for f in store.fills(sym, cy)
                    )
                    for label, guide, portion in sell_ladder:
                        if c >= guide and qty_now > 0:
                            q = float(int(qty_now * portion))  # 정수 주 내림
                            if q > 0 and store.add_fill(sym, cy, label, "sell", d, c, q, q * c):
                                qty_now -= q
                    if c >= final_exit and qty_now > 0:
                        store.add_fill(sym, cy, exit_label, "sell", d, c,
                                       qty_now, qty_now * c)
                        store.close_position(sym, f"정리 지점 도달({d})")
                        break
                    # v2.9 이익 보호 정리(운영자 결재 2026-09-02): 두 번째 매도선(90%)
                    # 이상을 터치한 뒤 종가가 직전 매도선 아래로 → 잔량 전량 정리.
                    # 첫 매도선(80%) 터치만으로는 미발동 — 가치 실현 전 정상 보유 구간.
                    for i, (_lb, gd, _p) in enumerate(sell_ladder):
                        if c >= gd:
                            max_touch = max(max_touch, i)
                    if max_touch >= 1 and qty_now > 0 and c < sell_ladder[max_touch - 1][1]:
                        store.add_fill(sym, cy, "이익 보호 정리", "sell", d, c,
                                       qty_now, qty_now * c)
                        hi = int(round(params.sell_levels[max_touch][0] * 100))
                        lo = int(round(params.sell_levels[max_touch - 1][0] * 100))
                        store.close_position(
                            sym,
                            f"이익 보호 정리 — 목표가 {hi}% 터치 후 {lo}% 선 이탈({d})",
                        )
                        break

            pos_now = next(pp for pp in store.latest_positions() if pp.symbol == sym)
            fills = store.fills(sym, cy)
            invested = sum(f.amount for f in fills if f.side == "buy")
            realized = sum(f.amount for f in fills if f.side == "sell")
            qty = sum(f.qty if f.side == "buy" else -f.qty for f in fills)
            done = {f.trigger for f in fills}
            next_buy = next(
                ((lb, gd) for lb, gd, _p in buy_ladder if lb not in done), None
            )
            next_sell = next(
                ((lb, gd) for lb, gd, _p in sell_ladder if lb not in done),
                None if exit_label in done else (exit_label, final_exit),
            )
            next_sell_qty: float | None = None
            if next_sell is not None and qty > 0:
                portion = next(
                    (por for lb, _gd, por in sell_ladder if lb == next_sell[0]), 1.0
                )  # 정리 트리거는 잔량 전량
                next_sell_qty = float(int(qty * portion)) if portion < 1.0 else qty
            next_buy_qty: float | None = None
            ceiling = (
                min(
                    base + (target - base) * params.buy_zone_progress,
                    sell_ladder[0][1] if sell_ladder else final_exit,
                )
                if target > base else None
            )
            remaining = sum(q for lb, _gd, q in buy_ladder if lb not in done)
            # 매수 상한은 체결 상태와 무관한 가격 규율 지표(현재가 < 상한 = 매수 가능)
            in_zone = bool(quotes and ceiling is not None and quotes[-1][1] < ceiling)
            if next_buy is not None:
                next_buy_qty = next(
                    (q for lb, _gd, q in buy_ladder if lb == next_buy[0]),
                    params.initial_qty,
                )
            plan: list[tuple[float, float]] = []
            q_proj = qty
            for lb, gd, por in sell_ladder:
                if lb in done or q_proj <= 0:
                    continue
                s = float(int(q_proj * por))
                if s > 0:
                    plan.append((gd, s))
                    q_proj -= s
            if exit_label not in done and q_proj > 0:
                plan.append((final_exit, q_proj))
            last_dt: str | None = quotes[-1][0] if quotes else None
            last_px: float | None = quotes[-1][1] if quotes else None
            exit_note = (
                f"정리 {final_exit:,.0f}원(목표가 "
                f"{int(round(params.final_exit_multiple * 100))}%) · "
                f"90%+ 터치 후 직전 매도선 이탈 시 잔량 정리(v2.9) · "
                f"{params.warn_days}일 정체 경고 · "
                f"{params.time_horizon_days}일 미수렴 청산 · 심사 veto 전환 시 재검토"
            )
            views.append(
                PositionView(
                    symbol=sym, status=pos_now.status, opened=pos.opened_bas_dt,
                    base_price=base, target_price=target, last_price=last_px, last_dt=last_dt,
                    invested=invested, realized=realized, qty=qty,
                    next_buy=next_buy, next_sell=next_sell,
                    next_sell_qty=next_sell_qty, next_buy_qty=next_buy_qty,
                    in_buy_zone=in_zone,
                    buy_ceiling=ceiling, buy_remaining=remaining,
                    final_exit_price=final_exit, cycle=cy, sell_plan=tuple(plan),
                    total_bought=sum(f.qty for f in fills if f.side == "buy"),
                    exit_note=exit_note,
                    closed_reason=pos_now.closed_reason,
                )
            )
    finally:
        mconn.close()
    return views


def main() -> int:
    args = sys.argv[1:]
    store = PaperStore()
    try:
        if args and args[0] == "register":
            from trading.web.picks import _build_picks, approved_picks

            picks = approved_picks(_build_picks())
            allow = set(args[1:]) or {p.rec.symbol for p in picks}
            mconn = sqlite3.connect("data/market.sqlite")
            # v2.10: 열려 있는 포지션만 차단 — 청산 종목은 신규 후보와 동일 게이트로
            # 재등록(새 사이클). 과거 실현이익은 가점도 감점도 아니다(운영자 원칙).
            prior = {p.symbol: p for p in store.latest_positions()}
            existing = {s for s, p in prior.items() if p.status == "open"}
            n = 0
            for p in picks:
                sym = p.rec.symbol
                if sym not in allow or sym in existing:
                    continue
                row = mconn.execute(
                    "SELECT bas_dt, clpr FROM daily_quotes WHERE srtn_cd=? "
                    "ORDER BY bas_dt DESC LIMIT 1", (sym,),
                ).fetchone()
                if row is None or p.upside_pct is None:
                    print(f"  {sym}: 시세/회귀 여력 결측 — 등록 불가(지어내지 않음)")
                    continue
                if p.upside_pct < MIN_UPSIDE_PCT:
                    print(f"  {p.name}({sym}): 여력 {p.upside_pct:+.0f}% < 하한 "
                          f"+{MIN_UPSIDE_PCT:.0f}% — 등록 제외(손익비 미성립)")
                    continue
                base = float(row[1])
                target = base * (1 + p.upside_pct / 100)
                store.open_position(sym, str(row[0]), base, target, PROPOSED_PAPER)
                re_cy = prior[sym].cycle + 1 if sym in prior else 0
                tag = f" · 재진입(사이클 {re_cy + 1})" if re_cy else ""
                print(f"  {p.name}({sym}) 등록 — 기준가 {base:,.0f} · 목표 {target:,.0f} "
                      f"(+{p.upside_pct:.0f}%){tag}")
                n += 1
            mconn.close()
            print(f"등록 {n}건 → data/paper.sqlite")
            return 0

        if args and args[0] == "unregister" and len(args) >= 2:
            sym = args[1]
            # 페이퍼 원장은 검증용 입력 — 등록 오류·기준 미달의 철회는 삭제로 정정
            store.reset_fills(sym)
            store._conn.execute("DELETE FROM positions WHERE symbol=?", (sym,))
            store._conn.commit()
            print(f"{sym} 등록 철회(포지션·체결 삭제)")
            return 0

        if args and args[0] == "rebase" and len(args) >= 3:
            sym, new_base = args[1], float(args[2])
            pos = next((p for p in store.latest_positions() if p.symbol == sym), None)
            if pos is None:
                print(f"{sym}: 포지션 없음")
                return 1
            # 목표가는 절대 앵커(섹터 중앙 PBR 도달가) — 기준가만 교체, 현 사이클만 재생
            store.reset_fills(sym, pos.cycle)
            store.open_position(sym, pos.opened_bas_dt, new_base, pos.target_price, pos.params)
            print(f"{sym} 기준가 {pos.base_price:,.0f} → {new_base:,.0f} (목표 "
                  f"{pos.target_price:,.0f} 유지) — 체결 재생됨")
            mark(store)
            return 0

        views = mark(store)
        if not views:
            print("포지션 없음 — python -m trading.paper register [심볼...]")
            return 0
        total_in = sum(v.invested for v in views)
        total_out = sum(v.realized + v.value for v in views)
        pnls = [v.pnl_pct for v in views if v.pnl_pct is not None]
        avg = sum(pnls) / len(pnls) if pnls else 0.0
        wtd = (total_out / total_in - 1) if total_in else 0.0
        # 기준은 %(운영자 2026-09-01): 100주 가정이라 종목별 투입액이 달라
        # 총액 %는 고가주 편중 — 균등가중 평균이 헤드라인
        print(f"페이퍼 포지션 {len(views)}건 · 평균 수익률(균등가중) {avg:+.2%} "
              f"[총액 기준 {wtd:+.2%} · 투입 {total_in:,.0f}원]")
        for v in views:
            if v.buy_ceiling is None or v.status != "open":
                nb = "—"
            elif not v.in_buy_zone:
                nb = f"상한 {v.buy_ceiling:,.0f} 초과 — 중단"
            else:
                nb = f"상한 {v.buy_ceiling:,.0f} 이하 가능"
            ns = (f"{v.next_sell[0]} @{v.next_sell[1]:,.0f}"
                  + (f"({v.next_sell_qty:,.0f}주)" if v.next_sell_qty else "")
                  if v.next_sell else "—")
            cyc = f"·사이클{v.cycle + 1}" if v.cycle else ""
            print(f"  {v.symbol} [{v.status}{cyc}] 현재 {v.last_price or 0:,.0f}({v.last_dt}) · "
                  f"수익률 {v.pnl_pct if v.pnl_pct is not None else 0:+.1%} · "
                  f"매수 {nb} · 다음 매도 {ns}")
        return 0
    finally:
        store.close()


__all__ = ["Fill", "MIN_UPSIDE_PCT", "PROPOSED_PAPER", "PaperParams", "PaperStore", "PositionView", "mark"]


if __name__ == "__main__":
    raise SystemExit(main())
