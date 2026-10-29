from __future__ import annotations

import logging
from collections.abc import Mapping

from dailyscraper.observability.notifications.channels.base import (
    Channel,
    ChannelConfigurationError,
)
from dailyscraper.observability.notifications.channels.email import EmailChannel
from dailyscraper.observability.notifications.channels.telegram import TelegramChannel
from dailyscraper.settings.models import (
    ChannelSettings,
    EmailChannelSettings,
    TelegramChannelSettings,
)

logger = logging.getLogger(__name__)


def build_channels(
    settings: tuple[ChannelSettings, ...], variables: Mapping[str, str]
) -> list[Channel]:
    """Every usable channel; a misconfigured one is skipped, it never stops a run."""
    channels: list[Channel] = []
    for config in settings:
        if not config.enabled:
            continue
        try:
            built = _build(config, variables)
        except ChannelConfigurationError as error:
            logger.warning("Notification channel %s disabled: %s", config.name, error)
            continue
        for channel in built:
            if any(existing.name == channel.name for existing in channels):
                logger.warning("Notification channel %s is configured twice", channel.name)
                continue
            channels.append(channel)
    return channels


def _build(
    config: ChannelSettings, variables: Mapping[str, str]
) -> list[TelegramChannel] | list[EmailChannel]:
    if isinstance(config, TelegramChannelSettings):
        return [
            TelegramChannel(
                name=config.name,
                bot_token=_required(variables, config.bot_token_env),
                chat_id=_required(variables, config.chat_id_env),
                api_root=config.api_root,
                timeout_seconds=config.timeout_seconds,
            )
        ]
    assert isinstance(config, EmailChannelSettings)
    username = _optional(variables, config.username_env)
    password = _optional(variables, config.password_env)
    if (config.username_env or config.password_env) and not (username and password):
        raise ChannelConfigurationError(
            f"set {config.username_env} and {config.password_env} in the environment"
        )
    return [
        EmailChannel(
            name=f"{config.name}:{recipient.casefold()}",
            host=config.host,
            port=config.port,
            encryption=config.encryption,
            from_address=config.from_address,
            recipient=recipient,
            username=username,
            password=password,
            subject_prefix=config.subject_prefix,
            timeout_seconds=config.timeout_seconds,
        )
        for recipient in config.recipients
    ]


def _required(variables: Mapping[str, str], name: str) -> str:
    value = variables.get(name, "").strip()
    if not value:
        raise ChannelConfigurationError(f"set {name} in the environment")
    return value


def _optional(variables: Mapping[str, str], name: str | None) -> str | None:
    return (variables.get(name, "").strip() or None) if name else None
