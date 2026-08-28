"""R4 판정 규칙 — 순수 함수. 사람·LLM 재량 없음(헌장 2), 탈락 사유 전수 반환.

발동 존(§3 R4): 화이트리스트 산업 ∧ R3 국면 ∈ {bottoming, recovering} ∧ 구조적 사양 아님.
존 안에서: ① 가치(산업 내 PBR percentile) ② 생존력(적자 연수·부채비율 — 금융업 면제)
③ 하드 탈락(PBR 산출 불가 = 자본잠식·시총 결측). 데이터 미확보 필터(감사의견·관리종목·
환원 가점·수급 네거티브)는 **unapplied로 명시** — 침묵 생략 금지.

``PROPOSED_R4``는 **policy-v1.2로 결재됨**(v1.1: 2026-08-27 기본 임계 / v1.2: 2026-08-28
가치 함정 방어 ROE 필터 — docs/POLICY_PARAMS.md §5). 실집행 연결은 Phase 4(§6 결재 +
운영자 전환 결정) 후. 개정은 R7+결재로만.
"""

from dataclasses import dataclass

from trading.contracts.longterm import CyclePhase, ValuationRecord, phase_ko

ENTRY_PHASES = frozenset({CyclePhase.BOTTOMING, CyclePhase.RECOVERING})

# 부채비율 필터 면제 산업(레버리지가 사업 모델인 금융업 — 부채비율 무의미)
DEBT_EXEMPT_INDUSTRIES = frozenset({"은행", "증권"})

UNAPPLIED_V1 = [
    "감사의견·관리종목 필터(소스 미확보 — PIVOT-3)",
    "환원·거버넌스 가점(PIVOT-3 수집 전)",
    "수급 네거티브 스크린(60~120거래일 창 축적 전)",
]


@dataclass(frozen=True)
class ScreenParams:
    max_industry_pbr_pct: float = 0.40   # 산업 내 PBR 하위 percentile 임계(이하 = 통과)
    max_loss_years_5y: int = 1           # 최근 5년 적자 연수 상한
    min_loss_observed: int = 3           # 적자 판정 최소 관측 연수(미만=관측 부족 탈락)
    max_debt_ratio: float = 2.0          # 부채비율 상한(금융업 면제)
    # v1.2 가치 함정 방어(운영자 2026-08-28, LG화학 심사 패킷 사례):
    # ① 최신 연간 ROE > 0 (= 연간 흑자 = PER 성립 — 종목 레벨 회복 확인)
    # ② 사이클 관통 수익성: 관측 5년 ROE 중앙값 ≥ 3% (만성 저수익 방어,
    #    실측 캘리브레이션: 212종목 중 56개가 이 선 아래·LG화학 중앙값 +5.0%는 ①로 탈락)
    min_roe_median_5y: float = 0.03


PROPOSED_R4 = ScreenParams()


def evaluate(
    val: ValuationRecord,
    *,
    industry: str,
    phase: CyclePhase,
    secular_decline: bool | None,
    industry_pbr_pct: float | None,
    params: ScreenParams,
) -> tuple[bool, list[str]]:
    """(통과 여부, 탈락 사유 전수). 발동 존 검사는 호출측(run)이 산업 단위로 수행."""
    reasons: list[str] = []

    if phase not in ENTRY_PHASES:
        reasons.append(f"발동 존 아님(국면={phase_ko(phase)})")
    if secular_decline is True:
        reasons.append("구조적 사양 산업(매출 장기 추세 하향)")

    if val.pbr is None:
        reasons.append("PBR 산출 불가(자본잠식 또는 시총·자본 결측) — 하드 탈락")
    if industry_pbr_pct is None:
        reasons.append("산업 내 PBR 상대 위치 미산출(표본 부족)")
    elif industry_pbr_pct > params.max_industry_pbr_pct:
        reasons.append(
            f"가치 미달(산업 내 PBR 하위 {industry_pbr_pct:.0%} > {params.max_industry_pbr_pct:.0%})"
        )

    # 가치 함정 방어(v1.2) — 낮은 PBR이 수익성 훼손의 반영일 가능성 차단
    if val.roe is None:
        reasons.append("최신 연간 수익성 미산출(연간 재무 없음)")
    elif val.roe <= 0:
        reasons.append(f"가치 함정 방어({val.roe:+.1%} — 최신 연간 ROE ≤ 0, 연간 흑자 요구)")
    if val.roe_median_5y is None or (val.roe_years_observed or 0) < params.min_loss_observed:
        reasons.append(
            f"수익성 관측 부족(연간 ROE {val.roe_years_observed or 0}년 < {params.min_loss_observed}년)"
        )
    elif val.roe_median_5y < params.min_roe_median_5y:
        reasons.append(
            f"만성 저수익({val.roe_median_5y:+.1%} — 5년 ROE 중앙값 < {params.min_roe_median_5y:.0%})"
        )

    if val.loss_years_5y is None or (val.loss_years_observed or 0) < params.min_loss_observed:
        reasons.append(
            f"흑자 유지력 관측 부족(연간 재무 {val.loss_years_observed or 0}년 < {params.min_loss_observed}년)"
        )
    elif val.loss_years_5y > params.max_loss_years_5y:
        reasons.append(
            f"적자 상한 초과({val.loss_years_5y}년/최근 5년 > {params.max_loss_years_5y}년)"
        )

    if industry not in DEBT_EXEMPT_INDUSTRIES:
        if val.debt_ratio is None:
            reasons.append("부채비율 미산출(자본·부채 결측)")
        elif val.debt_ratio > params.max_debt_ratio:
            reasons.append(f"부채비율 상한 초과({val.debt_ratio:.2f} > {params.max_debt_ratio})")

    return (not reasons, reasons)


__all__ = [
    "DEBT_EXEMPT_INDUSTRIES",
    "ENTRY_PHASES",
    "PROPOSED_R4",
    "ScreenParams",
    "UNAPPLIED_V1",
    "evaluate",
]
