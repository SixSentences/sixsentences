"""Submission compliance: PRISMA 2020 checklist and preregistration text.

The checklist maps the 27 PRISMA 2020 items to what the run already
provides (flow, search documentation, selection ledger, ...) versus what
only the author can write (rationale, discussion). The preregistration
renders the frozen protocol as an OSF/PROSPERO-style document — register
the plan BEFORE running the full review.
"""

from typing import Any

from sixsentences_server.core.models import PrismaCounts, ReviewProtocol

# (item, short label, covered) — covered=True means the run's artifacts
# satisfy the item's reporting substance; the note says which artifact.
_ITEMS: list[tuple[str, str, bool, str]] = [
    ("1", "Title identifies the report as a systematic review", False, "author"),
    ("2", "Abstract per the PRISMA abstract checklist", False, "author"),
    ("3", "Rationale for the review", False, "author"),
    ("4", "Objectives / research question", True, "protocol: research question"),
    ("5", "Eligibility criteria", True, "protocol: inclusion/exclusion criteria"),
    ("6", "Information sources", True, "search appendix: sources and dates"),
    ("7", "Full search strategies, verbatim", True, "search appendix (PRISMA-S)"),
    (
        "8",
        "Selection process (who screened, how)",
        True,
        "methods paragraph + decision ledger",
    ),
    (
        "9",
        "Data collection process",
        True,
        "evidence table (quote-verified extraction)",
    ),
    ("10", "Data items (variables sought)", True, "evidence table fields"),
    ("11", "Risk-of-bias assessment method", False, "author (not automated yet)"),
    ("12", "Effect measures", False, "author"),
    ("13", "Synthesis methods", False, "author (report draft can seed this)"),
    ("14", "Reporting bias assessment", False, "author"),
    ("15", "Certainty assessment", False, "author"),
    (
        "16",
        "Study selection results + flow diagram",
        True,
        "PRISMA flow (SVG/PNG export)",
    ),
    ("17", "Cited studies list", True, "bibliography exports (BibTeX/RIS/APA/IEEE)"),
    ("18", "Risk-of-bias results", False, "author"),
    ("19", "Individual study results", True, "evidence table"),
    ("20", "Synthesis results", False, "author (report draft can seed this)"),
    ("21", "Reporting bias results", False, "author"),
    ("22", "Certainty of evidence", False, "author"),
    ("23", "Discussion", False, "author"),
    (
        "24",
        "Registration and protocol",
        True,
        "preregistration export + frozen protocol",
    ),
    ("25", "Support / funding", False, "author"),
    ("26", "Competing interests", False, "author"),
    ("27", "Data availability", True, "reproducibility bundle (bundle.zip)"),
]


def build_checklist(prisma: PrismaCounts | None, has_extraction: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item, label, covered, note in _ITEMS:
        effective = covered
        if item in ("9", "10", "19") and not has_extraction:
            effective = False
            note = "run the evidence extraction to cover this"
        if item == "16" and (prisma is None or prisma.records_identified == 0):
            effective = False
            note = "no completed search yet"
        rows.append(
            {
                "item": item,
                "label": label,
                "status": "covered" if effective else "author",
                "note": note,
            }
        )
    return rows


def render_checklist_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# PRISMA 2020 checklist",
        "",
        "Item status: `covered` = the run's exports carry the substance;",
        "`author` = only you can write this part.",
        "",
        "| Item | Requirement | Status | Where / note |",
        "| --- | --- | --- | --- |",
    ]
    lines += [f"| {r['item']} | {r['label']} | {r['status']} | {r['note']} |" for r in rows]
    covered = sum(1 for r in rows if r["status"] == "covered")
    lines += ["", f"{covered} of {len(rows)} items carried by the run's artifacts."]
    return "\n".join(lines)


def render_preregistration(
    protocol: ReviewProtocol, *, corpus_version: str | None, question: str
) -> str:
    """OSF/PROSPERO-style preregistration of the frozen protocol."""
    inclusion = "\n".join(f"- {c}" for c in protocol.inclusion_criteria) or "- (none)"
    exclusion = "\n".join(f"- {c}" for c in protocol.exclusion_criteria) or "- (none)"
    years = (
        f"{protocol.year_from or 'open'} to {protocol.year_to or 'open'}"
        if (protocol.year_from or protocol.year_to)
        else "no restriction"
    )
    return "\n".join(
        [
            "# Systematic review preregistration",
            "",
            "## Research question",
            question,
            "",
            "## Eligibility criteria",
            "Inclusion:",
            inclusion,
            "",
            "Exclusion:",
            exclusion,
            "",
            f"Publication years: {years}."
            + (" Peer-reviewed sources only." if protocol.peer_reviewed_only else ""),
            "",
            "## Search strategy",
            "Boolean query (verbatim):",
            "```",
            protocol.query_string,
            "```",
            "Primary source: SixSentences_ scholarly retrieval record"
            + (f" (version {corpus_version})" if corpus_version else "")
            + ", with optional live OpenAlex layer, citation snowballing and",
            "uploaded database exports; every executed search is documented in",
            "the run's PRISMA-S appendix.",
            "",
            "## Screening plan",
            "Title/abstract screening by a cross-vendor model ensemble against",
            "the criteria above; disagreement goes to an adjudicator, unsure",
            "records to a human review queue. Every decision lands in an",
            "append-only ledger. Screening recall is estimated by",
            "capture-recapture (Chao2) with a preregistered target.",
            "",
            "## Data extraction plan",
            "Structured extraction per included study (population, method,",
            "sample size, outcomes, limitations by default), each value backed",
            "by a verbatim quote verified against the source text.",
            "",
            "## Deviations",
            "Any deviation from this protocol will be reported alongside the",
            "run's audit trail.",
        ]
    )
