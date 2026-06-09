"""EventStore — append-only EventRecord 영속·버전·종목조회 왕복."""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading.contracts.event import AffectedStock, EventRecord
from trading.journal.events import EventStore

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 6, 9, 16, 0, tzinfo=KST)


def _evt(eid: str = "evt.1", **over: Any) -> EventRecord:
    base: dict[str, Any] = {
        "id": eid, "as_of": NOW, "fetched_at": NOW, "source": "r2:test",
        "type": "corp_action", "summary_1line": "요약",
        "catalyst_type": "supply_chain", "scope": "single_stock",
        "catalyst_strength": 0.7, "novelty": 0.6,
        "affected": [AffectedStock(srtn_cd="001740", relevance=0.9)],
        "evidence": ["n1"], "entities": ["001740"],
    }
    base.update(over)
    return EventRecord(**base)


def test_append_and_recent_roundtrip(tmp_path: Path) -> None:
    es = EventStore(tmp_path / "events.sqlite")
    assert es.append([_evt()]) == 1
    got = es.recent()
    assert len(got) == 1
    ev = got[0]
    assert ev.id == "evt.1"
    assert ev.catalyst_type is not None and ev.catalyst_type.value == "supply_chain"
    assert ev.scope is not None and ev.scope.value == "single_stock"
    assert ev.catalyst_strength == 0.7 and ev.novelty == 0.6
    assert ev.affected[0].srtn_cd == "001740" and ev.affected[0].relevance == 0.9
    assert ev.as_of == NOW and ev.evidence == ["n1"]
    es.close()


def test_versioning_returns_latest(tmp_path: Path) -> None:
    es = EventStore(tmp_path / "events.sqlite")
    es.append([_evt(summary_1line="v1")])
    es.append([_evt(summary_1line="v2", catalyst_strength=0.9)])  # 같은 id → 새 version
    got = es.recent()
    assert len(got) == 1 and got[0].summary_1line == "v2" and got[0].catalyst_strength == 0.9
    assert es.count() == 2  # 두 버전 모두 보존(append-only)
    es.close()


def test_for_srtn_filters_by_affected(tmp_path: Path) -> None:
    es = EventStore(tmp_path / "events.sqlite")
    es.append([
        _evt(eid="a", affected=[AffectedStock(srtn_cd="001740", relevance=0.9)]),
        _evt(eid="b", affected=[AffectedStock(srtn_cd="005930", relevance=0.5)]),
    ])
    assert {e.id for e in es.for_srtn("001740")} == {"a"}
    assert {e.id for e in es.for_srtn("005930")} == {"b"}
    assert es.for_srtn("999999") == []
    es.close()


def test_for_srtn_returns_only_latest_version(tmp_path: Path) -> None:
    es = EventStore(tmp_path / "events.sqlite")
    es.append([_evt(eid="a", summary_1line="v1")])
    es.append([_evt(eid="a", summary_1line="v2")])
    hits = es.for_srtn("001740")
    assert len(hits) == 1 and hits[0].summary_1line == "v2"
    es.close()
