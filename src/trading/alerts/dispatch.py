"""알림 디스패처 — 등급별 라우팅 (설계서 §8).

- **P0**: 즉시 발송. 채널 실패 시 로그 폴백으로 강등(알림은 반드시 어딘가에 남고,
  발송 실패가 파이프라인을 죽이지 않는다). 발송 채널을 dispatches에 박제.
- **P1**: 적재만 — 점심·마감 다이제스트(``flush_digest``, cron ``alerts-digest``)가 묶어 발송.
- **P2**: 적재만 — 푸시 없음, R6 보고가 읽는다.

이벤트 트리거는 알림·체크리스트 갱신까지만 — **신규 주문 초안을 생성하지 않는다**(§8).
"""

import logging
from dataclasses import dataclass, field

from trading.alerts.channels import Channel, ChannelError, LogChannel, channel_from_env
from trading.alerts.model import Alert, Severity, format_alert
from trading.alerts.store import AlertStore

logger = logging.getLogger("trading.alerts")

_DIGEST_HEADER = "[P1 다이제스트] {n}건"


@dataclass
class AlertDispatcher:
    """채널·스토어 주입형 디스패처. 기본값은 .env 채널 + 기본 DB."""

    channel: Channel = field(default_factory=channel_from_env)
    store: AlertStore = field(default_factory=AlertStore)
    fallback: Channel = field(default_factory=LogChannel)

    def notify(self, alert: Alert) -> str:
        """알림 1건 처리 — 반환: 라우팅 결과(``sent:<channel>`` / ``queued`` / ``stored``)."""
        row_id = self.store.append(alert)
        if alert.severity is Severity.P0:
            channel = self.channel
            try:
                channel.send(format_alert(alert))
            except ChannelError as e:
                logger.error("P0 발송 실패 — 로그 폴백: %s", e)
                channel = self.fallback
                channel.send(format_alert(alert))
            self.store.mark_dispatched([row_id], channel.name)
            return f"sent:{channel.name}"
        if alert.severity is Severity.P1:
            return "queued"   # 점심·마감 flush_digest 가 발송
        return "stored"       # P2 — 보고서 전용

    def flush_digest(self) -> int:
        """미발송 P1을 1건의 다이제스트로 묶어 발송. 반환: 발송한 알림 수(0이면 무발송).

        발송 성공 시에만 dispatches 기록 — 실패하면 다음 flush가 재시도(중복 없음).
        """
        pending = self.store.pending(Severity.P1.value)
        if not pending:
            return 0
        body = "\n\n".join(format_alert(a) for _, a in pending)
        text = _DIGEST_HEADER.format(n=len(pending)) + "\n\n" + body
        channel = self.channel
        try:
            channel.send(text)
        except ChannelError as e:
            logger.error("P1 다이제스트 발송 실패 — 로그 폴백: %s", e)
            channel = self.fallback
            channel.send(text)
        self.store.mark_dispatched([rid for rid, _ in pending], channel.name)
        return len(pending)


def main() -> int:
    """``python -m trading.run alerts-digest`` — 점심·마감 P1 다이제스트 슬롯."""
    d = AlertDispatcher()
    n = d.flush_digest()
    d.store.close()
    print(f"P1 다이제스트: {n}건 발송" if n else "P1 다이제스트: 미발송 알림 없음")
    return 0


__all__ = ["AlertDispatcher", "main"]
