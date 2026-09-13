"""Entailment engine for the claim-support firewall.

`EntailmentChecker` is the swap point. The default `LLMEntailmentChecker` reuses
the LLM pool on a cheap model (TaskType.CLAIM_VERIFICATION) — no extra
dependency, provider-agnostic. A dedicated NLI model (MiniCheck ≈ GPT-4 quality
at ~400× lower cost; DeBERTa-v3-NLI) is a later drop-in behind the same
protocol. The verdict is deliberately three-valued: an unclear or unrelated
evidence text is NEUTRAL, not SUPPORTED, so the firewall never upgrades a weak
claim.
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from sixsentences_server.llm.base import BudgetExceededError, LLMConfigError, TaskType
from sixsentences_server.llm.pool import LLMPool
from sixsentences_server.llm.providers import ProviderError

EVIDENCE_CHARS = 8000  # cap the evidence window per check
NLI_BATCH_SIZE = 4
# Native Gemini includes thinking in maxOutputTokens. The former 150-token
# visible-answer assumption can truncate verdict JSON after reasoning.
# These are reservation ceilings, not expected usage or fixed user charges.
NLI_SINGLE_OUTPUT_TOKENS = 1024
NLI_BATCH_OUTPUT_TOKENS = 2048


class Support(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"  # evidence contradicts or fails to establish the claim
    NEUTRAL = "neutral"  # evidence is unrelated / insufficient to judge


@dataclass
class EntailmentVerdict:
    label: Support
    reason: str


class EntailmentChecker(Protocol):
    def check(self, claim: str, evidence: str) -> EntailmentVerdict: ...


NLI_SYSTEM = (
    "You are a strict fact-checker. Decide whether the EVIDENCE supports the CLAIM. "
    "Judge ONLY from the evidence text — never use outside knowledge. Answer 'supported' only "
    "if the evidence clearly establishes the claim; 'unsupported' if it contradicts the claim "
    "or does not establish it; 'neutral' if the evidence is unrelated to the claim. Respond "
    'with strict JSON: {"label": "supported"|"unsupported"|"neutral", "reason": "<one sentence>"}'
)
NLI_BATCH_SYSTEM = (
    "You are a strict fact-checker. Evaluate every numbered CLAIM only against "
    "its paired EVIDENCE. Never use outside knowledge and never let evidence "
    "from one item support another. Return 'supported' only when the paired "
    "evidence clearly establishes the claim; 'unsupported' when it contradicts "
    "or fails to establish it; 'neutral' when it is unrelated or insufficient. "
    "Respond with one strict JSON object containing the verdicts in input order: "
    '{"verdicts": [{"index": 0, "label": "supported"|"unsupported"|"neutral", '
    '"reason": "<one sentence>"}]}'
)


def _typed_verdict(data: object) -> EntailmentVerdict | None:
    """Accept a typed domain verdict only, never coerce arbitrary JSON values."""

    if not isinstance(data, dict):
        return None
    label, reason = data.get("label"), data.get("reason")
    if not isinstance(label, str) or not isinstance(reason, str):
        return None
    try:
        return EntailmentVerdict(label=Support(label), reason=reason[:300])
    except ValueError:
        return None


def _parse(text: str) -> EntailmentVerdict:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    # A firewall must not upgrade on a parse/schema failure.
    return _typed_verdict(data) or EntailmentVerdict(
        label=Support.NEUTRAL,
        reason="unparseable verifier response",
    )


def _parse_batch(text: str, expected: int) -> list[EntailmentVerdict]:
    """Parse a batch response without ever upgrading a missing result."""

    neutral = EntailmentVerdict(label=Support.NEUTRAL, reason="unparseable verifier response")
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(raw)
    except ValueError:
        return [neutral for _ in range(expected)]
    if isinstance(data, dict):
        data = data.get("verdicts")
    else:
        return [neutral for _ in range(expected)]
    if not isinstance(data, list):
        return [neutral for _ in range(expected)]
    parsed = [neutral for _ in range(expected)]
    seen: set[int] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if type(index) is not int or index < 0 or index >= expected:
            continue
        if index in seen:
            parsed[index] = neutral
            continue
        seen.add(index)
        parsed[index] = _typed_verdict(item) or neutral
    return parsed


class LLMEntailmentChecker:
    def __init__(self, pool: LLMPool) -> None:
        self.pool = pool

    def check(self, claim: str, evidence: str) -> EntailmentVerdict:
        try:
            response = self.pool.complete_json(
                TaskType.CLAIM_VERIFICATION,
                system=NLI_SYSTEM,
                prompt=f"CLAIM:\n{claim}\n\nEVIDENCE:\n{evidence[:EVIDENCE_CHARS]}",
                max_tokens=NLI_SINGLE_OUTPUT_TOKENS,
            )
        except (BudgetExceededError, LLMConfigError, ProviderError):
            # Verification is a trust annotation around an already grounded
            # answer. A transient verifier outage must never discard that
            # answer or leave the turn running forever; it remains explicitly
            # unverified instead of being upgraded to supported.
            return EntailmentVerdict(label=Support.NEUTRAL, reason="verifier unavailable")
        return _parse(response.text)

    def check_many(self, items: list[tuple[str, str]]) -> list[EntailmentVerdict]:
        """Verify cited claims in bounded batches instead of serial model calls.

        Each claim keeps its own evidence envelope, so batching changes only
        transport overhead, not the trust boundary or the three-valued result.
        Provider and parse failures remain fail-closed as neutral verdicts.
        """

        verdicts: list[EntailmentVerdict] = []
        for offset in range(0, len(items), NLI_BATCH_SIZE):
            chunk = items[offset : offset + NLI_BATCH_SIZE]
            payload = [
                {
                    "index": index,
                    "claim": claim,
                    "evidence": evidence[:EVIDENCE_CHARS],
                }
                for index, (claim, evidence) in enumerate(chunk)
            ]
            try:
                response = self.pool.complete_json(
                    TaskType.CLAIM_VERIFICATION,
                    system=NLI_BATCH_SYSTEM,
                    prompt="ITEMS:\n" + json.dumps(payload, ensure_ascii=False),
                    max_tokens=max(
                        NLI_SINGLE_OUTPUT_TOKENS,
                        len(chunk) * (NLI_BATCH_OUTPUT_TOKENS // NLI_BATCH_SIZE),
                    ),
                )
            except (BudgetExceededError, LLMConfigError, ProviderError):
                verdicts.extend(
                    EntailmentVerdict(label=Support.NEUTRAL, reason="verifier unavailable")
                    for _ in chunk
                )
                continue
            verdicts.extend(_parse_batch(response.text, len(chunk)))
        return verdicts
