"""Keyless production-routing coverage through the complete screening pipeline."""

import json
from collections import Counter

import httpx
import pytest
from sqlalchemy import select

from sixsentences_server.config import get_settings
from sixsentences_server.core.db import (
    LLMCallRow,
    Project,
    Run,
    RunEvent,
    db_session,
    get_default_org,
    init_db,
)
from sixsentences_server.core.models import ReviewProtocol, Verdict, WorkRecord
from sixsentences_server.corpus.duckdb_store import DuckDBCorpus
from sixsentences_server.llm.base import BudgetGovernor, ModelRef, TaskType
from sixsentences_server.llm.gemini_client import GeminiClient
from sixsentences_server.llm.pool import LLMPool
from sixsentences_server.llm.privacy import (
    PRIVATE_DEFAULT_MODEL,
    PRIVATE_PREMIUM_MODEL,
    PRIVATE_SCREENING_MODELS,
)
from sixsentences_server.pipeline.run import execute_run


@pytest.mark.parametrize("chat_model", [PRIVATE_DEFAULT_MODEL, PRIVATE_PREMIUM_MODEL])
@pytest.mark.parametrize("first_reviewer_unavailable", [False, True])
def test_default_native_pair_reaches_pipeline_and_records_actual_votes(
    corpus: DuckDBCorpus,
    monkeypatch: pytest.MonkeyPatch,
    chat_model: str,
    first_reviewer_unavailable: bool,
) -> None:
    """A pre-generation failure has no usage row, but cannot count as a vote."""
    monkeypatch.setenv("SIX_GEMINI_API_KEY", "inert-test-key")
    monkeypatch.setenv("SIX_GEMINI_DATA_PROCESSING_CONFIRMED", "1")
    get_settings.cache_clear()
    requests: list[tuple[str, str]] = []
    works = [
        WorkRecord(
            id=f"synthetic-native-reviewer-{index}",
            title=f"Synthetic native reviewer record {index}",
            abstract=(
                "This synthetic study evaluates LLM-generated Terraform."
                if index < 5
                else "This synthetic study concerns marine ecology."
            ),
        )
        for index in range(12)
    ]
    protocol = ReviewProtocol(
        question="Synthetic screening of LLM-generated Terraform evaluations",
        query_string="sixqanativereviewernocorpusmatch",
        inclusion_criteria=["Evaluates LLM-generated Terraform"],
        exclusion_criteria=["Unrelated research without infrastructure generation"],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "generativelanguage.googleapis.com"
        model = request.url.path.rsplit("/", 1)[-1].removesuffix(":generateContent")
        assert model in PRIVATE_SCREENING_MODELS
        body = json.loads(request.content)
        assert body["generationConfig"]["responseMimeType"] == "application/json"
        assert body["generationConfig"]["maxOutputTokens"] == 300
        prompt = body["contents"][0]["parts"][0]["text"]
        title = prompt.split("\nTitle: ", 1)[1].split("\n", 1)[0]
        requests.append((model, title))
        if first_reviewer_unavailable and model == PRIVATE_SCREENING_MODELS[0]:
            return httpx.Response(400, json={"error": {"message": "Synthetic rejection"}})
        off_topic = "Abstract: This synthetic study concerns marine ecology." in prompt
        quote = "concerns marine ecology" if off_topic else "evaluates LLM-generated Terraform"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "verdict": "exclude" if off_topic else "include",
                                            "reason": "Synthetic eligibility assessment",
                                            "quote": quote,
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 100,
                    "candidatesTokenCount": 30,
                    "totalTokenCount": 130,
                },
            },
        )

    with httpx.Client(
        base_url="https://generativelanguage.googleapis.com/v1beta/",
        transport=httpx.MockTransport(handler),
    ) as http:
        client = GeminiClient(api_key="inert-test-key", http=http, max_retries=0)
        monkeypatch.setattr("sixsentences_server.llm.pool.build_client", lambda spec: client)
        # Use the actual default environment construction and the same pinning
        # boundary as hosted runs, not a hand-written two-reviewer test config.
        pool = LLMPool.from_environment(BudgetGovernor(limit_usd=1.0)).pinned(
            ModelRef("gemini", chat_model)
        )
        assert pool.screening_refs() == [
            ModelRef("gemini", model) for model in PRIVATE_SCREENING_MODELS
        ]
        init_db()
        with db_session() as session:
            org = get_default_org(session)
            project = Project(org_id=org.id, name="Synthetic native screening")
            session.add(project)
            session.flush()
            run = Run(
                org_id=org.id, project_id=project.id, question=protocol.question, status="pending"
            )
            session.add(run)
            session.flush()
            result = execute_run(
                session,
                run,
                corpus=corpus,
                pool=pool,
                approved_protocol=protocol,
                imported=works,
                screen=True,
                exhaustive=False,
            )
            events = session.scalars(select(RunEvent).where(RunEvent.run_id == run.id)).all()
            started = next(event for event in events if event.event == "screening_started")
            assert started.payload["ensemble_size"] == 2
            assert run.status == "completed"
            assert Counter(model for model, _ in requests) == {
                model: 12 for model in PRIVATE_SCREENING_MODELS
            }
            assert len(set(requests)) == 24
            expected = {Verdict.INCLUDE: 5}
            expected[Verdict.UNSURE if first_reviewer_unavailable else Verdict.EXCLUDE] = 7
            assert Counter(decision.verdict for decision in result.decisions) == expected
            if first_reviewer_unavailable:
                assert all(
                    "single-model exclude downgraded" in decision.reason
                    for decision in result.decisions
                    if decision.verdict is Verdict.UNSURE
                )
            ledger = session.scalars(
                select(LLMCallRow).where(
                    LLMCallRow.run_id == run.id,
                    LLMCallRow.task == TaskType.SCREENING.value,
                )
            ).all()
            recorded_models = (
                PRIVATE_SCREENING_MODELS[1:]
                if first_reviewer_unavailable
                else (PRIVATE_SCREENING_MODELS)
            )
            assert Counter(row.model for row in ledger) == {model: 12 for model in recorded_models}
            assert all(
                row.provider == "gemini" and row.cost_source == "gemini_usage" for row in ledger
            )
            assert sum(row.cost_usd for row in ledger) == pytest.approx(pool.budget.spent_usd)
