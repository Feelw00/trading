"""장중 발동 감시기 — 승인(approved) 풀의 arm 조건을 실시간 평가해 충족 순간 P0 발화.

배경(운영자 2026-07-12): 발동 조건(전일고가 회복·체결강도·호가)은 장중 아무 때나 충족될 수
있는데 아침 arm-check는 1회 스냅샷이라, 조건 감시가 운영자 몫으로 남는 모순이 있었다
(P-9의 출발점 "계속 보고 있어야 하는 건 어렵다"와 충돌). 이 감시기가 그 구멍을 메운다.

**순수 코드(LLM 미개입, 절대금지 #2).** 판정은 arm-check와 동일 경로
(``flowsnap``(KIS 실시간) → ``selector.engine``)를 그대로 재사용 — 감시기는 새 판단을
만들지 않고 같은 판정을 주기 반복할 뿐이다. 주문도 없다(절대금지 #3) — 알림을 받은
운영자가 지정가/조건부 주문을 직접 입력한다(의도된 마찰).

알림 규약:
- 발동(P0, 즉시): 활성 approved 초안의 조건 전부 충족 — 행동="지정가 진입 검토+손절 동시
  입력", 기한="15:00 전"(운영자 거래 창). 초안·일자당 1회(WatchStore dedup, append-only).
- 마감 전 정리(P0, 14:40~): 활성 풀이 남아 있으면 "미체결 예약 취소·손절 주문 확인" 1회.
- 미승인 후보(candidates)는 감시하지 않는다 — 승인 전 발동 알림은 소음이다.

스케줄: openclaw cron이 09:00에 ``python -m trading.run arm-watch``(내부 루프, 15:00 종료)를
fire-and-forget으로 기동 + 12:00 재기동 슬롯(사망 대비). 중복 기동은 하트비트 파일로 차단.
자체 스케줄러 아님 — 기동은 전적으로 openclaw cron, 이 프로세스는 세션 내 폴링만 한다.
(README "예정"이던 heartbeat 배선의 실현체 — openclaw heartbeat CLI 검증 후 이관 가능.)
"""

import sqlite3
import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path

from trading.alerts import Alert, AlertDispatcher, Severity
from trading.arm_check import AssessResult, assess
from trading.collectors.base import KST, now_kst
from trading.market_calendar.calendar import MarketCalendar

DEFAULT_DB = Path("data") / "watch.sqlite"
HEARTBEAT_FILE = Path(".runtime") / "watch-heartbeat"

GUARD_SKIP_RC = 3  # trading.run 규약과 동일 — 세션 밖 정상 스킵


@dataclass(frozen=True)
class WatchConfig:
    session_start: time = time(9, 0)
    session_end: time = time(15, 0)    # 운영자 거래 창 상한(2026-07-12) — 정규장 15:30보다 이름
    closeout_from: time = time(14, 40)  # 마감 전 정리 리마인더 창 시작
    poll_seconds: int = 90              # KIS 폴링 간격(초안 수 × TR 콜 고려한 보수값)
    heartbeat_stale_s: int = 300        # 이 이내 하트비트가 있으면 중복 기동으로 보고 종료


_DDL = """
CREATE TABLE IF NOT EXISTS fired (
  day TEXT NOT NULL, key TEXT NOT NULL, kind TEXT NOT NULL, fired_at TEXT NOT NULL,
  UNIQUE(day, key, kind)
);
"""


class WatchStore:
    """발화 기록(append-only) — 같은 초안·같은 날 중복 알림 차단. 재기동에도 유지."""

    def __init__(self, db_path: Path | None = None) -> None:
        resolved = db_path if db_path is not None else DEFAULT_DB
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(resolved))
        self._conn.executescript(_DDL)

    def fired(self, day: str, key: str, kind: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM fired WHERE day=? AND key=? AND kind=?", (day, key, kind)
        ).fetchone()
        return row is not None

    def record(self, day: str, key: str, kind: str, at: str) -> bool:
        before = self._conn.total_changes
        self._conn.execute(
            "INSERT OR IGNORE INTO fired VALUES (?,?,?,?)", (day, key, kind, at)
        )
        self._conn.commit()
        return self._conn.total_changes > before

    def close(self) -> None:
        self._conn.close()


@dataclass(frozen=True)
class PassResult:
    rc: int
    fired: list[str] = field(default_factory=list)   # 이번 패스에 발화한 알림 키
    notes: list[str] = field(default_factory=list)


def _in_watch_window(now: datetime, cfg: WatchConfig, cal: MarketCalendar) -> bool:
    local = now.astimezone(KST)
    return cal.is_trading_day(local.date()) and cfg.session_start <= local.time() < cfg.session_end


def run_pass(
    *,
    now: datetime | None = None,
    config: WatchConfig | None = None,
    store: WatchStore | None = None,
    dispatcher: AlertDispatcher | None = None,
    calendar: MarketCalendar | None = None,
    assess_fn: Callable[..., AssessResult] = assess,
    playbook_store: object | None = None,
    kis_client: object | None = None,
) -> PassResult:
    """감시 1패스 — 세션 밖이면 rc=3. 발화는 초안·일자당 1회."""
    cfg = config or WatchConfig()
    resolved = (now if now is not None else now_kst()).astimezone(KST)
    cal = calendar if calendar is not None else MarketCalendar.default()
    if not _in_watch_window(resolved, cfg, cal):
        return PassResult(GUARD_SKIP_RC, notes=["감시 창 밖(거래일 09:00~15:00 아님)"])

    own_store = store is None
    st = store if store is not None else WatchStore()
    d = dispatcher if dispatcher is not None else AlertDispatcher()
    day = resolved.strftime("%Y%m%d")
    fired: list[str] = []
    notes: list[str] = []
    try:
        res = assess_fn(now=resolved, playbook_store=playbook_store, kis_client=kis_client)
        notes.extend(res.snapshot_notes)
        for it in res.items:  # 활성 approved 풀만
            if not it.active or st.fired(day, it.draft_id, "armed"):
                continue
            met = " / ".join(c.cond_ko for c in it.conditions if c.met) or "(조건 상세 없음)"
            d.notify(
                Alert(
                    severity=Severity.P0,
                    what=f"발동 — {it.headline}: 조건 전부 충족 ({met})",
                    rule="장중 감시기: approved 초안 arm 조건 전원 충족",
                    action=f"지정가 진입 검토 + 손절 동시 입력 ({it.stop} · {it.cap})",
                    deadline="장중 15:00 전",
                    created_at=resolved,
                )
            )
            st.record(day, it.draft_id, "armed", resolved.isoformat())
            fired.append(f"armed:{it.draft_id}")
        # 마감 전 정리 — 활성 풀이 있고(미발동 포함) 아직 안 알렸으면 1회
        if (
            resolved.time() >= cfg.closeout_from
            and res.items
            and not st.fired(day, "_session", "closeout")
        ):
            d.notify(
                Alert(
                    severity=Severity.P0,
                    what=f"마감 전 정리 — 활성 초안 {len(res.items)}건(발동 {res.active_count})",
                    rule="장중 감시기: 14:40 마감 전 점검 창",
                    action="미체결 예약 취소 · 체결분 손절 주문 확인",
                    deadline="15:00",
                    created_at=resolved,
                )
            )
            st.record(day, "_session", "closeout", resolved.isoformat())
            fired.append("closeout")
    finally:
        if own_store:
            st.close()
    return PassResult(0, fired=fired, notes=notes)


def _heartbeat_fresh(now: datetime, cfg: WatchConfig, path: Path = HEARTBEAT_FILE) -> bool:
    try:
        age = now.timestamp() - path.stat().st_mtime
    except FileNotFoundError:
        return False
    return 0 <= age < cfg.heartbeat_stale_s


def run_loop(
    *,
    config: WatchConfig | None = None,
    heartbeat_path: Path = HEARTBEAT_FILE,
    sleep_fn: Callable[[float], None] = _time.sleep,
    max_passes: int | None = None,
) -> int:
    """세션 내 폴링 루프 — cron(09:00·12:00 재기동)이 fire-and-forget으로 실행.

    거래일 아님/세션 밖이면 rc=3 즉시 종료. 이미 가동 중(하트비트 신선)이면 rc=0 종료.
    """
    cfg = config or WatchConfig()
    now = now_kst()
    if _heartbeat_fresh(now, cfg, heartbeat_path):
        print("감시기 이미 가동 중(하트비트 신선) — 중복 기동 종료")
        return 0
    first = run_pass(now=now, config=cfg)
    if first.rc == GUARD_SKIP_RC:
        print(f"감시 창 밖 — 스킵(rc={GUARD_SKIP_RC})")
        return GUARD_SKIP_RC
    passes = 1
    _report_pass(first)
    heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat_path.touch()
    while max_passes is None or passes < max_passes:
        sleep_fn(cfg.poll_seconds)
        heartbeat_path.touch()
        r = run_pass(config=cfg)
        passes += 1
        if r.rc == GUARD_SKIP_RC:  # 15:00 도달 — 정상 종료
            print(f"세션 종료 — 감시 마감(패스 {passes})")
            return 0
        _report_pass(r)
    return 0


def _report_pass(r: PassResult) -> None:
    if r.fired:
        print(f"발화: {', '.join(r.fired)}")
    for n in r.notes:
        print(f"  note: {n}")


__all__ = ["GUARD_SKIP_RC", "PassResult", "WatchConfig", "WatchStore", "run_loop", "run_pass"]
