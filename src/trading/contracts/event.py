"""EventRecord — R2 정형화 결과. 설계서 §4."""

from enum import Enum

from pydantic import Field

from trading.contracts.base import BaseRecord, NonEmptyStr


class EventType(str, Enum):
    POLICY = "policy"
    EARNINGS = "earnings"
    GEOPOLITICS = "geopolitics"
    CORP_ACTION = "corp_action"
    FLOW_ANOMALY = "flow_anomaly"


class EventRecord(BaseRecord):
    type: EventType
    entities: list[NonEmptyStr] = Field(default_factory=list)
    summary_1line: NonEmptyStr
    binary_ref: NonEmptyStr | None = None
    evidence: list[NonEmptyStr] = Field(default_factory=list)
    market_scope: list[NonEmptyStr] = Field(default_factory=list)


__all__ = ["EventRecord", "EventType"]
