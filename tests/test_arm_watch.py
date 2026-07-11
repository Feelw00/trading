"""장중 발동 감시기 — 세션 가드·1회 발화·dedup·마감 리마인더·중복 기동 차단."""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading.alerts.model import Alert
from trading.arm_check import AssessResult, ConditionView, ItemView
from trading.market_calendar.calendar import MarketCalendar
from trading.watch.arm_watch import (
    GUARD_SKIP_RC,
    WatchConfig,
    WatchStore,
    run_loop,
    run_pass,
)

KST = ZoneInfo("Asia/Seoul")
CAL = MarketCalendar()  # 주말만 휴장(추가 휴장 없음) — 테스트 결정론
MON_10AM = datetime(2026, 7, 13, 10, 0, tzinfo=KST)


class _Recorder:
    def __init__(self) -> None:
        self.alerts: list[Alert] = []

    def notify(self, alert: Alert) -> str:
        self.alerts.append(alert)
        return "sent:test"


def _item(*, active: bool, draft_id: str = "order.20260712.161890.buy") -> ItemView:
    return ItemView(
        playbook_id="pb.x", headline="한국콜마(161890) 매수", draft_id=draft_id,
        status="approved", active=active, summary="s",
        conditions=[ConditionView(cond_ko="전일 고가 회복", met=active, observed=1.0, note="")],
        tranches=[], stop="가격 스탑 100000", cap="기본단위의 50%",
    )


def _assess(items: list[ItemView]) -> Any:
    def fn(**kw: Any) -> AssessResult:
        return AssessResult(day="2026-07-13", now_iso="x", in_session=True,
                            snapshot_notes=[], items=items)
    return fn


def test_out_of_session_skips(tmp_path: Path) -> None:
    rec = _Recorder()
    for bad in (
        datetime(2026, 7, 12, 10, 0, tzinfo=KST),   # 일요일
        datetime(2026, 7, 13, 8, 59, tzinfo=KST),   # 개장 전
        datetime(2026, 7, 13, 15, 0, tzinfo=KST),   # 15:00 — 운영자 거래 창 밖(경계)
    ):
        r = run_pass(now=bad, store=WatchStore(tmp_path / "w.sqlite"),
                     dispatcher=rec, calendar=CAL, assess_fn=_assess([_item(active=True)]))  # type: ignore[arg-type]
        assert r.rc == GUARD_SKIP_RC
    assert rec.alerts == []


def test_active_fires_once_and_dedups(tmp_path: Path) -> None:
    st = WatchStore(tmp_path / "w.sqlite")
    rec = _Recorder()
    fn = _assess([_item(active=True)])
    r1 = run_pass(now=MON_10AM, store=st, dispatcher=rec, calendar=CAL, assess_fn=fn)  # type: ignore[arg-type]
    assert r1.rc == 0 and r1.fired == ["armed:order.20260712.161890.buy"]
    assert len(rec.alerts) == 1
    a = rec.alerts[0]
    assert a.severity.value == "P0" and "발동" in a.what and "전일 고가 회복" in a.what
    assert "손절" in a.action and "15:00" in a.deadline
    # 같은 날 재패스 — 무발화
    r2 = run_pass(now=MON_10AM, store=st, dispatcher=rec, calendar=CAL, assess_fn=fn)  # type: ignore[arg-type]
    assert r2.fired == [] and len(rec.alerts) == 1
    st.close()


def test_inactive_does_not_fire(tmp_path: Path) -> None:
    rec = _Recorder()
    r = run_pass(now=MON_10AM, store=WatchStore(tmp_path / "w.sqlite"),
                 dispatcher=rec, calendar=CAL, assess_fn=_assess([_item(active=False)]))  # type: ignore[arg-type]
    assert r.rc == 0 and r.fired == [] and rec.alerts == []


def test_closeout_reminder_once_in_window(tmp_path: Path) -> None:
    st = WatchStore(tmp_path / "w.sqlite")
    rec = _Recorder()
    fn = _assess([_item(active=False)])
    at_1445 = datetime(2026, 7, 13, 14, 45, tzinfo=KST)
    r = run_pass(now=at_1445, store=st, dispatcher=rec, calendar=CAL, assess_fn=fn)  # type: ignore[arg-type]
    assert r.fired == ["closeout"]
    assert "마감 전 정리" in rec.alerts[0].what
    r2 = run_pass(now=at_1445, store=st, dispatcher=rec, calendar=CAL, assess_fn=fn)  # type: ignore[arg-type]
    assert r2.fired == [] and len(rec.alerts) == 1
    st.close()


def test_closeout_skipped_when_pool_empty(tmp_path: Path) -> None:
    rec = _Recorder()
    at_1445 = datetime(2026, 7, 13, 14, 45, tzinfo=KST)
    r = run_pass(now=at_1445, store=WatchStore(tmp_path / "w.sqlite"),
                 dispatcher=rec, calendar=CAL, assess_fn=_assess([]))  # type: ignore[arg-type]
    assert r.fired == [] and rec.alerts == []


def test_run_loop_skips_when_heartbeat_fresh(tmp_path: Path) -> None:
    hb = tmp_path / "hb"
    hb.touch()  # 방금 갱신 = 다른 인스턴스 가동 중
    assert run_loop(heartbeat_path=hb) == 0  # 즉시 종료(중복 기동 차단), run_pass 미실행
