"""5개 데이터 계약의 스키마 테스트."""

from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError

from trading.contracts.event import EventRecord
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
