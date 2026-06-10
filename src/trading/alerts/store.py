"""AlertStore — 알림 append-only 영속 (``data/alerts.sqlite``, 시세·뉴스·이벤트 DB 동격).

- ``alerts``: 생성된 모든 알림(P2 포함 — R6 보고가 읽는다). UPDATE/DELETE 금지.
- ``dispatches``: 발송 기록(어느 알림을 어느 채널로 언제). 발송 상태 변경도 행 추가로만.
  P1 다이제스트의 "이미 보낸 것 재발송 금지"는 dispatches 부재 조건으로 판정.
"""

import sqlite3
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from trading.alerts.model import Alert
from trading.collectors.base import now_kst

DEFAULT_ALERTS_DB = Path("data") / "alerts.sqlite"

ALERTS_DDL = """
CREATE TABLE IF NOT EXISTS alerts (
  row_id INTEGER PRIMARY KEY AUTOINCREMENT,
  severity TEXT NOT NULL,
  what TEXT NOT NULL, rule TEXT NOT NULL, action TEXT, deadline TEXT,
  created_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dispatches (
  alert_row_id INTEGER NOT NULL, channel TEXT NOT NULL, sent_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_al_sev ON alerts(severity);
CREATE INDEX IF NOT EXISTS idx_al_created ON alerts(created_at);
CREATE INDEX IF NOT EXISTS idx_di_alert ON dispatches(alert_row_id);
"""


class AlertStore:
    """알림·발송기록 append-only SQLite."""

    def __init__(self, db_path: Path = DEFAULT_ALERTS_DB) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.executescript(ALERTS_DDL)

    def append(self, alert: Alert) -> int:
        """알림 적재 — 반환 row_id(발송 기록의 참조 키)."""
        cur = self._conn.execute(
            "INSERT INTO alerts (severity, what, rule, action, deadline, created_at, payload) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                alert.severity.value, alert.what, alert.rule, alert.action,
                alert.deadline, alert.created_at.isoformat(), alert.model_dump_json(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid or 0)

    def mark_dispatched(
        self, row_ids: Sequence[int], channel: str, *, sent_at: datetime | None = None
    ) -> None:
        ts = (sent_at if sent_at is not None else now_kst()).isoformat()
        self._conn.executemany(
            "INSERT INTO dispatches (alert_row_id, channel, sent_at) VALUES (?,?,?)",
            [(rid, channel, ts) for rid in row_ids],
        )
        self._conn.commit()

    def pending(self, severity: str) -> list[tuple[int, Alert]]:
        """미발송 알림(해당 등급) — 생성순. P1 다이제스트가 사용."""
        rows = self._conn.execute(
            "SELECT row_id, payload FROM alerts WHERE severity = ? "
            "AND row_id NOT IN (SELECT alert_row_id FROM dispatches) ORDER BY row_id",
            (severity,),
        ).fetchall()
        return [(int(rid), Alert.model_validate_json(payload)) for rid, payload in rows]

    def recent(self, *, limit: int = 100) -> list[Alert]:
        """최근 알림(전 등급) — R6 보고 렌더용."""
        rows = self._conn.execute(
            "SELECT payload FROM alerts ORDER BY row_id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [Alert.model_validate_json(p) for (p,) in rows]

    def close(self) -> None:
        self._conn.close()


__all__ = ["AlertStore", "DEFAULT_ALERTS_DB"]
