"""weekly-v3 체인 — P-19 ⑥ KRX 박제분 주 1회 재시도(운영자 위임 2026-09-07, policy v2.21 ②): best-effort 첫 단계."""

from collections.abc import Sequence

import pytest

import trading.run as run
import trading.sectors as sectors


def test_sectors_retry_step_passes_flag_and_is_best_effort(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[list[str]] = []

    def _main(argv: Sequence[str] | None = None) -> int:
        calls.append(list(argv or []))
        return 0

    monkeypatch.setattr(sectors, "main", _main)
    assert run._sectors_retry_pinned_v3() == 0
    assert calls == [["--retry-pinned"]]

    def _boom(argv: Sequence[str] | None = None) -> int:
        raise RuntimeError("KIS down")

    monkeypatch.setattr(sectors, "main", _boom)
    assert run._sectors_retry_pinned_v3() == 0  # 실패해도 주간 계측을 막지 않는다
    assert "재시도 실패" in capsys.readouterr().err


def test_weekly_chain_runs_sectors_retry_first(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    def _retry() -> int:
        order.append("sectors-retry")
        return 0

    def _owner_equity() -> int:
        order.append("owner-equity")
        return 1  # 여기서 체인 중단

    monkeypatch.setattr(run, "_sectors_retry_pinned_v3", _retry)
    monkeypatch.setattr(run, "_owner_equity_v3", _owner_equity)
    monkeypatch.setattr(run, "_alert_round_failure", lambda name, detail: None)
    assert run._weekly_v3() == 1
    assert order == ["sectors-retry", "owner-equity"]
