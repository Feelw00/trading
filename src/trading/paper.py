"""페이퍼 투자(v2.5 — 운영자 지시 2026-09-01) — `python -m trading.paper`.

**매매 가이드**(운영자 정정 2026-09-02): 심사 승인 종목이 자동 등록되면 그날 종가가
시작가(기준가)가 되고, 이후 EOD 종가로 현재가·수익률·매수 상한·매도 타이밍·정리 타이밍을
결정론으로 판정해 제시한다. 원장의 100단위는 **정규화 단위(100 = 비중 100%)**이며 실투자
수량이 아니다(v2.8 부기) — 얼마를 살지는 이 모듈이 정하지 않는다(R5 §6 결재 전).

- **시작가 불변**(운영자 2026-09-02): 추가 매수로 평단이 낮아져도 시작가를 옮기지 않는다 —
  "손해가 발생했으면 그것도 데이터". ``rebase``는 입력 오류 정정 전용(``--correction <사유>`` 필수).
- **실주문 없음** — EXEC와 완전 무관한 페이퍼 원장(절대금지 3과 무관). 계측·학습 전용.
- 규칙(PaperParams)은 가치투자·사이클투자 문헌 조사로 캘리브레이션(POLICY §7 v2.5~).
- 트리거는 **등록 시점에 전부 박제**된다: 매수 상한 = 기준가→정리가의 1/3 지점
  (실매수 규율 지표), 매도 가이드 = 목표가(등록 시점 목표 PBR = min(자기 역사 밴드 중앙, 정당 PBR) 도달가 = 회귀
  여력 소진점) 배수 사다리, 정리 = 목표가 150% 도달 또는 이익 보호(90% 이상 매도선
  터치 후 직전 선 이탈 — v2.9) 또는 시간 상한 또는 심사 veto.
- 체결은 일별 EOD 종가가 트리거를 넘은 첫날 그 종가로 기록(장중가 없음 — 정직한 근사).
- append-only: 포지션·체결 모두 새 행으로만. 수익률 = (실현 현금 + 평가액) / 투입 원금.
"""

import json
import sqlite3
import sys
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from trading.collectors.base import now_kst

DEFAULT_DB = Path("data") / "paper.sqlite"

# 등록 자격 하한(운영자 지적 2026-09-01: "1% 먹으려고 하락 리스크를 지는 건 아니다"):
# 회귀 여력 ≥ +30% — 안전마진 원칙(Tweedy Browne는 내재가치 60~70%에서 매수 = 여력
# 43%+, 실무 플레이북 -30% 기준의 보수적 하한).
MIN_UPSIDE_PCT = 30.0
# 보유 종목 목표가 괴리 표기 임계(운영자 지시 2026-09-02 "예상치가 바뀌면 표기"): 등록 목표가 대비
# 현재 추정 목표가(종가 × (1 + 회귀 여력))가 ±15% 이상 벌어지면 /paper ⚠ + eod 실행 보고 줄.
# 반영은 명시 명령(retarget)만 — 자동 갱신은 매도 사다리·조건주문(EXEC-12)을 흔든다(GUIDE-1).
TARGET_DRIFT_ALERT_PCT = 15.0


@dataclass(frozen=True)
class PaperParams:
    """분할 매매 규칙 — 문헌 캘리브레이션(v2.5, docs/POLICY_PARAMS.md §7).

    capital: 종목당 가상 원금(원). initial_pct: 등록 즉시 투입 비중.
    add_levels: (기준가 대비 하락률, 투입 비중) 사다리 — 하락할수록 싸게 더 산다.
    sell_levels: (목표가까지 갭 충족률, 보유 수량 대비 매도 비중) 사다리.
    time_horizon_days: 가치 실현 대기 상한 — 초과 시 정리 표기(강제 청산은 운영자 몫).
    """

    # v2.8(운영자 지시 2026-09-02): 등록 시 100단위 일괄 — 분할 매수 사다리 폐지.
    # 100 = 정규화 단위(비중 100%), 실투자 수량 아님(v2.8 부기) — 표시는 비중(%)로.
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


def _last_close(mconn: sqlite3.Connection, symbol: str) -> tuple[str, float] | None:
    row = mconn.execute(
        "SELECT bas_dt, clpr FROM daily_quotes WHERE srtn_cd=? ORDER BY bas_dt DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if row is None:
        return None
    try:
        return str(row[0]), float(row[1])
    except (TypeError, ValueError):
        return None


def current_targets(
    symbols: Iterable[str], *, market_db: Path = Path("data") / "market.sqlite",
    upside: Mapping[str, float | None] | None = None,
) -> dict[str, tuple[str, float, float] | None]:
    """심볼 → (최근 종가일, 종가, 현재 추정 목표가 = 종가 × (1 + 회귀 여력)). 결측은 None.

    회귀 여력은 /picks와 같은 산식(목표 PBR = min(자기 역사 5년 밴드 중앙, 정당 PBR) ÷ 현재 PBR − 1
    — v2.13 밴드·v2.14 정당 PBR 캡, 일간 시세 × 연간 자본총계. 구 섹터 중앙 PBR은 버킷 이질성으로 폐기).
    실보유 편입 목표가와 보유 종목 목표가 괴리 표기가 쓴다. ``upside`` 주입은 테스트·재사용용.
    """
    syms = list(symbols)
    if upside is None:
        from trading.web.picks import current_upside

        upside = current_upside(syms)
    mconn = sqlite3.connect(f"file:{market_db}?mode=ro", uri=True)
    try:
        out: dict[str, tuple[str, float, float] | None] = {}
        for sym in syms:
            u = upside.get(sym)
            last = _last_close(mconn, sym)
            if u is None or last is None:
                out[sym] = None
            else:
                out[sym] = (last[0], last[1], last[1] * (1 + u / 100))
        return out
    finally:
        mconn.close()


@dataclass(frozen=True)
class EnrollBlocked:
    """실보유 편입의 **정책 보류**(결측에 의한 불가와 구분) — GUIDE-1 ③(운영자 결정 2026-09-03):
    심사 승인 없는 실보유는 편입하지 않는다(목표가·매도 사다리·조건주문 없음). 승인되면 다음
    guide-orders 실행에서 자동 편입. 호출자는 가이드 밖 ⚠ 표기 + 첫 발견 시 P1."""

    reason: str


def current_verdicts() -> dict[str, str | None]:
    """심사 원장 현재 판정(만료분 제외) — {symbol: approved|hold|vetoed|None}."""
    from trading.review import ReviewStore, latest_annual_year

    rstore = ReviewStore()
    try:
        return {s: v.get("verdict") for s, v in rstore.all_current(latest_annual_year()).items()}
    finally:
        rstore.close()


def register_block_reason(
    verdict: str | None, upside_pct: float | None, cycle_caution: bool,
) -> str | None:
    """명시 등록(`paper register`) 자격 — 차단 사유 또는 None(자격 충족). 순수 함수.

    v2.12: 심사 승인 ∧ 회귀 여력 ≥ +30%. **v2.15(운영자 결정 2026-09-03, P-20 ①(a))**: 과열 산업
    (`CandidateRecord.cycle_caution`) 제외 — **가이드 등록 자격에만** 적용. 승인 노출·자동 심사는
    불변(P-18: 국면은 게이트가 아니라 도구). 국면이 바뀌면 같은 명령으로 재등록 가능.
    실보유 자동 편입(`enroll_holding`)은 이 함수를 쓰지 않는다 — 보유는 사실이며 승인만 본다.
    """
    if verdict != "approved":
        return f"심사 승인 아님({verdict or '대기'})"
    if cycle_caution:
        return "과열 산업(⚠) — 가이드 등록 제외(v2.15: 승인 노출은 유지, 국면 전환 시 재등록 가능)"
    if upside_pct is None:
        return "회귀 여력 결측(지어내지 않음)"
    if upside_pct < MIN_UPSIDE_PCT:
        return f"여력 {upside_pct:+.0f}% < 하한 +{MIN_UPSIDE_PCT:.0f}%(손익비 미성립)"
    return None


def enroll_holding(
    store: PaperStore, symbol: str, avg_price: float | None, *,
    targets: Mapping[str, tuple[str, float, float] | None] | None = None,
    market_db: Path = Path("data") / "market.sqlite",
    verdicts: Mapping[str, str | None] | None = None,
) -> str | EnrollBlocked | None:
    """실보유 편입 — 페이퍼는 실투자·명시 이동만(운영자 지시 2026-09-02).

    시작가 = 실평단(불변 원칙 그대로), 목표가 = 편입 시점 추정 목표. 여력 하한·과열은 묻지
    않는다 — 이미 산 종목의 사실 기록. **심사 승인은 본다(GUIDE-1 ③, 2026-09-03)**: 승인 없는
    보유는 `EnrollBlocked`(편입 보류 — 목표가·매도선 없음, 심사 우회 방지).
    편입 불가(평단·밸류에이션·시세 결측)면 None — 호출자가 P1로 올린다.
    """
    if avg_price is None or avg_price <= 0:
        return None
    if any(p.symbol == symbol and p.status == "open" for p in store.latest_positions()):
        return None
    vd = (verdicts if verdicts is not None else current_verdicts()).get(symbol)
    if vd != "approved":
        return EnrollBlocked(f"심사 승인 없음(판정: {vd or '미심사'})")
    tg = (targets if targets is not None else current_targets([symbol], market_db=market_db)).get(symbol)
    if tg is None:
        return None
    bas_dt, _close, target = tg
    store.open_position(symbol, bas_dt, float(avg_price), target, PROPOSED_PAPER)
    return (f"{symbol} 가이드 편입 — 시작가 {avg_price:,.0f}(실평단) · "
            f"목표 {target:,.0f}({target / avg_price - 1:+.0%})")


@dataclass(frozen=True)
class TargetDrift:
    """등록 목표가 vs 현재 추정 목표가 — 표기용 순수 산출(반영은 retarget 명령만)."""

    symbol: str
    registered: float
    estimated: float

    @property
    def pct(self) -> float:
        return (self.estimated / self.registered - 1) * 100

    @property
    def alert(self) -> bool:
        return abs(self.pct) >= TARGET_DRIFT_ALERT_PCT


def target_drift(
    views: Iterable[PositionView], targets: Mapping[str, tuple[str, float, float] | None],
) -> list[TargetDrift]:
    out: list[TargetDrift] = []
    for v in views:
        if v.status != "open" or v.target_price <= 0:
            continue
        tg = targets.get(v.symbol)
        if tg is not None:
            out.append(TargetDrift(v.symbol, v.target_price, tg[2]))
    return out


def _print_drift(views: list[PositionView]) -> None:
    """보유 종목 목표가 괴리 — 최상위 줄은 eod-v3 실행 보고(ALERT-1 요약)에 실린다."""
    open_syms = [v.symbol for v in views if v.status == "open"]
    if not open_syms:
        return
    try:
        targets = current_targets(open_syms)
    except Exception as exc:  # noqa: BLE001 — 표기 실패가 마킹을 막지 않는다(정직 표기)
        print(f"목표가 괴리 산출 불가: {exc!r}")
        return
    hot = [d for d in target_drift(views, targets) if d.alert]
    if not hot:
        return
    print(f"⚠ 목표가 괴리 {len(hot)}종(등록 대비 ±{TARGET_DRIFT_ALERT_PCT:.0f}% 이상) — 반영은 "
          "`python -m trading.paper retarget <심볼> auto --reason <사유>`")
    for d in hot:
        print(f"  {d.symbol} 등록 {d.registered:,.0f} → 추정 {d.estimated:,.0f} ({d.pct:+.0f}%)")


def main() -> int:
    args = sys.argv[1:]
    store = PaperStore()
    try:
        if args and args[0] == "register":
            # 운영자 지시(2026-09-02): 페이퍼 편입은 실투자(guide-orders 실보유 자동 편입) 또는
            # 명시 이동만 — 승인 일괄 자동 등록 폐지. 명시 이동은 등록 자격(승인 ∧ 여력 ≥ +30%) 유지.
            syms = [a for a in args[1:] if not a.startswith("--")]
            if not syms:
                print("페이퍼 편입은 실투자 자동 편입 또는 명시 이동만: "
                      "`register <심볼>...` (승인 일괄 자동 등록 폐지 — 운영자 지시 2026-09-02)")
                return 2
            from trading.web.picks import _build_picks

            allow = set(syms)
            picks = [p for p in _build_picks() if p.rec.symbol in allow]
            for miss in sorted(allow - {p.rec.symbol for p in picks}):
                print(f"  {miss}: 후보·원장에 없음 — 편입 불가(지어내지 않음)")
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
                # 등록 자격(v2.12 승인 ∧ 여력 ≥ +30% · v2.15 과열 산업 제외) — 순수 함수 단일 경로
                why = register_block_reason(p.verdict, p.upside_pct, p.rec.cycle_caution)
                if why is not None:
                    print(f"  {p.name}({sym}): {why} — 편입 불가")
                    continue
                row = mconn.execute(
                    "SELECT bas_dt, clpr FROM daily_quotes WHERE srtn_cd=? "
                    "ORDER BY bas_dt DESC LIMIT 1", (sym,),
                ).fetchone()
                if row is None or p.upside_pct is None:
                    print(f"  {sym}: 시세/회귀 여력 결측 — 등록 불가(지어내지 않음)")
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

        if args and args[0] == "close" and len(args) >= 2:
            # 운영자 실정리(2026-09-03 "심사 외 종목 정리했어"): 실보유가 사라진 가이드 포지션을
            # 이력 보존으로 닫는다(closed 새 버전, 체결 유지). unregister(삭제)는 등록 오류 정정 전용.
            sym = args[1]
            if "--reason" not in args or args.index("--reason") + 1 >= len(args):
                print("종료는 사유 필수: `close <심볼> --reason <사유>` (실정리·운영자 판단 기록)")
                return 2
            reason = args[args.index("--reason") + 1]
            pos = next((p for p in store.latest_positions()
                        if p.symbol == sym and p.status == "open"), None)
            if pos is None:
                print(f"{sym}: open 포지션 없음")
                return 1
            store.close_position(sym, reason)
            print(f"{sym} 가이드 종료(사이클 {pos.cycle + 1}, 시작가 {pos.base_price:,.0f} · "
                  f"목표 {pos.target_price:,.0f}) — 사유: {reason}")
            return 0

        if args and args[0] == "unregister" and len(args) >= 2:
            sym = args[1]
            # 페이퍼 원장은 검증용 입력 — 등록 오류·기준 미달의 철회는 삭제로 정정
            store.reset_fills(sym)
            store._conn.execute("DELETE FROM positions WHERE symbol=?", (sym,))
            store._conn.commit()
            print(f"{sym} 등록 철회(포지션·체결 삭제)")
            return 0

        if args and args[0] == "retarget" and len(args) >= 2:
            # 운영자 지시(2026-09-02): 보유 종목 예상치 변경은 표기하고, 반영은 명시 명령만.
            sym = args[1]
            if "--reason" not in args or args.index("--reason") + 1 >= len(args):
                print("목표가 반영은 명시 명령만: `retarget <심볼> [가격|auto] --reason <사유>` "
                      "(자동 갱신 없음 — 매도 사다리·조건주문 안정. OPEN_QUESTIONS GUIDE-1)")
                return 2
            reason = args[args.index("--reason") + 1]
            pos = next((p for p in store.latest_positions()
                        if p.symbol == sym and p.status == "open"), None)
            if pos is None:
                print(f"{sym}: open 포지션 없음")
                return 1
            spec = args[2] if len(args) > 2 and not args[2].startswith("--") else "auto"
            if spec == "auto":
                tg = current_targets([sym]).get(sym)
                if tg is None:
                    print(f"{sym}: 추정 목표가 결측(밸류에이션·시세) — 반영 불가(지어내지 않음)")
                    return 1
                new_target = tg[2]
            else:
                new_target = float(spec)
            print(f"반영 사유: {reason}")
            # 시작가 불변 — 목표가만 새 버전으로(append-only), 현 사이클 체결 재생
            store.reset_fills(sym, pos.cycle)
            store.open_position(sym, pos.opened_bas_dt, pos.base_price, new_target, pos.params)
            print(f"{sym} 목표가 {pos.target_price:,.0f} → {new_target:,.0f} "
                  f"(시작가 {pos.base_price:,.0f} 불변) — 체결 재생됨")
            mark(store)
            return 0

        if args and args[0] == "rebase" and len(args) >= 3:
            sym, new_base = args[1], float(args[2])
            if "--correction" not in args or args.index("--correction") + 1 >= len(args):
                print("시작가 불변(운영자 2026-09-02): 추가 매수로 평단이 낮아져도 시작가를 옮기지 "
                      "않는다 — 손실도 데이터. 입력 오류 정정만 "
                      "`rebase <심볼> <가> --correction <사유>`로.")
                return 2
            print(f"정정 사유: {args[args.index('--correction') + 1]}")
            pos = next((p for p in store.latest_positions() if p.symbol == sym), None)
            if pos is None:
                print(f"{sym}: 포지션 없음")
                return 1
            # 목표가는 절대 앵커(자기 역사 PBR 밴드 중앙 도달가) — 기준가만 교체, 현 사이클만 재생
            store.reset_fills(sym, pos.cycle)
            store.open_position(sym, pos.opened_bas_dt, new_base, pos.target_price, pos.params)
            print(f"{sym} 기준가 {pos.base_price:,.0f} → {new_base:,.0f} (목표 "
                  f"{pos.target_price:,.0f} 유지) — 체결 재생됨")
            mark(store)
            return 0

        views = mark(store)
        if not views:
            print("포지션 없음 — 실투자(guide-orders 자동 편입) 또는 "
                  "python -m trading.paper register <심볼>")
            return 0
        pnls = [v.pnl_pct for v in views if v.pnl_pct is not None]
        avg = sum(pnls) / len(pnls) if pnls else 0.0
        # 가이드 헤드라인 = 종목 균등가중 평균만(운영자 2026-09-02: 총액 기준 폐지 —
        # 정규화 단위에서 총액 %는 고가주 편중 지표라 의미 없음). 주수·투입액 미노출.
        print(f"매매 가이드 {len(views)}종목 · 평균 수익률(균등가중) {avg:+.2%}")
        for v in views:
            if v.buy_ceiling is None or v.status != "open":
                nb = "—"
            elif not v.in_buy_zone:
                nb = f"상한 {v.buy_ceiling:,.0f} 초과 — 중단"
            else:
                nb = f"상한 {v.buy_ceiling:,.0f} 이하 가능"
            share = (f"({v.next_sell_qty / v.total_bought:.0%})"
                     if v.next_sell_qty and v.total_bought > 0 else "")
            ns = f"{v.next_sell[0]} @{v.next_sell[1]:,.0f}{share}" if v.next_sell else "—"
            cyc = f"·사이클{v.cycle + 1}" if v.cycle else ""
            print(f"  {v.symbol} [{v.status}{cyc}] 현재 {v.last_price or 0:,.0f}({v.last_dt}) · "
                  f"수익률 {v.pnl_pct if v.pnl_pct is not None else 0:+.1%} · "
                  f"매수 {nb} · 다음 매도 {ns}")
        _print_drift(views)
        return 0
    finally:
        store.close()


__all__ = [
    "Fill", "MIN_UPSIDE_PCT", "PROPOSED_PAPER", "TARGET_DRIFT_ALERT_PCT", "PaperParams", "PaperStore",
    "PositionView", "TargetDrift", "current_targets", "enroll_holding", "mark", "target_drift",
]


if __name__ == "__main__":
    raise SystemExit(main())
