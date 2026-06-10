"""alerts — P0/P1/P2 알림 (설계서 §8): 4요소 페이로드 강제 + Telegram 직접 발송(폴링 없음)."""

from trading.alerts.channels import (
    Channel,
    ChannelError,
    LogChannel,
    TelegramChannel,
    channel_from_env,
)
from trading.alerts.dispatch import AlertDispatcher
from trading.alerts.model import Alert, Severity, format_alert
from trading.alerts.store import AlertStore

__all__ = [
    "Alert",
    "AlertDispatcher",
    "AlertStore",
    "Channel",
    "ChannelError",
    "LogChannel",
    "Severity",
    "TelegramChannel",
    "channel_from_env",
    "format_alert",
]
