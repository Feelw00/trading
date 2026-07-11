"""ScoreRecord — R7 주간 평가 산출 (설계서 §3 R7·§7).

채점 없는 멀티 에이전트는 가장 유창한 에이전트를 신뢰하게 만든다 — 이 레코드가
페르소나별 성적표의 단일 원천이다(R4가 "성적 나쁜 페르소나를 더 가혹하게 공격"할 때 입력).
모든 수치는 **코드가 계산**한다(LLM은 해석만). 미성숙·결측은 카운트로 명시(추측 금지).
"""

from pydantic import BaseModel, ConfigDict, Field

from trading.contracts.base import BaseRecord, NonEmptyStr
from trading.contracts.thesis import Persona


class CalibrationBucket(BaseModel):
    """확신도 구간 vs 실현 적중 — 캘리브레이션 측정 단위."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lo: float = Field(ge=0.0, le=1.0)
    hi: float = Field(ge=0.0, le=1.0)
    n: int = Field(ge=0)
    hits: int = Field(ge=0)


class PersonaScore(BaseModel):
    """페르소나 1개의 기간 성적 — 적중률은 성숙(scored) 논제만으로 계산."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    persona: Persona
    n_scored: int = Field(ge=0)       # 시계 도래 + 가격 데이터 확보 → 채점됨
    n_immature: int = Field(ge=0)     # 시계 미도래 — 채점 보류(추측 금지)
    n_flat: int = Field(ge=0)         # 방향 없음 — 적중률 모수에서 제외
    n_no_data: int = Field(ge=0)      # 가격 데이터 결측
    n_hit: int = Field(ge=0)
    hit_rate: float | None = None     # n_scored=0 이면 None(0%로 왜곡 금지)
    calibration: list[CalibrationBucket] = Field(default_factory=list)


class SwingTriggerScore(BaseModel):
    """스윙 기회 트리거 1종의 기간 성적(P-9 ②) — 발화 다음 거래일 진입 → window 거래일 후 종가.

    적중률·평균수익이 임계(SwingConfig) 튜닝의 실측 근거. 미성숙·결측은 채점하지 않는다.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    trigger: NonEmptyStr              # pullback | domain_ignition | catalyst | flow_turn
    window_days: int = Field(ge=1)    # 채점 창(거래일)
    n_scored: int = Field(ge=0)
    n_immature: int = Field(ge=0)
    n_no_data: int = Field(ge=0)
    n_hit: int = Field(ge=0)          # 수익 > 0
    hit_rate: float | None = None     # n_scored=0 이면 None(0%로 왜곡 금지)
    avg_return_pct: float | None = None


class ScoreRecord(BaseRecord):
    """평가 1회(주간) 산출 — append-only(ScoreStore)."""

    period_start: NonEmptyStr         # YYYYMMDD (관측 시작)
    period_end: NonEmptyStr           # YYYYMMDD (관측 끝 = 최신 거래일)
    personas: list[PersonaScore]
    # R4 기각 정확도: 기각 후 무이동=정확 / 생존 후 유의미 이동=정확 (임계는 R7Config)
    r4_refuted_checked: int = Field(ge=0)
    r4_refuted_correct: int = Field(ge=0)
    r4_confirmed_checked: int = Field(ge=0)
    r4_confirmed_correct: int = Field(ge=0)
    # 레짐 모니터(가용 프록시): 전종목 |등락률| 중앙값의 최근/기준 비율 — 입력 갭은 notes
    regime_volatility_ratio: float | None = None
    # 스윙 트리거 적중률(P-9 ②) — 스윙 DB 없던 기간은 빈 리스트(하위호환)
    swing_triggers: list[SwingTriggerScore] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


__all__ = ["CalibrationBucket", "PersonaScore", "ScoreRecord", "SwingTriggerScore"]
