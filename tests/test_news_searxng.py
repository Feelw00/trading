"""SearXNG 어댑터 — JSON results 파싱·ISO 날짜·발행처 추정·코어 정규화 연동.

네트워크 없이 주입 fetch로 검증. 라이브 검증은 인스턴스 확보 후 별도.
"""

from typing import Any

from trading.collectors.news import normalize
from trading.collectors.news_searxng import SearxngNewsSource, _parse_iso

_FIXTURE: dict[str, Any] = {
    "query": "Federal Reserve rate decision",
    "results": [
        {
            "url": "https://www.reuters.com/markets/fed-holds-rates",
            "title": "Fed holds rates steady",
            "content": "The Federal Reserve kept rates unchanged.",
            "engine": "google news",
            "publishedDate": "2026-06-08T13:00:00+00:00",
        },
        {
            "url": "https://unknown.example/blog/x",
            "title": "Opinion piece",
            "content": "blah",
            "publishedDate": None,  # 날짜 미상
        },
    ],
}


def test_parse_iso_aware_naive_and_garbage() -> None:
    dt = _parse_iso("2026-06-08T13:00:00+00:00")
    assert dt is not None and dt.tzinfo is not None and dt.hour == 13
    assert _parse_iso("2026-06-08T13:00:00Z") is not None      # Z 처리
    assert _parse_iso("2026-06-08T13:00:00") is None           # naive → 추측 안 함
    assert _parse_iso("nope") is None and _parse_iso(None) is None


def test_search_parses_and_builds_json_query() -> None:
    captured: dict[str, str] = {}

    def jf(url: str) -> Any:
        captured["url"] = url
        return _FIXTURE

    src = SearxngNewsSource("http://localhost:8888/", json_fetch=jf)  # 끝 슬래시 정리 확인
    raws = src.search("Federal Reserve rate decision", limit=10)
    assert "/search?" in captured["url"] and "format=json" in captured["url"]
    assert "categories=news" in captured["url"] and "localhost:8888//search" not in captured["url"]
    assert len(raws) == 2
    assert raws[0].publisher == "Reuters" and raws[0].published_at is not None
    assert raws[1].published_at is None


def test_search_malformed_returns_empty() -> None:
    assert SearxngNewsSource("http://x", json_fetch=lambda url: {}).search("q") == []
    assert SearxngNewsSource("http://x", json_fetch=lambda url: {"results": 1}).search("q") == []


def test_searxng_raw_normalizes_to_grounded_item() -> None:
    src = SearxngNewsSource("http://x", json_fetch=lambda url: _FIXTURE)
    raws = src.search("Fed")
    item = normalize(raws[0], source=src.name, query="Fed", entities=["theme:fed"])
    assert item is not None
    assert item.verified is True and item.trust == 0.95   # Reuters
    assert item.published_at is not None and item.published_at.tzinfo is not None
