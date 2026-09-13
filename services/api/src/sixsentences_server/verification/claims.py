"""Decompose an answer into atomic claims and verify each against its evidence.

A generated answer cites sources by id (e.g. ``[W123]``). Each sentence that
carries a citation is an evidence-backed claim; it is checked for entailment
against the cited sources' text. Sentences without a citation are framing, not
claims, and are skipped. The report says how many cited claims are actually
supported vs. flagged — the number that matters for trusting the answer.

Sentence-level decomposition is the v1; finer atomic-claim decomposition (an LLM
step, +12-18 F1 in the literature) is a later refinement behind this interface.
"""

import re
from dataclasses import dataclass

from sixsentences_server.verification.nli import (
    EVIDENCE_CHARS,
    EntailmentChecker,
    EntailmentVerdict,
    Support,
)

_SENTENCE = re.compile(r"[^.!?]+(?:[.!?]+(?:\s*CITATIONTOKEN\d+END)*|$)")
_CLAIM_BLOCK = re.compile(r"\n[ \t]*\n|\n(?=[ \t]*(?:[-*+][ \t]+|\d+[.)][ \t]+))")
_URL_TOKEN = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
_ID_PATTERN = re.compile(r"W\d+")
_CITATION_TOKEN = re.compile(r"\[([^\[\]]+)\]")
_MIN_CLAIM = 20  # ignore trivially short fragments
_SYSTEM_ACTION_CLAIM = re.compile(
    r"\b(?:i|we)\s+(?:have\s+)?(?:opened|saved|loaded|displayed|marked|"
    r"highlighted)\b|\b(?:ich|wir)\s+(?:habe[n]?|hatte[n]?)\b.{0,100}\b"
    r"(?:geöffnet|geoeffnet|gespeichert|geladen|angezeigt|markiert|"
    r"hervorgehoben)\b|\b(?:open|opened|geöffnet|geoeffnet)\s+(?:it|the\s+paper|"
    r"das\s+paper|es)\s+(?:here|there|right|hier|rechts)\b",
    re.IGNORECASE,
)


@dataclass
class ClaimVerdict:
    claim: str
    support: Support
    reason: str
    evidence_ids: list[str]


@dataclass
class ClaimReport:
    verdicts: list[ClaimVerdict]

    @property
    def checked(self) -> int:
        return len(self.verdicts)

    @property
    def supported(self) -> int:
        return sum(1 for v in self.verdicts if v.support is Support.SUPPORTED)

    @property
    def flagged(self) -> int:
        return sum(1 for v in self.verdicts if v.support is not Support.SUPPORTED)


def split_claims(text: str) -> list[str]:
    # Neither citation domains nor literal URLs contain prose sentence
    # boundaries. Preserve both exactly before decomposing the answer.
    citations: list[str] = []
    urls: list[str] = []

    def protect(match: re.Match[str]) -> str:
        citations.append(match.group(0))
        return f"CITATIONTOKEN{len(citations) - 1}END"

    def protect_url(match: re.Match[str]) -> str:
        raw = match.group(0)
        url = raw.rstrip(".!?,;:)")
        urls.append(url)
        return f"URLTOKEN{len(urls) - 1}END" + raw[len(url) :]

    protected = _CITATION_TOKEN.sub(protect, text)
    protected = _URL_TOKEN.sub(protect_url, protected)
    claims: list[str] = []
    # Source lists often have no terminal full stops. Keep each list item or
    # paragraph distinct instead of attaching its citations to the next item.
    for block in _CLAIM_BLOCK.split(protected):
        for sentence in _SENTENCE.findall(block):
            restored = sentence
            for index, url in enumerate(urls):
                restored = restored.replace(f"URLTOKEN{index}END", url)
            for index, citation in enumerate(citations):
                restored = restored.replace(f"CITATIONTOKEN{index}END", citation)
            if len(restored.strip()) >= _MIN_CLAIM:
                claims.append(restored.strip())
    return claims


def _claim_evidence(cited: list[str], evidence_by_id: dict[str, str]) -> str:
    """Share the verifier's bounded premise window across every cited source."""

    if len(cited) == 1:
        return evidence_by_id[cited[0]][:EVIDENCE_CHARS]
    headers = [f"[{source_id}]\n" for source_id in cited]
    overhead = sum(len(header) for header in headers) + 2 * (len(cited) - 1)
    available = EVIDENCE_CHARS - overhead
    if available < len(cited):
        # The callers retain the answer and mark verification unavailable.
        # Never silently drop later cited sources to make an oversized set fit.
        raise ValueError("cited source set exceeds the verifier evidence window")
    share, extra = divmod(available, len(cited))
    return "\n\n".join(
        header + evidence_by_id[source_id][: share + (index < extra)]
        for index, (source_id, header) in enumerate(zip(cited, headers, strict=True))
    )


def verify_answer(
    answer: str,
    evidence_by_id: dict[str, str],
    checker: EntailmentChecker,
) -> ClaimReport:
    """Check every cited sentence of `answer` against its cited evidence."""
    pending: list[tuple[str, list[str], str]] = []
    canonical_ids = {source_id.casefold(): source_id for source_id in evidence_by_id}
    for claim in split_claims(answer):
        # Reader, Library and workspace state is established by trusted tool
        # results, not by the cited paper text. Sending statements such as
        # "I opened it here [W1]" to the scientific entailment model produces
        # a false unsupported warning even though the UI action succeeded.
        if _SYSTEM_ACTION_CLAIM.search(claim):
            continue
        # Paper citations and opened web pages use the same bracket contract:
        # ``[W123]`` for corpus works and ``[docs.example.org]`` for web
        # evidence. Only exact keys from the supplied evidence map enter the
        # verifier, so arbitrary bracket text can never masquerade as a source.
        tokens = [match.strip() for match in _CITATION_TOKEN.findall(claim)]
        ids = tokens or _ID_PATTERN.findall(claim)
        cited = list(
            dict.fromkeys(
                canonical_ids[source_id.casefold()]
                for source_id in ids
                if source_id.casefold() in canonical_ids
            )
        )
        if not cited:
            continue  # a framing sentence, not an evidence-backed claim
        evidence = _claim_evidence(cited, evidence_by_id)
        pending.append((claim, cited, evidence))

    batch_check = getattr(checker, "check_many", None)
    if len(pending) >= 3 and callable(batch_check):
        checked = batch_check([(claim, evidence) for claim, _, evidence in pending])
    else:
        checked = [checker.check(claim, evidence) for claim, _, evidence in pending]
    if len(checked) < len(pending):
        checked.extend(
            EntailmentVerdict(label=Support.NEUTRAL, reason="verifier result missing")
            for _ in range(len(pending) - len(checked))
        )
    verdicts = [
        ClaimVerdict(
            claim=claim,
            support=verdict.label,
            reason=verdict.reason,
            evidence_ids=cited,
        )
        for (claim, cited, _), verdict in zip(pending, checked, strict=False)
    ]
    return ClaimReport(verdicts=verdicts)
