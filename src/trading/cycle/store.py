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

    def all_latest(self) -> list[CycleRecord]:
        """산업별 최신 레코드 전부 — 대시보드·산업 페이지 입력."""
        rows = self._conn.execute(
            "SELECT payload FROM cycles c WHERE rowid = "
            "(SELECT rowid FROM cycles WHERE industry = c.industry "
            " ORDER BY as_of DESC, version DESC LIMIT 1)"
        ).fetchall()
        return [CycleRecord.model_validate_json(str(r[0])) for r in rows]

    def recent_phases(self, *, n: int = 2) -> dict[str, list[str]]:
        """산업별 최근 n개 산출 회차의 국면(최신순) — 국면 전환 감지용(직전 산출 대비)."""
        out: dict[str, list[str]] = {}
        seen: dict[str, set[str]] = {}
        for r in self._conn.execute(
            "SELECT industry, phase, as_of FROM cycles ORDER BY industry, as_of DESC, version DESC"
        ):
            ind, phase, as_of = str(r[0]), str(r[1]), str(r[2])
            if as_of in seen.setdefault(ind, set()):
                continue
            if len(out.setdefault(ind, [])) < n:
                out[ind].append(phase)
                seen[ind].add(as_of)
        return out

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
