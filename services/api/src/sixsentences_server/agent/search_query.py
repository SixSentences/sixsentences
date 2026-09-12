"""Intent-derived query formulation for every external search surface."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Literal

from sixsentences_server.core.protocol import _heuristic_query
from sixsentences_server.llm.base import TaskType
from sixsentences_server.llm.pool import LLMPool

SearchSurface = Literal["academic", "web"]

_CONVERSATIONAL_QUERY = re.compile(
    r"\b(?:can\s+you|could\s+you|would\s+you|please|show\s+me|tell\s+me|"
    r"look\s+up|search\s+for|find\s+me|kannst\s+du|könntest\s+du|"
    r"koenntest\s+du|bitte|zeig\s+mir|zeige\s+mir|such\s+mal|suche\s+mal|"
    r"finde\s+mir|schau\s+mal)\b",
    re.IGNORECASE,
)

_INSTRUCTION_ONLY_TERMS = {
    "compare",
    "comparison",
    "contrast",
    "gegenueberstellen",
    "gegenüberstellen",
    "jetzt",
    "please",
    "relevant",
    "relevante",
    "relevanten",
    "vergleiche",
    "vergleichen",
}
_QUERY_DISCOURSE_TERMS = {
    "actually",
    "correction",
    "doch",
    "focus",
    "fokus",
    "forget",
    "instead",
    "korrektur",
    "nein",
    "rather",
    "stopp",
    "stop",
    "vergiss",
}
_REPORTING_FIELD_REQUEST = re.compile(
    r"\b(?:nenn|nenne|nennt|gib|gebe|zeig|zeige|list|include|report|return|show)\w*\b"
    r".{0,120}\b(?:jahr|year|venue|journal|conference|autor(?:en)?|authors?)\b",
    re.IGNORECASE,
)
_REPORTING_FIELD_TERMS = {
    "author",
    "authors",
    "autoren",
    "conference",
    "jahr",
    "journal",
    "venue",
    "year",
}

_HASHICORP_TERRAFORM = re.compile(r"\bterraform\b(?!ing)", re.IGNORECASE)
_INTERNAL_CONTEXT_MARKER = re.compile(
    r"\b(?:prior\s+topic\s+context\s+only|current\s+authoritative\s+request|"
    r"current\s+follow-up|conversation\s+context)\s*:",
    re.IGNORECASE,
)
_INTERNAL_ENVELOPE_MARKER = re.compile(
    r"\b(?:prior\s+topic\s+context\s+only|current\s+authoritative\s+request)\s*:",
    re.IGNORECASE,
)
_INTERNAL_CONTEXT_QUERY_TERMS = {
    "authoritative",
    "context",
    "current",
    "only",
    "prior",
    "request",
}
_INTERNAL_CONTROL_QUERY = re.compile(
    r"^\s*[\"']?(?:prior|current|context)[\"']?"
    r"(?:\s+AND\s+[\"']?[A-Za-z0-9+.#-]+[\"']?){0,3}\s*$",
    re.IGNORECASE,
)


def _strip_internal_context_markers(value: str) -> str:
    """Remove server control labels while preserving their semantic bodies."""

    return _INTERNAL_CONTEXT_MARKER.sub("\n", value).strip()


def _entity_disambiguation(request: str) -> str:
    """Return a short, deterministic entity constraint for ambiguous names."""

    if _HASHICORP_TERRAFORM.search(request):
        return (
            '"Terraform" means HashiCorp Terraform, the infrastructure as code '
            'tool. It never means planetary "terraforming" unless the user says so.'
        )
    return ""


def _enforce_entity_constraint(query: str, request: str, *, surface: SearchSurface) -> str:
    """Keep a formulated query on the user's named technology."""

    if not _HASHICORP_TERRAFORM.search(request):
        return query
    query = re.sub(r"\bterraforming\b", "Terraform", query, flags=re.IGNORECASE)
    if surface == "web":
        # Vendor documentation already disambiguates the product. An academic
        # Boolean/IaC wrapper can bury the exact command or page being sought.
        if re.search(r"\b(?:hashicorp|infrastructure\s+as\s+code|iac)\b", query, re.IGNORECASE):
            return query
        if _HASHICORP_TERRAFORM.search(query):
            return f"HashiCorp {query}"[:240]
        return f"HashiCorp Terraform {query}"[:240]
    if re.search(r"\b(?:infrastructure\s+as\s+code|iac)\b", query, re.IGNORECASE):
        return query
    constrained = f'"Terraform" AND "infrastructure as code" AND ({query})'
    return constrained[:240]


_ACADEMIC_QUERY_SYSTEM = (
    "Turn the research request into ONE compact boolean search query for an "
    "academic search engine. Infer the actual topic, correct obvious spelling "
    "mistakes, and ignore conversational filler and instruction verbs. Use 2 "
    "or 3 AND-joined concept groups, quoted phrases for multiword terms, and "
    "OR for established synonyms. Preserve exact paper, framework, method, "
    "dataset and author names. Use English academic vocabulary unless the "
    "topic is language-bound. Requested answer fields such as year, venue, "
    "authors, method, sample, findings or limitations are output requirements, "
    "not search concepts. Never add them to the query unless the user explicitly "
    "uses one as an eligibility constraint, for example papers published since "
    "2024 or studies using a named method. Stay under 180 characters. Never copy the user "
    "message verbatim merely because it is usable. Respond with the query "
    "only, without prose or code fences."
)

_WEB_QUERY_SYSTEM = (
    "Write ONE concise web-search query that retrieves the strongest pages "
    "for the research request. Infer the actual intent and entities, correct "
    "obvious spelling mistakes, remove conversational filler and instruction "
    "verbs, and add the decisive qualifier such as official documentation, "
    "current guidance, date range, standard, or source type when required. "
    "Use English unless the topic is language- or country-bound. Preserve "
    "exact product, paper, framework and author names. Never copy the user "
    "message verbatim merely because it is usable. Stay under 180 characters. "
    "Respond with the query only, without prose or code fences."
)


def _clean_query(value: str) -> str:
    query = value.strip().strip("`").strip()
    if query.lower().startswith("query:"):
        query = query[6:].strip()
    if not query or "\n" in query or len(query) > 240:
        return ""
    return query


def query_requires_formulation(query: str, request: str) -> bool:
    """Reject empty, conversational or near-verbatim provider queries.

    The tool router is itself allowed to formulate a good query. This guard
    prevents its failure mode from leaking a lightly re-punctuated user
    message to an external search provider while avoiding a second model call
    for already compact, intentional queries.
    """

    query = _clean_query(query)
    if not query:
        return True
    if _INTERNAL_CONTROL_QUERY.fullmatch(query):
        return True
    if _CONVERSATIONAL_QUERY.search(query):
        return True
    # A provider can occasionally answer the research question instead of
    # writing a query. Citation markers are a reliable signal for that failure
    # mode and must never be forwarded to OpenAlex or the web-search provider.
    if re.search(r"\[W\d+\]|\[web:[a-f0-9]{16}\]|\[[a-z0-9.-]+\]", query, re.IGNORECASE):
        return True

    def normalize(value: str) -> str:
        return " ".join(re.findall(r"[\w-]+", value.casefold()))

    normalized_query = normalize(query)
    normalized_request = normalize(request)
    if not normalized_query:
        return True
    if len(normalized_query.split()) > 24:
        return True
    query_terms = set(normalized_query.split()) - {"and", "or", "not"}
    if query_terms and query_terms <= _INSTRUCTION_ONLY_TERMS:
        return True
    if _INTERNAL_ENVELOPE_MARKER.search(request):
        semantic_request_terms = set(normalize(_strip_internal_context_markers(request)).split())
        leaked_control_terms = (
            query_terms & _INTERNAL_CONTEXT_QUERY_TERMS
        ) - semantic_request_terms
        if leaked_control_terms:
            return True
    # Conversation-control words describe how the current turn changes the
    # prior request. They are never scholarly concepts. Rejecting them here
    # prevents novice corrections such as "nein stopp, nur das RAG Paper"
    # from turning into a provider query like `"RAG" AND "nein"`.
    if query_terms & _QUERY_DISCOURSE_TERMS:
        return True
    if _REPORTING_FIELD_REQUEST.search(request) and query_terms & _REPORTING_FIELD_TERMS:
        return True
    if normalized_query == normalized_request:
        return True
    # Catch cosmetic rewrites such as dropping punctuation or one filler word.
    # Short named-entity queries intentionally remain exempt because their
    # only useful formulation may be the exact paper or framework name.
    if len(normalized_query.split()) >= 6 and normalized_request:
        similarity = SequenceMatcher(None, normalized_query, normalized_request).ratio()
        if similarity >= 0.84:
            return True
    return False


def formulate_search_query(
    request: str,
    pool: LLMPool,
    *,
    surface: SearchSurface,
    context: str = "",
) -> str:
    """Create a provider-ready query from intent, never a raw-message fallback.

    ``context`` is reserved for short conversation disambiguation and is
    labelled as data so a previous message cannot redefine the query-writer's
    instructions. If model formulation fails, the deterministic fallback
    extracts distinctive topic terms instead of forwarding the utterance.
    """

    semantic_request = _strip_internal_context_markers(request)
    semantic_context = _strip_internal_context_markers(context)
    prompt = f"Research request:\n{semantic_request[:4_000]}"
    if disambiguation := _entity_disambiguation(semantic_request):
        prompt += f"\n\nNamed-entity constraint:\n{disambiguation}"
    if semantic_context:
        prompt += f"\n\nAdditional evidence context:\n{semantic_context[:4_000]}"
    system = _ACADEMIC_QUERY_SYSTEM if surface == "academic" else _WEB_QUERY_SYSTEM
    try:
        response = pool.complete(
            TaskType.CHAT,
            system=system,
            prompt=prompt,
            max_tokens=120,
        )
        if (query := _clean_query(response.text)) and not query_requires_formulation(
            query, f"{request}\n{context}"
        ):
            return _enforce_entity_constraint(query, semantic_request, surface=surface)
    except Exception:  # noqa: BLE001 - retrieval keeps a topic-only fallback
        pass
    fallback_input = semantic_request
    if semantic_context:
        # Elliptical follow-ups such as "compare those two" contain no topic
        # by themselves. The deterministic fallback must therefore retain the
        # named entities from the recent conversation instead of searching for
        # instruction verbs such as "compare" or "vergleiche".
        fallback_input = f"{semantic_request}\n{semantic_context[-3_000:]}"
    fallback = _heuristic_query(fallback_input)
    if surface == "web":
        # The web fallback uses only attested request terms. Fixed routing
        # angles belong to the query-writer instructions, not provider terms.
        fallback = _heuristic_query(semantic_request)
        fallback = re.sub(r"\s+AND\s+", " ", fallback)
        if _HASHICORP_TERRAFORM.search(semantic_request):
            commands = re.findall(r"\b(?:plan|apply)\b", semantic_request, re.IGNORECASE)
            if commands:
                fallback = (
                    "HashiCorp Terraform "
                    + " ".join(dict.fromkeys(command.lower() for command in commands))
                    + " documentation"
                )
    return _enforce_entity_constraint(fallback, semantic_request, surface=surface)
