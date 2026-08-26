"""v0.3 장기 계약(longterm) 스키마 테스트 — 설계서 v0.3 §4."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from trading.contracts.longterm import (
    CyclePhase,
    CycleRecord,
    Governance,
    OrderDraft,
    OrderSide,
    PrimaryAxes,
    ThesisRecord,
    ValuationRecord,
)

KST = ZoneInfo("Asia/Seoul")


def _base(rec_id: str) -> dict[str, Any]:
    ts = datetime(2026, 8, 28, 18, 0, tzinfo=KST)
    return {"id": rec_id, "as_of": ts, "fetched_at": ts, "source": "derived:test"}


# --- ValuationRecord ---


def test_valuation_all_missing_is_valid() -> None:
    """결측 정직 — 지표 전부 None이어도 레코드는 성립한다(0 대체 금지)."""
    rec = ValuationRecord(**_base("val.20260828.005930"), symbol="005930")
    assert rec.pbr is None and rec.per is None and rec.sector_pbr_pct is None
    assert rec.governance == Governance()


def test_valuation_sector_pct_range() -> None:
    with pytest.raises(ValidationError):
        ValuationRecord(**_base("val.x"), symbol="005930", sector_pbr_pct=1.5)


def test_governance_rejects_extra_and_is_factual_only() -> None:
    with pytest.raises(ValidationError):
        Governance(score=0.9)  # type: ignore[call-arg]  # 점수 필드 유입 차단 — 사실만


# --- CycleRecord ---


def test_cycle_unknown_allows_missing_axes() -> None:
    rec = CycleRecord(
        **_base("cyc.20260830.shipping"),
        industry="shipping",
        phase=CyclePhase.UNKNOWN,
        axes_primary=PrimaryAxes(),
    )
    assert rec.temperature is None


def test_cycle_phase_requires_full_primary_axes() -> None:
    with pytest.raises(ValidationError, match="unknown"):
        CycleRecord(
            **_base("cyc.x"),
            industry="shipping",
            phase=CyclePhase.BOTTOMING,
            temperature=20,
            axes_primary=PrimaryAxes(sector_pbr_band_pct=0.1),  # 나머지 축 결측
        )


def test_cycle_phase_requires_temperature() -> None:
    axes = PrimaryAxes(
        sector_pbr_band_pct=0.1, sector_margin_band_pct=0.2, sector_rev_cycle_z=-1.0
    )
    with pytest.raises(ValidationError, match="temperature"):
        CycleRecord(**_base("cyc.x"), industry="shipping", phase=CyclePhase.BOTTOMING, axes_primary=axes)
    ok = CycleRecord(
        **_base("cyc.ok"),
        industry="shipping",
        phase=CyclePhase.BOTTOMING,
        temperature=18,
        axes_primary=axes,
        axes_aux={"freight_index_z": None},
    )
    assert ok.axes_aux["freight_index_z"] is None  # 보강 축 결측은 허용(게이트 아님)


# --- ThesisRecord ---


def test_thesis_horizon_and_invalidation() -> None:
    kwargs = {
        **_base("thesis.20260905.shipping.01"),
        "industry": "shipping",
        "symbol": "011200",
        "thesis": "운임 저점 통과 + 섹터 PBR 하위 — 사이클 회복 편승",
    }
    with pytest.raises(ValidationError):
        ThesisRecord(**kwargs, horizon_months=3, invalidation=["연간 적자 전환"])  # <6개월
    with pytest.raises(ValidationError):
        ThesisRecord(**kwargs, horizon_months=24, invalidation=[])  # 무효화 없음 반려
    rec = ThesisRecord(**kwargs, horizon_months=24, invalidation=["연간 적자 전환"])
    assert rec.review_cadence.value == "quarterly"


# --- OrderDraft (DCA) ---


def _tranches() -> list[dict[str, Any]]:
    return [
        {"seq": 1, "month": "2026-09", "pct": 34},
        {"seq": 2, "month": "2026-10", "pct": 33},
        {"seq": 3, "month": "2026-11", "pct": 33},
    ]


def test_order_draft_dca_ok() -> None:
    rec = OrderDraft.model_validate(
        {
            **_base("order.20260905.011200.dca"),
            "symbol": "011200",
            "side": OrderSide.BUY,
            "target_krw": 1_500_000,
            "tranches": _tranches(),
            "thesis_ref": "thesis.20260905.shipping.01",
        }
    )
    assert rec.status.value == "draft" and rec.created_when_market == "closed"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda ts: ts[0].update(pct=50),                  # 합 ≠ 100
        lambda ts: ts[1].update(seq=3),                   # seq 비연속
        lambda ts: ts[2].update(month="2026-01"),         # 월 역순
        lambda ts: ts[0].update(month="2026-13"),         # 월 패턴 위반
    ],
)
def test_order_draft_tranche_discipline(mutate: Any) -> None:
    tranches = _tranches()
    mutate(tranches)
    with pytest.raises(ValidationError):
        OrderDraft.model_validate(
            {
                **_base("order.x"),
                "symbol": "011200",
                "side": OrderSide.BUY,
                "target_krw": 1_000_000,
                "tranches": tranches,
                "thesis_ref": "thesis.x",
            }
        )
