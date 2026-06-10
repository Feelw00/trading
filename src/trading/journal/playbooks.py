"""PlaybookStore — R5 산출(플레이북·주문 초안·합성 메타) append-only 영속.

``data/playbooks.sqlite`` (이벤트·논제 DB 동격). 라운드 간 전달은 DB로만(설계서 §3):
- R5.5(아침 선택기)가 당일 PlaybookSet 을 읽어 발동 판정.
- R6(저녁 보고)가 OrderDraft 승인 요청 목록·체크리스트를 렌더.
status 변경(draft→approved→armed…)도 새 version append 로만(UPDATE 금지).
"""

import json
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from trading.contracts.order import OrderDraft
from trading.contracts.playbook import Playbook

DEFAULT_PLAYBOOKS_DB = Path("data") / "playbooks.sqlite"

PLAYBOOKS_DDL = """
CREATE TABLE IF NOT EXISTS playbooks (
  row_id INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL, version INTEGER NOT NULL,
  as_of TEXT NOT NULL, thesis_ref TEXT NOT NULL, order_draft_ref TEXT NOT NULL,
  payload TEXT NOT NULL,
  UNIQUE(id, version)
);
CREATE TABLE IF NOT EXISTS order_drafts (
  row_id INTEGER PRIMARY KEY AUTOINCREMENT,
  id TEXT NOT NULL, version INTEGER NOT NULL,
  as_of TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL, status TEXT NOT NULL,
  payload TEXT NOT NULL,
  UNIQUE(id, version)
);
CREATE TABLE IF NOT EXISTS synth_runs (
  row_id INTEGER PRIMARY KEY AUTOINCREMENT,
  as_of TEXT NOT NULL, scenario_tree TEXT NOT NULL, checklist TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pb_asof ON playbooks(as_of);
CREATE INDEX IF NOT EXISTS idx_od_asof ON order_drafts(as_of);
"""


def _next_version(conn: sqlite3.Connection, table: str, rec_id: str) -> int:
    row = conn.execute(
        f"SELECT COALESCE(MAX(version), 0) FROM {table} WHERE id = ?", (rec_id,)  # noqa: S608
    ).fetchone()
    return int(row[0]) + 1


class PlaybookStore:
    """플레이북·주문 초안 append-only SQLite. 조회=최신 version."""

    def __init__(self, db_path: Path = DEFAULT_PLAYBOOKS_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(PLAYBOOKS_DDL)

    def append_run(
        self,
        playbooks: Sequence[Playbook],
        drafts: Sequence[OrderDraft],
        *,
        as_of: str,
        scenario_tree: str,
        checklist: Sequence[str],
    ) -> int:
        """R5 1회 산출 일괄 적재. 반환=적재 레코드 수(플레이북+초안)."""
        n = 0
        for pb in playbooks:
            self._conn.execute(
                "INSERT INTO playbooks (id, version, as_of, thesis_ref, order_draft_ref, payload) "
                "VALUES (?,?,?,?,?,?)",
                (
                    pb.id, _next_version(self._conn, "playbooks", pb.id),
                    pb.as_of.isoformat(), pb.thesis_ref, pb.order_draft_ref,
                    pb.model_dump_json(),
                ),
            )
            n += 1
        for od in drafts:
            self._conn.execute(
                "INSERT INTO order_drafts (id, version, as_of, symbol, side, status, payload) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    od.id, _next_version(self._conn, "order_drafts", od.id),
                    od.as_of.isoformat(), od.symbol, od.side.value, od.status.value,
                    od.model_dump_json(),
                ),
            )
            n += 1
        self._conn.execute(
            "INSERT INTO synth_runs (as_of, scenario_tree, checklist) VALUES (?,?,?)",
            (as_of, scenario_tree, json.dumps(list(checklist), ensure_ascii=False)),
        )
        self._conn.commit()
        return n

    def playbooks_for_day(self, day: str) -> list[Playbook]:
        """해당 일자(YYYYMMDD, id 규약 ``pb.<day>.…``)의 최신 version 플레이북 — R5.5 입력."""
        rows = self._conn.execute(
            "SELECT payload FROM playbooks WHERE id LIKE ? AND version = "
            "(SELECT MAX(v.version) FROM playbooks v WHERE v.id = playbooks.id) ORDER BY id",
            (f"pb.{day}.%",),
        ).fetchall()
        return [Playbook.model_validate_json(p) for (p,) in rows]

    def draft(self, draft_id: str) -> OrderDraft | None:
        """주문 초안 최신 version 1건."""
        row = self._conn.execute(
            "SELECT payload FROM order_drafts WHERE id = ? ORDER BY version DESC LIMIT 1",
            (draft_id,),
        ).fetchone()
        return OrderDraft.model_validate_json(row[0]) if row else None

    def append_draft(self, draft: OrderDraft) -> int:
        """초안 새 version append(status 전이용 — draft→approved 등). 반환=version."""
        version = _next_version(self._conn, "order_drafts", draft.id)
        self._conn.execute(
            "INSERT INTO order_drafts (id, version, as_of, symbol, side, status, payload) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                draft.id, version, draft.as_of.isoformat(), draft.symbol,
                draft.side.value, draft.status.value, draft.model_dump_json(),
            ),
        )
        self._conn.commit()
        return version

    def latest_run(self) -> tuple[str, str, list[str]] | None:
        """최근 합성 메타 (as_of, scenario_tree, checklist) — R6 렌더용."""
        row = self._conn.execute(
            "SELECT as_of, scenario_tree, checklist FROM synth_runs ORDER BY row_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return str(row[0]), str(row[1]), [str(c) for c in json.loads(row[2])]

    def close(self) -> None:
        self._conn.close()


__all__ = ["DEFAULT_PLAYBOOKS_DB", "PlaybookStore"]
