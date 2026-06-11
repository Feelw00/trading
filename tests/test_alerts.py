"""alerts — 페이로드 4요소 강제·채널 어댑터·등급 라우팅·다이제스트 테스트 (M4 AC 선행).

핵심 AC: 행동 매핑 없는 알림이 코드 레벨에서 거부 / P0 즉시·P1 묶음·P2 푸시 없음 /
Telegram 실패 시 로그 폴백 / 토큰 비노출 / 다이제스트 재발송 없음.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from trading.alerts.channels import (
    ChannelError,
    LogChannel,
    TelegramChannel,
    channel_from_env,
)
from trading.alerts.dispatch import AlertDispatcher
from trading.alerts.model import Alert, Severity, format_alert
from trading.alerts.store import AlertStore

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 6, 10, 18, 0, tzinfo=KST)


def _alert(**over: Any) -> Alert:
    base: dict[str, Any] = {
        "severity": Severity.P0,
        "what": "USD/KRW 1550 돌파",
        "rule": "환율 임계 1540 (R1 conflict 제외 후 단일 소스)",
        "action": "보유 수출주 노출 점검 — 사전 약속: 자동 청산 체결 확인",
        "deadline": "오늘 21:00 저녁 결재 보고",
        "created_at": NOW,
    }
    base.update(over)
    return Alert(**base)


class _SpyChannel:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[str] = []

    @property
    def name(self) -> str:
        return "spy"

    def send(self, text: str) -> None:
        if self.fail:
            raise ChannelError("down")
        self.sent.append(text)


# --- 페이로드 4요소 강제 (M4 AC: 행동 매핑 없는 알림 거부) ---


def test_p0_without_action_rejected() -> None:
    with pytest.raises(ValidationError, match="action"):
        _alert(action="")


def test_p1_without_deadline_rejected() -> None:
    with pytest.raises(ValidationError, match="action"):
        _alert(severity=Severity.P1, deadline=" ")


def test_p2_is_info_only_no_action_allowed() -> None:
    a = _alert(severity=Severity.P2, action="", deadline="")
    assert a.severity is Severity.P2
    with pytest.raises(ValidationError, match="P2"):
        _alert(severity=Severity.P2, deadline="")  # action 이 남아 있으면 거부


def test_format_contains_four_elements() -> None:
    text = format_alert(_alert())
    assert "[P0]" in text and "USD/KRW" in text
    assert "규칙:" in text and "행동:" in text and "기한:" in text


def test_naive_created_at_rejected() -> None:
    with pytest.raises(ValidationError):
        _alert(created_at=datetime(2026, 6, 10, 18, 0))  # naive — 금지


# --- Telegram 채널 ---


def _tg(sink: list[tuple[str, bytes]], *, response: dict[str, Any] | None = None) -> TelegramChannel:
    def opener(url: str, body: bytes, timeout: float) -> bytes:
        sink.append((url, body))
        return json.dumps(response if response is not None else {"ok": True}).encode()

    return TelegramChannel(token="tok", chat_id="42", opener=opener)


def test_telegram_posts_sendmessage() -> None:
    sink: list[tuple[str, bytes]] = []
    _tg(sink).send("hello")
    url, body = sink[0]
    assert url == "https://api.telegram.org/bottok/sendMessage"
    payload = json.loads(body)
    assert payload == {"chat_id": "42", "text": "hello"}


def test_telegram_api_reject_raises_scrubbed() -> None:
    sink: list[tuple[str, bytes]] = []
    ch = _tg(sink, response={"ok": False, "description": "bad token tok"})
    with pytest.raises(ChannelError) as ei:
        ch.send("x")
    assert "tok" not in str(ei.value).replace("token", "")  # 토큰 문자열 비노출(스크럽)
    assert "***" in str(ei.value)


def test_telegram_overlong_truncated() -> None:
    sink: list[tuple[str, bytes]] = []
    _tg(sink).send("x" * 5000)
    text = json.loads(sink[0][1])["text"]
    assert len(text) <= 4096 and text.endswith("…(잘림)")


def test_channel_from_env() -> None:
    ch = channel_from_env({"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_CHAT_ID": "c"})
    assert isinstance(ch, TelegramChannel)
    assert isinstance(channel_from_env({"TELEGRAM_BOT_TOKEN": "t"}), LogChannel)
    assert isinstance(channel_from_env({}), LogChannel)


# --- 디스패처 라우팅 ---


def _dispatcher(tmp_path: Path, *, fail: bool = False) -> tuple[AlertDispatcher, _SpyChannel]:
    spy = _SpyChannel(fail=fail)
    d = AlertDispatcher(channel=spy, store=AlertStore(tmp_path / "alerts.sqlite"))
    return d, spy


def test_p0_sent_immediately(tmp_path: Path) -> None:
    d, spy = _dispatcher(tmp_path)
    assert d.notify(_alert()) == "sent:spy"
    assert len(spy.sent) == 1 and "[P0]" in spy.sent[0]


def test_p0_falls_back_to_log_on_failure(tmp_path: Path) -> None:
    d, _ = _dispatcher(tmp_path, fail=True)
    assert d.notify(_alert()) == "sent:log"  # 실패해도 예외 없이 폴백 박제


def test_p1_queued_not_sent(tmp_path: Path) -> None:
    d, spy = _dispatcher(tmp_path)
    assert d.notify(_alert(severity=Severity.P1)) == "queued"
    assert spy.sent == []


def test_p2_stored_never_pushed(tmp_path: Path) -> None:
    d, spy = _dispatcher(tmp_path)
    assert d.notify(_alert(severity=Severity.P2, action="", deadline="")) == "stored"
    assert spy.sent == []
    assert d.flush_digest() == 0  # 다이제스트(P1 전용)에도 안 섞임
    assert spy.sent == []


def test_digest_bundles_p1_once(tmp_path: Path) -> None:
    d, spy = _dispatcher(tmp_path)
    d.notify(_alert(severity=Severity.P1, what="외인 순매수 전환 징후"))
    d.notify(_alert(severity=Severity.P1, what="주문 레벨 -1% 접근"))
    assert d.flush_digest() == 2
    assert len(spy.sent) == 1 and spy.sent[0].startswith("[P1 다이제스트] 2건")
    assert "외인 순매수" in spy.sent[0] and "주문 레벨" in spy.sent[0]
    assert d.flush_digest() == 0  # 재발송 없음
    assert len(spy.sent) == 1


def test_digest_failure_retries_next_flush(tmp_path: Path) -> None:
    spy = _SpyChannel(fail=True)
    fallback = _SpyChannel()
    d = AlertDispatcher(channel=spy, store=AlertStore(tmp_path / "a.sqlite"), fallback=fallback)
    d.notify(_alert(severity=Severity.P1))
    assert d.flush_digest() == 1
    assert len(fallback.sent) == 1  # 폴백으로 나감 + 발송 기록 → 재발송 없음
    assert d.flush_digest() == 0


# --- 스토어 ---


def test_store_appends_and_recent_roundtrip(tmp_path: Path) -> None:
    s = AlertStore(tmp_path / "alerts.sqlite")
    rid = s.append(_alert())
    assert rid > 0
    [back] = s.recent(limit=1)
    assert back == _alert()
    s.close()


def test_format_alert_html_escapes_and_bolds() -> None:
    from trading.alerts.model import format_alert_html

    a = _alert(what="USD/KRW <1540 이탈 & 재돌파", rule="환율 임계 <1540")
    out = format_alert_html(a)
    assert out.startswith("<b>[P0] USD/KRW &lt;1540 이탈 &amp; 재돌파</b>")
    assert "규칙: 환율 임계 &lt;1540" in out
    assert "행동: <b>" in out


def test_p0_to_telegram_uses_html_parse_mode(tmp_path: Path) -> None:
    sink: list[bytes] = []

    def opener(url: str, body: bytes, timeout: float) -> bytes:
        sink.append(body)
        return b'{"ok": true}'

    tg = TelegramChannel(token="t", chat_id="c", opener=opener)
    d = AlertDispatcher(channel=tg, store=AlertStore(tmp_path / "a.sqlite"))
    assert d.notify(_alert()) == "sent:telegram"
    payload = json.loads(sink[0])
    assert payload["parse_mode"] == "HTML" and payload["text"].startswith("<b>[P0]")


def test_digest_to_telegram_html_with_plain_fallback(tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def opener(url: str, body: bytes, timeout: float) -> bytes:
        payload = json.loads(body)
        calls.append(payload)
        # HTML 발송은 거부 → 평문 폴백 검증
        if payload.get("parse_mode") == "HTML":
            return json.dumps({"ok": False, "description": "can't parse entities"}).encode()
        return b'{"ok": true}'

    tg = TelegramChannel(token="t", chat_id="c", opener=opener)
    d = AlertDispatcher(channel=tg, store=AlertStore(tmp_path / "a.sqlite"))
    d.notify(_alert(severity=Severity.P1))
    assert d.flush_digest() == 1
    assert calls[0]["parse_mode"] == "HTML" and "<b>[P1 다이제스트]" in calls[0]["text"]
    assert "parse_mode" not in calls[1] and calls[1]["text"].startswith("[P1 다이제스트]")
    assert d.flush_digest() == 0  # 폴백 발송도 dispatched 기록 — 재발송 없음
