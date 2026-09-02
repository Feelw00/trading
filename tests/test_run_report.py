"""ALERT-1(운영자 결정 2026-09-02) — 실행 보고(성공/실패 1통)·RunStore·미발화 감시(check-*)."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import trading.run as run
import trading.runs as runs
from trading.alerts import Alert, AlertDispatcher, AlertStore, Severity
from trading.alerts.channels import ChannelError
from trading.runs import RunStore

KST = ZoneInfo("Asia/Seoul")
T0 = datetime(2026, 9, 2, 18, 0, tzinfo=KST)


@dataclass
class _Spy:
    fail: bool = False
    sent: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return "spy"

    def send(self, text: str) -> None:
        if self.fail:
            raise ChannelError("down")
        self.sent.append(text)


def _dispatcher(tmp_path: Path, *, fail: bool = False) -> tuple[AlertDispatcher, _Spy]:
    spy = _Spy(fail=fail)
    return AlertDispatcher(channel=spy, store=AlertStore(tmp_path / "alerts.sqlite")), spy


def _p1(what: str) -> Alert:
    return Alert(severity=Severity.P1, what=what, rule="r", action="a", deadline="d")


# --- RunStore ---


def test_run_store_start_finish_latest_on(tmp_path: Path) -> None:
    s = RunStore(tmp_path / "runs.sqlite")
    assert s.latest_on("eod-v3", T0.date()) is None
    rid = s.start("eod-v3", at=T0)
    st = s.latest_on("eod-v3", T0.date())
    assert st is not None and not st.finished and st.run_id == rid
    s.finish("eod-v3", rid, rc=0, summary="ok", at=T0 + timedelta(minutes=12))
    st = s.latest_on("eod-v3", T0.date())
    assert st is not None and st.finished and st.rc == 0
    assert s.latest_on("eod-v3", T0.date() + timedelta(days=1)) is None  # 날짜 경계
    assert s.latest_on("weekly-v3", T0.date()) is None                    # 라운드 분리
    s.close()


# --- 실행 보고 ---


def test_run_report_success_carries_pending_p1_tail(tmp_path: Path) -> None:
    d, spy = _dispatcher(tmp_path)
    d.notify(_p1("라운드 실패: weekly-v3 — 구 오류"))  # 적체 P1
    res = d.send_run_report(
        round_name="eod-v3", ok=True, started_at=T0, finished_at=T0 + timedelta(minutes=12),
        summary_lines=["수집 일자 20260901:2870", "수급 축적: 186/186"],
    )
    assert res == "sent:spy" and len(spy.sent) == 1
    msg = spy.sent[0]
    assert msg.startswith("✅ eod-v3 완료 · 18:00→18:12 KST (12분)")
    assert "수집 일자 20260901:2870" in msg and "미발송 P1 1건" in msg and "구 오류" in msg
    assert d.store.pending(Severity.P1.value) == []  # 꼬리로 나간 P1은 재발송 안 함
    d.send_run_report(round_name="eod-v3", ok=True, started_at=T0, finished_at=T0, summary_lines=[])
    assert "미발송" not in spy.sent[1]  # 두 번째 보고엔 꼬리 없음


def test_run_report_partial_failure_header(tmp_path: Path) -> None:
    d, spy = _dispatcher(tmp_path)
    d.notify(_p1("라운드 실패: eod-v3/toss-facts-v3 — rc=1"))  # best-effort 단계 실패
    d.send_run_report(round_name="eod-v3", ok=True, started_at=T0, finished_at=T0, summary_lines=[])
    assert spy.sent[0].startswith("⚠️ eod-v3 완료 · 부분 실패 1건")


def test_run_report_failure_header_and_log_fallback(tmp_path: Path) -> None:
    d, spy = _dispatcher(tmp_path)
    d.send_run_report(
        round_name="weekly-v3", ok=False, started_at=T0, finished_at=T0,
        summary_lines=["밸류에이션 OK"], failure="rc=1",
    )
    assert spy.sent[0].startswith("❌ weekly-v3 실패") and "실패: rc=1" in spy.sent[0]
    d2, _ = _dispatcher(tmp_path, fail=True)
    assert d2.send_run_report(
        round_name="eod-v3", ok=True, started_at=T0, finished_at=T0, summary_lines=[]
    ) == "sent:log"  # 채널 실패 → 로그 폴백, 예외 없음


# --- trading.run 배선 ---


def test_reported_round_records_run_and_sends_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runs, "DEFAULT_RUNS_DB", tmp_path / "runs.sqlite")
    got: list[dict[str, Any]] = []
    monkeypatch.setattr(
        run, "_send_run_report", lambda name, **kw: got.append({"name": name, **kw})
    )

    def _chain() -> int:
        print("수집 일자 20260901:2870")
        print("  005820 [open] 상세")  # 들여쓰기 = 종목 상세 → 요약 제외
        print("매매 가이드 5종목 · 평균 수익률(균등가중) +1.03%")
        return 0

    monkeypatch.setitem(run.ROUNDS, "eod-v3", _chain)
    assert run.main(["eod-v3"]) == 0
    assert got and got[0]["name"] == "eod-v3" and got[0]["ok"] is True
    assert got[0]["summary_lines"] == [
        "수집 일자 20260901:2870", "매매 가이드 5종목 · 평균 수익률(균등가중) +1.03%",
    ]
    s = RunStore(tmp_path / "runs.sqlite")
    st = s.latest_on("eod-v3", datetime.now(tz=KST).date())
    assert st is not None and st.finished and st.rc == 0
    s.close()


def test_reported_round_crash_is_failure_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runs, "DEFAULT_RUNS_DB", tmp_path / "runs.sqlite")
    got: list[dict[str, Any]] = []
    monkeypatch.setattr(run, "_send_run_report", lambda name, **kw: got.append(kw))

    def _boom() -> int:
        raise RuntimeError("x")

    monkeypatch.setitem(run.ROUNDS, "weekly-v3", _boom)
    assert run.main(["weekly-v3"]) == 1
    assert got[0]["ok"] is False and "RuntimeError" in str(got[0]["failure"])
    s = RunStore(tmp_path / "runs.sqlite")
    st = s.latest_on("weekly-v3", datetime.now(tz=KST).date())
    assert st is not None and st.finished and st.rc == 1  # 크래시도 finished 행(rc=1)
    s.close()


def test_non_reported_round_untouched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runs, "DEFAULT_RUNS_DB", tmp_path / "runs.sqlite")
    monkeypatch.setitem(run.ROUNDS, "_plain", lambda: 0)
    assert run.main(["_plain"]) == 0
    s = RunStore(tmp_path / "runs.sqlite")
    assert s.latest_on("_plain", datetime.now(tz=KST).date()) is None
    s.close()


def test_summarize_caps_lines() -> None:
    lines = [f"l{i}" for i in range(20)] + ["  indented", ""]
    out = run._summarize(lines, limit=5)
    assert out[:5] == ["l0", "l1", "l2", "l3", "l4"] and out[-1] == "…(+15줄)"


# --- 미발화 감시(check-*) ---


def _check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> tuple[int, _Spy]:
    monkeypatch.setattr(runs, "DEFAULT_RUNS_DB", tmp_path / "runs.sqlite")
    d, spy = _dispatcher(tmp_path)
    return run._check_run(name, dispatcher=d), spy


def test_check_run_silent_when_finished(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    s = RunStore(tmp_path / "runs.sqlite")
    now = datetime.now(tz=KST)
    rid = s.start("eod-v3", at=now)
    s.finish("eod-v3", rid, rc=1, at=now)
    s.close()
    rc, spy = _check(tmp_path, monkeypatch, "eod-v3")
    assert rc == 0 and spy.sent == []  # 실패 보고는 이미 나갔다 — 감시는 침묵


def test_check_run_alerts_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rc, spy = _check(tmp_path, monkeypatch, "eod-v3")
    assert rc == 0 and len(spy.sent) == 1
    assert spy.sent[0].startswith("❌ eod-v3 미발화") and "실행 기록 없음" in spy.sent[0]


def test_check_run_alerts_when_unfinished(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    s = RunStore(tmp_path / "runs.sqlite")
    s.start("weekly-v3", at=datetime.now(tz=KST) - timedelta(minutes=40))
    s.close()
    rc, spy = _check(tmp_path, monkeypatch, "weekly-v3")
    assert rc == 0 and spy.sent[0].startswith("⏳ weekly-v3 미완료") and "40분" in spy.sent[0]


def test_check_rounds_registered_and_pure() -> None:
    assert {"check-eod-v3", "check-weekly-v3"} <= set(run.ROUNDS)
    assert not ({"check-eod-v3", "check-weekly-v3"} & run._LLM_ROUNDS)
    assert run.REPORTED_ROUNDS == {"eod-v3", "weekly-v3", "guide-orders"}
