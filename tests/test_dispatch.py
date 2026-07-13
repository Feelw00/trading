"""파이프라인 디스패치 — trading.run ROUNDS 라우팅 + cron 매니페스트 정합성."""

import importlib.util
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import trading.run as run
from trading.run import ROUNDS, main

KST = ZoneInfo("Asia/Seoul")

_EXPECTED = {
    "collect-macro", "collect-market", "collect-news",
    "classify-sectors", "screen", "factpack", "daily-eod",
    "score-news", "verify-catalysts", "reason-theses",
}


def test_rounds_registry_has_expected_keys() -> None:
    assert _EXPECTED <= set(ROUNDS)
    assert all(callable(h) for h in ROUNDS.values())


def test_list_empty_and_unknown() -> None:
    assert main(["--list"]) == 0
    assert main([]) == 2                # 인자 없음
    assert main(["no-such-round"]) == 2  # 미등록


def test_dispatch_routes_to_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(run, "_alert_round_failure", lambda n, d: calls.append((n, d)))
    monkeypatch.setitem(ROUNDS, "_dummy", lambda: 7)
    assert main(["_dummy"]) == 7
    assert calls == [("_dummy", "rc=7")]  # P1 경로 호출 확인(실발송은 conftest가 차단)


def test_daily_eod_chain_stops_on_first_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    def step(tag: str, rc: int):  # type: ignore[no-untyped-def]
        def _f() -> int:
            order.append(tag)
            return rc
        return _f

    monkeypatch.setattr(run, "_collect_market", step("market", 0))
    monkeypatch.setattr(run, "_classify_sectors", step("classify", 2))  # 실패
    monkeypatch.setattr(run, "_screen", step("screen", 0))
    monkeypatch.setattr(run, "_factpack", step("factpack", 0))
    assert run._daily_eod() == 2
    assert order == ["market", "classify"]  # classify 실패 후 중단


def _load_cron_jobs() -> Any:
    path = Path(__file__).resolve().parents[1] / "ops" / "openclaw" / "cron_jobs.py"
    spec = importlib.util.spec_from_file_location("cron_jobs", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cron_manifest_rounds_all_registered() -> None:
    cj = _load_cron_jobs()
    assert len(cj.JOBS) > 0
    for job in cj.JOBS:
        assert job.round in ROUNDS, f"매니페스트 잡 {job.name}의 round '{job.round}'가 ROUNDS에 없음"
        assert job.mode in {"exec", "llm"}


# --- §5 휴면 배선 (CAL-3: 장중 = 정규장 + 애프터마켓 16:00~20:00, 2026-09-14~) ---


def test_llm_round_skipped_during_after_market(monkeypatch: pytest.MonkeyPatch) -> None:
    """cron 경로가 애프터마켓엔 LLM 라운드를 스킵한다(rc=3, P1 알림 없음)."""
    from trading.market_calendar import calendar as mc

    ran: list[str] = []
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(mc, "now_kst", lambda: datetime(2026, 9, 14, 17, 0, tzinfo=KST))
    monkeypatch.setattr(run, "_alert_round_failure", lambda n, d: alerts.append((n, d)))
    def _score_round() -> int:
        ran.append("score")
        return 0

    monkeypatch.setitem(ROUNDS, "score-news", _score_round)

    assert main(["score-news"]) == run.GUARD_SKIP_RC
    assert ran == []      # 핸들러 진입 자체를 막았다
    assert alerts == []   # 정상 스킵 — 실패 알림 아님


def test_pure_code_round_runs_during_after_market(monkeypatch: pytest.MonkeyPatch) -> None:
    """수집·다이제스트 등 순수 코드 라운드는 애프터마켓에도 돈다(뉴스 제때 수집)."""
    from trading.market_calendar import calendar as mc

    monkeypatch.setattr(mc, "now_kst", lambda: datetime(2026, 9, 14, 17, 0, tzinfo=KST))
    monkeypatch.setitem(ROUNDS, "collect-news", lambda: 0)
    assert main(["collect-news"]) == 0


def test_cron_llm_slots_outside_dormant_window() -> None:
    """매니페스트의 LLM 슬롯이 전부 휴면 창 밖인가 — 슬롯을 창 안으로 되돌리면 여기서 깨진다."""
    from trading.market_calendar.calendar import MarketCalendar, in_extended_session

    cal = MarketCalendar()
    monday, saturday = date(2026, 9, 14), date(2026, 9, 19)  # 시행일(월) / 같은 주 토
    for job in _load_cron_jobs().JOBS:
        if job.round not in run._LLM_ROUNDS:
            continue
        minute, hour, _, _, dow = job.cron.split()
        day = saturday if "6" in dow else monday
        slot = datetime(day.year, day.month, day.day, int(hour), int(minute), tzinfo=KST)
        assert not in_extended_session(slot, cal), f"{job.name} 슬롯이 휴면 창 안: {job.cron}"
