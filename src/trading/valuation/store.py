"""ValuationRecord 저장소 — data/valuation.sqlite, append-only(버전 증가, UPDATE/DELETE 없음)."""

import sqlite3
from pathlib import Path

from trading.collectors.base import now_kst
from trading.contracts.longterm import ValuationRecord

DEFAULT_DB = Path("data") / "valuation.sqlite"

_DDL = """
CREATE TABLE IF NOT EXISTS valuations (
  id TEXT NOT NULL, version INTEGER NOT NULL, symbol TEXT NOT NULL,
  as_of TEXT NOT NULL, payload TEXT NOT NULL, appended_at TEXT NOT NULL,
  UNIQUE(id, version)
);
"""


class ValuationStore:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_DDL)

    def append(self, record: ValuationRecord) -> int:
        """append-only — 같은 id는 버전 증가로만 갱신. 부여된 version 반환."""
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM valuations WHERE id=?", (record.id,)
        ).fetchone()
        version = int(row[0]) + 1
        self._conn.execute(
            "INSERT INTO valuations (id, version, symbol, as_of, payload, appended_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                record.id,
                version,
                record.symbol,
                record.as_of.isoformat(),
                record.model_dump_json(),
                now_kst().isoformat(),
            ),
        )
        self._conn.commit()
        return version

    def latest(self, record_id: str) -> ValuationRecord | None:
        row = self._conn.execute(
            "SELECT payload FROM valuations WHERE id=? ORDER BY version DESC LIMIT 1",
            (record_id,),
        ).fetchone()
        if not row:
            return None
        return ValuationRecord.model_validate_json(str(row[0]))

    def latest_for_symbol(self, symbol: str) -> ValuationRecord | None:
        row = self._conn.execute(
            "SELECT payload FROM valuations WHERE symbol=? ORDER BY as_of DESC, version DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        if not row:
            return None
        return ValuationRecord.model_validate_json(str(row[0]))

    def all_latest(self) -> list[ValuationRecord]:
        """종목별 최신 레코드 전부 — R4 스크리너 입력."""
        rows = self._conn.execute(
            "SELECT payload FROM valuations v WHERE rowid = "
            "(SELECT rowid FROM valuations WHERE symbol = v.symbol "
            " ORDER BY as_of DESC, version DESC LIMIT 1)"
        ).fetchall()
        return [ValuationRecord.model_validate_json(str(r[0])) for r in rows]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(DISTINCT symbol) FROM valuations").fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        self._conn.close()


__all__ = ["DEFAULT_DB", "ValuationStore"]
