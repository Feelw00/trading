"""리플레이 하네스 — fixtures/replay 의 날짜별 FactRecord/EventRecord JSON을
시간(as_of) 순으로 저널에 주입하는 러너. 백테스트·리플레이가 운영과 동일 경로(설계서 §10).

픽스처 포맷:
  <root>/<YYYY-MM-DD>/facts.json   # FactRecord JSON 배열
  <root>/<YYYY-MM-DD>/events.json  # EventRecord JSON 배열
실데이터는 운영자가 채운다. fixtures/replay/sample 에 2일치 가짜 샘플.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trading.contracts.base import BaseRecord
from trading.contracts.event import EventRecord
from trading.contracts.fact import FactRecord
from trading.journal.store import InMemoryJournal


@dataclass(frozen=True)
class ReplayResult:
    facts_ingested: int
    events_ingested: int
    skipped: int
    order: list[str]  # 저널에 주입된 레코드 id (as_of 시간순)


def _load_array(path: Path) -> list[Any]:
    if not path.exists():
        return []
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path} must contain a JSON array")
    return raw


class ReplayRunner:
    """날짜 디렉터리를 순회하며 레코드를 검증→as_of 시간순 정렬→저널 append."""

    def __init__(self, journal: InMemoryJournal) -> None:
        self._journal = journal

    def run(self, root: Path) -> ReplayResult:
        validated: list[BaseRecord] = []
        skipped = 0
        facts = 0
        events = 0
        for day in sorted(p for p in root.iterdir() if p.is_dir()):
            for data in _load_array(day / "facts.json"):
                fact = self._journal.ingest(FactRecord, data)
                if fact is None:
                    skipped += 1
                else:
                    validated.append(fact)
                    facts += 1
            for data in _load_array(day / "events.json"):
                event = self._journal.ingest(EventRecord, data)
                if event is None:
                    skipped += 1
                else:
                    validated.append(event)
                    events += 1
        validated.sort(key=lambda r: r.as_of)
        order: list[str] = []
        for record in validated:
            self._journal.append(record)
            order.append(record.id)
        return ReplayResult(
            facts_ingested=facts, events_ingested=events, skipped=skipped, order=order
        )
