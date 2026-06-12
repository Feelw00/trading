"""PlaybookStore — R5 산출(플레이북·주문 초안·합성 메타) append-only 영속.

``data/playbooks.sqlite`` (이벤트·논제 DB 동격). 라운드 간 전달은 DB로만(설계서 §3):
- R5.5(아침 선택기)가 당일 PlaybookSet 을 읽어 발동 판정.
- R6(저녁 보고)가 OrderDraft 승인 요청 목록·체크리스트를 렌더.
status 변경(draft→approved→armed…)도 새 version append 로만(UPDATE 금지).
"""

import json
import sqlite3
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from trading.collectors.base import KST
from trading.contracts.order import OrderDraft, OrderStatus
from trading.contracts.playbook import Playbook
from trading.contracts.scenario import ScenarioAxis, axes_from_stored, axes_to_stored
from trading.market_calendar.calendar import MarketCalendar

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
        scenario_tree: Sequence[ScenarioAxis],
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
            (as_of, axes_to_stored(scenario_tree), json.dumps(list(checklist), ensure_ascii=False)),
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

    def _pool(
        self, now: datetime, *, status: OrderStatus, enforce_ttl: bool,
        calendar: MarketCalendar | None,
    ) -> list[tuple[Playbook, OrderDraft, date | None]]:
        """status별 풀 조회(날짜 라벨 비의존). 같은 (종목,방향)은 최신 초안만.

        만료일(초안 거래일 + time_stop_days 거래일)을 계산해 동봉하고, ``enforce_ttl`` 이면
        경과분을 제외한다(approved 풀은 강제, 미승인 후보는 참고 표기만).
        """
        cal = calendar if calendar is not None else MarketCalendar.default()
        today = now.astimezone(KST).date()
        rows = self._conn.execute(
            "SELECT payload FROM playbooks WHERE version = "
            "(SELECT MAX(v.version) FROM playbooks v WHERE v.id = playbooks.id) "
            "ORDER BY as_of DESC"  # 같은 종목·방향 중 최신 우선
        ).fetchall()
        seen: set[tuple[str, str]] = set()
        out: list[tuple[Playbook, OrderDraft, date | None]] = []
        for (payload,) in rows:
            pb = Playbook.model_validate_json(payload)
            draft = self.draft(pb.order_draft_ref)
            if draft is None or draft.status is not status:
                continue
            expiry: date | None = None
            if draft.time_stop_days is not None:
                expiry = cal.add_trading_days(
                    draft.as_of.astimezone(KST).date(), draft.time_stop_days
                )
                if enforce_ttl and today > expiry:
                    continue  # TTL 경과 — 셋업 만료(추격 금지)
            key = (draft.symbol, draft.side.value)
            if key in seen:
                continue  # 더 최신 초안을 이미 채택
            seen.add(key)
            out.append((pb, draft, expiry))
        return out

    def active_playbooks(
        self, now: datetime, *, calendar: MarketCalendar | None = None
    ) -> list[tuple[Playbook, OrderDraft, date | None]]:
        """활성 approved 풀 — status=approved + TTL 미경과(arm-check/R5.5 입력).

        같은 (종목, 방향)은 최신 초안만(매일 R5 재생성 중복 제거). 반환은
        (Playbook, OrderDraft, 만료일|None) — time_stop_days 없으면 만료일 None.
        """
        return self._pool(
            now, status=OrderStatus.APPROVED, enforce_ttl=True, calendar=calendar
        )

    def candidate_playbooks(
        self, now: datetime, *, calendar: MarketCalendar | None = None
    ) -> list[tuple[Playbook, OrderDraft, date | None]]:
        """미승인(draft) 후보 풀 — 아침 arm-check가 검토·승인 대상으로 조회.

        TTL 미적용(승인 전이라 만료 개념 없음) — 만료일은 "승인 시 유효기간" 참고 표기.
        같은 (종목, 방향)은 최신 초안만.
        """
        return self._pool(
            now, status=OrderStatus.DRAFT, enforce_ttl=False, calendar=calendar
        )

    def pending_drafts(self) -> list[OrderDraft]:
        """미승인(draft) 최신 초안 — 승인 도구·arm-check 힌트용. 만료 무관(승인 전)."""
        rows = self._conn.execute(
            "SELECT DISTINCT id FROM order_drafts ORDER BY id"
        ).fetchall()
        out: list[OrderDraft] = []
        for (draft_id,) in rows:
            d = self.draft(draft_id)
            if d is not None and d.status is OrderStatus.DRAFT:
                out.append(d)
        return out

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

    def latest_run(self) -> tuple[str, list[ScenarioAxis], list[str]] | None:
        """최근 합성 메타 (as_of, scenario_tree 축 목록, checklist) — R6 렌더용.

        구조화 이전 산문 레코드는 ``axes_from_stored`` 가 줄 단위로 감싸 호환.
        """
        row = self._conn.execute(
            "SELECT as_of, scenario_tree, checklist FROM synth_runs ORDER BY row_id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return str(row[0]), axes_from_stored(str(row[1])), [str(c) for c in json.loads(row[2])]

    def close(self) -> None:
        self._conn.close()


__all__ = ["DEFAULT_PLAYBOOKS_DB", "PlaybookStore"]
