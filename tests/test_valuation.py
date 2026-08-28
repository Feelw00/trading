"""R2 밸류에이션 — 지표 산출·섹터 상대·조립 테스트 (순수 코드, 외부 호출 없음)."""

from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from trading.collectors.fins import FinStore
from trading.collectors.market import MarketStore
from trading.sectors import KRX_SOURCE
from trading.valuation import build_valuation_records, derive_metrics, loss_years, percentile_rank
from trading.valuation.store import ValuationStore

KST = ZoneInfo("Asia/Seoul")


# --- metrics ---


def test_derive_metrics_annual_basis() -> None:
    m = derive_metrics(
        mrkt_tot_amt=1_000.0,
        equity=500.0,
        liabilities=250.0,
        annual_net_income=100.0,
        annual_revenue=2_000.0,
        annual_equity=400.0,
    )
    assert m.pbr == 2.0 and m.per == 10.0 and m.psr == 0.5
    assert m.roe == 0.25 and m.debt_ratio == 0.5


def test_derive_metrics_honest_none() -> None:
    # 순손실 PER·자본잠식 PBR/ROE는 무의미 → None (0 폴백 금지)
    m = derive_metrics(
        mrkt_tot_amt=1_000.0,
        equity=-10.0,
        liabilities=250.0,
        annual_net_income=-5.0,
        annual_revenue=0.0,
        annual_equity=-10.0,
    )
    assert m.pbr is None and m.per is None and m.psr is None
    assert m.roe is None and m.debt_ratio is None


def test_derive_metrics_quarterly_only_no_per() -> None:
    # 연간 IS 미적재 → PER/PSR/ROE 미산출(연환산 추측 금지), BS 기반 PBR은 산출
    m = derive_metrics(
        mrkt_tot_amt=1_000.0,
        equity=500.0,
        liabilities=100.0,
        annual_net_income=None,
        annual_revenue=None,
        annual_equity=None,
    )
    assert m.pbr == 2.0 and m.per is None and m.psr is None and m.roe is None


def test_percentile_rank() -> None:
    group = [0.5, 1.0, 1.5, 2.0]
    assert percentile_rank(group, 0.5) == pytest.approx(0.125)  # 최저 = 하위
    assert percentile_rank(group, 2.0) == pytest.approx(0.875)
    with pytest.raises(ValueError):
        percentile_rank([], 1.0)


def test_loss_years_none_not_counted_as_profit() -> None:
    losses, observed = loss_years([100.0, None, -5.0, -1.0, 50.0, -99.0])  # 최근 5년 창
    assert (losses, observed) == (2, 4)  # None은 관측 제외, 창 밖 -99는 미포함
    assert loss_years([None, None]) == (None, 0)


# --- build (fins × market × sectors) ---


def _fin_row(nm: str, th: str, fr: str = "0", sj: str = "IS") -> dict[str, Any]:
    return {"fs_div": "CFS", "sj_div": sj, "account_nm": nm,
            "thstrm_amount": th, "frmtrm_amount": fr, "currency": "KRW"}


def _quote(srtn: str, cap: str) -> dict[str, Any]:
    return {"basDt": "20260827", "srtnCd": srtn, "itmsNm": f"종목{srtn}",
            "mrktCtg": "KOSPI", "clpr": "10000", "mrktTotAmt": cap, "lstgStCnt": "1000"}


def _load_symbol(fins: FinStore, srtn: str, *, equity: str, net_income: str, revenue: str) -> None:
    fins.upsert(srtn, "2025", "11011", [
        _fin_row("매출액", revenue),
        _fin_row("영업이익", "50"),
        _fin_row("당기순이익(손실)", net_income),
        _fin_row("부채총계", "300", sj="BS"),
        _fin_row("자본총계", equity, sj="BS"),
    ])


def test_build_valuation_records(tmp_path: Path) -> None:
    fins = FinStore(tmp_path / "f.sqlite")
    market = MarketStore(tmp_path / "m.sqlite")
    # 같은 섹터 3종목(그룹 최소 충족) + 시세 없는 1종목(스킵돼야)
    for srtn, eq in (("000001", "1000"), ("000002", "2000"), ("000003", "4000")):
        _load_symbol(fins, srtn, equity=eq, net_income="100", revenue="1000")
        market.upsert([_quote(srtn, "2000")])
    _load_symbol(fins, "000009", equity="1000", net_income="100", revenue="1000")  # 시세 없음
    market.upsert_sectors(
        [{"srtn_cd": s, "name": s, "sectors": ["철강·금속"], "confidence": 1.0}
         for s in ("000001", "000002", "000003")],
        source=KRX_SOURCE, as_of="2026-08-27",
    )

    now = datetime(2026, 8, 28, 9, 0, tzinfo=KST)
    records, summary = build_valuation_records(fins, market, now=now)

    assert summary.total == 3  # 시세 없는 000009 스킵(지어내지 않음)
    by_sym = {r.symbol: r for r in records}
    r1 = by_sym["000001"]
    assert r1.pbr == 2.0 and r1.per == 20.0 and r1.psr == 2.0  # cap 2000 / (ni 100 · rev 1000)
    assert r1.fin_basis == "BS 2025/11011"
    assert r1.sector_krx == "철강·금속"
    # PBR: 2.0(000001) > 1.0(000002) > 0.5(000003) — 하위 percentile 정렬 확인
    assert by_sym["000003"].sector_pbr_pct is not None
    assert by_sym["000003"].sector_pbr_pct < by_sym["000001"].sector_pbr_pct  # type: ignore[operator]
    assert r1.loss_years_5y == 0 and r1.loss_years_observed == 1
    assert r1.roe_median_5y == 0.1 and r1.roe_years_observed == 1  # ni 100 / eq 1000
    fins.close()
    market.close()


def test_build_small_sector_group_no_percentile(tmp_path: Path) -> None:
    fins = FinStore(tmp_path / "f.sqlite")
    market = MarketStore(tmp_path / "m.sqlite")
    _load_symbol(fins, "000001", equity="1000", net_income="100", revenue="1000")
    market.upsert([_quote("000001", "2000")])
    market.upsert_sectors(
        [{"srtn_cd": "000001", "name": "x", "sectors": ["해운"], "confidence": 1.0}],
        source=KRX_SOURCE, as_of="2026-08-27",
    )
    records, _ = build_valuation_records(fins, market)
    assert records[0].sector_pbr_pct is None  # 그룹 1 — 상대 위치 무의미 = 결측
    fins.close()
    market.close()


def test_valuation_store_append_only(tmp_path: Path) -> None:
    fins = FinStore(tmp_path / "f.sqlite")
    market = MarketStore(tmp_path / "m.sqlite")
    _load_symbol(fins, "000001", equity="1000", net_income="100", revenue="1000")
    market.upsert([_quote("000001", "2000")])
    records, _ = build_valuation_records(fins, market)
    store = ValuationStore(tmp_path / "v.sqlite")
    assert store.append(records[0]) == 1
    assert store.append(records[0]) == 2  # 같은 id 재적재 = 새 버전(UPDATE 없음)
    latest = store.latest(records[0].id)
    assert latest is not None and latest.symbol == "000001"
    assert store.count() == 1
    store.close()
    fins.close()
    market.close()
