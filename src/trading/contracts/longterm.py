"""v0.3 장기 사이클·가치 계약 — ValuationRecord / CycleRecord / ThesisRecord / OrderDraft(DCA).

설계서 v0.3 §4. v0.2 계약(thesis.py·order.py 등)은 동결 저널 호환을 위해 그대로 두고,
v0.3 계약은 이 모듈 네임스페이스로 분리한다(`trading.contracts.longterm.ThesisRecord`).

원칙(설계서 §1·§4):
- 결측은 결측(None)으로 — 0·평균 대체 금지.
- 관측 불가능한 무효화 조건 금지 — invalidation은 최소 1개, 텍스트는 관측 가능 조건 서술.
- 1차 축이 결측인 섹터의 국면은 unknown 강제(§3 R3) — 스키마가 직접 막는다.
"""

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from trading.contracts.base import BaseRecord, NonEmptyStr

YearMonth = Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]


class Governance(BaseModel):
    """환원·거버넌스 경향성(PIVOT-7 ④) — 단년 환원액 금지, track record만.

    None = 미관측(수집 전). 감점 폭·시효는 정책 파라미터(부록 B) — 여기는 사실만 담는다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    div_years_paid_10y: int | None = Field(default=None, ge=0, le=10)
    retire_events_10y: int | None = Field(default=None, ge=0)
    policy_disclosed: bool | None = None
    # 물적분할 후 자회사 상장 이력 — 관측 가능한 주주가치 훼손 전력(공시 조합 판정)
    spinoff_relist: bool | None = None
    # 소각 없는 자사주 처분 이력 — 오버행 전력
    treasury_disposal: bool | None = None


class ValuationRecord(BaseRecord):
    """R2 밸류에이션·환원 계산기 산출 — 전부 결정론(순수 코드)."""

    symbol: NonEmptyStr
    sector_krx: str | None = None
    pbr: float | None = None
    # PER/PSR/ROE는 연간(11011) IS 기준으로만 산출 — 분기 연환산 추측 금지(§3 R2)
    per: float | None = None
    psr: float | None = None
    roe: float | None = None
    # 사이클 관통 수익성(v1.2 가치 함정 방어) — 관측 연간(최대 5년) ROE 중앙값
    roe_median_5y: float | None = None
    roe_years_observed: int | None = Field(default=None, ge=0, le=5)
    debt_ratio: float | None = None
    interest_coverage: float | None = None  # 이자비용 소스 확보 전까지 항상 None(PIVOT-3)
    loss_years_5y: int | None = Field(default=None, ge=0, le=5)
    loss_years_observed: int | None = Field(default=None, ge=0, le=5)
    sector_pbr_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    governance: Governance = Governance()
    fin_basis: str | None = None  # 어느 보고서 기준인지 예: "2025/11011·2026/11013"
    evidence: list[NonEmptyStr] = Field(default_factory=list)


class CyclePhase(str, Enum):
    BOTTOMING = "bottoming"
    RECOVERING = "recovering"
    OVERHEATED = "overheated"
    DECLINING = "declining"
    UNKNOWN = "unknown"


# 표시용 한글 라벨(운영자 2026-08-28: DB는 영어 enum, 보고서는 한글) — 렌더러 단일 소스
PHASE_KO: dict[CyclePhase, str] = {
    CyclePhase.BOTTOMING: "바닥 통과",
    CyclePhase.RECOVERING: "회복",
    CyclePhase.OVERHEATED: "과열",
    CyclePhase.DECLINING: "하강",
    CyclePhase.UNKNOWN: "판정 불가",
}

PHASE_LEGEND_KO = (
    "국면 기준: 바닥 통과=밴드 하단(30% 이하)+개선 시작, 회복=중간 구간+개선, "
    "과열=밴드 상단(75% 이상), 하강=개선 없음(하단 포함), 판정 불가=관측 부족"
)


def phase_ko(phase: CyclePhase) -> str:
    return PHASE_KO[phase]


class PrimaryAxes(BaseModel):
    """자체 히스토리 밴드 1차 축(PIVOT-7 ②) — 편입 판정의 기준."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sector_pbr_band_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    sector_margin_band_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    sector_rev_cycle_z: float | None = None


class CycleRecord(BaseRecord):
    """R3 산업 사이클 온도계 산출(주간)."""

    industry: NonEmptyStr
    phase: CyclePhase
    temperature: int | None = Field(default=None, ge=0, le=100)
    axes_primary: PrimaryAxes
    axes_aux: dict[str, float | None] = Field(default_factory=dict)
    secular_decline: bool | None = None
    evidence: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unknown_when_unobserved(self) -> "CycleRecord":
        # §3 R3: 1차 축 결측 섹터는 unknown(편입 불가) — 부분 관측으로 국면을 지어내지 않는다
        primary = (
            self.axes_primary.sector_pbr_band_pct,
            self.axes_primary.sector_margin_band_pct,
            self.axes_primary.sector_rev_cycle_z,
        )
        if self.phase is not CyclePhase.UNKNOWN:
            if any(v is None for v in primary):
                raise ValueError("primary axes incomplete — phase must be 'unknown'")
            if self.temperature is None:
                raise ValueError("temperature required unless phase='unknown'")
        return self


class ReviewCadence(str, Enum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


class ThesisRecord(BaseRecord):
    """v0.3 논제 — 시계는 개월, 무효화는 관측 가능 조건 목록(최소 1개)."""

    industry: NonEmptyStr
    symbol: NonEmptyStr
    thesis: NonEmptyStr
    horizon_months: int = Field(ge=6, le=36)  # §1 운영 전제: 논제당 6~36개월
    invalidation: list[NonEmptyStr] = Field(min_length=1)
    review_cadence: ReviewCadence = ReviewCadence.QUARTERLY
    evidence: list[NonEmptyStr] = Field(default_factory=list)


class CandidateRecord(BaseRecord):
    """R4 스크리너 산출 — 편입·제외는 여기서만 결정되며 **탈락 사유를 전수 박제**한다(§3 R4).

    `unapplied`: 데이터 미확보로 이번 판정에 적용되지 않은 필터 목록 — 침묵 생략 금지,
    "통과"가 "전 필터 통과"로 오독되지 않게 한다.
    """

    symbol: NonEmptyStr
    industry: NonEmptyStr            # 화이트리스트 산업명(policy WHITELIST 키)
    sector_krx: str | None = None
    phase: CyclePhase                # 판정 시점 산업 국면(R3)
    passed: bool
    reject_reasons: list[NonEmptyStr] = Field(default_factory=list)
    industry_pbr_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    unapplied: list[NonEmptyStr] = Field(default_factory=list)
    valuation_ref: NonEmptyStr
    cycle_ref: NonEmptyStr

    @model_validator(mode="after")
    def _passed_xor_reasons(self) -> "CandidateRecord":
        if self.passed and self.reject_reasons:
            raise ValueError("passed=True이면 reject_reasons는 비어야 한다")
        if not self.passed and not self.reject_reasons:
            raise ValueError("탈락은 사유 없이 박제할 수 없다(§3 R4 — 전수 사유)")
        return self


class DossierRecord(BaseRecord):
    """R4.5 심사 패킷 — LLM 서술의 감사 기록(§3 R4.5).

    **어떤 수치·점수도 산출하지 않으며 파이프라인의 어떤 판정에도 입력되지 않는다.**
    bear_case는 의무(확증 편향을 시스템이 막는다). 서술의 사실 근거는 ``fact_card``
    (결정론 조립 정량 카드)에 한정되며 카드 밖 수치 주장은 프롬프트로 금지된다.
    """

    candidate_ref: NonEmptyStr
    symbol: NonEmptyStr
    industry: NonEmptyStr
    model: NonEmptyStr                                    # 서술 모델(감사용)
    bull_case: list[NonEmptyStr] = Field(min_length=1)
    bear_case: list[NonEmptyStr] = Field(min_length=1)    # 의무 — 비면 ValidationError
    risks: list[NonEmptyStr] = Field(default_factory=list)
    fact_card: NonEmptyStr                                # 주입한 정량 카드 원문(귀속 근거)


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    ACTIVE = "active"
    DONE = "done"
    VETOED = "vetoed"
    INVALIDATED = "invalidated"


class DcaTranche(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int = Field(ge=1)
    month: YearMonth
    pct: int = Field(gt=0, le=100)


class OrderDraft(BaseRecord):
    """v0.3 DCA 주문 초안 — 지정가 전용(절대금지 #3), 스탑 필드 없음(청산=논제 붕괴 경로)."""

    symbol: NonEmptyStr
    side: OrderSide
    target_krw: int = Field(gt=0)
    tranches: list[DcaTranche] = Field(min_length=1)
    limit_rule: NonEmptyStr = "prev_close_band"  # 지정가 산정 규칙(결정론) 참조 키
    thesis_ref: NonEmptyStr
    created_when_market: Literal["closed"] = "closed"
    status: OrderStatus = OrderStatus.DRAFT

    @model_validator(mode="after")
    def _tranche_discipline(self) -> "OrderDraft":
        pct_sum = sum(t.pct for t in self.tranches)
        if pct_sum != 100:
            raise ValueError(f"tranche pct must sum to 100, got {pct_sum}")
        seqs = [t.seq for t in self.tranches]
        if seqs != list(range(1, len(seqs) + 1)):
            raise ValueError("tranche seq must be consecutive from 1")
        months = [t.month for t in self.tranches]
        if months != sorted(months):
            raise ValueError("tranche months must be non-decreasing")
        return self


__all__ = [
    "CandidateRecord",
    "CyclePhase",
    "CycleRecord",
    "DcaTranche",
    "DossierRecord",
    "Governance",
    "OrderDraft",
    "OrderSide",
    "OrderStatus",
    "PHASE_KO",
    "PHASE_LEGEND_KO",
    "PrimaryAxes",
    "phase_ko",
    "ReviewCadence",
    "ThesisRecord",
    "ValuationRecord",
    "YearMonth",
]
