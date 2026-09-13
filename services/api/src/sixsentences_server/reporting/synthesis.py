"""Run report synthesis: everything a finished search produced, written up.

The report is the run's shareable artifact: an executive summary and thematic
synthesis written by the strong model (grounded in the included works, cited by
id), wrapped in deterministic facts the audit trail already guarantees — the
protocol, PRISMA counts, the included studies, grey-literature sources and the
PRISMA-S methods paragraph. The frontend renders it as a branded PDF.

The LLM part is best-effort: keyless or budget-tripped environments still get a
complete report with deterministic sections.
"""

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.core.db import (
    ProtocolRow,
    ReportRow,
    Run,
    ScreeningDecisionRow,
    WebSourceRow,
)
from sixsentences_server.core.models import WorkRecord
from sixsentences_server.core.textutil import strip_dashes
from sixsentences_server.llm.base import BudgetExceededError, LLMConfigError, TaskType
from sixsentences_server.llm.pool import LLMPool
from sixsentences_server.reporting.exports import works_for_run
from sixsentences_server.reporting.methods import render_methods

MAX_WORKS_IN_PROMPT = 15
ABSTRACT_CHARS = 400

REPORT_SYSTEM = (
    "You are the SixSentences_ research assistant writing the synthesis "
    "sections of a systematic literature search report. Ground every claim in "
    "the provided works and cite their ids in square brackets, e.g. "
    "[W2741809807]. One id per bracket pair: write [W1] [W2], never [W1, W2]. "
    "Be honest: if little or nothing was included, say what "
    "that means and what the likely cause is. Never invent works or findings. "
    "Never use em dashes. Respond with STRICT JSON only:\n"
    '{"executive_summary": "<120-180 words>", '
    '"key_findings": ["<one sentence each, 3-6 items>"], '
    '"themes": [{"title": "<short>", "body": "<80-140 words with citations>"}], '
    '"limitations": "<60-120 words>", '
    '"next_steps": ["<one imperative sentence each, 2-4 items>"]}\n'
    "2 to 4 themes. No prose outside the JSON."
)


def _final_verdicts(session: Session, run: Run) -> dict[str, ScreeningDecisionRow]:
    """Effective decision per work: human > later model pass > earlier."""
    rows = session.scalars(
        select(ScreeningDecisionRow)
        .where(
            ScreeningDecisionRow.run_id == run.id,
            ScreeningDecisionRow.org_id == run.org_id,
        )
        .order_by(ScreeningDecisionRow.id)
    ).all()
    final: dict[str, ScreeningDecisionRow] = {}
    for d in rows:
        prev = final.get(d.work_id)
        if (
            prev is None
            or d.reviewer.startswith("human:")
            or not prev.reviewer.startswith("human:")
        ):
            final[d.work_id] = d
    return final


def _work_entry(work: WorkRecord, decision: ScreeningDecisionRow | None) -> dict[str, Any]:
    return {
        "id": work.id,
        "title": work.title,
        "authors": work.authors[:4],
        "year": work.year,
        "venue": work.venue,
        "doi": work.doi,
        "cited_by_count": work.cited_by_count,
        "reason": decision.reason if decision else None,
    }


def _render_prompt_works(works: list[WorkRecord]) -> str:
    lines: list[str] = []
    for work in works[:MAX_WORKS_IN_PROMPT]:
        meta = ", ".join(str(x) for x in (work.year, work.venue) if x)
        header = f"[{work.id}] {work.title}" + (f" ({meta})" if meta else "")
        abstract = (work.abstract or "").strip()
        if len(abstract) > ABSTRACT_CHARS:
            abstract = abstract[:ABSTRACT_CHARS] + "..."
        lines.append(header + (f"\n{abstract}" if abstract else ""))
    return "\n\n".join(lines)


def _fallback_sections(
    question: str, prisma: dict[str, Any], included: list[dict[str, Any]]
) -> dict[str, Any]:
    """Deterministic sections when no strong model is available."""
    screened = prisma.get("records_screened", 0)
    summary = (
        f"This search examined the question: {question}. "
        f"{prisma.get('records_identified', 0)} records were identified, "
        f"{prisma.get('duplicates_removed', 0)} duplicates removed and "
        f"{screened} records screened; {len(included)} works were included. "
        "No language model was available to write a narrative synthesis, so "
        "this report presents the audited facts of the search."
    )
    return {
        "executive_summary": summary,
        "key_findings": [f"{w['title']} ({w['year'] or 'n.d.'})" for w in included[:5]],
        "themes": [],
        "limitations": (
            "The narrative synthesis is unavailable in this environment; the "
            "included works, protocol and PRISMA counts remain fully audited."
        ),
        "next_steps": [
            "Review the included works in the results view.",
            "Ask follow-up questions in the run's chat.",
        ],
    }


def _llm_sections(
    pool: LLMPool,
    question: str,
    query_string: str,
    prisma: dict[str, Any],
    included_works: list[WorkRecord],
    unsure_count: int,
) -> tuple[dict[str, Any], str]:
    parts = [
        f"Research question: {question}",
        f"Search query used: {query_string}",
        f"PRISMA counts: {json.dumps(prisma)}",
    ]
    if included_works:
        parts.append("Included works:\n" + _render_prompt_works(included_works))
    else:
        parts.append("No works were included by the screening ensemble.")
    if unsure_count:
        parts.append(f"Works awaiting a human verdict (unsure): {unsure_count}")
    response = pool.complete(
        TaskType.CHAT, system=REPORT_SYSTEM, prompt="\n\n".join(parts), max_tokens=2200
    )
    raw = response.text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    data = json.loads(raw)
    if not isinstance(data, dict) or "executive_summary" not in data:
        raise ValueError("report synthesis returned an unexpected shape")
    return _clean_strings(data), f"{response.provider}:{response.model}"


def _clean_strings(value: Any) -> Any:
    """Apply the house text conventions to every string in the sections."""
    if isinstance(value, str):
        return strip_dashes(value)
    if isinstance(value, list):
        return [_clean_strings(item) for item in value]
    if isinstance(value, dict):
        return {key: _clean_strings(item) for key, item in value.items()}
    return value


def build_report(
    session: Session, run: Run, pool: LLMPool | None, *, force: bool = False
) -> ReportRow:
    """Return the run's report, synthesizing (and caching) it if needed."""
    existing = session.scalars(
        select(ReportRow)
        .where(ReportRow.run_id == run.id, ReportRow.org_id == run.org_id)
        .order_by(ReportRow.id.desc())
    ).first()
    if existing is not None and not force:
        return existing

    protocol: dict[str, Any] = {}
    if run.protocol_id is not None:
        row = session.get(ProtocolRow, run.protocol_id)
        if row is not None:
            protocol = row.payload or {}

    works = works_for_run(session, run.id, org_id=run.org_id)
    by_id = {w.id: w for w in works}
    verdicts = _final_verdicts(session, run)
    included_ids = [wid for wid, d in verdicts.items() if d.verdict == "include"]
    included_works = [by_id[wid] for wid in included_ids if wid in by_id]
    unsure_count = sum(1 for d in verdicts.values() if d.verdict == "unsure")

    web_rows = session.scalars(
        select(WebSourceRow)
        .where(WebSourceRow.run_id == run.id, WebSourceRow.org_id == run.org_id)
        .order_by(WebSourceRow.score.desc())
        .limit(10)
    ).all()

    prisma = dict(run.prisma or {})
    question = run.question
    query_string = str(protocol.get("query_string", ""))

    model = ""
    if pool is not None and pool.has_strong():
        try:
            sections, model = _llm_sections(
                pool, question, query_string, prisma, included_works, unsure_count
            )
        except (LLMConfigError, BudgetExceededError, ValueError, KeyError):
            sections = _fallback_sections(
                question,
                prisma,
                [_work_entry(w, verdicts.get(w.id)) for w in included_works],
            )
    else:
        sections = _fallback_sections(
            question,
            prisma,
            [_work_entry(w, verdicts.get(w.id)) for w in included_works],
        )

    payload: dict[str, Any] = {
        "question": question,
        "title": run.title or question,
        "query_string": query_string,
        "criteria": {
            "inclusion": protocol.get("inclusion_criteria", []),
            "exclusion": protocol.get("exclusion_criteria", []),
        },
        "prisma": prisma,
        "sections": sections,
        "included": [_work_entry(w, verdicts.get(w.id)) for w in included_works],
        "unsure_count": unsure_count,
        "web_sources": [{"title": w.title, "url": w.url, "domain": w.domain} for w in web_rows],
        "methods": render_methods(session, run),
        "run": {
            "id": run.id,
            "created_at": run.created_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "corpus_version": run.corpus_version,
        },
        "model": model,
    }
    report = ReportRow(org_id=run.org_id, run_id=run.id, payload=payload, model=model)
    session.add(report)
    session.flush()
    return report
