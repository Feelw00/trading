"""ThesisRecord — R3 페르소나 분석 결과. 무효화(invalidation) 필수. 설계서 §4."""

from enum import Enum

from pydantic import Field

from trading.contracts.base import BaseRecord, NonEmptyStr


class Persona(str, Enum):
    SUPPLY = "supply"
    CYCLE = "cycle"
    MACRO = "macro"


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class ThesisRecord(BaseRecord):
    persona: Persona
    thesis: NonEmptyStr
    direction: Direction
    instrument_class: NonEmptyStr
    trigger: NonEmptyStr
    # 무효화 조건 필수 — 비거나 공백이면 ValidationError
    # (NonEmptyStr = strip_whitespace + min_length=1)
    invalidation: NonEmptyStr
    horizon_days: int = Field(gt=0)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[NonEmptyStr] = Field(default_factory=list)


__all__ = ["Direction", "Persona", "ThesisRecord"]
