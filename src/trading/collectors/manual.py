"""운영자 수동 입력 채널 — data/manual.sqlite (PIVOT-8, 설계서 v0.3 §4 규약).

API가 없거나 어려운 **사실 데이터**(실물 지표 등)를 운영자가 입력해 축적한다.
가드(§4 — 운영자 입력도 데이터다):
- ``source="manual:<출처명>"`` 형식 강제 + ``as_of`` timezone-aware 필수, 미래 시점 거부.
- 전회 대비 급변(기본 ±50%)은 ``confirm=True`` 없이는 거부 — 오타 방어.
- append-only: 수정은 새 버전 레코드. 자동 어댑터가 확정되면 그 소스가 manual보다 우선.
- 입력 가능한 것은 지표값(사실)뿐 — 점수·국면·판단의 입력 경로는 만들지 않는다.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from trading.collectors.base import now_kst

DEFAULT_DB = Path("data") / "manual.sqlite"
SOURCE_PREFIX = "manual:"
SURGE_PCT_DEFAULT = 0.5  # 전회 대비 ±50% 초과 변동 → confirm 요구

_DDL = """
CREATE TABLE IF NOT EXISTS manual_facts (
  metric TEXT NOT NULL, version INTEGER NOT NULL,
  value REAL NOT NULL, unit TEXT, as_of TEXT NOT NULL,
  source TEXT NOT NULL, note TEXT, entered_at TEXT NOT NULL,
  UNIQUE(metric, version)
);
"""


class ManualInputError(ValueError):
    """가드 위반 — 입력 거부(저장 안 됨)."""


class SurgeConfirmRequired(ManualInputError):
    """전회 대비 급변 — 오타가 아니라면 confirm=True로 재시도."""


@dataclass(frozen=True)
class ManualEntry:
    metric: str
    version: int
    value: float
    unit: str | None
    as_of: str
    source: str
    note: str | None
    entered_at: str


class ManualStore:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)

    def latest(self, metric: str) -> ManualEntry | None:
        row = self._conn.execute(
            "SELECT metric, version, value, unit, as_of, source, note, entered_at "
            "FROM manual_facts WHERE metric=? ORDER BY version DESC LIMIT 1",
            (metric,),
        ).fetchone()
        return _entry(row) if row else None

    def history(self, metric: str) -> list[ManualEntry]:
        rows = self._conn.execute(
            "SELECT metric, version, value, unit, as_of, source, note, entered_at "
            "FROM manual_facts WHERE metric=? ORDER BY version ASC",
            (metric,),
        ).fetchall()
        return [_entry(r) for r in rows]

    def metrics(self) -> list[str]:
        return [
            str(r[0])
            for r in self._conn.execute("SELECT DISTINCT metric FROM manual_facts ORDER BY metric")
        ]

    def _insert(self, entry: ManualEntry) -> None:
        self._conn.execute(
            "INSERT INTO manual_facts (metric, version, value, unit, as_of, source, note, entered_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                entry.metric,
                entry.version,
                entry.value,
                entry.unit,
                entry.as_of,
                entry.source,
                entry.note,
                entry.entered_at,
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _entry(row: "tuple[Any, ...]") -> ManualEntry:
    return ManualEntry(
        metric=str(row[0]),
        version=int(row[1]),
        value=float(row[2]),
        unit=str(row[3]) if row[3] is not None else None,
        as_of=str(row[4]),
        source=str(row[5]),
        note=str(row[6]) if row[6] is not None else None,
        entered_at=str(row[7]),
    )


def add_entry(
    store: ManualStore,
    *,
    metric: str,
    value: float,
    source: str,
    as_of: datetime,
    unit: str | None = None,
    note: str | None = None,
    confirm: bool = False,
    surge_pct: float = SURGE_PCT_DEFAULT,
    now: datetime | None = None,
) -> ManualEntry:
    """가드 통과 시 새 버전으로 append. 위반은 ManualInputError(저장 안 됨)."""
    metric = metric.strip()
    if not metric:
        raise ManualInputError("metric이 비었다")
    if not source.startswith(SOURCE_PREFIX) or len(source) <= len(SOURCE_PREFIX):
        raise ManualInputError(f'source는 "{SOURCE_PREFIX}<출처명>" 형식 필수 — 어디서 본 수치인지 박제')
    if as_of.tzinfo is None:
        raise ManualInputError("as_of는 timezone-aware 필수(KST 명시) — naive 거부")
    current = now or now_kst()
    if as_of > current:
        raise ManualInputError(f"as_of가 미래({as_of.isoformat()}) — 신선도 판정 회피 차단")

    prev = store.latest(metric)
    if prev is not None and prev.value != 0 and not confirm:
        change = abs(value / prev.value - 1)
        if change > surge_pct:
            raise SurgeConfirmRequired(
                f"{metric}: 전회 {prev.value} → {value} ({change:+.0%}) — "
                f"오타가 아니면 confirm으로 재시도"
            )

    entry = ManualEntry(
        metric=metric,
        version=(prev.version + 1) if prev else 1,
        value=value,
        unit=unit,
        as_of=as_of.isoformat(),
        source=source,
        note=note,
        entered_at=current.isoformat(),
    )
    store._insert(entry)
    return entry


__all__ = [
    "DEFAULT_DB",
    "ManualEntry",
    "ManualInputError",
    "ManualStore",
    "SOURCE_PREFIX",
    "SURGE_PCT_DEFAULT",
    "SurgeConfirmRequired",
    "add_entry",
]
