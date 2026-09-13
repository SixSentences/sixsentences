"""Community resource profile and local safety limits.

The open-source service has no paid tiers or purchase flow. All product
capabilities are available to every workspace. Structural limits and provider
spend ceilings remain because they protect a self-hosted deployment from
accidental resource exhaustion; operators may change them in their fork.
"""

from enum import StrEnum

from pydantic import BaseModel, Field, computed_field

CAPACITY_SCALE = 10_000
SCREENING_BATCH_SIZE = 20


class PlanTier(StrEnum):
    """Stable identifier for the sole non-commercial resource profile."""

    COMMUNITY = "community"


class Capability(StrEnum):
    """Stable product bundles shared by API enforcement and clients."""

    CORE_WORKSPACE = "core_workspace"
    BASIC_REVIEW = "basic_review"
    DISCOVERY_PACK = "discovery_pack"
    EXPERT_SEARCH = "expert_search"
    STUDIO_BASIC = "studio_basic"
    DEEP_REVIEW = "deep_review"
    RESEARCH_STUDIO = "research_studio"
    AUTOMATION = "automation"
    TEAM_REVIEW = "team_review"
    INSTITUTION_CONTROLS = "institution_controls"


ALL_CAPABILITIES = tuple(Capability)


class Plan(BaseModel):
    """Local capability and resource-safety profile."""

    tier: PlanTier = PlanTier.COMMUNITY
    name: str = "Community"
    tagline: str = "The complete self-hosted research workspace."
    capacity_label: str = "Operator-managed capacity"
    monthly_credits: int | None = Field(default=None, exclude=True, repr=False)
    max_works_per_run: int | None = 30_000
    max_storage_bytes: int | None = 25 * 1024 * 1024 * 1024
    max_library_documents: int | None = 5_000
    max_seats: int | None = 25
    max_concurrent_runs: int = 3
    max_concurrent_ai_actions: int = 18
    max_concurrent_figures: int = 6
    max_survey_questions: int = 200
    max_survey_responses_per_survey: int = 25_000
    max_dataset_rows: int = 1_000_000
    max_dataset_upload_bytes: int = 100 * 1024 * 1024
    max_interview_duration_ms: int = 180 * 60_000
    max_interview_upload_bytes: int = 100 * 1024 * 1024
    monthly_ai_cost_usd: float = Field(default=25.0, exclude=True, repr=False)
    max_ai_cost_per_action_usd: float = Field(default=25.0, exclude=True, repr=False)
    capabilities: tuple[Capability, ...] = ALL_CAPABILITIES

    def includes(self, capability: Capability | str) -> bool:
        """Return whether the community profile contains a capability."""
        try:
            resolved = Capability(capability)
        except ValueError:
            return False
        return resolved in self.capabilities

    @computed_field  # type: ignore[prop-decorator]
    @property
    def live_search(self) -> bool:
        return self.includes(Capability.DISCOVERY_PACK)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def full_text_acquisition(self) -> bool:
        return self.includes(Capability.DEEP_REVIEW)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def full_text_screening(self) -> bool:
        return self.includes(Capability.DEEP_REVIEW)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def chat(self) -> bool:
        return self.includes(Capability.CORE_WORKSPACE)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def api_access(self) -> bool:
        return self.includes(Capability.AUTOMATION)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def web_search(self) -> bool:
        return self.includes(Capability.DEEP_REVIEW)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def living_reviews(self) -> bool:
        return self.includes(Capability.AUTOMATION)


class CapacityProfile(BaseModel):
    """Internal usage weights retained for auditing and reservations."""

    question: int = 60
    search: int = 600
    screening_batch: int = 10
    figure: int = 350
    extraction_work: int = 12
    interview_minute: int = 5
    interview_live_minute: int = 250


COMMUNITY_PLAN = Plan()
PLANS: dict[PlanTier, Plan] = {PlanTier.COMMUNITY: COMMUNITY_PLAN}
COMMUNITY_CAPACITY_PROFILE = CapacityProfile()
DEFAULT_TIER = PlanTier.COMMUNITY


def get_plan(_tier: str | PlanTier = PlanTier.COMMUNITY) -> Plan:
    """Return the sole community profile, including for legacy stored values."""
    return COMMUNITY_PLAN


def all_plans() -> list[Plan]:
    """Return the non-commercial profile exposed by this deployment."""
    return [COMMUNITY_PLAN]


def capacity_profile(_plan: Plan | str = COMMUNITY_PLAN) -> CapacityProfile:
    """Return the community accounting weights."""
    return COMMUNITY_CAPACITY_PROFILE


def screening_credits(works_screened: int, plan: Plan | str = COMMUNITY_PLAN) -> int:
    """Return auditable usage units for the actual screening volume."""
    if works_screened <= 0:
        return 0
    batches = -(-works_screened // SCREENING_BATCH_SIZE)
    return batches * capacity_profile(plan).screening_batch
