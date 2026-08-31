"""종목 페이지 데이터 조회 — DB 직독, 순수 조회(판정 없음). W2."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from trading.collectors.fins import FinStore, parse_amount
from trading.collectors.market import MarketStore
from trading.collectors.toss_facts import TossFactsStore
from trading.contracts.longterm import CandidateRecord, CycleRecord, DossierRecord, ValuationRecord
from trading.cycle.bands import SectorYear, build_sector_years, full_year_ends
from trading.cycle.policy import CURATED_GROUPS, WHITELIST
from trading.cycle.store import CycleStore
from trading.dossier import DossierStore
from trading.screen.store import CandidateStore
from trading.sectors import KRX_SOURCE
from trading.valuation.store import ValuationStore

GROUP_TO_INDUSTRY = {group: industry for industry, group in WHITELIST.items()}


@dataclass(frozen=True)
class StockRow:
    symbol: str
    name: str
    group: str | None            # 밴드 그룹(큐레이션 우선, 없으면 KRX 업종)
    industry: str | None         # 화이트리스트 산업명(해당 시)
    val: ValuationRecord
    r4: str                      # "통과" | 첫 탈락 사유 | "평가 대상 아님"


@dataclass(frozen=True)
class BandContext:
    group: str
    cycle: CycleRecord | None
    pbr_lo: float | None
    pbr_hi: float | None
    pbr_now: float | None
    margin_lo: float | None
    margin_hi: float | None
    amplitude: float | None      # 사이클 진폭 — PBR 밴드 최고/최저 배율


@dataclass(frozen=True)
class StockDetail:
    row: StockRow
    closes: list[tuple[str, float]]                 # (일자, 종가) 오름차순
    annual: list[tuple[str, dict[str, float | None]]]  # 연도 desc
    band: BandContext | None
    flows: list[tuple[str, float | None, float | None, float | None]]  # (일자, 개인, 외인, 기관) 오름차순
    short_rates: list[tuple[str, float]]            # (일자, 공매도 비중) 오름차순
    lending_balance: list[tuple[str, float]]        # (일자, 대차잔고 수량) 오름차순
    candidate: CandidateRecord | None
    dossier: DossierRecord | None


def _group_of(symbol: str, sector_krx: str | None) -> str | None:
    for group, codes in CURATED_GROUPS.items():
        if symbol in codes:
            return group
    return sector_krx


def stock_rows() -> list[StockRow]:
    vstore, cstore = ValuationStore(), CandidateStore()
    market = MarketStore()
    try:
        vals = vstore.all_latest()
        cands = {c.symbol: c for c in cstore.latest_run()}
        names = market.sector_names(KRX_SOURCE)
    finally:
        vstore.close()
        cstore.close()
        market.close()

    rows: list[StockRow] = []
    for val in vals:
        cand = cands.get(val.symbol)
        if cand is None:
            r4 = "평가 대상 아님(화이트리스트 밖)"
        elif cand.passed:
            r4 = "통과"
        else:
            r4 = cand.reject_reasons[0] if cand.reject_reasons else "탈락"
        group = _group_of(val.symbol, val.sector_krx)
        rows.append(
            StockRow(
                symbol=val.symbol,
                name=names.get(val.symbol, val.symbol),
                group=group,
                industry=cand.industry if cand else (GROUP_TO_INDUSTRY.get(group or "")),
                val=val,
                r4=r4,
            )
        )
    rows.sort(key=lambda r: (r.val.sector_pbr_pct if r.val.sector_pbr_pct is not None else 2.0))
    return rows


def _flows_series(symbol: str) -> list[tuple[str, float | None, float | None, float | None]]:
    path = Path("data") / "flows.sqlite"
    if not path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    try:
        rows = conn.execute(
            "SELECT bas_dt, prsn_ntby_tr_pbmn, frgn_ntby_tr_pbmn, orgn_ntby_tr_pbmn "
            "FROM investor_flows WHERE scope='stock' AND code=? ORDER BY bas_dt",
            (symbol,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [
        (str(r[0]), parse_amount(r[1]), parse_amount(r[2]), parse_amount(r[3])) for r in rows
    ]


def _band_context(group: str | None) -> BandContext | None:
    if group is None:
        return None
    fins, market, cyc_store = FinStore(), MarketStore(), CycleStore()
    try:
        year_ends = full_year_ends(market)
        if not year_ends:
            return None
        sector_years = build_sector_years(
            fins, market, year_end_dates=year_ends, extra_groups=CURATED_GROUPS
        )
        rows: list[SectorYear] | None = sector_years.get(group)
        cycle = cyc_store.latest_for_industry(group)
    finally:
        fins.close()
        market.close()
        cyc_store.close()
    if not rows:
        return None
    hist = [r.pbr for r in rows if r.year != "current" and r.pbr is not None]
    now = next((r.pbr for r in rows if r.year == "current"), None)
    margins = [r.margin for r in rows if r.margin is not None]
    pbr_lo, pbr_hi = (min(hist), max(hist)) if hist else (None, None)
    amplitude = (pbr_hi / pbr_lo) if pbr_lo and pbr_hi and pbr_lo > 0 else None
    return BandContext(
        group=group,
        cycle=cycle,
        pbr_lo=pbr_lo,
        pbr_hi=pbr_hi,
        pbr_now=now,
        margin_lo=min(margins) if margins else None,
        margin_hi=max(margins) if margins else None,
        amplitude=amplitude,
    )


def stock_detail(symbol: str) -> StockDetail | None:
    rows = [r for r in stock_rows() if r.symbol == symbol]
    if not rows:
        return None
    row = rows[0]

    market, fins = MarketStore(), FinStore()
    cstore, dstore, tstore = CandidateStore(), DossierStore(), TossFactsStore()
    try:
        closes = market.closes_for(symbol, "20210101")
        annual = fins.annual_series(symbol)
        cand = next((c for c in cstore.latest_run() if c.symbol == symbol), None)
        dossier = dstore.latest_for_symbol(symbol)
        shorts = [
            (d, float(str(p.get("shortSellingVolumeRate"))))
            for d, p in reversed(tstore.series("short-selling", symbol))
            if p.get("shortSellingVolumeRate") is not None
        ]
        lending = [
            (d, float(str(p.get("balanceQuantity"))))
            for d, p in reversed(tstore.series("securities-lending", symbol))
            if p.get("balanceQuantity") is not None
        ]
    finally:
        market.close()
        fins.close()
        cstore.close()
        dstore.close()
        tstore.close()

    return StockDetail(
        row=row,
        closes=closes,
        annual=annual,
        band=_band_context(row.group),
        flows=_flows_series(symbol),
        short_rates=shorts,
        lending_balance=lending,
        candidate=cand,
        dossier=dossier,
    )


__all__ = ["BandContext", "GROUP_TO_INDUSTRY", "StockDetail", "StockRow", "stock_detail", "stock_rows"]
