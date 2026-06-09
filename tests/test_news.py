"""뉴스 수집 코어(백엔드-무관) — 정규화·교차소스 dedup·landing·라우터 blocked."""

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from trading.collectors.base import CollectError
from trading.collectors.news import (
    NewsQuery,
    NewsStore,
    RawNews,
    build_query_plan,
    collect_news,
    dedupe,
    norm_url,
    normalize,
    strip_html,
)

KST = ZoneInfo("Asia/Seoul")
FETCHED = datetime(2026, 6, 9, 8, 0, tzinfo=KST)


def test_strip_html_and_unescape() -> None:
    assert strip_html("<b>삼성전자</b> 신고가&hellip;  돌파") == "삼성전자 신고가… 돌파"


def test_norm_url_strips_tracking_and_fragment() -> None:
    a = norm_url("HTTPS://News.Example.COM/a?utm_source=x&id=5#frag")
    b = norm_url("https://news.example.com/a?id=5")
    assert a == b == "https://news.example.com/a?id=5"


def test_normalize_kst_and_verified_and_trust() -> None:
    raw = RawNews(
        title="<b>대원제약</b> 실적 발표",
        url="https://yna.co.kr/view/1",
        publisher="연합뉴스",
        published_at=datetime(2026, 6, 9, 0, 0, tzinfo=timezone.utc),  # UTC → KST 09:00
        snippet="요약",
    )
    item = normalize(raw, source="naver", query="대원제약", entities=["003220"], fetched_at=FETCHED)
    assert item is not None
    assert item.title == "대원제약 실적 발표"           # 태그 제거
    assert item.published_at is not None and item.published_at.tzinfo is not None
    assert item.published_at.hour == 9                  # UTC+9 변환
    assert item.verified is True and item.trust == 0.95  # 연합뉴스
    assert item.entities == ["003220"]


def test_normalize_drops_invalid_url_and_marks_unverified() -> None:
    assert normalize(RawNews(title="x", url="not-a-url"), source="searxng", query="q", entities=[]) is None
    # 날짜 미상 → verified False(드롭 후 날조 금지, 항목은 유지)
    item = normalize(
        RawNews(title="해외 뉴스", url="https://ft.com/a", publisher="Financial Times"),
        source="searxng", query="Fed", entities=["theme:fed"], fetched_at=FETCHED,
    )
    assert item is not None and item.verified is False and item.published_at is None


def _item(url: str, title: str, *, source: str, trust_pub: str | None, when: datetime | None, ent: list[str]):
    return normalize(
        RawNews(title=title, url=url, publisher=trust_pub, published_at=when),
        source=source, query="q", entities=ent, fetched_at=FETCHED,
    )


def test_dedupe_cross_source_by_url_and_title() -> None:
    early = datetime(2026, 6, 9, 7, 0, tzinfo=KST)
    late = datetime(2026, 6, 9, 9, 0, tzinfo=KST)
    items = [
        _item("https://yna.co.kr/x?utm_source=a", "삼성전자, 신고가 경신", source="naver",
              trust_pub="연합뉴스", when=late, ent=["005930"]),
        _item("https://yna.co.kr/x", "삼성전자, 신고가 경신", source="searxng",   # 같은 URL(트래킹만 다름)
              trust_pub=None, when=early, ent=["theme:semi"]),
        _item("https://other.com/y", "삼성전자, 신고가 경신!!", source="searxng",  # 다른 URL·같은 제목
              trust_pub=None, when=None, ent=["theme:semi2"]),
    ]
    out = dedupe([i for i in items if i is not None])
    assert len(out) == 1
    merged = out[0]
    assert set(merged.entities) == {"005930", "theme:semi", "theme:semi2"}  # 합집합
    assert merged.trust == 0.95                       # 연합뉴스(높은 trust) 유지
    assert merged.published_at is not None and merged.published_at.hour == 7  # 가장 이른 보도


def test_news_store_idempotent(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.sqlite")
    item = _item("https://yna.co.kr/z", "기사", source="naver", trust_pub="연합뉴스",
                 when=FETCHED, ent=["005930"])
    assert item is not None
    assert store.upsert([item]) == 1
    assert store.upsert([item]) == 0   # 같은 id → IGNORE(실행 간 dedup)
    assert store.count() == 1
    store.close()


class _FakeSource:
    def __init__(self, name: str, raws: list[RawNews]) -> None:
        self.name = name
        self._raws = raws

    def search(self, query: str, *, limit: int) -> list[RawNews]:
        return self._raws[:limit]


class _BoomSource:
    name = "searxng"

    def search(self, query: str, *, limit: int) -> list[RawNews]:
        raise CollectError("backend down")


def test_collect_news_routes_and_blocks(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.sqlite")
    naver = _FakeSource("naver", [RawNews(title="대원제약 실적", url="https://yna.co.kr/1",
                                          publisher="연합뉴스", published_at=FETCHED)])
    plan = [
        NewsQuery(text="대원제약", backend="naver", entities=["003220"]),
        NewsQuery(text="Fed", backend="searxng", entities=["theme:fed"]),  # 백엔드 미연결 → blocked
    ]
    summary = collect_news({"naver": naver}, plan, store)
    assert summary.stored == 1 and summary.collected == 1
    assert any("searxng 백엔드 미연결" in b for b in summary.blocked)
    store.close()


def test_collect_news_search_error_is_blocked(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.sqlite")
    plan = [NewsQuery(text="Fed", backend="searxng", entities=["theme:fed"])]
    summary = collect_news({"searxng": _BoomSource()}, plan, store)
    assert summary.stored == 0
    assert any("검색 실패" in b for b in summary.blocked)
    store.close()


def test_build_query_plan_routes_domestic_vs_foreign() -> None:
    plan = build_query_plan([("003220", "대원제약")], themes=["Federal Reserve rate decision"])
    backends = {q.backend for q in plan}
    assert backends == {"naver", "searxng"}
    naver_q = next(q for q in plan if q.backend == "naver")
    assert naver_q.text == "대원제약" and naver_q.entities == ["003220"]
