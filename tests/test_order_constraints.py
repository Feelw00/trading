"""OrderDraft 규율 제약(설계서 §6): 손절 2종 중 최소 1, 장 마감 후 생성, 시장가 부재."""

from typing import Any

import pytest
from pydantic import ValidationError

from trading.contracts.order import OrderDraft, OrderType


def test_valid_order(order_kwargs: dict[str, Any]) -> None:
    assert OrderDraft(**order_kwargs).symbol == "000660"


def test_stop_and_time_stop_both_missing_rejected(order_kwargs: dict[str, Any]) -> None:
    order_kwargs["stop"] = None
    order_kwargs["time_stop_days"] = None
    with pytest.raises(ValidationError):
        OrderDraft(**order_kwargs)


def test_only_time_stop_is_ok(order_kwargs: dict[str, Any]) -> None:
    order_kwargs["stop"] = None  # time_stop_days=5 남음 → 유효
    assert OrderDraft(**order_kwargs).time_stop_days == 5


def test_created_when_market_must_be_closed(order_kwargs: dict[str, Any]) -> None:
    order_kwargs["created_when_market"] = "open"
    with pytest.raises(ValidationError):
        OrderDraft(**order_kwargs)


def test_no_market_order_type() -> None:
    # 시장가 타입은 스키마에 아예 없다(절대 금지)
    assert {t.value for t in OrderType} == {"limit"}
