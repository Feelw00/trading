"""뉴스 수집 코어(백엔드-무관) — 정규화·교차소스 dedup·landing·라우터.

COLLECT-4: 웹서치 허용은 **뉴스 한정** + 출처 가드. 이 모듈은 **결정론 코드**(LLM 미개입):
어댑터가 반환한 ``RawNews`` 를 ``NewsItem`` 으로 정규화하고, 같은 사건을 1건으로 dedup,
append-only landing 적재. 어댑터 미연결/실패는 ``blocked`` 으로 보고(빈 결과 날조 금지).

소스 분담(라우터): 국내 후보·시장 쿼리→네이버, 해외 매크로·테마→SearXNG.
실제 백엔드 어댑터(NaverNewsSource·SearxngNewsSource)는 별 모듈(여긴 Protocol만).
"""

import hashlib
import html
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from trading.collectors.base import KST, now_kst
from trading.contracts.news import NewsItem

# 발행처 신뢰(부분일치). 미상·기타는 기본값.
_TRUST: tuple[tuple[str, float], ...] = (
    ("연합뉴스", 0.95), ("Yonhap", 0.95), ("Reuters", 0.95), ("Bloomberg", 0.95),
    ("한국경제", 0.9), ("매일경제", 0.9), ("조선", 0.85), ("중앙", 0.85), ("동아", 0.85),
    ("Financial Times", 0.9), ("Wall Street Journal", 0.9), ("CNBC", 0.85),
)
_TRUST_DEFAULT = 0.5

# URL 도메인 → 발행처(어댑터 공용). 미상은 host 그대로. trust는 _TRUST 가 이름으로 매칭.
_DOMAIN_PUBLISHER: dict[str, str] = {
    # 국내
    "yna.co.kr": "연합뉴스", "hankyung.com": "한국경제", "mk.co.kr": "매일경제",
    "chosun.com": "조선일보", "joongang.co.kr": "중앙일보", "donga.com": "동아일보",
    "edaily.co.kr": "이데일리", "mt.co.kr": "머니투데이", "sedaily.com": "서울경제",
    "fnnews.com": "파이낸셜뉴스", "news.naver.com": "네이버뉴스",
    # 해외
    "reuters.com": "Reuters", "bloomberg.com": "Bloomberg", "ft.com": "Financial Times",
    "wsj.com": "Wall Street Journal", "cnbc.com": "CNBC", "apnews.com": "AP",
    "nytimes.com": "New York Times",
}


def host_of(url: str) -> str:
    host = url.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0].lower()
    return host[4:] if host.startswith("www.") else host


def publisher_from_url(url: str) -> str | None:
    """URL → 발행처명(도메인 매칭, 미상은 host). 어댑터 공용."""
    host = host_of(url)
    if not host:
        return None
    for dom, name in _DOMAIN_PUBLISHER.items():
        if host == dom or host.endswith("." + dom):
            return name
    return host
# 해외 매크로·테마 키워드 → SearXNG(네이버 약점 보완). 큐레이션(임의 확장 금지).
FOREIGN_THEMES: tuple[str, ...] = (
    "Federal Reserve rate decision",
    "SOX semiconductor index",
    "Nvidia AI chip",
    "TSMC foundry",
    "US CPI inflation",
    "WTI crude oil price",
)


@dataclass(frozen=True)
class RawNews:
    """어댑터 출력(소스가 자기 포맷을 파싱해 채움). 날짜는 tz-aware 또는 None."""

    title: str
    url: str
    publisher: str | None = None
    published_at: datetime | None = None
    snippet: str | None = None
    lang: str | None = None


class NewsSource(Protocol):
    """뉴스 백엔드 어댑터 표면. 검색 실패는 CollectError(빈 결과 날조 금지)."""

    name: str

    def search(self, query: str, *, limit: int) -> list[RawNews]: ...


@dataclass(frozen=True)
class NewsQuery:
    text: str
    backend: str            # "naver" | "searxng"
    entities: list[str] = field(default_factory=list)


def build_query_plan(
    candidates: Sequence[tuple[str, str]], themes: Sequence[str] = FOREIGN_THEMES
) -> list[NewsQuery]:
    """국내 후보명→네이버, 해외 테마→SearXNG. (srtn_cd, name) + 테마 키워드."""
    plan = [NewsQuery(text=name, backend="naver", entities=[srtn_cd]) for srtn_cd, name in candidates]
    plan += [NewsQuery(text=t, backend="searxng", entities=[f"theme:{_slug(t)}"]) for t in themes]
    return plan


def _slug(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "-", text.lower()).strip("-") or "x"


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def strip_html(text: str) -> str:
    """검색 결과의 <b> 등 태그 제거 + 엔티티 unescape + 공백 정리."""
    return _WS.sub(" ", html.unescape(_TAG.sub("", text))).strip()


_TRACKING = re.compile(r"(?i)^(utm_[^=]*|fbclid|gclid|igshid)=")


def norm_url(url: str) -> str:
    """dedup용 URL 정규화 — scheme·host 소문자, fragment·트래킹 파라미터 제거, 끝 슬래시 정리."""
    u = url.strip().split("#", 1)[0]
    m = re.match(r"^(https?://)([^/]+)(.*)$", u, re.I)
    if m:
        u = m.group(1).lower() + m.group(2).lower() + m.group(3)
    base, sep, query = u.partition("?")
    if sep:
        kept = [p for p in query.split("&") if p and not _TRACKING.match(p)]
        u = base + ("?" + "&".join(kept) if kept else "")
    return u.rstrip("/?&")


def _trust(publisher: str | None, source: str) -> float:
    if publisher:
        for key, val in _TRUST:
            if key.lower() in publisher.lower():
                return val
    return _TRUST_DEFAULT if source == "naver" else _TRUST_DEFAULT - 0.1


def _dedup_id(url: str) -> str:
    return hashlib.sha1(norm_url(url).encode("utf-8")).hexdigest()[:16]


def _norm_title(title: str) -> str:
    return _WS.sub(" ", re.sub(r"[^0-9a-z가-힣]+", " ", title.lower())).strip()


def normalize(
    raw: RawNews, *, source: str, query: str, entities: list[str], fetched_at: datetime | None = None
) -> NewsItem | None:
    """RawNews → NewsItem(결정론). URL/제목 없으면 None(드롭, 날조 금지)."""
    title = strip_html(raw.title)
    url = raw.url.strip()
    if not title or not url.startswith("http"):
        return None
    pub = raw.published_at.astimezone(KST) if raw.published_at else None
    return NewsItem(
        id=_dedup_id(url),
        source=source,
        query=query,
        title=title,
        url=url,
        publisher=raw.publisher,
        published_at=pub,
        fetched_at=fetched_at or now_kst(),
        snippet=strip_html(raw.snippet) if raw.snippet else None,
        lang=raw.lang,
        entities=entities,
        trust=_trust(raw.publisher, source),
        verified=pub is not None,
    )


def dedupe(items: Sequence[NewsItem]) -> list[NewsItem]:
    """교차소스 병합 — 같은 정규화 URL 또는 같은 정규화 제목은 1건으로.

    유지 규칙: trust 높은 쪽 우선, entities·query 합집합, published_at 최솟값(가장 이른 보도).
    """
    by_key: dict[str, NewsItem] = {}
    title_to_key: dict[str, str] = {}
    for it in items:
        key = it.id
        tkey = _norm_title(it.title)
        if tkey and tkey in title_to_key:
            key = title_to_key[tkey]
        if key in by_key:
            by_key[key] = _merge(by_key[key], it)
        else:
            by_key[key] = it
            if tkey:
                title_to_key.setdefault(tkey, key)
    return list(by_key.values())


def _merge(a: NewsItem, b: NewsItem) -> NewsItem:
    ents = list(dict.fromkeys([*a.entities, *b.entities]))
    keep, other = (a, b) if a.trust >= b.trust else (b, a)
    pubs = [p for p in (a.published_at, b.published_at) if p is not None]
    return keep.model_copy(
        update={
            "entities": ents,
            "published_at": min(pubs) if pubs else None,
            "verified": a.verified or b.verified,
            "query": keep.query if keep.query == other.query else f"{keep.query}|{other.query}",
        }
    )


# 뉴스는 시계열 촉매(며칠 지속) — 시세(market.sqlite)처럼 단일 영속 DB. (PROPOSALS P-3)
DEFAULT_NEWS_DB = Path("data") / "news.sqlite"

NEWS_DDL = """
CREATE TABLE IF NOT EXISTS news_items (
  id TEXT PRIMARY KEY, source TEXT, query TEXT, title TEXT, title_norm TEXT, url TEXT,
  publisher TEXT, published_at TEXT, fetched_at TEXT, snippet TEXT, lang TEXT,
  trust REAL, verified INTEGER
);
CREATE TABLE IF NOT EXISTS news_entities (
  news_id TEXT NOT NULL, entity TEXT NOT NULL, entity_type TEXT,
  PRIMARY KEY (news_id, entity)
);
CREATE INDEX IF NOT EXISTS idx_ne_entity ON news_entities(entity);
CREATE INDEX IF NOT EXISTS idx_ni_published ON news_items(published_at);
CREATE INDEX IF NOT EXISTS idx_ni_title_norm ON news_items(title_norm);
"""
_NEWS_INSERT = (
    "INSERT OR IGNORE INTO news_items (id, source, query, title, title_norm, url, publisher, "
    "published_at, fetched_at, snippet, lang, trust, verified) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
_ENTITY_INSERT = "INSERT OR IGNORE INTO news_entities (news_id, entity, entity_type) VALUES (?,?,?)"


def _row_to_news_item(r: tuple[object, ...]) -> NewsItem:
    """recent_for 행 → NewsItem. 마지막 컬럼은 GROUP_CONCAT 된 entities(콤마)."""
    return NewsItem(
        id=str(r[0]), source=str(r[1]), query=str(r[2]), title=str(r[3]), url=str(r[4]),
        publisher=str(r[5]) if r[5] is not None else None,
        published_at=datetime.fromisoformat(str(r[6])) if r[6] is not None else None,
        fetched_at=datetime.fromisoformat(str(r[7])),
        snippet=str(r[8]) if r[8] is not None else None,
        lang=str(r[9]) if r[9] is not None else None,
        trust=float(str(r[10])) if r[10] is not None else 0.0,
        verified=bool(r[11]),
        entities=str(r[12]).split(",") if r[12] else [],
    )


def _entity_type(entity: str) -> str:
    return "theme" if entity.startswith("theme:") else "ticker"


class NewsStore:
    """뉴스 landing SQLite — 단일 영속 ``data/news.sqlite``(시세 DB와 동격, P-3).

    append-only: URL 중복은 PK(id)로 IGNORE, 정규화 제목(title_norm) 중복은 **크로스-런 병합**
    (기사 행은 안 늘리고 entities 만 기존 행에 합침). entity 는 정규화 조인 테이블(news_entities)로
    적재 → 종목/테마별 **인덱스** 조회(과거 ``entities LIKE`` 풀스캔·부분일치 오탐 제거).
    """

    def __init__(self, db_path: Path = DEFAULT_NEWS_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(NEWS_DDL)

    def upsert(self, items: Sequence[NewsItem]) -> int:
        """기사 적재 + entities 머지. 반환=새로 적재된 news_items 수(신규 기사만).

        같은 URL(id) 또는 같은 정규화 제목(title_norm)이 이미 있으면 기사 행은 추가하지 않고
        entities 만 기존 행에 합친다(전역 dedup). "언제 처음 봤나"는 최초 행의 fetched_at 에 남는다.
        """
        stored = 0
        for it in items:
            tnorm = _norm_title(it.title)
            existing = (
                self._conn.execute(
                    "SELECT id FROM news_items WHERE title_norm = ? LIMIT 1", (tnorm,)
                ).fetchone()
                if tnorm
                else None
            )
            if existing is not None:
                target_id = str(existing[0])
            else:
                cur = self._conn.execute(
                    _NEWS_INSERT,
                    (
                        it.id, it.source, it.query, it.title, tnorm, it.url, it.publisher,
                        it.published_at.isoformat() if it.published_at else None,
                        it.fetched_at.isoformat(), it.snippet, it.lang, it.trust, int(it.verified),
                    ),
                )
                target_id = it.id
                if cur.rowcount > 0:
                    stored += 1
            for e in it.entities:
                self._conn.execute(_ENTITY_INSERT, (target_id, e, _entity_type(e)))
        self._conn.commit()
        return stored

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM news_items").fetchone()
        return int(row[0]) if row else 0

    def recent_for(self, entities: Sequence[str], *, limit: int = 10) -> list[NewsItem]:
        """해당 entities(종목 srtn_cd/테마) 최근 뉴스 — 발행일 최신순(날짜 미상은 뒤).

        news_entities(entity 인덱스) 정확매칭. 각 행의 entities 는 조인으로 복원.
        """
        if not entities:
            return []
        placeholders = ",".join("?" for _ in entities)
        cur = self._conn.execute(
            "SELECT n.id, n.source, n.query, n.title, n.url, n.publisher, "
            "n.published_at, n.fetched_at, n.snippet, n.lang, n.trust, n.verified, "
            "(SELECT GROUP_CONCAT(e2.entity) FROM news_entities e2 WHERE e2.news_id = n.id) "
            "FROM news_items n "
            f"WHERE n.id IN (SELECT news_id FROM news_entities WHERE entity IN ({placeholders})) "
            "ORDER BY (n.published_at IS NULL), n.published_at DESC LIMIT ?",
            [*entities, limit],
        )
        return [_row_to_news_item(r) for r in cur]

    def close(self) -> None:
        self._conn.close()


@dataclass(frozen=True)
class NewsCollectSummary:
    collected: int          # dedup 후 적재 시도 건수
    stored: int             # 신규 적재(중복 제외)
    blocked: list[str]      # 미연결 백엔드·검색 실패


def collect_news(
    sources: dict[str, NewsSource],
    plan: Sequence[NewsQuery],
    store: NewsStore,
    *,
    limit: int = 10,
) -> NewsCollectSummary:
    """라우터: 쿼리별 지정 백엔드 호출 → 정규화 → 교차소스 dedup → landing.

    백엔드 어댑터 미연결(키/인스턴스 없음)은 ``blocked`` — 다른 소스로 임의 대체 안 함.
    """
    from trading.collectors.base import CollectError

    collected: list[NewsItem] = []
    blocked: list[str] = []
    missing_reported: set[str] = set()
    for q in plan:
        src = sources.get(q.backend)
        if src is None:
            if q.backend not in missing_reported:
                blocked.append(f"{q.backend} 백엔드 미연결 — blocked")
                missing_reported.add(q.backend)
            continue
        try:
            raws = src.search(q.text, limit=limit)
        except CollectError as e:
            blocked.append(f"{q.backend} 검색 실패({q.text}): {e}")
            continue
        for raw in raws:
            item = normalize(raw, source=src.name, query=q.text, entities=q.entities)
            if item is not None:
                collected.append(item)
    merged = dedupe(collected)
    stored = store.upsert(merged)
    return NewsCollectSummary(collected=len(merged), stored=stored, blocked=blocked)


__all__ = [
    "DEFAULT_NEWS_DB",
    "FOREIGN_THEMES",
    "NewsCollectSummary",
    "NewsQuery",
    "NewsSource",
    "NewsStore",
    "RawNews",
    "build_query_plan",
    "collect_news",
    "dedupe",
    "host_of",
    "normalize",
    "norm_url",
    "publisher_from_url",
    "strip_html",
]
