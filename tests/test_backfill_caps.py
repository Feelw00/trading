"""P-17 ① 시총 스냅샷 소급 — 순수 함수·스토어·밴드 오버레이 테스트."""

from pathlib import Path

import pytest

from trading.backfill_caps import common_shares, parse_count, pick_year_end_bar


def test_parse_count_honest_none() -> None:
    assert parse_count("140,679,337") == 140679337
    assert parse_count("-") is None and parse_count(None) is None and parse_count("abc") is None


def test_pick_year_end_bar_december_only() -> None:
    payload = {"candles": [
        {"timestamp": "2017-01-02T09:00:00+09:00", "closePrice": "999"},   # 다음해 — 제외
        {"timestamp": "2016-12-29T09:00:00+09:00", "closePrice": "36000"},
        {"timestamp": "2016-12-28T09:00:00+09:00", "closePrice": "35500"},
        {"timestamp": "2016-11-30T09:00:00+09:00", "closePrice": "100"},   # 11월 — 제외
    ]}
    assert pick_year_end_bar(payload, "2016") == ("20161229", 36000.0)
    assert pick_year_end_bar({"candles": []}, "2016") is None
    assert pick_year_end_bar({}, "2016") is None


def test_common_shares_guards() -> None:
    rows = [
        {"se": "보통주", "istc_totqy": "140,679,337", "stlm_dt": "2016-12-31"},
        {"se": "우선주", "istc_totqy": "20,513,427", "stlm_dt": "2016-12-31"},
    ]
    assert common_shares(rows, "2016") == 140679337
    # 비 12월 결산은 정직 제외(연말 종가와 시점 불일치)
    assert common_shares([{"se": "보통주", "istc_totqy": "10", "stlm_dt": "2016-03-31"}], "2016") is None
    assert common_shares([], "2016") is None


def test_cap_snapshots_store_and_band_overlay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """스냅샷 적재 → full_year_ends 연말 발견 → 밴드 PBR 축이 소급 연도를 흡수한다."""
    monkeypatch.chdir(tmp_path)
    from trading.collectors.fins import FinStore
    from trading.collectors.market import MarketStore
    from trading.cycle.bands import build_sector_years, full_year_ends
    from trading.sectors import KRX_SOURCE

    (tmp_path / "data").mkdir()
    market = MarketStore(Path("data") / "market.sqlite")
    # daily_quotes: 2020 연말 + 현재
    for dt in ("20201230", "20260828"):
        market.upsert([{ "basDt": dt, "srtnCd": s, "isinCd": "", "itmsNm": n, "mrktCtg": "KOSPI",
                         "clpr": "100", "vs": "0", "fltRt": "0", "mkp": "100", "hipr": "100",
                         "lopr": "100", "trqu": "1", "trPrc": "1", "lstgStCnt": "10",
                         "mrktTotAmt": "1000" } for s, n in (("000001", "가"), ("000002", "나"), ("000003", "다"))])
    market.upsert_sectors(
        [{"srtn_cd": s, "name": s, "sectors": ["철강"], "confidence": 1.0} for s in ("000001", "000002", "000003")],
        source=KRX_SOURCE, as_of="2026-08-28",
    )
    # 소급 스냅샷: 2016 연말(파생 시총 600씩)
    n = market.upsert_cap_snapshots(
        [("20161229", s, 60.0, 10, 600.0, "derived:test") for s in ("000001", "000002", "000003")]
    )
    assert n == 3
    assert market.upsert_cap_snapshots(
        [("20161229", "000001", 60.0, 10, 600.0, "derived:test")]
    ) == 0  # append-only(IGNORE)

    fins = FinStore(Path("data") / "fins.sqlite")
    for s in ("000001", "000002", "000003"):
        for year in ("2016", "2020"):
            fins.upsert(s, year, "11011", [
                {"fs_div": "CFS", "sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "1000"},
            ])

    ye = full_year_ends(market)
    assert ye["2016"] == "20161229" and ye["2020"] == "20201230"  # 스냅샷 연말 발견
    sy = build_sector_years(fins, market, year_end_dates=ye)
    rows = {r.year: r for r in sy["철강"]}
    assert rows["2016"].pbr == pytest.approx(1800 / 3000)  # 스냅샷 시총 합산
    assert rows["2020"].pbr == pytest.approx(3000 / 3000)  # daily_quotes 우선
    fins.close()
    market.close()
