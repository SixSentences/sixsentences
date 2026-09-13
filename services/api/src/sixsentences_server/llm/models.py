"""Separate private-workspace Gemini and public-research model catalogs.

Historical OpenRouter preferences never override the private-content route.
The research-only catalog remains available for explicitly public server work;
it is not exposed as a private workspace model selection.
"""

from dataclasses import dataclass

from sixsentences_server.llm.base import ModelRef, price_of
from sixsentences_server.llm.privacy import PRIVATE_DEFAULT_MODEL, PRIVATE_PREMIUM_MODEL

# Existing clients still send "auto". It resolves to this concrete default,
# while new clients persist the model id directly.
AUTO_ID = PRIVATE_DEFAULT_MODEL
PUBLIC_RESEARCH_AUTO_ID = "sixsentences-router"
LEGACY_AUTO_ID = "auto"
_BLEND_IN, _BLEND_OUT = 4, 1


@dataclass(frozen=True)
class ChatModel:
    id: str
    label: str
    tagline: str
    tagline_de: str
    provider: str
    model: str
    multiplier: float
    locked: bool
    input_price: float
    output_price: float
    context_length: int
    supports_tools: bool = True
    reasoning_visible: bool = False

    @property
    def impact(self) -> str:
        """Backward-compatible name for legacy API clients."""
        return self.cost_tier

    @property
    def cost_tier(self) -> str:
        """Relative user-facing cost tier; exact economics stay server-side."""
        if self.multiplier <= 3:
            return "low"
        if self.multiplier <= 6:
            return "medium"
        if self.multiplier <= 30:
            return "high"
        return "very_high"


def _model(
    id: str,
    label: str,
    tagline: str,
    tagline_de: str,
    model: str,
    multiplier: float,
    *,
    locked: bool,
    context_length: int,
    supports_tools: bool = True,
    reasoning_visible: bool = False,
    provider: str = "openrouter",
) -> ChatModel:
    price_in, price_out = price_of(model)
    return ChatModel(
        id=id,
        label=label,
        tagline=tagline,
        tagline_de=tagline_de,
        provider=provider,
        model=model,
        multiplier=multiplier,
        locked=locked,
        input_price=price_in,
        output_price=price_out,
        context_length=context_length,
        supports_tools=supports_tools,
        reasoning_visible=reasoning_visible,
    )


PUBLIC_RESEARCH_MODELS: list[ChatModel] = [
    _model(
        PUBLIC_RESEARCH_AUTO_ID,
        "SixSentences Router",
        "Automatic routing balanced for everyday research work.",
        "Automatische, ausgewogene Auswahl für die tägliche Forschungsarbeit.",
        "deepseek/deepseek-v4-flash",
        1,
        locked=False,
        context_length=1_048_576,
        reasoning_visible=True,
    ),
    _model(
        "deepseek-v4-flash",
        "DeepSeek V4 Flash",
        "Fast research analysis with a very large context window.",
        "Schnelle Forschungsanalyse mit sehr großem Kontextfenster.",
        "deepseek/deepseek-v4-flash",
        1,
        locked=True,
        context_length=1_048_576,
        reasoning_visible=True,
    ),
    _model(
        "deepseek-v4-pro",
        "DeepSeek V4 Pro",
        "Deeper analysis for demanding evidence synthesis.",
        "Tiefere Analyse für anspruchsvolle Evidenzsynthesen.",
        "deepseek/deepseek-v4-pro",
        18,
        locked=True,
        context_length=1_048_576,
        reasoning_visible=True,
    ),
    _model(
        "gpt-5.6-terra-pro",
        "GPT Terra 5.6 Pro",
        "High-precision long-context reasoning for complex research work.",
        "Hochpräzises Langkontext-Denken für komplexe Forschungsarbeit.",
        "openai/gpt-5.6-terra-pro",
        63,
        locked=True,
        context_length=1_050_000,
        reasoning_visible=True,
    ),
    _model(
        "glm-5.2",
        "GLM 5.2",
        "Efficient agentic reasoning across large research contexts.",
        "Effizientes agentisches Denken für große Forschungskontexte.",
        "z-ai/glm-5.2",
        16,
        locked=True,
        context_length=1_048_576,
        reasoning_visible=True,
    ),
    _model(
        "mistral-small-4",
        "Mistral Small 4",
        "Fast multilingual analysis for concise research tasks.",
        "Schnelle mehrsprachige Analyse für kompakte Forschungsaufgaben.",
        "mistralai/mistral-small-2603",
        3,
        locked=True,
        context_length=262_144,
        reasoning_visible=True,
    ),
    _model(
        "llama-4-maverick",
        "Llama 4 Maverick",
        "Efficient multimodal reasoning across large research contexts.",
        "Effizientes multimodales Denken über große Forschungskontexte.",
        "meta-llama/llama-4-maverick",
        3,
        locked=True,
        context_length=1_048_576,
        supports_tools=False,
    ),
    _model(
        "gpt-5.6-luna-pro",
        "GPT 5.6 Luna Pro",
        "Economical million-token reasoning for broad research context.",
        "Wirtschaftliches Million-Token-Denken für breite Forschungskontexte.",
        "openai/gpt-5.6-luna-pro",
        7,
        locked=True,
        context_length=1_050_000,
        reasoning_visible=True,
    ),
    _model(
        "nemotron-3-super",
        "Nemotron 3 Super",
        "Structured long-context reasoning for evidence-heavy work.",
        "Strukturiertes Langkontext-Denken für evidenzintensive Arbeit.",
        "nvidia/nemotron-3-super-120b-a12b",
        2,
        locked=True,
        context_length=262_144,
        reasoning_visible=True,
    ),
    _model(
        "minimax-m3",
        "MiniMax M3",
        "Agentic reasoning tuned for multi-step research tasks.",
        "Agentisches Denken für mehrstufige Forschungsaufgaben.",
        "minimax/minimax-m3",
        5,
        locked=True,
        context_length=1_048_576,
        reasoning_visible=True,
    ),
    _model(
        "gemini-3.1-flash-lite",
        "Gemini 3.1 Flash Lite",
        "Fast multimodal reasoning across large research contexts.",
        "Schnelles multimodales Denken über große Forschungskontexte.",
        "google/gemini-3.1-flash-lite",
        5,
        locked=True,
        context_length=1_048_576,
        reasoning_visible=True,
    ),
    _model(
        "kimi-k2.6",
        "Kimi K2.6",
        "Strong agentic reasoning for complex research workflows.",
        "Starkes agentisches Denken für komplexe Forschungsabläufe.",
        "moonshotai/kimi-k2.6",
        15,
        locked=True,
        context_length=262_144,
        reasoning_visible=True,
    ),
    _model(
        "claude-sonnet-5",
        "Claude Sonnet 5",
        "Premium long-context reasoning for the hardest synthesis work.",
        "Premium-Langkontext-Denken für besonders anspruchsvolle Synthesen.",
        "anthropic/claude-sonnet-5",
        34,
        locked=True,
        context_length=1_000_000,
        reasoning_visible=True,
    ),
]


def blended_price(model: str, input_tokens: int = 0) -> float:
    """USD per million tokens at the product's 4:1 input/output mix."""
    price_in, price_out = price_of(model, input_tokens=input_tokens)
    return (_BLEND_IN * price_in + _BLEND_OUT * price_out) / (_BLEND_IN + _BLEND_OUT)


CHAT_MODELS: list[ChatModel] = [
    _model(
        PRIVATE_DEFAULT_MODEL,
        "Gemini 3.5 Flash",
        "Direct Gemini processing for everyday workspace research.",
        "Direkte Gemini-Verarbeitung für die tägliche Forschungsarbeit.",
        PRIVATE_DEFAULT_MODEL,
        1,
        locked=False,
        context_length=1_048_576,
        provider="gemini",
    ),
    _model(
        PRIVATE_PREMIUM_MODEL,
        "Gemini 3.1 Pro Preview",
        "Deeper analysis through the direct Gemini workspace route.",
        "Vertiefte Analyse über die direkte Gemini-Anbindung.",
        PRIVATE_PREMIUM_MODEL,
        # Reservations use Standard short-context economics (4/3 of Flash),
        # not a rounded premium tier. Actual per-call token/context/usage
        # tariffs remain independently enforced by the shared USD governor.
        blended_price(PRIVATE_PREMIUM_MODEL) / blended_price(PRIVATE_DEFAULT_MODEL),
        locked=True,
        context_length=1_048_576,
        provider="gemini",
    ),
]

_BY_ID = {entry.id: entry for entry in CHAT_MODELS}


def auto_reference_price() -> float:
    return blended_price(_BY_ID[AUTO_ID].model)


def available_chat_models() -> list[ChatModel]:
    """Only the private-workspace catalog; locked Gemini entries remain visible."""
    return CHAT_MODELS


def public_research_models() -> list[ChatModel]:
    """Retain the public-only catalog without making it a workspace fallback."""
    return PUBLIC_RESEARCH_MODELS


def resolve_chat_model(model_id: str | None, *, allow_locked: bool = False) -> ChatModel:
    """Resolve a catalog id while keeping unknown provider slugs impossible.

    Interactive API entrypoints pass ``allow_locked`` only after checking the
    caller's admin entitlement or the global operator switch. Workers use it
    for a concrete model id that was validated before the job was queued.
    """
    requested = (
        AUTO_ID if model_id in (None, "", LEGACY_AUTO_ID, PUBLIC_RESEARCH_AUTO_ID) else model_id
    )
    entry = _BY_ID.get(requested)
    if entry is None or (entry.locked and not allow_locked):
        return _BY_ID[AUTO_ID]
    return entry


def pinned_ref(entry: ChatModel) -> ModelRef:
    """Report and pin the actual provider/model, never relabel an old route."""
    return ModelRef(entry.provider, entry.model)
