"""저널 — append-only 레코드 저장 + 버전 패턴 + 스키마 위반 알림 훅.

설계서 §4(append-only), §9(스키마 위반 시 폐기+알림). M1은 in-memory 백엔드,
PostgreSQL 백엔드는 M2. 레코드 수정은 새 버전 append로만(같은 id, 증가하는 version).
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, TypeVar
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from trading.contracts.base import BaseRecord

logger = logging.getLogger("trading.journal")
KST = ZoneInfo("Asia/Seoul")

RecordT = TypeVar("RecordT", bound=BaseRecord)


class AlertHook(Protocol):
    """스키마 위반 등 이상 상황 알림 훅(설계서 §8). M1은 로그 구현."""

    def __call__(self, message: str, *, severity: str, context: Mapping[str, Any]) -> None: ...


def log_alert_hook(message: str, *, severity: str, context: Mapping[str, Any]) -> None:
    logger.warning("[alert:%s] %s | %s", severity, message, dict(context))


def _now_kst() -> datetime:
    return datetime.now(tz=KST)


@dataclass(frozen=True)
class JournalEntry:
    record: BaseRecord
    version: int
    appended_at: datetime


class InMemoryJournal:
    """append-only 저널. delete/update 없음 — 수정은 append로 새 버전 레코드."""

    def __init__(
        self,
        alert_hook: AlertHook = log_alert_hook,
        clock: Callable[[], datetime] = _now_kst,
    ) -> None:
        self._entries: list[JournalEntry] = []
        self._alert_hook = alert_hook
        self._clock = clock

    def append(self, record: BaseRecord) -> JournalEntry:
        version = sum(1 for e in self._entries if e.record.id == record.id) + 1
        entry = JournalEntry(record=record, version=version, appended_at=self._clock())
        self._entries.append(entry)
        return entry

    def ingest(self, model: type[RecordT], data: Mapping[str, Any]) -> RecordT | None:
        """raw dict 검증 → 성공 시 레코드 반환, 실패 시 알림 훅 호출 후 None."""
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            record_id = data.get("id") if isinstance(data, Mapping) else None
            self._alert_hook(
                f"schema violation for {model.__name__}",
                severity="P1",
                context={"record_id": record_id, "error_count": exc.error_count()},
            )
            return None

    def latest(self, record_id: str) -> BaseRecord | None:
        for entry in reversed(self._entries):
            if entry.record.id == record_id:
                return entry.record
        return None

    def versions(self, record_id: str) -> list[JournalEntry]:
        return [e for e in self._entries if e.record.id == record_id]

    @property
    def entries(self) -> Sequence[JournalEntry]:
        return tuple(self._entries)

    def __len__(self) -> int:
        return len(self._entries)
