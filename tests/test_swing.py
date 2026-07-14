"""P-9 스윙 스크리너 — 추세·MDD·축 결측·트리거 순수 계산 테스트."""

from trading.collectors.fins import FinSnapshot
from trading.swing import (
    AxisValue,
    SwingConfig,
    SwingRow,
    _detect_triggers,
    fund_axis,
    max_drawdown,
    trend_axis,
    vol_adjusted_momentum,
)

CFG = SwingConfig()


def _steady_up(n: int, step: float = 0.005) -> list[float]:
    """완만한 우상향(수익률 미세 교차 변동 — 완전 등비는 분산 0이라 축이 None)."""
    c, out = 100.0, []
    for i in range(n):
        out.append(c)
        c *= 1 + step + (0.002 if i % 2 == 0 else -0.002)
    return out


def _spiky(n: int) -> list[float]:
    """같은 총수익이지만 급등·급락 반복(고변동)."""
    c, out = 100.0, []
    for i in range(n):
        out.append(c)
        c *= 1.06 if i % 2 == 0 else 0.955
    return out


def test_vol_adjusted_momentum_prefers_smooth_trend() -> None:
    smooth = vol_adjusted_momentum(_steady_up(80), 60)
    spiky = vol_adjusted_momentum(_spiky(80), 60)
    assert smooth is not None and spiky is not None
    assert smooth > spiky  # 완만한 추세 > 스파이크(변동성 페널티)


def test_vol_adjusted_momentum_insufficient_history_is_none() -> None:
    assert vol_adjusted_momentum(_steady_up(30), 60) is None
    assert vol_adjusted_momentum([100.0] * 80, 60) is None  # 무변동


def test_max_drawdown() -> None:
    closes = [100.0, 120.0, 90.0, 110.0]  # 고점 120 → 90 = -25%
    assert abs(max_drawdown(closes, 120) - (-0.25)) < 1e-9
    assert max_drawdown(_steady_up(50), 120) == 0.0


def test_trend_axis_missing_history() -> None:
    axis, _, _ = trend_axis(_steady_up(40), CFG)
    assert not axis.ok and "히스토리" in axis.note


def test_fund_axis_missing_and_turnaround() -> None:
    axis, _ = fund_axis(None, CFG)
    assert not axis.ok and axis.note == "재무 미수집"
    snap = FinSnapshot("x", "2026", "11013", "CFS",
                       revenue=110, revenue_prev=100, op_income=10, op_income_prev=-5,
                       liabilities=None, equity=None)
    axis2, debt = fund_axis(snap, CFG)
    assert axis2.ok and debt is None
    assert abs(axis2.raw - (0.5 * 0.1 + 0.5 * CFG.yoy_cap)) < 1e-9  # 흑자전환 = 캡 점수


def test_fund_axis_caps_extreme_yoy() -> None:
    snap = FinSnapshot("x", "2026", "11013", "CFS",
                       revenue=500, revenue_prev=100, op_income=50, op_income_prev=10,
                       liabilities=None, equity=None)
    axis, _ = fund_axis(snap, CFG)
    assert axis.raw <= CFG.yoy_cap  # 4배 성장도 캡


def _row(score: float, *, trend_raw: float = 1.0, domain_pct: float = 0.5) -> SwingRow:
    return SwingRow(
        "111110", "테스트", "KOSPI", 100.0, ("semiconductor",),
        trend=AxisValue(raw=trend_raw, ok=True), domain=AxisValue(raw=0.5, ok=True),
        fund=AxisValue(), flow=AxisValue(), mdd=-0.1,
        score=score, pct={"trend": 0.9, "domain": domain_pct},
    )


def test_trigger_pullback() -> None:
    # 상승 추세 후 고점 110 → 현재 103 (-6.4%), ma20 위
    closes = _steady_up(70)
    closes[-1] = closes[-2] * 0.97
    highs = list(closes)
    highs[-5] = max(closes) * 1.05  # 20일 내 고점
    depth = 1 - closes[-1] / max(highs[-20:])
    assert CFG.pullback_min <= depth <= CFG.pullback_max
    trigs = _detect_triggers(_row(0.7), closes, highs, [], None, {}, CFG)
    assert "pullback" in trigs


def test_trigger_no_pullback_when_at_high() -> None:
    closes = _steady_up(70)  # 신고가 진행형 — 조정폭 < 3%
    trigs = _detect_triggers(_row(0.7), closes, list(closes), [], None, {}, CFG)
    assert "pullback" not in trigs


def test_trigger_domain_ignition_and_catalyst() -> None:
    closes = _steady_up(70)
    r = _row(0.7, domain_pct=0.85)
    trigs = _detect_triggers(r, closes, list(closes), [], {}, {"111110": 0.8}, CFG)
    assert "domain_ignition" in trigs and "catalyst" in trigs


def test_trigger_flow_turn() -> None:
    closes = _steady_up(70)
    # 최신순: 최근 5일 순매수 양전, 20일 전체는 음수
    flows = [5.0, 3.0, 2.0, 1.0, 1.0] + [-10.0] * 15
    trigs = _detect_triggers(_row(0.7), closes, list(closes), flows, None, {}, CFG)
    assert "flow_turn" in trigs
    # 창 전체가 이미 양수(전환 아님) → 미발화
    flows2 = [5.0] * 20
    trigs2 = _detect_triggers(_row(0.7), closes, list(closes), flows2, None, {}, CFG)
    assert "flow_turn" not in trigs2


def test_swing_store_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from trading.swing import SwingResult, SwingStore

    r1 = _row(0.9)
    res = SwingResult("20260610", [r1], 10, 5, {}, {}, (), [])
    store = SwingStore(tmp_path / "s.sqlite")
    n_uni, n_trig = store.record(res)
    assert (n_uni, n_trig) == (1, 0)
    assert store.latest_bas_dt() == "20260610"
    assert store.latest_universe() == [("111110", "테스트")]  # flows 확장(P-9 ③) 소비 형태
    assert store.all_triggers() == []
    # 같은 날 재적재는 멱등(append-only IGNORE)
    assert store.record(res) == (0, 0)
    store.close()
