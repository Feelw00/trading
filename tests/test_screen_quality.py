"""screen.quality — v1.8 안정 코어(표시 계층, 게이트 아님) 단위 테스트."""

from trading.screen.quality import (
    PROPOSED_STABILITY,
    StabilityParams,
    is_stable_core,
    stability_metrics,
)


def _year(rev: float | None, ni: float | None, eq: float | None) -> dict[str, float | None]:
    return {"revenue": rev, "op_income": None, "equity": eq, "net_income": ni}


def _series(rows: list[tuple[str, float | None, float | None, float | None]]) -> list[tuple[str, dict[str, float | None]]]:
    # annual_series 계약: 연도 desc
    return [(y, _year(rev, ni, eq)) for y, rev, ni, eq in sorted(rows, reverse=True)]


def test_stable_core_all_axes_met() -> None:
    s = _series([
        ("2021", 100.0, 8.0, 100.0), ("2022", 110.0, 9.0, 105.0),
        ("2023", 120.0, 10.0, 110.0), ("2024", 130.0, 11.0, 120.0),
        ("2025", 140.0, 12.0, 130.0),
    ])
    m = stability_metrics(s)
    assert m.revenue_cagr is not None and m.revenue_cagr > 0.08
    assert m.loss_free is True and m.roe_min is not None and m.roe_min > 0.07
    assert is_stable_core(m)


def test_one_loss_year_breaks_core() -> None:
    s = _series([
        ("2021", 100.0, 8.0, 100.0), ("2022", 110.0, -1.0, 105.0),
        ("2023", 120.0, 10.0, 110.0), ("2024", 130.0, 11.0, 120.0),
        ("2025", 140.0, 12.0, 130.0),
    ])
    m = stability_metrics(s)
    assert m.loss_free is False and not is_stable_core(m)


def test_declining_revenue_breaks_core() -> None:
    s = _series([
        ("2021", 140.0, 8.0, 100.0), ("2022", 130.0, 9.0, 105.0),
        ("2023", 120.0, 10.0, 110.0), ("2024", 110.0, 11.0, 120.0),
        ("2025", 100.0, 12.0, 130.0),
    ])
    m = stability_metrics(s)
    assert m.revenue_cagr is not None and m.revenue_cagr < 0 and not is_stable_core(m)


def test_low_roe_floor_breaks_core_and_param() -> None:
    s = _series([
        ("2021", 100.0, 1.0, 100.0), ("2022", 110.0, 9.0, 105.0),
        ("2023", 120.0, 10.0, 110.0), ("2024", 130.0, 11.0, 120.0),
        ("2025", 140.0, 12.0, 130.0),
    ])
    m = stability_metrics(s)
    assert m.roe_min is not None and m.roe_min < 0.02
    assert not is_stable_core(m)
    assert is_stable_core(m, StabilityParams(min_roe_floor=0.005))  # 노브 동작


def test_insufficient_observation_is_not_core() -> None:
    # 관측 3년 < 4년 — 축이 좋아 보여도 판정 보류(정직 강등)
    s = _series([
        ("2023", 120.0, 10.0, 110.0), ("2024", 130.0, 11.0, 120.0),
        ("2025", 140.0, 12.0, 130.0),
    ])
    m = stability_metrics(s)
    assert m.years_observed == 3 and not is_stable_core(m)


def test_missing_equity_years_excluded_from_roe() -> None:
    s = _series([
        ("2021", 100.0, 8.0, None), ("2022", 110.0, 9.0, 105.0),
        ("2023", 120.0, 10.0, 110.0), ("2024", 130.0, 11.0, 120.0),
        ("2025", 140.0, 12.0, 130.0),
    ])
    m = stability_metrics(s)
    assert m.roe_min is not None and m.roe_min > 0.02  # 자본 결측 연도는 ROE 표본에서 제외
    assert is_stable_core(m, PROPOSED_STABILITY)


def test_returns_core_v19() -> None:
    from trading.screen.quality import dividend_streak, has_cancellation, meets_returns_core

    div = {"2023": {"dps": 100.0}, "2024": {"dps": 120.0}, "2025": {"dps": 130.0}}
    assert dividend_streak(div) == 3
    assert dividend_streak({"2024": {"dps": None}, "2025": {"dps": 130.0}}) == 1
    assert dividend_streak({}) == 0

    assert has_cancellation({"2025": {"acqs": 10.0, "incnr": 5.0}}) is True
    assert has_cancellation({"2025": {"acqs": 10.0, "incnr": 0.0}}) is False

    # 3y 연속 배당 OR 소각 — 리츠는 면제(COLLECT-5 ① 확인 전)
    assert meets_returns_core(3, False, industry="화학")
    assert meets_returns_core(0, True, industry="화학")
    assert not meets_returns_core(2, False, industry="화학")
    assert meets_returns_core(0, False, industry="리츠")


def test_earnings_quality_flag_v21() -> None:
    """v2.1: 최신 연간 순이익이 영업이익 기반이 아니면 플래그(진양제약 사례 인코딩)."""
    from trading.screen.quality import earnings_quality_flag

    def _y(ni: float | None, oi: float | None) -> dict[str, float | None]:
        return {"revenue": 100.0, "op_income": oi, "equity": 100.0, "net_income": ni}

    # 진양제약 2025 실측 축약: 순익 223 vs 영업익 25 (8.8배) → 플래그
    jinyang = [("2025", _y(223.0, 25.3)), ("2024", _y(297.0, 117.0))]
    assert earnings_quality_flag(jinyang) is True
    # 정상: 순익 ≤ 1.5×영업익
    assert earnings_quality_flag([("2025", _y(120.0, 100.0))]) is False
    # 영업 적자인데 순이익 흑자 → 플래그
    assert earnings_quality_flag([("2025", _y(50.0, -10.0))]) is True
    # 순이익 적자는 대상 아님(기존 게이트 소관)
    assert earnings_quality_flag([("2025", _y(-30.0, -50.0))]) is False
    # 영업이익 결측 → 판정 불가(지어내지 않음) — 관측된 직전 연도로 소급
    assert earnings_quality_flag([("2025", _y(100.0, None)), ("2024", _y(297.0, 117.0))]) is True
    assert earnings_quality_flag([("2025", _y(100.0, None))]) is False


def test_op_roe_direction_v22() -> None:
    """v2.2: 이익 방향은 영업이익/자본 기준 — 영업외 스파이크가 중앙값을 왜곡하지 않는다."""
    from trading.screen.quality import op_roe_direction

    def _y(oi: float | None, eq: float, ni: float = 0.0) -> dict[str, float | None]:
        return {"revenue": 100.0, "op_income": oi, "equity": eq, "net_income": ni}

    # 신세계I&C 축약: 영업이익 개선 중인데 과거 순익 스파이크 존재 → 영업 기준은 양수
    s = [("2021", _y(355.0, 1000.0, ni=390.0)), ("2022", _y(375.0, 1000.0, ni=841.0)),
         ("2023", _y(400.0, 1000.0, ni=304.0)), ("2024", _y(370.0, 1000.0, ni=338.0)),
         ("2025", _y(491.0, 1000.0, ni=283.0))]
    d = op_roe_direction(s)
    assert d is not None and d > 0.09  # 최신 49.1% − 중앙 37.5% = +11.6%p

    # 영업 하강(KX 축약) → 음수
    s2 = [("2021", _y(741.0, 1000.0)), ("2022", _y(786.0, 1000.0)),
          ("2023", _y(620.0, 1000.0)), ("2024", _y(597.0, 1000.0)), ("2025", _y(384.0, 1000.0))]
    d2 = op_roe_direction(s2)
    assert d2 is not None and d2 < -0.2

    # 관측 부족(<4) → None (지어내지 않음)
    assert op_roe_direction(s2[:3]) is None
    # 영업이익 결측 연도는 표본에서 제외
    s3 = s2[:4] + [("2025", _y(None, 1000.0))]
    d3 = op_roe_direction(s3)
    assert d3 is not None  # 2024까지 4관측으로 판정


def test_revenue_trend_v23() -> None:
    """v2.3: 2y 연속 역성장=코어 강등 신호, 단년 -10%+ 급감=표기 신호."""
    from trading.screen.quality import revenue_trend

    def _y(rev: float | None) -> dict[str, float | None]:
        return {"revenue": rev, "op_income": 10.0, "equity": 100.0, "net_income": 5.0}

    # NPC 축약: -9.9% → -1.7% 연속 역성장
    npc = [("2023", _y(5087.0)), ("2024", _y(4583.0)), ("2025", _y(4503.0))]
    tr = revenue_trend(npc)
    assert tr.consecutive_decline is True and tr.sharp_drop is False

    # KX 축약: +0.2% → -14.3% — 연속은 아니고 급감 표기
    kx = [("2023", _y(4031.0)), ("2024", _y(4038.0)), ("2025", _y(3461.0))]
    tr2 = revenue_trend(kx)
    assert tr2.consecutive_decline is False and tr2.sharp_drop is True
    assert tr2.yoy_latest is not None and tr2.yoy_latest < -0.14

    # 성장 지속 — 무신호
    ok = [("2023", _y(100.0)), ("2024", _y(110.0)), ("2025", _y(120.0))]
    tr3 = revenue_trend(ok)
    assert not tr3.consecutive_decline and not tr3.sharp_drop

    # 관측 부족(연간 1개) — 판정 불가, 신호 없음
    tr4 = revenue_trend([("2025", _y(100.0))])
    assert tr4.yoy_latest is None and not tr4.consecutive_decline and not tr4.sharp_drop


def test_roe_cv_stability_axis() -> None:
    """ROE 변동계수 — 낮을수록 안정, 평균≤0·관측 부족은 None(지어내지 않음)."""
    from trading.screen.quality import roe_cv

    def _y(ni: float, eq: float = 1000.0) -> dict[str, float | None]:
        return {"revenue": 100.0, "op_income": 10.0, "equity": eq, "net_income": ni}

    # 안정(ROE 8~10% 균질) vs 변동(2~25%)
    stable = [(str(2021 + i), _y(v)) for i, v in enumerate([80.0, 90.0, 85.0, 95.0, 90.0])]
    choppy = [(str(2021 + i), _y(v)) for i, v in enumerate([20.0, 250.0, 60.0, 180.0, 40.0])]
    cv_s, cv_c = roe_cv(stable), roe_cv(choppy)
    assert cv_s is not None and cv_c is not None and cv_s < 0.1 < cv_c
    # 평균 ≤ 0 → None
    assert roe_cv([(str(2021 + i), _y(v)) for i, v in enumerate([-50.0, 10.0, -20.0, 5.0, -10.0])]) is None
    # 관측 3년 < 4 → None
    assert roe_cv(stable[:3]) is None
