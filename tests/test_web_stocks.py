"""W2 종목 페이지 — 차트 결정론·목록 조립·상세 내성 테스트."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading.contracts.longterm import CandidateRecord, CyclePhase, ValuationRecord
from trading.web.svg import dual_bar_chart, line_chart

KST = ZoneInfo("Asia/Seoul")
TS = datetime(2026, 8, 28, 12, 0, tzinfo=KST)


def test_line_chart_annotations_and_short_series() -> None:
    svg = line_chart([1.0, 3.0, 2.0], start_label="s", end_label="e", fmt=".1f")
    assert "polyline" in svg and "3.0" in svg and "1.0" in svg and "2.0" in svg
    assert line_chart([1.0]) .count("관측 부족") == 1
    assert line_chart([1.0, 3.0, 2.0], start_label="s", end_label="e", fmt=".1f") == svg  # 결정론


def test_dual_bar_negative_and_missing() -> None:
    svg = dual_bar_chart(["2024", "2025"], [100.0, None], [-20.0, 30.0], label_a="매출", label_b="영업이익")
    assert svg.count("<rect") == 3  # 결측 칸은 비움, 적자 바 포함
    assert "매출" in svg and "25" in svg


def _seed(tmp_path: Path) -> None:
    from trading.collectors.market import MarketStore
    from trading.screen.store import CandidateStore
    from trading.sectors import KRX_SOURCE
    from trading.valuation.store import ValuationStore

    (tmp_path / "data").mkdir()
    vs = ValuationStore(Path("data") / "valuation.sqlite")
    for sym, pct in (("011780", 0.11), ("005930", 0.29)):
        vs.append(
            ValuationRecord(
                id=f"val.20260827.{sym}", as_of=TS, fetched_at=TS, source="derived:test",
                symbol=sym, sector_krx="화학" if sym == "011780" else "전기·전자",
                pbr=0.9, per=8.0, roe=0.08, roe_median_5y=0.06, roe_years_observed=5,
                loss_years_5y=0, loss_years_observed=5, debt_ratio=0.5, sector_pbr_pct=pct,
            )
        )
    vs.close()
    cs = CandidateStore(Path("data") / "candidates.sqlite")
    cs.append(
        CandidateRecord(
            id="cand.20260828.011780", as_of=TS, fetched_at=TS, source="derived:test",
            symbol="011780", industry="화학·정유", sector_krx="화학",
            phase=CyclePhase.RECOVERING, passed=True, industry_pbr_pct=0.11,
            valuation_ref="v", cycle_ref="c",
        )
    )
    cs.close()
    ms = MarketStore(Path("data") / "market.sqlite")
    ms.upsert_sectors(
        [{"srtn_cd": "011780", "name": "금호석유화학", "sectors": ["화학"], "confidence": 1.0}],
        source=KRX_SOURCE, as_of="2026-08-28",
    )
    ms.close()


def test_stock_rows_r4_and_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed(tmp_path)
    from trading.web.stocks_data import stock_rows

    rows = stock_rows()
    by = {r.symbol: r for r in rows}
    assert by["011780"].r4 == "통과" and by["011780"].name == "금호석유화학"
    assert by["011780"].industry == "화학·정유"
    assert by["005930"].r4.startswith("평가 대상 아님")
    assert [r.symbol for r in rows] == ["011780", "005930"]  # 산업내 PBR 오름차순


def test_render_pages_survive_sparse_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    _seed(tmp_path)
    from trading.web.stocks import render_detail, render_list

    listing = render_list()
    assert "sortBy" in listing and "금호석유화학" in listing and "통과" in listing
    detail = render_detail("011780")
    assert detail is not None and "밸류에이션" in detail and "R4 판정" in detail
    assert render_detail("999999") is None


def test_list_splits_passed_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """V1 — 통과 종목이 상단 분리 테이블로, 전체 유니버스와 구분된다."""
    monkeypatch.chdir(tmp_path)
    _seed(tmp_path)
    from trading.web.stocks import render_list

    body = render_list()
    assert "R4 통과" in body and "전체 평가 유니버스" in body
    assert body.index("R4 통과") < body.index("전체 평가 유니버스")  # 통과가 위
    assert "passed-row" in body and "ind-filter" in body            # 강조행 + 산업 필터
    assert "data-tip=" in body                                       # 헤더 용어 설명
