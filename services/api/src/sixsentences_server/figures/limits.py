"""Stable request-size contracts shared by Visual Lab entry points."""

# A character limit is predictable across browsers and languages. Sixteen
# thousand characters accommodates a complete PaperBanana-style brief while
# leaving substantial room for server instructions, grounding and model output
# in every configured planner, critic and default image model context.
FIGURE_PROMPT_MAX_CHARACTERS = 16_000
