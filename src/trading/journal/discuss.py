"""DiscussPack 캐시 — append-only 버전 레코드 (PROPOSALS P-5).

같은 종목 재토론 시 최신 버전을 재사용하고, 갱신은 새 버전 INSERT(UPDATE/DELETE 금지).
TTL 없음(영구 보존 — 운영자 결정 2026-06-11). 신선도 판정은 저장이 아니라 조회측
(``discuss_pack --check``)이 시세 DB 최신 거래일과 비교해 결정론으로 한다.
"""

import sqlite3
from pathlib import Path

from trading.contracts.discuss import DiscussPack

DEFAULT_DISCUSS_DB = Path("data") / "discuss.sqlite"

DDL = """
CREATE TABLE IF NOT EXISTS discuss_packs (
  srtn_cd TEXT NOT NULL,
  version INTEGER NOT NULL,
  built_at TEXT NOT NULL,        -- KST ISO8601
  price_as_of TEXT NOT NULL,     -- 팩 가격맥락 기준 거래일(YYYYMMDD) — 신선도 비교 키
  pack_json TEXT NOT NULL,
  UNIQUE(srtn_cd, version)
);
CREATE TABLE IF NOT EXISTS processed_news (
  srtn_cd TEXT NOT NULL,
  news_id TEXT NOT NULL,
  processed_at TEXT NOT NULL,    -- KST ISO8601
  UNIQUE(srtn_cd, news_id)
);
"""


class DiscussStore:
    """DiscussPack append-only 캐시(SQLite)."""

    def __init__(self, db_path: Path = DEFAULT_DISCUSS_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(DDL)

    def append(self, pack: DiscussPack) -> int:
        """새 버전으로 적재. 부여된 version 반환."""
        code = pack.fact.srtn_cd
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM discuss_packs WHERE srtn_cd=?", (code,)
        ).fetchone()
        version = int(row[0]) + 1
        self._conn.execute(
            "INSERT INTO discuss_packs (srtn_cd, version, built_at, price_as_of, pack_json) "
            "VALUES (?,?,?,?,?)",
            (code, version, pack.built_at.isoformat(), pack.fact.price.as_of,
             pack.model_dump_json()),
        )
        self._conn.commit()
        return version

    def latest(self, srtn_cd: str) -> tuple[int, DiscussPack] | None:
        """최신 버전 (version, pack). 없으면 None."""
        row = self._conn.execute(
            "SELECT version, pack_json FROM discuss_packs WHERE srtn_cd=? "
            "ORDER BY version DESC LIMIT 1",
            (srtn_cd,),
        ).fetchone()
        if row is None:
            return None
        return int(row[0]), DiscussPack.model_validate_json(str(row[1]))

    def processed_news_ids(self, srtn_cd: str) -> set[str]:
        """이 종목 토론에서 이미 R2에 들어간 뉴스 id — 미이벤트화 뉴스 재처리 방지."""
        cur = self._conn.execute(
            "SELECT news_id FROM processed_news WHERE srtn_cd=?", (srtn_cd,)
        )
        return {str(r[0]) for r in cur}

    def mark_news_processed(self, srtn_cd: str, news_ids: list[str], processed_at: str) -> None:
        self._conn.executemany(
            "INSERT OR IGNORE INTO processed_news (srtn_cd, news_id, processed_at) VALUES (?,?,?)",
            [(srtn_cd, nid, processed_at) for nid in news_ids],
        )
        self._conn.commit()

    def versions(self, srtn_cd: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM discuss_packs WHERE srtn_cd=?", (srtn_cd,)
        ).fetchone()
        return int(row[0]) if row else 0

    def close(self) -> None:
        self._conn.close()


__all__ = ["DEFAULT_DISCUSS_DB", "DiscussStore"]
