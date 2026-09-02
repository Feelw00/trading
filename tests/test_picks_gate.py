"""승인 노출 하한(운영자 지시 2026-09-02) — 실현 예상 수익(회귀 여력) < +30%는 승인 종목 제외."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from trading.contracts.longterm import CandidateRecord, CyclePhase, ValuationRecord
from trading.paper import MIN_UPSIDE_PCT
from trading.web.picks import Pick, approved_picks, regression_upside, sector_median_pbr

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
    def _v(sym: str, pbr: float, sector: str = "X") -> ValuationRecord:
        return ValuationRecord(
            id=f"val.{sym}", as_of=TS, fetched_at=TS, source="derived:test", symbol=sym,
            sector_krx=sector, pbr=pbr,
        )

    latest = [_v(f"S{i}", p) for i, p in enumerate([0.4, 0.5, 0.6, 0.8, 1.0])] + [_v("T1", 0.3, "Y")]
    med = sector_median_pbr(latest)
    assert med == {"X": 0.6}                                             # 표본 <5 섹터 제외
    assert regression_upside(latest[0], med) == pytest.approx(50.0)     # 0.6/0.4 − 1
    assert regression_upside(latest[-1], med) is None                   # 섹터 중앙 없음
    assert regression_upside(None, med) is None
