"""대시보드 데이터 조회 — DB 직독(P-15: cron 갱신 = 자동 최신화). 순수 조회, 판정 없음."""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from trading.contracts.longterm import CandidateRecord, CycleRecord, phase_ko
from trading.cycle.policy import WHITELIST
from trading.cycle.store import CycleStore
from trading.screen.store import CandidateStore


@dataclass(frozen=True)
class Freshness:
    label: str
    detail: str
    window: float | None = None   # 진행 바(현재)
    target: float | None = None   # 진행 바(목표)


def _q1(db: str, sql: str) -> tuple[object, ...] | None:
    path = Path("data") / db
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute(sql).fetchone()
            return tuple(row) if row is not None else None
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return None


def industry_rows() -> list[CycleRecord]:
    """산업별 최신 온도계 — PBR 밴드 위치 오름차순(싼 곳부터)."""
    store = CycleStore()
    try:
        records = store.all_latest()
    finally:
        store.close()
    return sorted(
        records,
        key=lambda r: (
            r.axes_primary.sector_pbr_band_pct
            if r.axes_primary.sector_pbr_band_pct is not None
            else 2.0
        ),
    )


def phase_transitions() -> list[tuple[str, str, str]]:
    """직전 산출 대비 국면이 바뀐 산업 [(산업, 이전, 현재)] — 한글 라벨."""
    store = CycleStore()
    try:
        recent = store.recent_phases(n=2)
    finally:
        store.close()
    out: list[tuple[str, str, str]] = []
    for industry, phases in sorted(recent.items()):
        if len(phases) == 2 and phases[0] != phases[1]:
            from trading.contracts.longterm import CyclePhase

            out.append(
                (industry, phase_ko(CyclePhase(phases[1])), phase_ko(CyclePhase(phases[0])))
            )
    return out


def screen_funnel() -> tuple[list[tuple[str, int]], list[CandidateRecord]]:
    """R4 깔때기 단계별 잔존 수 + 통과 후보(최신 회차)."""
    store = CandidateStore()
    try:
        records = store.latest_run()
    finally:
        store.close()

    def survives(rec: CandidateRecord, *prefixes: str) -> bool:
        return not any(r.startswith(prefixes) for r in rec.reject_reasons)

    zone = [r for r in records if survives(r, "발동 존 아님", "구조적 사양")]
    value = [r for r in zone if survives(r, "가치 미달", "산업 내 PBR", "PBR 산출 불가")]
    health = [
        r
        for r in value
        if survives(r, "적자", "흑자", "부채비율", "가치 함정", "만성 저수익", "수익성", "최신 연간")
    ]
    passed = [r for r in records if r.passed]
    stages = [
        ("평가(화이트리스트 멤버)", len(records)),
        ("발동 존(바닥·회복 산업)", len(zone)),
        ("가치(산업 내 PBR 하위 40%)", len(value)),
        ("생존력·수익성", len(health)),
        ("통과", len(passed)),
    ]
    return stages, passed


def freshness_rows() -> list[Freshness]:
    out: list[Freshness] = []
    r = _q1("market.sqlite", "SELECT COUNT(DISTINCT bas_dt), MAX(bas_dt) FROM daily_quotes")
    out.append(Freshness("시세", f"{r[0]}거래일 · 최신 {r[1]}" if r and r[0] else "비어 있음"))
    r = _q1(
        "fins.sqlite",
        "SELECT COUNT(DISTINCT srtn_cd), "
        "COUNT(DISTINCT srtn_cd||bsns_year) FILTER (WHERE reprt_code='11011') FROM fin_facts",
    )
    out.append(Freshness("재무", f"{r[0]}종목 · 연간 {r[1]}건" if r and r[0] else "비어 있음"))
    r = _q1(
        "flows.sqlite",
        "SELECT COUNT(DISTINCT code), COUNT(DISTINCT bas_dt) FROM investor_flows WHERE scope='stock'",
    )
    if r and r[0]:
        window = float(str(r[1]))
        out.append(
            Freshness("수급 창", f"{r[0]}종목", window=window, target=60)
        )
    else:
        out.append(Freshness("수급 창", "비어 있음"))
    r = _q1(
        "toss_facts.sqlite",
        "SELECT COUNT(DISTINCT symbol), COUNT(DISTINCT date), MAX(date) FROM stock_daily",
    )
    out.append(
        Freshness("공매도·대차·신용", f"{r[0]}종목 · {r[1]}일자 · 최신 {r[2]}" if r and r[0] else "비어 있음")
    )
    r = _q1("valuation.sqlite", "SELECT COUNT(DISTINCT symbol), substr(MAX(as_of),1,10) FROM valuations")
    out.append(Freshness("밸류에이션", f"{r[0]}종목 · as_of {r[1]}" if r and r[0] else "비어 있음"))
    return out


def whitelist_groups() -> set[str]:
    return set(WHITELIST.values())


def stock_names() -> dict[str, str]:
    from trading.collectors.market import MarketStore
    from trading.sectors import KRX_SOURCE

    store = MarketStore()
    try:
        return store.sector_names(KRX_SOURCE)
    finally:
        store.close()


def passed_delta() -> tuple[set[str], set[str]]:
    """최근 2회차 통과 심볼 비교 — (신규 진입, 이탈). 회차 1개뿐이면 (∅, ∅)."""
    path = Path("data") / "candidates.sqlite"
    if not path.exists():
        return set(), set()
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return set(), set()
    try:
        as_ofs = [
            str(r[0])
            for r in conn.execute("SELECT DISTINCT as_of FROM candidates ORDER BY as_of DESC LIMIT 2")
        ]
        if len(as_ofs) < 2:
            return set(), set()

        def passed_at(as_of: str) -> set[str]:
            return {
                str(r[0])
                for r in conn.execute(
                    "SELECT symbol FROM candidates c WHERE as_of=? AND passed=1 "
                    "AND version = (SELECT MAX(version) FROM candidates WHERE id = c.id)",
                    (as_of,),
                )
            }

        now, prev = passed_at(as_ofs[0]), passed_at(as_ofs[1])
        return now - prev, prev - now
    except sqlite3.OperationalError:
        return set(), set()
    finally:
        conn.close()


__all__ = [
    "Freshness",
    "freshness_rows",
    "industry_rows",
    "phase_transitions",
    "screen_funnel",
    "whitelist_groups",
]
