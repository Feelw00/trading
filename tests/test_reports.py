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
    # 승인 요청이 문서 최상단 결정 섹션에
    assert "order.20260610.001740.buy" in r.text
    assert r.text.index("결정") < r.text.index("시나리오")
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
    assert "승인 요청 없음" in r.text


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
        "## 결정 — 내일 OrderDraft 승인 요청\n"
        "**승인 요청 없음 — 내일 비거래.** (정상)\n"
        "> 검토 후 approved 전이\n"
        "- 분기 A: 갭<-3 & 거래량>2배\n"
        "- [ ] 갭 확인\n"
    )
    out = to_telegram_html(md)
    assert "<b>저녁 결재 보고 — 2026-06-11</b>" in out
    assert "<b>승인 요청 없음 — 내일 비거래.</b> (정상)" in out
    assert "<i>검토 후 approved 전이</i>" in out
    assert "• 분기 A: 갭&lt;-3 &amp; 거래량&gt;2배" in out  # 본문 HTML 이스케이프
    assert "□ 갭 확인" in out
    assert "#" not in out and "**" not in out
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
