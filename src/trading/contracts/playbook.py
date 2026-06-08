"""Playbook — R5 사전 승인 플레이북. 설계서 §4.

발동 조건(arm_conditions)은 흐름 변수만 허용 — 화이트리스트 강제는 M3.
M1은 형태(스키마)만 정의한다.
"""

from enum import Enum

from pydantic import Field

from trading.contracts.base import BaseRecord, NonEmptyStr


class PlaybookState(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Playbook(BaseRecord):
    thesis_ref: NonEmptyStr
    arm_conditions: dict[NonEmptyStr, NonEmptyStr]
    abort_conditions: dict[NonEmptyStr, NonEmptyStr] = Field(default_factory=dict)
    order_draft_ref: NonEmptyStr
    default: PlaybookState = PlaybookState.INACTIVE


__all__ = ["Playbook", "PlaybookState"]
