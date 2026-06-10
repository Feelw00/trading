"""synth_playbooks 러너 — 장중 거부 가드·PlaybookStore 왕복·실패 P1 알림 테스트."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading import synth_playbooks
from trading.alerts import AlertDispatcher, AlertStore, Severity
from trading.contracts.order import OrderStatus
from trading.journal.playbooks import PlaybookStore
from trading.journal.theses import ThesisStore
from trading.llm import LLMError
from trading.rounds.r5 import run_r5
from test_r5 import _OneShotClient, _proposal, _thesis

KST = ZoneInfo("Asia/Seoul")
NIGHT = datetime(2026, 6, 10, 20, 30, tzinfo=KST)   # 수, 장 마감 후
SESSION = datetime(2026, 6, 10, 10, 0, tzinfo=KST)  # 수, 장중


class _NullChannel:
    @property
    def name(self) -> str:
        return "null"

    def send(self, text: str) -> None:
        pass


def _stores(tmp_path: Path) -> tuple[ThesisStore, PlaybookStore, AlertDispatcher]:
    ts = ThesisStore(tmp_path / "theses.sqlite")
    ps = PlaybookStore(tmp_path / "playbooks.sqlite")
    d = AlertDispatcher(channel=_NullChannel(), store=AlertStore(tmp_path / "alerts.sqlite"))
    return ts, ps, d


def test_runner_refuses_during_session(tmp_path: Path) -> None:
    ts, ps, d = _stores(tmp_path)
    rc = synth_playbooks.run(
        now=SESSION, client=_OneShotClient({}),
        thesis_store=ts, playbook_store=ps, dispatcher=d,
    )
    assert rc == 3  # 장중 주문 설계 금지(설계서 §1·§5)


def test_runner_skips_without_theses(tmp_path: Path) -> None:
    ts, ps, d = _stores(tmp_path)
    rc = synth_playbooks.run(
        now=NIGHT, client=_OneShotClient({}),
        thesis_store=ts, playbook_store=ps, dispatcher=d,
    )
    assert rc == 0


def test_llm_failure_fires_p1_alert(tmp_path: Path) -> None:
    ts, ps, d = _stores(tmp_path)
    ts.append("001740", [_thesis()])
    rc = synth_playbooks.run(
        now=NIGHT, client=_OneShotClient(fail=True),
        thesis_store=ts, event_store=None, playbook_store=ps, dispatcher=d,
    )
    assert rc == 1
    # P1 큐에 적재됐는지(다이제스트 발송 전 pending)
    pending = d.store.pending(Severity.P1.value)
    assert len(pending) == 1
    assert "초안 갱신 불가" in pending[0][1].what
    assert pending[0][1].action == "전일 초안 유지 또는 폐기 선택"


def test_store_roundtrip_for_r55_and_r6(tmp_path: Path) -> None:
    ps = PlaybookStore(tmp_path / "pb.sqlite")
    res = run_r5(
        _OneShotClient({"playbooks": [_proposal()], "scenario_tree": "트리", "checklist": ["갭 확인"]}),
        [_thesis()], [], [], now=NIGHT,
    )
    n = ps.append_run(
        res.playbooks, res.drafts,
        as_of="2026-06-10", scenario_tree=res.scenario_tree, checklist=res.checklist,
    )
    assert n == 2
    # R5.5 경로: 당일 플레이북 → 참조 초안
    [pb] = ps.playbooks_for_day("20260610")
    draft = ps.draft(pb.order_draft_ref)
    assert draft is not None and draft.symbol == "001740"
    # R6 경로: 합성 메타
    run_meta = ps.latest_run()
    assert run_meta is not None and run_meta[1] == "트리" and run_meta[2] == ["갭 확인"]
    # status 전이는 새 version append로만
    approved = draft.model_copy(update={"status": OrderStatus.APPROVED})
    assert ps.append_draft(approved) == 2
    latest = ps.draft(draft.id)
    assert latest is not None and latest.status is OrderStatus.APPROVED
    ps.close()
