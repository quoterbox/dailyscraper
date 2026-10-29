from __future__ import annotations

import smtplib
import ssl
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

from dailyscraper.observability.notifications.channels.base import (
    ChannelConfigurationError,
    DeliveryOutcome,
    Message,
    PermanentFailure,
    RetryAfter,
    Sent,
    scrub,
    scrub_error,
)

DEFAULT_RETRY_SECONDS = 30.0


class EmailChannel:
    """One recipient through an SMTP account; one channel per address keeps recipients private."""

    def __init__(
        self,
        *,
        name: str,
        host: str,
        port: int,
        encryption: str,
        from_address: str,
        recipient: str,
        username: str | None = None,
        password: str | None = None,
        subject_prefix: str = "",
        timeout_seconds: float = 10.0,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        for label, value in (("from address", from_address), ("recipient", recipient)):
            if not value.strip() or "\r" in value or "\n" in value:
                raise ChannelConfigurationError(f"SMTP {label} is invalid")
        if "\r" in subject_prefix or "\n" in subject_prefix:
            raise ChannelConfigurationError("SMTP subject prefix contains a line break")
        if bool(username) != bool(password):
            raise ChannelConfigurationError("SMTP username and password go together")
        if encryption == "none" and username:
            raise ChannelConfigurationError("SMTP login over an unencrypted connection")
        self.name = name
        self.host = host
        self.port = port
        self.encryption = encryption
        self.from_address = from_address.strip()
        self.recipient = recipient.strip()
        self.username = username
        self.subject_prefix = subject_prefix.strip()
        self.timeout_seconds = timeout_seconds
        self._password = password
        self._client_factory = client_factory

    def send(self, message: Message) -> DeliveryOutcome:
        client = None
        try:
            client = self._connect()
            client.ehlo()
            if self.encryption == "starttls":
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if self.username:
                client.login(self.username, self._password)
            refused = client.send_message(
                self._email(message), from_addr=self.from_address, to_addrs=[self.recipient]
            )
            if refused:
                code, text = next(iter(refused.values()))
                return self._response(code, text)
            return Sent()
        except smtplib.SMTPRecipientsRefused as error:
            code, text = next(iter(error.recipients.values()))
            return self._response(code, text)
        except (ssl.SSLCertVerificationError, smtplib.SMTPNotSupportedError) as error:
            return PermanentFailure(scrub_error(error, self._secrets))
        except smtplib.SMTPResponseException as error:
            return self._response(error.smtp_code, error.smtp_error)
        except Exception as error:
            return RetryAfter(DEFAULT_RETRY_SECONDS, scrub_error(error, self._secrets))
        finally:
            if client is not None:
                try:
                    client.quit()
                except Exception:
                    pass

    @property
    def _secrets(self) -> tuple[str, ...]:
        return (self._password,) if self._password else ()

    def _connect(self) -> Any:
        if self._client_factory is not None:
            return self._client_factory(
                host=self.host, port=self.port, timeout=self.timeout_seconds
            )
        if self.encryption == "ssl":
            return smtplib.SMTP_SSL(
                self.host,
                self.port,
                timeout=self.timeout_seconds,
                context=ssl.create_default_context(),
            )
        return smtplib.SMTP(self.host, self.port, timeout=self.timeout_seconds)

    def _email(self, message: Message) -> EmailMessage:
        email = EmailMessage()
        email["Subject"] = " ".join(part for part in (self.subject_prefix, message.title) if part)
        email["From"] = self.from_address
        email["To"] = self.recipient
        email.set_content(message.as_text())
        return email

    def _response(self, code: Any, text: Any) -> DeliveryOutcome:
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        status = int(code) if str(code).isdigit() else 0
        reason = scrub(f"{status} {text}".strip(), self._secrets)
        if 500 <= status < 600:
            return PermanentFailure(reason)
        return RetryAfter(DEFAULT_RETRY_SECONDS, reason)
