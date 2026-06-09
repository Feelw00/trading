"""네이버 검색 API 어댑터 — 응답 파싱·발행처 추정·pubDate(RFC822→KST)·코어 정규화 연동.

네트워크 없이 주입 fetch로 검증. 라이브 검증은 키 확보 후 별도.
"""

from typing import Any

from trading.collectors.news import normalize
from trading.collectors.news_naver import NaverNewsSource, _parse_pubdate

# 네이버 news.json 응답 픽스처(공식 포맷)
_FIXTURE: dict[str, Any] = {
    "total": 2,
    "items": [
        {
            "title": "<b>대원제약</b>, 1분기 실적 발표&hellip;",
            "originallink": "https://www.yna.co.kr/view/AKR123",
            "link": "https://n.news.naver.com/mnews/article/001/0001",
            "description": "<b>대원제약</b>이 1분기 실적을 발표했다.",
            "pubDate": "Mon, 08 Jun 2026 09:30:00 +0900",
        },
        {
            "title": "무명 블로그 글",
            "originallink": "https://blog.unknown.io/post/1",
            "link": "https://blog.unknown.io/post/1",
            "description": "내용",
            "pubDate": "",
        },
    ],
}


def test_parse_pubdate_rfc822_and_empty() -> None:
    dt = _parse_pubdate("Mon, 08 Jun 2026 09:30:00 +0900")
    assert dt is not None and dt.tzinfo is not None and dt.hour == 9
    assert _parse_pubdate("") is None
    assert _parse_pubdate("garbage") is None


def test_search_parses_items_and_builds_url() -> None:
    captured: dict[str, str] = {}

    def jf(url: str) -> Any:
        captured["url"] = url
        return _FIXTURE

    src = NaverNewsSource("id", "secret", json_fetch=jf)
    raws = src.search("대원제약", limit=5)
    assert "news.json?" in captured["url"] and "sort=date" in captured["url"] and "display=5" in captured["url"]
    assert len(raws) == 2
    assert raws[0].url == "https://www.yna.co.kr/view/AKR123"  # originallink 우선
    assert raws[0].publisher == "연합뉴스" and raws[0].lang == "ko"
    assert raws[0].published_at is not None


def test_search_empty_or_malformed_returns_empty() -> None:
    assert NaverNewsSource("i", "s", json_fetch=lambda url: {}).search("x") == []
    assert NaverNewsSource("i", "s", json_fetch=lambda url: {"items": "nope"}).search("x") == []


def test_naver_raw_normalizes_to_grounded_item() -> None:
    src = NaverNewsSource("id", "secret", json_fetch=lambda url: _FIXTURE)
    raws = src.search("대원제약")
    item = normalize(raws[0], source=src.name, query="대원제약", entities=["003220"])
    assert item is not None
    assert item.title == "대원제약, 1분기 실적 발표…"   # 태그/엔티티 정리
    assert item.verified is True and item.trust == 0.95  # 연합뉴스
    assert item.published_at is not None and item.published_at.tzinfo is not None
    # 날짜 없는 2번째 항목 → verified False (드롭 안 하고 표시)
    item2 = normalize(raws[1], source=src.name, query="대원제약", entities=["003220"])
    assert item2 is not None and item2.verified is False
