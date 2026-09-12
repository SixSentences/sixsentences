"""Community transactional mail contains no hosted operator identity."""

from sixsentences_server.mail import (
    password_reset_email,
    signup_verification_email,
    voice_session_email,
)
from sixsentences_server.mail.service import _valid_message_id


def test_security_templates_are_operator_neutral() -> None:
    rendered = password_reset_email(reset_url="https://local.example/reset?t=x")
    verification = signup_verification_email(
        first_name="Ada", verify_url="https://local.example/verify?t=x"
    )

    combined = f"{rendered.text}\n{rendered.html}\n{verification.text}\n{verification.html}"
    assert "sixsentences.com" not in combined.casefold()
    assert "hello@" not in combined.casefold()
    assert "local.example" in combined


def test_interview_notification_keeps_research_context() -> None:
    rendered = voice_session_email(
        study_title="Field study",
        participant_label="Participant 7",
        duration_label="18 min",
        interview_url="https://local.example/interviews/7",
    )

    assert "Field study" in rendered.subject
    assert "Participant 7" in rendered.text
    assert "https://local.example/interviews/7" in rendered.text


def test_persisted_message_id_validation_is_bounded_and_strict() -> None:
    assert _valid_message_id("<retry-42@local.example>")
    assert not _valid_message_id("<missing-domain@>")
    assert not _valid_message_id("<" + "a" * 2_000 + "@local.example>")
