"""밸류에이션 지표 산출 — 순수 함수. 결측=None(0·평균 대체 금지), 연환산 추측 금지.

설계서 v0.3 §3 R2:
- PBR·부채비율은 BS 계정이라 최신 보고서(분기 포함) 기준.
- PER·PSR·ROE는 손익 흐름이라 **연간(11011) IS 기준으로만** — 분기 ×4 연환산은
  계절성·일회성 추측이므로 하지 않는다. 연간 미적재면 None.
- 순손실(net_income<=0)의 PER, 자본잠식(equity<=0)의 PBR/ROE는 무의미 → None.
"""

from collections.abc import Sequence
from dataclasses import dataclass

ANNUAL_REPRT = "11011"


@dataclass(frozen=True)
class Metrics:
    pbr: float | None
    per: float | None
    psr: float | None
    roe: float | None
    debt_ratio: float | None


def derive_metrics(
    *,
    mrkt_tot_amt: float | None,
    equity: float | None,
    liabilities: float | None,
    annual_net_income: float | None,
    annual_revenue: float | None,
    annual_equity: float | None,
    owner_equity: float | None = None,
    annual_owner_net_income: float | None = None,
) -> Metrics:
    """시총 + BS(최신) + 연간 IS → 지표. 분모가 무의미한 경우 None.

    COLLECT-6(운영자 결재 2026-09-01): PBR 분모는 **지배기업 소유주지분 우선**
    (owner_equity, 결측 시 자본총계 폴백). 시총은 지배주주 몫만 반영하므로 비지배지분
    포함 자본총계와 비교하면 지주형 기업의 PBR이 과소된다(실측: KG케미칼 연결 자본
    3.94조 중 비지배 2.94조 — PBR 0.08→0.32). 부채비율·ROE는 자본총계 유지
    (연결 순이익·부채가 비지배 포함이라 내부 정합).

    P-20 ④(2026-09-04): PER 분모도 **지배기업 소유주지분 귀속 당기순이익 우선**(연간 IS,
    `ifrs-full_ProfitLossAttributableToOwnersOfParent`) — 시총(지배주주 몫) ÷ 연결 순이익(비지배 포함)의
    분자·분모 불일치 교정(KG케미칼 PER 1.2 실증). 귀속 순이익이 있고 ≤0이면 PER None(지배주주 기준
    손실 — 연결로 되돌리지 않는다). 미수집이면 연결 순이익 폴백."""
    pbr = None
    pbr_equity = owner_equity if owner_equity is not None else equity
    if mrkt_tot_amt is not None and pbr_equity is not None and pbr_equity > 0:
        pbr = mrkt_tot_amt / pbr_equity

    debt_ratio = None
    if liabilities is not None and equity is not None and equity > 0:
        debt_ratio = liabilities / equity

    per = None
    per_income = annual_owner_net_income if annual_owner_net_income is not None else annual_net_income
    if mrkt_tot_amt is not None and per_income is not None and per_income > 0:
        per = mrkt_tot_amt / per_income

    psr = None
    if mrkt_tot_amt is not None and annual_revenue is not None and annual_revenue > 0:
        psr = mrkt_tot_amt / annual_revenue

    roe = None
    if annual_net_income is not None and annual_equity is not None and annual_equity > 0:
        roe = annual_net_income / annual_equity

    return Metrics(pbr=pbr, per=per, psr=psr, roe=roe, debt_ratio=debt_ratio)


def percentile_rank(values: Sequence[float], x: float) -> float:
    """x의 그룹 내 하위 percentile [0,1] — 낮을수록 그룹에서 싸다. 동값은 0.5 가중.

    빈 그룹은 호출부가 막는다(ValueError — 조용한 0.0 금지).
    """
    if not values:
        raise ValueError("percentile_rank: empty group")
    less = sum(1 for v in values if v < x)
    equal = sum(1 for v in values if v == x)
    return (less + 0.5 * equal) / len(values)


def loss_years(net_incomes: Sequence[float | None], *, window: int = 5) -> tuple[int | None, int]:
    """최근 window년 중 적자 연수. (적자 수 | 관측 0이면 None, 관측 연수) 반환.

    None 항목(해당 연도 계정 결측)은 관측에서 제외 — 결측을 흑자로 세지 않는다.
    """
    recent = list(net_incomes)[:window]
    observed = [v for v in recent if v is not None]
    if not observed:
        return None, 0
    return sum(1 for v in observed if v < 0), len(observed)


__all__ = ["ANNUAL_REPRT", "Metrics", "derive_metrics", "loss_years", "percentile_rank"]
