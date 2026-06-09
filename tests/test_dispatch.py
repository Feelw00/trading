"""파이프라인 디스패치 — trading.run ROUNDS 라우팅 + cron 매니페스트 정합성."""

import importlib.util
from pathlib import Path
from typing import Any

import pytest

import trading.run as run
from trading.run import ROUNDS, main

_EXPECTED = {
    "collect-macro", "collect-market", "collect-news",
    "classify-sectors", "screen", "factpack", "daily-eod",
}


def test_rounds_registry_has_expected_keys() -> None:
    assert _EXPECTED <= set(ROUNDS)
    assert all(callable(h) for h in ROUNDS.values())


def test_list_empty_and_unknown() -> None:
    assert main(["--list"]) == 0
    assert main([]) == 2                # 인자 없음
    assert main(["no-such-round"]) == 2  # 미등록


def test_dispatch_routes_to_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(ROUNDS, "_dummy", lambda: 7)
    assert main(["_dummy"]) == 7


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
