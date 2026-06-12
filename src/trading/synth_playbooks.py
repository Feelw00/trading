"""R5 실행 러너 — ``python -m trading.synth_playbooks``. (score_news 패턴)

ThesisStore 최신 논제 + EventStore 검증 이벤트 + factpack 가격 컨텍스트 → R5 합성 →
PlaybookStore 적재. cron 20:30 슬롯(synth-pm)이 exec 트리거.

가드(설계서 §1·§5, SCHED-1 잡 내부 가드 패턴):
- **장중 실행 거부** — 주문 설계는 장 마감 후에만(``require_market_closed``).
- LLM 장애 시 §9: "초안 갱신 불가, 전일 초안 유지/폐기 선택" **P1 알림** 발화.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from trading.alerts import Alert, AlertDispatcher, Severity
from trading.collectors.base import now_kst
from trading.contracts.factpack import FactPack
from trading.factpack import build_fact_pack_for
from trading.journal.events import EventStore
from trading.journal.playbooks import PlaybookStore
from trading.journal.theses import ThesisStore
from trading.llm import LLMClient, client_from_env
from trading.market_calendar.calendar import (
    MarketGuardError,
    in_krx_session,
    require_market_closed,
)
from trading.rounds.r5 import R5Config, R5Result, run_r5

DEFAULT_THESES_LIMIT = 60
_MACRO_GLOB = "macro_indicators.sqlite"


def _macro_lines() -> list[str]:
    """최신 거시 수집 sqlite에서 verified 지표를 compact 라인으로(reason_news와 동일 규약)."""
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


def _alert_synthesis_failed(detail: str, dispatcher: AlertDispatcher | None) -> None:
    d = dispatcher if dispatcher is not None else AlertDispatcher()
    d.notify(
        Alert(
            severity=Severity.P1,
            what=f"R5 합성 실패 — 주문 초안 갱신 불가 ({detail[:120]})",
            rule="설계서 §9 API 장애 대응",
            action="전일 초안 유지 또는 폐기 선택",
            deadline="오늘 21:00 저녁 결재 보고",
        )
    )
    if dispatcher is None:
        d.store.close()


def run(
    *,
    theses_limit: int = DEFAULT_THESES_LIMIT,
    now: datetime | None = None,
    config: R5Config | None = None,
    client: LLMClient | None = None,
    thesis_store: ThesisStore | None = None,
    event_store: EventStore | None = None,
    playbook_store: PlaybookStore | None = None,
    dispatcher: AlertDispatcher | None = None,
    force: bool = False,
) -> int:
    """논제 → R5 합성 → PlaybookStore. 장중이면 거부(rc=3), LLM 장애면 P1 알림(rc=1).

    ``force``(수동 CLI 전용, cron은 미사용): 장중 가드를 우회한다. R5 입력은 EOD라 장중
    실시간 가격에 휩쓸리지 않고, 산출은 draft → 다음 거래일 아침 arm-check 승인이므로 충동
    집행은 차단된다(설계서 §1·§5 완화, 운영자 결정 2026-06-12). 휴장일·장외는 force 없이도 통과.
    """
    if not force:
        try:
            require_market_closed(now)
        except MarketGuardError as e:
            print(f"R5 거부 — {e} (수동 강제 실행: --force)")
            return 3
    elif in_krx_session(now if now is not None else now_kst()):
        print(
            "R5 장중 강제 실행(--force) — 입력은 EOD, 산출은 draft. "
            "집행은 다음 거래일 아침 /arm-check 승인 후(충동 집행 차단 유지)."
        )

    ts = thesis_store if thesis_store is not None else ThesisStore()
    theses = ts.recent(limit=theses_limit)
    if thesis_store is None:
        ts.close()
    if not theses:
        print("R5 스킵 — 논제 없음 (먼저 reason-theses)")
        return 0

    es = event_store if event_store is not None else EventStore()
    events = es.recent(limit=100)
    if event_store is None:
        es.close()

    srtns = sorted({t.id.split(".")[2] for t in theses if len(t.id.split(".")) > 3})
    packs: list[FactPack] = []
    for s in srtns:
        fp = build_fact_pack_for(s)
        if fp is not None:
            packs.append(fp)

    llm = client if client is not None else client_from_env()
    result: R5Result = run_r5(
        llm, theses, events, packs,
        macro_lines=_macro_lines(), now=now, config=config or R5Config(),
    )

    if result.error is not None:
        print(f"R5 실패 — {result.error}")
        _alert_synthesis_failed(result.error, dispatcher)
        return 1

    ps = playbook_store if playbook_store is not None else PlaybookStore()
    run_day = (now if now is not None else now_kst()).date().isoformat()
    stored = ps.append_run(
        result.playbooks, result.drafts,
        as_of=run_day,  # 비거래 런에도 합성 일자 기록(보고 표기·이력 추적)
        scenario_tree=result.scenario_tree,
        checklist=result.checklist,
    )
    if playbook_store is None:
        ps.close()

    print(
        f"R5 합성: 논제 {len(theses)} → 플레이북 {len(result.playbooks)} "
        f"/ 초안 {len(result.drafts)} / 폐기 {result.rejected} / 적재 {stored}"
    )
    if not result.playbooks:
        print("  비거래 — 조건을 충족한 플레이북 없음(정상 경로)")
    for r in result.rejected_reasons[:5]:
        print(f"  reject: {r}")
    return 0


def main() -> int:
    import sys

    return run(force="--force" in sys.argv[1:])


__all__ = ["run"]


if __name__ == "__main__":
    raise SystemExit(main())
