"""섹터 히스토리 밴드(R3 1차 축 재료) — 합산 규율·무결성 가드 테스트."""

from pathlib import Path
from typing import Any

from trading.collectors.fins import FinStore
from trading.collectors.market import MarketStore
from trading.cycle import band_positions, build_sector_years
from trading.sectors import KRX_SOURCE


def _fin_row(nm: str, th: str, sj: str = "IS") -> dict[str, Any]:
    return {"fs_div": "CFS", "sj_div": sj, "account_nm": nm,
            "thstrm_amount": th, "frmtrm_amount": "0", "currency": "KRW"}


def _annual(fins: FinStore, srtn: str, year: str, *, rev: str, op: str, eq: str, ni: str) -> None:
    fins.upsert(srtn, year, "11011", [
        _fin_row("매출액", rev),
        _fin_row("영업이익", op),
        _fin_row("당기순이익(손실)", ni),
        _fin_row("자본총계", eq, sj="BS"),
    ])


def _quote(srtn: str, bas_dt: str, cap: str) -> dict[str, Any]:
    return {"basDt": bas_dt, "srtnCd": srtn, "itmsNm": srtn, "mrktCtg": "KOSPI",
            "clpr": "100", "mrktTotAmt": cap, "lstgStCnt": "10"}


def _setup(tmp_path: Path) -> tuple[FinStore, MarketStore]:
    fins = FinStore(tmp_path / "f.sqlite")
    market = MarketStore(tmp_path / "m.sqlite")
    # 철강 3종목(그룹 충족) — 2023 고평가(합산 PBR 2.0), 2024 저평가(1.0), 현재 최저(0.5)
    for srtn in ("000001", "000002", "000003"):
        _annual(fins, srtn, "2023", rev="1000", op="100", eq="500", ni="80")
        _annual(fins, srtn, "2024", rev="900", op="45", eq="500", ni="40")
        market.upsert([
            _quote(srtn, "20231228", "1000"),
            _quote(srtn, "20241230", "500"),
            _quote(srtn, "20260827", "250"),
        ])
    # 해운 2종목 — MIN_COMPOSITION(3) 미달 → 축 전부 None이어야
    for srtn in ("000008", "000009"):
        _annual(fins, srtn, "2024", rev="100", op="10", eq="50", ni="5")
        market.upsert([_quote(srtn, "20241230", "100"), _quote(srtn, "20260827", "80")])
    items = [
        {"srtn_cd": s, "name": s, "sectors": ["철강"], "confidence": 1.0}
        for s in ("000001", "000002", "000003")
    ] + [
        {"srtn_cd": s, "name": s, "sectors": ["해운"], "confidence": 1.0}
        for s in ("000008", "000009")
    ]
    market.upsert_sectors(items, source=KRX_SOURCE, as_of="2026-08-27")
    return fins, market


_DATES = {"2023": "20231228", "2024": "20241230", "current": "20260827"}


def test_sector_years_pair_matched_aggregation(tmp_path: Path) -> None:
    fins, market = _setup(tmp_path)
    years = build_sector_years(fins, market, year_end_dates=_DATES)
    steel = {r.year: r for r in years["철강"]}
    assert steel["2023"].pbr == 2.0 and steel["2023"].n_pbr == 3   # 3000/1500
    assert steel["2024"].pbr == 1.0
    assert steel["current"].pbr == 0.5                              # 750/1500(최신 연간 자본)
    assert steel["2023"].margin == 0.1 and steel["2024"].margin == 0.05
    assert steel["2024"].revenue == 2700.0
    fins.close()
    market.close()


def test_min_composition_guard(tmp_path: Path) -> None:
    fins, market = _setup(tmp_path)
    years = build_sector_years(fins, market, year_end_dates=_DATES)
    shipping = {r.year: r for r in years["해운"]}
    assert shipping["2024"].pbr is None and shipping["2024"].n_pbr == 2  # 표본 2 < 3
    assert shipping["2024"].margin is None and shipping["current"].pbr is None
    fins.close()
    market.close()


def test_band_positions_current_is_bottom(tmp_path: Path) -> None:
    fins, market = _setup(tmp_path)
    positions = band_positions(build_sector_years(fins, market, year_end_dates=_DATES))
    steel = next(p for p in positions if p.sector == "철강")
    # 현재 0.5는 [2.0, 1.0, 0.5] 히스토리에서 최하단
    assert steel.pbr_band_pct is not None and steel.pbr_band_pct < 0.2
    assert steel.pbr_years == 3
    # 마진 최신(2024, 0.05)은 [0.1, 0.05] 중 하단
    assert steel.margin_band_pct is not None and steel.margin_band_pct <= 0.5
    assert steel.rev_yoy is not None and abs(steel.rev_yoy - (-0.1)) < 1e-9
    shipping = next(p for p in positions if p.sector == "해운")
    assert shipping.pbr_band_pct is None  # 표본 미달 — 결측 정직
    fins.close()
    market.close()


def test_extra_groups_curated(tmp_path: Path) -> None:
    """큐레이션 그룹(policy ① 조선) — 명시 종목 리스트로 별도 밴드, 재무 미적재 종목은 제외."""
    fins, market = _setup(tmp_path)
    years = build_sector_years(
        fins, market, year_end_dates=_DATES,
        extra_groups={"조선(큐레이션)": ["000001", "000002", "000008", "999999"]},  # 999999 미적재
    )
    curated = {r.year: r for r in years["조선(큐레이션)"]}
    # 000001·000002(철강 소속이어도 명시 리스트 우선)·000008 = 3종목 → MIN 충족
    assert curated["2024"].n_fin == 3
    assert "철강" in years  # 파생 그룹은 그대로 유지
    fins.close()
    market.close()


def test_annual_series_extraction(tmp_path: Path) -> None:
    fins = FinStore(tmp_path / "f.sqlite")
    _annual(fins, "000001", "2024", rev="900", op="45", eq="500", ni="-40")
    series = dict(fins.annual_series("000001"))
    assert series["2024"] == {"revenue": 900.0, "op_income": 45.0, "equity": 500.0,
                              "net_income": -40.0, "net_interest": None,
                              "owner_equity": None, "owner_net_income": None}  # P-20 ④ 키(미수집 None)
    assert fins.annual_net_incomes("000001") == [("2024", -40.0)]
    fins.close()
