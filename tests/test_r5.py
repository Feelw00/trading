"""R5 — 합성·플레이북·주문 초안: 규율 코드 강제·화이트리스트·비거래 기본 테스트 (M3 AC).

핵심: LLM 출력을 신뢰하지 않는다 — 3트랜치(20/50/30)·총량 상한·손절 2종은 코드가 주입,
stop_level 미제공은 폐기(코드가 가격을 지어내지 않음), 흐름 변수 외 조건은 계약이 거부.
"""

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from trading.contracts.order import OrderStatus, OrderType, Side
from trading.contracts.playbook import FLOW_VARIABLES, Playbook, PlaybookState
from trading.contracts.thesis import Direction, Persona, ThesisRecord
from trading.llm import LLMError
from trading.flowsnap import OBSERVABLE_FLOW_VARS
from trading.rounds.r5 import TOTAL_SIZE_CAP, R5Config, build_prompt, run_r5

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 6, 10, 20, 30, tzinfo=KST)


def _thesis(**over: Any) -> ThesisRecord:
    base: dict[str, Any] = {
        "id": "thesis.20260610.001740.supply",
        "as_of": NOW, "fetched_at": NOW, "source": "r3:claude",
        "persona": Persona.SUPPLY,
        "thesis": "반대매매 소진 후 스윙 반등",
        "direction": Direction.LONG,
        "instrument_class": "SK네트웍스",
        "trigger": "시초 갭다운 후 30분 내 저점 미이탈",
        "invalidation": "플러시 저점 종가 이탈",
        "horizon_days": 5,
        "confidence": 0.55,
    }
    base.update(over)
    return ThesisRecord(**base)


def _proposal(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "thesis_ref": "thesis.20260610.001740.supply",
        "srtn_cd": "001740", "side": "buy",
        "arm_conditions": {"gap_pct": "<-3.0", "premkt_volume_rank": "<=20"},
        "abort_conditions": {"new_low_after": "09:30"},
        "stop_level": 5000,
        "confirmation_condition": "prev_day_high_reclaim",
        "time_stop_days": 5,
        "summary": "플러시 롱",
    }
    base.update(over)
    return base


class _OneShotClient:
    def __init__(self, payload: dict[str, Any] | None = None, *, fail: bool = False) -> None:
        self.payload = payload
        self.fail = fail

    def complete(self, prompt: str) -> str:
        if self.fail:
            raise LLMError("boom")
        return json.dumps(self.payload if self.payload is not None else {"playbooks": []})


def _run(payload: dict[str, Any] | None = None, theses: list[ThesisRecord] | None = None, **kw: Any) -> Any:
    return run_r5(
        _OneShotClient(payload, **kw),
        theses if theses is not None else [_thesis()],
        [], [], now=NOW,
    )


# --- 계약: 흐름 변수 화이트리스트 (M3 AC: 로드 시점 거부) ---


def test_playbook_rejects_non_flow_variable() -> None:
    with pytest.raises(ValidationError, match="whitelist"):
        Playbook(
            id="pb.x", as_of=NOW, fetched_at=NOW, source="t",
            thesis_ref="t1", order_draft_ref="o1",
            arm_conditions={"per_valuation": "<10"},  # 가치 변수 — 거부
        )


def test_playbook_rejects_empty_arm() -> None:
    with pytest.raises(ValidationError, match="arm_conditions"):
        Playbook(
            id="pb.x", as_of=NOW, fetched_at=NOW, source="t",
            thesis_ref="t1", order_draft_ref="o1", arm_conditions={},
        )


def test_playbook_accepts_flow_variables_default_inactive() -> None:
    pb = Playbook(
        id="pb.x", as_of=NOW, fetched_at=NOW, source="t",
        thesis_ref="t1", order_draft_ref="o1",
        arm_conditions={"gap_pct": "<-3.0"},
        abort_conditions={"new_low_after": "09:30"},
    )
    assert pb.default is PlaybookState.INACTIVE  # 기본 비활성(발동은 R5.5)


# --- 규율 코드 강제 ---


def test_discipline_params_injected_ignoring_llm() -> None:
    res = _run({"playbooks": [_proposal()], "scenario_tree": "s", "checklist": ["c1"]})
    [draft] = res.drafts
    assert [(t.label, t.pct_of_plan) for t in draft.tranches] == [
        ("impatience_fee", 20), ("flush", 50), ("confirmation", 30),
    ]
    assert draft.tranches[0].order_type is OrderType.LIMIT
    assert draft.tranches[2].condition == "prev_day_high_reclaim"
    assert draft.total_size_cap == TOTAL_SIZE_CAP
    assert draft.stop is not None and draft.stop.level == 5000.0
    assert draft.time_stop_days == 5                 # 손절 2종 모두
    assert draft.status is OrderStatus.DRAFT
    assert draft.created_when_market.value == "closed"
    [pb] = res.playbooks
    assert pb.order_draft_ref == draft.id and pb.default is PlaybookState.INACTIVE
    assert pb.summary == "플러시 롱"   # 저녁 결재 근거 1줄 보존(버리지 않는다)


def test_missing_stop_level_rejected_not_invented() -> None:
    res = _run({"playbooks": [_proposal(stop_level=None)]})
    assert res.drafts == [] and res.rejected == 1
    assert "stop_level" in res.rejected_reasons[0]


def test_bad_time_stop_falls_back_to_thesis_horizon() -> None:
    res = _run({"playbooks": [_proposal(time_stop_days=999)]})
    assert res.drafts[0].time_stop_days == 5  # grounded 폴백(논제 horizon)


def test_direction_side_mismatch_rejected() -> None:
    res = _run({"playbooks": [_proposal(side="sell")]})  # long 논제 + sell
    assert res.rejected == 1 and "불일치" in res.rejected_reasons[0]


def test_unknown_thesis_ref_rejected() -> None:
    res = _run({"playbooks": [_proposal(thesis_ref="thesis.ghost")]})
    assert res.rejected == 1


def test_flat_theses_produce_no_trade() -> None:
    res = _run(None, theses=[_thesis(direction=Direction.FLAT)])
    assert res.playbooks == [] and res.drafts == []
    assert "비거래" in res.scenario_tree[0].title


def test_non_flow_arm_condition_from_llm_rejected() -> None:
    res = _run({"playbooks": [_proposal(arm_conditions={"consensus_revision": ">0"})]})
    assert res.rejected == 1 and "whitelist" in res.rejected_reasons[0]


def test_empty_playbooks_is_normal_path() -> None:
    res = _run({"playbooks": [], "scenario_tree": "조건 미충족", "checklist": []})
    assert res.playbooks == [] and res.rejected == 0 and res.error is None


# --- 시나리오 구조화 (2026-06-12 가독성 개편: 통문단 금지, 산출 시점 강제) ---


def test_scenario_tree_structured_axes_parsed() -> None:
    res = _run({
        "playbooks": [],
        "scenario_tree": [
            {"title": "축1(반도체 장비)", "lines": ["분기 A-1: SOX 보합 이상", "분기 A-2: 환율 1540 상회 시 비활성"]},
            {"title": "축2(과열 배제)", "lines": ["climax 미소진 — 신규 진입 배제"]},
            {"title": "", "lines": []},                  # 빈 축은 버림
            "통문단 문자열",                              # 비객체 항목은 버림
        ],
        "checklist": [],
    })
    assert [a.title for a in res.scenario_tree] == ["축1(반도체 장비)", "축2(과열 배제)"]
    assert res.scenario_tree[0].lines == ["분기 A-1: SOX 보합 이상", "분기 A-2: 환율 1540 상회 시 비활성"]


def test_scenario_tree_string_fallback_preserves_lines() -> None:
    # 스키마 불복종(문자열)도 데이터 유실 없이 줄 단위 보존
    res = _run({"playbooks": [], "scenario_tree": "첫 줄\n둘째 줄\n", "checklist": []})
    assert len(res.scenario_tree) == 1 and res.scenario_tree[0].title == ""
    assert res.scenario_tree[0].lines == ["첫 줄", "둘째 줄"]


# --- 프롬프트: 관측 가능 흐름변수로 조건 제약 (NXT 미수집 변수 사용 금지) ---


def test_prompt_constrains_arm_to_observable_flow_vars() -> None:
    p = build_prompt([_thesis()], [], [], [], R5Config())
    for v in OBSERVABLE_FLOW_VARS:                       # 관측 가능 변수는 허용 목록에 명시
        assert v in p
    # NXT/미배선 변수는 '미관측 — 영영 미충족, 쓰지 마라' 맥락에 등장
    assert "premkt_volume_ratio" in p and "gap_pct" in p
    assert "관측 가능" in p and "영영 미충족" in p
    # 변수 범위·단위 명시(R5가 범위 밖 임계값 짓지 않도록) — orderbook 범위·체결강도 기준
    assert "-1.0~+1.0" in p and "100 기준" in p


def test_llm_failure_surfaces_error() -> None:
    res = run_r5(_OneShotClient(fail=True), [_thesis()], [], [], now=NOW)
    assert res.error is not None and res.playbooks == []


def test_max_playbooks_cap() -> None:
    many = [_proposal() for _ in range(8)]
    res = _run({"playbooks": many})
    assert len(res.playbooks) <= R5Config().max_playbooks


def test_confirmation_condition_must_be_flow_variable() -> None:
    res = _run({"playbooks": [_proposal(confirmation_condition="목표가 도달")]})
    assert res.rejected == 1 and "흐름 변수" in res.rejected_reasons[0]


def test_confirmation_condition_strips_operator_to_key() -> None:
    # R5가 키에 조건식을 붙여 와도(prev_day_high_reclaim==true) 키만 추출(폐기 아님)
    res = _run({"playbooks": [_proposal(confirmation_condition="prev_day_high_reclaim==true")]})
    assert res.rejected == 0
    [draft] = res.drafts
    assert draft.tranches[2].condition == "prev_day_high_reclaim"


def test_whitelist_matches_design_doc() -> None:
    # 설계서 §4 예시·§6 확인 조건이 화이트리스트에 존재
    assert {"gap_pct", "premkt_volume_rank", "new_low_after", "prev_day_high_reclaim"} <= FLOW_VARIABLES


def test_confirmation_condition_missing_discards_playbook() -> None:
    # 운영자 2026-07-14: 기본 조건 주입 금지 — R5가 확인 조건을 안 내면 규율 위반으로 폐기
    res = _run({"playbooks": [_proposal(confirmation_condition=None)]})
    assert res.rejected == 1 and "기본 조건 주입 금지" in res.rejected_reasons[0]


def test_graded_recovery_is_flow_variable() -> None:
    # 운영자 2026-07-14: 완전/일부 회복을 계획이 임계로 명시 — 연속 변수 화이트리스트 등재
    assert "prev_day_high_recovery" in FLOW_VARIABLES
    res = _run({"playbooks": [_proposal(
        arm_conditions={"prev_day_high_recovery": ">=0.97", "execution_strength": ">110"},
    )]})
    assert res.rejected == 0
    [pb] = res.playbooks
    assert pb.arm_conditions["prev_day_high_recovery"] == ">=0.97"
