"""레짐 감시(EXEC-7) — 지수 급락 판정·집행 게이트. 7/13 -8.9% 폭락 사후 설계."""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading.executor import ExecPolicy, ExecStore, execute_armed
from trading.regime import Regime, snapshot

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 7, 14, 10, 0, tzinfo=KST)


class _FakeIdxToss:
    def __init__(self, cur: float, prev: float) -> None:
        self._cur, self._prev = cur, prev

    def market_indicator_prices(self, symbols: list[str]) -> list[dict[str, Any]]:
        return [{"symbol": s, "lastPrice": str(self._cur if s == "KOSPI" else 800)} for s in symbols]

    def market_indicator_candles(self, symbol: str, **kw: Any) -> list[dict[str, Any]]:
        base = self._prev if symbol == "KOSPI" else 810
        return [
            {"timestamp": "2026-07-14T00:00:00.000+09:00", "closePrice": str(self._cur)},  # 진행형 오늘 캔들
            {"timestamp": "2026-07-13T00:00:00.000+09:00", "closePrice": str(base)},
        ]


def test_regime_thresholds() -> None:
    assert snapshot(_FakeIdxToss(6900, 6807), now=NOW).regime is Regime.NORMAL   # +1.4%
    assert snapshot(_FakeIdxToss(6600, 6807), now=NOW).regime is Regime.CAUTION  # -3.0%
    assert snapshot(_FakeIdxToss(6400, 6807), now=NOW).regime is Regime.RISK_OFF # -6.0%
    assert snapshot(None, now=NOW).regime is Regime.UNKNOWN


def test_regime_snapshot_lines_carry_change() -> None:
    s = snapshot(_FakeIdxToss(6194.3, 6806.93), now=NOW)  # -9.0%
    assert s.regime is Regime.RISK_OFF
    assert any("KOSPI" in ln and "-9.0%" in ln for ln in s.lines)




def test_regime_cli_unknown_without_toss(capsys: Any, monkeypatch: Any) -> None:
    import trading.collectors.toss as toss_mod
    from trading.regime import main

    monkeypatch.setattr(toss_mod, "client_from_env", lambda: None)
    assert main() == 0
    out = capsys.readouterr().out
    assert "레짐 UNKNOWN" in out and "관측 불가" in out
