"""OrderDraft — R5 주문 초안. 규율 파라미터 내장, 시장가 부재, 청산 우선. 설계서 §4·§6.

제약(M1):
- 시장가(market) 주문 타입은 스키마에 아예 없음(절대 금지).
- ``created_when_market`` 은 ``closed`` 만 허용(장중 생성 금지).
- ``stop`` 과 ``time_stop_days`` 가 둘 다 없으면 ValidationError(손절 2종 중 최소 1).
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trading.contracts.base import BaseRecord, NonEmptyStr


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    """지정가만 존재. 시장가(market)는 의도적으로 부재 — 절대 금지(CLAUDE.md, 설계서 §6)."""

    LIMIT = "limit"


class Tranche(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: NonEmptyStr
    pct_of_plan: int = Field(gt=0, le=100)
    order_type: OrderType | None = None
    condition: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _require_type_or_condition(self) -> "Tranche":
        # 지정가 트랜치는 order_type, 확인(confirmation) 트랜치는 condition — 둘 다 없으면 무효
        if self.order_type is None and self.condition is None:
            raise ValueError("tranche must have order_type or condition")
        return self


class StopType(str, Enum):
    """증권사 조건부 주문만. 시장가 청산은 부재(설계서 §6)."""

    CONDITIONAL_ORDER_AT_BROKER = "conditional_order_at_broker"


class Stop(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: StopType
    level: float | None = None


class MarketState(str, Enum):
    """주문 초안은 장 마감 후에만 생성 — closed만 허용(설계서 §1·§5)."""

    CLOSED = "closed"


class OrderStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    ARMED = "armed"
    EXECUTED = "executed"
    EXPIRED = "expired"


class OrderDraft(BaseRecord):
    symbol: NonEmptyStr
    side: Side
    tranches: list[Tranche] = Field(min_length=1)
    total_size_cap: NonEmptyStr
    stop: Stop | None = None
    time_stop_days: int | None = Field(default=None, gt=0)
    created_when_market: MarketState
    status: OrderStatus = OrderStatus.DRAFT

    @model_validator(mode="after")
    def _require_stop_or_time_stop(self) -> "OrderDraft":
        # 가격 손절(stop) 또는 시간 손절(time_stop_days) 중 최소 하나 필수(설계서 §6)
        if self.stop is None and self.time_stop_days is None:
            raise ValueError("OrderDraft requires stop or time_stop_days (둘 다 없음 금지)")
        return self


__all__ = [
    "MarketState",
    "OrderDraft",
    "OrderStatus",
    "OrderType",
    "Side",
    "Stop",
    "StopType",
    "Tranche",
]
