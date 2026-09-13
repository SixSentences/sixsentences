"""LLM usage / cost accounting for a run.

Aggregates the per-call `llm_calls` rows into the shape a provider-usage system and a
UI cost panel both need: totals plus breakdowns by task and by provider. This
is the persisted, queryable counterpart to the in-run BudgetGovernor.
"""

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from sixsentences_server.core.db import LLMCallRow


class UsageBucket(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, row: LLMCallRow) -> None:
        self.calls += 1
        self.input_tokens += row.input_tokens
        self.output_tokens += row.output_tokens
        self.cost_usd = round(self.cost_usd + row.cost_usd, 6)


class UsageSummary(BaseModel):
    run_id: int
    total_cost_usd: float = 0.0
    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    by_task: dict[str, UsageBucket] = Field(default_factory=dict)
    by_provider: dict[str, UsageBucket] = Field(default_factory=dict)


def usage_for_run(session: Session, run_id: int, *, org_id: int | None = None) -> UsageSummary:
    stmt = select(LLMCallRow).where(LLMCallRow.run_id == run_id)
    if org_id is not None:  # defence in depth: also scope by tenant
        stmt = stmt.where(LLMCallRow.org_id == org_id)
    rows = session.scalars(stmt.order_by(LLMCallRow.id)).all()
    summary = UsageSummary(run_id=run_id)
    for row in rows:
        summary.total_calls += 1
        summary.total_input_tokens += row.input_tokens
        summary.total_output_tokens += row.output_tokens
        summary.total_cost_usd = round(summary.total_cost_usd + row.cost_usd, 6)
        summary.by_task.setdefault(row.task, UsageBucket()).add(row)
        summary.by_provider.setdefault(row.provider, UsageBucket()).add(row)
    return summary
