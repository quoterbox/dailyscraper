"""Notification channels: one adapter per messenger behind ``Channel.send``."""

from dailyscraper.observability.notifications.channels.base import (
    Channel,
    ChannelConfigurationError,
    DeliveryOutcome,
    Message,
    PermanentFailure,
    RetryAfter,
    Sent,
    scrub,
    scrub_error,
)
from dailyscraper.observability.notifications.channels.email import EmailChannel
from dailyscraper.observability.notifications.channels.factory import build_channels
from dailyscraper.observability.notifications.channels.telegram import TelegramChannel

__all__ = [
    "Channel",
    "ChannelConfigurationError",
    "DeliveryOutcome",
    "EmailChannel",
    "Message",
    "PermanentFailure",
    "RetryAfter",
    "Sent",
    "TelegramChannel",
    "build_channels",
    "scrub",
    "scrub_error",
]
