"""R4 스크리닝 실행 — 화이트리스트 산업별 후보 판정 조립(DB-first, 외부 호출 없음)."""

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from trading.collectors.base import now_kst
from trading.contracts.longterm import CandidateRecord, CyclePhase, ValuationRecord
from trading.cycle.policy import CURATED_GROUPS, WHITELIST
from trading.cycle.store import CycleStore
from trading.screen.rules import UNAPPLIED_V1, ScreenParams, evaluate
from trading.valuation.metrics import percentile_rank
from trading.valuation.store import ValuationStore

MIN_INDUSTRY_GROUP = 3  # 산업 내 상대 위치 최소 표본(밴드 MIN_COMPOSITION과 동일 철학)


@dataclass(frozen=True)
class ScreenSummary:
    evaluated: int
    passed: int
    reject_counts: dict[str, int]
    skipped_industries: list[str]  # 국면 레코드 부재 등으로 판정 자체를 못 한 산업


def _members(
    industry_group: str, latest: list[ValuationRecord]
) -> list[ValuationRecord]:
    if industry_group in CURATED_GROUPS:
        allow = set(CURATED_GROUPS[industry_group])
        return [v for v in latest if v.symbol in allow]
    return [v for v in latest if v.sector_krx == industry_group]


def run_screen(
    val_store: ValuationStore,
    cycle_store: CycleStore,
    *,
    params: ScreenParams,
    now: datetime | None = None,
) -> tuple[list[CandidateRecord], ScreenSummary]:
    fetched = now or now_kst()
    latest = val_store.all_latest()

    records: list[CandidateRecord] = []
    reject_counter: Counter[str] = Counter()
    skipped: list[str] = []

    for industry, group in WHITELIST.items():
        cyc = cycle_store.latest_for_industry(group)
        if cyc is None:
            skipped.append(f"{industry}(국면 레코드 없음 — R3 미산출)")
            continue
        members = _members(group, latest)
        pbrs = [v.pbr for v in members if v.pbr is not None]

        for val in members:
            industry_pct = (
                percentile_rank(pbrs, val.pbr)
                if val.pbr is not None and len(pbrs) >= MIN_INDUSTRY_GROUP
                else None
            )
            passed, reasons = evaluate(
                val,
                industry=industry,
                phase=cyc.phase,
                secular_decline=cyc.secular_decline,
                industry_pbr_pct=industry_pct,
                params=params,
            )
            reject_counter.update(r.split("(")[0] for r in reasons)
            records.append(
                CandidateRecord(
                    id=f"cand.{fetched.strftime('%Y%m%d')}.{val.symbol}",
                    as_of=cyc.as_of,
                    fetched_at=fetched,
                    source="derived:screen-r4",
                    symbol=val.symbol,
                    industry=industry,
                    sector_krx=val.sector_krx,
                    phase=cyc.phase,
                    passed=passed,
                    reject_reasons=reasons,
                    industry_pbr_pct=industry_pct,
                    unapplied=list(UNAPPLIED_V1),
                    valuation_ref=val.id,
                    cycle_ref=cyc.id,
                )
            )

    summary = ScreenSummary(
        evaluated=len(records),
        passed=sum(1 for r in records if r.passed),
        reject_counts=dict(reject_counter.most_common()),
        skipped_industries=skipped,
    )
    return records, summary


__all__ = ["MIN_INDUSTRY_GROUP", "ScreenSummary", "run_screen"]
