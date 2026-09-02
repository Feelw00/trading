"""R4 판정 규칙 — 순수 함수. 사람·LLM 재량 없음(헌장 2), 탈락 사유 전수 반환.

**P-18(2026-08-31 운영자 결재) — 우선순위 반전**: 게이트는 가치·건전성만.
① 가치(병행 — 결재 ③): 산업 내 PBR percentile ≤ 40% **또는** 시장 전체 percentile ≤ 40%
   (둘 다 박제, 하나만 충족해도 가치 성립 — 혼성 산업·소표본 왜곡 보정).
② 생존력: 적자 연수·부채비율(금융업 면제) ③ 가치 함정 방어(v1.2 ROE 필터)
④ 구조적 사양 가드(유지 — 사이클 이론이 아니라 가치 함정 방어 그 자체).
⑤ 하드 탈락(PBR 산출 불가 = 자본잠식·시총 결측).

**사이클은 도구(게이트 아님)**: 발동 존 검사 폐지. 과열 산업의 통과분은 탈락시키지 않고
호출측(run)이 `cycle_caution` 플래그만 부착(결재 ① — "플래그만 달고 편입").
데이터 미확보 필터(감사의견·관리종목·환원 가점·수급 네거티브)는 **unapplied로 명시**.

``PROPOSED_R4``: v1.1(2026-08-27 기본 임계)·v1.2(2026-08-28 가치 함정 방어)·
v1.6(2026-08-31 P-18 — 존 게이트 제거·시장 percentile 병행)·
v1.7(2026-09-01 운영자 결재 — 최신 ROE 하한 0→1%: PBR=ROE×PER 항등식상 deep PBR
+명목 흑자(0<ROE<1%)는 암묵 PER 폭주 경로. 실측: 통과 545 중 22종(4.0%) 차단.
docs/POLICY_PARAMS.md §5). 실집행 연결은 Phase 4(§6 결재 + 운영자 전환 결정) 후.
개정은 R7+결재로만.
"""

from dataclasses import dataclass

from trading.contracts.longterm import CyclePhase, ValuationRecord

# P-18 이후 발동 존은 게이트가 아니라 도구(우선순위·정렬)의 기준으로만 쓰인다.
ENTRY_PHASES = frozenset({CyclePhase.BOTTOMING, CyclePhase.RECOVERING})

# 부채비율 필터 면제 산업(레버리지가 사업 모델인 금융업 — 부채비율 무의미).
# P-18 전 상장 확장으로 KRX 버킷 산업명(금융·보험)도 포함.
DEBT_EXEMPT_INDUSTRIES = frozenset({"은행", "증권", "금융", "보험"})

UNAPPLIED_V1 = [
    "감사의견·관리종목 필터(소스 미확보 — PIVOT-3)",
    "환원·거버넌스 가점(PIVOT-3 수집 전)",
    "수급 네거티브 스크린(60~120거래일 창 축적 전)",
]


@dataclass(frozen=True)
class ScreenParams:
    max_industry_pbr_pct: float = 0.40   # 산업 내 PBR 하위 percentile 임계(이하 = 가치 성립)
    # P-18 결재 ③ "병행" — 시장 전체 percentile 임계. 산업 내와 동일 40%를 미러링
    # (새 임계 발명 아님 — 기결재 40%의 대칭 적용. 재캘리브레이션은 R7+결재).
    max_market_pbr_pct: float = 0.40
    max_loss_years_5y: int = 1           # 최근 5년 적자 연수 상한
    min_loss_observed: int = 3           # 적자 판정 최소 관측 연수(미만=관측 부족 탈락)
    max_debt_ratio: float = 2.0          # 부채비율 상한(금융업 면제)
    # v1.2 가치 함정 방어(운영자 2026-08-28, LG화학 심사 패킷 사례):
    # ① 최신 연간 ROE 하한 (v1.7: 0 초과 → 1% — 명목 흑자는 PER 성립으로 보지 않음.
    #    사이클 저점 "정상 적자"는 이미 ROE≤0에서 걸리므로 저점 전략과 충돌 없음)
    # ② 사이클 관통 수익성: 관측 5년 ROE 중앙값 ≥ 3% (만성 저수익 방어)
    min_roe_latest: float = 0.01
    min_roe_median_5y: float = 0.03


PROPOSED_R4 = ScreenParams()


def evaluate(
    val: ValuationRecord,
    *,
    industry: str,
    secular_decline: bool | None,
    industry_pbr_pct: float | None,
    market_pbr_pct: float | None,
    params: ScreenParams,
) -> tuple[bool, list[str]]:
    """(통과 여부, 탈락 사유 전수). P-18: 국면은 게이트가 아니므로 입력받지 않는다."""
    reasons: list[str] = []

    if secular_decline is True:
        reasons.append("구조적 사양 산업(매출 장기 추세 하향)")

    if val.pbr is None:
        reasons.append("PBR 산출 불가(자본잠식 또는 시총·자본 결측) — 하드 탈락")
    else:
        # 가치 병행 기준(결재 ③): 산업 내 OR 시장 전체 — 하나만 충족해도 성립
        industry_ok = industry_pbr_pct is not None and industry_pbr_pct <= params.max_industry_pbr_pct
        market_ok = market_pbr_pct is not None and market_pbr_pct <= params.max_market_pbr_pct
        if industry_pbr_pct is None and market_pbr_pct is None:
            reasons.append("PBR 상대 위치 미산출(산업·시장 표본 모두 부족)")
        elif not (industry_ok or market_ok):
            ind_s = f"{industry_pbr_pct:.0%}" if industry_pbr_pct is not None else "미산출"
            mkt_s = f"{market_pbr_pct:.0%}" if market_pbr_pct is not None else "미산출"
            reasons.append(
                f"가치 미달(산업 내 {ind_s} · 시장 전체 {mkt_s} — 병행 기준 산업 "
                f"{params.max_industry_pbr_pct:.0%}·시장 {params.max_market_pbr_pct:.0%} 모두 초과)"
            )

    # 가치 함정 방어(v1.2) — 낮은 PBR이 수익성 훼손의 반영일 가능성 차단
    if val.roe is None:
        reasons.append("최신 연간 수익성 미산출(연간 재무 없음)")
    elif val.roe < params.min_roe_latest:
        reasons.append(
            f"가치 함정 방어({val.roe:+.1%} — 최신 연간 ROE < {params.min_roe_latest:.0%}, "
            "유의미 흑자 요구)"
        )
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
