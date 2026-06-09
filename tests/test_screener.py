"""스크리너 — 게이트(보통주·거래대금)·신호 랭킹·아티팩트 가드·하락장 필터 검증."""

from dataclasses import replace
from pathlib import Path
from typing import Any

from trading.collectors.market import MarketStore
from trading.screener import ScreenConfig, screen

DAYS = ["20260601", "20260602", "20260603", "20260604", "20260605"]

_TUNE = ScreenConfig(
    min_tr_prc=1e8, min_mrkt_cap=0.0, lookback_surge=2, mom_short=2, mom_long=3,
    lookback_high=5, top_n=10,
)


def _q(
    d: str, code: str, name: str, clpr: float, hipr: float, trpc: float, mcap: float,
    shares: float | None = None,
) -> dict[str, Any]:
    row = {
        "basDt": d, "srtnCd": code, "itmsNm": name, "mrktCtg": "KOSPI",
        "clpr": str(clpr), "hipr": str(hipr), "trPrc": str(trpc), "mrktTotAmt": str(mcap),
    }
    if shares is not None:
        row["lstgStCnt"] = str(shares)
    return row


def _seed(store: MarketStore) -> None:
    rows: list[dict[str, Any]] = []
    # STRONG: 우상향 + 마지막날 거래대금 급증 + 신고가
    strong = [100, 110, 120, 130, 140]
    strong_tr = [1e9, 1e9, 1e9, 1e9, 5e9]
    # WEAK: 우하향, 거래대금 평탄
    weak = [100, 99, 98, 97, 96]
    # SMALL: STRONG과 동일 패턴이나 거래대금 게이트 미달
    for i, d in enumerate(DAYS):
        rows.append(_q(d, "000010", "스트롱", strong[i], strong[i], strong_tr[i], 1e12))
        rows.append(_q(d, "000020", "위크", weak[i], 100, 1e9, 1e12))
        rows.append(_q(d, "000030", "스몰", strong[i], strong[i], 1e6, 1e12))  # 거래대금 1e6 << 게이트
        rows.append(_q(d, "000015", "스트롱우", strong[i], strong[i], strong_tr[i], 1e12))  # 우선주코드
    store.upsert(rows)


def test_screener_gate_and_ranking(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    _seed(store)
    cfg = ScreenConfig(
        min_tr_prc=1e8, min_mrkt_cap=0.0, lookback_surge=2, mom_short=2, mom_long=3,
        lookback_high=5, top_n=10,
    )
    res = screen(store, cfg)
    store.close()

    codes = [c.srtn_cd for c in res.candidates]
    assert "000030" not in codes  # 거래대금 게이트 탈락
    assert "000015" not in codes  # 보통주 아님(末 '5') 탈락
    assert res.universe == 2  # STRONG, WEAK 만 통과
    assert res.candidates[0].srtn_cd == "000010"  # 모든 신호 우위 → 1위
    top = res.candidates[0].signals
    assert top.tr_value_surge > 1.5  # 5e9 / ((1e9+5e9)/2)=1.67
    assert top.high_proximity == 1.0  # 종가=최고가


def test_screener_excludes_share_count_artifact(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    strong = [100, 110, 120, 130, 140]
    tr = [1e9, 1e9, 1e9, 1e9, 5e9]
    rows: list[dict[str, Any]] = []
    for i, d in enumerate(DAYS):
        rows.append(_q(d, "000010", "정상", strong[i], strong[i], tr[i], 1e12, shares=1000))
        # 분할주: 같은 강한 패턴이나 마지막날 상장주식수 5배 급변(=가격 시리즈 불연속)
        rows.append(_q(d, "000040", "분할주", strong[i], strong[i], tr[i], 1e12, shares=1000 if i < 4 else 5000))
    store.upsert(rows)
    on = {c.srtn_cd for c in screen(store, _TUNE).candidates}
    off = {c.srtn_cd for c in screen(store, replace(_TUNE, exclude_adjustment_artifacts=False)).candidates}
    store.close()
    assert "000040" in off               # 가드 끄면 통과
    assert "000040" not in on            # 가드 켜면(기본) 아티팩트 제외
    assert "000010" in on and "000010" in off  # 주식수 일정한 정상주는 항상 유지


def test_screener_bear_market_absolute_floor(tmp_path: Path) -> None:
    store = MarketStore(tmp_path / "m.sqlite")
    _seed(store)  # 000010 우상향, 000020 우하향(96/100-1=-4%)
    base = {c.srtn_cd for c in screen(store, _TUNE).candidates}
    floored = {c.srtn_cd for c in screen(store, replace(_TUNE, min_mom_long=0.0)).candidates}
    store.close()
    assert "000020" in base                    # 필터 없으면 하락주도 통과(횡단면 랭크)
    assert "000020" not in floored             # 절대필터 켜면 장기수익률<0 제외
    assert "000010" in floored                 # 상승주는 유지
