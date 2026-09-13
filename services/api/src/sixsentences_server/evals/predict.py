"""Reproducible prediction runner for the release-quality golden suite.

The quality scorer is intentionally independent from this module. This runner
executes the real review pipeline without exposing any gold labels to it, then
exports the stage outputs the scorer expects. Release evidence therefore
measures the shipped behavior instead of a hand-authored prediction fixture.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.acquisition.service import Acquirer
from sixsentences_server.acquisition.store import DocumentStore, LocalDocumentStore
from sixsentences_server.config import get_settings
from sixsentences_server.core.db import (
    DocumentRow,
    Org,
    Project,
    Run,
    ScreeningDecisionRow,
    SourceRecordRow,
    WorkRow,
)
from sixsentences_server.core.models import RunStatus, WorkRecord
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.evals.quality import (
    DEFAULT_EVAL_SEED,
    CasePrediction,
    ClaimPrediction,
    GoldenCase,
    GoldenSuite,
    PredictedVerdict,
    PredictionRun,
    current_git_revision,
    sha256_json,
)
from sixsentences_server.llm.base import TaskType
from sixsentences_server.llm.pool import LLMPool
from sixsentences_server.pipeline.run import RunResult, execute_run
from sixsentences_server.ranking.scorer import rank_works
from sixsentences_server.screening.evidence import decision_stage

PREDICTION_RUNNER: Final = "sixsentences_server.release-eval"
PREDICTION_SHARD_RUNNER: Final = "sixsentences_server.release-eval.shard"
MAX_CITATION_CANDIDATES: Final = 50
MAX_EVIDENCE_CHARACTERS_PER_SOURCE: Final = 1_600
MAX_CLAIM_EVIDENCE_CHARACTERS: Final = 60_000
MIN_CLAIM_TOKEN_LENGTH: Final = 4

CLAIM_VERIFICATION_SYSTEM: Final = (
    "You verify one pre-registered claim against a closed set of candidate research records. "
    "Use only the supplied candidate IDs and text. A source supports the claim only when its "
    "text directly entails the material assertion, not merely the topic. Return strict JSON "
    'only: {"supported_by":["WORK_ID"],"reason":"short audit note"}. If no candidate '
    'directly supports the claim, return {"supported_by":[],"reason":"..."}. Never invent '
    "an ID and never use outside knowledge."
)


class QualityPredictionConfig(BaseModel):
    """Pinned run controls recorded in the prediction artifact digest."""

    model_config = ConfigDict(extra="forbid")

    live: bool = False
    exhaustive: bool = True
    retrieval_limit: int = Field(default=100_000, ge=1)
    screen_limit: int = Field(default=1_000, ge=1)
    live_limit: int = Field(default=5_000, ge=1)
    acquire: bool = True
    full_text_screen: bool = True
    peer_reviewed_only: bool = False
    seed: int = DEFAULT_EVAL_SEED

    def model_post_init(self, __context: Any) -> None:
        if self.full_text_screen and not self.acquire:
            raise ValueError("full-text screening requires acquisition")


class PredictionRunError(RuntimeError):
    """A release prediction could not be completed honestly."""


def _routing_evidence(pool: LLMPool) -> dict[str, Any]:
    return {
        "synthesis": str(pool.routing.synthesis) if pool.routing.synthesis else None,
        "adjudication": str(pool.routing.adjudication) if pool.routing.adjudication else None,
        "screening": [str(ref) for ref in pool.routing.screening],
    }


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(raw[start : end + 1])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _work_text(
    session: Session,
    run: Run,
    work: WorkRecord,
    store: DocumentStore,
) -> str:
    row = session.scalar(
        select(DocumentRow)
        .where(
            DocumentRow.run_id == run.id,
            DocumentRow.org_id == run.org_id,
            DocumentRow.work_id == work.id,
            DocumentRow.status == "retrieved",
        )
        .order_by(DocumentRow.id.desc())
    )
    full_text = store.get_text(row.checksum) if row is not None and row.checksum else None
    return full_text or work.abstract or ""


def _claim_excerpt(text: str, claim: str, *, limit: int) -> str:
    """Select claim-relevant passages without sending an entire document."""

    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    claim_terms = {
        token
        for token in re.findall(r"[\w-]+", claim.casefold())
        if len(token) >= MIN_CLAIM_TOKEN_LENGTH
    }
    passages = [
        " ".join(part.split()) for part in re.split(r"(?<=[.!?])\s+|\n{2,}", text) if part.strip()
    ]
    scored = sorted(
        enumerate(passages),
        key=lambda item: (
            -len(claim_terms.intersection(re.findall(r"[\w-]+", item[1].casefold()))),
            item[0],
        ),
    )
    selected: list[tuple[int, str]] = []
    used = 0
    for index, passage in scored:
        separator = 1 if selected else 0
        remaining = limit - used - separator
        if remaining <= 0:
            break
        selected.append((index, passage[:remaining]))
        used += min(len(passage), remaining) + separator
    return " ".join(passage for _, passage in sorted(selected))


def _claim_evidence(
    session: Session,
    run: Run,
    claim_text: str,
    candidates: list[WorkRecord],
    store: DocumentStore,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    used = 0
    for work in candidates:
        excerpt = _claim_excerpt(
            _work_text(session, run, work, store),
            claim_text,
            limit=MAX_EVIDENCE_CHARACTERS_PER_SOURCE,
        )
        item = {
            "work_id": work.id,
            "title": work.title,
            "year": work.year,
            "text": excerpt,
        }
        item_size = len(json.dumps(item, ensure_ascii=False))
        if evidence and used + item_size > MAX_CLAIM_EVIDENCE_CHARACTERS:
            break
        if item_size > MAX_CLAIM_EVIDENCE_CHARACTERS:
            item["text"] = excerpt[: max(0, MAX_CLAIM_EVIDENCE_CHARACTERS // 2)]
            item_size = len(json.dumps(item, ensure_ascii=False))
        evidence.append(item)
        used += item_size
    return evidence


def _predict_claims(
    session: Session,
    run: Run,
    case: GoldenCase,
    ranked_works: list[WorkRecord],
    pool: LLMPool,
    store: DocumentStore,
) -> list[ClaimPrediction]:
    candidates = ranked_works[:MAX_CITATION_CANDIDATES]
    allowed = {work.id for work in candidates}
    predictions: list[ClaimPrediction] = []
    for claim in case.claims:
        evidence = _claim_evidence(session, run, claim.text, candidates, store)
        response = pool.complete(
            TaskType.CLAIM_VERIFICATION,
            system=CLAIM_VERIFICATION_SYSTEM,
            prompt=(
                f"Claim ID: {claim.claim_id}\n"
                f"Claim: {claim.text}\n\n"
                f"Candidates: {json.dumps(evidence, ensure_ascii=False)}"
            ),
            max_tokens=600,
        )
        payload = _extract_json_object(response.text) or {}
        raw_ids = payload.get("supported_by")
        cited = (
            [str(work_id) for work_id in raw_ids if str(work_id) in allowed]
            if isinstance(raw_ids, list)
            else []
        )
        predictions.append(
            ClaimPrediction(claim_id=claim.claim_id, cited_work_ids=list(dict.fromkeys(cited)))
        )
    return predictions


def _stage_predictions(
    session: Session,
    run: Run,
) -> tuple[dict[str, PredictedVerdict], dict[str, PredictedVerdict]]:
    rows = session.scalars(
        select(ScreeningDecisionRow)
        .where(
            ScreeningDecisionRow.run_id == run.id,
            ScreeningDecisionRow.org_id == run.org_id,
        )
        .order_by(ScreeningDecisionRow.id)
    ).all()
    screening: dict[str, PredictedVerdict] = {}
    full_text: dict[str, PredictedVerdict] = {}
    for row in rows:
        try:
            verdict = PredictedVerdict(row.verdict)
        except ValueError:
            verdict = PredictedVerdict.UNSURE
        stage = decision_stage(row.reviewer)
        if stage == "title_abstract":
            screening[row.work_id] = verdict
        elif stage in {"full_text", "human"}:
            full_text[row.work_id] = verdict
    return screening, full_text


def _retrieved_works(session: Session, run: Run) -> list[WorkRecord]:
    rows = (
        session.scalars(
            select(WorkRow)
            .join(SourceRecordRow, SourceRecordRow.work_id == WorkRow.id)
            .where(
                SourceRecordRow.run_id == run.id,
                SourceRecordRow.org_id == run.org_id,
            )
        )
        .unique()
        .all()
    )
    return [WorkRecord.model_validate(row.payload) for row in rows]


def _case_prediction(
    session: Session,
    run: Run,
    case: GoldenCase,
    result: RunResult,
    *,
    corpus: DuckDBCorpus,
    pool: LLMPool,
    store: DocumentStore,
) -> CasePrediction:
    works = _retrieved_works(session, run)
    ranked = [item.work for item in rank_works(works, result.protocol)]
    screening, full_text = _stage_predictions(session, run)
    return CasePrediction(
        case_id=case.case_id,
        corpus_work_ids=corpus.present_work_ids([work.work_id for work in case.works]),
        retrieved_ids=[work.id for work in ranked],
        screening=screening,
        full_text=full_text,
        final_ranked_ids=[work.id for work in ranked],
        claims=_predict_claims(session, run, case, ranked, pool, store),
    )


def generate_predictions(
    session: Session,
    suite: GoldenSuite,
    *,
    corpus: DuckDBCorpus,
    pool: LLMPool,
    config: QualityPredictionConfig | None = None,
    acquirer: Acquirer | None = None,
    document_store: DocumentStore | None = None,
    on_progress: Callable[[int, int, str], None] | None = None,
    case_ids: set[str] | None = None,
    generated_by: str = PREDICTION_RUNNER,
) -> PredictionRun:
    """Run the complete shipped pipeline for every golden case.

    Gold work labels and expected citation links are never passed to
    ``execute_run`` or to the claim verifier. A paused, cancelled, or failed
    case aborts the export, so partial evidence cannot be mistaken for a
    release-quality prediction run.
    """

    controls = config or QualityPredictionConfig()
    random.seed(controls.seed)
    settings = get_settings()
    store = document_store or LocalDocumentStore(settings.documents_dir)
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    org = Org(name=f"release-eval-{suite.suite_id[:80]}-{stamp}", plan="community")
    session.add(org)
    session.flush()
    project = Project(
        org_id=org.id,
        name=f"Release evaluation {suite.suite_id}@{suite.version}",
        kind="evaluation",
        status="active",
    )
    session.add(project)
    session.flush()

    cases = [case for case in suite.cases if case_ids is None or case.case_id in case_ids]
    if case_ids is not None:
        unknown = sorted(case_ids - {case.case_id for case in suite.cases})
        if unknown:
            raise PredictionRunError(f"unknown release-evaluation cases: {', '.join(unknown)}")
        if not cases:
            raise PredictionRunError("release-evaluation shard contains no cases")

    predictions: list[CasePrediction] = []
    total = len(cases)
    for index, case in enumerate(cases, start=1):
        if on_progress is not None:
            on_progress(index, total, case.case_id)
        run = Run(
            org_id=org.id,
            project_id=project.id,
            question=case.question,
            status=RunStatus.PENDING.value,
            config={
                "release_evaluation": True,
                "suite_id": suite.suite_id,
                "suite_version": suite.version,
                "case_id": case.case_id,
                "seed": controls.seed,
                "evaluation_controls": controls.model_dump(mode="json"),
            },
        )
        session.add(run)
        session.flush()
        result = execute_run(
            session,
            run,
            corpus=corpus,
            pool=pool,
            query_override=case.query,
            live=controls.live,
            screen=True,
            paper_limit=0,
            screen_limit=controls.screen_limit,
            exhaustive=controls.exhaustive,
            retrieval_limit=controls.retrieval_limit,
            live_limit=controls.live_limit,
            acquire=controls.acquire,
            acquirer=acquirer,
            full_text_screen=controls.full_text_screen,
            document_store=store,
            peer_reviewed_only=controls.peer_reviewed_only,
        )
        session.refresh(run)
        if run.status != RunStatus.COMPLETED.value:
            raise PredictionRunError(
                f"case {case.case_id!r} ended as {run.status}; retained audit run {run.public_id}"
            )
        predictions.append(
            _case_prediction(
                session,
                run,
                case,
                result,
                corpus=corpus,
                pool=pool,
                store=store,
            )
        )

    info = corpus.info()
    build_digest = str(info.get("build_digest") or "")
    corpus_sha256 = str(info.get("sha256") or "")
    if len(build_digest) != 64 or len(corpus_sha256) != 64:
        raise PredictionRunError("candidate corpus lacks immutable build/checksum evidence")
    config_evidence = {
        "controls": controls.model_dump(mode="json"),
        "routing": _routing_evidence(pool),
        "claim_candidate_limit": MAX_CITATION_CANDIDATES,
        "claim_evidence_character_limit_per_source": MAX_EVIDENCE_CHARACTERS_PER_SOURCE,
        "claim_evidence_character_limit_total": MAX_CLAIM_EVIDENCE_CHARACTERS,
    }
    return PredictionRun(
        suite_id=suite.suite_id,
        suite_version=suite.version,
        suite_digest=suite.digest,
        created_at=datetime.now(UTC),
        generated_by=generated_by,
        git_revision=current_git_revision(),
        corpus_version=str(info["version"]),
        corpus_sha256=corpus_sha256,
        corpus_build_digest=build_digest,
        config_digest=sha256_json(config_evidence),
        seed=controls.seed,
        predictions=predictions,
    )


def partition_suite_cases(suite: GoldenSuite, shard_count: int) -> list[list[str]]:
    """Balance immutable suite cases across deterministic evaluation shards.

    The gold labels are used only to estimate case size for scheduling. They
    never enter the review pipeline or a model prompt. Cases remain ordered as
    they appear in the suite inside each shard, making reruns reproducible.
    """

    if shard_count < 1 or shard_count > len(suite.cases):
        raise ValueError("shard count must be between 1 and the number of suite cases")
    suite_order = {case.case_id: index for index, case in enumerate(suite.cases)}
    buckets: list[list[str]] = [[] for _ in range(shard_count)]
    weights = [0 for _ in range(shard_count)]
    ranked = sorted(
        suite.cases,
        key=lambda case: (-(len(case.works) * 10 + len(case.claims)), case.case_id),
    )
    for case in ranked:
        target = min(range(shard_count), key=lambda index: (weights[index], index))
        buckets[target].append(case.case_id)
        weights[target] += len(case.works) * 10 + len(case.claims)
    for bucket in buckets:
        bucket.sort(key=suite_order.__getitem__)
    return buckets


def merge_prediction_shards(
    suite: GoldenSuite,
    shards: list[PredictionRun],
) -> PredictionRun:
    """Attest a complete canonical run from independently generated shards.

    A merge fails closed on any metadata mismatch, duplicate, missing or extra
    case. Only this complete artifact uses the approved canonical runner name;
    individual shards cannot pass the release gate.
    """

    if not shards:
        raise PredictionRunError("at least one prediction shard is required")
    first = shards[0]
    expected_metadata = (
        first.suite_id,
        first.suite_version,
        first.suite_digest,
        first.git_revision,
        first.corpus_version,
        first.corpus_sha256,
        first.corpus_build_digest,
        first.config_digest,
        first.seed,
    )
    predictions: list[CasePrediction] = []
    seen: set[str] = set()
    for shard in shards:
        if shard.generated_by != PREDICTION_SHARD_RUNNER:
            raise PredictionRunError(
                f"prediction input was not produced by {PREDICTION_SHARD_RUNNER}"
            )
        metadata = (
            shard.suite_id,
            shard.suite_version,
            shard.suite_digest,
            shard.git_revision,
            shard.corpus_version,
            shard.corpus_sha256,
            shard.corpus_build_digest,
            shard.config_digest,
            shard.seed,
        )
        if metadata != expected_metadata:
            raise PredictionRunError("prediction shard metadata does not match")
        for prediction in shard.predictions:
            if prediction.case_id in seen:
                raise PredictionRunError(
                    f"duplicate case across prediction shards: {prediction.case_id}"
                )
            seen.add(prediction.case_id)
            predictions.append(prediction)

    expected = {case.case_id for case in suite.cases}
    missing = sorted(expected - seen)
    extra = sorted(seen - expected)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing: {', '.join(missing)}")
        if extra:
            details.append(f"extra: {', '.join(extra)}")
        raise PredictionRunError("incomplete prediction shards; " + "; ".join(details))
    suite_order = {case.case_id: index for index, case in enumerate(suite.cases)}
    predictions.sort(key=lambda prediction: suite_order[prediction.case_id])
    return PredictionRun(
        suite_id=first.suite_id,
        suite_version=first.suite_version,
        suite_digest=first.suite_digest,
        created_at=datetime.now(UTC),
        generated_by=PREDICTION_RUNNER,
        git_revision=first.git_revision,
        corpus_version=first.corpus_version,
        corpus_sha256=first.corpus_sha256,
        corpus_build_digest=first.corpus_build_digest,
        config_digest=first.config_digest,
        seed=first.seed,
        predictions=predictions,
    )


def write_predictions(path: Path, run: PredictionRun) -> None:
    """Create one immutable prediction artifact after all cases completed."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(run.model_dump_json(indent=2))
