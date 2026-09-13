"""Neutral transactional email templates for community deployments."""

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class EmailAttachment:
    """One optional text attachment."""

    filename: str
    content: str
    subtype: str = "plain"


@dataclass(frozen=True)
class RenderedEmail:
    """Multipart-ready email content without transport credentials."""

    subject: str
    text: str
    html: str
    unsubscribe_url: str = ""
    one_click_url: str = ""
    attachments: tuple[EmailAttachment, ...] = ()


def _layout(*, title: str, intro: str, action_label: str, action_url: str) -> str:
    safe_title = escape(title)
    safe_intro = escape(intro)
    safe_label = escape(action_label)
    safe_url = escape(action_url, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="font-family:system-ui,sans-serif;color:#102923;background:#f5f3ed;padding:24px">
<main style="max-width:620px;margin:auto;background:white;padding:32px;border-radius:16px">
<p style="letter-spacing:.08em">SIXSENTENCES_</p>
<h1>{safe_title}</h1>
<p>{safe_intro}</p>
<p><a href="{safe_url}">{safe_label}</a></p>
<hr><p style="color:#62716c">Sent by your SixSentences community deployment.</p>
</main></body></html>"""


def password_reset_email(*, reset_url: str) -> RenderedEmail:
    """Render a password reset message."""

    subject = "Reset your SixSentences password"
    intro = "A password reset was requested for your research workspace."
    text = (
        f"{subject}\n\nThis link is valid for one hour:\n{reset_url}\n\n"
        "If you did not request this, ignore the message."
    )
    return RenderedEmail(
        subject,
        text,
        _layout(
            title="Reset your password",
            intro=intro,
            action_label="Choose a new password",
            action_url=reset_url,
        ),
    )


def signup_verification_email(*, first_name: str, verify_url: str) -> RenderedEmail:
    """Render the optional self-signup email verification message."""

    name = first_name.strip()
    welcome = f"Welcome, {name}." if name else "Welcome."
    subject = "Confirm your email for SixSentences"
    intro = f"{welcome} Confirm your email address to activate your research workspace."
    text = (
        f"{subject}\n\n{intro}\n\nConfirm your email:\n{verify_url}\n\n"
        "This link is valid for twenty four hours."
    )
    return RenderedEmail(
        subject,
        text,
        _layout(
            title="Confirm your email",
            intro=intro,
            action_label="Confirm my email",
            action_url=verify_url,
        ),
    )


def voice_session_email(
    *,
    study_title: str,
    participant_label: str,
    duration_label: str,
    interview_url: str,
) -> RenderedEmail:
    """Render the researcher's completed-interview notification."""

    subject = f"New interview completed: {study_title}"
    intro = (
        f"{participant_label} finished an interview in {study_title} "
        f"({duration_label}). The transcript and analysis are ready for review."
    )
    text = f"{subject}\n\n{intro}\n\nReview: {interview_url}\n"
    return RenderedEmail(
        subject,
        text,
        _layout(
            title="A participant finished",
            intro=intro,
            action_label="Review the interview",
            action_url=interview_url,
        ),
    )
