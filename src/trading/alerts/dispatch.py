"""알림 디스패처 — 등급별 라우팅 (설계서 §8).

- **P0**: 즉시 발송. 채널 실패 시 로그 폴백으로 강등(알림은 반드시 어딘가에 남고,
  발송 실패가 파이프라인을 죽이지 않는다). 발송 채널을 dispatches에 박제.
- **P1**: 적재만 — 점심·마감 다이제스트(``flush_digest``, cron ``alerts-digest``)가 묶어 발송.
- **P2**: 적재만 — 푸시 없음, R6 보고가 읽는다.

이벤트 트리거는 알림·체크리스트 갱신까지만 — **신규 주문 초안을 생성하지 않는다**(§8).
"""

import html as _html
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from trading.alerts.channels import (
    Channel,
    ChannelError,
    LogChannel,
    TelegramChannel,
    channel_from_env,
)
from trading.alerts.model import Alert, Severity, format_alert, format_alert_html
from trading.alerts.store import AlertStore

logger = logging.getLogger("trading.alerts")

_DIGEST_HEADER = "[P1 다이제스트] {n}건"


def _deliver(channel: Channel, plain: str, rich: str) -> None:
    """발송 — Telegram이면 HTML(서식), 거부 시 평문 폴백. 그 외 채널은 평문."""
    if isinstance(channel, TelegramChannel):
        try:
            replace(channel, parse_mode="HTML").send(rich)
            return
        except ChannelError:
            pass  # HTML 거부(엔티티 오류 등) → 평문으로 — 알림 미달이 최악
    channel.send(plain)


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
                _deliver(channel, format_alert(alert), format_alert_html(alert))
            except ChannelError as e:
                logger.error("P0 발송 실패 — 로그 폴백: %s", e)
                channel = self.fallback
                channel.send(format_alert(alert))
            self.store.mark_dispatched([row_id], channel.name)
            return f"sent:{channel.name}"
        if alert.severity is Severity.P1:
            return "queued"   # 점심·마감 flush_digest 가 발송
        return "stored"       # P2 — 보고서 전용

    def flush_digest(self, *, header: str | None = None) -> int:
        """미발송 P1을 1건의 다이제스트로 묶어 발송. 반환: 발송한 알림 수(0이면 무발송).

        발송 성공 시에만 dispatches 기록 — 실패하면 다음 flush가 재시도(중복 없음).
        ``header``: 제목 대체 — 미발화 감시처럼 즉시 발송하는 호출자용(ALERT-1).
        """
        pending = self.store.pending(Severity.P1.value)
        if not pending:
            return 0
        header = header or _DIGEST_HEADER.format(n=len(pending))
        text = header + "\n\n" + "\n\n".join(format_alert(a) for _, a in pending)
        rich = (
            f"<b>{_html.escape(header, quote=False)}</b>\n\n"
            + "\n\n".join(format_alert_html(a) for _, a in pending)
        )
        channel = self.channel
        try:
            _deliver(channel, text, rich)
        except ChannelError as e:
            logger.error("P1 다이제스트 발송 실패 — 로그 폴백: %s", e)
            channel = self.fallback
            channel.send(text)
        self.store.mark_dispatched([rid for rid, _ in pending], channel.name)
        return len(pending)

    def send_run_report(
        self,
        *,
        round_name: str,
        ok: bool,
        started_at: datetime,
        finished_at: datetime,
        summary_lines: Sequence[str],
        failure: str | None = None,
    ) -> str:
        """실행 보고 1통(ALERT-1, 운영자 결정 2026-09-02) — 성공/실패 헤더 + 체인 요약 +
        미발송 P1 꼬리.

        헤더: ✅ 완료 / ⚠️ 완료·부분 실패(best-effort 단계의 P1이 같은 라운드 것일 때) / ❌ 실패.
        보고 자체는 P2로 원장에 남고(정보 — 행동 없음), 꼬리로 실린 P1은 발송 기록을 남겨
        재발송하지 않는다. 채널 실패 시 로그 폴백. 반환 ``sent:<channel>``.
        """
        pending = self.store.pending(Severity.P1.value)
        partial = [a for _, a in pending if a.what.startswith(f"라운드 실패: {round_name}/")]
        if not ok:
            head = f"❌ {round_name} 실패"
        elif partial:
            head = f"⚠️ {round_name} 완료 · 부분 실패 {len(partial)}건"
        else:
            head = f"✅ {round_name} 완료"
        minutes = max(0.0, (finished_at - started_at).total_seconds() / 60)
        head += f" · {started_at:%H:%M}→{finished_at:%H:%M} KST ({minutes:.0f}분)"
        body = [*summary_lines]
        if failure:
            body.append(f"실패: {failure}")
        row_id = self.store.append(
            Alert(
                severity=Severity.P2,
                what=f"실행 보고: {head}",
                rule="ALERT-1 실행 보고(운영자 결정 2026-09-02)",
                created_at=finished_at,
            )
        )
        plain = head + ("\n" + "\n".join(body) if body else "")
        rich = f"<b>{_html.escape(head, quote=False)}</b>" + (
            "\n" + _html.escape("\n".join(body), quote=False) if body else ""
        )
        if pending:
            tail = f"— 미발송 P1 {len(pending)}건 —"
            plain += "\n\n" + tail + "\n\n" + "\n\n".join(format_alert(a) for _, a in pending)
            rich += (
                f"\n\n<b>{_html.escape(tail, quote=False)}</b>\n\n"
                + "\n\n".join(format_alert_html(a) for _, a in pending)
            )
        channel = self.channel
        try:
            _deliver(channel, plain, rich)
        except ChannelError as e:
            logger.error("실행 보고 발송 실패 — 로그 폴백: %s", e)
            channel = self.fallback
            channel.send(plain)
        self.store.mark_dispatched([row_id, *(rid for rid, _ in pending)], channel.name)
        return f"sent:{channel.name}"


def main() -> int:
    """``python -m trading.run alerts-digest`` — 점심·마감 P1 다이제스트 슬롯."""
    d = AlertDispatcher()
    n = d.flush_digest()
    d.store.close()
    print(f"P1 다이제스트: {n}건 발송" if n else "P1 다이제스트: 미발송 알림 없음")
    return 0


__all__ = ["AlertDispatcher", "main"]
