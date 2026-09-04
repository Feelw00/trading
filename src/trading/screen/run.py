"""R4 스크리닝 실행 — P-18 가치 코어: 전 유니버스 심사 조립(DB-first, 외부 호출 없음).

P-18(운영자 결재 2026-08-31): 게이트는 가치·건전성만, 사이클은 도구.
- 1패스: 화이트리스트 큐레이션 산업(다중 소속 허용 — 종목이 산업별로 각각 심사됨).
- 2패스: 나머지 전 종목을 KRX 버킷 산업으로 심사(결재 ② 전 상장 확장).
- 국면은 게이트가 아니라 도구 정보 — 레코드에 박제 + 과열이면 `cycle_caution` 플래그
  (결재 ① — 탈락 아님). 국면 레코드 부재 산업도 심사한다(도구 정보 없이, unknown).
- 가치 기준 병행(결재 ③): 산업 내 percentile + 시장 전체 percentile 모두 산출·박제.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from trading.collectors.audit import AuditVerdict, current_opinion
from trading.collectors.base import now_kst
from trading.collectors.status import DEFAULT_DB as STATUS_DB
from trading.collectors.status import KisFlags, StatusStore, classify_kis
from trading.contracts.longterm import CandidateRecord, CyclePhase, CycleRecord, ValuationRecord
from trading.cycle.policy import CURATED_GROUPS, WHITELIST
from trading.cycle.store import CycleStore
from trading.screen.rules import UNAPPLIED_V1, ScreenParams, evaluate, status_filter
from trading.valuation.metrics import percentile_rank
from trading.valuation.store import ValuationStore

MIN_INDUSTRY_GROUP = 3  # 산업 내 상대 위치 최소 표본(밴드 MIN_COMPOSITION과 동일 철학)
STATUS_MAX_AGE_DAYS = 7  # KIS 상태 스냅샷 신선도 — 이보다 오래되면 미적용(오래된 상태로 탈락·통과시키지 않음)


def load_status_inputs(
    *, fy: str | None = None, db_path: Path = STATUS_DB, now: datetime | None = None,
) -> tuple[dict[str, KisFlags], dict[str, AuditVerdict]]:
    """SCREEN-1(v2.16) 입력 — data/status.sqlite의 최신 KIS 상태(신선도 가드) + FY 감사의견 요약.

    DB-first: 외부 호출 없음. fy 미지정 시 재무 DB 최신 연간 사업연도(심사 원장 기준과 동일).
    """
    if fy is None:
        from trading.collectors.fins import FinStore

        fs = FinStore()
        try:
            fy = fs.latest_annual_year()
        finally:
            fs.close()
    cutoff = ((now or now_kst()) - timedelta(days=STATUS_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    store = StatusStore(db_path)
    try:
        flags = {
            sym: classify_kis(row) for sym, row in store.latest_kis_all().items() if row.as_of >= cutoff
        }
        audits = {sym: current_opinion(store.audit_rows(sym, fy)) for sym in store.audit_symbols(fy)}
    finally:
        store.close()
    return flags, audits


@dataclass(frozen=True)
class ScreenSummary:
    evaluated: int
    passed: int
    reject_counts: dict[str, int]
    skipped_industries: list[str]  # P-18: 국면 레코드 부재 산업(심사는 수행 — 도구 정보만 결측)


def run_screen(
    val_store: ValuationStore,
    cycle_store: CycleStore,
    *,
    params: ScreenParams,
    now: datetime | None = None,
    kis_flags: Mapping[str, KisFlags] | None = None,
    audits: Mapping[str, AuditVerdict] | None = None,
) -> tuple[list[CandidateRecord], ScreenSummary]:
    """``kis_flags``·``audits``: SCREEN-1(v2.16) 상태·감사의견 입력(`load_status_inputs`). None이면 해당
    필터는 전 종목 미적용 고지(침묵 생략 금지)."""
    fetched = now or now_kst()
    latest = val_store.all_latest()
    market_pbrs = [v.pbr for v in latest if v.pbr is not None]

    records: list[CandidateRecord] = []
    reject_counter: Counter[str] = Counter()
    skipped: list[str] = []

    def _screen_members(
        industry: str, members: list[ValuationRecord], cyc: CycleRecord | None
    ) -> None:
        # 미분류 버킷은 산업이 아니다 — 산업 내 상대 위치를 산출하지 않는다(시장 기준만).
        pbrs = [] if industry == "미분류" else [v.pbr for v in members if v.pbr is not None]
        phase = cyc.phase if cyc else CyclePhase.UNKNOWN
        for val in members:
            industry_pct = (
                percentile_rank(pbrs, val.pbr)
                if val.pbr is not None and len(pbrs) >= MIN_INDUSTRY_GROUP
                else None
            )
            market_pct = percentile_rank(market_pbrs, val.pbr) if val.pbr is not None else None
            passed, reasons = evaluate(
                val,
                industry=industry,
                secular_decline=cyc.secular_decline if cyc else None,
                industry_pbr_pct=industry_pct,
                market_pbr_pct=market_pct,
                params=params,
            )
            # v2.16 SCREEN-1 하드 필터(운영자 결재 (a) 2026-09-03) — 관리·정지·상폐 의심·감사의견 비적정
            st_reasons, st_unapplied = status_filter(
                kis_flags.get(val.symbol) if kis_flags is not None else None,
                audits.get(val.symbol) if audits is not None else None,
            )
            reasons = reasons + st_reasons
            passed = not reasons
            reject_counter.update(r.split("(")[0] for r in reasons)
            records.append(
                CandidateRecord(
                    # id에 산업 포함 — 다중 소속 종목이 두 산업에서 심사되면 레코드도 둘이다
                    # (동일 id면 store 최신 뷰가 한쪽을 가림 — 전수 박제 원칙 위반)
                    id=f"cand.{fetched.strftime('%Y%m%d')}.{val.symbol}.{industry}",
                    # 회차 키 — 한 실행의 전 레코드가 같은 as_of를 가져야 배치 조회
                    # (store latest_all/latest_passed의 MAX(as_of))가 회차를 온전히 본다
                    as_of=fetched,
                    fetched_at=fetched,
                    source="derived:screen-r4",
                    symbol=val.symbol,
                    industry=industry,
                    sector_krx=val.sector_krx,
                    phase=phase,
                    passed=passed,
                    reject_reasons=reasons,
                    industry_pbr_pct=industry_pct,
                    market_pbr_pct=market_pct,
                    cycle_caution=phase is CyclePhase.OVERHEATED,
                    unapplied=[*UNAPPLIED_V1, *st_unapplied],
                    valuation_ref=val.id,
                    cycle_ref=cyc.id if cyc else None,
                )
            )

    # 1패스 — 화이트리스트 큐레이션 산업(다중 소속: 산업별 각각 심사)
    curated_codes: set[str] = set()
    for industry, group in WHITELIST.items():
        cyc = cycle_store.latest_for_industry(group)
        if cyc is None:
            skipped.append(f"{industry}(국면 레코드 없음 — 도구 정보 없이 심사)")
        allow = set(CURATED_GROUPS.get(group, []))
        curated_codes |= allow
        _screen_members(industry, [v for v in latest if v.symbol in allow], cyc)

    # 2패스 — 나머지 전 종목을 KRX 버킷 산업으로(결재 ② 전 상장)
    rest = [v for v in latest if v.symbol not in curated_codes]
    by_bucket: dict[str, list[ValuationRecord]] = {}
    for v in rest:
        by_bucket.setdefault(v.sector_krx or "미분류", []).append(v)
    for bucket in sorted(by_bucket):
        cyc = cycle_store.latest_for_industry(bucket) if bucket != "미분류" else None
        if cyc is None:
            skipped.append(f"{bucket}(국면 레코드 없음 — 도구 정보 없이 심사)")
        _screen_members(bucket, by_bucket[bucket], cyc)

    summary = ScreenSummary(
        evaluated=len(records),
        passed=sum(1 for r in records if r.passed),
        reject_counts=dict(reject_counter.most_common()),
        skipped_industries=skipped,
    )
    return records, summary


__all__ = ["MIN_INDUSTRY_GROUP", "ScreenSummary", "run_screen"]
