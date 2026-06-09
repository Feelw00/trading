"""뉴스 수집 코어(백엔드-무관) — 정규화·교차소스 dedup·landing·라우터 blocked."""

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from trading.collectors.base import CollectError
from trading.collectors.news import (
    NewsQuery,
    NewsStore,
    RawNews,
    _sector_query,
    build_query_plan,
    collect_news,
    dedupe,
    norm_url,
    normalize,
    publisher_from_url,
    strip_html,
)
from trading.domains import Sector

KST = ZoneInfo("Asia/Seoul")
FETCHED = datetime(2026, 6, 9, 8, 0, tzinfo=KST)


def test_strip_html_and_unescape() -> None:
    assert strip_html("<b>삼성전자</b> 신고가&hellip;  돌파") == "삼성전자 신고가… 돌파"


def test_publisher_from_url_domain_and_fallback() -> None:
    assert publisher_from_url("https://www.yna.co.kr/view/1") == "연합뉴스"   # 국내
    assert publisher_from_url("https://reuters.com/markets/x") == "Reuters"  # 해외
    assert publisher_from_url("https://blog.unknown.io/x") == "blog.unknown.io"  # 미상은 host


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


def test_news_store_recent_for_by_entity(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.sqlite")
    early = datetime(2026, 6, 9, 7, 0, tzinfo=KST)
    late = datetime(2026, 6, 9, 9, 0, tzinfo=KST)
    a = _item("https://yna.co.kr/a", "삼성 뉴스 늦은", source="naver", trust_pub="연합뉴스", when=late, ent=["005930"])
    b = _item("https://yna.co.kr/b", "삼성 뉴스 이른", source="naver", trust_pub="연합뉴스", when=early, ent=["005930"])
    c = _item("https://yna.co.kr/c", "다른 종목", source="naver", trust_pub="연합뉴스", when=late, ent=["000660"])
    store.upsert([x for x in (a, b, c) if x is not None])
    got = store.recent_for(["005930"])
    assert [n.title for n in got] == ["삼성 뉴스 늦은", "삼성 뉴스 이른"]  # 발행 최신순, 005930만
    assert store.recent_for([]) == []
    assert store.recent_for(["999999"]) == []  # 매칭 없음
    store.close()


def test_news_store_idempotent(tmp_path: Path) -> None:
    store = NewsStore(tmp_path / "news.sqlite")
    item = _item("https://yna.co.kr/z", "기사", source="naver", trust_pub="연합뉴스",
                 when=FETCHED, ent=["005930"])
    assert item is not None
    assert store.upsert([item]) == 1
    assert store.upsert([item]) == 0   # 같은 id → IGNORE(실행 간 dedup)
    assert store.count() == 1
    store.close()


def test_news_store_cross_run_title_dedup(tmp_path: Path) -> None:
    """다른 수집 실행에서 같은 제목·다른 URL → 기사 1건, entities 머지(전역 dedup, P-3)."""
    store = NewsStore(tmp_path / "news.sqlite")
    a = _item("https://yna.co.kr/a", "삼성전자 신고가 경신", source="naver",
              trust_pub="연합뉴스", when=FETCHED, ent=["005930"])
    b = _item("https://other.com/b", "삼성전자 신고가 경신", source="searxng",  # 다른 URL·같은 제목
              trust_pub=None, when=FETCHED, ent=["theme:semi"])
    assert a is not None and b is not None
    assert store.upsert([a]) == 1
    assert store.upsert([b]) == 0          # 제목 같음 → 기사 행 안 늘어남
    assert store.count() == 1
    got = store.recent_for(["005930"])
    assert len(got) == 1
    assert set(got[0].entities) == {"005930", "theme:semi"}             # entities 머지
    assert [n.title for n in store.recent_for(["theme:semi"])] == ["삼성전자 신고가 경신"]  # 테마로도 조회
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


def test_sector_query_cleans_label() -> None:
    # 괄호 보충 제거 + 복합 라벨 1차 키워드(결정론, 새 데이터 없음)
    assert _sector_query("반도체") == "반도체"
    assert _sector_query("AI·SW/플랫폼") == "AI"
    assert _sector_query("금융(은행·증권·보험)") == "금융"
    assert _sector_query("2차전지(셀)") == "2차전지"
    assert _sector_query("유통·소비재") == "유통"


def test_build_query_plan_three_layers() -> None:
    plan = build_query_plan(
        [("003220", "대원제약")],
        sectors=[Sector.SEMICONDUCTOR, Sector.ROBOTICS],
        themes=["US CPI inflation"],
    )
    # L1 종목(naver, srtn_cd) · L2 섹터(naver, sector:) · L3 거시(searxng, theme:)
    l1 = [q for q in plan if q.entities and q.entities[0] == "003220"]
    l2 = [q for q in plan if q.entities and q.entities[0].startswith("sector:")]
    l3 = [q for q in plan if q.entities and q.entities[0].startswith("theme:")]
    assert len(l1) == 1 and len(l2) == 2 and len(l3) == 1
    assert all(q.backend == "naver" for q in l1 + l2)
    assert all(q.backend == "searxng" for q in l3)
    sem = next(q for q in l2 if q.entities[0] == "sector:semiconductor")
    assert sem.text == "반도체"


def test_build_query_plan_no_sectors_backward_compat() -> None:
    # sectors 미지정 → 기존 L1+L3만(하위호환)
    plan = build_query_plan([("003220", "대원제약")], themes=["US CPI inflation"])
    assert not [q for q in plan if q.entities and q.entities[0].startswith("sector:")]
