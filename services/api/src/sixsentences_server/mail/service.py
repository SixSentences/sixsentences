"""Small SMTP client for the local, retrying mail relay."""

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from sixsentences_server.config import Settings, get_settings
from sixsentences_server.mail.templates import RenderedEmail


class MailDeliveryError(RuntimeError):
    """The message could not be handed to the configured SMTP queue."""


def _valid_message_id(value: str) -> bool:
    """Validate a persisted Message-ID in bounded linear time."""

    if not 5 <= len(value) <= 998 or value[0] != "<" or value[-1] != ">":
        return False
    left, separator, right = value[1:-1].rpartition("@")
    if separator != "@" or not left or not right or "@" in left:
        return False
    left_allowed = frozenset(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._%+-/="
    )
    right_allowed = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-")
    return all(character in left_allowed for character in left) and all(
        character in right_allowed for character in right
    )


def send_email(
    recipient: str,
    rendered: RenderedEmail,
    *,
    settings: Settings | None = None,
    message_id: str | None = None,
) -> None:
    """Hand one multipart email to SMTP.

    The deployment operator owns transport security, retry policy and relay
    configuration. SMTP credentials are read only from runtime settings.

    A persisted ``message_id`` identifies retries, but SMTP hand-off remains
    at-least-once because it cannot commit atomically with application state.
    """

    settings = settings or get_settings()
    if not settings.smtp_host:
        raise MailDeliveryError("SMTP is not configured")

    stable_message_id = (message_id or "").strip()
    if stable_message_id and not _valid_message_id(stable_message_id):
        raise MailDeliveryError("The persisted email Message-ID is invalid")
    resolved_message_id = stable_message_id or make_msgid(domain=settings.mail_message_id_domain)

    message = EmailMessage()
    message["Subject"] = rendered.subject
    message["From"] = settings.mail_from
    message["To"] = recipient
    if settings.mail_reply_to:
        message["Reply-To"] = settings.mail_reply_to
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = resolved_message_id
    message["X-Entity-Ref-ID"] = resolved_message_id.strip("<>")
    if rendered.one_click_url:
        message["List-Unsubscribe"] = f"<{rendered.one_click_url}>"
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content(rendered.text)
    message.add_alternative(rendered.html, subtype="html")
    for attachment in rendered.attachments:
        message.add_attachment(
            attachment.content,
            subtype=attachment.subtype,
            filename=attachment.filename,
        )

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=12) as smtp:
            smtp.ehlo()
            if settings.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise MailDeliveryError("SMTP did not accept the message") from exc
