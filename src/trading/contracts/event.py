"""EventRecord — R2 정형화 결과. 설계서 §4.

P-4(경제 뉴스 촉매 파이프라인): R2가 채우는 **촉매 스코어·분류 필드**를 확장(PROPOSALS P-4 §1).
사건 레벨엔 **객관 속성만** 둔다 — 방향·시계·확신은 종목·페르소나별이라 R3 ThesisRecord 몫.
(같은 사건이 A엔 호재·B엔 악재이므로 사건 레벨에 방향을 박으면 오류.)
신규 필드는 전부 옵셔널 — 비-촉매 이벤트(기존 R2)는 채우지 않아도 유효(하위호환).
"""

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from trading.contracts.base import BaseRecord, NonEmptyStr
from trading.domains import CatalystType

# 0~1 정규화 스코어(사건 객관 속성). 범위 밖은 ValidationError.
Score = Annotated[float, Field(ge=0.0, le=1.0)]


class EventType(str, Enum):
    POLICY = "policy"
    EARNINGS = "earnings"
    GEOPOLITICS = "geopolitics"
    CORP_ACTION = "corp_action"
    FLOW_ANOMALY = "flow_anomaly"


class Scope(str, Enum):
    """사건 영향 카디널리티 — entity 1차 추정 + R2 LLM 확정(PROPOSALS P-4 §1)."""

    SINGLE_STOCK = "single_stock"
    SECTOR_THEME = "sector_theme"
    BROAD_MARKET = "broad_market"


class AffectedStock(BaseModel):
    """영향종목 + 연결강도. 근거 없는 종목 박제 금지(relevance 0~1, 근거는 evidence)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    srtn_cd: NonEmptyStr
    relevance: Score


class LensVerdict(BaseModel):
    """R4 적대검증 렌즈 1개 결과(PROPOSALS P-4 §4 — 강도/종목연결/시점정합)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lens: NonEmptyStr            # strength | linkage | timing
    survived: bool               # 적대 공격에서 살아남음(refute 실패=촉매 유효)
    reason: NonEmptyStr


class Verification(BaseModel):
    """R4 적대검증 종합 — 선별된 고강도·single_stock 촉매에만 부착(저강도·broad는 None)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verified_by: NonEmptyStr                    # "r4:claude" 등
    confirmed: bool                             # 다수 렌즈 생존 → 생존 촉매
    lens_verdicts: list[LensVerdict] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class EventRecord(BaseRecord):
    type: EventType
    entities: list[NonEmptyStr] = Field(default_factory=list)
    summary_1line: NonEmptyStr
    binary_ref: NonEmptyStr | None = None
    evidence: list[NonEmptyStr] = Field(default_factory=list)
    market_scope: list[NonEmptyStr] = Field(default_factory=list)
    # --- P-4 촉매 스코어·분류 (R2가 채움; 비-촉매 이벤트는 None) ---
    catalyst_type: CatalystType | None = None     # 촉매유형축(섹터축과 직교)
    scope: Scope | None = None                    # 영향 카디널리티
    catalyst_strength: Score | None = None        # 사건 시장 임팩트(종목 독립)
    novelty: Score | None = None                  # 신규성(재탕 디스카운트)
    affected: list[AffectedStock] = Field(default_factory=list)  # 영향종목+연결강도
    # --- P-4 R4 적대검증 (선별된 고강도·single_stock에만; 그 외 None) ---
    verification: Verification | None = None


__all__ = [
    "AffectedStock",
    "EventRecord",
    "EventType",
    "LensVerdict",
    "Score",
    "Scope",
    "Verification",
]
