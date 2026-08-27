"""CycleRecord 저장소 — data/cycle.sqlite, append-only(버전 증가, UPDATE/DELETE 없음)."""

import sqlite3
from pathlib import Path

from trading.collectors.base import now_kst
from trading.contracts.longterm import CycleRecord

DEFAULT_DB = Path("data") / "cycle.sqlite"

_DDL = """
CREATE TABLE IF NOT EXISTS cycles (
  id TEXT NOT NULL, version INTEGER NOT NULL, industry TEXT NOT NULL,
  phase TEXT NOT NULL, as_of TEXT NOT NULL, payload TEXT NOT NULL, appended_at TEXT NOT NULL,
  UNIQUE(id, version)
);
"""


class CycleStore:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)

    def append(self, record: CycleRecord) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM cycles WHERE id=?", (record.id,)
        ).fetchone()
        version = int(row[0]) + 1
        self._conn.execute(
            "INSERT INTO cycles (id, version, industry, phase, as_of, payload, appended_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                record.id,
                version,
                record.industry,
                record.phase.value,
                record.as_of.isoformat(),
                record.model_dump_json(),
                now_kst().isoformat(),
            ),
        )
        self._conn.commit()
        return version

    def latest_for_industry(self, industry: str) -> CycleRecord | None:
        row = self._conn.execute(
            "SELECT payload FROM cycles WHERE industry=? ORDER BY as_of DESC, version DESC LIMIT 1",
            (industry,),
        ).fetchone()
        return CycleRecord.model_validate_json(str(row[0])) if row else None

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(DISTINCT industry) FROM cycles").fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        self._conn.close()


__all__ = ["DEFAULT_DB", "CycleStore"]
