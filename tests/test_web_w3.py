"""W3 — 산업 페이지·보고서 탭·자료실 CSV 테스트."""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from trading.web.svg import phase_strip

KST = ZoneInfo("Asia/Seoul")
TS = datetime(2026, 8, 28, 12, 0, tzinfo=KST)


def test_phase_strip_labels() -> None:
    svg = phase_strip([("2024", "bottoming"), ("2025", "overheated"), ("현재", "recovering")])
    assert svg.count("<rect") == 3 and "바닥 통과" in svg and "과열" in svg and "현재" in svg


def _fin_row(nm: str, th: str, sj: str = "IS") -> dict[str, Any]:
    return {"fs_div": "CFS", "sj_div": sj, "account_nm": nm,
            "thstrm_amount": th, "frmtrm_amount": "0", "currency": "KRW"}


def _seed_bands(tmp_path: Path) -> None:
    from trading.collectors.fins import FinStore
    from trading.collectors.market import MarketStore
    from trading.sectors import KRX_SOURCE

    (tmp_path / "data").mkdir(exist_ok=True)
    fins = FinStore(Path("data") / "fins.sqlite")
    market = MarketStore(Path("data") / "market.sqlite")
    for srtn in ("000001", "000002", "000003"):
        for year, rev, op, eq, ni in (
            ("2021", "1000", "100", "500", "80"), ("2022", "1100", "120", "520", "90"),
            ("2023", "900", "40", "530", "30"), ("2024", "950", "60", "540", "50"),
            ("2025", "1200", "150", "560", "110"),
        ):
            fins.upsert(srtn, year, "11011", [
                _fin_row("매출액", rev), _fin_row("영업이익", op),
                _fin_row("당기순이익(손실)", ni), _fin_row("자본총계", eq, sj="BS"),
            ])
        for bas, cap in (("20211230", "1500"), ("20221229", "900"), ("20231228", "700"),
                         ("20241230", "800"), ("20251230", "1100"), ("20260827", "1000")):
            market.upsert([{"basDt": bas, "srtnCd": srtn, "itmsNm": f"철강{srtn[-1]}",
                            "mrktCtg": "KOSPI", "clpr": "100", "mrktTotAmt": cap, "lstgStCnt": "10"}])
    market.upsert_sectors(
        [{"srtn_cd": s, "name": f"철강{s[-1]}", "sectors": ["금속"], "confidence": 1.0}
         for s in ("000001", "000002", "000003")],
        source=KRX_SOURCE, as_of="2026-08-27",
    )
    fins.close()
    market.close()


def test_industry_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed_bands(tmp_path)
    from trading.web.industries import render_industries_list, render_industry_detail

    listing = render_industries_list()
    assert "금속" in listing and "사이클 진폭" in listing
    detail = render_industry_detail("금속")
    assert detail is not None
    assert "국면 타임라인" in detail and "섹터 PBR 밴드" in detail and "구성 종목" in detail
    assert render_industry_detail("없는그룹") is None


def test_reports_tabs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = tmp_path / "reports"
    (base / "dossiers").mkdir(parents=True)
    (base / "weekly-20260828.html").write_text("<html>w</html>", encoding="utf-8")
    (base / "dossiers" / "20260828-011780.md").write_text("# d", encoding="utf-8")
    import trading.web.reports_page as rp

    monkeypatch.setattr(rp, "REPORT_DIR", base)
    body = rp.render_reports(week=None, tab="weekly")
    assert "weekly-20260828.html" in body and "iframe" in body
    body2 = rp.render_reports(week="20260828", tab="dossier")
    assert "20260828-011780.md" in body2


def test_csv_builders(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    from trading.contracts.longterm import CyclePhase, CycleRecord, PrimaryAxes, ValuationRecord
    from trading.cycle.store import CycleStore
    from trading.valuation.store import ValuationStore

    vs = ValuationStore(Path("data") / "valuation.sqlite")
    vs.append(ValuationRecord(
        id="val.20260827.000001", as_of=TS, fetched_at=TS, source="derived:test",
        symbol="000001", sector_krx="금속", pbr=0.5, roe=0.08,
    ))
    vs.close()
    cs = CycleStore(Path("data") / "cycle.sqlite")
    cs.append(CycleRecord(
        id="cyc.20260828.금속", as_of=TS, fetched_at=TS, source="derived:test",
        industry="금속", phase=CyclePhase.UNKNOWN, axes_primary=PrimaryAxes(),
    ))
    cs.close()

    from trading.web.files_page import CSV_BUILDERS

    val_csv = CSV_BUILDERS["valuation.csv"]()
    assert val_csv.splitlines()[0].startswith("symbol,") and "000001" in val_csv
    cyc_csv = CSV_BUILDERS["cycle.csv"]()
    assert "금속" in cyc_csv and "판정 불가" in cyc_csv
    assert CSV_BUILDERS["candidates.csv"]().startswith("symbol,")
