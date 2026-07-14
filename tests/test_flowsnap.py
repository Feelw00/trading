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
    monkeypatch.setattr(flowsnap, "_toss_from_env", lambda: None)
    snap, notes = flowsnap.build_snapshot(["170920"], kis_client=None, now=NOW)
    assert snap == {"170920": {}}
    assert any("관측치 없음" in n for n in notes)
    assert any("premkt_volume_ratio" in n for n in notes)  # NXT 결측은 항상 정직 표기


def test_injected_file_only(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(flowsnap, "_toss_from_env", lambda: None)
    monkeypatch.setattr(flowsnap, "INJECT_DIR", tmp_path)
    (tmp_path / "20260611.json").write_text(
        json.dumps({"170920": {"premkt_volume_ratio": 2.3, "junk": "x"}}), encoding="utf-8"
    )
    snap, _ = flowsnap.build_snapshot(["170920"], kis_client=None, now=NOW)
    assert snap["170920"] == {"premkt_volume_ratio": 2.3}  # 비수치는 버림


def test_kis_realtime_computes_flow_vars(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(flowsnap, "_toss_from_env", lambda: None)
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
    assert abs(obs["prev_day_high_recovery"] - 55300 / 55000) < 1e-3  # 등급형 병행
    assert abs(obs["orderbook_imbalance"] - (3000 / 23000)) < 1e-9


def test_prev_high_prefers_kis_daily_over_stale_db(tmp_path: Path, monkeypatch: Any) -> None:
    """KIS 일자별(진짜 전일 고가)이 1차 — 2026-07-14 뉴파워 오발동(T-2 고가 기준) 재발 방지."""
    monkeypatch.setattr(flowsnap, "_toss_from_env", lambda: None)
    monkeypatch.setattr(flowsnap, "INJECT_DIR", tmp_path)

    class _Store:
        def nth_recent_date(self, n: int) -> str:
            return "20260605"

        def series_for(self, srtn_cd: str, cutoff: str) -> list[tuple[Any, ...]]:
            # DB는 T-2(6/9)까지만 적재(낡음) — 고가 50000
            return [("170920", "x", "KOSPI", "20260609", "49000", "50000", "", "", "")]

    class _KisDaily(_FakeKis):
        def daily_prices(self, srtn_cd: str) -> list[dict[str, Any]]:
            return [
                {"stck_bsop_date": "20260611", "stck_hgpr": "56000"},  # 당일 진행분 — 건너뜀
                {"stck_bsop_date": "20260610", "stck_hgpr": "55500"},  # 진짜 전일
            ]

    kis = _KisDaily(ccnl={"stck_prpr": "55300", "tday_rltv": "91.8"}, asking={})
    snap, _ = flowsnap.build_snapshot(
        ["170920"], kis_client=kis, market_store=_Store(), now=NOW  # type: ignore[arg-type]
    )
    obs = snap["170920"]
    assert obs["prev_day_high_reclaim"] == 0.0                       # 55300 < 55500(진짜 전일)
    assert abs(obs["prev_day_high_recovery"] - 55300 / 55500) < 1e-3


def test_prev_high_stale_db_yields_no_observation(tmp_path: Path, monkeypatch: Any) -> None:
    """KIS 일자별 부재 + DB 최신이 직전 거래일이 아니면 미관측(보수) — 낡은 기준 판정 금지."""
    monkeypatch.setattr(flowsnap, "_toss_from_env", lambda: None)
    monkeypatch.setattr(flowsnap, "INJECT_DIR", tmp_path)

    class _Store:
        def nth_recent_date(self, n: int) -> str:
            return "20260605"

        def series_for(self, srtn_cd: str, cutoff: str) -> list[tuple[Any, ...]]:
            # NOW=6/11(목)의 직전 거래일은 6/10 — DB는 6/9까지만(낡음)
            return [("170920", "x", "KOSPI", "20260609", "49000", "50000", "", "", "")]

    kis = _FakeKis(ccnl={"stck_prpr": "55300", "tday_rltv": "91.8"}, asking={})
    snap, _ = flowsnap.build_snapshot(
        ["170920"], kis_client=kis, market_store=_Store(), now=NOW  # type: ignore[arg-type]
    )
    obs = snap["170920"]
    assert "prev_day_high_reclaim" not in obs
    assert "prev_day_high_recovery" not in obs
    assert obs["execution_strength"] == 91.8  # 다른 관측은 정상


def test_kis_realtime_overrides_injected(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(flowsnap, "_toss_from_env", lambda: None)
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

class _FakeToss:
    """rankings_trading_amount만 흉내 — P-11 Stage B 섹터 점화."""

    def __init__(self, symbols: list[str]) -> None:
        self._symbols = symbols

    def rankings_trading_amount(self, **kw: Any) -> list[dict[str, Any]]:
        return [{"rank": i + 1, "symbol": s} for i, s in enumerate(self._symbols)]


class _SectorStore:
    """sector_map_multi 지원 스토어 페이크(시세 계열은 미사용 경로)."""

    def __init__(self, secmap: dict[str, list[str]]) -> None:
        self._m = secmap

    def sector_map_multi(self, sources: Any) -> dict[str, list[str]]:
        return dict(self._m)

    def nth_recent_date(self, n: int) -> str:
        return "20260605"

    def series_for(self, srtn_cd: str, cutoff: str) -> list[tuple[Any, ...]]:
        return []


def test_sector_ignition_from_rankings(monkeypatch: Any) -> None:
    # 상위 100 중 '기계·장비' 5종목 → 점화 / '금융' 1종목 → 비점화
    secmap = {f"00000{i}": ["기계·장비"] for i in range(5)}
    secmap["105560"] = ["금융"]
    secmap["170920"] = ["기계·장비"]   # 감시 대상 — 점화 섹터 소속
    toss = _FakeToss([f"00000{i}" for i in range(5)] + ["105560"])
    snap, notes = flowsnap.build_snapshot(
        ["170920", "105560", "999999"], kis_client=None,
        market_store=_SectorStore(secmap), toss_client=toss, now=NOW,  # type: ignore[arg-type]
    )
    assert snap["170920"]["sector_ignition"] == 1.0   # 소속 섹터 점화
    assert snap["105560"]["sector_ignition"] == 0.0   # 소속 섹터 비점화(1종목)
    assert "sector_ignition" not in snap["999999"]    # 미태깅 — 관측치 없음(보수)
    assert not any("sector_ignition" in n and "미가용" in n for n in notes)


def test_sector_ignition_absent_when_toss_fails(monkeypatch: Any) -> None:
    class _DownToss:
        def rankings_trading_amount(self, **kw: Any) -> list[dict[str, Any]]:
            raise OSError("down")

    snap, notes = flowsnap.build_snapshot(
        ["170920"], kis_client=None,
        market_store=_SectorStore({"170920": ["기계·장비"]}), toss_client=_DownToss(), now=NOW,  # type: ignore[arg-type]
    )
    assert "sector_ignition" not in snap["170920"]   # 실패 = 결측(지어내지 않음)
    assert any("sector_ignition" in n for n in notes)
