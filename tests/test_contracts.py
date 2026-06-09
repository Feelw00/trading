"""5개 데이터 계약의 스키마 테스트."""

from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError

from trading.contracts.event import AffectedStock, EventRecord, Scope
from trading.contracts.fact import FactRecord
from trading.contracts.order import OrderDraft
from trading.contracts.playbook import Playbook
from trading.contracts.thesis import ThesisRecord


def test_all_contracts_construct(
    fact_kwargs: dict[str, Any],
    event_kwargs: dict[str, Any],
    thesis_kwargs: dict[str, Any],
    playbook_kwargs: dict[str, Any],
    order_kwargs: dict[str, Any],
) -> None:
    assert FactRecord(**fact_kwargs).metric == "kospi_foreign_net_buy_krw"
    assert EventRecord(**event_kwargs).summary_1line.startswith("외국인")
    assert ThesisRecord(**thesis_kwargs).confidence == 0.55
    assert Playbook(**playbook_kwargs).default.value == "inactive"
    order = OrderDraft(**order_kwargs)
    assert len(order.tranches) == 3
    assert order.status.value == "draft"


def test_records_are_frozen(fact_kwargs: dict[str, Any]) -> None:
    fact = FactRecord(**fact_kwargs)
    with pytest.raises(ValidationError):
        setattr(fact, "value", 0.0)


def test_naive_datetime_rejected(fact_kwargs: dict[str, Any]) -> None:
    fact_kwargs["as_of"] = datetime(2026, 6, 8, 18, 0)  # naive — tzinfo 없음
    with pytest.raises(ValidationError):
        FactRecord(**fact_kwargs)


def test_extra_field_rejected(fact_kwargs: dict[str, Any]) -> None:
    fact_kwargs["unexpected"] = "x"
    with pytest.raises(ValidationError):
        FactRecord(**fact_kwargs)


def test_missing_required_field_rejected(fact_kwargs: dict[str, Any]) -> None:
    del fact_kwargs["source"]
    with pytest.raises(ValidationError):
        FactRecord(**fact_kwargs)


# --- P-4 EventRecord 촉매 필드 확장 ---


def test_event_catalyst_fields_default_none(event_kwargs: dict[str, Any]) -> None:
    """비-촉매 이벤트(기존 R2)는 촉매 필드 없이도 유효 — 전부 None/빈 리스트(하위호환)."""
    evt = EventRecord(**event_kwargs)
    assert evt.catalyst_type is None
    assert evt.scope is None
    assert evt.catalyst_strength is None
    assert evt.novelty is None
    assert evt.affected == []


def test_event_catalyst_fields_construct(event_kwargs: dict[str, Any]) -> None:
    evt = EventRecord(
        **event_kwargs,
        catalyst_type="supply_chain",
        scope="sector_theme",
        catalyst_strength=0.8,
        novelty=0.6,
        affected=[{"srtn_cd": "001740", "relevance": 0.9}],
    )
    assert evt.catalyst_type is not None and evt.catalyst_type.value == "supply_chain"
    assert evt.scope is Scope.SECTOR_THEME
    assert evt.catalyst_strength == 0.8
    assert evt.affected[0].srtn_cd == "001740"


@pytest.mark.parametrize("bad", [-0.01, 1.01])
def test_event_score_out_of_range_rejected(event_kwargs: dict[str, Any], bad: float) -> None:
    with pytest.raises(ValidationError):
        EventRecord(**event_kwargs, catalyst_strength=bad)


def test_event_unknown_catalyst_type_rejected(event_kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EventRecord(**event_kwargs, catalyst_type="not_a_real_type")


@pytest.mark.parametrize("bad", [-0.5, 2.0])
def test_affected_relevance_out_of_range_rejected(bad: float) -> None:
    with pytest.raises(ValidationError):
        AffectedStock(srtn_cd="005930", relevance=bad)
