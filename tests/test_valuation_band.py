"""자기 역사 PBR 밴드(policy v2.13, 운영자 결재 2026-09-03) — 회귀 여력 앵커의 순수 산식."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from trading.collectors.fins import FinStore
from trading.valuation.band import (
    MIN_DAYS,
    build_band,
    equity_asof,
    fiscal_year_asof,
    pbr_bands,
)

EQ = {y: 1000.0 for y in range(2019, 2026)}  # 자본총계 1,000 고정 → PBR = 종가 × 주식수 / 1000


def _quotes(
    prices: list[float], shares: float = 100.0, end: date = date(2026, 9, 1),
) -> list[tuple[str, float, float]]:
    """prices[0] = 최신 종가. 달력일 하루 한 개, 최신순."""
    return [((end - timedelta(days=i)).strftime("%Y%m%d"), p, shares) for i, p in enumerate(prices)]


def test_fiscal_year_asof_switches_on_april_1() -> None:
    assert fiscal_year_asof("20260331") == 2024  # 사업보고서 제출 전 — 전전년
    assert fiscal_year_asof("20260401") == 2025  # 제출 기한(3월 말) 경과 — 전년
    assert fiscal_year_asof("20261230") == 2025


def test_equity_asof_falls_back_one_year_then_none() -> None:
    eq = {2025: 300.0, 2023: 100.0}
    assert equity_asof(eq, "20260901") == (2025, 300.0)
    assert equity_asof(eq, "20250901") == (2023, 100.0)  # 2024 결측 → 한 해 폴백
    assert equity_asof(eq, "20240301") is None           # 2022·2021 없음 — 지어내지 않음
    assert equity_asof({2025: 0.0}, "20260901") is None  # 자본잠식 제외


def test_build_band_median_current_and_upside() -> None:
    # 최신 5.0 → PBR 0.5, 이후 10.0×300일(1.0)·20.0×299일(2.0) — 중앙 1.0
    band = build_band("A", _quotes([5.0] + [10.0] * 300 + [20.0] * 299), EQ)
    assert band is not None
    assert band.current == pytest.approx(0.5)
    assert band.median == pytest.approx(1.0)
    assert band.low == pytest.approx(0.5)
    assert band.high == pytest.approx(2.0)
    assert band.n_days == 600
    assert band.upside_pct == pytest.approx(100.0)  # 1.0 / 0.5 − 1
    assert band.last_bas_dt == "20260901"
    assert band.equity_basis == "FY2025 연간 자본총계"


def test_build_band_is_split_safe() -> None:
    # 액면분할: 종가 반토막 + 주식수 2배 → 그날 시총 동일 → PBR 불변
    q = _quotes([5.0] * 300, shares=200.0) + _quotes(
        [10.0] * 300, shares=100.0, end=date(2026, 9, 1) - timedelta(days=300)
    )
    band = build_band("A", q, EQ)
    assert band is not None
    assert band.low == pytest.approx(1.0) and band.high == pytest.approx(1.0)
    assert band.upside_pct == pytest.approx(0.0)


def test_build_band_requires_min_days() -> None:
    assert build_band("A", _quotes([10.0] * (MIN_DAYS - 1)), EQ) is None
    assert build_band("A", _quotes([10.0] * MIN_DAYS), EQ) is not None


def test_build_band_window_caps_history() -> None:
    q = _quotes([1.0] * 500 + [100.0] * 100)
    full = build_band("A", q, EQ, window_days=600)
    capped = build_band("A", q, EQ, window_days=500)
    assert full is not None and capped is not None
    assert full.high == pytest.approx(10.0)   # 창 안: 100 × 100 / 1000
    assert capped.high == pytest.approx(0.1)  # 오래된 100일은 창 밖
    assert capped.n_days == 500


def test_build_band_skips_days_without_equity_and_never_fabricates() -> None:
    # FY2025만 존재 → 2026-04-01 이후(154일)만 표본, 그 전은 FY2024·2023 결측이라 제외
    q = _quotes([10.0] * 600)
    assert build_band("A", q, {2025: 1000.0}) is None  # 154 < MIN_DAYS
    band = build_band("A", q, {2025: 1000.0}, min_days=100)
    assert band is not None and band.n_days == 154


def test_pbr_bands_reads_db_with_ofs_fallback(tmp_path: Path) -> None:
    fins = FinStore(tmp_path / "f.sqlite")
    for year in ("2023", "2024", "2025"):
        # 별도(OFS)만 공시하는 종목(대한약품 유형) — annual_series의 CFS→OFS 폴백
        fins.upsert("000001", year, "11011", [
            {"fs_div": "OFS", "sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "1000"},
        ])
    fins.close()
    mdb = tmp_path / "m.sqlite"
    conn = sqlite3.connect(mdb)
    conn.execute("CREATE TABLE daily_quotes (bas_dt TEXT, srtn_cd TEXT, clpr TEXT, lstg_st_cnt TEXT)")
    conn.executemany(
        "INSERT INTO daily_quotes VALUES (?, '000001', ?, ?)",
        [(d, str(int(c)), str(int(n))) for d, c, n in _quotes([5.0] + [10.0] * 599)],
    )
    conn.commit()
    conn.close()

    bands = pbr_bands(["000001", "999999"], market_db=mdb, fins_db=tmp_path / "f.sqlite")
    band = bands["000001"]
    assert band is not None
    assert band.n_days == 600 and band.current == pytest.approx(0.5)
    assert band.median == pytest.approx(1.0)
    assert bands["999999"] is None  # 시세·재무 없음 — 결측
    assert pbr_bands([], market_db=mdb, fins_db=tmp_path / "f.sqlite") == {}


def test_justified_pbr_and_target_cap() -> None:
    # v2.14(운영자 결재 2026-09-03): 정당 PBR = (ROE − g) ÷ (COE − g), COE 10%·g 1%
    from trading.valuation.band import TargetPbr, justified_pbr, regression_upside, target_pbr

    assert justified_pbr(0.10) == pytest.approx(1.0)          # ROE = COE → 1배
    assert justified_pbr(0.055) == pytest.approx(0.5)         # (5.5 − 1) / 9
    assert justified_pbr(0.005) == 0.0                        # ROE ≤ g → 0(회귀 근거 없음), 음수 금지
    assert justified_pbr(None) is None
    assert justified_pbr(0.10, coe=0.01, g=0.01) is None      # 분모 0 방어

    band = build_band("A", _quotes([5.0] + [10.0] * 599), EQ)  # 현재 0.5 · 중앙 1.0
    assert band is not None
    t_band = target_pbr(band, 0.12)                           # 정당 1.22 > 중앙 1.0 → 밴드 앵커
    assert isinstance(t_band, TargetPbr) and t_band.anchor == "band"
    assert t_band.value == pytest.approx(1.0) and t_band.justified == pytest.approx(11 / 9)
    t_cap = target_pbr(band, 0.055)                           # 정당 0.5 < 중앙 → 캡
    assert t_cap is not None and t_cap.anchor == "justified" and t_cap.value == pytest.approx(0.5)
    assert target_pbr(band, None) is None
    assert regression_upside(band, 0.12) == pytest.approx(100.0)   # 1.0 / 0.5 − 1
    assert regression_upside(band, 0.055) == pytest.approx(0.0)    # 0.5 / 0.5 − 1 — 제값
    assert regression_upside(band, 0.005) == pytest.approx(-100.0) # 정당 0 → 게이트 미달
    assert regression_upside(band, None) is None


def test_equity_asof_with_receipt_dates_removes_lookahead() -> None:
    """P-20 ④: 접수일 다음날 적용 — 12월 결산은 4/1보다 앞당겨지고, 3월 결산은 실제 공개(6월 말)로 늦춰진다."""
    from trading.valuation.band import apply_date

    eq = {2025: 300.0, 2024: 200.0}
    dec = {2025: "20260320", 2024: "20250318"}          # 12월 결산: 3/19 접수 → 3/20 적용
    assert apply_date(2025, dec) == "20260320" and apply_date(2023, dec) == "20240401"  # 접수일 없으면 기본 4/1
    assert equity_asof(eq, "20260325", dec) == (2025, 300.0)   # 기본 규칙(4/1)이라면 2024였을 날
    assert equity_asof(eq, "20260319", dec) == (2024, 200.0)   # 접수 당일은 아직 전년
    mar = {2025: "20260629", 2024: "20250627"}          # 3월 결산(FY2025 = 2026-03-31 마감): 6/28 접수
    assert equity_asof(eq, "20260501", mar) == (2024, 200.0)   # 기본 규칙은 2025를 미리 써서 룩어헤드
    assert equity_asof(eq, "20260629", mar) == (2025, 300.0)
    # 기본(접수일 없음)은 종전과 동일
    assert equity_asof(eq, "20260401") == (2025, 300.0) and equity_asof(eq, "20260331") == (2024, 200.0)


def test_choose_equities_promotes_only_when_all_recent_years_have_owner_equity() -> None:
    from trading.valuation.band import BASIS_OWNER, BASIS_TOTAL, choose_equities

    total = {y: 1000.0 for y in range(2016, 2026)}
    owner_full = {y: 800.0 for y in range(2019, 2026)}            # 최근 7개년 전부
    eqs, label = choose_equities(total, owner_full)
    assert label == BASIS_OWNER and eqs == {y: 800.0 for y in range(2019, 2026)}
    owner_partial = {y: 800.0 for y in range(2021, 2026)}         # 2019·2020 결측 → 승격 안 함(편향 방지)
    eqs2, label2 = choose_equities(total, owner_partial)
    assert label2 == BASIS_TOTAL and eqs2 == total
    assert choose_equities({}, {})[1] == BASIS_TOTAL
    # 자본총계가 5개년만 있으면 그 5개년 기준으로 판정
    eqs3, label3 = choose_equities({y: 1000.0 for y in range(2021, 2026)}, owner_partial)
    assert label3 == BASIS_OWNER and eqs3 == {y: 800.0 for y in range(2021, 2026)}


def test_build_band_basis_label_and_apply_from() -> None:
    from trading.valuation.band import BASIS_OWNER

    q = _quotes([10.0] * 600)
    band = build_band("A", q, EQ, basis_label=BASIS_OWNER, apply_from={2025: "20260320"})
    assert band is not None and band.equity_basis == f"FY2025 {BASIS_OWNER}"
    plain = build_band("A", q, EQ)
    assert plain is not None and plain.equity_basis == "FY2025 연간 자본총계"
