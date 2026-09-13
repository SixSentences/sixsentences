"""Pre-drafted methods paragraph with the run's real numbers.

The north star is "citable search": a run should be documented rigorously
enough to be referenced in a paper's methods section. This assembles that
paragraph from what the run already produced — corpus version, synthesised
query, PRISMA flow, screening method, capture-recapture completeness and recall
estimates, and integrity checks — so every figure is real and the run is
reproducible from the text. Built entirely from the persisted run + audit
events, so it works identically right after a run and later via the API.
"""

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

import sixsentences_server
from sixsentences_server.core.db import ProtocolRow, Run, RunEvent
from sixsentences_server.screening.frameworks import review_method


class MethodsFacts(BaseModel):
    date: str = ""
    corpus_version: str = "(unversioned)"
    synthesized_by: str = "heuristic"
    query_string: str = ""
    queries_executed: int = 1
    stopped_because: str = ""
    identified: int = 0
    duplicates: int = 0
    screened: int = 0
    included: int = 0
    excluded: int = 0
    unsure: int = 0
    ensemble_size: int = 0
    adjudicated: int = 0
    quotes_verified: int = 0
    coverage_method: str = "undetermined"
    coverage_completeness: float = 0.0
    coverage_ci_low: float = 0.0
    coverage_ci_high: float = 0.0
    recall_method: str = "undetermined"
    recall_value: float = 0.0
    recall_ci_low: float = 0.0
    recall_ci_high: float = 0.0
    recall_certified: bool = False
    retracted_flagged: int = 0
    cites_retracted: int = 0
    reports_sought: int = 0
    fulltext_retrieved: int = 0
    reports_not_retrieved: int = 0
    fulltext_parsed: int = 0
    fulltext_by_basis: dict[str, int] = {}
    reports_assessed: int = 0
    fulltext_excluded: int = 0
    studies_included: int = 0
    fulltext_quotes_verified: int = 0
    tool_version: str = sixsentences_server.__version__
    review_method: str = "prisma"


def collect_methods_facts(session: Session, run: Run) -> MethodsFacts:
    events = session.scalars(
        select(RunEvent).where(RunEvent.run_id == run.id).order_by(RunEvent.id)
    ).all()
    by_event = {e.event: e.payload for e in events}  # last occurrence wins
    prisma = run.prisma or {}
    protocol = session.get(ProtocolRow, run.protocol_id) if run.protocol_id else None
    payload = protocol.payload if protocol else {}
    saturation = by_event.get("saturation_check", {})
    coverage = by_event.get("coverage_estimated", {})
    recall = by_event.get("screening_recall_certified", {})
    screening = by_event.get("screening_done", {})
    integrity = by_event.get("integrity_signals_done", {})
    retraction = by_event.get("retraction_check_done", {})
    acquisition = by_event.get("acquisition_done", {})
    fulltext = by_event.get("fulltext_screening_done", {})
    stamp = run.finished_at or run.created_at
    return MethodsFacts(
        date=stamp.strftime("%Y-%m-%d") if isinstance(stamp, datetime) else "",
        corpus_version=run.corpus_version or "(unversioned)",
        synthesized_by=str(payload.get("synthesized_by", "heuristic")),
        query_string=str(payload.get("query_string", "")),
        queries_executed=int(saturation.get("queries_executed", 1)),
        stopped_because=str(saturation.get("stopped_because", "")),
        identified=int(prisma.get("records_identified", 0)),
        duplicates=int(prisma.get("duplicates_removed", 0)),
        screened=int(prisma.get("records_screened", 0)),
        included=int(prisma.get("included", 0)),
        excluded=int(prisma.get("records_excluded", 0)),
        unsure=int(prisma.get("records_unsure", 0)),
        ensemble_size=int(screening.get("ensemble_size", 0)),
        adjudicated=int(screening.get("adjudicated", 0)),
        quotes_verified=int(screening.get("quotes_verified", 0)),
        coverage_method=str(coverage.get("method", "undetermined")),
        coverage_completeness=float(coverage.get("completeness", 0.0)),
        coverage_ci_low=float(coverage.get("ci_low", 0.0)),
        coverage_ci_high=float(coverage.get("ci_high", 0.0)),
        recall_method=str(recall.get("method", "undetermined")),
        recall_value=float(recall.get("estimated_recall", 0.0)),
        recall_ci_low=float(recall.get("ci_low", 0.0)),
        recall_ci_high=float(recall.get("ci_high", 0.0)),
        recall_certified=bool(recall.get("certified", False)),
        retracted_flagged=int(retraction.get("flagged", 0)),
        cites_retracted=int(integrity.get("cites_retracted", 0)),
        reports_sought=int(acquisition.get("sought", 0)),
        fulltext_retrieved=int(acquisition.get("retrieved", 0)),
        reports_not_retrieved=int(acquisition.get("not_retrieved", 0)),
        fulltext_parsed=int(acquisition.get("parsed", 0)),
        fulltext_by_basis=dict(acquisition.get("by_legal_basis", {})),
        reports_assessed=int(fulltext.get("assessed", 0)),
        fulltext_excluded=int(fulltext.get("excluded", 0)),
        studies_included=int(fulltext.get("studies_included", 0)),
        fulltext_quotes_verified=int(fulltext.get("quotes_verified", 0)),
        tool_version=sixsentences_server.__version__,
        review_method=str((run.config or {}).get("review_method") or "prisma"),
    )


def render_methods_paragraph(f: MethodsFacts) -> str:
    unique = f.identified - f.duplicates
    query = f.query_string if len(f.query_string) <= 200 else f.query_string[:200] + " …"
    sentences: list[str] = []

    on = f" on {f.date}" if f.date else ""
    sentences.append(
        f"The search was executed{on} corpus-first against the SixSentences_ corpus "
        f"(version {f.corpus_version}); the verbatim query and per-search details are given "
        f"in the PRISMA-S search appendix."
    )
    protocol_origin = (
        "was supplied by the researcher"
        if f.synthesized_by == "user"
        else "was frozen before retrieval"
    )
    sentences.append(f'The review protocol {protocol_origin} and used the boolean query "{query}".')
    expansion = (
        f" using an exhaustive query-expansion loop ({f.queries_executed} queries executed, "
        f"stopped on {f.stopped_because or 'saturation'})"
        if f.queries_executed > 1
        else ""
    )
    sentences.append(
        f"Retrieval{expansion} identified {f.identified} records and removed {f.duplicates} "
        f"duplicates, leaving {unique} unique records."
    )

    if f.screened:
        if f.ensemble_size > 1:
            adj = " with independent adjudication of conflicts" if f.adjudicated else ""
            reviewer = (
                f"an ensemble of {f.ensemble_size} independent model reviewers "
                f"(any-include recall rule{adj}; single-model exclusions were not trusted)"
            )
        else:
            reviewer = "an automated screener"
        quotes = (
            f", and {f.quotes_verified} decisions carry a verbatim quote verified against "
            f"the source abstract"
            if f.quotes_verified
            else ""
        )
        sentences.append(
            f"Titles and abstracts were screened by {reviewer}; of {f.screened} records "
            f"screened, {f.included} were included, {f.excluded} excluded and {f.unsure} marked "
            f"unsure for human review{quotes}."
        )

    if f.reports_sought:
        bases = (
            ", ".join(f"{n} {b.replace('_', ' ')}" for b, n in sorted(f.fulltext_by_basis.items()))
            or "none"
        )
        sentences.append(
            f"Full text was sought for {f.reports_sought} report(s) retained after "
            f"title/abstract screening, including records still marked unsure; "
            f"{f.fulltext_retrieved} were retrieved from open-access sources (by legal basis: "
            f"{bases}; {f.fulltext_parsed} parsed to text) and {f.reports_not_retrieved} could "
            f"not be retrieved. Only open-access full texts were fetched, each recorded with its "
            f"legal basis."
        )

    if f.reports_assessed:
        quotes = (
            " each carrying a verbatim quote verified against the full text,"
            if f.fulltext_quotes_verified
            else ""
        )
        sentences.append(
            f"Full-text eligibility was then assessed for {f.reports_assessed} report(s) by "
            f"an automated full-text reviewer;{quotes} {f.fulltext_excluded} were excluded "
            f"with reasons, "
            f"leaving {f.studies_included} studies included in the review."
        )

    stats: list[str] = []
    if f.coverage_method == "chao2":
        stats.append(
            f"estimated search completeness (capture-recapture, Chao2) was "
            f"{f.coverage_completeness:.1%} (95% CI {f.coverage_ci_low:.1%} to "
            f"{f.coverage_ci_high:.1%})"
        )
    if f.recall_method == "chao2":
        cert = "certified" if f.recall_certified else "not certified"
        stats.append(
            f"estimated screening recall was {f.recall_value:.1%} "
            f"(95% CI {f.recall_ci_low:.1%} to {f.recall_ci_high:.1%}, {cert})"
        )
    if stats:
        joined = "; ".join(stats)
        sentences.append(joined[0].upper() + joined[1:] + ".")

    integrity = f"Integrity screening flagged {f.retracted_flagged} retracted work(s)"
    if f.cites_retracted:
        integrity += f" and {f.cites_retracted} work(s) citing a retracted paper"
    sentences.append(integrity + ".")

    framework = review_method(f.review_method)
    sentences.append(
        f"The review workflow follows {framework['label']} guidance "
        f"({framework['screening']}); the selection flow is reported with PRISMA 2020. "
        f"Automation tool SixSentences_ {f.tool_version}."
    )
    return " ".join(sentences)


def render_methods(session: Session, run: Run) -> str:
    return render_methods_paragraph(collect_methods_facts(session, run))
