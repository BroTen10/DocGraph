"""Email delivery adapters: SMTP, console and an in-memory outbox."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from ..config import settings

logger = logging.getLogger(__name__)

OUTBOX: list[dict[str, str]] = []


class EmailDeliveryError(RuntimeError):
    """Raised when a message cannot be handed to the configured backend."""


def send_email(
    to_email: str,
    subject: str,
    plain_text: str,
    html: str | None = None,
) -> None:
    """Send one email through the backend selected by ``EMAIL_BACKEND``."""
    backend = settings.email_backend

    if backend == "outbox":
        OUTBOX.append(
            {
                "to_email": to_email,
                "subject": subject,
                "plain_text": plain_text,
                "html": html or "",
            }
        )
        return

    if backend == "console":
        logger.warning("email console backend: to=%s subject=%s", to_email, subject)
        return

    if backend == "smtp":
        _send_smtp(to_email, subject, plain_text, html)
        return

    raise EmailDeliveryError(f"不支持的邮件后端: {backend}")


def _send_smtp(
    to_email: str,
    subject: str,
    plain_text: str,
    html: str | None = None,
) -> None:
    if not settings.smtp_host:
        raise EmailDeliveryError("SMTP_HOST 未配置，无法发送邮件")

    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(plain_text)
    if html:
        message.add_alternative(html, subtype="html")

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as client:
        if settings.smtp_use_tls:
            client.starttls()
        if settings.smtp_username:
            client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message)


def clear_outbox() -> None:
    """Remove captured outbox messages. Used by tests and local tooling."""
    OUTBOX.clear()
