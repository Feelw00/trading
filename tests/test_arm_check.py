"""arm-check — 활성 approved 풀 발동 판단 + 결정론 해설 (P-6/P-7). arm/발송 없음."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading import arm_check, flowsnap
from trading.contracts.order import OrderStatus
from trading.journal.playbooks import PlaybookStore
from trading.rounds.r5 import run_r5
from test_r5 import _OneShotClient, _proposal, _thesis

KST = ZoneInfo("Asia/Seoul")
SYNTH = datetime(2026, 6, 10, 20, 30, tzinfo=KST)   # R5 생성(밤)
NEXT_AM = datetime(2026, 6, 11, 10, 0, tzinfo=KST)  # 다음 거래일 아침(장중) — 날짜 라벨 다름


def _seeded_store(tmp_path: Path, *, approve: bool = True) -> PlaybookStore:
    ps = PlaybookStore(tmp_path / "pb.sqlite")
    res = run_r5(
        _OneShotClient({"playbooks": [_proposal()], "checklist": [],
                        "scenario_tree": [{"title": "축1", "lines": ["분기 A-1"]}]}),
        [_thesis()], [], [], now=SYNTH,
    )
    ps.append_run(res.playbooks, res.drafts, as_of="2026-06-10",
                  scenario_tree=res.scenario_tree, checklist=res.checklist)
    if approve:
        for d in res.drafts:
            ps.append_draft(d.model_copy(update={"status": OrderStatus.APPROVED}))
    return ps


def _no_kis(monkeypatch: Any) -> None:
    monkeypatch.setattr("trading.arm_check.kis_from_env", lambda: None)


def test_empty_store_is_no_trade(tmp_path: Path, monkeypatch: Any) -> None:
    _no_kis(monkeypatch)
    ps = PlaybookStore(tmp_path / "empty.sqlite")
    r = arm_check.assess(now=NEXT_AM, playbook_store=ps)
    ps.close()
    assert r.items == [] and r.no_trade and r.pending_count == 0
    assert "승인된 셋업: 없음" in arm_check.render_text(r)


def test_unapproved_drafts_show_as_candidates(tmp_path: Path, monkeypatch: Any) -> None:
    _no_kis(monkeypatch)
    monkeypatch.setattr(flowsnap, "INJECT_DIR", tmp_path / "noflow")
    ps = _seeded_store(tmp_path, approve=False)  # draft만 — 활성 풀 밖, 후보로
    r = arm_check.assess(now=NEXT_AM, playbook_store=ps)
    ps.close()
    assert r.items == [] and r.pending_count == 1   # 활성 0, 후보 1
    txt = arm_check.render_text(r)
    assert "승인된 셋업: 없음" in txt
    assert "승인 후보(미승인 1건" in txt
    assert "python -m trading.approve" in txt        # 승인 명령 안내


def test_approved_pool_survives_to_next_day(tmp_path: Path, monkeypatch: Any) -> None:
    # 핵심: 6/10 밤 승인분을 6/11 아침이 본다 — 날짜 라벨 어긋남 버그 해소
    _no_kis(monkeypatch)
    monkeypatch.setattr(flowsnap, "INJECT_DIR", tmp_path / "noflow")
    ps = _seeded_store(tmp_path)
    r = arm_check.assess(now=NEXT_AM, playbook_store=ps)
    ps.close()
    assert len(r.items) == 1
    assert r.items[0].status == "approved"
    assert r.items[0].expiry is not None  # TTL 만료일 표기
    assert "만료" in arm_check.render_text(r)


def test_missing_flow_means_inactive_with_reasons(tmp_path: Path, monkeypatch: Any) -> None:
    _no_kis(monkeypatch)
    monkeypatch.setattr(flowsnap, "INJECT_DIR", tmp_path / "noflow")
    ps = _seeded_store(tmp_path)
    r = arm_check.assess(now=NEXT_AM, playbook_store=ps)
    ps.close()
    item = r.items[0]
    assert not item.active and r.no_trade
    assert any("관측치 없음" in c.note for c in item.conditions)
    assert "기본단위의 50%" in item.cap and "플러시" in " ".join(item.tranches)


def test_injected_flow_can_activate(tmp_path: Path, monkeypatch: Any) -> None:
    _no_kis(monkeypatch)
    inj = tmp_path / "flow"
    inj.mkdir()
    # _proposal arm: gap_pct<-3.0 AND premkt_volume_rank<=20 → 충족 흐름 주입(6/11)
    (inj / "20260611.json").write_text(
        json.dumps({"001740": {"gap_pct": -4.0, "premkt_volume_rank": 10}}), encoding="utf-8"
    )
    monkeypatch.setattr(flowsnap, "INJECT_DIR", inj)
    ps = _seeded_store(tmp_path)
    r = arm_check.assess(now=NEXT_AM, playbook_store=ps)
    ps.close()
    assert r.active_count == 1 and not r.no_trade
    txt = arm_check.render_text(r)
    assert "● 발동" in txt and "발동 가능 1/1" in txt


def test_candidate_shows_would_arm_preview(tmp_path: Path, monkeypatch: Any) -> None:
    # 미승인 후보도 흐름 판단 — "지금 승인하면 발동"을 미리 보여준다
    _no_kis(monkeypatch)
    inj = tmp_path / "flow"
    inj.mkdir()
    (inj / "20260611.json").write_text(
        json.dumps({"001740": {"gap_pct": -4.0, "premkt_volume_rank": 10}}), encoding="utf-8"
    )
    monkeypatch.setattr(flowsnap, "INJECT_DIR", inj)
    ps = _seeded_store(tmp_path, approve=False)  # draft → 후보
    r = arm_check.assess(now=NEXT_AM, playbook_store=ps)
    ps.close()
    assert r.items == [] and r.pending_count == 1
    assert r.candidates[0].active  # 승인하면 발동
    assert "지금 승인 시 발동 1건" in arm_check.render_text(r)


def test_boolean_arm_condition_activates(tmp_path: Path, monkeypatch: Any) -> None:
    # SEL-2 end-to-end: boolean ==true 조건이 발동까지 반영된다
    # (reclaim은 2026-07-14 폐지 — 잔존 boolean인 sector_ignition으로 검증)
    _no_kis(monkeypatch)
    inj = tmp_path / "flow"
    inj.mkdir()
    (inj / "20260611.json").write_text(
        json.dumps({"001740": {"sector_ignition": 1.0}}), encoding="utf-8"
    )
    monkeypatch.setattr(flowsnap, "INJECT_DIR", inj)
    ps = PlaybookStore(tmp_path / "pb.sqlite")
    res = run_r5(
        _OneShotClient({"playbooks": [_proposal(arm_conditions={"sector_ignition": "==true"})],
                        "checklist": [], "scenario_tree": []}),
        [_thesis()], [], [], now=SYNTH,
    )
    ps.append_run(res.playbooks, res.drafts, as_of="2026-06-10",
                  scenario_tree=res.scenario_tree, checklist=res.checklist)
    for d in res.drafts:
        ps.append_draft(d.model_copy(update={"status": OrderStatus.APPROVED}))
    r = arm_check.assess(now=NEXT_AM, playbook_store=ps)
    ps.close()
    assert r.active_count == 1  # boolean 조건 충족 → 발동(과거엔 '평가 불가'로 빠졌음)


def test_ttl_expired_setup_excluded(tmp_path: Path, monkeypatch: Any) -> None:
    _no_kis(monkeypatch)
    monkeypatch.setattr(flowsnap, "INJECT_DIR", tmp_path / "noflow")
    ps = _seeded_store(tmp_path)  # as_of 6/10, time_stop_days=5 → 만료 ~6/17
    far = datetime(2026, 7, 1, 10, 0, tzinfo=KST)  # TTL 한참 지남
    r = arm_check.assess(now=far, playbook_store=ps)
    ps.close()
    assert r.items == []  # 만료된 셋업은 활성 풀에서 빠짐(추격 금지)
