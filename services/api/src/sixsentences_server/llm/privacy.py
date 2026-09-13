"""Server-owned AI routing boundaries; user model preferences are not consent."""

from typing import Literal

from sixsentences_server.llm.base import LLMConfigError, ModelRef

ContentScope = Literal["private", "public"]
PRIVATE_DEFAULT_MODEL = "gemini-3.5-flash"
PRIVATE_PREMIUM_MODEL = "gemini-3.1-pro-preview"
# Internal high-volume reviewers, not additional choices in the chat picker.
PRIVATE_SCREENING_MODELS = ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite")
PRIVATE_GEMINI_MODELS = frozenset(
    {
        PRIVATE_DEFAULT_MODEL,
        PRIVATE_PREMIUM_MODEL,
        *PRIVATE_SCREENING_MODELS,
    }
)


def require_route_for_scope(ref: ModelRef, content_scope: ContentScope) -> None:
    """Reject disallowed routes before pinning, budget reservation or transport.

    The inert mock provider remains available to keyless tests. Public scope is
    an explicit server decision, never inferred from a scientific-looking query.
    """
    if content_scope not in {"private", "public"}:
        raise LLMConfigError("Unknown AI content scope")
    if (
        content_scope == "private"
        and ref.provider != "mock"
        and (ref.provider != "gemini" or ref.model not in PRIVATE_GEMINI_MODELS)
    ):
        raise LLMConfigError("Private workspace content requires the direct Gemini route")
