"""CandidateRecord 저장소 — data/candidates.sqlite, append-only(탈락 포함 전수 박제)."""

import sqlite3
from pathlib import Path

from trading.collectors.base import now_kst
from trading.contracts.longterm import CandidateRecord

DEFAULT_DB = Path("data") / "candidates.sqlite"

_DDL = """
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT NOT NULL, version INTEGER NOT NULL, symbol TEXT NOT NULL,
  industry TEXT NOT NULL, passed INTEGER NOT NULL,
  as_of TEXT NOT NULL, payload TEXT NOT NULL, appended_at TEXT NOT NULL,
  UNIQUE(id, version)
);
"""


class CandidateStore:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)

    def append(self, record: CandidateRecord) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM candidates WHERE id=?", (record.id,)
        ).fetchone()
        version = int(row[0]) + 1
        self._conn.execute(
            "INSERT INTO candidates (id, version, symbol, industry, passed, as_of, payload, appended_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                record.id,
                version,
                record.symbol,
                record.industry,
                1 if record.passed else 0,
                record.as_of.isoformat(),
                record.model_dump_json(),
                now_kst().isoformat(),
            ),
        )
        self._conn.commit()
        return version

    def latest_passed(self) -> list[CandidateRecord]:
        rows = self._conn.execute(
            "SELECT payload FROM candidates c WHERE passed=1 AND rowid = "
            "(SELECT rowid FROM candidates WHERE symbol = c.symbol "
            " ORDER BY as_of DESC, version DESC LIMIT 1)"
        ).fetchall()
        return [CandidateRecord.model_validate_json(str(r[0])) for r in rows]

    def close(self) -> None:
        self._conn.close()


__all__ = ["DEFAULT_DB", "CandidateStore"]
