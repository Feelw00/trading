"""ThesisStore — R3 ThesisRecord append-only 영속(라운드 간 전달, 설계서 §3/§4).

R3 페르소나 산출을 단일 영속 ``data/theses.sqlite``(시세·뉴스·이벤트 DB 동격)에 적재.
**append-only**: 재분석은 같은 id 새 version. ThesisRecord엔 종목 필드가 없어 ``srtn_cd`` 를
적재 시 명시(컬럼)로 받는다 → R4/R5가 종목·페르소나로 조회. 전체 레코드는 JSON payload 무손실.
"""

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from trading.contracts.thesis import ThesisRecord

DEFAULT_THESES_DB = Path("data") / "theses.sqlite"

THESES_DDL = """
CREATE TABLE IF NOT EXISTS theses (
  row_id INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL, version INTEGER NOT NULL,
  srtn_cd TEXT NOT NULL, persona TEXT NOT NULL,
  as_of TEXT NOT NULL, fetched_at TEXT NOT NULL, source TEXT NOT NULL,
  direction TEXT NOT NULL, confidence REAL NOT NULL, horizon_days INTEGER NOT NULL,
  payload TEXT NOT NULL,
  UNIQUE(id, version)
);
CREATE INDEX IF NOT EXISTS idx_th_srtn ON theses(srtn_cd);
CREATE INDEX IF NOT EXISTS idx_th_persona ON theses(persona);
CREATE INDEX IF NOT EXISTS idx_th_asof ON theses(as_of);
"""

_LATEST = "version = (SELECT MAX(v.version) FROM theses v WHERE v.id = theses.id)"


class ThesisStore:
    """ThesisRecord append-only SQLite. 재분석=새 version, 조회=최신 version."""

    def __init__(self, db_path: Path = DEFAULT_THESES_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(THESES_DDL)

    def append(self, srtn_cd: str, records: Sequence[ThesisRecord]) -> int:
        """종목별 논제 적재 — id별 version 자동 증가. 반환=적재 건수."""
        n = 0
        for rec in records:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM theses WHERE id = ?", (rec.id,)
            ).fetchone()
            version = int(row[0]) + 1
            self._conn.execute(
                "INSERT INTO theses (id, version, srtn_cd, persona, as_of, fetched_at, source, "
                "direction, confidence, horizon_days, payload) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rec.id, version, srtn_cd, rec.persona.value, rec.as_of.isoformat(),
                    rec.fetched_at.isoformat(), rec.source, rec.direction.value,
                    rec.confidence, rec.horizon_days, rec.model_dump_json(),
                ),
            )
            n += 1
        self._conn.commit()
        return n

    def for_srtn(self, srtn_cd: str) -> list[ThesisRecord]:
        """해당 종목의 최신 version 논제(페르소나별) — as_of 최신순."""
        cur = self._conn.execute(
            f"SELECT payload FROM theses WHERE srtn_cd = ? AND {_LATEST} ORDER BY as_of DESC",
            (srtn_cd,),
        )
        return [ThesisRecord.model_validate_json(r[0]) for r in cur]

    def recent(self, *, limit: int = 200) -> list[ThesisRecord]:
        cur = self._conn.execute(
            f"SELECT payload FROM theses WHERE {_LATEST} ORDER BY as_of DESC LIMIT ?", (limit,)
        )
        return [ThesisRecord.model_validate_json(r[0]) for r in cur]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM theses").fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        self._conn.close()


__all__ = ["DEFAULT_THESES_DB", "ThesisStore"]
