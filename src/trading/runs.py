"""RunStore — 라운드 실행 사실 원장 append-only (``data/runs.sqlite``) · ALERT-1.

운영자 결정(2026-09-02): v0.3 체인(eod-v3·weekly-v3)은 끝나면 성공/실패 1통을 텔레그램으로
보내고(실행 보고), 별도 감시 슬롯(check-*)이 "오늘 실행 기록이 없다"를 잡아낸다(미발화 감시 —
openclaw 트리거 턴 자체가 실패하면 Python은 뜨지 않으므로 사실 원장이 있어야 부재를 판정한다).

- 행 = 이벤트(``started`` / ``finished``). UPDATE/DELETE 없음 — 완료는 finished 행 추가로만.
- 타임스탬프는 KST aware ISO. 일자 판정은 시작 시각의 KST 날짜.
"""

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from trading.collectors.base import now_kst

DEFAULT_RUNS_DB = Path("data") / "runs.sqlite"

RUNS_DDL = """
CREATE TABLE IF NOT EXISTS runs (
  row_id INTEGER PRIMARY KEY AUTOINCREMENT,
  round TEXT NOT NULL, run_id TEXT NOT NULL, event TEXT NOT NULL,
  ts TEXT NOT NULL, rc INTEGER, summary TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_round_ts ON runs(round, ts);
"""


@dataclass(frozen=True)
class RunStatus:
    """한 실행의 상태 — started 행 + (있으면) finished 행."""

    round: str
    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    rc: int | None = None

    @property
    def finished(self) -> bool:
        return self.finished_at is not None


class RunStore:
    """실행 사실 append-only SQLite(WAL)."""

    def __init__(self, db_path: Path | None = None) -> None:
        path = db_path if db_path is not None else DEFAULT_RUNS_DB
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(RUNS_DDL)

    def start(self, round_name: str, *, at: datetime | None = None) -> str:
        """started 행 적재 — 반환 run_id(finished 행의 참조 키)."""
        run_id = uuid.uuid4().hex[:12]
        ts = (at if at is not None else now_kst()).isoformat()
        self._conn.execute(
            "INSERT INTO runs (round, run_id, event, ts) VALUES (?,?,?,?)",
            (round_name, run_id, "started", ts),
        )
        self._conn.commit()
        return run_id

    def finish(
        self, round_name: str, run_id: str, *, rc: int, summary: str = "",
        at: datetime | None = None,
    ) -> None:
        ts = (at if at is not None else now_kst()).isoformat()
        self._conn.execute(
            "INSERT INTO runs (round, run_id, event, ts, rc, summary) VALUES (?,?,?,?,?,?)",
            (round_name, run_id, "finished", ts, rc, summary),
        )
        self._conn.commit()

    def latest_on(self, round_name: str, day: date) -> RunStatus | None:
        """그 날(KST) 시작된 **마지막** 실행의 상태. 없으면 None(=미발화)."""
        row = self._conn.execute(
            "SELECT run_id, ts FROM runs WHERE round = ? AND event = 'started' "
            "AND substr(ts, 1, 10) = ? ORDER BY row_id DESC LIMIT 1",
            (round_name, day.isoformat()),
        ).fetchone()
        if row is None:
            return None
        run_id, started = str(row[0]), datetime.fromisoformat(str(row[1]))
        fin = self._conn.execute(
            "SELECT ts, rc FROM runs WHERE round = ? AND run_id = ? AND event = 'finished' "
            "ORDER BY row_id DESC LIMIT 1",
            (round_name, run_id),
        ).fetchone()
        if fin is None:
            return RunStatus(round_name, run_id, started)
        return RunStatus(
            round_name, run_id, started,
            finished_at=datetime.fromisoformat(str(fin[0])),
            rc=int(fin[1]) if fin[1] is not None else None,
        )

    def close(self) -> None:
        self._conn.close()


__all__ = ["DEFAULT_RUNS_DB", "RunStatus", "RunStore"]
