"""ScoreStore — R7 ScoreRecord append-only 영속 (``data/scores.sqlite``).

R4가 "각 페르소나의 최근 성적표를 입력받아 성적 나쁜 페르소나를 더 가혹하게 공격"(§3 R4)
할 때의 단일 원천. 재평가는 같은 id의 새 version append.
"""

import sqlite3
from pathlib import Path

from trading.contracts.score import ScoreRecord

DEFAULT_SCORES_DB = Path("data") / "scores.sqlite"

SCORES_DDL = """
CREATE TABLE IF NOT EXISTS scores (
  row_id INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL, version INTEGER NOT NULL,
  as_of TEXT NOT NULL, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
  payload TEXT NOT NULL,
  UNIQUE(id, version)
);
CREATE INDEX IF NOT EXISTS idx_sc_asof ON scores(as_of);
"""


class ScoreStore:
    def __init__(self, db_path: Path = DEFAULT_SCORES_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(SCORES_DDL)

    def append(self, record: ScoreRecord) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM scores WHERE id = ?", (record.id,)
        ).fetchone()
        version = int(row[0]) + 1
        self._conn.execute(
            "INSERT INTO scores (id, version, as_of, period_start, period_end, payload) "
            "VALUES (?,?,?,?,?,?)",
            (
                record.id, version, record.as_of.isoformat(),
                record.period_start, record.period_end, record.model_dump_json(),
            ),
        )
        self._conn.commit()
        return version

    def latest(self) -> ScoreRecord | None:
        row = self._conn.execute(
            "SELECT payload FROM scores ORDER BY row_id DESC LIMIT 1"
        ).fetchone()
        return ScoreRecord.model_validate_json(row[0]) if row else None

    def close(self) -> None:
        self._conn.close()


__all__ = ["DEFAULT_SCORES_DB", "ScoreStore"]
