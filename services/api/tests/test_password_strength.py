"""Password policy at the public signup boundary."""

from sixsentences_server.core.auth import signup_password_issues


def test_strong_composed_password_is_accepted() -> None:
    assert (
        signup_password_issues(
            "ResearchReady42!",
            email="ada@example.org",
            first_name="Ada",
        )
        == ()
    )


def test_long_passphrase_is_accepted_without_forced_character_classes() -> None:
    assert (
        signup_password_issues(
            "correct horse battery staple",
            email="ada@example.org",
            first_name="Ada",
        )
        == ()
    )


def test_short_common_and_personal_passwords_report_specific_issues() -> None:
    short = signup_password_issues("password123")
    personal = signup_password_issues(
        "AdaResearchReady42!",
        email="ada@example.org",
        first_name="AdaResearch",
    )

    assert "use at least 12 characters" in short
    assert "avoid common passwords, repeated characters and sequences" in short
    assert "do not use your name or email address" in personal
