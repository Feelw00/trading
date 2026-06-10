"""R4 실행 러너 — ``python -m trading.verify_news``. (score_news 패턴)

EventStore의 R2 이벤트 → 선별 적대검증 → ``verification`` 부착한 새 version을 EventStore에 append.
근거 기사는 ``NewsStore.by_ids`` 로 끌어온다. LLM은 ``client_from_env()``(claude -p, 모델 .env).
추후 openclaw cron(06:45/16:45 R4 슬롯)이 이 커맨드를 exec 트리거.
"""

import sys

from trading.collectors.news import NewsStore
from trading.journal.events import EventStore
from trading.llm import LLMClient, client_from_env
from trading.rounds.r4 import R4Config, R4Result, run_r4

DEFAULT_EVENT_LIMIT = 200


def run(
    *,
    event_limit: int = DEFAULT_EVENT_LIMIT,
    config: R4Config | None = None,
    client: LLMClient | None = None,
    store: EventStore | None = None,
) -> int:
    """최신 이벤트 → 선별 적대검증 → verification 부착 새 version append. 종료코드 반환."""
    es = store if store is not None else EventStore()
    events = es.recent(limit=event_limit)
    if not events:
        print("R4 스킵 — 적재된 이벤트 없음 (먼저 score-news)")
        if store is None:
            es.close()
        return 0

    need = sorted({eid for e in events for eid in e.evidence})
    nstore = NewsStore()
    evidence_by_id = {n.id: n for n in nstore.by_ids(need)}
    nstore.close()

    llm = client if client is not None else client_from_env()
    result: R4Result = run_r4(llm, events, evidence_by_id, config=config or R4Config.from_env())
    stored = es.append(result.verified)
    if store is None:
        es.close()

    print(
        f"R4 적대검증: 선별 {result.selected} / 검증 {len(result.verified)} "
        f"/ 생존(confirmed) {result.confirmed} / 적재 {stored}"
    )
    return 0


def main() -> int:
    return run()


__all__ = ["run"]


if __name__ == "__main__":
    raise SystemExit(main())
