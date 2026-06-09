"""네이버 검색 API(뉴스) 어댑터 — 국내 후보 촉매(COLLECT-4 분담: 국내→네이버).

요청형식(공식 문서, 라이브 검증 대기 — 키 확보 후):
  GET https://openapi.naver.com/v1/search/news.json?query=&display=&sort=date
  헤더: X-Naver-Client-Id / X-Naver-Client-Secret
  응답: {total, items:[{title, originallink, link, description, pubDate(RFC822)}]}
    title·description은 <b> 태그/엔티티 포함 → 코어 normalize 가 strip.
    publisher 필드 없음 → originallink 도메인에서 발행처 추정.
인증 실패·한도초과는 HTTP 오류 → fetch_json 재시도 후 CollectError(빈 결과 날조 금지).
"""

from collections.abc import Callable
from email.utils import parsedate_to_datetime
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from trading.collectors.base import fetch_json
from trading.collectors.news import RawNews

NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"

JsonFetch = Callable[[str], Any]

# originallink 도메인 → 발행처(신뢰 랭킹은 코어 _TRUST 가 이름으로 매칭). 미상은 host 그대로.
_DOMAIN_PUBLISHER: dict[str, str] = {
    "yna.co.kr": "연합뉴스",
    "hankyung.com": "한국경제",
    "mk.co.kr": "매일경제",
    "chosun.com": "조선일보",
    "joongang.co.kr": "중앙일보",
    "donga.com": "동아일보",
    "edaily.co.kr": "이데일리",
    "mt.co.kr": "머니투데이",
    "sedaily.com": "서울경제",
    "fnnews.com": "파이낸셜뉴스",
    "news.naver.com": "네이버뉴스",
}


def _host(url: str) -> str:
    m = url.split("://", 1)[-1].split("/", 1)[0].lower()
    return m[4:] if m.startswith("www.") else m


def _publisher(url: str) -> str | None:
    host = _host(url)
    if not host:
        return None
    for dom, name in _DOMAIN_PUBLISHER.items():
        if host == dom or host.endswith("." + dom):
            return name
    return host


def _parse_pubdate(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(str(raw))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo is not None else None  # tz 없는 값은 추측 안 함


class NaverNewsSource:
    """COLLECT-4 뉴스 백엔드(국내). ``search`` 는 RawNews 리스트(코어가 정규화·dedup)."""

    name = "naver"

    def __init__(self, client_id: str, client_secret: str, *, json_fetch: JsonFetch | None = None) -> None:
        self._headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
        self._json: JsonFetch = json_fetch or self._real

    def _real(self, url: str) -> Any:
        return fetch_json(url, headers=self._headers)

    def search(self, query: str, *, limit: int = 10) -> list[RawNews]:
        q = urlencode({"query": query, "display": max(1, min(limit, 100)), "sort": "date"})
        data = self._json(f"{NAVER_NEWS_URL}?{q}")
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []
        out: list[RawNews] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            url = str(it.get("originallink") or it.get("link") or "")
            out.append(
                RawNews(
                    title=str(it.get("title", "")),
                    url=url,
                    publisher=_publisher(url),
                    published_at=_parse_pubdate(it.get("pubDate")),
                    snippet=str(it["description"]) if it.get("description") else None,
                    lang="ko",
                )
            )
        return out


__all__ = ["NaverNewsSource"]
