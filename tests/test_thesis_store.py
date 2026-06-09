"""ThesisStore — append-only ThesisRecord 영속·버전·종목조회 왕복."""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading.contracts.thesis import ThesisRecord
from trading.journal.theses import ThesisStore

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 6, 9, 16, 0, tzinfo=KST)


def _thesis(persona: str = "supply", **over: Any) -> ThesisRecord:
    base: dict[str, Any] = {
        "id": f"thesis.20260609.001740.{persona}", "as_of": NOW, "fetched_at": NOW,
        "source": "r3:test", "persona": persona, "thesis": "수주 모멘텀", "direction": "long",
        "instrument_class": "SK네트웍스", "trigger": "전고 돌파", "invalidation": "종가 5만원 하회",
        "horizon_days": 7, "confidence": 0.5, "evidence": ["evt.x"],
    }
    base.update(over)
    return ThesisRecord(**base)


def test_append_and_for_srtn_roundtrip(tmp_path: Path) -> None:
    ts = ThesisStore(tmp_path / "theses.sqlite")
    assert ts.append("001740", [_thesis("supply"), _thesis("macro")]) == 2
    got = ts.for_srtn("001740")
    assert len(got) == 2
    t = next(x for x in got if x.persona.value == "supply")
    assert t.invalidation == "종가 5만원 하회" and t.horizon_days == 7 and t.confidence == 0.5
    assert ts.for_srtn("999999") == []
    ts.close()


def test_versioning_returns_latest(tmp_path: Path) -> None:
    ts = ThesisStore(tmp_path / "theses.sqlite")
    ts.append("001740", [_thesis("supply", thesis="v1")])
    ts.append("001740", [_thesis("supply", thesis="v2", confidence=0.7)])
    got = ts.for_srtn("001740")
    assert len(got) == 1 and got[0].thesis == "v2" and got[0].confidence == 0.7
    assert ts.count() == 2  # 두 버전 모두 보존
    ts.close()
