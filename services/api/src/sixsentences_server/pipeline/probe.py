"""Post-hoc probe: why is a given paper in, out, or absent from a run?

Deterministic forensics over the persisted trail (source records, screening
ledger, acquisition ledger, corpus index, protocol) — no LLM involved. This
answers the question every supervisor and reviewer asks: "why is Miller 2021
not in your review?" with an audit-grade trail instead of a shrug.
"""

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.core.db import (
    DocumentRow,
    Run,
    ScreeningDecisionRow,
    SourceRecordRow,
    WorkRow,
)
from sixsentences_server.core.models import ReviewProtocol, WorkRecord
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus

_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)
_OPENALEX_RE = re.compile(r"\bW\d{4,}\b")
_NONWORD = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")
# tokens of the boolean query language that are not search terms
_QUERY_NOISE = {"and", "or", "not", "near"}

_TITLE_MATCH_THRESHOLD = 0.82


@dataclass
class ProbeStep:
    stage: str
    detail: str
    outcome: str = ""  # short machine-ish tag for the UI badge


@dataclass
class ProbeResult:
    status: str
    resolved: dict[str, Any] | None = None
    steps: list[ProbeStep] = field(default_factory=list)
    suggestion: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "resolved": self.resolved,
            "steps": [
                {"stage": s.stage, "detail": s.detail, "outcome": s.outcome} for s in self.steps
            ],
            "suggestion": self.suggestion,
        }


def _norm_title(title: str) -> str:
    return _WS.sub(" ", _NONWORD.sub(" ", (title or "").lower())).strip()


def _norm_doi(text: str) -> str | None:
    match = _DOI_RE.search(text or "")
    if not match:
        return None
    return match.group(0).rstrip(".,;)").lower()


def _query_terms(query_string: str) -> list[str]:
    """Extract the searchable terms of a boolean query: quoted phrases plus
    bare words, operators and parentheses dropped. A heuristic view — good
    enough to say which terms a candidate paper does not contain."""
    phrases = re.findall(r'"([^"]+)"', query_string or "")
    rest = re.sub(r'"[^"]*"', " ", query_string or "")
    words = [
        w
        for w in re.findall(r"[\w\*]+", rest.lower())
        if w not in _QUERY_NOISE and not w.isdigit() and len(w.strip("*")) > 1
    ]
    return [p.lower() for p in phrases] + words


def _term_in_text(term: str, text: str) -> bool:
    if term.endswith("*"):
        stem = term.rstrip("*")
        return bool(re.search(rf"\b{re.escape(stem)}\w*", text))
    return term in text


def _title_similarity(target_norm: str, candidate_norm: str) -> float:
    """Similarity that honors partial titles: typing the head of a long title
    (before the subtitle colon) is a match, not a near-miss."""
    if len(target_norm) >= 15 and target_norm in candidate_norm:
        return 1.0
    return SequenceMatcher(None, target_norm, candidate_norm).ratio()


def _resolved(work_id: str, title: str, doi: str | None, year: int | None) -> dict[str, Any]:
    return {"work_id": work_id, "title": title, "doi": doi, "year": year}


def probe_run(
    session: Session,
    run: Run,
    protocol: ReviewProtocol | None,
    corpus: DuckDBCorpus,
    query_text: str,
    *,
    resolve_external: Any | None = None,  # OpenAlexClient-like, optional
) -> dict[str, Any]:
    """Trace one paper (DOI, OpenAlex id, or title) through a run's record."""
    text = (query_text or "").strip()
    doi = _norm_doi(text)
    oa_match = _OPENALEX_RE.search(text)
    oa_id = oa_match.group(0) if oa_match else None
    title_query = None if (doi or oa_id) else text

    # -- 1) among the run's identified records? -----------------------------
    pairs = session.execute(
        select(SourceRecordRow, WorkRow)
        .join(WorkRow, WorkRow.id == SourceRecordRow.work_id)
        .where(SourceRecordRow.run_id == run.id)
    ).all()
    by_work: dict[str, list[SourceRecordRow]] = {}
    works: dict[str, WorkRow] = {}
    for source_row, work_row in pairs:
        works[work_row.id] = work_row
        by_work.setdefault(work_row.id, []).append(source_row)

    hit: WorkRow | None = None
    for work in works.values():
        if oa_id and work.id == oa_id:
            hit = work
            break
        if doi and (work.doi or "").lower().endswith(doi):
            hit = work
            break
    if hit is None and title_query:
        target = _norm_title(title_query)
        best_ratio = 0.0
        for work in works.values():
            ratio = _title_similarity(target, _norm_title(work.title))
            if ratio > best_ratio:
                best_ratio, hit = ratio, work
        if best_ratio < _TITLE_MATCH_THRESHOLD:
            hit = None

    if hit is not None:
        return _trace_found(session, run, hit, by_work.get(hit.id, [])).as_dict()

    # -- 2) not in the run: is it in the available scholarly material? ------
    candidates = corpus.lookup(work_id=oa_id, doi=doi, title=title_query)
    record: WorkRecord | None = None
    if candidates and title_query:
        target = _norm_title(title_query)
        scored = [(_title_similarity(target, _norm_title(c.title)), c) for c in candidates]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if scored and scored[0][0] >= _TITLE_MATCH_THRESHOLD:
            record = scored[0][1]
    elif candidates:
        record = candidates[0]

    if record is not None:
        return _explain_not_retrieved(run, protocol, record).as_dict()

    # -- 3) not even in the index: known outside? ---------------------------
    result = ProbeResult(status="not_found")
    result.steps.append(
        ProbeStep(
            "index",
            "Not in the scholarly sources this run searched.",
            "missing",
        )
    )
    if resolve_external is not None and (doi or oa_id):
        external = resolve_external.get_work(f"doi:{doi}" if doi else str(oa_id))
        if external is not None:
            result.status = "outside_index"
            result.resolved = _resolved(external.id, external.title, external.doi, external.year)
            result.steps.append(
                ProbeStep(
                    "openalex",
                    f"OpenAlex knows this work ({external.year or 'year unknown'}, "
                    f"{external.venue or 'venue unknown'}); it lies outside the "
                    "retrieved source set.",
                    "found_externally",
                )
            )
            result.suggestion = (
                "Re-run with the live index enabled, or widen the year window; "
                "the searched source set does not contain this work."
            )
            return result.as_dict()
    result.suggestion = (
        "Check the spelling or paste the DOI; neither the run nor the index has a matching record."
    )
    return result.as_dict()


def _trace_found(
    session: Session, run: Run, work: WorkRow, sources: list[SourceRecordRow]
) -> ProbeResult:
    result = ProbeResult(status="identified_not_screened")
    result.resolved = _resolved(work.id, work.title, work.doi, work.year)
    source_names = sorted({s.source for s in sources}) or ["unknown"]
    result.steps.append(
        ProbeStep(
            "identification",
            f"Identified by the search via {', '.join(source_names)}.",
            "identified",
        )
    )
    if bool((work.payload or {}).get("is_retracted")):
        result.steps.append(
            ProbeStep("integrity", "Flagged as retracted by the integrity check.", "retracted")
        )

    rows = session.scalars(
        select(ScreeningDecisionRow)
        .where(
            ScreeningDecisionRow.run_id == run.id,
            ScreeningDecisionRow.work_id == work.id,
        )
        .order_by(ScreeningDecisionRow.id)
    ).all()
    if not rows:
        result.steps.append(
            ProbeStep(
                "screening",
                "Never screened in this run (screening was off, capped, or "
                "still pending when the run stopped).",
                "not_screened",
            )
        )
        return result

    votes = [
        r
        for r in rows
        if ":" in r.reviewer
        and r.reviewer.split(":", 1)[0] not in ("adjudicator", "fulltext", "human")
    ]
    if votes:
        tally = {"include": 0, "exclude": 0, "unsure": 0}
        for vote in votes:
            tally[vote.verdict] = tally.get(vote.verdict, 0) + 1
        result.steps.append(
            ProbeStep(
                "screening",
                f"{len(votes)} independent reviewers voted: "
                f"{tally['include']} include, {tally['exclude']} exclude, "
                f"{tally['unsure']} unsure.",
                "voted",
            )
        )
    for row in rows:
        head = row.reviewer.split(":", 1)[0]
        if head == "adjudicator":
            result.steps.append(
                ProbeStep(
                    "adjudication",
                    f"Reviewers disagreed; the adjudicator ruled {row.verdict}: "
                    f"{row.reason or 'no reason recorded'}",
                    row.verdict,
                )
            )
        elif head == "fulltext":
            result.steps.append(
                ProbeStep(
                    "full_text",
                    f"Full-text screening ruled {row.verdict}: "
                    f"{row.reason or 'no reason recorded'}",
                    row.verdict,
                )
            )
        elif head == "human":
            result.steps.append(
                ProbeStep(
                    "human_review",
                    f"A human reviewer recorded {row.verdict}: {row.reason or 'no reason given'}",
                    row.verdict,
                )
            )

    # effective decision: human beats model, later stage beats earlier
    effective = rows[0]
    for row in rows:
        if row.reviewer.startswith("human:") or not effective.reviewer.startswith("human:"):
            effective = row
    result.status = effective.verdict
    reason = effective.reason or "no reason recorded"
    result.steps.append(
        ProbeStep(
            "verdict",
            f"Effective decision: {effective.verdict} "
            f"(by {'human' if effective.reviewer.startswith('human:') else 'model'}). {reason}",
            effective.verdict,
        )
    )

    document = session.scalar(
        select(DocumentRow).where(DocumentRow.run_id == run.id, DocumentRow.work_id == work.id)
    )
    if document is not None:
        result.steps.append(
            ProbeStep(
                "acquisition",
                f"Full text {document.status} (source: {document.source or 'n/a'}).",
                document.status,
            )
        )
    return result


def _explain_not_retrieved(
    run: Run, protocol: ReviewProtocol | None, record: WorkRecord
) -> ProbeResult:
    result = ProbeResult(status="in_index_not_retrieved")
    result.resolved = _resolved(record.id, record.title, record.doi, record.year)
    result.steps.append(
        ProbeStep(
            "index",
            f"The scholarly catalog contains this work "
            f"({record.year or 'year unknown'}, {record.venue or 'venue unknown'}), "
            "but the executed searches never returned it.",
            "in_index",
        )
    )
    if protocol is None:
        result.suggestion = "The run has no protocol to analyze the query against."
        return result

    if protocol.year_from and record.year and record.year < protocol.year_from:
        result.steps.append(
            ProbeStep(
                "filters",
                f"Published {record.year}, outside the protocol's year window "
                f"(from {protocol.year_from}).",
                "year_window",
            )
        )
        result.suggestion = "Widen the year window to reach this work."
        return result
    if protocol.year_to and record.year and record.year > protocol.year_to:
        result.steps.append(
            ProbeStep(
                "filters",
                f"Published {record.year}, outside the protocol's year window "
                f"(until {protocol.year_to}).",
                "year_window",
            )
        )
        result.suggestion = "Widen the year window to reach this work."
        return result

    haystack = f"{record.title} {record.abstract or ''}".lower()
    terms = _query_terms(protocol.query_string)
    missing = [t for t in terms if not _term_in_text(t, haystack)]
    matched = [t for t in terms if t not in missing]
    if not matched:
        detail = (
            f"None of the query terms ({', '.join(missing[:8]) or 'none'}) appear "
            "in its title or abstract — the boolean query cannot reach it."
        )
    else:
        detail = (
            f"Query terms present: {', '.join(matched[:8])}. Missing: "
            f"{', '.join(missing[:8]) or 'none'}. Depending on the boolean "
            "structure, the missing terms are what kept it out."
        )
    result.steps.append(ProbeStep("query", detail, "query_miss"))
    result.suggestion = (
        "Broaden the query (or add one of the missing terms as an OR branch) "
        "and re-run; the record is in the index and would then be screened."
    )
    return result
