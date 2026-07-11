"""P-9 스윙 스크리너 — 스윙 품질 유니버스(4축) + 기회 트리거. **순수 코드**(LLM 미개입).

구조(PROPOSALS P-9, 운영자 방향 합의 2026-07-11):
- **유니버스(천천히 변함)**: "기회가 오면 살 자격이 있는 종목" — 4축 횡단면 백분위 가중합.
  1. 추세 품질: 변동성 조정 모멘텀(60/120일) + MDD 컷 + 이동평균 정배열 (급등 스파이크 배제)
  2. 도메인 열기: 섹터 거래대금 점유율 변화·상승 breadth + R2 이벤트 촉매(섹터 집계)
  3. 펀더멘털: 매출·영업이익 YoY(``fins.sqlite``), 부채비율 게이트
  4. 수급 지속성: 외인+기관 순매수 누적(거래대금 정규화)·지속일수(``flows.sqlite``)
- **기회 트리거(매일)**: 유니버스 종목에서 눌림목/도메인 점화/촉매/수급 전환 발화 시 후보 승격.

환각·침묵 폴백 가드:
- 축 데이터 미확보는 **결측**으로 명시(0으로 흘리지 않음) — 가용 축만으로 가중 재정규화,
  추세 축 필수 + 최소 2축. 커버리지가 출력에 나온다.
- 뉴스 축은 EventStore 최신 ``as_of`` 가 창보다 오래되면 **stale로 제외**(결측≠촉매없음 —
  2026-06-15~07-10 뉴스 공백 오독 방지).
- 임계값은 전부 SwingConfig 보수 초기값 — R7 주간 채점으로 튜닝 예정(임의 확신 금지).
"""

import json
import math
import sqlite3
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from statistics import pstdev
from typing import Any

from trading.collectors.base import KST, now_kst
from trading.collectors.fins import FinStore
from trading.collectors.flows import FlowStore
from trading.collectors.market import MarketStore
from trading.journal.events import EventStore
from trading.screener import SECTOR_SOURCES, ScreenConfig, _f, _percentiles, screen

DEFAULT_DB = Path("data") / "swing.sqlite"


@dataclass(frozen=True)
class SwingConfig:
    # ── 추세 품질 ──
    lookback_short: int = 60
    lookback_long: int = 120
    mdd_window: int = 120
    mdd_cut: float = -0.40        # 최대낙폭 컷(유니버스 제외)
    ma_short: int = 20
    ma_long: int = 60
    # ── 도메인 열기 ──
    breadth_window: int = 20
    share_short: int = 5
    share_long: int = 60
    min_sector_members: int = 3   # 이 미만 섹터는 열기 미산출(소표본 노이즈)
    news_window_days: int = 10
    # ── 펀더멘털 ──
    yoy_cap: float = 1.0          # YoY 극단값 캡(랭크 왜곡 방지)
    debt_gate: float = 4.0        # 부채비율(부채/자본) 초과 시 유니버스 제외
    # ── 수급 ──
    flow_window: int = 20
    flow_min_days: int = 5        # 관측일 미만이면 축 결측
    # ── 합성 ──
    w_trend: float = 1.0
    w_domain: float = 1.0
    w_fund: float = 1.0
    w_flow: float = 1.0
    min_axes: int = 2             # 추세 필수 + 최소 축 수
    top_n: int = 30
    # ── 기회 트리거 (보수 초기값 — R7 튜닝 예정) ──
    trigger_min_score: float = 0.55
    pullback_min: float = 0.03    # 20일 고점 대비 조정폭 하한
    pullback_max: float = 0.15    # 상한(추세 훼손 배제)
    ignition_pct: float = 0.80    # 도메인 열기 백분위 임계
    catalyst_days: int = 3
    catalyst_min_strength: float = 0.6
    flow_turn_short: int = 5      # 단기 순매수 전환 창


@dataclass(frozen=True)
class AxisValue:
    """축 원시값 + 확보 여부 — ok=False면 raw는 무의미(침묵 폴백 방지)."""

    raw: float = 0.0
    ok: bool = False
    note: str = ""


@dataclass(frozen=True)
class SwingRow:
    srtn_cd: str
    name: str
    market: str | None
    clpr: float
    sectors: tuple[str, ...]
    trend: AxisValue
    domain: AxisValue
    fund: AxisValue
    flow: AxisValue
    mdd: float
    score: float = 0.0            # 가용 축 백분위 가중평균(0~1)
    pct: dict[str, float] = field(default_factory=dict)  # 축별 백분위
    excluded: str = ""            # 비면 유니버스 포함, 아니면 제외 사유
    triggers: tuple[str, ...] = ()


@dataclass(frozen=True)
class SwingResult:
    as_of: str
    universe: list[SwingRow]      # 점수순 top_n (제외분 미포함)
    gate_total: int               # 게이트 통과 종목수
    scored: int                   # 점수 산출 종목수(축 요건 충족)
    excluded: dict[str, int]      # 제외 사유별 카운트
    coverage: dict[str, int]      # 축별 확보 종목수
    warnings: tuple[str, ...] = ()
    triggered: list[SwingRow] = field(default_factory=list)  # 오늘 트리거 발화분


# ──────────────────────────── 축 계산 ────────────────────────────


def _returns(closes: list[float]) -> list[float]:
    return [b / a - 1 for a, b in zip(closes, closes[1:]) if a > 0]


def vol_adjusted_momentum(closes: list[float], window: int) -> float | None:
    """window일 수익률 ÷ (일변동성×√window) — Sharpe류. 히스토리 부족·무변동은 None."""
    if len(closes) <= window:
        return None
    seg = closes[-(window + 1) :]
    if seg[0] <= 0:
        return None
    rets = _returns(seg)
    sd = pstdev(rets) if len(rets) > 1 else 0.0
    if sd <= 0:
        return None
    total = seg[-1] / seg[0] - 1
    return total / (sd * math.sqrt(window))


def max_drawdown(closes: list[float], window: int) -> float:
    """window 내 최대낙폭(음수). 데이터 짧으면 있는 만큼."""
    seg = closes[-window:]
    peak = float("-inf")
    mdd = 0.0
    for c in seg:
        peak = max(peak, c)
        if peak > 0:
            mdd = min(mdd, c / peak - 1)
    return mdd


def _ma(closes: list[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def trend_axis(closes: list[float], cfg: SwingConfig) -> tuple[AxisValue, float, bool]:
    """(축, mdd, 정배열). 60일 미만 히스토리는 결측."""
    vam_s = vol_adjusted_momentum(closes, cfg.lookback_short)
    if vam_s is None:
        return AxisValue(note="히스토리<60일"), 0.0, False
    vam_l = vol_adjusted_momentum(closes, cfg.lookback_long)
    raw = vam_s if vam_l is None else 0.6 * vam_s + 0.4 * vam_l
    mdd = max_drawdown(closes, cfg.mdd_window)
    ma_s, ma_l = _ma(closes, cfg.ma_short), _ma(closes, cfg.ma_long)
    aligned = ma_s is not None and ma_l is not None and closes[-1] > ma_s > ma_l
    return AxisValue(raw=raw, ok=True), mdd, aligned


def sector_heat(
    series: dict[str, list[tuple[Any, ...]]],
    secmap: dict[str, list[str]],
    events_by_stock: dict[str, float] | None,
    cfg: SwingConfig,
) -> dict[str, float]:
    """섹터 → 열기 점수(구성 지표 백분위 평균). 소표본 섹터 제외.

    events_by_stock=None 이면 뉴스 축 stale — 시세 기반 2지표만으로 산출.
    """
    members: dict[str, list[str]] = {}
    for cd, secs in secmap.items():
        if cd in series:
            for s in secs:
                members.setdefault(s, []).append(cd)
    members = {s: cds for s, cds in members.items() if len(cds) >= cfg.min_sector_members}
    if not members:
        return {}

    def _tr_sum(cds: list[str], days: int) -> float:
        total = 0.0
        for cd in cds:
            for r in series[cd][-days:]:
                v = _f(r[6])
                if v is not None:
                    total += v
        return total

    all_codes = list(series)
    tot_s, tot_l = _tr_sum(all_codes, cfg.share_short), _tr_sum(all_codes, cfg.share_long)
    names = sorted(members)
    breadth: list[float] = []
    share_ratio: list[float] = []
    news: list[float] = []
    for s in names:
        cds = members[s]
        ups = 0
        for cd in cds:
            closes = [c for c in (_f(r[4]) for r in series[cd]) if c is not None]
            if len(closes) > cfg.breadth_window and closes[-(cfg.breadth_window + 1)] > 0:
                ups += closes[-1] / closes[-(cfg.breadth_window + 1)] - 1 > 0
        breadth.append(ups / len(cds))
        sh_s = _tr_sum(cds, cfg.share_short) / tot_s if tot_s > 0 else 0.0
        sh_l = _tr_sum(cds, cfg.share_long) / tot_l if tot_l > 0 else 0.0
        share_ratio.append(sh_s / sh_l if sh_l > 0 else 0.0)
        if events_by_stock is not None:
            news.append(sum(events_by_stock.get(cd, 0.0) for cd in cds) / len(cds))

    parts = [_percentiles(breadth), _percentiles(share_ratio)]
    if events_by_stock is not None:
        parts.append(_percentiles(news))
    return {s: sum(p[i] for p in parts) / len(parts) for i, s in enumerate(names)}


def recent_event_strength(
    estore: EventStore, as_of_day: datetime, window_days: int
) -> dict[str, float] | None:
    """종목 → 최근 window 내 촉매강도 합(relevance 가중). 이벤트 DB가 창보다 오래되면 None(stale)."""
    events = estore.recent(limit=500)
    if not events:
        return None
    cutoff = as_of_day - timedelta(days=window_days)
    if max(e.as_of for e in events) < cutoff:
        return None  # 수집 공백 — "촉매 없음"과 구분(결측)
    out: dict[str, float] = {}
    for e in events:
        if e.as_of < cutoff:
            continue
        strength = e.catalyst_strength or 0.0
        for a in e.affected:
            out[a.srtn_cd] = out.get(a.srtn_cd, 0.0) + strength * (a.relevance or 0.5)
    return out


def _mn(v: object) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def flow_axis(
    fstore: FlowStore, srtn_cd: str, avg_tr_prc: float, cfg: SwingConfig
) -> tuple[AxisValue, list[float]]:
    """외인+기관 순매수(백만원) 누적을 평균 거래대금으로 정규화. 관측 부족은 결측.

    반환 둘째 값 = 일별 순매수(최신순, 트리거 전환 판정용).
    """
    rows = fstore.recent_for("stock", srtn_cd, limit=cfg.flow_window)
    daily: list[float] = []
    for _, _prsn, frgn, orgn, _fund in rows:
        f, o = _mn(frgn), _mn(orgn)
        if f is None and o is None:
            continue
        daily.append((f or 0.0) + (o or 0.0))
    if len(daily) < cfg.flow_min_days:
        return AxisValue(note=f"수급 관측 {len(daily)}일<{cfg.flow_min_days}"), daily
    denom = avg_tr_prc / 1e6 * len(daily)  # 백만원 환산 거래대금
    raw = sum(daily) / denom if denom > 0 else 0.0
    return AxisValue(raw=raw, ok=True), daily


def fund_axis(snap: Any, cfg: SwingConfig) -> tuple[AxisValue, float | None]:
    """(축, 부채비율). 스냅샷 없음·매출 YoY 미산출은 결측. 흑자전환은 캡 점수."""
    if snap is None:
        return AxisValue(note="재무 미수집"), None
    rev = snap.rev_yoy
    if rev is None:
        return AxisValue(note="매출 YoY 미산출"), snap.debt_ratio
    rev_c = max(-cfg.yoy_cap, min(cfg.yoy_cap, rev))
    op = snap.op_yoy
    if op is not None:
        op_c = max(-cfg.yoy_cap, min(cfg.yoy_cap, op))
    elif snap.op_turned_positive:
        op_c = cfg.yoy_cap  # 흑자전환 — YoY 무의미하지만 강한 개선 신호
    else:
        op_c = -cfg.yoy_cap if (snap.op_income or 0) < 0 else 0.0  # 적자 지속/판단 불가
    return AxisValue(raw=0.5 * rev_c + 0.5 * op_c, ok=True), snap.debt_ratio


# ──────────────────────────── 합성·트리거 ────────────────────────────


def _detect_triggers(
    row: SwingRow,
    closes: list[float],
    highs: list[float],
    daily_flows: list[float],
    events_by_stock: dict[str, float] | None,
    recent_strength: dict[str, float],
    cfg: SwingConfig,
) -> tuple[str, ...]:
    out: list[str] = []
    # 눌림목: 추세 유지(vam>0·ma20 위) + 20일 고점 대비 3~15% 조정
    high20 = max(highs[-cfg.ma_short :]) if highs else 0.0
    ma20 = _ma(closes, cfg.ma_short)
    if high20 > 0 and row.trend.ok and row.trend.raw > 0 and ma20 is not None:
        depth = 1 - closes[-1] / high20
        if cfg.pullback_min <= depth <= cfg.pullback_max and closes[-1] >= ma20:
            out.append("pullback")
    # 도메인 점화
    if row.domain.ok and row.pct.get("domain", 0.0) >= cfg.ignition_pct:
        out.append("domain_ignition")
    # 종목 촉매(최근 N일)
    if recent_strength.get(row.srtn_cd, 0.0) >= cfg.catalyst_min_strength:
        out.append("catalyst")
    # 수급 전환: 최근 단기 순매수 양전 + 창 전체는 음수(방향 전환)
    if len(daily_flows) >= cfg.flow_window:
        short = sum(daily_flows[: cfg.flow_turn_short])
        if daily_flows[0] > 0 and short > 0 and sum(daily_flows) <= 0:
            out.append("flow_turn")
    return tuple(out)


def build(
    store: MarketStore,
    fstore: FlowStore,
    finstore: FinStore,
    estore: EventStore,
    config: SwingConfig | None = None,
    *,
    now: datetime | None = None,
) -> SwingResult:
    cfg = config or SwingConfig()
    res = screen(store, ScreenConfig(top_n=1_000_000))  # 게이트·아티팩트 가드 재사용
    if not res.candidates:
        return SwingResult("", [], 0, 0, {}, {})
    as_of = res.as_of
    warnings = list(res.warnings)

    cutoff = store.nth_recent_date(max(cfg.lookback_long, cfg.mdd_window, 252)) or as_of
    survivors = {c.srtn_cd: c for c in res.candidates}
    series: dict[str, list[tuple[Any, ...]]] = {}
    for row in store.rows_since(cutoff):
        cd = str(row[0])
        if cd in survivors:
            series.setdefault(cd, []).append(row)
    for recs in series.values():
        recs.sort(key=lambda r: str(r[3]))

    secmap = store.sector_map_multi(SECTOR_SOURCES)
    as_of_dt = datetime.strptime(as_of, "%Y%m%d").replace(tzinfo=KST)
    events_by_stock = recent_event_strength(estore, now or as_of_dt, cfg.news_window_days)
    if events_by_stock is None:
        warnings.append(
            f"이벤트 DB가 {cfg.news_window_days}일 창보다 오래됨 — 도메인 뉴스축·촉매 트리거 제외(결측≠촉매없음)"
        )
    heat = sector_heat(series, secmap, events_by_stock, cfg)

    rows: list[SwingRow] = []
    trig_inputs: dict[str, tuple[list[float], list[float], list[float]]] = {}
    excluded: dict[str, int] = {}
    for cd, cand in survivors.items():
        recs = series.get(cd, [])
        closes = [v for v in (_f(r[4]) for r in recs) if v is not None]
        highs = [v for v in (_f(r[5]) for r in recs) if v is not None]
        trs = [v for v in (_f(r[6]) for r in recs) if v is not None]
        trend, mdd, _aligned = trend_axis(closes, cfg)
        secs = tuple(secmap.get(cd, []))
        dom_scores = [heat[s] for s in secs if s in heat]
        domain = (
            AxisValue(raw=max(dom_scores), ok=True)
            if dom_scores
            else AxisValue(note="섹터 미분류/소표본")
        )
        avg_tr = sum(trs[-cfg.flow_window :]) / max(1, len(trs[-cfg.flow_window :]))
        flow, daily_flows = flow_axis(fstore, cd, avg_tr, cfg)
        fund, debt = fund_axis(finstore.snapshot_for(cd), cfg)

        srow = SwingRow(cd, cand.name, cand.market, cand.clpr, secs, trend, domain, fund, flow, mdd)
        if not trend.ok:
            excluded["추세축 결측"] = excluded.get("추세축 결측", 0) + 1
            continue
        if mdd < cfg.mdd_cut:
            excluded[f"MDD<{cfg.mdd_cut:.0%}"] = excluded.get(f"MDD<{cfg.mdd_cut:.0%}", 0) + 1
            continue
        if debt is not None and debt > cfg.debt_gate:
            excluded[f"부채비율>{cfg.debt_gate:.0f}x"] = excluded.get(f"부채비율>{cfg.debt_gate:.0f}x", 0) + 1
            continue
        if sum(a.ok for a in (trend, domain, fund, flow)) < cfg.min_axes:
            excluded["가용축 부족"] = excluded.get("가용축 부족", 0) + 1
            continue
        rows.append(srow)
        trig_inputs[cd] = (closes, highs, daily_flows)

    # 축별 횡단면 백분위(확보 종목만) → 가중 재정규화 합성
    axis_defs: list[tuple[str, float]] = [
        ("trend", cfg.w_trend), ("domain", cfg.w_domain), ("fund", cfg.w_fund), ("flow", cfg.w_flow),
    ]
    pct_by_axis: dict[str, dict[str, float]] = {}
    for axis, _w in axis_defs:
        got = [(r.srtn_cd, getattr(r, axis).raw) for r in rows if getattr(r, axis).ok]
        pcts = _percentiles([v for _, v in got])
        pct_by_axis[axis] = {cd: p for (cd, _), p in zip(got, pcts)}

    scored: list[SwingRow] = []
    for r in rows:
        pct: dict[str, float] = {}
        num = den = 0.0
        for axis, w in axis_defs:
            p = pct_by_axis[axis].get(r.srtn_cd)
            if p is not None:
                pct[axis] = p
                num += w * p
                den += w
        score = num / den if den else 0.0
        scored.append(replace(r, score=score, pct=pct))
    scored.sort(key=lambda r: r.score, reverse=True)

    recent_strength = (
        recent_event_strength(estore, now or as_of_dt, cfg.catalyst_days) or {}
        if events_by_stock is not None
        else {}
    )
    universe: list[SwingRow] = []
    triggered: list[SwingRow] = []
    for r in scored[: cfg.top_n]:
        trigs: tuple[str, ...] = ()
        if r.score >= cfg.trigger_min_score:
            closes, highs, daily_flows = trig_inputs[r.srtn_cd]
            trigs = _detect_triggers(r, closes, highs, daily_flows, events_by_stock, recent_strength, cfg)
        final = replace(r, triggers=trigs)
        universe.append(final)
        if trigs:
            triggered.append(final)

    coverage = {
        axis: sum(1 for r in rows if getattr(r, axis).ok) for axis, _ in axis_defs
    }
    return SwingResult(
        as_of, universe, res.universe, len(scored), excluded, coverage, tuple(warnings), triggered
    )


# ──────────────────────────── 영속(트리거 이력 — R6/R7 소비) ────────────────────────────

_SWING_DDL = """
CREATE TABLE IF NOT EXISTS swing_universe (
  bas_dt TEXT NOT NULL, srtn_cd TEXT NOT NULL, name TEXT, score REAL,
  axes_json TEXT, fetched_at TEXT,
  UNIQUE(bas_dt, srtn_cd)
);
CREATE TABLE IF NOT EXISTS swing_triggers (
  bas_dt TEXT NOT NULL, srtn_cd TEXT NOT NULL, name TEXT, trigger TEXT NOT NULL,
  score REAL, fetched_at TEXT,
  UNIQUE(bas_dt, srtn_cd, trigger)
);
"""


class SwingStore:
    """유니버스 스냅샷·트리거 이력(append-only) — R6 보고·R7 적중률 채점 소비."""

    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(_SWING_DDL)

    def record(self, result: SwingResult) -> tuple[int, int]:
        fetched = now_kst().isoformat()
        before = self._conn.total_changes
        self._conn.executemany(
            "INSERT OR IGNORE INTO swing_universe VALUES (?,?,?,?,?,?)",
            [
                (
                    result.as_of, r.srtn_cd, r.name, r.score,
                    json.dumps({k: round(v, 4) for k, v in r.pct.items()}, ensure_ascii=False),
                    fetched,
                )
                for r in result.universe
            ],
        )
        n_uni = self._conn.total_changes - before
        before = self._conn.total_changes
        self._conn.executemany(
            "INSERT OR IGNORE INTO swing_triggers VALUES (?,?,?,?,?,?)",
            [
                (result.as_of, r.srtn_cd, r.name, t, r.score, fetched)
                for r in result.triggered
                for t in r.triggers
            ],
        )
        self._conn.commit()
        return n_uni, self._conn.total_changes - before

    def triggers_on(self, bas_dt: str) -> list[tuple[str, str, str, float]]:
        cur = self._conn.execute(
            "SELECT srtn_cd, name, trigger, score FROM swing_triggers WHERE bas_dt=? ORDER BY score DESC",
            (bas_dt,),
        )
        return [(str(r[0]), str(r[1]), str(r[2]), float(r[3])) for r in cur]

    def close(self) -> None:
        self._conn.close()


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    store = MarketStore()
    fstore = FlowStore()
    finstore = FinStore()
    estore = EventStore()
    res = build(store, fstore, finstore, estore)
    store.close()
    fstore.close()
    finstore.close()
    estore.close()
    if not res.universe:
        print("스윙 유니버스 없음 (게이트/축 요건 미충족)")
        return 0
    for w in res.warnings:
        print(f"⚠️ {w}")
    cov = " · ".join(f"{k} {v}" for k, v in res.coverage.items())
    exc = " · ".join(f"{k} {v}" for k, v in res.excluded.items()) or "없음"
    print(
        f"스윙 유니버스 as_of={res.as_of} · 게이트 {res.gate_total} → 점수산출 {res.scored} · "
        f"축 커버리지: {cov} · 제외: {exc}"
    )
    print(f"{'#':>3} {'종목':<12}{'점수':>5} {'추세':>5}{'도메인':>5}{'재무':>5}{'수급':>5}  트리거 / 섹터")
    for i, r in enumerate(res.universe, 1):
        cells = [
            f"{r.pct[a]:.2f}" if a in r.pct else "  — "
            for a in ("trend", "domain", "fund", "flow")
        ]
        trig = ",".join(r.triggers) or "-"
        secs = ",".join(r.sectors) or "미분류"
        print(f"{i:>3} {r.name:<12}{r.score:>5.2f} {cells[0]:>5}{cells[1]:>5}{cells[2]:>5}{cells[3]:>5}  {trig} / {secs}")
    if res.triggered:
        print(f"\n오늘 기회 트리거 {len(res.triggered)}종목: " + ", ".join(
            f"{r.name}({','.join(r.triggers)})" for r in res.triggered
        ))
    else:
        print("\n오늘 기회 트리거 없음")
    if not dry_run:
        sstore = SwingStore()
        n_uni, n_trig = sstore.record(res)
        sstore.close()
        print(f"적재: 유니버스 {n_uni}행 · 트리거 {n_trig}행 → {DEFAULT_DB}")
    return 0


__all__ = [
    "AxisValue",
    "SwingConfig",
    "SwingResult",
    "SwingRow",
    "SwingStore",
    "build",
    "flow_axis",
    "fund_axis",
    "max_drawdown",
    "sector_heat",
    "trend_axis",
    "vol_adjusted_momentum",
]


if __name__ == "__main__":
    raise SystemExit(main())
