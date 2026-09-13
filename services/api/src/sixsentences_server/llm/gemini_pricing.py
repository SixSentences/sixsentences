"""Reviewed Standard Gemini tariffs and native generateContent usage accounting.

Sources (checked 2026-09-08): https://ai.google.dev/gemini-api/docs/pricing
and https://ai.google.dev/api/generate-content#UsageMetadata. No explicit
cachedContents, grounding, batch, priority or Live API requests are priced here.
Live audio retains its own modality-aware server-relay ledger.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from sixsentences_server.llm.base import cost_usd

GEMINI_TEXT_MODELS = frozenset(
    {
        "gemini-3.5-flash",
        "gemini-3.1-pro-preview",
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
    }
)
GEMINI_IMAGE_OUTPUT_PRICES_PER_MTOK = {
    "gemini-3-pro-image": 120.0,
    "gemini-3.1-flash-image": 60.0,
}
GEMINI_IMAGE_TOKENS_BY_SIZE = {
    "gemini-3-pro-image": {"1K": 1_120, "2K": 1_120, "4K": 2_000},
    "gemini-3.1-flash-image": {
        "0.5K": 747,
        "1K": 1_120,
        "2K": 1_680,
        "4K": 2_520,
    },
}


def report_gemini_uncertain_cost(
    model: str,
    *,
    task: str,
    reserved_cost_usd: float,
    on_cost: Callable[[str, str, float, str], None] | None,
) -> None:
    """Keep an unresolved post-egress attempt in the ledger, never as exact spend."""
    if on_cost is not None:
        on_cost(task, model, reserved_cost_usd, "uncertain")


@dataclass(frozen=True)
class GeminiUsage:
    """Validated provider counters; output includes both candidates and thought."""

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int
    candidate_tokens: int


def _count(value: Any) -> int | None:
    # bool is an int subclass, but is not a valid provider token counter.
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def parse_gemini_usage(usage: Mapping[str, Any] | None) -> GeminiUsage | None:
    """Return valid native counters, or None so callers retain an honest reserve.

    Cached tokens are a subset of the complete prompt, not an extra input.
    Thinking is separate from candidate tokens. A provider total can recover
    unitemized output, but a smaller total or impossible cache is never trusted.
    Missing usage must not make a paid call appear free.
    """
    if not isinstance(usage, Mapping) or "promptTokenCount" not in usage:
        return None
    prompt = _count(usage.get("promptTokenCount"))
    candidates = _count(usage.get("candidatesTokenCount", 0))
    thoughts = _count(usage.get("thoughtsTokenCount", 0))
    cached = _count(usage.get("cachedContentTokenCount", 0))
    tool_prompt = _count(usage.get("toolUsePromptTokenCount", 0))
    if any(value is None for value in (prompt, candidates, thoughts, cached, tool_prompt)):
        return None
    assert prompt is not None and candidates is not None and thoughts is not None
    assert cached is not None and tool_prompt is not None
    if cached > prompt or tool_prompt:
        return None
    # Native REST ServiceTier values are lowercase; unspecified means Standard.
    # Keep the older aliases compatible, but never price flex/priority/unknown
    # tiers with the Standard catalog. Do not coerce non-string metadata.
    if usage.get("serviceTier") not in (
        None,
        "",
        "standard",
        "unspecified",
        "STANDARD",
        "SERVICE_TIER_UNSPECIFIED",
    ):
        return None
    output = candidates + thoughts
    if "totalTokenCount" in usage:
        total = _count(usage.get("totalTokenCount"))
        if total is None or total < prompt + output:
            return None
        # A missing thinking breakdown is not evidence of free thinking.
        output = total - prompt
    elif not any(key in usage for key in ("candidatesTokenCount", "thoughtsTokenCount")):
        return None
    if prompt + output <= 0:
        return None
    return GeminiUsage(prompt, output, cached, candidates)


def _image_tokens(model: str, image_count: int, image_size: str) -> int:
    if _count(image_count) is None:
        raise ValueError("Image count must be a non-negative integer")
    sizes = GEMINI_IMAGE_TOKENS_BY_SIZE.get(model)
    if sizes is None or image_size not in sizes:
        raise ValueError("Unreviewed Gemini image model or output size")
    return image_count * sizes[image_size]


def gemini_projected_cost_usd(
    model: str,
    input_tokens: int,
    max_output_tokens: int,
    *,
    output_image_count: int = 0,
    image_size: str = "1K",
) -> float:
    """Reserve uncached input and bounded output before native provider egress.

    Input must include all modalities and system/context tokens, using an upper
    bound or countTokens result rather than a prose chars/4 estimate. Output is
    the transmitted generation ceiling including thought. Image generation can
    return more than the requested image count, so its entire output allowance
    is reserved at the image tariff (the more expensive output modality).
    """
    if _count(input_tokens) is None or _count(max_output_tokens) is None:
        raise ValueError("Token projections must be non-negative integers")
    if model not in GEMINI_TEXT_MODELS and model not in GEMINI_IMAGE_OUTPUT_PRICES_PER_MTOK:
        raise ValueError("Unreviewed direct Gemini model")
    if _count(output_image_count) is None:
        raise ValueError("Image count must be a non-negative integer")
    result = cost_usd(model, input_tokens, max_output_tokens)
    if output_image_count:
        requested_image_cost = (
            _image_tokens(model, output_image_count, image_size)
            * GEMINI_IMAGE_OUTPUT_PRICES_PER_MTOK[model]
            / 1_000_000
        )
        result = max(
            result + requested_image_cost,
            cost_usd(model, input_tokens, 0)
            + max_output_tokens * GEMINI_IMAGE_OUTPUT_PRICES_PER_MTOK[model] / 1_000_000,
        )
    return result


def gemini_usage_cost_usd(
    model: str,
    usage_metadata: Mapping[str, Any] | None,
    *,
    output_image_count: int | None = None,
    image_size: str = "1K",
) -> tuple[float, str] | None:
    """Price observed native usage without confusing image and text output.

    None means unpriced/inconsistent metadata: settle the caller's conservative
    request reserve, labelled as an estimate. Never treat None as zero. For
    image responses lacking modality counters, actual returned image count and
    the requested size permit a separately labelled image-token estimate.
    """
    usage = parse_gemini_usage(usage_metadata)
    if usage is None:
        return None
    if model not in GEMINI_TEXT_MODELS and model not in GEMINI_IMAGE_OUTPUT_PRICES_PER_MTOK:
        return None
    assert usage_metadata is not None
    image_tokens = 0
    source = "gemini_usage"
    details = usage_metadata.get("candidatesTokensDetails")
    if details is not None:
        if not isinstance(details, list):
            return None
        detailed_tokens = 0
        for detail in details:
            if not isinstance(detail, Mapping):
                return None
            count = _count(detail.get("tokenCount"))
            modality = detail.get("modality")
            if count is None or modality not in ("TEXT", "IMAGE"):
                return None
            detailed_tokens += count
            if modality == "IMAGE":
                image_tokens += count
        if detailed_tokens > usage.candidate_tokens:
            return None
    if model in GEMINI_IMAGE_OUTPUT_PRICES_PER_MTOK:
        if output_image_count is not None and _count(output_image_count) is None:
            return None
        if not image_tokens and output_image_count:
            try:
                image_tokens = _image_tokens(model, output_image_count, image_size)
            except ValueError:
                return None
            source = "gemini_estimate"
        elif details is None and output_image_count is None:
            return None
    elif image_tokens or output_image_count:
        return None
    if image_tokens > usage.candidate_tokens:
        return None
    result = cost_usd(
        model,
        usage.input_tokens,
        usage.output_tokens - image_tokens,
        cached_input_tokens=usage.cached_input_tokens,
    )
    if image_tokens:
        result += image_tokens * GEMINI_IMAGE_OUTPUT_PRICES_PER_MTOK[model] / 1_000_000
    return result, source
