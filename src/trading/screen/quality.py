"""안정 코어 판정 — v1.8(운영자 결재 2026-09-01, docs/POLICY_PARAMS.md §5). 순수 함수.

운영자 언명: "가치투자는 안정적인 기업이 기본 — 저PBR이라고 산업 전 종목을 추천해선
안 된다. 안정적이고 우상향이며 주주환원에 적극적이고 분할상장 이력이 없어야."
이 중 기존 fins(연간 재무)로 즉시 측정 가능한 3축을 코어 게이트로 인코딩한다:

1. 우상향 — 매출 CAGR(관측 창 첫→끝 연도) > 0. **실현된** 성장만 인정 —
   미실현 "성장 가능성" 축은 불채택(결재 ③: 관측 불가 서사는 이 시스템이 평가할 수 없다).
2. 흑자 연속 — 최근 5개 연간 순이익 전부 > 0 (v1.1 적자 상한 ≤1보다 강한 코어 기준).
3. 수익성 바닥 — 5년 ROE **최소치** > 2% (중앙값 아닌 최소치 — 최악 연도가 기준).

v1.9(운영자 결재 2026-09-01) — 환원·분할 축 편입(DART 수집 실측 분포 첨부 후 결재):
4. 주주환원 — **3년+ 연속 배당 또는 자사주 소각 이력(5y)**. 통과군 실측: 3y+ 연속 315/523
   (60%)·소각 109(21%). 리츠는 COLLECT-5 ①(alotMatter 분배금 미관측 의심) 확인 전 면제.
5. 분할 이력 — 10년 창 주요사항보고(분할) 보유 시 **코어 강등(관찰로, ⚠분할 표기)**.
   배제가 아닌 강등인 이유: report_nm만으로 인적/물적 구분 불가(COLLECT-5 ②).

관측 부족(<4년)은 코어 아님(정직 강등, 탈락 아님). 코어는 게이트가 아니라
**표시 계층**이다: 통과/탈락 박제는 불변, 숏리스트가 코어/관찰 2단으로 나뉠 뿐이다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

CORE_TOP_N = 15
CORE_INDUSTRY_CAP = 3
WATCH_TOP_N = 10

# fins.FinStore.annual_series 연도 dict 키
_REV, _NI, _EQ, _OI = "revenue", "net_income", "equity", "op_income"

# v2.1(운영자 결재 2026-09-01) — 이익 질 임계: 최신 연간 순이익이 영업이익의 이 배수를
# 넘으면(또는 영업이익 ≤0인데 순이익 >0) 영업외 의존 의심. 실측 근거: 진양제약 2025
# 순익 223억 vs 영업익 25억(8.8배 — 투자부동산 평가이익 290억이 원천), NPC 2024 스파이크.
EARNINGS_QUALITY_MULTIPLE = 1.5


@dataclass(frozen=True)
class StabilityParams:
    min_revenue_cagr: float = 0.0   # 우상향: 관측 창 CAGR 하한(초과 요구)
    min_roe_floor: float = 0.02     # 5년 ROE 최소치 하한(초과 요구)
    min_years_observed: int = 4     # 미만이면 판정 보류(코어 아님) — 창은 5개 연간 고정


PROPOSED_STABILITY = StabilityParams()


@dataclass(frozen=True)
class StabilityMetrics:
    revenue_cagr: float | None      # 관측 창(최대 6개 연간, 5년 스팬) CAGR
    loss_free: bool | None          # 최근 5개 연간 순이익 전부 흑자
    roe_min: float | None           # 최근 5개 연간 ROE 최소치
    years_observed: int             # 순이익 관측 연수(판정 창 기준)


def stability_metrics(
    series: Sequence[tuple[str, Mapping[str, float | None]]],
) -> StabilityMetrics:
    """FinStore.annual_series(연도 desc) → 안정성 3축 원시 지표. 결측은 None(추측 금지)."""
    asc = sorted(series, key=lambda t: t[0])
    rev = [(y, d[_REV]) for y, d in asc if d.get(_REV) is not None]
    ni = [(y, d[_NI]) for y, d in asc if d.get(_NI) is not None]
    eq = {y: d[_EQ] for y, d in asc if d.get(_EQ) is not None}

    cagr: float | None = None
    win = rev[-6:]
    if len(win) >= 2:
        (y0, v0), (y1, v1) = win[0], win[-1]
        span = int(y1) - int(y0)
        if span > 0 and v0 is not None and v1 is not None and v0 > 0 and v1 > 0:
            cagr = (v1 / v0) ** (1.0 / span) - 1.0

    ni_win = ni[-5:]
    loss_free: bool | None = (
        all(v is not None and v > 0 for _, v in ni_win) if ni_win else None
    )

    roes: list[float] = []
    for y, v in ni_win:
        e = eq.get(y)
        if v is not None and e is not None and e > 0:
            roes.append(v / e)
    roe_min = min(roes) if roes else None

    return StabilityMetrics(
        revenue_cagr=cagr,
        loss_free=loss_free,
        roe_min=roe_min,
        years_observed=len(ni_win),
    )


def is_stable_core(m: StabilityMetrics, params: StabilityParams = PROPOSED_STABILITY) -> bool:
    """3축 전부 관측·충족일 때만 코어 — 결측·관측 부족은 코어 아님(관찰로 강등, 탈락 아님)."""
    if m.years_observed < params.min_years_observed:
        return False
    return (
        m.revenue_cagr is not None
        and m.revenue_cagr > params.min_revenue_cagr
        and m.loss_free is True
        and m.roe_min is not None
        and m.roe_min > params.min_roe_floor
    )


# v2.3(운영자 결재 2026-09-01) — 역성장 가드: "사업 역성장 진입 기업에 대한 가치투자는
# 도박"(운영자 원칙). 5y CAGR>0(코어 요건)은 최근 변곡에 둔감 — 실측: 코어 20 중
# 최근 역성장 12종, 2y 연속 4종(NPC·리드코프·아세아제지·BYC), KX는 -14.3% 급감인데
# CAGR +1.7%로 통과. 2y 연속=코어 강등, 단년 -10%+ 급감=표기만(노이즈 가능성).
SHARP_REVENUE_DROP = -0.10


@dataclass(frozen=True)
class RevenueTrend:
    yoy_latest: float | None      # 최근 연간 매출 YoY
    yoy_prev: float | None        # 직전 연간 매출 YoY
    consecutive_decline: bool     # 2년 연속 역성장(둘 다 관측·음수) → 코어 강등
    sharp_drop: bool              # 최근 YoY ≤ -10% → ⚠매출급감 표기(강등 아님)


def revenue_trend(
    series: Sequence[tuple[str, Mapping[str, float | None]]],
) -> RevenueTrend:
    """최근 2개 연간 매출 YoY — 관측 부족은 None(지어내지 않음), 판정은 관측분만."""
    revs = [
        (year, v)
        for year, d in sorted(series, key=lambda t: t[0])
        if (v := d.get(_REV)) is not None and v > 0
    ]
    yoy1 = revs[-1][1] / revs[-2][1] - 1 if len(revs) >= 2 else None
    yoy2 = revs[-2][1] / revs[-3][1] - 1 if len(revs) >= 3 else None
    return RevenueTrend(
        yoy_latest=yoy1,
        yoy_prev=yoy2,
        consecutive_decline=yoy1 is not None and yoy2 is not None and yoy1 < 0 and yoy2 < 0,
        sharp_drop=yoy1 is not None and yoy1 <= SHARP_REVENUE_DROP,
    )


def op_roe_direction(
    series: Sequence[tuple[str, Mapping[str, float | None]]],
    min_obs: int = 4,
) -> float | None:
    """영업 기준 이익 방향(v2.2) — 최신 연간 (영업이익/자본) − 5년 중앙 (소수, %p는 ×100).

    순이익 기반 방향은 영업외 스파이크(KX 2024 순익 975억 vs 영익 597억,
    신세계I&C 2020·2022 처분익)가 기준점(중앙값)을 부풀려 회복 중인 종목을
    음수로 보이게 한다 — 영업이익/자본으로 분자를 바꿔 왜곡을 제거한다.
    관측(영업이익·자본 동시) < min_obs 이면 None(지어내지 않음).
    """
    obs: list[tuple[str, float]] = []
    for year, d in sorted(series, key=lambda t: t[0]):
        oi, eq = d.get(_OI), d.get(_EQ)
        if oi is not None and eq is not None and eq > 0:
            obs.append((year, oi / eq))
    window = obs[-5:]
    if len(window) < min_obs:
        return None
    vals = sorted(v for _y, v in window)
    n = len(vals)
    median = (vals[(n - 1) // 2] + vals[n // 2]) / 2
    return window[-1][1] - median


def roe_cv(
    series: Sequence[tuple[str, Mapping[str, float | None]]],
    min_obs: int = 4,
) -> float | None:
    """ROE 변동계수(5y) — 표준편차 ÷ 평균(운영자 요청 2026-09-01: 수익 안정성 표시축).

    ROE 최소치가 "바닥의 위치"라면 변동계수는 "출렁임의 크기"다 — 낮을수록 안정.
    연간 ROE(순이익/자본) 최근 5개 관측 기준, 관측 < min_obs 또는 평균 ≤ 0이면
    None(음수 평균의 CV는 무의미 — 지어내지 않음).
    """
    roes: list[float] = []
    for _year, d in sorted(series, key=lambda t: t[0]):
        ni, eq = d.get(_NI), d.get(_EQ)
        if ni is not None and eq is not None and eq > 0:
            roes.append(ni / eq)
    window = roes[-5:]
    if len(window) < min_obs:
        return None
    mean = sum(window) / len(window)
    if mean <= 0:
        return None
    var = sum((v - mean) ** 2 for v in window) / len(window)
    return float(var**0.5 / mean)


def earnings_quality_flag(
    series: Sequence[tuple[str, Mapping[str, float | None]]],
    multiple: float = EARNINGS_QUALITY_MULTIPLE,
) -> bool:
    """이익 질 플래그(v2.1) — True = 최신 연간 이익이 영업 기반이 아닐 의심.

    최신 연간(순이익·영업이익 모두 관측된 해) 기준:
    - 영업이익 ≤ 0 인데 순이익 > 0 → 플래그(영업외가 흑자를 만든 것)
    - 순이익 > multiple × 영업이익 → 플래그(평가이익·일회성 의존 의심)
    둘 다 관측된 해가 없으면 False(관측 불가 — 지어내지 않음). 순이익 ≤ 0은 대상 아님
    (적자는 기존 게이트가 처리). 표시·코어 강등용 — 게이트 판정·박제 불변.
    """
    for _year, d in sorted(series, key=lambda t: t[0], reverse=True):
        ni, oi = d.get(_NI), d.get(_OI)
        if ni is None or oi is None:
            continue
        if ni <= 0:
            return False
        if oi <= 0:
            return True
        return ni > multiple * oi
    return False


# --- v1.9 환원·분할 축 ---

CORE_MIN_DIVIDEND_STREAK = 3
# COLLECT-5 ①: 리츠 분배금이 alotMatter에 안 잡히는 것으로 의심(의무 분배인데 무배당
# 관측) — 실측 확인 전까지 환원 요건 면제(무배당 오판으로 탈락시키지 않는다).
RETURNS_EXEMPT_INDUSTRIES = frozenset({"리츠"})


def dividend_streak(series: Mapping[str, Mapping[str, float | None]]) -> int:
    """최근 연도부터 역방향 연속 배당 연수 — dps > 0 **또는 수익률 > 0** 기준.

    수익률 폴백(실관측 2026-09-01, 와이엔텍): 주당 배당금 행에 총액을 오기재하는
    공시가 있어(파서가 무효 처리) dps만으로는 실지급 연도를 놓친다 — 현금배당수익률이
    양수면 지급으로 판정한다.
    """
    streak = 0
    for year in sorted(series, reverse=True):
        d = series[year]
        dps, yld = d.get("dps"), d.get("yield_pct")
        if (dps is not None and dps > 0) or (yld is not None and yld > 0):
            streak += 1
        else:
            break
    return streak


def has_cancellation(buybacks: Mapping[str, Mapping[str, float]]) -> bool:
    """수집 창 내 자사주 소각(incnr) 이력 유무."""
    return any((v.get("incnr") or 0.0) > 0 for v in buybacks.values())


def meets_returns_core(
    streak: int, cancelled: bool, *, industry: str,
    min_streak: int = CORE_MIN_DIVIDEND_STREAK,
) -> bool:
    """주주환원 코어 요건 — 연속 배당 또는 소각. 면제 산업(리츠)은 통과(unknown 정직)."""
    if industry in RETURNS_EXEMPT_INDUSTRIES:
        return True
    return streak >= min_streak or cancelled


__all__ = [
    "CORE_INDUSTRY_CAP",
    "CORE_MIN_DIVIDEND_STREAK",
    "CORE_TOP_N",
    "EARNINGS_QUALITY_MULTIPLE",
    "PROPOSED_STABILITY",
    "RETURNS_EXEMPT_INDUSTRIES",
    "RevenueTrend",
    "SHARP_REVENUE_DROP",
    "StabilityMetrics",
    "StabilityParams",
    "WATCH_TOP_N",
    "dividend_streak",
    "earnings_quality_flag",
    "has_cancellation",
    "is_stable_core",
    "meets_returns_core",
    "op_roe_direction",
    "revenue_trend",
    "roe_cv",
    "stability_metrics",
]
