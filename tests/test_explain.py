"""explain — OrderDraft·흐름변수 결정론 해설 (P-6, 순수 코드)."""

from trading.contracts.order import OrderDraft, OrderType, Side, Stop, StopType, Tranche
from trading.contracts.playbook import FLOW_VARIABLES
from trading.reports import explain

from datetime import datetime
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 6, 11, 20, 30, tzinfo=KST)


def test_flow_var_dict_covers_whitelist() -> None:
    # 화이트리스트 변수에 해설 누락이 없어야(import 시점 RuntimeError 가드와 동일 계약)
    assert set(FLOW_VARIABLES) <= set(explain.FLOW_VAR_KO)


def test_explain_condition_numeric() -> None:
    assert "110 이상" in explain.explain_condition("execution_strength", ">=110")
    assert "-3.0 미만" in explain.explain_condition("gap_pct", "<-3.0")


def test_explain_condition_boolean() -> None:
    # SEL-2: boolean 흐름변수는 '= 예/아니오'로 — 평가 불가가 아니다
    assert "예(true)" in explain.explain_condition("prev_day_high_reclaim", "==true")
    assert "아니오(false)" in explain.explain_condition("volume_climax", "==false")


def test_explain_condition_still_unevaluable_for_non_numeric_non_bool() -> None:
    # 시각·문자열 등 진짜 평가 불가 조건은 여전히 명시
    out = explain.explain_condition("new_low_after", "09:30")
    assert "평가 불가" in out


def test_humanize_cap() -> None:
    assert explain.humanize_cap("0.5 * normal_unit") == "기본단위의 50%"
    assert explain.humanize_cap("1.0 * normal_unit") == "기본단위의 100%"
    assert explain.humanize_cap("weird") == "weird"  # 미지의 표현식은 원문(추측 금지)


def test_explain_tranches_structure() -> None:
    ts = [
        Tranche(label="impatience_fee", pct_of_plan=20, order_type=OrderType.LIMIT),
        Tranche(label="flush", pct_of_plan=50, order_type=OrderType.LIMIT),
        Tranche(label="confirmation", pct_of_plan=30, condition="prev_day_high_reclaim"),
    ]
    lines = explain.explain_tranches(ts)
    assert "20%" in lines[0] and "조급비용" in lines[0]
    assert "50%" in lines[1] and "플러시" in lines[1]
    assert "30%" in lines[2] and "충족 시" in lines[2]  # 조건부 트랜치


def test_explain_stop_both_kinds() -> None:
    out = explain.explain_stop(Stop(type=StopType.CONDITIONAL_ORDER_AT_BROKER, level=47000.0), 10)
    assert "47000" in out and "10거래일" in out


def _draft() -> OrderDraft:
    return OrderDraft(
        id="order.20260611.170920.buy", as_of=NOW, fetched_at=NOW, source="t",
        symbol="170920", side=Side.BUY,
        tranches=[Tranche(label="flush", pct_of_plan=100, order_type=OrderType.LIMIT)],
        total_size_cap="0.5 * normal_unit",
        stop=Stop(type=StopType.CONDITIONAL_ORDER_AT_BROKER, level=47000.0),
        time_stop_days=10, created_when_market="closed",  # type: ignore[arg-type]
    )


def test_draft_headline_with_and_without_name() -> None:
    d = _draft()
    assert explain.draft_headline(d, name="엘티씨") == "엘티씨(170920) 매수"
    assert explain.draft_headline(d) == "170920 매수"
