"""Exact renames distinguish narrow edit scope from an independent read-only veto."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from sixsentences_server.surveys.service import run_survey_agent


class _MisleadingPool:
    """Always propose an unrelated edit so the server-owned boundary is tested."""

    def complete(self, *args: object, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            text=json.dumps(
                {
                    "answer": "I changed the title.",
                    "actions": [{"operation": "set_title", "value": "Unrelated title"}],
                    "workspace_actions": [],
                }
            ),
            provider="fake",
            model="exact-rename-test",
        )


@pytest.mark.parametrize(
    ("user_request", "renamed"),
    [
        ("nenn Doktorand bitte Promovierende Person, sonst nix ändern", True),
        ("nenn Doktorand bitte Promovierende Person, sonst nichts mehr ändern.", True),
        ("rename Doktorand to Promovierende Person and change nothing else", True),
        (
            "nur prüfen: nenn Doktorand bitte Promovierende Person, sonst nix ändern",
            False,
        ),
        ("nenn Doktorand bitte Promovierende Person, nichts ändern", False),
        ("sag kurz, ob schon jemand geantwortet hat, nix ändern", False),
        (
            "Do not rename Doktorand to Promovierende Person and change nothing else",
            False,
        ),
        (
            "Don't change Doktorand to Promovierende Person, change nothing else",
            False,
        ),
        (
            "rename Doktorand to Promovierende Person not yet, change nothing else",
            False,
        ),
        (
            "nenn Doktorand bitte Promovierende Person noch nicht, sonst nix ändern",
            False,
        ),
        ("Do not rename Doktorand to Promovierende Person", False),
        ("nenn Doktorand bitte Promovierende Person noch nicht", False),
    ],
)
def test_exact_rename_preserves_independent_read_only_instruction(
    user_request: str,
    renamed: bool,
) -> None:
    """A uniquely identified option may change, but never during an explicit review."""
    turn = run_survey_agent(
        _MisleadingPool(),  # type: ignore[arg-type]
        request=user_request,
        title="Pilot",
        description="",
        status="draft",
        questions=[
            {
                "id": "role",
                "title": "Rolle",
                "type": "single_choice",
                "options": ["Student", "Doktorand"],
            }
        ],
        settings={},
        responses=[],
        history=[],
        language="de",
    )
    expected = (
        [
            {
                "operation": "update_question",
                "question_id": "role",
                "changes": {"options": ["Student", "Promovierende Person"]},
            }
        ]
        if renamed
        else []
    )
    assert turn.actions == expected
    assert turn.workspace_actions == []


def test_ambiguous_option_does_not_relax_read_only_boundary() -> None:
    """A scoped phrase must not authorize a model-chosen target among duplicates."""
    turn = run_survey_agent(
        _MisleadingPool(),  # type: ignore[arg-type]
        request="nenn Doktorand bitte Promovierende Person, sonst nix ändern",
        title="Pilot",
        description="",
        status="draft",
        questions=[
            {
                "id": question_id,
                "title": "Rolle",
                "type": "single_choice",
                "options": ["Student", "Doktorand"],
            }
            for question_id in ("role", "former_role")
        ],
        settings={},
        responses=[],
        history=[],
        language="de",
    )
    assert turn.actions == []
    assert turn.workspace_actions == []
