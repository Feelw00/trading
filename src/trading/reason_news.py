"""R3 실행 러너 — ``python -m trading.reason_news``. (score_news 패턴)

EventStore의 촉매 보유 종목 → 종목별 (factpack + 촉매 + 거시 백드롭) → R3 3페르소나 →
ThesisStore 적재. 촉매가 있는 종목만 분석(비용·관련성). LLM은 ``client_from_env()``(claude -p).
추후 openclaw cron(R3 슬롯)이 exec 트리거.

**P-9 3단계(스윙 승격):** 당일 스윙 기회 트리거 발화 종목도 후보에 합류(점수순 상한
``max_swing``). 촉매가 없어도 "스윙 승격 근거" 라인(코드 계산 — 트리거 유형·점수·as_of)이
R3 슬라이스에 주입되어 grounded 분석이 가능하다. 산출 논제는 R5가 자동 소비(승격→결재 흐름).
"""

import sqlite3
import sys
from pathlib import Path

from trading.factpack import build_fact_pack_for
from trading.journal.events import EventStore
from trading.journal.theses import ThesisStore
from trading.llm import LLMClient, client_from_env
from trading.rounds.r3 import R3Config, run_r3

DEFAULT_MAX_CANDIDATES = 10
DEFAULT_MAX_SWING = 5  # 스윙 승격 상한(비용 가드 — 트리거 다발일도 점수 상위만)
_MACRO_GLOB = "macro_indicators.sqlite"


def _macro_lines() -> list[str]:
    """최신 거시 수집 sqlite에서 verified 지표를 compact 라인으로(없으면 빈 리스트)."""
    base = Path(".runtime") / "collect"
    if not base.exists():
        return []
    dbs = sorted(base.glob(f"*/{_MACRO_GLOB}"))
    if not dbs:
        return []
    try:
        conn = sqlite3.connect(str(dbs[-1]))
        rows = conn.execute(
            "SELECT name, value, unit, as_of FROM facts WHERE verified=1 GROUP BY name "
            "ORDER BY name"
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []
    return [f"{n}: {v}{u or ''} (as_of {a})" for n, v, u, a in rows]


def _candidate_srtns(events_store: EventStore, *, limit: int) -> list[str]:
    """촉매(affected) 보유 종목 — 최신 이벤트에서 distinct srtn_cd."""
    seen: list[str] = []
    for e in events_store.recent(limit=500):
        for a in e.affected:
            if a.srtn_cd not in seen:
                seen.append(a.srtn_cd)
    return seen[:limit]


def _swing_promotions(*, limit: int) -> dict[str, str]:
    """스윙 승격(P-9 3단계) — 최신 스냅샷 트리거 발화 종목 → {srtn: 승격 근거 라인}.

    근거 라인은 전부 코드 계산치(트리거 유형·스윙 점수·as_of) — R3 슬라이스에 주입되어
    촉매 없는 승격 종목도 "왜 후보인지"가 grounded된다. 스냅샷 없으면 빈 dict.
    """
    from trading.swing import SwingStore

    store = SwingStore()
    day = store.latest_bas_dt()
    if day is None:
        store.close()
        return {}
    merged: dict[str, tuple[float, list[str]]] = {}
    for cd, _name, trigger, score in store.triggers_on(day):
        if cd in merged:
            merged[cd][1].append(trigger)
        else:
            merged[cd] = (score, [trigger])
    store.close()
    top = sorted(merged.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
    return {
        cd: (
            f"스윙 승격 근거(P-9, as_of {day}): 기회 트리거 {', '.join(trigs)} 발화 · "
            f"스윙 품질 점수 {score:.2f} (4축 백분위 가중평균 — 코드 계산치)"
        )
        for cd, (score, trigs) in top
    }


def run(
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    max_swing: int = DEFAULT_MAX_SWING,
    config: R3Config | None = None,
    client: LLMClient | None = None,
    event_store: EventStore | None = None,
    thesis_store: ThesisStore | None = None,
) -> int:
    """촉매 보유 종목 + 스윙 승격 종목 → R3 페르소나 분석 → ThesisStore. 종료코드 반환."""
    es = event_store if event_store is not None else EventStore()
    srtns = _candidate_srtns(es, limit=max_candidates)
    swing_notes = _swing_promotions(limit=max_swing)
    n_promoted = 0
    for cd in swing_notes:
        if cd not in srtns:
            srtns.append(cd)
            n_promoted += 1
    if not srtns:
        print("R3 스킵 — 촉매 보유·스윙 승격 종목 없음 (먼저 score-news/verify-catalysts 또는 daily-eod)")
        if event_store is None:
            es.close()
        return 0
    if n_promoted:
        print(f"스윙 승격(P-9): {n_promoted}종목 합류 (트리거 발화, 상한 {max_swing})")

    macro = _macro_lines()
    llm = client if client is not None else client_from_env()
    ts = thesis_store if thesis_store is not None else ThesisStore()
    total_theses = 0
    total_rejected = 0
    total_errors = 0
    for srtn in srtns:
        fp = build_fact_pack_for(srtn)
        name = fp.name if fp is not None else srtn
        events = es.for_srtn(srtn)
        extra = (swing_notes[srtn],) if srtn in swing_notes else ()
        result = run_r3(
            llm, (srtn, name), fp, events,
            macro_lines=macro, extra_lines=extra, config=config or R3Config(),
        )
        stored = ts.append(srtn, result.theses)
        total_theses += stored
        total_rejected += result.rejected
        total_errors += len(result.persona_errors)
        err = f", LLM에러 {len(result.persona_errors)}" if result.persona_errors else ""
        print(f"  {srtn} {name}: 논제 {stored} (폐기 {result.rejected}, 촉매 {len(events)}{err})")
        for pe in result.persona_errors:
            print(f"     persona-error: {pe}")

    if thesis_store is None:
        ts.close()
    if event_store is None:
        es.close()
    print(
        f"R3 페르소나 분석: 종목 {len(srtns)} / 논제 적재 {total_theses} "
        f"/ 폐기 {total_rejected} / LLM에러 {total_errors}"
    )
    return 0


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_MAX_CANDIDATES
    return run(max_candidates=n)


__all__ = ["run"]


if __name__ == "__main__":
    raise SystemExit(main())
