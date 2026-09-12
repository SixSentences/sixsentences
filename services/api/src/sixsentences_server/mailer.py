"""SMTP transport and durable local outbox processing."""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Protocol

from sixsentences_server.config import Settings
from sixsentences_server.models import OutboxEmail


class MailTransport(Protocol):
    """Injectable boundary used by worker and tests."""

    def send(self, message: OutboxEmail) -> None: ...


@dataclass(slots=True)
class SMTPTransport:
    """Send plain-text mail through an operator-configured SMTP account."""

    settings: Settings

    def send(self, message: OutboxEmail) -> None:
        if not self.settings.smtp_host:
            raise RuntimeError("SMTP is not configured")
        outgoing = EmailMessage()
        outgoing["From"] = self.settings.smtp_from
        outgoing["To"] = message.recipient
        outgoing["Subject"] = message.subject
        outgoing.set_content(message.text_body)
        with smtplib.SMTP(
            self.settings.smtp_host,
            self.settings.smtp_port,
            timeout=30,
        ) as client:
            if self.settings.smtp_starttls:
                client.starttls(context=ssl.create_default_context())
            password = self.settings.smtp_password.get_secret_value()
            if self.settings.smtp_username:
                client.login(self.settings.smtp_username, password)
            client.send_message(outgoing)
        message.sent_at = datetime.now(UTC)


class MemoryTransport:
    """Deterministic transport for tests and explicit local demonstrations."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str]] = []

    def send(self, message: OutboxEmail) -> None:
        self.messages.append((message.recipient, message.subject, message.text_body))
        message.sent_at = datetime.now(UTC)
