"""알림 채널 어댑터 — Telegram Bot API 직접 호출(폴링 없음) + 로컬 로그 폴백.

**발신 전용**: ``sendMessage`` 만 호출하고 ``getUpdates``(폴링)는 쓰지 않는다 —
다른 openclaw 인스턴스가 같은 봇을 폴링해도 충돌 원천이 없다(M3 결정).
비밀값(토큰)은 .env 주입(``TELEGRAM_BOT_TOKEN``/``TELEGRAM_CHAT_ID``)이며
**에러 메시지·로그에 토큰을 노출하지 않는다**(스크럽).
"""

import json
import logging
import os
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger("trading.alerts")

# (url, body, timeout) -> raw bytes. 테스트에서 주입.
PostOpener = Callable[[str, bytes, float], bytes]


class ChannelError(RuntimeError):
    """채널 발송 실패 — 호출측은 폴백 채널로 강등한다(알림이 파이프라인을 죽이면 안 됨)."""


class Channel(Protocol):
    """알림 발송 표면 — 평문 1건 발송."""

    @property
    def name(self) -> str: ...

    def send(self, text: str) -> None: ...


def _urlpost(url: str, body: bytes, timeout: float) -> bytes:
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data: bytes = resp.read()
    return data


_TELEGRAM_MAX_LEN = 4096  # Bot API sendMessage 텍스트 상한(공식 문서)


@dataclass(frozen=True)
class TelegramChannel:
    """Bot API ``sendMessage`` 직접 POST. parse_mode 미사용(이스케이프 사고 방지, 평문)."""

    token: str
    chat_id: str
    timeout_s: float = 10.0
    opener: PostOpener = _urlpost

    @property
    def name(self) -> str:
        return "telegram"

    def _scrub(self, text: str) -> str:
        return text.replace(self.token, "***") if self.token else text

    def send(self, text: str) -> None:
        if len(text) > _TELEGRAM_MAX_LEN:
            text = text[: _TELEGRAM_MAX_LEN - 12] + "\n…(잘림)"
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        body = json.dumps({"chat_id": self.chat_id, "text": text}).encode()
        try:
            raw = self.opener(url, body, self.timeout_s)
        except Exception as e:  # noqa: BLE001 — 네트워크 계층 전체를 채널 실패로 수렴
            raise ChannelError(f"telegram 발송 실패: {self._scrub(str(e))[:200]}") from None
        try:
            res = json.loads(raw)
        except json.JSONDecodeError:
            raise ChannelError(f"telegram 응답 파싱 실패: {raw[:100]!r}") from None
        if not (isinstance(res, dict) and res.get("ok")):
            desc = str(res.get("description", "")) if isinstance(res, dict) else str(res)
            raise ChannelError(f"telegram API 거부: {self._scrub(desc)[:200]}")


@dataclass(frozen=True)
class LogChannel:
    """로컬 로그 폴백 — 채널 미설정·발송 실패 시에도 알림은 반드시 어딘가에 남는다."""

    level: int = logging.WARNING

    @property
    def name(self) -> str:
        return "log"

    def send(self, text: str) -> None:
        logger.log(self.level, "[alert]\n%s", text)


def channel_from_env(env: Mapping[str, str] | None = None) -> Channel:
    """.env 주입: 토큰·chat_id 둘 다 있으면 Telegram, 아니면 로그 폴백(키 안내는 호출측)."""
    e = env if env is not None else os.environ
    token = e.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = e.get("TELEGRAM_CHAT_ID", "").strip()
    if token and chat_id:
        return TelegramChannel(token=token, chat_id=chat_id)
    return LogChannel()


__all__ = [
    "Channel",
    "ChannelError",
    "LogChannel",
    "PostOpener",
    "TelegramChannel",
    "channel_from_env",
]
