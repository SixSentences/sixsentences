"""CLI (`six`): developer and operator entry point for the pipeline.

six corpus sync [--limit N]        ingest a CS/ML slice into the MicroCorpus
six corpus info                    show corpus version + counts
six providers                      show configured LLM providers + routing
six auth create-owner --email EMAIL   bootstrap the first tenant owner securely
six retractions sync               download the Retraction Watch dataset
six run --question "..."           exhaustive pipeline run (corpus-first)
six usage --run N                  LLM usage + cost breakdown for a run
six methods --run N                citable methods paragraph (real numbers)
six chat --run N -q "..."          grounded Q&A over a run's results
six export --run N --format bibtex bibliography export (bibtex|ris|csl)
six eval --dataset --query         SYNERGY recall report
six quality predict                execute the shipped pipeline on a pinned suite
six quality evaluate               evaluate all review stages against a pinned suite
six db init                        create database tables
six runs cancel --public-id ID     stop one active run with an audit event
six api                            start the API server
"""

from datetime import UTC, datetime
from pathlib import Path

import typer
import uvicorn
from sqlalchemy import select

from sixsentences_server.chat.service import ChatError, answer_question
from sixsentences_server.config import get_settings
from sixsentences_server.connectors.openalex import OpenAlexClient
from sixsentences_server.connectors.retractions import download_retractions
from sixsentences_server.core.auth import AuthError, provision_owner
from sixsentences_server.core.db import Project as ProjectRow
from sixsentences_server.core.db import (
    Run,
    RunEvent,
    db_session,
    get_default_org,
    init_db,
)
from sixsentences_server.core.entitlements import finish_ai_action
from sixsentences_server.core.models import RunStatus
from sixsentences_server.core.state import is_terminal, set_status
from sixsentences_server.corpus.duckdb_store import CorpusManifestError, DuckDBCorpus
from sixsentences_server.corpus.ingest import sync_corpus, sync_micro_corpus
from sixsentences_server.corpus.manifest import production_corpus_plan
from sixsentences_server.jobs import cancel_run_jobs
from sixsentences_server.llm.base import BudgetGovernor, LLMConfigError
from sixsentences_server.llm.pool import LLMPool, RoutingConfig
from sixsentences_server.llm.providers import PROVIDERS
from sixsentences_server.pipeline.run import execute_run
from sixsentences_server.reporting.exports import (
    EXTENSIONS,
    FORMATS,
    render,
    works_for_run,
)
from sixsentences_server.reporting.methods import render_methods
from sixsentences_server.reporting.prisma import (
    render_flow_text,
    render_search_appendix,
)
from sixsentences_server.reporting.usage import usage_for_run

app = typer.Typer(no_args_is_help=True, add_completion=False)
corpus_app = typer.Typer(no_args_is_help=True)
retractions_app = typer.Typer(no_args_is_help=True)
db_app = typer.Typer(no_args_is_help=True)
auth_app = typer.Typer(no_args_is_help=True)
runs_app = typer.Typer(no_args_is_help=True)
quality_app = typer.Typer(no_args_is_help=True)
app.add_typer(corpus_app, name="corpus")
app.add_typer(retractions_app, name="retractions")
app.add_typer(db_app, name="db")
app.add_typer(auth_app, name="auth")
app.add_typer(runs_app, name="runs")
app.add_typer(quality_app, name="quality")


def _runtime_revision_is_attested() -> bool:
    """Return whether this runtime identifies a committed revision."""

    revision = get_settings().release_git_revision.strip().lower()
    return len(revision) == 40 and all(character in "0123456789abcdef" for character in revision)


@auth_app.command("create-owner")
def auth_create_owner(
    email: str = typer.Option(..., "--email", help="owner email"),
    org: str = typer.Option("", "--org", help="workspace name"),
    first_name: str = typer.Option("", "--first-name", help="owner display name"),
) -> None:
    """Create a tenant owner without emitting a token or accepting a password flag."""
    password = typer.prompt(
        "Password",
        hide_input=True,
        confirmation_prompt=True,
    )
    init_db()
    with db_session() as session:
        try:
            user = provision_owner(
                session,
                email,
                password,
                org,
                first_name,
                email_verified=True,
            )
            created_email = user.email
            created_org_id = user.org_id
        except AuthError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from exc
    typer.echo(f"created tenant owner {created_email} for workspace #{created_org_id}")
    typer.echo("Sign in through the application to create a session.")


def _corpus() -> DuckDBCorpus:
    return DuckDBCorpus(get_settings().corpus_dir)


def _pool_if_configured() -> LLMPool | None:
    settings = get_settings()
    try:
        pool = LLMPool.from_environment(
            BudgetGovernor(limit_usd=settings.llm_budget_usd),
            routing_json=settings.llm_routing,
        )
    except LLMConfigError as exc:
        typer.echo(f"llm routing problem: {exc}", err=True)
        return None
    if not pool.clients:
        return None
    return pool


@corpus_app.command("sync")
def corpus_sync(
    limit: int | None = typer.Option(
        None,
        help="target works (default: 20,000 micro; 1,000,000 production)",
        min=1,
    ),
    from_year: int = typer.Option(2015, help="earliest publication year"),
    to_year: int = typer.Option(
        datetime.now(UTC).year,
        help="latest publication year",
    ),
    profile: str = typer.Option(
        "micro",
        help="micro (single CS slice) or production (balanced four-domain release)",
    ),
) -> None:
    """Build an OpenAlex corpus; production generations remain staged."""
    settings = get_settings()
    client = OpenAlexClient(mailto=settings.openalex_mailto, api_key=settings.openalex_api_key)
    if profile not in {"micro", "production"}:
        typer.echo("profile must be 'micro' or 'production'", err=True)
        raise typer.Exit(2)
    resolved_limit = limit or (1_000_000 if profile == "production" else 20_000)
    plan = (
        production_corpus_plan(
            target_works=resolved_limit,
            from_year=from_year,
            to_year=to_year,
        )
        if profile == "production"
        else None
    )
    digest = f", plan={plan.digest}" if plan is not None else ""
    typer.echo(
        f"syncing {profile} corpus (target={resolved_limit}, years={from_year}-{to_year}"
        f"{digest}) ..."
    )

    def progress(n: int) -> None:
        typer.echo(f"  fetched {n} unique works")

    if profile == "production":
        assert plan is not None
        result = sync_corpus(
            _corpus(),
            client,
            plan=plan,
            progress=progress,
            reserve_bytes=settings.storage_reserve_bytes,
            snapshots_to_keep=settings.corpus_snapshots_to_keep,
            activate=False,
        )
    else:
        result = sync_micro_corpus(
            _corpus(),
            client,
            limit=resolved_limit,
            from_year=from_year,
            to_year=to_year,
            progress=progress,
            reserve_bytes=settings.storage_reserve_bytes,
            snapshots_to_keep=settings.corpus_snapshots_to_keep,
        )
    state = "active" if result.activated else "staged, not active"
    typer.echo(
        f"done: {result.works} works -> {result.version} (generation={result.generation}; {state})"
    )
    if not result.activated:
        typer.echo(
            "run the pinned quality suites and cost scenarios, then use "
            "`six corpus promote` with their reports"
        )


@corpus_app.command("info")
def corpus_info() -> None:
    corpus = _corpus()
    if not corpus.exists():
        typer.echo("no corpus synced yet; run `six corpus sync`")
        raise typer.Exit(1)
    info = corpus.info()
    typer.echo(f"version:    {info['version']}")
    typer.echo(f"works:      {info['works']}")
    typer.echo(f"bytes:      {info['bytes']}")
    typer.echo(f"snapshots:  {info['snapshots']}")
    if info["sha256"]:
        typer.echo(f"sha256:     {info['sha256']}")
    for source, descriptor in info["sources"].items():
        typer.echo(f"source:  {source}: {descriptor}")


@corpus_app.command("verify")
def corpus_verify() -> None:
    """Hash, count, de-duplicate, and schema-check the active generation."""

    try:
        result = _corpus().verify()
    except CorpusManifestError as exc:
        typer.echo(f"FAILED: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"verified {result['version']}: {result['works']:,} unique works, "
        f"{result['bytes']:,} bytes, sha256={result['sha256']}"
    )


@corpus_app.command("snapshots")
def corpus_snapshots() -> None:
    """List retained corpus generations and the active rollback target."""

    snapshots = _corpus().list_snapshots()
    if not snapshots:
        typer.echo("no versioned corpus snapshots")
        return
    for item in snapshots:
        marker = "*" if item["active"] else " "
        typer.echo(
            f"{marker} {item['generation']}  {item['works']:,} works  "
            f"{item['version']}  {item['created_at'] or ''}"
        )


@corpus_app.command("rollback")
def corpus_rollback(
    generation: str = typer.Option(..., "--generation", help="retained generation id"),
) -> None:
    """Verify and atomically reactivate a retained corpus generation."""

    settings = get_settings()
    try:
        _corpus().activate_generation(
            generation,
            snapshots_to_keep=settings.corpus_snapshots_to_keep,
            require_release_evidence=False,
        )
        result = _corpus().verify()
    except CorpusManifestError as exc:
        typer.echo(f"FAILED: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"active corpus rolled back to {result['version']} ({result['works']:,} works)")


@corpus_app.command("verify-release")
def corpus_verify_release(
    generation: str | None = typer.Option(
        None,
        "--generation",
        help="generation id (default: active generation)",
    ),
) -> None:
    """Verify all stored quality, regression, cost, and corpus release evidence."""

    from sixsentences_server.evals.release import (
        ReleaseGateError,
        render_release_evidence,
        verify_release_evidence,
    )

    corpus = _corpus()
    resolved = generation
    if resolved is None:
        if not corpus.exists():
            typer.echo("FAILED: no active corpus", err=True)
            raise typer.Exit(1)
        resolved = corpus.works_path.parent.name
    try:
        evidence = verify_release_evidence(corpus, resolved)
    except (CorpusManifestError, ReleaseGateError) as exc:
        typer.echo(f"FAILED: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(render_release_evidence(evidence))
    typer.echo("release evidence: verified")


@corpus_app.command("promote")
def corpus_promote(
    generation: str = typer.Option(..., "--generation", help="staged generation id"),
    quality_report: Path = typer.Option(..., "--quality-report", exists=True, dir_okay=False),
    quality_suite: Path = typer.Option(..., "--quality-suite", exists=True, dir_okay=False),
    quality_predictions: Path = typer.Option(
        ..., "--quality-predictions", exists=True, dir_okay=False
    ),
    cost_reports: list[Path] = typer.Option(..., "--cost-report", exists=True, dir_okay=False),
    cost_calibration: Path = typer.Option(
        ...,
        "--cost-calibration",
        exists=True,
        dir_okay=False,
        help="passing calibration from a completed representative review",
    ),
    suite_rebaseline_reason: str | None = typer.Option(
        None,
        "--suite-rebaseline-reason",
        help="audited reason when intentionally changing the golden suite",
    ),
    approve_active_unreleased_beta: bool = typer.Option(
        False,
        "--approve-active-unreleased-beta",
        help=(
            "Explicitly make the first formal release from the exact active production beta "
            "when, and only when, it has no release receipt"
        ),
    ),
) -> None:
    """Promote a staged generation only after quality and cost gates pass."""

    from sixsentences_server.evals.release import (
        ReleaseGateError,
        promote_corpus_release,
        render_release_evidence,
    )

    settings = get_settings()
    try:
        evidence = promote_corpus_release(
            _corpus(),
            generation=generation,
            quality_report_path=quality_report,
            quality_suite_path=quality_suite,
            quality_predictions_path=quality_predictions,
            cost_report_paths=cost_reports,
            cost_calibration_path=cost_calibration,
            snapshots_to_keep=settings.corpus_snapshots_to_keep,
            suite_rebaseline_reason=suite_rebaseline_reason,
            approve_active_unreleased_beta=approve_active_unreleased_beta,
        )
    except (CorpusManifestError, ReleaseGateError) as exc:
        typer.echo(f"FAILED: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(render_release_evidence(evidence))


@app.command("providers")
def providers_cmd() -> None:
    """Show LLM providers (configured = API key present) and effective routing."""
    settings = get_settings()
    typer.echo("providers:")
    for spec in PROVIDERS.values():
        if spec.available():
            state = "configured"
        elif spec.name == "gemini":
            state = f"missing {spec.key_env} or data-processing confirmation"
        else:
            state = f"missing {spec.key_env}"
        typer.echo(f"  {spec.name:<10} {state:<28} cheap={spec.cheap}  strong={spec.strong}")
    routing = (
        RoutingConfig.from_json(settings.llm_routing)
        if settings.llm_routing
        else RoutingConfig.defaults()
    )
    typer.echo("\neffective routing:")
    typer.echo(f"  synthesis:    {routing.synthesis or '(none — heuristic fallback)'}")
    typer.echo(f"  adjudication: {routing.adjudication or '(none)'}")
    if routing.screening:
        typer.echo("  screening ensemble (cross-vendor):")
        for ref in routing.screening:
            typer.echo(f"    - {ref}")
    else:
        typer.echo("  screening ensemble: (none — stub marks everything unsure)")
    typer.echo(f"\nper-run budget: ${settings.llm_budget_usd:.2f} (SIX_LLM_BUDGET_USD)")


@retractions_app.command("sync")
def retractions_sync() -> None:
    """Download the Retraction Watch dataset (Crossref, daily public CSV)."""
    typer.echo("downloading retraction data (tens of MB) ...")
    path = download_retractions(get_settings().data_dir)
    typer.echo(f"saved to {path}")


@db_app.command("init")
def db_init() -> None:
    init_db()
    typer.echo("database tables created")


@runs_app.command("cancel")
def runs_cancel(
    public_id: str = typer.Option(..., "--public-id", help="public run id"),
) -> None:
    """Administratively cancel one active run through the persisted state machine."""

    init_db()
    with db_session() as session:
        run = session.scalar(select(Run).where(Run.public_id == public_id))
        if run is None:
            typer.echo(f"run {public_id!r} not found", err=True)
            raise typer.Exit(1)
        cancel_run_jobs(session, run.id)
        if is_terminal(run.status):
            typer.echo(f"run {public_id} is already {run.status}")
            return

        set_status(run, RunStatus.CANCELLED)
        run.finished_at = datetime.now(UTC)
        session.add(
            RunEvent(
                org_id=run.org_id,
                run_id=run.id,
                stage="report",
                event="run_cancelled",
                payload={"control": "operator_cli"},
            )
        )
        action_id = str((run.config or {}).get("cost_action_id") or "")
        if action_id:
            finish_ai_action(session, action_id, status="cancelled")
        run_id = run.id

    typer.echo(f"cancelled run {public_id} (#{run_id})")


@app.command("run")
def run_cmd(
    question: str = typer.Option(..., "--question", "-q", help="research question"),
    query: str = typer.Option("", help="boolean query override (expert mode)"),
    project: str = typer.Option("default", help="project name"),
    live: bool = typer.Option(False, help="add the OpenAlex live freshness layer"),
    screen: bool = typer.Option(False, help="run ensemble title/abstract screening"),
    screen_limit: int = typer.Option(
        0, help="max works to screen; 0 = everything (exhaustive default)"
    ),
    fast: bool = typer.Option(
        False,
        "--fast",
        help="iteration mode: skip query expansion and cap screening at 50 "
        "(the product default is exhaustive; expect long runs)",
    ),
    limit: int = typer.Option(100_000, help="corpus retrieval cap per query"),
    canary: str = typer.Option("", help="comma-separated known must-hit OpenAlex ids"),
    acquire: bool = typer.Option(
        False, help="seek open-access full text for included works (needs network)"
    ),
    fulltext: bool = typer.Option(
        False,
        "--fulltext",
        help="second-pass full-text eligibility screening (implies --screen --acquire)",
    ),
    year_from: int = typer.Option(0, "--year-from", help="earliest publication year (0=no bound)"),
    year_to: int = typer.Option(0, "--year-to", help="latest publication year (0=no bound)"),
    peer_reviewed: bool = typer.Option(
        False, "--peer-reviewed", help="exclude preprints + non-article types"
    ),
    web: bool = typer.Option(
        False,
        "--web",
        help="also collect grey-literature web sources (needs a web key)",
    ),
) -> None:
    """Execute an exhaustive pipeline run against the local corpus."""
    init_db()
    corpus = _corpus()
    pool = _pool_if_configured()
    canary_ids = [c.strip() for c in canary.split(",") if c.strip()]

    if fulltext:  # full-text screening needs screened includes with acquired text
        screen = acquire = True
    if fast and screen_limit == 0:
        screen_limit = 50

    with db_session() as session:
        org = get_default_org(session)
        project_row = (
            session.query(ProjectRow)
            .filter(ProjectRow.org_id == org.id, ProjectRow.name == project)
            .one_or_none()
        )
        if project_row is None:
            project_row = ProjectRow(org_id=org.id, name=project)
            session.add(project_row)
            session.flush()
        run = Run(
            org_id=org.id,
            project_id=project_row.id,
            question=question,
            status="pending",
        )
        session.add(run)
        session.flush()

        result = execute_run(
            session,
            run,
            corpus=corpus,
            pool=pool,
            query_override=query or None,
            live=live,
            screen=screen,
            screen_limit=screen_limit,
            exhaustive=not fast,
            retrieval_limit=limit,
            canary_ids=canary_ids or None,
            acquire=acquire,
            full_text_screen=fulltext,
            year_from=year_from or None,
            year_to=year_to or None,
            peer_reviewed_only=peer_reviewed,
            web_search=web,
        )
        methods_text = render_methods(session, run)

    typer.echo(f"\nrun #{result.run_id}  (corpus {result.corpus_version})")
    typer.echo(f"protocol by: {result.protocol.synthesized_by}")
    typer.echo(f"queries executed: {len(result.queries_executed)}")
    for executed_query in result.queries_executed:
        typer.echo(f"  - {executed_query}")
    typer.echo("")
    typer.echo(render_flow_text(result.prisma))
    if result.hit_calibration is not None:
        hc = result.hit_calibration
        typer.echo(f"\nquery calibration: {hc.verdict} — {hc.note}")
    if result.canary is not None:
        cn = result.canary
        typer.echo(
            f"canary recall: {cn.found}/{cn.total} ({cn.recall:.0%})"
            + (f"  missed: {', '.join(cn.missed)}" if cn.missed else "")
        )
    if result.coverage is not None:
        cov = result.coverage
        if cov.method == "chao2":
            typer.echo(
                f"\nestimated search completeness: {cov.completeness:.1%} "
                f"[{cov.ci_low:.1%}, {cov.ci_high:.1%}]  ({cov.note})"
            )
        else:
            typer.echo(f"\nsearch completeness: undetermined ({cov.note})")
    if result.llm_spent_usd:
        typer.echo(f"\nllm spend: ${result.llm_spent_usd:.4f}")
    if result.budget_paused:
        typer.echo(
            "NOTE: budget exhausted — screening paused honestly; raise "
            "SIX_LLM_BUDGET_USD and re-run to continue."
        )
    typer.echo("\n-- search appendix (PRISMA-S) " + "-" * 30)
    typer.echo(render_search_appendix(result.search_executions))
    if result.ranked:
        typer.echo("\ntop works (decomposed score  rel/imp/rec):")
        for rw in result.ranked:
            s = rw.signals
            flag = " [RETRACTED]" if rw.retracted else ""
            typer.echo(
                f"  {rw.score:.3f}  "
                f"{s.relevance:.2f}/{s.impact:.2f}/{s.recency:.2f}  "
                f"{rw.work.title[:70]}{flag}"
            )
    if result.integrity_flags:
        typer.echo("\nintegrity flags:")
        for report in result.integrity_flags[:10]:
            typer.echo(f"  {report.severity.value:8} {report.work_id}  {report.reason[:70]}")
    if result.decisions:
        typer.echo("\nscreening decisions (first 10):")
        for decision in result.decisions[:10]:
            typer.echo(f"  {decision.verdict.value:8} {decision.work_id} — {decision.reason[:70]}")
            if decision.quote:
                typer.echo(f'           evidence: "{decision.quote[:80]}"')
    if result.acquisition is not None:
        acq = result.acquisition
        typer.echo(
            f"\nfull-text acquisition: {acq.retrieved}/{acq.sought} retrieved "
            f"({acq.parsed} parsed, {acq.stored_unparsed} stored-unparsed, "
            f"{acq.not_retrieved} not retrieved)"
        )
        for basis, n in sorted(acq.by_legal_basis.items()):
            typer.echo(f"  {n:4}  {basis}")
    if result.fulltext is not None:
        ft = result.fulltext
        typer.echo(
            f"\nfull-text eligibility: {ft.assessed} assessed -> {ft.included} studies included, "
            f"{ft.excluded} excluded, {ft.unsure} unsure "
            f"({ft.quotes_verified} quotes verified vs full text)"
        )
        for exclusion in ft.exclusions[:5]:
            typer.echo(f"  excluded {exclusion['id']}: {exclusion['reason'][:66]}")
    if result.web_sources:
        typer.echo(f"\ngrey-literature web sources ({len(result.web_sources)}, non-academic):")
        for src in result.web_sources[:8]:
            typer.echo(f"  {src.quality:.2f} {src.category:<13} {src.domain:<22} {src.title[:44]}")
    cert = result.recall_certification
    if cert is not None:
        if cert.method == "chao2":
            mark = "CERTIFIED" if cert.certified else "not certified"
            typer.echo(
                f"\nscreening recall: {cert.estimated_recall:.1%} "
                f"[{cert.ci_low:.1%}, {cert.ci_high:.1%}]  ({mark}; {cert.note})"
            )
        else:
            typer.echo(f"\nscreening recall: undetermined ({cert.note})")
    typer.echo("\n-- methods paragraph (citable) " + "-" * 29)
    typer.echo(methods_text)


@app.command("methods")
def methods_cmd(run: int = typer.Option(..., "--run", help="run id")) -> None:
    """Print the pre-drafted, citable methods paragraph for a run."""
    init_db()
    with db_session() as session:
        run_row = session.get(Run, run)
        if run_row is None:
            typer.echo(f"run {run} not found", err=True)
            raise typer.Exit(1)
        typer.echo(render_methods(session, run_row))


@app.command("usage")
def usage_cmd(run: int = typer.Option(..., "--run", help="run id")) -> None:
    """Show LLM usage and provider cost for a run."""
    init_db()
    with db_session() as session:
        summary = usage_for_run(session, run)
    typer.echo(f"run #{run} LLM usage")
    typer.echo(
        f"  total: ${summary.total_cost_usd:.4f}  {summary.total_calls} calls  "
        f"{summary.total_input_tokens} in / {summary.total_output_tokens} out tokens"
    )
    if summary.by_task:
        typer.echo("  by task:")
        for task, bucket in summary.by_task.items():
            typer.echo(f"    {task:22} {bucket.calls:>4} calls  ${bucket.cost_usd:.4f}")
    if summary.by_provider:
        typer.echo("  by provider:")
        for provider, bucket in summary.by_provider.items():
            typer.echo(f"    {provider:22} {bucket.calls:>4} calls  ${bucket.cost_usd:.4f}")


@app.command("chat")
def chat_cmd(
    run: int = typer.Option(..., "--run", help="run id to ask about"),
    question: str = typer.Option(..., "--question", "-q", help="your question"),
) -> None:
    """Ask an AI about a run's results (answered only from the run's works)."""
    init_db()
    pool = _pool_if_configured()
    if pool is None:
        typer.echo("no LLM provider configured (set a provider key)", err=True)
        raise typer.Exit(1)
    with db_session() as session:
        run_row = session.get(Run, run)
        if run_row is None:
            typer.echo(f"run {run} not found", err=True)
            raise typer.Exit(1)
        try:
            answer = answer_question(session, run_row, pool, question)
        except ChatError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(1) from exc
    typer.echo(answer.answer)
    if answer.citations:
        typer.echo("\nsources:")
        for citation in answer.citations:
            typer.echo(f"  [{citation.id}] {citation.title[:70]}")


@app.command("export")
def export_cmd(
    run: int = typer.Option(..., "--run", help="run id to export"),
    fmt: str = typer.Option("bibtex", "--format", help=f"one of {sorted(FORMATS)}"),
    included_only: bool = typer.Option(
        False, "--included-only", help="only works with an INCLUDE decision"
    ),
    out: Path | None = typer.Option(None, "--out", help="write to file instead of stdout"),
) -> None:
    """Export a run's works as BibTeX, RIS or CSL-JSON."""
    if fmt not in FORMATS:
        typer.echo(f"unknown format {fmt!r}; choose from {sorted(FORMATS)}", err=True)
        raise typer.Exit(1)
    init_db()
    with db_session() as session:
        works = works_for_run(session, run, included_only=included_only)
    if not works:
        typer.echo(f"no works found for run {run}", err=True)
        raise typer.Exit(1)
    text = render(works, fmt)
    if out is not None:
        target = out if out.suffix else out.with_suffix(f".{EXTENSIONS[fmt]}")
        target.write_text(text, encoding="utf-8")
        typer.echo(f"wrote {len(works)} works to {target}")
    else:
        typer.echo(text)


@app.command("eval")
def eval_cmd(
    dataset: str = typer.Option(..., help="SYNERGY dataset name, e.g. Hall_2012"),
    query: str = typer.Option(..., help="boolean query to evaluate"),
) -> None:
    """Golden-set recall report: corpus coverage + retrieval recall (SYNERGY)."""
    from sixsentences_server.evals.golden import evaluate_retrieval_recall

    report = evaluate_retrieval_recall(_corpus(), dataset, query)
    typer.echo(report.render())


@quality_app.command("evaluate")
def quality_evaluate(
    suite: Path = typer.Option(..., "--suite", exists=True, dir_okay=False),
    predictions: Path = typer.Option(..., "--predictions", exists=True, dir_okay=False),
    out: Path = typer.Option(..., "--out", dir_okay=False),
    fail_on_gate: bool = typer.Option(True, "--fail-on-gate/--no-fail-on-gate"),
) -> None:
    """Score a complete review prediction run and write immutable JSON evidence."""

    from sixsentences_server.evals.quality import (
        evaluate_quality,
        load_predictions,
        load_suite,
        render_quality_report,
        write_report,
    )

    report = evaluate_quality(load_suite(suite), load_predictions(predictions))
    write_report(out, report)
    typer.echo(render_quality_report(report))
    typer.echo(f"evidence: {out}")
    if fail_on_gate and not report.passed:
        raise typer.Exit(1)


@quality_app.command("predict")
def quality_predict(
    suite: Path = typer.Option(..., "--suite", exists=True, dir_okay=False),
    generation: str = typer.Option(..., "--generation"),
    out: Path = typer.Option(..., "--out", dir_okay=False),
    budget_usd: float = typer.Option(..., "--budget-usd", min=0.01),
    live: bool = typer.Option(False, "--live/--no-live"),
    retrieval_limit: int = typer.Option(100_000, "--retrieval-limit", min=1),
    screen_limit: int = typer.Option(1_000, "--screen-limit", min=1),
    live_limit: int = typer.Option(5_000, "--live-limit", min=1),
    acquire: bool = typer.Option(True, "--acquire/--no-acquire"),
    full_text: bool = typer.Option(True, "--full-text/--no-full-text"),
    peer_reviewed: bool = typer.Option(False, "--peer-reviewed"),
    confirm_provider_spend: bool = typer.Option(False, "--confirm-provider-spend"),
    shard_count: int = typer.Option(1, "--shard-count", min=1),
    shard_index: int = typer.Option(0, "--shard-index", min=0),
) -> None:
    """Run the real review pipeline and export release-quality predictions."""

    import subprocess

    from sixsentences_server.evals.predict import (
        PREDICTION_RUNNER,
        PREDICTION_SHARD_RUNNER,
        PredictionRunError,
        QualityPredictionConfig,
        generate_predictions,
        partition_suite_cases,
        write_predictions,
    )
    from sixsentences_server.evals.quality import inspect_suite, load_suite

    if not confirm_provider_spend:
        typer.echo(
            "refusing provider calls without --confirm-provider-spend; "
            f"the hard suite budget is ${budget_usd:.2f}",
            err=True,
        )
        raise typer.Exit(2)
    if out.exists():
        typer.echo(f"refusing to overwrite immutable prediction artifact: {out}", err=True)
        raise typer.Exit(2)
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        if not _runtime_revision_is_attested():
            typer.echo(f"could not verify the release Git worktree: {exc}", err=True)
            raise typer.Exit(2) from exc
        dirty = ""
    if dirty:
        typer.echo("release predictions require a clean Git worktree", err=True)
        raise typer.Exit(2)
    loaded = load_suite(suite)
    try:
        partitions = partition_suite_cases(loaded, shard_count)
    except ValueError as exc:
        typer.echo(f"invalid release-evaluation shard plan: {exc}", err=True)
        raise typer.Exit(2) from exc
    if shard_index >= shard_count:
        typer.echo("--shard-index must be lower than --shard-count", err=True)
        raise typer.Exit(2)
    selected_case_ids = set(partitions[shard_index]) if shard_count > 1 else None
    prediction_runner = PREDICTION_SHARD_RUNNER if shard_count > 1 else PREDICTION_RUNNER
    inventory = inspect_suite(loaded)
    if not inventory.release_ready:
        typer.echo(
            "suite does not satisfy production provenance and coverage gates; "
            "run `six quality inspect` for details",
            err=True,
        )
        raise typer.Exit(2)
    settings = get_settings()
    try:
        pool = LLMPool.from_environment(
            BudgetGovernor(limit_usd=budget_usd),
            routing_json=settings.llm_routing,
        )
    except LLMConfigError as exc:
        typer.echo(f"llm routing problem: {exc}", err=True)
        raise typer.Exit(2) from exc
    if not pool.clients:
        typer.echo("no release-evaluation LLM routes are configured", err=True)
        raise typer.Exit(2)
    corpus = DuckDBCorpus(settings.corpus_dir, generation=generation)
    try:
        corpus.generation_manifest(generation)
    except CorpusManifestError as exc:
        typer.echo(f"candidate corpus problem: {exc}", err=True)
        raise typer.Exit(2) from exc
    init_db()

    def progress(index: int, total: int, case_id: str) -> None:
        typer.echo(f"[{index}/{total}] {case_id}")

    try:
        controls = QualityPredictionConfig(
            live=live,
            retrieval_limit=retrieval_limit,
            screen_limit=screen_limit,
            live_limit=live_limit,
            acquire=acquire,
            full_text_screen=full_text,
            peer_reviewed_only=peer_reviewed,
        )
    except ValueError as exc:
        typer.echo(f"invalid release-evaluation controls: {exc}", err=True)
        raise typer.Exit(2) from exc
    try:
        with db_session() as session:
            predictions = generate_predictions(
                session,
                loaded,
                corpus=corpus,
                pool=pool,
                config=controls,
                on_progress=progress,
                case_ids=selected_case_ids,
                generated_by=prediction_runner,
            )
    except PredictionRunError as exc:
        typer.echo(f"prediction run failed closed: {exc}", err=True)
        raise typer.Exit(1) from exc
    try:
        write_predictions(out, predictions)
    except FileExistsError as exc:
        typer.echo(
            f"prediction artifact appeared during the run and was not overwritten: {out}",
            err=True,
        )
        raise typer.Exit(1) from exc
    typer.echo(f"wrote {len(predictions.predictions)} complete cases to {out}")
    typer.echo(f"prediction digest: {predictions.digest}")
    typer.echo(f"provider spend: ${pool.budget.spent_usd:.4f}")
    if shard_count > 1:
        typer.echo(
            f"shard: {shard_index + 1}/{shard_count} "
            f"({len(predictions.predictions)} cases; merge required before evaluation)"
        )


@quality_app.command("merge-predictions")
def quality_merge_predictions(
    suite: Path = typer.Option(..., "--suite", exists=True, dir_okay=False),
    shards: list[Path] = typer.Option(..., "--shard", exists=True, dir_okay=False),
    out: Path = typer.Option(..., "--out", dir_okay=False),
) -> None:
    """Merge complete, matching prediction shards into canonical evidence."""

    from sixsentences_server.evals.predict import (
        PredictionRunError,
        merge_prediction_shards,
        write_predictions,
    )
    from sixsentences_server.evals.quality import load_predictions, load_suite

    if out.exists():
        typer.echo(f"refusing to overwrite immutable prediction artifact: {out}", err=True)
        raise typer.Exit(2)
    try:
        merged = merge_prediction_shards(
            load_suite(suite),
            [load_predictions(path) for path in shards],
        )
        write_predictions(out, merged)
    except (PredictionRunError, FileExistsError, ValueError) as exc:
        typer.echo(f"prediction merge failed closed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"wrote {len(merged.predictions)} complete cases to {out}")
    typer.echo(f"prediction digest: {merged.digest}")


@quality_app.command("inspect")
def quality_inspect(
    suite: Path = typer.Option(..., "--suite", exists=True, dir_okay=False),
    fail_on_gate: bool = typer.Option(True, "--fail-on-gate/--no-fail-on-gate"),
) -> None:
    """Inspect source provenance and coverage before running an evaluation."""

    from sixsentences_server.evals.quality import (
        inspect_suite,
        load_suite,
        render_suite_inventory,
    )

    loaded = load_suite(suite)
    inventory = inspect_suite(loaded)
    typer.echo(render_suite_inventory(inventory))
    typer.echo("")
    for source in loaded.sources:
        typer.echo(
            f"source:   {source.name}@{source.version} [{source.kind}] "
            f"{source.license} sha256={source.sha256}"
        )
    if fail_on_gate and not inventory.release_ready:
        raise typer.Exit(1)


@quality_app.command("compare")
def quality_compare(
    baseline: Path = typer.Option(..., "--baseline", exists=True, dir_okay=False),
    candidate: Path = typer.Option(..., "--candidate", exists=True, dir_okay=False),
    out: Path = typer.Option(..., "--out", dir_okay=False),
    fail_on_gate: bool = typer.Option(True, "--fail-on-gate/--no-fail-on-gate"),
) -> None:
    """Compare a candidate report with the last approved quality baseline."""

    from sixsentences_server.evals.quality import (
        QualityReport,
        compare_quality_reports,
        render_regression_report,
        write_regression_report,
    )

    old = QualityReport.model_validate_json(baseline.read_text(encoding="utf-8"))
    new = QualityReport.model_validate_json(candidate.read_text(encoding="utf-8"))
    try:
        report = compare_quality_reports(old, new)
    except ValueError as exc:
        typer.echo(f"FAILED: {exc}", err=True)
        raise typer.Exit(1) from exc
    write_regression_report(out, report)
    typer.echo(render_regression_report(report))
    typer.echo(f"evidence: {out}")
    if fail_on_gate and not report.passed:
        raise typer.Exit(1)


@quality_app.command("build-webis")
def quality_build_webis(
    source: Path = typer.Option(..., "--source", exists=True, dir_okay=False),
    out: Path = typer.Option(..., "--out", dir_okay=False),
    suite_id: str = typer.Option("webis-sr4all", "--suite-id"),
    version: str = typer.Option(..., "--version"),
    source_uri: str = typer.Option(..., "--source-uri"),
    license_name: str = typer.Option(..., "--license"),
    license_verified: bool = typer.Option(False, "--license-verified"),
    limit: int | None = typer.Option(None, "--limit", min=1),
) -> None:
    """Build a pinned cross-disciplinary retrieval suite from normalized JSONL."""

    from sixsentences_server.evals.sources import load_webis_jsonl, write_suite

    suite = load_webis_jsonl(
        source,
        suite_id=suite_id,
        version=version,
        source_uri=source_uri,
        license_name=license_name,
        license_verified=license_verified,
        limit=limit,
    )
    write_suite(out, suite)
    domain_count = len({case.domain for case in suite.cases})
    typer.echo(f"wrote {len(suite.cases)} cases across {domain_count} domains")
    typer.echo(f"suite digest: {suite.digest}")


@quality_app.command("build-adjudicated")
def quality_build_adjudicated(
    source: Path = typer.Option(..., "--source", exists=True, dir_okay=False),
    out: Path = typer.Option(..., "--out", dir_okay=False),
    suite_id: str = typer.Option("sixsentences-adjudicated", "--suite-id"),
    version: str = typer.Option(..., "--version"),
    source_name: str = typer.Option("SixSentences adjudication", "--source-name"),
    source_uri: str = typer.Option(..., "--source-uri"),
) -> None:
    """Build strict human-adjudicated stage and citation evidence."""

    from sixsentences_server.evals.sources import load_adjudicated_jsonl, write_suite

    suite = load_adjudicated_jsonl(
        source,
        suite_id=suite_id,
        version=version,
        source_name=source_name,
        source_uri=source_uri,
    )
    write_suite(out, suite)
    typer.echo(f"wrote {len(suite.cases)} adjudicated cases to {out}")
    typer.echo(f"suite digest: {suite.digest}")


@quality_app.command("merge")
def quality_merge(
    suites: list[Path] = typer.Option(..., "--suite", exists=True, dir_okay=False),
    out: Path = typer.Option(..., "--out", dir_okay=False),
    suite_id: str = typer.Option(..., "--suite-id"),
    version: str = typer.Option(..., "--version"),
    title: str = typer.Option("SixSentences production quality suite", "--title"),
) -> None:
    """Merge independently pinned benchmark sources into one release suite."""

    from sixsentences_server.evals.quality import load_suite
    from sixsentences_server.evals.sources import merge_suites, write_suite

    merged = merge_suites(
        [load_suite(path) for path in suites],
        suite_id=suite_id,
        version=version,
        title=title,
    )
    write_suite(out, merged)
    typer.echo(f"wrote {len(merged.cases)} cases from {len(merged.sources)} sources")
    typer.echo(f"suite digest: {merged.digest}")


@quality_app.command("build-synergy")
def quality_build_synergy(
    spec: Path = typer.Option(..., "--spec", exists=True, dir_okay=False),
    source_dir: Path = typer.Option(..., "--source-dir", exists=True, file_okay=False),
    out: Path = typer.Option(..., "--out", dir_okay=False),
    suite_id: str = typer.Option("synergy", "--suite-id"),
    version: str = typer.Option(..., "--version"),
) -> None:
    """Build a pinned study-selection suite from locally downloaded SYNERGY data."""

    import json

    from sixsentences_server.evals.sources import (
        SynergyCaseSpec,
        build_synergy_suite,
        write_suite,
    )

    raw = json.loads(spec.read_text(encoding="utf-8"))
    specs = [SynergyCaseSpec.model_validate(item) for item in raw]
    suite = build_synergy_suite(
        specs,
        source_dir=source_dir,
        suite_id=suite_id,
        version=version,
    )
    write_suite(out, suite)
    typer.echo(f"wrote {len(suite.cases)} SYNERGY cases to {out}")
    typer.echo(f"suite digest: {suite.digest}")


@quality_app.command("cost")
def quality_cost(
    scenario: str = typer.Option("base", "--scenario", help="low, base, or high"),
    out: Path | None = typer.Option(None, "--out", dir_okay=False),
) -> None:
    """Estimate one complete review against runtime prices and plan guardrails."""

    from sixsentences_server.evals.economics import (
        CostRouting,
        estimate_review_cost,
        render_cost_report,
        standard_scenarios,
        write_cost_report,
    )

    scenarios = standard_scenarios()
    if scenario not in scenarios:
        typer.echo(f"unknown scenario {scenario!r}; choose from {sorted(scenarios)}", err=True)
        raise typer.Exit(2)
    settings = get_settings()
    try:
        effective_routing = (
            RoutingConfig.from_json(settings.llm_routing)
            if settings.llm_routing
            else RoutingConfig.defaults()
        )
        report = estimate_review_cost(
            scenarios[scenario], routing=CostRouting.from_runtime(effective_routing)
        )
    except (ValueError, LLMConfigError) as exc:
        typer.echo(f"FAILED: review cost routing is invalid: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(render_cost_report(report))
    if out is not None:
        write_cost_report(out, report)
        typer.echo(f"evidence: {out}")


@quality_app.command("calibrate-cost")
def quality_calibrate_cost(
    run: int = typer.Option(..., "--run", help="completed representative review run id"),
    estimate: Path = typer.Option(..., "--estimate", exists=True, dir_okay=False),
    out: Path = typer.Option(..., "--out", dir_okay=False),
    fail_on_gate: bool = typer.Option(True, "--fail-on-gate/--no-fail-on-gate"),
) -> None:
    """Compare observed review spend with the normalized base cost model."""

    from sixsentences_server.core.db import LLMCallRow
    from sixsentences_server.evals.economics import (
        ObservedReviewUsage,
        ObservedStageUsage,
        ReviewCostReport,
        calibrate_review_cost,
        render_cost_calibration,
        validate_observed_cost_routes,
        write_cost_calibration,
    )
    from sixsentences_server.evals.quality import sha256_json

    try:
        baseline = ReviewCostReport.model_validate_json(estimate.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(f"FAILED: base cost report is invalid: {estimate}", err=True)
        raise typer.Exit(1) from exc
    init_db()
    with db_session() as session:
        run_row = session.get(Run, run)
        if run_row is None:
            typer.echo(f"FAILED: run {run} not found", err=True)
            raise typer.Exit(1)
        usage = usage_for_run(session, run, org_id=run_row.org_id)
        cost_sources = session.execute(
            select(
                LLMCallRow.cost_source,
                LLMCallRow.task,
                LLMCallRow.provider,
                LLMCallRow.model,
            ).where(
                LLMCallRow.run_id == run,
                LLMCallRow.org_id == run_row.org_id,
            )
        ).all()
        config = dict(run_row.config or {})
        prisma = dict(run_row.prisma or {})
        revision = str(config.get("git_revision") or "")
        if not revision:
            typer.echo(
                "FAILED: run has no recorded Git revision; execute a new review with this release",
                err=True,
            )
            raise typer.Exit(1)
        try:
            validate_observed_cost_routes(
                baseline.routing,
                ((task, provider, model) for _, task, provider, model in cost_sources),
            )
            # Google returns native usage counters rather than an invoice
            # amount. Only validated usage-based prices qualify here; missing
            # usage estimates and uncertain egress retain their unpriced status.
            provider_priced_calls = sum(
                1
                for source, _, provider, _ in cost_sources
                if source == "provider" or (source == "gemini_usage" and provider == "gemini")
            )
            observed = ObservedReviewUsage(
                run_id=run_row.id,
                run_public_id=run_row.public_id,
                corpus_version=str(run_row.corpus_version or "unknown"),
                git_revision=revision,
                run_config_digest=sha256_json(config),
                candidates=int(prisma.get("records_screened") or 0),
                completed=run_row.status == RunStatus.COMPLETED.value,
                total_calls=usage.total_calls,
                total_input_tokens=usage.total_input_tokens,
                total_output_tokens=usage.total_output_tokens,
                total_cost_usd=usage.total_cost_usd,
                provider_priced_calls=provider_priced_calls,
                catalog_priced_calls=len(cost_sources) - provider_priced_calls,
                by_task={
                    task: ObservedStageUsage(**bucket.model_dump())
                    for task, bucket in usage.by_task.items()
                },
            )
            report = calibrate_review_cost(baseline, observed)
        except ValueError as exc:
            typer.echo(f"FAILED: {exc}", err=True)
            raise typer.Exit(1) from exc
    write_cost_calibration(out, report)
    typer.echo(render_cost_calibration(report))
    typer.echo(f"evidence: {out}")
    if fail_on_gate and not report.passed:
        raise typer.Exit(1)


@app.command("api")
def api_cmd(port: int = typer.Option(8000)) -> None:
    """Start the API server."""
    uvicorn.run(
        "sixsentences_server.api.app:app",
        port=port,
        ws="websockets-sansio",
        ws_max_size=16_384,
        ws_max_queue=8,
        ws_per_message_deflate=False,
    )


if __name__ == "__main__":
    app()
