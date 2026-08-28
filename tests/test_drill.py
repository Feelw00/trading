"""수동 드릴 — 단계 레지스트리·빈 스토어 내성·실행 루프 테스트."""

from pathlib import Path

import pytest

from trading import drill
from trading.run import ROUNDS


def test_stages_cover_both_chains_in_order() -> None:
    names = [s.name for s in drill.STAGES]
    assert names == ["market", "sectors", "fins", "flows", "valuation", "cycle", "screen", "digest"]
    chains = {s.name: s.chain for s in drill.STAGES}
    assert chains["market"] == "eod-v3" and chains["digest"] == "weekly-v3"


def test_eod_stages_reuse_cron_handlers() -> None:
    """드릴과 cron이 같은 핸들러를 쓴다 — 경로 이원화 금지."""
    by_name = {s.name: s for s in drill.STAGES}
    assert by_name["market"].runner() is ROUNDS["collect-market"]
    assert by_name["flows"].runner() is ROUNDS["flows-v3"]


def test_status_survives_empty_stores(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """스토어가 없어도 status·metric은 죽지 않고 '비어 있음'을 보고한다."""
    monkeypatch.chdir(tmp_path)
    assert drill.cmd_status() == 0
    assert drill.m_market() == "비어 있음"
    assert drill.m_digest() == "없음"


def test_run_reports_failure_and_unknown_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []

    def _fake(name: str, rc: int) -> object:
        def runner() -> object:
            def inner() -> int:
                ran.append(name)
                return rc

            return inner

        return runner

    fake_stages = (
        drill.Stage("a", "eod-v3", _fake("a", 0), lambda: "m"),  # type: ignore[arg-type]
        drill.Stage("b", "eod-v3", _fake("b", 1), lambda: "m"),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(drill, "STAGES", fake_stages)
    assert drill.cmd_run([]) == 1  # 실패 단계가 있으면 rc=1
    assert ran == ["a", "b"]  # 실패해도 다음 단계 진행(관찰 우선)
    assert drill.cmd_run(["없는단계"]) == 2
