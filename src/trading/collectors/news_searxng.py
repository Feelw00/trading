"""SearXNG(셀프호스트 메타검색) 어댑터 — 해외 매크로·테마(COLLECT-4 분담: 해외→SearXNG).

요청형식(SearXNG JSON API, 라이브 검증 대기 — 인스턴스 확보 후):
  GET {SEARXNG_URL}/search?q=&format=json&categories=news&language=&time_range=
  응답: {results:[{url, title, content, engine, publishedDate(ISO8601, 없을 수 있음)}], ...}
    publisher 없음 → url 도메인 추정(코어 publisher_from_url). publishedDate 없거나 naive면 None.
주의: 인스턴스에 ``format=json`` 활성 필요. 미활성/다운 시 비-JSON·HTTP오류 → fetch_json 재시도 후
CollectError(빈 결과 날조 금지). 키 불필요(인스턴스 URL만).
"""

from collections.abc import Callable
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from trading.collectors.base import fetch_json
from trading.collectors.news import RawNews, publisher_from_url

JsonFetch = Callable[[str], Any]


def _parse_iso(raw: object) -> datetime | None:
    """ISO8601 → tz-aware datetime. naive·미상·이상값 → None(추측 안 함)."""
    if not raw:
        return None
    s = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


class SearxngNewsSource:
    """COLLECT-4 뉴스 백엔드(해외). ``search`` 는 RawNews 리스트(코어가 정규화·dedup)."""

    name = "searxng"

    def __init__(
        self,
        base_url: str,
        *,
        json_fetch: JsonFetch | None = None,
        categories: str = "news",
        language: str = "en",
        time_range: str = "week",
    ) -> None:
        self._base = base_url.rstrip("/")
        self._json: JsonFetch = json_fetch or self._real
        self._categories = categories
        self._language = language
        self._time_range = time_range

    def _real(self, url: str) -> Any:
        return fetch_json(url)

    def search(self, query: str, *, limit: int = 10) -> list[RawNews]:
        q = urlencode(
            {
                "q": query,
                "format": "json",
                "categories": self._categories,
                "language": self._language,
                "time_range": self._time_range,
            }
        )
        data = self._json(f"{self._base}/search?{q}")
        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            return []
        out: list[RawNews] = []
        for r in results[:limit]:
            if not isinstance(r, dict):
                continue
            url = str(r.get("url") or "")
            out.append(
                RawNews(
                    title=str(r.get("title", "")),
                    url=url,
                    publisher=publisher_from_url(url),
                    published_at=_parse_iso(r.get("publishedDate")),
                    snippet=str(r["content"]) if r.get("content") else None,
                    lang=self._language,
                )
            )
        return out


__all__ = ["SearxngNewsSource"]
