"""R6 보고 — 렌더·as_of 병기·결측 명시·분량 가드(M4 AC) 테스트."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from trading import report as report_runner
from trading.alerts import AlertDispatcher, AlertStore, Severity
from trading.contracts.order import OrderStatus
from trading.journal.playbooks import PlaybookStore
from trading.reports.render import ReportLengthError, render_evening, render_morning
from trading.rounds.r5 import run_r5
from test_r5 import _OneShotClient, _proposal, _thesis

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 6, 10, 21, 0, tzinfo=KST)


def _seeded_store(tmp_path: Path) -> PlaybookStore:
    ps = PlaybookStore(tmp_path / "pb.sqlite")
    res = run_r5(
        _OneShotClient({"playbooks": [_proposal()], "scenario_tree": "분기 A/B",
                        "checklist": ["갭 확인", "거래량 확인"]}),
        [_thesis()], [], [], now=NOW,
    )
    ps.append_run(res.playbooks, res.drafts, as_of="2026-06-10",
                  scenario_tree=res.scenario_tree, checklist=res.checklist)
    return ps


def test_morning_renders_playbooks_and_checklist(tmp_path: Path) -> None:
    ps = _seeded_store(tmp_path)
    r = render_morning(now=NOW, playbook_store=ps)
    ps.close()
    assert r.kind == "morning" and r.day == "2026-06-10"
    assert "pb.20260610.001740.buy" in r.text
    assert "draft" in r.text                      # 주문 상태 표기
    assert "- [ ] 갭 확인" in r.text              # 체크리스트
    assert "읽기 전용" in r.text


def test_morning_no_playbooks_says_no_trade(tmp_path: Path) -> None:
    ps = PlaybookStore(tmp_path / "empty.sqlite")
    r = render_morning(now=NOW, playbook_store=ps)
    ps.close()
    assert "오늘 할 일 없음" in r.text and "비거래" in r.text


def test_evening_contains_approvals_and_missing_sections(tmp_path: Path) -> None:
    ps = _seeded_store(tmp_path)
    als = AlertStore(tmp_path / "al.sqlite")
    r = render_evening(now=NOW, playbook_store=ps, alert_store=als)
    ps.close()
    als.close()
    assert r.kind == "evening"
    # 검토 후보가 문서 최상단에 — 승인은 내일 아침 arm-check로 이관
    assert "`order.20260610.001740.buy`" in r.text   # ID는 코드 표기(텔레그램 자동 링크 차단)
    assert "플러시 롱" in r.text                      # 근거 1줄(R5 summary) 병기
    assert "발동 조건: gap_pct <-3.0" in r.text       # arm 조건 병기
    assert "기본단위의 50%" in r.text                  # "0.5 * normal_unit" 내부 표현식 노출 금지
    assert "매수" in r.text                           # side 한국어 표기
    assert "아침 `/arm-check`에서" in r.text          # 승인은 아침으로 이관
    assert r.text.index("검토 후보") < r.text.index("시나리오")
    # 미수집은 결측 명시(추측 대체 없음)
    assert "KIS" in r.text and "미수집" in r.text
    assert "시나리오" in r.text and "분기 A/B" in r.text


def test_evening_excludes_already_approved_from_requests(tmp_path: Path) -> None:
    ps = _seeded_store(tmp_path)
    draft = ps.draft("order.20260610.001740.buy")
    assert draft is not None
    ps.append_draft(draft.model_copy(update={"status": OrderStatus.APPROVED}))
    als = AlertStore(tmp_path / "al.sqlite")
    r = render_evening(now=NOW, playbook_store=ps, alert_store=als)
    ps.close()
    als.close()
    assert "검토 후보 없음" in r.text


def test_evening_scenario_axes_render_as_title_and_bullets(tmp_path: Path) -> None:
    ps = PlaybookStore(tmp_path / "pb.sqlite")
    res = run_r5(
        _OneShotClient({"playbooks": [], "checklist": [], "scenario_tree": [
            {"title": "축1(반도체 장비)", "lines": ["분기 A-1: SOX 보합 이상", "공통 리스크: 단기 선반영"]},
        ]}),
        [_thesis()], [], [], now=NOW,
    )
    ps.append_run(res.playbooks, res.drafts, as_of="2026-06-10",
                  scenario_tree=res.scenario_tree, checklist=res.checklist)
    als = AlertStore(tmp_path / "al.sqlite")
    r = render_evening(now=NOW, playbook_store=ps, alert_store=als)
    ps.close()
    als.close()
    assert "**축1(반도체 장비)**" in r.text       # 축 제목은 굵게 — 통문단 금지
    assert "- 분기 A-1: SOX 보합 이상" in r.text  # 분기는 1줄 1불릿
    assert "- 공통 리스크: 단기 선반영" in r.text


def test_evening_legacy_prose_scenario_still_renders(tmp_path: Path) -> None:
    # 구조화 이전 적재분(산문 TEXT) — 내용 무변경으로 줄 단위 표시(하위 호환)
    import sqlite3 as _sq

    ps = PlaybookStore(tmp_path / "pb.sqlite")
    ps.close()
    conn = _sq.connect(str(tmp_path / "pb.sqlite"))
    conn.execute("INSERT INTO synth_runs (as_of, scenario_tree, checklist) VALUES (?,?,?)",
                 ("2026-06-10", "축1(장비): 분기 A-1 어쩌고. 축2: 과열 배제.", "[]"))
    conn.commit()
    conn.close()
    ps = PlaybookStore(tmp_path / "pb.sqlite")
    als = AlertStore(tmp_path / "al.sqlite")
    r = render_evening(now=NOW, playbook_store=ps, alert_store=als)
    ps.close()
    als.close()
    assert "축1(장비): 분기 A-1 어쩌고. 축2: 과열 배제." in r.text


def test_length_guard_fails_not_truncates(tmp_path: Path) -> None:
    ps = _seeded_store(tmp_path)
    with pytest.raises(ReportLengthError, match="분량 초과"):
        render_morning(now=NOW, playbook_store=ps, max_chars=50)
    ps.close()


def test_runner_writes_file_and_alerts_on_guard_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _Null:
        @property
        def name(self) -> str:
            return "null"

        def send(self, text: str) -> None:
            pass

    d = AlertDispatcher(channel=_Null(), store=AlertStore(tmp_path / "al.sqlite"))
    # 정상 경로: 파일 생성(발송은 끔)
    rc = report_runner.run("morning", now=NOW, out_dir=tmp_path / "out", dispatcher=d, send=False)
    assert rc == 0
    files = list((tmp_path / "out").glob("*-morning.md"))
    assert len(files) == 1
    # 분량 가드 실패 경로: P1 알림
    monkeypatch.setattr("trading.report.render_morning",
                        lambda **kw: (_ for _ in ()).throw(ReportLengthError("morning 보고 분량 초과: 9999 > 50자")))
    rc2 = report_runner.run("morning", now=NOW, out_dir=tmp_path / "out", dispatcher=d, send=False)
    assert rc2 == 1
    pending = d.store.pending(Severity.P1.value)
    assert len(pending) == 1 and "보고 생성 실패" in pending[0][1].what


def test_tgfmt_converts_report_subset() -> None:
    from trading.reports.tgfmt import to_telegram_html

    md = (
        "# 저녁 결재 보고 — 2026-06-11\n\n\n"
        "## 내일 검토 후보 (아침 arm-check에서 승인)\n"
        "**검토 후보 없음 — 내일 비거래.** (정상)\n"
        "> 검토 후 approved 전이\n"
        "- 분기 A: 갭<-3 & 거래량>2배\n"
        "- 스탑 47000 | `order.20260611.170920.buy`\n"
        "- [ ] 갭 확인\n"
    )
    out = to_telegram_html(md)
    assert "<b>저녁 결재 보고 — 2026-06-11</b>" in out
    assert "<b>검토 후보 없음 — 내일 비거래.</b> (정상)" in out
    assert "<i>검토 후 approved 전이</i>" in out
    assert "• 분기 A: 갭&lt;-3 &amp; 거래량&gt;2배" in out  # 본문 HTML 이스케이프
    # ID는 <code> 엔티티 — 텔레그램이 .buy gTLD를 URL로 오인해 링크 거는 것을 차단
    assert "<code>order.20260611.170920.buy</code>" in out
    assert "□ 갭 확인" in out
    assert "#" not in out and "**" not in out and "`" not in out
    assert "\n\n\n" not in out  # 연속 빈 줄 압축


def test_telegram_channel_parse_mode_in_payload() -> None:
    import json as _json
    from trading.alerts.channels import TelegramChannel

    sink: list[tuple[str, bytes]] = []

    def opener(url: str, body: bytes, timeout: float) -> bytes:
        sink.append((url, body))
        return b'{"ok": true}'

    TelegramChannel(token="t", chat_id="c", parse_mode="HTML", opener=opener).send("<b>x</b>")
    assert _json.loads(sink[0][1])["parse_mode"] == "HTML"
    TelegramChannel(token="t", chat_id="c", opener=opener).send("plain")
    assert "parse_mode" not in _json.loads(sink[1][1])


# ── P-9 ① 저녁 보고 스윙 기회 섹션 ─────────────────────────────────────


def _seed_swing(triggers: bool) -> None:
    """격리된 기본 경로(conftest)의 SwingStore에 스냅샷 적재."""
    from trading.swing import AxisValue, SwingResult, SwingRow, SwingStore

    row = SwingRow(
        "111110", "가상반도체", "KOSPI", 100.0, ("semiconductor",),
        trend=AxisValue(1.0, True), domain=AxisValue(0.9, True),
        fund=AxisValue(), flow=AxisValue(), mdd=-0.1,
        score=0.87, pct={"trend": 0.9, "domain": 0.95},
        triggers=("pullback", "domain_ignition") if triggers else (),
    )
    res = SwingResult(
        as_of="20260610", universe=[row], gate_total=10, scored=5,
        excluded={}, coverage={}, triggered=[row] if triggers else [],
    )
    store = SwingStore()
    store.record(res)
    store.close()


def test_evening_swing_section_lists_triggers(tmp_path: Path) -> None:
    _seed_swing(triggers=True)
    ps = _seeded_store(tmp_path)
    als = AlertStore(tmp_path / "al.sqlite")
    r = render_evening(now=NOW, playbook_store=ps, alert_store=als)
    ps.close(); als.close()
    assert "스윙 기회" in r.text
    assert "가상반도체 — domain_ignition, pullback (스윙 점수 0.87, `111110`)" in r.text
    assert "관심 후보이지 주문 아님" in r.text


def test_evening_swing_section_explicit_when_no_triggers(tmp_path: Path) -> None:
    _seed_swing(triggers=False)
    ps = _seeded_store(tmp_path)
    als = AlertStore(tmp_path / "al.sqlite")
    r = render_evening(now=NOW, playbook_store=ps, alert_store=als)
    ps.close(); als.close()
    assert "스윙 기회" in r.text and "오늘 기회 트리거 없음" in r.text


def test_evening_no_swing_db_omits_section(tmp_path: Path) -> None:
    ps = _seeded_store(tmp_path)
    als = AlertStore(tmp_path / "al.sqlite")
    r = render_evening(now=NOW, playbook_store=ps, alert_store=als)
    ps.close(); als.close()
    assert "스윙 기회" not in r.text  # 스냅샷 자체가 없으면 섹션 생략(가짜 '없음' 단정 금지)
