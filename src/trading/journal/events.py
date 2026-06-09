"""EventStore — R2 EventRecord append-only 영속(라운드 간 전달, 설계서 §3/§4).

R2가 산출한 촉매 ``EventRecord`` 를 단일 영속 ``data/events.sqlite``(시세·뉴스 DB 동격)에 적재.
**append-only**: 재스코어링은 같은 id의 **새 version**으로 append(UPDATE/DELETE 금지 — CLAUDE.md).
핵심 컬럼은 조회용, 전체 레코드는 JSON ``payload`` 로 무손실 보존(pydantic 왕복). ``affected`` 는
종목별 조회용 정규화 조인(R3·factpack이 후보 srtn_cd로 촉매를 끌어옴).
"""

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from trading.contracts.event import EventRecord

DEFAULT_EVENTS_DB = Path("data") / "events.sqlite"

EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS events (
  row_id INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL, version INTEGER NOT NULL,
  as_of TEXT NOT NULL, fetched_at TEXT NOT NULL, source TEXT NOT NULL,
  type TEXT NOT NULL, catalyst_type TEXT, scope TEXT,
  catalyst_strength REAL, novelty REAL,
  summary_1line TEXT NOT NULL, payload TEXT NOT NULL,
  UNIQUE(id, version)
);
CREATE TABLE IF NOT EXISTS event_affected (
  row_id INTEGER NOT NULL, srtn_cd TEXT NOT NULL, relevance REAL,
  PRIMARY KEY (row_id, srtn_cd)
);
CREATE INDEX IF NOT EXISTS idx_ev_id ON events(id);
CREATE INDEX IF NOT EXISTS idx_ev_asof ON events(as_of);
CREATE INDEX IF NOT EXISTS idx_ev_ctype ON events(catalyst_type);
CREATE INDEX IF NOT EXISTS idx_ea_srtn ON event_affected(srtn_cd);
"""

_LATEST = "version = (SELECT MAX(v.version) FROM events v WHERE v.id = events.id)"


class EventStore:
    """EventRecord append-only SQLite. 재스코어링=새 version append, 조회=최신 version."""

    def __init__(self, db_path: Path = DEFAULT_EVENTS_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(EVENTS_DDL)

    def append(self, records: Sequence[EventRecord]) -> int:
        """레코드 적재 — id별 version 자동 증가. 반환=적재 건수."""
        n = 0
        for rec in records:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(version), 0) FROM events WHERE id = ?", (rec.id,)
            ).fetchone()
            version = int(row[0]) + 1
            cur = self._conn.execute(
                "INSERT INTO events (id, version, as_of, fetched_at, source, type, catalyst_type, "
                "scope, catalyst_strength, novelty, summary_1line, payload) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rec.id, version, rec.as_of.isoformat(), rec.fetched_at.isoformat(), rec.source,
                    rec.type.value,
                    rec.catalyst_type.value if rec.catalyst_type else None,
                    rec.scope.value if rec.scope else None,
                    rec.catalyst_strength, rec.novelty, rec.summary_1line, rec.model_dump_json(),
                ),
            )
            row_id = cur.lastrowid
            if row_id is not None:
                for a in rec.affected:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO event_affected (row_id, srtn_cd, relevance) VALUES (?,?,?)",
                        (row_id, a.srtn_cd, a.relevance),
                    )
            n += 1
        self._conn.commit()
        return n

    def recent(self, *, limit: int = 200) -> list[EventRecord]:
        """최신 version 이벤트 — as_of 최신순."""
        cur = self._conn.execute(
            f"SELECT payload FROM events WHERE {_LATEST} ORDER BY as_of DESC LIMIT ?", (limit,)
        )
        return [EventRecord.model_validate_json(r[0]) for r in cur]

    def for_srtn(self, srtn_cd: str, *, limit: int = 50) -> list[EventRecord]:
        """해당 종목(affected)에 연결된 최신 version 이벤트 — as_of 최신순."""
        cur = self._conn.execute(
            "SELECT events.payload FROM events JOIN event_affected a ON a.row_id = events.row_id "
            f"WHERE a.srtn_cd = ? AND {_LATEST} ORDER BY events.as_of DESC LIMIT ?",
            (srtn_cd, limit),
        )
        return [EventRecord.model_validate_json(r[0]) for r in cur]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        self._conn.close()


__all__ = ["DEFAULT_EVENTS_DB", "EventStore"]
