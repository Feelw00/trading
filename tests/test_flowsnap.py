"""flowsnap — 흐름 관측치 스냅샷 조립 (P-6, 결정론). KIS 실시간 + 주입 파일 병합."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading import flowsnap

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 6, 11, 10, 0, tzinfo=KST)


class _FakeKis:
    """KIS 어댑터 스텁 — quote_ccnl/quote_asking_price만(필드는 2026-06-12 관측 발췌)."""

    def __init__(self, ccnl: dict[str, Any], asking: dict[str, Any]) -> None:
        self._ccnl, self._asking = ccnl, asking

    def quote_ccnl(self, srtn_cd: str) -> dict[str, Any]:
        return self._ccnl

    def quote_asking_price(self, srtn_cd: str) -> dict[str, Any]:
        return self._asking


def test_empty_when_no_sources(monkeypatch: Any) -> None:
    snap, notes = flowsnap.build_snapshot(["170920"], kis_client=None, now=NOW)
    assert snap == {"170920": {}}
    assert any("관측치 없음" in n for n in notes)
    assert any("premkt_volume_ratio" in n for n in notes)  # NXT 결측은 항상 정직 표기


def test_injected_file_only(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(flowsnap, "INJECT_DIR", tmp_path)
    (tmp_path / "20260611.json").write_text(
        json.dumps({"170920": {"premkt_volume_ratio": 2.3, "junk": "x"}}), encoding="utf-8"
    )
    snap, _ = flowsnap.build_snapshot(["170920"], kis_client=None, now=NOW)
    assert snap["170920"] == {"premkt_volume_ratio": 2.3}  # 비수치는 버림


def test_kis_realtime_computes_flow_vars(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(flowsnap, "INJECT_DIR", tmp_path)

    # 전일 고가 55000, 현재가 55300 → 회복(1.0). 체결강도 78.03. 호가 매수우위.
    class _Store:
        def nth_recent_date(self, n: int) -> str:
            return "20260605"

        def series_for(self, srtn_cd: str, cutoff: str) -> list[tuple[Any, ...]]:
            # (srtn, name, market, bas_dt, clpr, hipr, ...)
            return [("170920", "엘티씨", "KOSPI", "20260610", "54000", "55000", "", "", "")]

    kis = _FakeKis(
        ccnl={"stck_prpr": "55300", "tday_rltv": "78.03"},
        asking={"total_bidp_rsqn": "13000", "total_askp_rsqn": "10000"},
    )
    snap, _ = flowsnap.build_snapshot(
        ["170920"], kis_client=kis, market_store=_Store(), now=NOW  # type: ignore[arg-type]
    )
    obs = snap["170920"]
    assert obs["execution_strength"] == 78.03
    assert obs["prev_day_high_reclaim"] == 1.0          # 55300 > 55000
    assert abs(obs["orderbook_imbalance"] - (3000 / 23000)) < 1e-9


def test_kis_realtime_overrides_injected(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(flowsnap, "INJECT_DIR", tmp_path)
    (tmp_path / "20260611.json").write_text(
        json.dumps({"170920": {"execution_strength": 50.0, "premkt_volume_ratio": 2.0}}),
        encoding="utf-8",
    )

    class _Store:
        def nth_recent_date(self, n: int) -> str:
            return "20260605"

        def series_for(self, srtn_cd: str, cutoff: str) -> list[tuple[Any, ...]]:
            return [("170920", "x", "KOSPI", "20260610", "54000", "55000", "", "", "")]

    kis = _FakeKis(ccnl={"stck_prpr": "55300", "tday_rltv": "91.8"}, asking={})
    snap, _ = flowsnap.build_snapshot(
        ["170920"], kis_client=kis, market_store=_Store(), now=NOW  # type: ignore[arg-type]
    )
    obs = snap["170920"]
    assert obs["execution_strength"] == 91.8       # KIS 실시간이 주입값 덮어씀
    assert obs["premkt_volume_ratio"] == 2.0       # KIS 미가용분은 주입값 보존
