"""Run pipeline: synchronous stage execution with a full audit trail.

Stages: protocol synthesis -> query compilation -> EXHAUSTIVE retrieval
(corpus-first; iterative query expansion until saturation; optional live
freshness) -> dedup -> integrity (retractions, zombie citations, tortured
phrases) -> decomposed multi-signal ranking -> ensemble screening -> PRISMA
report.

Exhaustiveness is the default operating mode (docs/VISION.md principle 8):
a run finishes when the evidence says it is complete — expansion saturated,
everything screened — not when a timer fires. Hours are an expected cost;
the USD budget is the safety net and trips into an HONEST PAUSE (audit event
+ pending works), never a silent truncation.
"""

from collections import Counter
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from sixsentences_server.acquisition.models import AcquisitionSummary
from sixsentences_server.acquisition.service import (
    Acquirer,
    acquire_for_run,
    default_acquisition_service,
)
from sixsentences_server.acquisition.store import DocumentStore, LocalDocumentStore
from sixsentences_server.chat.service import summarize_completed_run
from sixsentences_server.config import get_settings
from sixsentences_server.connectors.openalex import (
    OpenAlexClient,
    OpenAlexError,
    OpenAlexSearchPage,
)
from sixsentences_server.connectors.retractions import load_retracted_dois
from sixsentences_server.connectors.webharvest import harvest_works
from sixsentences_server.connectors.websearch import (
    MAX_REQUESTS_PER_DISCOVERY,
    WebSearchCallBudget,
    WebSearchClient,
    WebSearcher,
    WebSearchService,
    WebSource,
    query_angles,
)
from sixsentences_server.core import control
from sixsentences_server.core.db import (
    DocumentRow,
    ProtocolRow,
    Run,
    RunEvent,
    ScreeningDecisionRow,
    SourceRecordRow,
    WebSourceRow,
    WorkRow,
)
from sixsentences_server.core.entitlements import (
    EntitlementError,
    attach_action_usage_sink,
    finish_ai_action,
    settle_screening_credits,
)
from sixsentences_server.core.models import (
    PrismaCounts,
    ReviewProtocol,
    RunStatus,
    ScreeningDecision,
    SearchExecution,
    StageName,
    Verdict,
    WorkRecord,
)
from sixsentences_server.core.protocol import synthesize_protocol
from sixsentences_server.core.state import set_status
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.coverage.estimator import CoverageReport, estimate_completeness
from sixsentences_server.integrity.report import (
    IntegrityReport,
    Severity,
    assess_corpus,
)
from sixsentences_server.integrity.venue import is_non_peer_reviewed, load_venue_lists
from sixsentences_server.llm.base import BudgetExceededError, LLMUsage
from sixsentences_server.llm.pool import LLMPool
from sixsentences_server.llm.providers import ProviderError
from sixsentences_server.pipeline.calibration import (
    CanaryReport,
    HitCountCalibration,
    calibrate_hit_count,
    check_canaries,
)
from sixsentences_server.pipeline.dedup import (
    _normalize_title,
    dedup_by_title,
    merge_same_study,
)
from sixsentences_server.pipeline.expansion import (
    MAX_ROUNDS,
    NOVELTY_THRESHOLD,
    propose_variants,
)
from sixsentences_server.pipeline.semantic import propose_question_paraphrases
from sixsentences_server.pipeline.snowball import collect_snowball_candidates
from sixsentences_server.querylang.compile_duckdb import compile_duckdb
from sixsentences_server.querylang.compile_openalex import compile_openalex
from sixsentences_server.querylang.parser import parse_query
from sixsentences_server.ranking.scorer import (
    DEFAULT_WEIGHTS,
    RankedWork,
    protocol_relevance,
    rank_works,
)
from sixsentences_server.screening.certification import (
    RecallCertification,
    certify_screening_recall,
)
from sixsentences_server.screening.ensemble import (
    ensemble_agreement,
    include_vote_counts,
    screen_ensemble,
)
from sixsentences_server.screening.evidence import (
    final_decisions,
    select_final_work_ids,
)
from sixsentences_server.screening.fulltext import FullTextSummary, screen_full_text
from sixsentences_server.screening.reviewer import screen_stub

POSTGRES_WORK_UPSERT_BATCH_SIZE = 5_000
SQLITE_WORK_UPSERT_BATCH_SIZE = 100
POSTGRES_SOURCE_LOOKUP_BATCH_SIZE = 10_000
SQLITE_SOURCE_LOOKUP_BATCH_SIZE = 500


@dataclass
class RunResult:
    run_id: int
    protocol: ReviewProtocol
    corpus_version: str
    prisma: PrismaCounts
    queries_executed: list[str] = field(default_factory=list)
    search_executions: list[SearchExecution] = field(default_factory=list)
    ranked: list[RankedWork] = field(default_factory=list)
    decisions: list[ScreeningDecision] = field(default_factory=list)
    integrity_flags: list[IntegrityReport] = field(default_factory=list)
    coverage: CoverageReport | None = None
    recall_certification: RecallCertification | None = None
    hit_calibration: HitCountCalibration | None = None
    canary: CanaryReport | None = None
    acquisition: AcquisitionSummary | None = None
    fulltext: FullTextSummary | None = None
    web_sources: list[WebSource] = field(default_factory=list)
    quality_warnings: list[dict[str, str]] = field(default_factory=list)
    llm_spent_usd: float = 0.0
    budget_paused: bool = False


def _upsert_work_values(session: Session, values: list[dict[str, Any]]) -> None:
    """Upsert works below each database driver's bind-parameter ceiling."""

    if not values:
        return
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        batch_size = POSTGRES_WORK_UPSERT_BATCH_SIZE
        for offset in range(0, len(values), batch_size):
            session.execute(
                postgresql_insert(WorkRow)
                .values(values[offset : offset + batch_size])
                .on_conflict_do_nothing(index_elements=[WorkRow.id])
            )
    elif dialect == "sqlite":
        batch_size = SQLITE_WORK_UPSERT_BATCH_SIZE
        for offset in range(0, len(values), batch_size):
            session.execute(
                sqlite_insert(WorkRow)
                .values(values[offset : offset + batch_size])
                .on_conflict_do_nothing(index_elements=[WorkRow.id])
            )
    else:  # pragma: no cover - supported deployments use PostgreSQL/SQLite
        raise ValueError(f"unsupported work-upsert dialect: {dialect}")


def _existing_source_keys(
    session: Session,
    *,
    run_id: int,
    record_ids: set[str],
) -> set[tuple[str, str]]:
    """Read existing provenance keys without exceeding an ``IN`` limit."""

    if not record_ids:
        return set()
    dialect = session.get_bind().dialect.name
    batch_size = (
        SQLITE_SOURCE_LOOKUP_BATCH_SIZE
        if dialect == "sqlite"
        else POSTGRES_SOURCE_LOOKUP_BATCH_SIZE
    )
    ordered_ids = sorted(record_ids)
    existing: set[tuple[str, str]] = set()
    for offset in range(0, len(ordered_ids), batch_size):
        rows = session.execute(
            select(SourceRecordRow.work_id, SourceRecordRow.source).where(
                SourceRecordRow.run_id == run_id,
                SourceRecordRow.work_id.in_(ordered_ids[offset : offset + batch_size]),
            )
        ).all()
        existing.update((str(row[0]), str(row[1])) for row in rows)
    return existing


def _persist_run_sources(
    session: Session,
    run: Run,
    records: list[WorkRecord],
    *,
    source: str | None,
    corpus_version: str,
) -> None:
    """Persist canonical works before provenance under strict foreign keys."""

    unique_records = {record.id: record for record in records}
    if not unique_records:
        return
    values = [
        {
            "id": record.id,
            "doi": record.doi,
            "title": record.title,
            "year": record.year,
            "payload": record.model_dump(mode="json"),
        }
        for record in unique_records.values()
    ]
    dialect = session.get_bind().dialect.name
    if dialect in {"postgresql", "sqlite"}:
        _upsert_work_values(session, values)
    else:  # pragma: no cover - supported deployments use PostgreSQL/SQLite
        existing_ids = set(
            session.scalars(select(WorkRow.id).where(WorkRow.id.in_(unique_records))).all()
        )
        session.add_all(
            WorkRow(
                id=record.id,
                doi=record.doi,
                title=record.title,
                year=record.year,
                payload=record.model_dump(mode="json"),
            )
            for record in unique_records.values()
            if record.id not in existing_ids
        )
    session.flush()

    record_ids = set(unique_records)
    existing_sources = _existing_source_keys(
        session,
        run_id=run.id,
        record_ids=record_ids,
    )
    session.add_all(
        SourceRecordRow(
            org_id=run.org_id,
            run_id=run.id,
            work_id=record_id,
            source=source or unique_records[record_id].source,
            corpus_version=corpus_version,
        )
        for record_id in record_ids
        if (record_id, source or unique_records[record_id].source) not in existing_sources
    )
    session.flush()


class RunRecorder:
    """Append-only event writer bound to one run.

    Every emit COMMITS: an event is a durable checkpoint. This is what makes
    the SSE progress stream live (other sessions can only see committed rows)
    and keeps a long run from pinning the database behind one giant write
    transaction for hours.
    """

    def __init__(self, session: Session, org_id: int, run_id: int) -> None:
        self.session = session
        self.org_id = org_id
        self.run_id = run_id

    def emit(self, stage: StageName, event: str, payload: dict[str, Any] | None = None) -> None:
        self.session.add(
            RunEvent(
                org_id=self.org_id,
                run_id=self.run_id,
                stage=stage.value,
                event=event,
                payload=payload or {},
            )
        )
        self.session.commit()


def _attach_usage_sink(session: Session, run: Run, pool: LLMPool | None) -> None:
    """Persist every LLM call against this run (cost meter and UI usage panel)."""
    if pool is None:
        return
    pool.usage.clear()
    attach_action_usage_sink(
        session,
        pool,
        org_id=run.org_id,
        action_id=str((run.config or {}).get("cost_action_id") or f"run:{run.id}"),
        resource_type="run",
        resource_id=run.id,
        run_id=run.id,
    )


def _record_pool_external_usage(pool: LLMPool, usage: LLMUsage) -> None:
    """Mirror ``LLMPool.complete`` accounting for a direct connector call."""

    pool.usage.append(usage)
    if pool.on_usage is not None:
        pool.on_usage(usage)


def _persist_protocol(session: Session, run: Run, protocol: ReviewProtocol, version: int) -> None:
    row = ProtocolRow(
        org_id=run.org_id,
        project_id=run.project_id,
        version=version,
        payload=protocol.model_dump(mode="json"),
    )
    session.add(row)
    session.flush()
    run.protocol_id = row.id


def _apply_search_filters(
    protocol: ReviewProtocol,
    year_from: int | None,
    year_to: int | None,
    peer_reviewed_only: bool,
) -> None:
    """Explicit search filters override whatever the protocol was synthesised with."""
    if year_from is not None:
        protocol.year_from = year_from
    if year_to is not None:
        protocol.year_to = year_to
    if peer_reviewed_only:
        protocol.peer_reviewed_only = True


def _year_note(year_from: int | None, year_to: int | None) -> str:
    if year_from and year_to:
        return f"years {year_from}–{year_to}"
    if year_from:
        return f"years from {year_from}"
    if year_to:
        return f"years to {year_to}"
    return ""


def _screen_full_text_stage(
    session: Session,
    run: Run,
    recorder: RunRecorder,
    works: list[WorkRecord],
    protocol: ReviewProtocol,
    pool: LLMPool,
    store: DocumentStore,
) -> FullTextSummary:
    """Second-pass eligibility on the acquired full text (PRISMA bottom).

    Only works whose full text was retrieved AND parsed can be assessed; the
    rest are left out honestly (they never reach 'studies included'). Each
    decision is persisted as a `fulltext:*` reviewer with a quote verified
    against the full text; a budget trip pauses honestly.
    """
    parsed: dict[str, str] = {}
    for document_row in session.scalars(
        select(DocumentRow).where(
            DocumentRow.run_id == run.id,
            DocumentRow.status == "retrieved",
            DocumentRow.text_status == "parsed",
        )
    ).all():
        if document_row.checksum:
            parsed[document_row.work_id] = document_row.checksum

    # resume-safe: reconstruct the complete stage summary before assessing only
    # the remaining parsed reports.
    existing_decisions = session.scalars(
        select(ScreeningDecisionRow)
        .where(
            ScreeningDecisionRow.run_id == run.id,
            ScreeningDecisionRow.reviewer.like("fulltext:%"),
        )
        .order_by(ScreeningDecisionRow.id)
    ).all()
    latest_existing = {decision.work_id: decision for decision in existing_decisions}
    done = set(latest_existing)
    summary = FullTextSummary()
    for decision in latest_existing.values():
        summary.assessed += 1
        if decision.verdict == Verdict.INCLUDE.value:
            summary.included += 1
        elif decision.verdict == Verdict.EXCLUDE.value:
            summary.excluded += 1
            if len(summary.exclusions) < 50:
                summary.exclusions.append({"id": decision.work_id, "reason": decision.reason or ""})
        else:
            summary.unsure += 1
        if decision.quote:
            summary.quotes_verified += 1

    assessable = [work for work in works if work.id in parsed and store.get_text(parsed[work.id])]
    assessable_ids = {work.id for work in assessable}
    completed_assessable = len(done.intersection(assessable_ids))
    control_signal = ""
    recorder.emit(
        StageName.SCREENING_FULL_TEXT,
        "fulltext_screening_started",
        {
            "completed": completed_assessable,
            "total": len(assessable),
            "pending": max(0, len(assessable) - completed_assessable),
        },
    )
    for work in assessable:  # title/abstract-retained works, in rank order
        if work.id in done:
            continue
        control_signal = control.poll(run.id, org_id=run.org_id)
        if control_signal:
            recorder.emit(
                StageName.SCREENING_FULL_TEXT,
                "run_control",
                {"signal": control_signal, "checkpoint": "before_fulltext_assessment"},
            )
            break
        checksum = parsed.get(work.id)
        assert checksum is not None
        full_text = store.get_text(checksum)
        assert full_text
        try:
            assessment = screen_full_text(work, protocol, full_text, pool)
        except BudgetExceededError:
            summary.budget_paused = True
            break
        summary.assessed += 1
        session.add(
            ScreeningDecisionRow(
                org_id=run.org_id,
                run_id=run.id,
                work_id=work.id,
                reviewer=assessment.reviewer,
                verdict=assessment.verdict.value,
                reason=assessment.reason,
                quote=assessment.quote,
            )
        )
        if assessment.criteria:
            recorder.emit(
                StageName.SCREENING_FULL_TEXT,
                "fulltext_criteria_assessed",
                {
                    "work_id": work.id,
                    "verdict": assessment.verdict.value,
                    "criteria": [
                        criterion.model_dump(mode="json") for criterion in assessment.criteria
                    ],
                },
            )
        session.commit()  # checkpoint per assessment (same rationale as above)
        if assessment.verdict is Verdict.INCLUDE:
            summary.included += 1
        elif assessment.verdict is Verdict.EXCLUDE:
            summary.excluded += 1
            if len(summary.exclusions) < 50:
                summary.exclusions.append({"id": work.id, "reason": assessment.reason})
        else:
            summary.unsure += 1
        if assessment.quote:
            summary.quotes_verified += 1
        if (
            summary.assessed == 1
            or summary.assessed % 5 == 0
            or summary.assessed == len(assessable)
        ):
            recorder.emit(
                StageName.SCREENING_FULL_TEXT,
                "fulltext_screening_progress",
                {
                    "completed": summary.assessed,
                    "total": len(assessable),
                    "included": summary.included,
                    "excluded": summary.excluded,
                    "unsure": summary.unsure,
                    "pending": max(0, len(assessable) - summary.assessed),
                },
            )
    session.flush()
    recorder.emit(
        StageName.SCREENING_FULL_TEXT,
        (
            "fulltext_screening_paused"
            if summary.budget_paused or control_signal
            else "fulltext_screening_done"
        ),
        {
            "assessed": summary.assessed,
            "studies_included": summary.included,
            "excluded": summary.excluded,
            "unsure": summary.unsure,
            "quotes_verified": summary.quotes_verified,
            "budget_paused": summary.budget_paused,
            "control_signal": control_signal or None,
            "exclusion_reasons": summary.exclusions,
        },
    )
    return summary


# Citation snowballing is intentionally limited to one bounded pass by
# default so exhaustive searches remain predictable in time and cost.
SNOWBALL_ROUNDS = 1
SNOWBALL_FORWARD_CAP = 200  # citing works fetched per round (top-cited first)
SEMANTIC_SWEEP_CAP = 100  # meaning-ranked candidates per run (honestly capped)
LIVE_RESULT_HARD_CAP = 5_000
LIVE_MIN_PAGES = 3
LIVE_SATURATION_STREAK = 2
LIVE_NOVELTY_THRESHOLD = 0.05
LIVE_RELEVANCE_SHARE_THRESHOLD = 0.05
RELAXED_SWEEP_MIN_CORPUS_WORKS = 100_000
RELAXED_SWEEP_PER_QUERY_CAP = 500
RELAXED_SWEEP_TOTAL_NEW_CAP = 5_000


def execute_run(
    session: Session,
    run: Run,
    *,
    corpus: DuckDBCorpus,
    pool: LLMPool | None = None,
    query_override: str | None = None,
    live: bool = False,
    screen: bool = False,
    paper_limit: int = 0,  # 0 = return every eligible paper
    screen_limit: int = 0,  # legacy/internal cap; 0 screens every candidate
    exhaustive: bool = True,
    retrieval_limit: int = 100_000,
    live_limit: int = LIVE_RESULT_HARD_CAP,
    year_from: int | None = None,  # publication-year window (overrides the protocol)
    year_to: int | None = None,
    peer_reviewed_only: bool = False,  # exclude preprints + non-article types
    web_search: bool = False,  # also collect grey-literature web sources
    web_limit: int = 50,
    web_searcher: WebSearcher | None = None,  # injected in tests; default from config
    snowball: bool = False,  # citation snowballing seeded by the includes
    snowball_rounds: int = SNOWBALL_ROUNDS,
    semantic: bool = False,  # LLM-paraphrased relevance sweep of the question
    semantic_client: Any | None = None,  # injected in tests; default OpenAlex
    imported: list[WorkRecord] | None = None,  # uploaded RIS/BibTeX records
    imported_meta: list[dict[str, Any]] | None = None,  # per-batch PRISMA-S info
    canary_ids: list[str] | None = None,  # known must-hit works to validate recall
    gate_protocol: bool = False,  # stop after synthesis for human approval
    approved_protocol: ReviewProtocol | None = None,  # resume past the gate
    acquire: bool = False,  # seek OA full text for retained works (H1)
    acquirer: Acquirer | None = None,  # injected in tests; default = OA service
    full_text_screen: bool = False,  # second-pass eligibility on acquired full text
    document_store: DocumentStore | None = None,  # injected in tests; default = local store
) -> RunResult:
    settings = get_settings()
    recorder = RunRecorder(session, run.org_id, run.id)
    set_status(run, RunStatus.RUNNING)
    session.flush()
    _attach_usage_sink(session, run, pool)

    # Stage 0: protocol. Three modes: resume with a human-approved protocol,
    # synthesize then STOP at the gate, or synthesize and run straight through.
    if approved_protocol is not None:
        protocol = approved_protocol
        _apply_search_filters(protocol, year_from, year_to, peer_reviewed_only)
        _persist_protocol(session, run, protocol, version=2)
        recorder.emit(
            StageName.PROTOCOL_SYNTHESIS,
            "protocol_approved",
            {"query": protocol.query_string, "reviewer": "human"},
        )
    else:
        protocol = synthesize_protocol(run.question, pool)
        if query_override:
            # A user-supplied query replaces only the retrieval expression. It
            # must not erase the eligibility contract used by title/abstract
            # and full-text reviewers. The old empty protocol made every
            # structured full-text assessment resolve to ``unsure``.
            protocol = protocol.model_copy(
                update={
                    "query_string": query_override,
                    "synthesized_by": "user",
                }
            )
        _apply_search_filters(protocol, year_from, year_to, peer_reviewed_only)
        _persist_protocol(session, run, protocol, version=1)
        recorder.emit(
            StageName.PROTOCOL_SYNTHESIS,
            "protocol_created",
            {
                "synthesized_by": protocol.synthesized_by,
                "query": protocol.query_string,
                "auto_approved": not gate_protocol,
            },
        )
        if gate_protocol:
            set_status(run, RunStatus.AWAITING_PROTOCOL_APPROVAL)
            recorder.emit(
                StageName.PROTOCOL_SYNTHESIS,
                "protocol_gate_opened",
                {"note": "awaiting human approval of the protocol before retrieval"},
            )
            session.flush()
            return RunResult(
                run_id=run.id,
                protocol=protocol,
                corpus_version="",
                prisma=PrismaCounts(),
            )

    # Stage 1: base query compilation (both targets, verbatim for PRISMA-S)
    base_ast = parse_query(protocol.query_string)
    duckdb_sql, duckdb_params = compile_duckdb(base_ast)
    openalex_query, oa_notes = compile_openalex(base_ast)
    recorder.emit(
        StageName.QUERY_COMPILATION,
        "queries_compiled",
        {
            "query_verbatim": protocol.query_string,
            "target_corpus_sql": duckdb_sql,
            "target_corpus_params": duckdb_params,
            "target_openalex": openalex_query,
            "openalex_degradations": {
                "dropped_wildcards": oa_notes.dropped_wildcards,
                "dropped_fields": oa_notes.dropped_fields,
            },
        },
    )

    # Stage 2: exhaustive retrieval — corpus-first, expansion until saturation
    corpus_version = corpus.version()
    run.corpus_version = corpus_version.version

    unique: dict[str, WorkRecord] = {}
    seen_dois: set[str] = set()
    identified = 0
    executions: list[SearchExecution] = []
    queries_executed: list[str] = []
    # per-work capture frequency across corpus queries -> capture-recapture
    capture_counts: dict[str, int] = {}
    live_search_truncated = False
    live_search_stop_reason = "not_requested"
    live_search_returned = 0
    control_signal = ""

    year_note = _year_note(protocol.year_from, protocol.year_to)

    def run_query(query_string: str, source_label: str) -> int:
        """Execute one query against the corpus; return count of NEW works."""
        nonlocal identified, control_signal
        control_signal = control.poll(run.id, org_id=run.org_id)
        if control_signal:
            return 0
        hits = corpus.search(
            parse_query(query_string),
            limit=retrieval_limit,
            year_from=protocol.year_from,
            year_to=protocol.year_to,
        )
        identified += len(hits)
        executions.append(
            SearchExecution(
                source=source_label,
                platform=corpus_version.version,
                query_verbatim=query_string,
                limits=[f"result cap {retrieval_limit}"] + ([year_note] if year_note else []),
                records_returned=len(hits),
            )
        )
        queries_executed.append(query_string)
        new = 0
        for record in hits:
            capture_counts[record.id] = capture_counts.get(record.id, 0) + 1
            doi = (record.doi or "").lower()
            if record.id in unique or (doi and doi in seen_dois):
                continue
            unique[record.id] = record
            if doi:
                seen_dois.add(doi)
            new += 1
        return new

    base_new = run_query(protocol.query_string, "sixsentences-corpus")
    recorder.emit(
        StageName.RETRIEVAL,
        "corpus_search_done",
        {
            "corpus_version": corpus_version.version,
            "query": protocol.query_string,
            "new_unique": base_new,
        },
    )

    # expansion loop: keep proposing query variants until novelty saturates
    stopped_because = "expansion_disabled"
    if exhaustive and pool is not None and pool.has_strong():
        stopped_because = "saturated"
        for round_index in range(1, MAX_ROUNDS + 1):
            control_signal = control.poll(run.id, org_id=run.org_id)
            if control_signal:
                stopped_because = f"run_{control_signal}"
                break
            before = len(unique)
            sample_titles = [w.title for w in list(unique.values())[:15]]
            try:
                variants = propose_variants(protocol, queries_executed, sample_titles, pool)
            except BudgetExceededError:
                stopped_because = "budget_exhausted"
                break
            if not variants:
                stopped_because = "no_new_variants"
                break
            new_in_round = sum(
                run_query(variant, "sixsentences-corpus/expansion") for variant in variants
            )
            novelty = new_in_round / before if before else 1.0
            recorder.emit(
                StageName.RETRIEVAL,
                "expansion_round_done",
                {
                    "round": round_index,
                    "variants": variants,
                    "new_unique_works": new_in_round,
                    "novelty": round(novelty, 4),
                    "total_unique": len(unique),
                },
            )
            if novelty < NOVELTY_THRESHOLD:
                break
        else:
            stopped_because = "max_rounds"

    # On a large local snapshot, exact Boolean phrases alone can miss nearby
    # vocabulary (for example ``social grant`` versus ``disability grant``).
    # Run a bounded companion search for every audited variant: its concept
    # groups remain intact, while phrases may match significant constituent
    # tokens. Small corpora do not need this extra scan.
    relaxed_returned = 0
    relaxed_new = 0
    if exhaustive and corpus_version.works >= RELAXED_SWEEP_MIN_CORPUS_WORKS:
        for query_string in list(queries_executed):
            control_signal = control.poll(run.id, org_id=run.org_id)
            if control_signal or relaxed_new >= RELAXED_SWEEP_TOTAL_NEW_CAP:
                break
            hits = corpus.search_relaxed(
                parse_query(query_string),
                limit=RELAXED_SWEEP_PER_QUERY_CAP,
                year_from=protocol.year_from,
                year_to=protocol.year_to,
            )
            relaxed_returned += len(hits)
            identified += len(hits)
            executions.append(
                SearchExecution(
                    source="sixsentences-corpus/lexical-relaxation",
                    platform=corpus_version.version,
                    query_verbatim=query_string,
                    limits=[
                        f"result cap {RELAXED_SWEEP_PER_QUERY_CAP}",
                        "AND/OR concept structure preserved",
                        "quoted phrases relaxed to significant tokens",
                    ]
                    + ([year_note] if year_note else []),
                    records_returned=len(hits),
                )
            )
            for record in hits:
                capture_counts[record.id] = capture_counts.get(record.id, 0) + 1
                doi = (record.doi or "").lower()
                if record.id in unique or (doi and doi in seen_dois):
                    continue
                unique[record.id] = record
                if doi:
                    seen_dois.add(doi)
                relaxed_new += 1
                if relaxed_new >= RELAXED_SWEEP_TOTAL_NEW_CAP:
                    break
        recorder.emit(
            StageName.RETRIEVAL,
            "lexical_relaxation_done",
            {
                "queries": len(queries_executed),
                "records_returned": relaxed_returned,
                "new_unique": relaxed_new,
                "new_unique_cap": RELAXED_SWEEP_TOTAL_NEW_CAP,
                "control_signal": control_signal or None,
            },
        )
    recorder.emit(
        StageName.RETRIEVAL,
        "saturation_check",
        {
            "queries_executed": len(queries_executed),
            "total_unique": len(unique),
            "stopped_because": stopped_because,
            "novelty_threshold": NOVELTY_THRESHOLD,
        },
    )

    # capture-recapture: honest completeness estimate of the search strategy
    coverage = estimate_completeness(capture_counts, len(queries_executed))
    recorder.emit(StageName.RETRIEVAL, "coverage_estimated", coverage.model_dump())

    # optional live freshness layer
    if live:
        client = OpenAlexClient(mailto=settings.openalex_mailto, api_key=settings.openalex_api_key)
        live_new_unique = 0
        saturation_streak = 0
        last_page_has_more = False
        try:
            pages: Iterator[OpenAlexSearchPage]
            control_signal = control.poll(run.id, org_id=run.org_id)
            if control_signal:
                live_search_stop_reason = f"run_{control_signal}"
                recorder.emit(
                    StageName.RETRIEVAL,
                    "run_control",
                    {
                        "signal": control_signal,
                        "checkpoint": "before_live_search",
                        "records_returned": 0,
                    },
                )
                pages = iter(())
            else:
                pages = client.iter_search_pages(
                    openalex_query,
                    limit=live_limit,
                    year_from=protocol.year_from,
                    year_to=protocol.year_to,
                )
            for page in pages:
                page_new = 0
                relevance_scores = [protocol_relevance(record, protocol) for record in page.records]
                for record in page.records:
                    doi = (record.doi or "").lower()
                    if record.id in unique or (doi and doi in seen_dois):
                        continue
                    unique[record.id] = record
                    if doi:
                        seen_dois.add(doi)
                    page_new += 1

                page_returned = len(page.records)
                live_search_returned += page_returned
                live_new_unique += page_new
                novelty = page_new / page_returned if page_returned else 0.0
                relevant_share = (
                    sum(score > 0.0 for score in relevance_scores) / page_returned
                    if page_returned
                    else 0.0
                )
                mean_relevance = sum(relevance_scores) / page_returned if page_returned else 0.0
                page_saturated = (
                    novelty < LIVE_NOVELTY_THRESHOLD
                    or relevant_share < LIVE_RELEVANCE_SHARE_THRESHOLD
                )
                if page.page_number >= LIVE_MIN_PAGES and page_saturated:
                    saturation_streak += 1
                else:
                    saturation_streak = 0
                last_page_has_more = page.has_more
                recorder.emit(
                    StageName.RETRIEVAL,
                    "live_search_page",
                    {
                        "page": page.page_number,
                        "records_returned": page_returned,
                        "records_returned_total": live_search_returned,
                        "new_unique": page_new,
                        "new_unique_total": live_new_unique,
                        "novelty": round(novelty, 4),
                        "relevant_share": round(relevant_share, 4),
                        "mean_protocol_relevance": round(mean_relevance, 4),
                        "saturation_streak": saturation_streak,
                        "provider_total": page.provider_total,
                        "has_more": page.has_more,
                    },
                )

                control_signal = control.poll(run.id, org_id=run.org_id)
                if control_signal:
                    live_search_stop_reason = f"run_{control_signal}"
                    recorder.emit(
                        StageName.RETRIEVAL,
                        "run_control",
                        {
                            "signal": control_signal,
                            "checkpoint": "after_live_search_page",
                            "records_returned": live_search_returned,
                        },
                    )
                    break

                if not page.has_more:
                    live_search_stop_reason = "provider_exhausted"
                    break
                if saturation_streak >= LIVE_SATURATION_STREAK:
                    low_novelty = novelty < LIVE_NOVELTY_THRESHOLD
                    low_relevance = relevant_share < LIVE_RELEVANCE_SHARE_THRESHOLD
                    if low_novelty and low_relevance:
                        live_search_stop_reason = "novelty_and_relevance_saturated"
                    elif low_novelty:
                        live_search_stop_reason = "novelty_saturated"
                    else:
                        live_search_stop_reason = "relevance_saturated"
                    break
            else:
                live_search_stop_reason = (
                    "hard_cap"
                    if live_search_returned >= live_limit and last_page_has_more
                    else "provider_exhausted"
                )
        except OpenAlexError:
            live_search_stop_reason = "provider_error"
            recorder.emit(
                StageName.RETRIEVAL,
                "live_search_unavailable" if not live_search_returned else "live_search_partial",
                {
                    "reason": "provider_temporarily_unavailable",
                    "records_returned": live_search_returned,
                    "new_unique": live_new_unique,
                    "fallback": "continuing with the available scholarly sources",
                },
            )
        finally:
            identified += live_search_returned
            live_search_truncated = live_search_stop_reason == "hard_cap" and last_page_has_more
            if live_search_returned or live_search_stop_reason == "provider_exhausted":
                limit_note = (
                    f"adaptive freshness retrieval, hard safety cap {live_limit}; "
                    f"stopped because {live_search_stop_reason.replace('_', ' ')}"
                )
                if live_search_truncated:
                    limit_note += " (cap reached — results truncated)"
                executions.append(
                    SearchExecution(
                        source="openalex-live",
                        platform="api.openalex.org",
                        query_verbatim=openalex_query,
                        limits=[limit_note] + ([year_note] if year_note else []),
                        records_returned=live_search_returned,
                    )
                )
                recorder.emit(
                    StageName.RETRIEVAL,
                    "live_search_done",
                    {
                        "records_returned": live_search_returned,
                        "new_unique": live_new_unique,
                        "hard_cap": live_limit,
                        "truncated": live_search_truncated,
                        "stopped_because": live_search_stop_reason,
                        "query_verbatim": openalex_query,
                    },
                )

    # Stage 2a¼: semantic sweep — meaning-preserving paraphrases of the
    # RESEARCH QUESTION (LLM-proposed), each run as an OpenAlex relevance
    # search. Finds the synonym phrasings the boolean wording cannot reach;
    # every candidate passes the same screening, every phrase lands verbatim
    # in the PRISMA-S record.
    if semantic and not control_signal:
        sweep_client = semantic_client
        if sweep_client is None:
            sweep_client = OpenAlexClient(
                mailto=settings.openalex_mailto, api_key=settings.openalex_api_key
            )
        try:
            paraphrases = propose_question_paraphrases(run.question, protocol, pool)
        except BudgetExceededError:
            paraphrases = []
        sweeps = [run.question, *paraphrases]
        per_sweep = max(20, SEMANTIC_SWEEP_CAP // len(sweeps))
        semantic_returned = 0
        new_semantic = 0
        unavailable_sweeps = 0
        for phrase in sweeps:
            try:
                hits = sweep_client.search(
                    phrase,
                    limit=per_sweep,
                    year_from=protocol.year_from,
                    year_to=protocol.year_to,
                )
            except OpenAlexError:
                unavailable_sweeps += 1
                continue
            semantic_returned += len(hits)
            executions.append(
                SearchExecution(
                    source="semantic-sweep",
                    platform="api.openalex.org (relevance ranking)",
                    query_verbatim=phrase,
                    limits=[f"semantic sweep, cap {per_sweep}"]
                    + ([year_note] if year_note else []),
                    records_returned=len(hits),
                )
            )
            for record in hits:
                doi = (record.doi or "").lower()
                if record.id in unique or (doi and doi in seen_dois):
                    continue
                unique[record.id] = record
                if doi:
                    seen_dois.add(doi)
                new_semantic += 1
        identified += semantic_returned
        recorder.emit(
            StageName.RETRIEVAL,
            "semantic_sweep_done",
            {
                "sweeps": len(sweeps),
                "paraphrases": paraphrases,
                "returned": semantic_returned,
                "new_unique": new_semantic,
                "cap": SEMANTIC_SWEEP_CAP,
                "unavailable_sweeps": unavailable_sweeps,
                "note": "meaning-level sweep of the research question; "
                "candidates face the same screening as every other arm",
            },
        )

    # Stage 2a½: uploaded reference exports (RIS/BibTeX) join identification
    # as their own database arm — the multi-database workflow (run the
    # translated query in Scopus/WoS/PubMed, upload the export here).
    if imported:
        new_imported = 0
        for record in imported:
            doi = (record.doi or "").lower()
            if record.id in unique or (doi and doi in seen_dois):
                continue
            unique[record.id] = record
            if doi:
                seen_dois.add(doi)
            new_imported += 1
        identified += len(imported)
        for batch in imported_meta or []:
            executions.append(
                SearchExecution(
                    source=f"import:{batch['label']}",
                    platform="user-uploaded export",
                    query_verbatim=f"reference export {batch['filename']}",
                    limits=["external database export"],
                    records_returned=batch["count"],
                )
            )
        recorder.emit(
            StageName.RETRIEVAL,
            "imports_merged",
            {
                "batches": len(imported_meta or []),
                "records": len(imported),
                "new_unique": new_imported,
                "note": "uploaded exports join dedup and screening "
                "(PRISMA: identification via other databases)",
            },
        )

    # Stage 2b: grey-literature web search + scholarly harvest. Runs BEFORE
    # dedup so papers hiding in web results (arXiv/DOI links) enter the same
    # dedup -> integrity -> screening flow as the database arm — the PRISMA
    # 2020 "identification via other methods" stream. What remains after the
    # harvest is genuine grey literature and stays a separate list.
    web_sources: list[WebSource] = []
    other_identified = 0
    citation_identified = 0
    if web_search and not control_signal:
        searcher = web_searcher
        if searcher is None and settings.websearch_enabled and pool is not None:
            searcher = WebSearchClient(
                settings.websearch_openrouter_api_key,
                url=settings.openrouter_endpoint("chat/completions"),
                budget=pool.budget,
                on_usage=lambda usage: _record_pool_external_usage(pool, usage),
                call_budget=WebSearchCallBudget(limit=MAX_REQUESTS_PER_DISCOVERY),
            )
        if searcher is not None:
            angles = query_angles(run.question, protocol.inclusion_criteria)
            search_service = WebSearchService(searcher)
            found = search_service.discover(
                angles,
                limit=web_limit,
                year_from=protocol.year_from,
                year_to=protocol.year_to,
            )
            if search_service.failure_codes and not found:
                recorder.emit(
                    StageName.WEB_SEARCH,
                    "web_search_failed",
                    {
                        "error_code": search_service.failure_codes[-1],
                        "queries_attempted": len(search_service.failure_codes),
                        "note": "No empty successful result was recorded for a connector failure.",
                    },
                )
            else:
                oa_client = OpenAlexClient(
                    mailto=settings.openalex_mailto, api_key=settings.openalex_api_key
                )
                harvest = harvest_works(found, oa_client)
                new_from_web = 0
                for record in harvest.works:
                    doi = (record.doi or "").lower()
                    if record.id in unique or (doi and doi in seen_dois):
                        continue
                    unique[record.id] = record
                    if doi:
                        seen_dois.add(doi)
                    new_from_web += 1
                identified += harvest.resolved
                other_identified = harvest.resolved
                if harvest.resolved:
                    executions.append(
                        SearchExecution(
                            source="websearch-harvest",
                            platform="web-search + api.openalex.org",
                            query_verbatim="; ".join(angles),
                            limits=[f"papers recovered from {len(found)} web results"],
                            records_returned=harvest.resolved,
                        )
                    )
                # resolved papers moved into the academic arm; the rest is grey lit
                web_sources = [s for s in found if s.url not in harvest.resolved_urls]
                for src in web_sources:
                    session.add(
                        WebSourceRow(
                            org_id=run.org_id,
                            run_id=run.id,
                            title=src.title,
                            url=src.url,
                            snippet=src.snippet,
                            domain=src.domain,
                            score=src.quality,  # store the authority-weighted score
                        )
                    )
                session.flush()
                recorder.emit(
                    StageName.WEB_SEARCH,
                    "web_search_partial" if search_service.failure_codes else "web_search_done",
                    {
                        "found": len(found),
                        "grey_literature": len(web_sources),
                        "queries": len(angles),
                        "failed_queries": len(search_service.failure_codes),
                        "by_category": dict(Counter(s.category for s in web_sources)),
                        "note": (
                            "grey literature — not peer-reviewed, separate from the academic flow"
                        ),
                    },
                )
                recorder.emit(
                    StageName.WEB_SEARCH,
                    "web_harvest_done",
                    {
                        "scholarly_links_detected": harvest.candidates,
                        "resolved_works": harvest.resolved,
                        "new_unique_works": new_from_web,
                        "unresolved": harvest.unresolved[:10],
                        "note": "papers recovered from web results join dedup and screening "
                        "(PRISMA: identification via other methods)",
                    },
                )
        else:
            recorder.emit(
                StageName.WEB_SEARCH,
                "web_search_skipped",
                {
                    "reason": (
                        "reviewed OpenRouter/Sonar web search is not enabled or "
                        "a central run budget is unavailable"
                    )
                },
            )

    # exact id/DOI dedup already happened during retrieval; now collapse the
    # same paper re-indexed under different ids (preprint/published/mirrors),
    # then fold companion REPORTS of the same study (PRISMA counts studies)
    id_unique = len(unique)
    deduped, title_duplicates = dedup_by_title(list(unique.values()))
    merged_works, companion_pairs = merge_same_study(deduped)
    unique = {work.id: work for work in merged_works}
    duplicates_removed = identified - len(unique)
    recorder.emit(
        StageName.RETRIEVAL,
        "dedup_done",
        {
            "identified": identified,
            "unique": len(unique),
            "duplicates": duplicates_removed,
            "id_doi_duplicates": identified - id_unique,
            "title_duplicates": title_duplicates,
            "companion_reports_merged": len(companion_pairs),
            "companion_pairs": [
                {
                    "dropped": dropped.id,
                    "dropped_title": dropped.title,
                    "kept": kept_work.id,
                    "kept_title": kept_work.title,
                }
                for dropped, kept_work in companion_pairs[:20]
            ],
        },
    )

    # query calibration: hit-count sanity + canary recall (known must-hits)
    hit_calibration = calibrate_hit_count(len(unique), corpus_version.works)
    recorder.emit(StageName.QUERY_COMPILATION, "query_calibration", hit_calibration.model_dump())
    canary: CanaryReport | None = None
    if canary_ids:
        canary = check_canaries(set(unique), canary_ids)
        recorder.emit(StageName.QUERY_COMPILATION, "canary_check", canary.model_dump())

    # Persist canonical parents before provenance. PostgreSQL enforces this
    # immediately, and live retrieval can discover the same work concurrently.
    _persist_run_sources(
        session,
        run,
        list(unique.values()),
        source=None,
        corpus_version=corpus_version.version,
    )

    # Stage 3: integrity — retractions + zombie citations + tortured phrases
    retracted_dois = frozenset(load_retracted_dois(settings.data_dir))
    retracted = [
        w for w in unique.values() if w.is_retracted or ((w.doi or "").lower() in retracted_dois)
    ]
    recorder.emit(
        StageName.INTEGRITY,
        "retraction_check_done",
        {
            "flagged": len(retracted),
            "retraction_data_loaded": bool(retracted_dois),
            "flagged_ids": [w.id for w in retracted[:50]],
        },
    )
    indexed_venues, predatory_venues = load_venue_lists(settings.data_dir)
    integrity = assess_corpus(
        list(unique.values()),
        retracted_dois=retracted_dois,
        indexed_venues=indexed_venues,
        predatory_venues=predatory_venues,
    )
    integrity_flagged = [r for r in integrity.values() if r.flagged]
    critical = sum(1 for r in integrity_flagged if r.severity is Severity.CRITICAL)
    cites_retracted = sum(1 for r in integrity.values() if r.cites_retracted)
    tortured = sum(1 for r in integrity.values() if r.tortured_phrases)
    predatory = sum(1 for r in integrity.values() if r.venue_status == "predatory")
    preprints = sum(1 for r in integrity.values() if r.venue_status == "preprint")
    recorder.emit(
        StageName.INTEGRITY,
        "integrity_signals_done",
        {
            "assessed": len(integrity),
            "flagged": len(integrity_flagged),
            "critical": critical,
            "cites_retracted": cites_retracted,
            "tortured_phrase_hits": tortured,
            "predatory_venues": predatory,
            "preprints": preprints,
            "venue_lists_loaded": bool(indexed_venues or predatory_venues),
            "flagged_detail": [
                {"id": r.work_id, "severity": r.severity.value, "reason": r.reason}
                for r in integrity_flagged[:50]
            ],
        },
    )

    # optional peer-review filter: drop detected preprints + non-article types
    # (auditable pre-screening exclusion; unknown venues are kept)
    if protocol.peer_reviewed_only:
        dropped = [
            work_id
            for work_id in list(unique)
            if work_id in integrity
            and is_non_peer_reviewed(integrity[work_id].venue_status, unique[work_id].work_type)
        ]
        for work_id in dropped:
            unique.pop(work_id, None)
        recorder.emit(
            StageName.INTEGRITY,
            "peer_review_filter",
            {
                "excluded": len(dropped),
                "excluded_ids": dropped[:50],
                "kept": len(unique),
            },
        )

    # Stage 4: ranking — decomposed multi-signal score (never a black box).
    # Ranked first so screening also processes the most promising works first.
    retracted_ids = frozenset(w.id for w in retracted)
    ranked_works = rank_works(list(unique.values()), protocol, retracted_ids=retracted_ids)
    ranked = [rw.work for rw in ranked_works]
    screening_work_limit = min(
        screen_limit if screen_limit > 0 else retrieval_limit,
        retrieval_limit,
    )
    final_paper_limit = min(paper_limit, retrieval_limit) if paper_limit > 0 else 0
    recorder.emit(
        StageName.RANKING,
        "ranking_done",
        {
            "ranked": len(ranked_works),
            "weights": DEFAULT_WEIGHTS,
            "top": [
                {
                    "id": rw.work.id,
                    "title": rw.work.title,
                    "score": rw.score,
                    "signals": rw.signals.model_dump(),
                    "retracted": rw.retracted,
                }
                for rw in ranked_works[:10]
            ],
        },
    )

    # Stage 5: ensemble screening (exhaustive by default: every unique work).
    # Resume-safe + cancellable: title/abstract decisions already made for this
    # run are reloaded and skipped, and a pause/cancel signal stops the loop.
    decisions: list[ScreeningDecision] = []
    excluded = unsure = included = 0
    budget_paused = False
    capacity_paused = False
    provider_paused = False
    recall_certification: RecallCertification | None = None
    if screen:
        prior = session.scalars(
            select(ScreeningDecisionRow).where(
                ScreeningDecisionRow.run_id == run.id,
                ScreeningDecisionRow.reviewer.not_like("human:%"),
                ScreeningDecisionRow.reviewer.not_like("fulltext:%"),
            )
        ).all()
        already = {d.work_id for d in prior}
        decisions = [
            ScreeningDecision(
                work_id=d.work_id,
                reviewer=d.reviewer,
                verdict=Verdict(d.verdict),
                reason=d.reason,
                quote=d.quote,
            )
            for d in prior
        ]
        capped = ranked[:screening_work_limit]
        capped_ids = {work.id for work in capped}
        to_screen = [w for w in capped if w.id not in already]
        already_in_scope = already & capped_ids
        refs = pool.screening_refs() if pool is not None else []
        outcomes = []
        recorder.emit(
            StageName.SCREENING_TITLE_ABSTRACT,
            "screening_started",
            {
                "total": len(capped),
                "already_screened": len(already_in_scope),
                "pending": len(to_screen),
                "ensemble_size": len(refs),
            },
        )
        if decisions:
            try:
                settle_screening_credits(
                    session,
                    run,
                    len({decision.work_id for decision in decisions}),
                )
                session.commit()
            except EntitlementError:
                capacity_paused = True
                recorder.emit(
                    StageName.SCREENING_TITLE_ABSTRACT,
                    "capacity_exhausted",
                    {
                        "screened": len({decision.work_id for decision in decisions}),
                        "pending": len(to_screen),
                        "note": "The run is safely paused before more provider work.",
                    },
                )
        for index, work in enumerate(to_screen):
            if capacity_paused:
                break
            control_signal = control.poll(run.id, org_id=run.org_id)
            if control_signal:  # pause / cancel requested — stop screening here
                recorder.emit(
                    StageName.SCREENING_TITLE_ABSTRACT,
                    "run_control",
                    {"signal": control_signal, "pending": len(to_screen) - index},
                )
                break
            if pool is None or not refs:
                decision = screen_stub(work)
            else:
                try:
                    outcome = screen_ensemble(work, protocol, pool, refs)
                except BudgetExceededError:
                    budget_paused = True
                    recorder.emit(
                        StageName.SCREENING_TITLE_ABSTRACT,
                        "budget_exhausted",
                        {
                            "screened": index,
                            "pending": len(to_screen) - index,
                            "spent_usd": round(pool.budget.spent_usd, 4),
                            "note": "honest pause: remaining works stay pending, "
                            "raise SIX_LLM_BUDGET_USD and re-run to continue",
                        },
                    )
                    break
                except ProviderError:
                    provider_paused = True
                    recorder.emit(
                        StageName.SCREENING_TITLE_ABSTRACT,
                        "provider_unavailable",
                        {
                            "screened": len(already_in_scope) + index,
                            "pending": len(to_screen) - index,
                            "note": (
                                "All screening routes were unavailable after retries. "
                                "The run is safely paused and can be resumed."
                            ),
                        },
                    )
                    break
                outcomes.append(outcome)
                decision = outcome.final
            decisions.append(decision)
            session.add(
                ScreeningDecisionRow(
                    org_id=run.org_id,
                    run_id=run.id,
                    work_id=decision.work_id,
                    reviewer=decision.reviewer,
                    verdict=decision.verdict.value,
                    reason=decision.reason,
                    quote=decision.quote,
                )
            )
            # checkpoint: each decision (and its LLM usage rows) is durable
            # immediately — this is what makes pause/resume honest and keeps
            # the write lock short during an hours-long screening pass
            session.commit()
            completed = index + 1
            if completed == 1 or completed % 20 == 0 or completed == len(to_screen):
                current_decisions = decisions
                try:
                    settle_screening_credits(
                        session,
                        run,
                        len({decision.work_id for decision in current_decisions}),
                    )
                    session.commit()
                except EntitlementError:
                    capacity_paused = True
                    recorder.emit(
                        StageName.SCREENING_TITLE_ABSTRACT,
                        "capacity_exhausted",
                        {
                            "screened": len({decision.work_id for decision in current_decisions}),
                            "pending": max(0, len(to_screen) - completed),
                            "note": "The run is safely paused before more provider work.",
                        },
                    )
                    break
                recorder.emit(
                    StageName.SCREENING_TITLE_ABSTRACT,
                    "screening_progress",
                    {
                        "completed": len(already_in_scope) + completed,
                        "total": len(capped),
                        "pending": max(0, len(to_screen) - completed),
                        "included": sum(
                            decision.verdict is Verdict.INCLUDE for decision in current_decisions
                        ),
                        "excluded": sum(
                            decision.verdict is Verdict.EXCLUDE for decision in current_decisions
                        ),
                        "unsure": sum(
                            decision.verdict is Verdict.UNSURE for decision in current_decisions
                        ),
                    },
                )
        title_decisions = [decision for decision in decisions if decision.work_id in capped_ids]
        decided_in_scope = {decision.work_id for decision in title_decisions}
        pending_screening = max(0, len(capped) - len(decided_in_scope))
        excluded = sum(1 for d in title_decisions if d.verdict is Verdict.EXCLUDE)
        unsure = sum(1 for d in title_decisions if d.verdict is Verdict.UNSURE)
        included = sum(1 for d in title_decisions if d.verdict is Verdict.INCLUDE)
        agreement = ensemble_agreement(outcomes)
        screening_complete = not (
            control_signal
            or budget_paused
            or capacity_paused
            or provider_paused
            or pending_screening
        )
        recorder.emit(
            StageName.SCREENING_TITLE_ABSTRACT,
            ("screening_paused" if not screening_complete else "screening_done"),
            {
                "screened": len(decided_in_scope),
                "of_total": len(capped),
                "pending": pending_screening,
                "included": included,
                "excluded": excluded,
                "unsure": unsure,
                "ensemble_size": len(refs),
                "adjudicated": sum(1 for o in outcomes if o.adjudicated),
                "pairwise_agreement": agreement,
                "quotes_verified": sum(1 for d in decisions if d.quote),
                "screen_limit": screen_limit or "unlimited",
            },
        )
        # Stage 5a: citation snowballing — the includes seed a backward sweep
        # (their reference lists) and a forward sweep (works citing them);
        # every candidate is screened under the same frozen protocol.
        if (
            snowball
            and not control_signal
            and not budget_paused
            and not capacity_paused
            and not provider_paused
        ):
            snowball_client: OpenAlexClient | None = None
            if settings.openalex_api_key:
                snowball_client = OpenAlexClient(
                    mailto=settings.openalex_mailto, api_key=settings.openalex_api_key
                )
            known_titles = {_normalize_title(w.title) for w in unique.values()}
            include_ids = {d.work_id for d in decisions if d.verdict is Verdict.INCLUDE}
            seeds = [w for w in unique.values() if w.id in include_ids]
            seeded: set[str] = set()
            for snowball_round in range(1, max(1, snowball_rounds) + 1):
                fresh = [s for s in seeds if s.id not in seeded]
                if not fresh:
                    break
                seeded.update(s.id for s in fresh)
                snowball_harvest = collect_snowball_candidates(
                    fresh,
                    known_ids=set(unique),
                    known_dois=seen_dois,
                    known_titles=known_titles,
                    corpus=corpus,
                    client=snowball_client,
                    year_from=protocol.year_from,
                    year_to=protocol.year_to,
                    forward_cap=SNOWBALL_FORWARD_CAP,
                )
                if not snowball_harvest.records:
                    recorder.emit(
                        StageName.SNOWBALL,
                        "snowball_round_done",
                        {
                            "round": snowball_round,
                            "seeds": len(fresh),
                            "backward_refs": snowball_harvest.backward_refs,
                            "backward_resolved": snowball_harvest.backward_resolved,
                            "forward_returned": snowball_harvest.forward_returned,
                            "new_records": 0,
                            "live_forward": snowball_client is not None,
                            "note": "saturated — no unseen candidates",
                        },
                    )
                    break
                for record in snowball_harvest.records:
                    unique[record.id] = record
                    doi = (record.doi or "").lower()
                    if doi:
                        seen_dois.add(doi)
                    known_titles.add(_normalize_title(record.title))
                    if record.is_retracted or (doi and doi in retracted_dois):
                        retracted.append(record)
                identified += len(snowball_harvest.records)
                citation_identified += len(snowball_harvest.records)
                executions.append(
                    SearchExecution(
                        source=f"citation-snowball-round-{snowball_round}",
                        platform="pinned corpus + api.openalex.org",
                        query_verbatim=(
                            f"references and citing works of {len(fresh)} included records"
                        ),
                        limits=[f"forward cap {SNOWBALL_FORWARD_CAP}"]
                        + ([year_note] if year_note else []),
                        records_returned=len(snowball_harvest.records),
                    )
                )
                _persist_run_sources(
                    session,
                    run,
                    snowball_harvest.records,
                    source="citation-snowball",
                    corpus_version=corpus_version.version,
                )
                # Re-rank the complete identified pool after every snowball
                # harvest. A newly found paper can displace an earlier paper
                # in the bounded working set; only new members of that top set
                # need an additional screening decision.
                ranked_works = rank_works(
                    list(unique.values()),
                    protocol,
                    retracted_ids=frozenset(w.id for w in retracted),
                )
                ranked = [rw.work for rw in ranked_works]
                decided_ids = {decision.work_id for decision in decisions}
                ranked_new = [
                    item
                    for item in ranked_works[:screening_work_limit]
                    if item.work.id not in decided_ids
                ]
                round_includes: list[WorkRecord] = []
                screened_this_round = 0
                recorder.emit(
                    StageName.SNOWBALL,
                    "snowball_screening_started",
                    {
                        "round": snowball_round,
                        "candidates": len(ranked_new),
                        "seeds": len(fresh),
                    },
                )
                for ranked_record in ranked_new:
                    if capacity_paused:
                        break
                    work = ranked_record.work
                    control_signal = control.poll(run.id, org_id=run.org_id)
                    if control_signal:
                        recorder.emit(
                            StageName.SNOWBALL,
                            "run_control",
                            {"signal": control_signal},
                        )
                        break
                    if pool is None or not refs:
                        decision = screen_stub(work)
                    else:
                        try:
                            outcome = screen_ensemble(work, protocol, pool, refs)
                        except BudgetExceededError:
                            budget_paused = True
                            recorder.emit(
                                StageName.SNOWBALL,
                                "budget_exhausted",
                                {"screened": screened_this_round},
                            )
                            break
                        except ProviderError:
                            provider_paused = True
                            recorder.emit(
                                StageName.SNOWBALL,
                                "provider_unavailable",
                                {
                                    "screened": screened_this_round,
                                    "pending": max(
                                        0,
                                        len(ranked_new) - screened_this_round,
                                    ),
                                    "note": (
                                        "All screening routes were unavailable after retries. "
                                        "The run is safely paused and can be resumed."
                                    ),
                                },
                            )
                            break
                        outcomes.append(outcome)
                        decision = outcome.final
                    decisions.append(decision)
                    session.add(
                        ScreeningDecisionRow(
                            org_id=run.org_id,
                            run_id=run.id,
                            work_id=decision.work_id,
                            reviewer=decision.reviewer,
                            verdict=decision.verdict.value,
                            reason=decision.reason,
                            quote=decision.quote,
                        )
                    )
                    session.commit()
                    screened_this_round += 1
                    if decision.verdict is Verdict.INCLUDE:
                        round_includes.append(work)
                    if (
                        screened_this_round == 1
                        or screened_this_round % 20 == 0
                        or screened_this_round == len(ranked_new)
                    ):
                        try:
                            settle_screening_credits(
                                session,
                                run,
                                len({decision.work_id for decision in decisions}),
                            )
                            session.commit()
                        except EntitlementError:
                            capacity_paused = True
                            recorder.emit(
                                StageName.SNOWBALL,
                                "capacity_exhausted",
                                {
                                    "round": snowball_round,
                                    "completed": screened_this_round,
                                    "pending": max(
                                        0,
                                        len(ranked_new) - screened_this_round,
                                    ),
                                    "note": ("The run is safely paused before more provider work."),
                                },
                            )
                            break
                        recorder.emit(
                            StageName.SNOWBALL,
                            "snowball_screening_progress",
                            {
                                "round": snowball_round,
                                "completed": screened_this_round,
                                "total": len(ranked_new),
                                "pending": max(0, len(ranked_new) - screened_this_round),
                                "new_includes": len(round_includes),
                            },
                        )
                recorder.emit(
                    StageName.SNOWBALL,
                    "snowball_round_done",
                    {
                        "round": snowball_round,
                        "seeds": len(fresh),
                        "backward_refs": snowball_harvest.backward_refs,
                        "backward_resolved": snowball_harvest.backward_resolved,
                        "forward_returned": snowball_harvest.forward_returned,
                        "skipped_known": snowball_harvest.skipped_known,
                        "skipped_filters": snowball_harvest.skipped_filters,
                        "new_records": len(snowball_harvest.records),
                        "screened": screened_this_round,
                        "new_includes": len(round_includes),
                        "live_forward": snowball_client is not None,
                    },
                )
                seeds = seeds + round_includes
                if control_signal or budget_paused or provider_paused:
                    break
            excluded = sum(1 for d in decisions if d.verdict is Verdict.EXCLUDE)
            unsure = sum(1 for d in decisions if d.verdict is Verdict.UNSURE)
            included = sum(1 for d in decisions if d.verdict is Verdict.INCLUDE)

        # the volume-priced part of the bill, now that its size is known
        try:
            charged = settle_screening_credits(
                session,
                run,
                len({decision.work_id for decision in decisions}),
            )
        except EntitlementError:
            charged = 0
            capacity_paused = True
        if charged:
            recorder.emit(
                StageName.SCREENING_TITLE_ABSTRACT,
                "capacity_recorded",
                {
                    "action": "screening",
                    "capacity_units": charged,
                    "works": len(decisions),
                },
            )

        # Preserve model-overlap estimates without certifying recall from model
        # agreement. The hosted route has no independently verified reviewer
        # independence; distinct model/vendor names cannot supply that evidence.
        if (
            screening_complete
            and not control_signal
            and not budget_paused
            and not capacity_paused
            and not provider_paused
            and not prior
        ):
            recall_certification = certify_screening_recall(
                include_vote_counts(outcomes),
                reviewers=len(set(refs)),
                screened=len(decisions),
                target_recall=settings.target_recall,
                reviewer_independence_verified=False,
            )
            recorder.emit(
                StageName.SCREENING_TITLE_ABSTRACT,
                "screening_recall_certified",
                recall_certification.model_dump(),
            )

    # Stage 5b: full-text acquisition — seek OA full text for every record not
    # excluded at title/abstract. UNSURE must advance in a recall-first review;
    # otherwise missing abstract evidence becomes a silent false exclusion.
    # (PRISMA "reports sought for retrieval"). Open-access sources only; every
    # acquired document records its legal basis (principle 7).
    candidate_works: list[WorkRecord] = []
    if included or unsure:
        advanced_ids = {
            decision.work_id for decision in decisions if decision.verdict is not Verdict.EXCLUDE
        }
        candidate_works = [
            work for work in ranked[:screening_work_limit] if work.id in advanced_ids
        ]

    # a pause/cancel that arrived outside the screening loop still stops here
    if not control_signal:
        control_signal = control.poll(run.id, org_id=run.org_id)

    acquisition: AcquisitionSummary | None = None
    if (
        acquire
        and candidate_works
        and not control_signal
        and not budget_paused
        and not capacity_paused
        and not provider_paused
    ):
        service = acquirer or default_acquisition_service()
        last_acquisition_checkpoint = -1

        class AcquisitionControlCheckpoint(Exception):
            """Stop between documents after persisting the acquisition ledger."""

        def acquisition_progress(
            completed: int,
            total: int,
            progress_summary: AcquisitionSummary,
        ) -> None:
            nonlocal last_acquisition_checkpoint, acquisition, control_signal
            control_signal = control.poll(run.id, org_id=run.org_id)
            if control_signal:
                acquisition = progress_summary
                recorder.emit(
                    StageName.ACQUISITION,
                    "run_control",
                    {
                        "signal": control_signal,
                        "checkpoint": "between_documents",
                        "completed": completed,
                        "pending": max(0, total - completed),
                    },
                )
                raise AcquisitionControlCheckpoint
            should_emit = (
                completed == 0
                or completed == total
                or completed == 1
                or completed - last_acquisition_checkpoint >= 5
            )
            if not should_emit:
                return
            last_acquisition_checkpoint = completed
            recorder.emit(
                StageName.ACQUISITION,
                "acquisition_progress",
                {
                    "completed": completed,
                    "total": total,
                    "retrieved": progress_summary.retrieved,
                    "parsed": progress_summary.parsed,
                    "not_retrieved": progress_summary.not_retrieved,
                    "pending": max(0, total - completed),
                },
            )

        # The callback has already committed partial results and the signal.
        with suppress(AcquisitionControlCheckpoint):
            acquisition = acquire_for_run(
                session,
                run,
                candidate_works,
                service=service,
                on_progress=acquisition_progress,
            )
        assert acquisition is not None
        recorder.emit(
            StageName.ACQUISITION,
            "acquisition_paused" if control_signal else "acquisition_done",
            {
                "sought": acquisition.sought,
                "retrieved": acquisition.retrieved,
                "not_retrieved": acquisition.not_retrieved,
                "parsed": acquisition.parsed,
                "stored_unparsed": acquisition.stored_unparsed,
                "fallback_found": acquisition.fallback_found,
                "by_legal_basis": acquisition.by_legal_basis,
                "by_source": acquisition.by_source,
                "note": "open-access sources only; legal basis recorded per document",
            },
        )
    elif acquire:
        recorder.emit(
            StageName.ACQUISITION,
            "acquisition_skipped",
            {
                "sought": 0,
                "reason": (
                    "run paused before acquisition"
                    if budget_paused or capacity_paused or provider_paused or control_signal
                    else "no title/abstract candidates to seek full text for"
                ),
            },
        )

    # Stage 5c: full-text screening — second-pass eligibility on the acquired
    # full text (PRISMA "reports assessed for eligibility" -> "studies included").
    fulltext: FullTextSummary | None = None
    control_signal = control.poll(run.id, org_id=run.org_id) or control_signal
    if (
        full_text_screen
        and acquisition is not None
        and pool is not None
        and pool.has_strong()
        and not control_signal
        and not budget_paused
        and not capacity_paused
        and not provider_paused
    ):
        store = document_store or LocalDocumentStore(settings.documents_dir)
        fulltext = _screen_full_text_stage(
            session, run, recorder, candidate_works, protocol, pool, store
        )
        budget_paused = budget_paused or fulltext.budget_paused
        control_signal = control.poll(run.id, org_id=run.org_id) or control_signal

    quality_warnings: list[dict[str, str]] = []
    if (run.config or {}).get("living_refresh_web_search_skipped"):
        quality_warnings.append(
            {
                "code": "living_refresh_web_search_not_reused",
                "severity": "info",
                "title": "Web sources were not reused automatically",
                "detail": (
                    "This living refresh used scholarly sources only because web search "
                    "requires a fresh public-data confirmation for every new run."
                ),
            }
        )
    if live_search_stop_reason == "provider_error":
        quality_warnings.append(
            {
                "code": "live_provider_interrupted",
                "severity": "warning",
                "title": "Live scholarly retrieval ended early",
                "detail": (
                    f"The live provider became unavailable after {live_search_returned:,} "
                    "records. The review continued with the local corpus and every live "
                    "record already retrieved; additional matching records may exist."
                ),
            }
        )
    if live_search_truncated:
        quality_warnings.append(
            {
                "code": "live_results_capped",
                "severity": "warning",
                "title": "The live index reached its configured result cap",
                "detail": (
                    f"The live search retained {live_search_returned:,} records before "
                    f"the {live_limit:,}-record safety ceiling. "
                    "Additional matching records may exist outside this run."
                ),
            }
        )
    if coverage.method != "chao2":
        quality_warnings.append(
            {
                "code": "search_coverage_undetermined",
                "severity": "warning",
                "title": "Search coverage could not be estimated",
                "detail": coverage.note,
            }
        )
    elif coverage.ci_low < 0.8:
        quality_warnings.append(
            {
                "code": "search_coverage_uncertain",
                "severity": "warning",
                "title": "The conservative search-coverage estimate is low",
                "detail": (
                    f"The lower confidence bound is {coverage.ci_low:.0%}. "
                    "Consider broadening the query or adding another database export."
                ),
            }
        )
    if screen and (recall_certification is None or not recall_certification.certified):
        quality_warnings.append(
            {
                "code": "screening_recall_not_certified",
                "severity": "warning",
                "title": "Screening recall is not certified",
                "detail": (
                    recall_certification.note
                    if recall_certification is not None
                    else "No independent-reviewer recall estimate was available for this run."
                ),
            }
        )
    if acquisition is not None and acquisition.not_retrieved:
        quality_warnings.append(
            {
                "code": "fulltext_access_gap",
                "severity": "warning",
                "title": "Some reports could not be retrieved automatically",
                "detail": (
                    f"{acquisition.not_retrieved} of {acquisition.sought} reports remain "
                    "eligible candidates but need a library, institutional, or user-supplied copy."
                ),
            }
        )
    if fulltext is not None and fulltext.unsure:
        quality_warnings.append(
            {
                "code": "fulltext_review_pending",
                "severity": "warning",
                "title": "Some full-text decisions still need review",
                "detail": (
                    f"{fulltext.unsure} reports were retained as unsure instead of being "
                    "silently excluded."
                ),
            }
        )
    if not retracted_dois or not (indexed_venues or predatory_venues):
        missing = []
        if not retracted_dois:
            missing.append("retraction data")
        if not (indexed_venues or predatory_venues):
            missing.append("venue watchlists")
        quality_warnings.append(
            {
                "code": "integrity_sources_partial",
                "severity": "warning",
                "title": "Integrity checks used partial reference data",
                "detail": f"Missing locally configured data: {', '.join(missing)}.",
            }
        )
    run.config = {
        **dict(run.config or {}),
        "quality_warnings": quality_warnings,
    }
    recorder.emit(
        StageName.REPORT,
        "quality_gate_evaluated",
        {
            "warnings": quality_warnings,
            "passed_without_warnings": not quality_warnings,
        },
    )

    # The public paper limit is a final relevance-ranked output bound. It is
    # applied only after acquisition and deep screening. Papers with no
    # reachable OA copy, and papers left unsure, remain candidates: missing
    # access is not an eligibility decision. Only a confirmed full-text
    # exclusion removes a title/abstract include from the final result set.
    selected_ids: list[str] = []
    if (
        final_paper_limit > 0
        and not control_signal
        and not budget_paused
        and not capacity_paused
        and not provider_paused
    ):
        if screen:
            effective = final_decisions(session, run.id, run.org_id)
            all_retained_ids = select_final_work_ids(candidate_works, effective)
            selected_ids = all_retained_ids[:final_paper_limit]
            eligible_count = len(all_retained_ids)
            selection_basis = (
                "confirmed full-text or human inclusions first, then provisional "
                "includes and unsure records; relevance rank preserved within each tier"
            )
        else:
            selected_ids = [item.work.id for item in ranked_works[:final_paper_limit]]
            eligible_count = len(ranked_works)
            selection_basis = "highest relevance rank"
        run.config = {
            **dict(run.config or {}),
            "paper_selection_ids": selected_ids,
            "paper_selection_finalized": True,
        }
        recorder.emit(
            StageName.RANKING,
            "output_set_finalized",
            {
                "identified_unique": len(ranked_works),
                "screened": len(decisions),
                "eligible": eligible_count,
                "selected": len(selected_ids),
                "paper_limit": final_paper_limit,
                "selection": selection_basis,
                "finalized_after_full_text": bool(full_text_screen),
            },
        )

    # Stage 6: report
    prisma = PrismaCounts(
        records_identified=identified,
        other_identified=other_identified,
        citation_identified=citation_identified,
        duplicates_removed=duplicates_removed,
        companion_reports_merged=len(companion_pairs),
        records_screened=len(decisions),
        records_excluded=excluded,
        records_unsure=unsure,
        retracted_flagged=len(retracted),
        included=included,
        reports_sought_for_retrieval=acquisition.sought if acquisition else 0,
        reports_not_retrieved=acquisition.not_retrieved if acquisition else 0,
        reports_assessed_for_eligibility=fulltext.assessed if fulltext else 0,
        reports_excluded_fulltext=fulltext.excluded if fulltext else 0,
        studies_included=fulltext.included if fulltext else 0,
    )
    run.prisma = prisma.model_dump()
    # PRISMA-S: persist the executed searches verbatim so the appendix and
    # the compliance checklist can be rendered later via the API
    recorder.emit(
        StageName.REPORT,
        "search_executions",
        {"executions": [e.model_dump(mode="json") for e in executions]},
    )
    # Serialize finalization with API control changes. A late pause must not
    # become "completed", and a cancelled run must not be reopened by this
    # process's older ORM state while its fenced lease is being stopped.
    session.refresh(run, attribute_names=["status"], with_for_update=True)
    latest_signal = control.poll(run.id, org_id=run.org_id)
    if run.status == RunStatus.CANCELLED or latest_signal == control.CANCEL:
        control_signal = control.CANCEL
    elif latest_signal:
        control_signal = latest_signal
    if control_signal == control.CANCEL:
        set_status(run, RunStatus.CANCELLED)
        final_event = "run_cancelled"
    elif control_signal == control.PAUSE or budget_paused or capacity_paused or provider_paused:
        set_status(run, RunStatus.PAUSED)  # resumable — work so far is persisted
        final_event = "run_paused"
    else:
        set_status(run, RunStatus.COMPLETED)
        final_event = "run_completed"
    # Only an explicit resume clears a pause, atomically with its queued job.
    if final_event != "run_paused":
        run.finished_at = datetime.now(UTC)
    # closing chat message: the assistant walks the researcher through what
    # the finished search found. Written BEFORE the terminal event so the
    # summary is already committed when clients react to `done` and refetch.
    # Best-effort — a summary failure never fails the run itself.
    if final_event == "run_completed" and pool is not None and pool.has_strong() and ranked:
        with suppress(Exception):  # deliberately non-fatal
            summarize_completed_run(
                session,
                run,
                pool,
                protocol=protocol,
                prisma=prisma,
                ranked=(
                    sorted(
                        [item for item in ranked_works if item.work.id in set(selected_ids)],
                        key=lambda item: selected_ids.index(item.work.id),
                    )
                    if final_paper_limit > 0
                    else ranked_works
                ),
                grey_sources=len(web_sources),
            )

    recorder.emit(
        StageName.REPORT,
        final_event,
        {
            "prisma": prisma.model_dump(),
            "llm_spent_usd": round(pool.budget.spent_usd, 4) if pool else 0.0,
            "budget_paused": budget_paused,
            "capacity_paused": capacity_paused,
            "provider_paused": provider_paused,
            "control": control_signal or None,
            "quality_warnings": quality_warnings,
        },
    )
    if final_event in {"run_completed", "run_cancelled"} or provider_paused:
        action_id = str((run.config or {}).get("cost_action_id") or "")
        if action_id:
            finish_ai_action(
                session,
                action_id,
                status="paused" if provider_paused else "settled",
            )
    session.flush()

    return RunResult(
        run_id=run.id,
        protocol=protocol,
        corpus_version=corpus_version.version,
        prisma=prisma,
        queries_executed=queries_executed,
        search_executions=executions,
        ranked=ranked_works[:10],
        decisions=decisions,
        integrity_flags=integrity_flagged,
        coverage=coverage,
        recall_certification=recall_certification,
        hit_calibration=hit_calibration,
        canary=canary,
        acquisition=acquisition,
        fulltext=fulltext,
        web_sources=web_sources,
        quality_warnings=quality_warnings,
        llm_spent_usd=pool.budget.spent_usd if pool else 0.0,
        budget_paused=budget_paused,
    )


def resume_run(
    session: Session,
    run: Run,
    *,
    corpus: DuckDBCorpus,
    approved_protocol: ReviewProtocol,
    pool: LLMPool | None = None,
    live: bool = False,
    screen: bool = False,
    paper_limit: int = 0,
    screen_limit: int = 0,
    exhaustive: bool = True,
    retrieval_limit: int = 100_000,
    canary_ids: list[str] | None = None,
    acquire: bool = False,
    full_text_screen: bool = False,
    web_search: bool = False,
    snowball: bool = False,
    snowball_rounds: int = SNOWBALL_ROUNDS,
    semantic: bool = False,
    imported: list[WorkRecord] | None = None,
    imported_meta: list[dict[str, Any]] | None = None,
) -> RunResult:
    """Continue a gated run past the Protocol Gate with the approved protocol.

    Every run flag must survive the gate — the year window and peer-review
    filter ride inside the approved protocol, the rest is passed through.
    """
    return execute_run(
        session,
        run,
        corpus=corpus,
        pool=pool,
        live=live,
        screen=screen,
        paper_limit=paper_limit,
        screen_limit=screen_limit,
        exhaustive=exhaustive,
        retrieval_limit=retrieval_limit,
        canary_ids=canary_ids,
        acquire=acquire,
        full_text_screen=full_text_screen,
        web_search=web_search,
        snowball=snowball,
        snowball_rounds=snowball_rounds,
        semantic=semantic,
        imported=imported,
        imported_meta=imported_meta,
        approved_protocol=approved_protocol,
    )
