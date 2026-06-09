"""뉴스 수집 독립 커맨드 — ``python -m trading.collect_news [top_n]``.

코어(``collectors.news``: 정규화·dedup·landing·라우터)는 순수 라이브러리로 두고,
여기서 **오케스트레이션만** 한다: env→백엔드 구성, 스크리너 후보→쿼리플랜, 실행·landing·보고.
COLLECT-4 하네스: 승인 search 어댑터만, 미연결/실패는 blocked(빈 결과 날조 금지).
추후 openclaw cron(R0 수집 슬롯)이 이 커맨드를 exec 트리거.
"""

import os
import sys

from trading.collectors.market import MarketStore
from trading.collectors.news import (
    FOREIGN_THEMES,
    NewsSource,
    NewsStore,
    build_query_plan,
    collect_news,
)
from trading.domains import Sector
from trading.screener import ScreenConfig, screen

DEFAULT_TOP_N = 15
SEARCH_LIMIT = 10


def build_sources_from_env() -> dict[str, NewsSource]:
    """env 키로 가용 백엔드 구성. 미설정 백엔드는 누락 → collect_news 가 blocked 보고.

    네이버: NAVER_CLIENT_ID/SECRET(국내). SearXNG: SEARXNG_URL(해외).
    """
    out: dict[str, NewsSource] = {}
    cid, csec = os.environ.get("NAVER_CLIENT_ID"), os.environ.get("NAVER_CLIENT_SECRET")
    if cid and csec:
        from trading.collectors.news_naver import NaverNewsSource

        out["naver"] = NaverNewsSource(cid, csec)
    searxng_url = os.environ.get("SEARXNG_URL")
    if searxng_url:
        from trading.collectors.news_searxng import SearxngNewsSource

        out["searxng"] = SearxngNewsSource(searxng_url)
    return out


def run(top_n: int = DEFAULT_TOP_N) -> int:
    """스크리너 후보 → 라우팅 검색 → 교차소스 dedup → landing. 종료코드 반환."""
    mstore = MarketStore()
    res = screen(mstore, ScreenConfig(top_n=top_n))
    mstore.close()
    if not res.candidates:
        print("뉴스 수집 스킵 — 스크리너 후보 없음")
        return 0
    # 3계층(P-4 §3): L1 후보종목 + L2 26섹터(테마-광범위 촉매) + L3 거시(FOREIGN_THEMES).
    sectors = list(Sector)
    plan = build_query_plan([(c.srtn_cd, c.name) for c in res.candidates], sectors=sectors)
    sources = build_sources_from_env()
    store = NewsStore()  # 단일 영속 data/news.sqlite — 시계열 통합 (P-3)
    summary = collect_news(sources, plan, store, limit=SEARCH_LIMIT)
    store.close()
    print(
        f"뉴스 수집 as_of={res.as_of} (쿼리플랜 L1={len(res.candidates)}·L2={len(sectors)}·"
        f"L3={len(FOREIGN_THEMES)}): 적재 {summary.stored}건 (dedup후 {summary.collected})"
    )
    for b in summary.blocked[:8]:
        print(f"  blocked: {b}")
    return 0


def main() -> int:
    top_n = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TOP_N
    return run(top_n)


__all__ = ["build_sources_from_env", "run"]


if __name__ == "__main__":
    raise SystemExit(main())
