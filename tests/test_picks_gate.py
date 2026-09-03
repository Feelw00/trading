"""승인 노출 하한(운영자 지시 2026-09-02) — 실현 예상 수익(회귀 여력) < +30%는 승인 종목 제외."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trading.contracts.longterm import CandidateRecord, CyclePhase
from trading.paper import MIN_UPSIDE_PCT
from trading.valuation.band import PbrBand
from trading.web.picks import Pick, approved_picks, regression_upside

TS = datetime(2026, 9, 2, 15, 30, tzinfo=ZoneInfo("Asia/Seoul"))


def _pick(
    symbol: str, verdict: str | None, upside: float | None, risk_adj: float = 10.0, *,
    held: bool = False,
) -> Pick:
    rec = CandidateRecord(
        id=f"cand.20260902.{symbol}", as_of=TS, fetched_at=TS, source="derived:test",
        symbol=symbol, industry="섬유·의류", sector_krx="섬유·의류", phase=CyclePhase.BOTTOMING,
        passed=True, reject_reasons=[], valuation_ref="v", cycle_ref="c",
    )
    return Pick(
        rec=rec, name=symbol, pbr=0.4, upside_pct=upside, div_yield=None, div_streak=0,
        cancelled=False, roe_delta=1.0, yoy_latest=None, yoy_prev=None, splits=0, flags=[],
        risk_adj=risk_adj, verdict=verdict, tier="ledger", held=held,
    )


def test_effective_verdict_blocks_low_upside() -> None:
    assert _pick("A", "approved", 45.0).effective_verdict == "approved"
    assert _pick("A", "approved", MIN_UPSIDE_PCT).effective_verdict == "approved"  # 경계 포함
    assert _pick("A", "approved", 1.0).effective_verdict == "approved_blocked"     # LF 사례
    assert _pick("A", "approved", None).effective_verdict == "approved_blocked"    # 결측=불충족
    assert _pick("A", "hold", 1.0).effective_verdict == "hold"                     # 비승인 불변
    # 보유 중이어도 예외 없음 — 승인 종목 표에서 빠지고 매도 가이드는 /paper가 담당
    assert _pick("A", "approved", 10.0, held=True).effective_verdict == "approved_blocked"


def test_approved_picks_exposes_only_upside_ok_sorted_by_risk_adj() -> None:
    ps = [
        _pick("LOW", "approved", 1.0, 50.0),   # 여력 부족 — 위험조정수익률이 높아도 제외
        _pick("B", "approved", 40.0, 8.0),
        _pick("A", "approved", 90.0, 12.0),
        _pick("H", "hold", 90.0, 99.0),
    ]
    assert [p.name for p in approved_picks(ps)] == ["A", "B"]


def test_regression_upside_formula() -> None:
    # v2.13(운영자 결재 2026-09-03): 앵커 = 자기 역사 5년 PBR 밴드 중앙(와이엔텍 실측치)
    # v2.14(같은 날): 정당 PBR = (ROE − 1%) ÷ (10% − 1%)를 상한으로
    band = PbrBand(
        symbol="067900", current=0.32, median=0.66, low=0.29, high=1.78, n_days=1250,
        last_bas_dt="20260901", equity_basis="FY2025 연간 자본총계",
    )
    # ROE 10.3% → 정당 1.03 > 밴드 0.66 → 캡 미발동, 밴드 중앙 앵커
    assert regression_upside(band, 0.103) == pytest.approx((0.66 / 0.32 - 1) * 100)  # +106%
    # ROE 5% → 정당 0.444 < 밴드 → 캡 발동
    assert regression_upside(band, 0.05) == pytest.approx((0.05 - 0.01) / 0.09 / 0.32 * 100 - 100)
    assert regression_upside(band, None) is None   # ROE 결측 — 캡 검증 불가 → 결측
    assert regression_upside(None, 0.10) is None   # 이력 부족·자본 결측 — 섹터 폴백 없이 결측
