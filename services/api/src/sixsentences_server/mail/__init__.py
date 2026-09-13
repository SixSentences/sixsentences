"""Transactional email rendering and SMTP delivery."""

from sixsentences_server.mail.service import MailDeliveryError, send_email
from sixsentences_server.mail.templates import (
    RenderedEmail,
    password_reset_email,
    signup_verification_email,
    voice_session_email,
)

__all__ = [
    "MailDeliveryError",
    "RenderedEmail",
    "password_reset_email",
    "send_email",
    "signup_verification_email",
    "voice_session_email",
]
