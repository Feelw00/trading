"""ValuationRecord 조립 — fins(DART 재무) × market(data.go.kr 시총) × sectors(KRX 업종).

DB-first(PIVOT-8): 외부 호출 없음 — 이미 적재된 스토어만 읽는다.
시총(mrkt_tot_amt)·상장주식수는 data.go.kr 일별시세 실관측 필드(market.py `_SRC`).
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from trading.collectors.base import now_kst
from trading.collectors.fins import FinStore, parse_amount
from trading.collectors.market import MarketStore
from trading.contracts.longterm import ValuationRecord
from trading.sectors import KRX_SOURCE
from trading.valuation.metrics import Metrics, derive_metrics, loss_years, percentile_rank

KST = ZoneInfo("Asia/Seoul")

# 섹터 percentile 최소 그룹 크기 — 이보다 작으면 상대 위치가 무의미해 None(결측 정직)
MIN_SECTOR_GROUP = 3


@dataclass(frozen=True)
class BuildSummary:
    total: int
    with_pbr: int
    with_per: int
    with_sector_pct: int


@dataclass(frozen=True)
class _Row:
    srtn_cd: str
    bas_dt: str
    sector: str | None
    metrics: Metrics
    losses: int | None
    observed: int
    roe_median: float | None
    roe_observed: int
    basis: str
    evidence: list[str]


def _roe_median(annuals: list[tuple[str, dict[str, float | None]]]) -> tuple[float | None, int]:
    """관측 연간(최대 5년) ROE 중앙값 — 사이클 관통 수익성(가치 함정 방어, policy-v1.2).

    ROE = 당기순이익/자본총계, 자본잠식(equity<=0) 연도는 관측 제외. 관측 0이면 None.
    """
    roes: list[float] = []
    for _year, vals in annuals[:5]:
        ni, eq = vals["net_income"], vals["equity"]
        if ni is not None and eq is not None and eq > 0:
            roes.append(ni / eq)
    if not roes:
        return None, 0
    roes.sort()
    n = len(roes)
    mid = (roes[n // 2] if n % 2 else (roes[n // 2 - 1] + roes[n // 2]) / 2)
    return mid, n


def _as_of_close(bas_dt: str) -> datetime:
    """YYYYMMDD → 그날 15:30 KST(정규장 마감) — 시총 기준 시점."""
    return datetime.strptime(bas_dt, "%Y%m%d").replace(hour=15, minute=30, tzinfo=KST)


def build_valuation_records(
    fin_store: FinStore,
    market_store: MarketStore,
    *,
    now: datetime | None = None,
) -> tuple[list[ValuationRecord], BuildSummary]:
    """재무가 적재된 전 종목의 ValuationRecord 산출. 결측은 None으로 박제."""
    fetched = now or now_kst()
    sector_map = market_store.sector_map(KRX_SOURCE)

    rows: list[_Row] = []
    for srtn_cd in sorted(fin_store.symbols()):
        quote = market_store.latest_quote(srtn_cd)
        if quote is None:
            continue  # 시총 없음 — 밸류에이션 불가(스킵, 지어내지 않음)
        bas_dt = str(quote[0])
        cap = parse_amount(quote[3])
        snap = fin_store.snapshot_for(srtn_cd)
        if snap is None:
            continue
        annual = (
            snap
            if snap.reprt_code == "11011"
            else fin_store.snapshot_for(srtn_cd, annual_only=True)
        )
        metrics = derive_metrics(
            mrkt_tot_amt=cap,
            equity=snap.equity,
            liabilities=snap.liabilities,
            annual_net_income=annual.net_income if annual else None,
            annual_revenue=annual.revenue if annual else None,
            annual_equity=annual.equity if annual else None,
        )
        annuals = fin_store.annual_series(srtn_cd)
        losses, observed = loss_years([vals["net_income"] for _year, vals in annuals])
        roe_median, roe_observed = _roe_median(annuals)
        sectors = sector_map.get(srtn_cd, [])
        basis = f"BS {snap.bsns_year}/{snap.reprt_code}"
        if annual is not None and annual is not snap:
            basis += f" · IS {annual.bsns_year}/{annual.reprt_code}"
        rows.append(
            _Row(
                srtn_cd=srtn_cd,
                bas_dt=bas_dt,
                sector=sectors[0] if sectors else None,
                metrics=metrics,
                losses=losses,
                observed=min(observed, 5),
                roe_median=roe_median,
                roe_observed=roe_observed,
                basis=basis,
                evidence=[
                    f"fins:{srtn_cd}:{snap.bsns_year}/{snap.reprt_code}",
                    f"market:{srtn_cd}:{bas_dt}",
                ],
            )
        )

    # 섹터 내 PBR 하위 percentile — 그룹이 작으면 None(상대 위치 무의미)
    groups: dict[str, list[float]] = {}
    for row in rows:
        if row.sector is not None and row.metrics.pbr is not None:
            groups.setdefault(row.sector, []).append(row.metrics.pbr)

    records: list[ValuationRecord] = []
    for row in rows:
        sector_pct: float | None = None
        if row.sector is not None and row.metrics.pbr is not None:
            group = groups.get(row.sector, [])
            if len(group) >= MIN_SECTOR_GROUP:
                sector_pct = percentile_rank(group, row.metrics.pbr)
        records.append(
            ValuationRecord(
                id=f"val.{row.bas_dt}.{row.srtn_cd}",
                as_of=_as_of_close(row.bas_dt),
                fetched_at=fetched,
                source="derived:fins+market",
                symbol=row.srtn_cd,
                sector_krx=row.sector,
                pbr=row.metrics.pbr,
                per=row.metrics.per,
                psr=row.metrics.psr,
                roe=row.metrics.roe,
                roe_median_5y=row.roe_median,
                roe_years_observed=row.roe_observed,
                debt_ratio=row.metrics.debt_ratio,
                loss_years_5y=row.losses,
                loss_years_observed=row.observed,
                sector_pbr_pct=sector_pct,
                fin_basis=row.basis,
                evidence=row.evidence,
            )
        )

    summary = BuildSummary(
        total=len(records),
        with_pbr=sum(1 for x in records if x.pbr is not None),
        with_per=sum(1 for x in records if x.per is not None),
        with_sector_pct=sum(1 for x in records if x.sector_pbr_pct is not None),
    )
    return records, summary


def main() -> int:
    from trading.valuation.store import DEFAULT_DB, ValuationStore

    fin_store = FinStore()
    market_store = MarketStore()
    store = ValuationStore()
    try:
        records, s = build_valuation_records(fin_store, market_store)
        for rec in records:
            store.append(rec)
        print(
            f"밸류에이션 산출: {s.total}종목 · PBR {s.with_pbr} · PER(연간) {s.with_per} · "
            f"섹터상대 {s.with_sector_pct} → {DEFAULT_DB}"
        )
        if not records:
            print("산출 0건 — fins/market 스토어 적재 여부를 확인하라(수집 선행).")
    finally:
        fin_store.close()
        market_store.close()
        store.close()
    return 0


__all__ = ["BuildSummary", "MIN_SECTOR_GROUP", "build_valuation_records", "main"]
