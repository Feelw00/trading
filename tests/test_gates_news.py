"""R1 뉴스 신선도·정합성 게이트 — 순수 코드 단위 테스트."""

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from trading.contracts.news import NewsItem
from trading.gates.news import (
    GateConfig,
    NewsFlag,
    gate_item,
    gate_news,
    summarize,
)

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 6, 9, 16, 0, tzinfo=KST)


def _item(**over: Any) -> NewsItem:
    base: dict[str, Any] = {
        "id": "h1",
        "source": "naver",
        "query": "대원제약",
        "title": "대원제약 신약 임상 진입",
        "url": "https://yna.co.kr/1",
        "publisher": "연합뉴스",
        "published_at": NOW - timedelta(hours=2),
        "fetched_at": NOW,
        "trust": 0.95,
    }
    base.update(over)
    return NewsItem(**base)


def test_fresh_item_no_flags() -> None:
    v = gate_item(_item(), NOW, GateConfig())
    assert v.flags == frozenset()
    assert v.usable and v.fresh


def test_stale_item_flagged() -> None:
    old = _item(published_at=NOW - timedelta(days=5))
    v = gate_item(old, NOW, GateConfig(max_age_days=3.0))
    assert NewsFlag.STALE in v.flags
    assert not v.fresh and not v.usable


def test_undated_item_flagged() -> None:
    v = gate_item(_item(published_at=None), NOW, GateConfig())
    assert NewsFlag.UNDATED in v.flags
    assert not v.fresh  # 신선도 판정 불가 → 하드게이트


def test_future_dated_flagged() -> None:
    future = _item(published_at=NOW + timedelta(hours=3))
    v = gate_item(future, NOW, GateConfig(future_skew_minutes=60.0))
    assert NewsFlag.FUTURE_DATED in v.flags
    assert not v.fresh


def test_future_within_skew_not_flagged() -> None:
    # 시계 skew 허용 오차 내 미래는 통과
    v = gate_item(_item(published_at=NOW + timedelta(minutes=30)), NOW, GateConfig(future_skew_minutes=60.0))
    assert NewsFlag.FUTURE_DATED not in v.flags
    assert v.fresh


def test_low_trust_flagged_but_fresh() -> None:
    # 저신뢰는 LOW_TRUST지만 신선도(fresh)는 유지 → R5 하드게이트 대상 아님(R2 가중 낮춤)
    v = gate_item(_item(trust=0.3), NOW, GateConfig(min_trust=0.5))
    assert v.flags == frozenset({NewsFlag.LOW_TRUST})
    assert v.fresh and not v.usable


def test_stale_boundary_inclusive() -> None:
    cfg = GateConfig(max_age_days=3.0)
    # 정확히 3일 전 = 경계(미만 아님) → stale 아님
    assert NewsFlag.STALE not in gate_item(_item(published_at=NOW - timedelta(days=3)), NOW, cfg).flags
    # 3일+1초 전 → stale
    assert NewsFlag.STALE in gate_item(
        _item(published_at=NOW - timedelta(days=3, seconds=1)), NOW, cfg
    ).flags


def test_summarize_counts() -> None:
    verdicts = gate_news(
        [
            _item(id="a"),                                   # fresh
            _item(id="b", published_at=NOW - timedelta(days=9)),  # stale
            _item(id="c", published_at=None),                # undated
            _item(id="d", trust=0.1),                        # low_trust (fresh)
        ],
        now=NOW,
        config=GateConfig(max_age_days=3.0, min_trust=0.5),
    )
    s = summarize(verdicts)
    assert s["total"] == 4
    assert s["stale"] == 1 and s["undated"] == 1 and s["low_trust"] == 1
    assert s["usable"] == 1            # a만 무결
    assert s["fresh"] == 2            # a, d (low_trust는 fresh)
