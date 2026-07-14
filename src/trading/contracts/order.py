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


class ExitLevel(BaseModel):
    """계단식 청산 한 단(EXEC-2, 운영자 결정 2026-07-13) — 레벨 + 원 포지션 대비 비중."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    level: float = Field(gt=0)
    pct: int = Field(gt=0, le=100)


class Stop(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: StopType
    level: float | None = None


class EntryBand(BaseModel):
    """진입 유효 가격 범위(EXEC-8, 운영자 결정 2026-07-14) — 코드가 결정론 산출,
    R5는 이 범위 안에서 조이기만 가능(넓히기=계약 위반 폐기)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    low: float = Field(gt=0)    # 하한 — 손절·경고 라인과의 최소 이격(스탑 잡음 진입 차단)
    high: float = Field(gt=0)   # 상한 — 가중 보상 R:R + 익절1 이격(익절 근접 진입 차단)

    @model_validator(mode="after")
    def _ordered(self) -> "EntryBand":
        if self.low >= self.high:
            raise ValueError("entry_band: low < high 여야 한다")
        return self


class MarketState(str, Enum):
    """주문 초안은 장 마감 후에만 생성 — closed만 허용(설계서 §1·§5)."""

    CLOSED = "closed"


class OrderStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    ARMED = "armed"
    EXECUTED = "executed"
    EXPIRED = "expired"
    VETOED = "vetoed"  # 운영자 거부(EXEC-1 자동 승인 체제의 거부권) — 활성 풀 제외


class OrderDraft(BaseRecord):
    symbol: NonEmptyStr
    side: Side
    tranches: list[Tranche] = Field(min_length=1)
    total_size_cap: NonEmptyStr
    stop: Stop | None = None
    time_stop_days: int | None = Field(default=None, gt=0)
    created_when_market: MarketState
    status: OrderStatus = OrderStatus.DRAFT
    # --- 계단식 청산 (EXEC-2, 선택 — R5 분석 지정. 구필드 레코드 호환 위해 기본값) ---
    targets: list[ExitLevel] = Field(default_factory=list)  # 상방 익절 사다리(레벨 오름차순)
    soft_stop: ExitLevel | None = None                      # 경고 축소(하드 스탑 위) — 하드는 stop
    # --- 진입 정책 (EXEC-8, 운영자 결정 2026-07-14. 구필드 레코드 호환 위해 기본값) ---
    max_entries: int = Field(default=1, ge=1, le=2)  # 1=재진입 불허 · 2=1회 재진입(체감 50%)
    entry_band: EntryBand | None = None              # 코드 산출 밴드(∩ R5 조임) — 집행 시 검사

    @model_validator(mode="after")
    def _require_stop_or_time_stop(self) -> "OrderDraft":
        # 가격 손절(stop) 또는 시간 손절(time_stop_days) 중 최소 하나 필수(설계서 §6)
        if self.stop is None and self.time_stop_days is None:
            raise ValueError("OrderDraft requires stop or time_stop_days (둘 다 없음 금지)")
        return self

    @model_validator(mode="after")
    def _exit_ladder_sanity(self) -> "OrderDraft":
        # 익절 사다리: 레벨 순증가 + 비중 합 ≤100 (초과 청산 방지)
        levels = [t.level for t in self.targets]
        if levels != sorted(levels) or len(set(levels)) != len(levels):
            raise ValueError("targets 레벨은 순증가여야 한다")
        if sum(t.pct for t in self.targets) > 100:
            raise ValueError("targets 비중 합이 100%를 초과")
        # 경고 축소: 하드 스탑 위에 있어야 하고(무효화 규율), 전량(100%)은 하드의 몫
        if self.soft_stop is not None:
            if self.soft_stop.pct >= 100:
                raise ValueError("soft_stop은 부분 축소만(<100%) — 전량은 하드 스탑")
            if self.stop is not None and self.stop.level is not None and self.soft_stop.level <= self.stop.level:
                raise ValueError("soft_stop 레벨은 하드 스탑보다 위여야 한다")
        return self


__all__ = [
    "EntryBand",
    "ExitLevel",
    "MarketState",
    "OrderDraft",
    "OrderStatus",
    "OrderType",
    "Side",
    "Stop",
    "StopType",
    "Tranche",
]
