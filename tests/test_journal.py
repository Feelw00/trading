"""저널: append-only 버전 패턴 + 스키마 위반 알림 훅."""

from typing import Any

from trading.contracts.fact import FactRecord
from trading.journal.store import InMemoryJournal


def test_append_only_versioning(fact_kwargs: dict[str, Any]) -> None:
    journal = InMemoryJournal()
    rec = FactRecord(**fact_kwargs)
    e1 = journal.append(rec)
    e2 = journal.append(rec)  # 같은 id 재기록 → 새 버전
    assert e1.version == 1
    assert e2.version == 2
    assert len(journal) == 2
    assert len(journal.versions(rec.id)) == 2
    assert journal.latest(rec.id) is rec


def test_appended_at_is_tz_aware(fact_kwargs: dict[str, Any]) -> None:
    journal = InMemoryJournal()
    entry = journal.append(FactRecord(**fact_kwargs))
    assert entry.appended_at.tzinfo is not None


def test_ingest_invalid_calls_hook_and_returns_none() -> None:
    captured: list[tuple[str, str]] = []

    def hook(message: str, *, severity: str, context: Any) -> None:
        captured.append((message, severity))

    journal = InMemoryJournal(alert_hook=hook)
    result = journal.ingest(FactRecord, {"id": "x", "metric": "m", "value": 1.0})  # source 누락
    assert result is None
    assert len(captured) == 1
    assert captured[0][1] == "P1"


def test_ingest_valid_returns_record(fact_kwargs: dict[str, Any]) -> None:
    journal = InMemoryJournal()
    rec = journal.ingest(FactRecord, fact_kwargs)
    assert rec is not None
    assert rec.metric == "kospi_foreign_net_buy_krw"
