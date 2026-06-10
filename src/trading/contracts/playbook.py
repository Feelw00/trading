"""Playbook — R5 사전 승인 플레이북. 설계서 §4 / §3 R5.

발동(arm)·중단(abort) 조건은 **흐름 변수 화이트리스트만** 허용한다(M3, 설계서 §3 R5):
09~10시 구간의 가격은 f(가치)가 아니라 f(주문 흐름)이므로 가치·내러티브 변수
(밸류에이션·컨센서스·목표가 등)가 조건에 들어오면 **로드 시점에 ValidationError**.

화이트리스트는 설계서 §3 R5의 7종 + §4 예시 키의 1:1 구현:
갭 크기 / 프리마켓 거래량(절대·순위) / 호가 잔량 불균형 / 체결강도 /
동시호가 예상체결가 궤적 / 거래량 클라이맥스 / 신저가 갱신(실패) / 전고점 회복.
새 변수가 필요하면 임의 추가하지 말고 OPEN_QUESTIONS에 등록(절대금지 #1 정신).
"""

from enum import Enum

from pydantic import Field, model_validator

from trading.contracts.base import BaseRecord, NonEmptyStr

# 흐름 변수 화이트리스트 (설계서 §3 R5 — 키는 §4 예시 표기 따름)
FLOW_VARIABLES: frozenset[str] = frozenset(
    {
        "gap_pct",                  # 갭 크기(%)
        "premkt_volume_ratio",      # 프리마켓 거래량 / 평시 비율
        "premkt_volume_rank",       # 프리마켓 거래량 순위
        "orderbook_imbalance",      # 호가 잔량 불균형
        "execution_strength",       # 체결강도
        "auction_projection",       # 동시호가 예상체결가 궤적
        "volume_climax",            # 거래량 클라이맥스
        "new_low_after",            # 신저가 갱신 시각 조건 (§4 abort 예시)
        "new_low_renewal_fail",     # 신저가 갱신 실패
        "prev_day_high_reclaim",    # 전일 고가 회복 (§6 확인 트랜치 조건)
    }
)


class PlaybookState(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class Playbook(BaseRecord):
    thesis_ref: NonEmptyStr
    arm_conditions: dict[NonEmptyStr, NonEmptyStr]
    abort_conditions: dict[NonEmptyStr, NonEmptyStr] = Field(default_factory=dict)
    order_draft_ref: NonEmptyStr
    default: PlaybookState = PlaybookState.INACTIVE

    @model_validator(mode="after")
    def _flow_variables_only(self) -> "Playbook":
        bad = [
            k
            for k in (*self.arm_conditions, *self.abort_conditions)
            if k not in FLOW_VARIABLES
        ]
        if bad:
            raise ValueError(
                f"flow-variable whitelist violation: {sorted(set(bad))} — "
                "arm/abort 조건은 흐름 변수만 허용(설계서 §3 R5, 가치·내러티브 금지)"
            )
        if not self.arm_conditions:
            raise ValueError("arm_conditions 비어 있음 — 발동 조건 없는 플레이북 금지")
        return self


__all__ = ["FLOW_VARIABLES", "Playbook", "PlaybookState"]
