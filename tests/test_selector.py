"""R5.5 선택기 — 순수 함수 평가·비거래 기본·arm 워크플로 테스트 (M3 AC).

AC: 조건 불일치 시 "비거래" 반환 / 흐름 변수 화이트리스트(계약 테스트는 test_r5) /
관측치 누락=비활성(추측 금지) / approved 초안만 arm / 장중·휴장 거부.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from trading import flowsnap, select_playbooks
from trading.alerts import AlertDispatcher, AlertStore, Severity
from trading.contracts.order import OrderStatus
from trading.contracts.playbook import Playbook, PlaybookState
from trading.journal.playbooks import PlaybookStore
from trading.rounds.r5 import run_r5
from trading.selector import eval_condition, select
from test_r5 import _OneShotClient, _proposal, _thesis

KST = ZoneInfo("Asia/Seoul")
NIGHT = datetime(2026, 6, 10, 20, 30, tzinfo=KST)     # 전일 밤(R5)
MORNING = datetime(2026, 6, 11, 8, 50, tzinfo=KST)    # 목, 08:50
SESSION = datetime(2026, 6, 11, 10, 0, tzinfo=KST)    # 목, 장중
SUNDAY = datetime(2026, 6, 14, 8, 50, tzinfo=KST)


@pytest.fixture(autouse=True)
def _no_kis(monkeypatch: pytest.MonkeyPatch) -> None:
    # 러너가 흐름 스냅샷에서 KIS env를 건드리지 않도록 격리(테스트는 주입 파일만)
    monkeypatch.setattr("trading.select_playbooks.kis_from_env", lambda: None)


def _playbook(arm: dict[str, str], *, srtn: str = "001740", day: str = "20260611") -> Playbook:
    return Playbook(
        id=f"pb.{day}.{srtn}.buy", as_of=MORNING, fetched_at=MORNING, source="r5:claude",
        thesis_ref="t1", order_draft_ref=f"order.{day}.{srtn}.buy",
        arm_conditions=arm,
    )


# --- 평가 엔진 (순수 함수) ---


def test_condition_operators() -> None:
    assert eval_condition("gap_pct", "<-3.0", -3.5).met
    assert not eval_condition("gap_pct", "<-3.0", -2.0).met
    assert eval_condition("premkt_volume_rank", "<=20", 20.0).met
    assert eval_condition("execution_strength", ">=1.2", 1.2).met
    assert eval_condition("volume_climax", "==1", 1.0).met
    assert not eval_condition("volume_climax", ">2", 2.0).met


def test_missing_observation_is_unmet_not_guessed() -> None:
    ev = eval_condition("gap_pct", "<-3.0", None)
    assert not ev.met and ev.note == "관측치 없음"


def test_unevaluable_expression_is_unmet() -> None:
    ev = eval_condition("new_low_after", "09:30", 1.0)  # 비숫자 조건식
    assert not ev.met and "평가 불가" in ev.note


def test_all_conditions_and_semantics() -> None:
    pb = _playbook({"gap_pct": "<-3.0", "premkt_volume_rank": "<=20"})
    res = select([pb], {"001740": {"gap_pct": -4.0, "premkt_volume_rank": 50.0}})
    assert res.no_trade  # 하나라도 미충족 → 비활성
    res2 = select([pb], {"001740": {"gap_pct": -4.0, "premkt_volume_rank": 10.0}})
    assert [a.playbook.id for a in res2.active] == [pb.id]


def test_no_trade_is_default(tmp_path: Path) -> None:
    pb = _playbook({"gap_pct": "<-3.0"})
    assert select([pb], {}).no_trade            # 스냅샷 없음
    assert select([], {}).no_trade              # 플레이북 없음


# --- 러너: 가드·arm 워크플로 ---


def _seed(tmp_path: Path, *, status: OrderStatus = OrderStatus.APPROVED) -> tuple[PlaybookStore, AlertDispatcher]:
    """전일 R5 산출을 store에 심고(승인 상태 주입), 디스패처 준비."""
    ps = PlaybookStore(tmp_path / "pb.sqlite")
    res = run_r5(
        _OneShotClient({"playbooks": [_proposal()], "scenario_tree": "s", "checklist": []}),
        [_thesis()], [], [], now=datetime(2026, 6, 11, 20, 30, tzinfo=KST),
    )
    # 테스트 편의: R5 산출 id가 당일(20260611) 규약이 되도록 now를 6/11로 — 6/11 아침이 읽는다
    ps.append_run(res.playbooks, res.drafts, as_of="2026-06-11",
                  scenario_tree=res.scenario_tree, checklist=[])
    if status is not OrderStatus.DRAFT:
        draft = res.drafts[0]
        ps.append_draft(draft.model_copy(update={"status": status}))

    class _Null:
        @property
        def name(self) -> str:
            return "null"

        def send(self, text: str) -> None:
            pass

    d = AlertDispatcher(channel=_Null(), store=AlertStore(tmp_path / "al.sqlite"))
    return ps, d


def _flow(tmp_path: Path, obs: dict[str, dict[str, Any]]) -> Path:
    flow_dir = tmp_path / "flow"
    flow_dir.mkdir()
    (flow_dir / "20260611.json").write_text(json.dumps(obs), encoding="utf-8")
    return flow_dir


def test_runner_refuses_session_and_holiday(tmp_path: Path) -> None:
    ps, d = _seed(tmp_path)
    assert select_playbooks.run(now=SESSION, playbook_store=ps, dispatcher=d) == 3
    assert select_playbooks.run(now=SUNDAY, playbook_store=ps, dispatcher=d) == 3
    ps.close()


def test_runner_no_snapshot_means_no_trade(tmp_path: Path) -> None:
    ps, d = _seed(tmp_path)
    rc = select_playbooks.run(
        now=MORNING, playbook_store=ps, dispatcher=d, flow_dir=tmp_path / "flow"
    )
    assert rc == 0
    # arm 없음 — 초안은 approved(v2) 그대로
    draft = ps.draft("order.20260611.001740.buy")
    assert draft is not None and draft.status is OrderStatus.APPROVED
    ps.close()


def test_runner_arms_approved_draft_and_alerts(tmp_path: Path) -> None:
    ps, d = _seed(tmp_path, status=OrderStatus.APPROVED)
    flow_dir = _flow(tmp_path, {"001740": {"gap_pct": -4.0, "premkt_volume_rank": 10}})
    rc = select_playbooks.run(now=MORNING, playbook_store=ps, dispatcher=d, flow_dir=flow_dir)
    assert rc == 0
    draft = ps.draft("order.20260611.001740.buy")
    assert draft is not None and draft.status is OrderStatus.ARMED
    pending = d.store.pending(Severity.P1.value)
    assert len(pending) == 1 and "arm" in pending[0][1].what
    ps.close()


def test_runner_holds_unapproved_draft(tmp_path: Path) -> None:
    ps, d = _seed(tmp_path, status=OrderStatus.DRAFT)
    flow_dir = _flow(tmp_path, {"001740": {"gap_pct": -4.0, "premkt_volume_rank": 10}})
    rc = select_playbooks.run(now=MORNING, playbook_store=ps, dispatcher=d, flow_dir=flow_dir)
    assert rc == 0
    draft = ps.draft("order.20260611.001740.buy")
    # 승인 없인 arm 불가(§6 의도된 마찰) — status 전이 없음
    assert draft is not None and draft.status is OrderStatus.DRAFT
    assert d.store.pending(Severity.P1.value) == []
    ps.close()


def test_runner_arms_across_date_label_mismatch(tmp_path: Path) -> None:
    # SEL-3 해소: R5 산출일(6/11 밤)과 조회일(6/12 아침)이 달라도 approved 풀로 찾아 arm.
    # 과거 playbooks_for_day(20260612)는 pb.20260611을 못 찾아 arm 0이었다.
    ps, d = _seed(tmp_path)  # pb.20260611, approved, as_of 6/11
    flow_dir = tmp_path / "flow"
    flow_dir.mkdir()
    (flow_dir / "20260612.json").write_text(
        json.dumps({"001740": {"gap_pct": -4.0, "premkt_volume_rank": 10}}), encoding="utf-8"
    )
    rc = select_playbooks.run(
        now=datetime(2026, 6, 12, 8, 50, tzinfo=KST),
        playbook_store=ps, dispatcher=d, flow_dir=flow_dir,
    )
    assert rc == 0
    draft = ps.draft("order.20260611.001740.buy")
    assert draft is not None and draft.status is OrderStatus.ARMED  # 날짜 라벨 무관 arm
    assert len(d.store.pending(Severity.P1.value)) == 1
    ps.close()


def test_activation_state_enum() -> None:
    pb = _playbook({"gap_pct": "<-3.0"})
    [act] = select([pb], {"001740": {"gap_pct": -5.0}}).activations
    assert act.state is PlaybookState.ACTIVE
