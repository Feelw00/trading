"""R2 실행 러너 — ``python -m trading.score_news``. (collect_news 패턴)

코어(``rounds.r2``: 배치·프롬프트·파싱)는 순수 라이브러리로 두고 여기서 **오케스트레이션만**:
스크리너 후보(universe) + ``NewsStore`` 최근 뉴스 → R1 게이트 → R2(배치 LLM) → ``EventStore`` 적재.
LLM은 ``client_from_env()``(claude -p, 모델 .env 주입) — 비용 억제는 **저단가 모델(R2_MODEL)** + 배치.
추후 openclaw cron(06:30/16:30 R2 슬롯)이 이 커맨드를 exec 트리거.
"""

import sys

from trading.collectors.market import MarketStore
from trading.collectors.news import NewsStore
from trading.gates.news import gate_news
from trading.journal.events import EventStore
from trading.llm import LLMClient, client_from_env
from trading.rounds.r2 import BatchProgress, R2Config, R2Result, run_r2
from trading.screener import ScreenConfig, screen

DEFAULT_TOP_N = 15
DEFAULT_NEWS_LIMIT = 500


def run(
    top_n: int = DEFAULT_TOP_N,
    *,
    news_limit: int = DEFAULT_NEWS_LIMIT,
    config: R2Config | None = None,
    client: LLMClient | None = None,
    store: EventStore | None = None,
) -> int:
    """후보 universe + 최근 뉴스 → R1 게이트 → R2 → EventStore. 종료코드 반환."""
    mstore = MarketStore()
    res = screen(mstore, ScreenConfig(top_n=top_n))
    mstore.close()
    if not res.candidates:
        print("R2 스킵 — 스크리너 후보 없음")
        return 0
    candidates = [(c.srtn_cd, c.name) for c in res.candidates]

    nstore = NewsStore()
    items = nstore.recent(limit=news_limit)
    nstore.close()
    if not items:
        print("R2 스킵 — 적재된 뉴스 없음 (먼저 /collect-news)")
        return 0

    verdicts = gate_news(items)
    llm = client if client is not None else client_from_env()
    es = store if store is not None else EventStore()

    stored = 0

    def _on_batch(p: BatchProgress) -> None:
        nonlocal stored
        if p.events:
            stored += es.append(p.events)
        err = f" ERR={p.error[:60]}" if p.error else ""
        print(
            f"  [{p.index:>2}/{p.total}] {p.key}: events={len(p.events)} "
            f"rejected={p.rejected}{err}",
            flush=True,
        )

    result: R2Result = run_r2(
        llm, verdicts, candidates,
        config=config or R2Config(),
        on_batch=_on_batch,
    )

    if store is None:
        es.close()

    print(
        f"R2 분류·스코어 as_of={res.as_of}: 이벤트 {len(result.events)} 적재 {stored} "
        f"/ 배치 {result.batches} 폐기 {result.rejected} LLM에러 {len(result.batch_errors)}"
    )
    for r in result.rejected_reasons[:5]:
        print(f"  reject: {r}")
    for e in result.batch_errors[:5]:
        print(f"  llm-error: {e}")
    return 0


def main() -> int:
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TOP_N
    return run(top_n)


__all__ = ["run"]


if __name__ == "__main__":
    raise SystemExit(main())
