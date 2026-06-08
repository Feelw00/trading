"""FactRecord — R0 수집 결과(정량 사실). 설계서 §4."""

from enum import Enum

from pydantic import Field

from trading.contracts.base import BaseRecord, NonEmptyStr


class FactFlag(str, Enum):
    """R1 게이트가 부착하는 플래그(설계서 §3 R1)."""

    STALE = "stale"
    CONFLICT = "conflict"


class FactRecord(BaseRecord):
    metric: NonEmptyStr
    value: float
    flags: list[FactFlag] = Field(default_factory=list)


__all__ = ["FactFlag", "FactRecord"]
