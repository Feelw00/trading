"""PositionStore — 보유 포지션 append-only 영속 (P-8).

``data/positions.sqlite``. 다른 저널과 동일 규약: UPDATE/DELETE 금지, 상태 전이
(open→closed)·수량 변경은 새 version append로만. 조회=최신 version.
"""

import sqlite3
from pathlib import Path

from trading.contracts.position import PositionRecord, PositionStatus

DEFAULT_POSITIONS_DB = Path("data") / "positions.sqlite"

POSITIONS_DDL = """
CREATE TABLE IF NOT EXISTS positions (
  row_id INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL, version INTEGER NOT NULL,
  as_of TEXT NOT NULL, symbol TEXT NOT NULL, status TEXT NOT NULL,
  payload TEXT NOT NULL,
  UNIQUE(id, version)
);
CREATE INDEX IF NOT EXISTS idx_pos_symbol ON positions(symbol);
"""


class PositionStore:
    """보유 포지션 append-only SQLite. 조회=최신 version."""

    def __init__(self, db_path: Path | None = None) -> None:
        # 기본 경로는 호출 시점 해석 — 테스트가 DEFAULT_POSITIONS_DB를 격리 경로로 패치 가능
        resolved = db_path if db_path is not None else DEFAULT_POSITIONS_DB
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(resolved))
        self._conn.executescript(POSITIONS_DDL)

    def append(self, pos: PositionRecord) -> int:
        """새 version append(등록·수량변경·정리 전이 공용). 반환=version."""
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM positions WHERE id = ?", (pos.id,)
        ).fetchone()
        version = int(row[0]) + 1
        self._conn.execute(
            "INSERT INTO positions (id, version, as_of, symbol, status, payload) "
            "VALUES (?,?,?,?,?,?)",
            (
                pos.id, version, pos.as_of.isoformat(), pos.symbol,
                pos.status.value, pos.model_dump_json(),
            ),
        )
        self._conn.commit()
        return version

    def get(self, position_id: str) -> PositionRecord | None:
        """최신 version 1건."""
        row = self._conn.execute(
            "SELECT payload FROM positions WHERE id = ? ORDER BY version DESC LIMIT 1",
            (position_id,),
        ).fetchone()
        return PositionRecord.model_validate_json(row[0]) if row else None

    def open_positions(self) -> list[PositionRecord]:
        """보유 중(open) 포지션 — 최신 version 기준."""
        rows = self._conn.execute(
            "SELECT payload FROM positions WHERE version = "
            "(SELECT MAX(v.version) FROM positions v WHERE v.id = positions.id) "
            "ORDER BY as_of"
        ).fetchall()
        out = []
        for (payload,) in rows:
            pos = PositionRecord.model_validate_json(payload)
            if pos.status is PositionStatus.OPEN:
                out.append(pos)
        return out

    def latest_for_source(self, source_ref: str) -> PositionRecord | None:
        """초안(source_ref)에 연결된 포지션의 최신 상태 — 재진입 판정(EXEC-8)용."""
        rows = self._conn.execute(
            "SELECT payload FROM positions WHERE version = "
            "(SELECT MAX(v.version) FROM positions v WHERE v.id = positions.id) "
            "ORDER BY as_of DESC"
        ).fetchall()
        for (payload,) in rows:
            pos = PositionRecord.model_validate_json(payload)
            if pos.source_ref == source_ref:
                return pos
        return None

    def close(self) -> None:
        self._conn.close()


__all__ = ["DEFAULT_POSITIONS_DB", "PositionStore"]
