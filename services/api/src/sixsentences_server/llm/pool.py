"""LLMPool: routing + budget in one place.

The pool owns (a) the per-provider clients, (b) the routing config that maps
tasks to concrete models, and (c) the budget governor. Callers never touch a
provider directly; every call is budget-checked and task-attributed here.

Private tasks default to exact reviewed direct Gemini models. SIX_LLM_ROUTING
may customize their task assignment but cannot escape that provider boundary.
Explicitly public scoped callers retain a separate OpenRouter configuration;
private conversation, criteria or manuscript content never infer public scope.
"""

import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock

from sixsentences_server.llm.base import (
    BudgetGovernor,
    LLMCancelledError,
    LLMClient,
    LLMConfigError,
    LLMResponse,
    LLMUsage,
    ModelRef,
    TaskType,
    projected_cost_usd,
    settled_cost_source,
    settled_cost_usd,
)
from sixsentences_server.llm.privacy import (
    PRIVATE_DEFAULT_MODEL,
    PRIVATE_PREMIUM_MODEL,
    PRIVATE_SCREENING_MODELS,
    ContentScope,
    require_route_for_scope,
)
from sixsentences_server.llm.providers import (
    PROVIDERS,
    ProviderError,
    available_specs,
    build_client,
    default_screening_refs,
    default_strong_ref,
    provider_failure_diagnostics,
)

_LOG = logging.getLogger(__name__)

_GLOBAL_USER_PROSE_STYLE = (
    "\n\nGLOBAL USER-FACING PROSE STYLE\n"
    "Do not use em dashes or en dashes as punctuation in text shown to the user. "
    "Use commas, colons, parentheses or full stops instead. Preserve hyphens that "
    "are required by source titles, established compound terms, URLs, code, "
    "identifiers, quotations, structured data and LaTeX syntax."
)


@dataclass
class RoutingConfig:
    synthesis: ModelRef | None = None
    adjudication: ModelRef | None = None
    screening: list[ModelRef] = field(default_factory=list)

    @classmethod
    def from_json(cls, raw: str) -> "RoutingConfig":
        data = json.loads(raw)
        return cls(
            synthesis=ModelRef.parse(data["synthesis"]) if data.get("synthesis") else None,
            adjudication=(
                ModelRef.parse(data["adjudication"]) if data.get("adjudication") else None
            ),
            screening=[ModelRef.parse(s) for s in data.get("screening", [])],
        )

    @classmethod
    def defaults(cls, *, content_scope: ContentScope = "private") -> "RoutingConfig":
        specs = available_specs(content_scope=content_scope)
        strong = default_strong_ref(specs)
        screening = default_screening_refs(specs)
        return cls(
            synthesis=strong,
            # Adjudication and full-text eligibility can fan out across
            # hundreds of records. Keep those decisions on the economical
            # primary screening route; the secondary reviewer remains
            # available as a fallback through ``precision_refs``.
            adjudication=(
                ModelRef("gemini", PRIVATE_SCREENING_MODELS[1])
                if content_scope == "private" and screening and screening[0].provider == "gemini"
                else screening[0]
                if screening
                else strong
            ),
            screening=screening,
        )


class LLMPool:
    def __init__(
        self,
        clients: dict[str, LLMClient],
        routing: RoutingConfig,
        budget: BudgetGovernor,
        *,
        content_scope: ContentScope = "private",
    ) -> None:
        self.clients = clients
        self.routing = routing
        self.budget = budget
        self.content_scope = content_scope
        # every completed call is captured for cost accounting; on_usage lets a
        # caller (the pipeline) persist each call live for the UI usage panel.
        self.usage: list[LLMUsage] = []
        self.on_usage: Callable[[LLMUsage], None] | None = None
        # Parallel reviewers may share a non-thread-safe persistence callback.
        # Serialize only settlement/recording, never the provider request.
        self._usage_lock = RLock()
        # Action wrappers can install one cooperative cancellation signal on
        # the pool. Nested verifier/tool calls then inherit it automatically,
        # while an individual call may still provide an explicit override.
        self.cancel_check: Callable[[], bool] | None = None
        self._unavailable_refs: set[ModelRef] = set()

    # -- construction -------------------------------------------------------

    @classmethod
    def from_environment(
        cls,
        budget: BudgetGovernor,
        routing_json: str = "",
        *,
        content_scope: ContentScope = "private",
    ) -> "LLMPool":
        routing = (
            RoutingConfig.from_json(routing_json)
            if routing_json
            else RoutingConfig.defaults(content_scope=content_scope)
        )
        for ref in [routing.synthesis, routing.adjudication, *routing.screening]:
            if ref is not None:
                require_route_for_scope(ref, content_scope)
        needed = {ref.provider for ref in routing.screening}
        for ref in (routing.synthesis, routing.adjudication):
            if ref is not None:
                needed.add(ref.provider)
        clients: dict[str, LLMClient] = {}
        for name in sorted(needed):
            spec = PROVIDERS.get(name)
            if spec is None:
                raise LLMConfigError(f"unknown provider in routing: {name}")
            if not spec.available():
                requirement = (
                    f"{spec.key_env} and SIX_GEMINI_DATA_PROCESSING_CONFIRMED=1 are required"
                    if name == "gemini"
                    else f"{spec.key_env} is not set"
                )
                raise LLMConfigError(f"provider {name!r} is routed but {requirement}")
            clients[name] = build_client(spec)
        return cls(clients=clients, routing=routing, budget=budget, content_scope=content_scope)

    def pinned(self, ref: ModelRef) -> "LLMPool":
        """A view with bounded synthesis work pinned to the user's model.

        High-volume screening keeps the economical reviewer route, while
        adjudication and full-text eligibility keep the fixed economical route.
        A premium user choice must never fan out once per candidate record.
        Usage accounting stays shared.
        """
        require_route_for_scope(ref, self.content_scope)
        clients = dict(self.clients)
        if ref.provider not in clients:
            spec = PROVIDERS.get(ref.provider)
            if spec is None or not spec.available():
                raise LLMConfigError(f"provider {ref.provider!r} is not configured")
            clients[ref.provider] = build_client(spec)
        clone = LLMPool(
            clients=clients,
            routing=RoutingConfig(
                synthesis=ref,
                adjudication=self.routing.adjudication,
                screening=self.routing.screening,
            ),
            budget=self.budget,
            content_scope=self.content_scope,
        )
        clone.usage = self.usage  # one shared ledger per request
        clone.on_usage = self.on_usage
        clone._usage_lock = self._usage_lock
        clone.cancel_check = self.cancel_check
        clone._unavailable_refs = self._unavailable_refs
        return clone

    # -- capability checks ---------------------------------------------------

    def has_strong(self) -> bool:
        return self.routing.synthesis is not None or self.routing.adjudication is not None

    def screening_refs(self) -> list[ModelRef]:
        return self.routing.screening

    def synthesis_refs(self) -> list[ModelRef]:
        """Available protocol/query routes, strongest first with cheap fallbacks."""
        candidates = [
            self.routing.synthesis,
            self.routing.adjudication,
            *self.routing.screening,
        ]
        if self._uses_internal_gemini_screening():
            # The cheaper screening rollout must not downgrade chat, protocol,
            # Writer or their fallback route to an internal Lite reviewer.
            candidates = [self.routing.synthesis, *self._workspace_gemini_refs()]
        refs: list[ModelRef] = []
        for ref in candidates:
            if (
                ref is not None
                and ref.provider in self.clients
                and ref not in self._unavailable_refs
                and ref not in refs
            ):
                refs.append(ref)
        return refs

    def _uses_internal_gemini_screening(self) -> bool:
        return self.content_scope == "private" and any(
            ref.provider == "gemini" and ref.model in PRIVATE_SCREENING_MODELS
            for ref in self.routing.screening
        )

    def _workspace_gemini_refs(self) -> list[ModelRef]:
        """Keep interactive generation and claim checks on the reviewed chat models."""
        return [
            ref
            for model in (PRIVATE_DEFAULT_MODEL, PRIVATE_PREMIUM_MODEL)
            if (ref := ModelRef("gemini", model)) not in self._unavailable_refs
            and ref.provider in self.clients
        ]

    def precision_refs(self) -> list[ModelRef]:
        """Available precision routes in preference order.

        Adjudication and full-text screening prefer the fixed economical route,
        but a provider-level policy or availability failure must not terminate
        an otherwise healthy review. Economical screening routes are safe
        fallbacks because they are already pinned independently of the user's
        synthesis-model choice.
        """
        candidates = [self.routing.adjudication, *self.routing.screening]
        refs: list[ModelRef] = []
        for ref in candidates:
            if (
                ref is not None
                and ref.provider in self.clients
                and ref not in self._unavailable_refs
                and ref not in refs
            ):
                refs.append(ref)
        return refs

    def mark_unavailable(self, ref: ModelRef) -> None:
        """Stop retrying a precision route that exhausted provider recovery."""
        self._unavailable_refs.add(ref)

    def _routing_diagnostic_counts(
        self, task: TaskType, explicit: ModelRef | None
    ) -> tuple[int, int, int]:
        """Count configured routes, eligible task candidates and disabled routes.

        These counters describe the existing routing state without changing it
        or exposing prompts, provider responses, credentials or budget contents.
        """
        configured = {
            ref
            for ref in (
                self.routing.synthesis,
                self.routing.adjudication,
                *self.routing.screening,
                explicit,
            )
            if ref is not None
        }
        return (
            len(configured),
            len(self._completion_candidates(task, explicit)),
            len(configured & self._unavailable_refs),
        )

    # -- calls ---------------------------------------------------------------

    def complete(
        self,
        task: TaskType,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 2048,
        ref: ModelRef | None = None,
        on_delta: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
        on_stream_reset: Callable[[], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        json_response: bool = False,
    ) -> LLMResponse:
        if cancel_check is None:
            cancel_check = self.cancel_check
        if cancel_check is not None and cancel_check():
            raise LLMCancelledError("LLM request cancelled")
        system = system + _GLOBAL_USER_PROSE_STYLE
        candidates = self._completion_candidates(task, ref)
        if not candidates:
            _LOG.warning(
                "llm routing unavailable reason=no_candidate_routes task=%s "
                "error_class=LLMConfigError cause_class=none "
                "configured_routes=%s candidates=%s unavailable_routes=%s",
                task.value,
                *self._routing_diagnostic_counts(task, ref),
            )
            raise LLMConfigError(f"no model routed for task {task.value}")
        last_error: ProviderError | None = None
        for target in candidates[:2]:
            # Recheck even manually constructed or mutated pools. A stale
            # persisted model selection must never reroute private context.
            require_route_for_scope(target, self.content_scope)
            if cancel_check is not None and cancel_check():
                raise LLMCancelledError("LLM request cancelled")
            client = self.clients.get(target.provider)
            if client is None:
                continue
            projected_input_tokens = (
                len(system.encode("utf-8")) + len(prompt.encode("utf-8")) + 256
                if target.provider == "gemini"
                else max(1, (len(system) + len(prompt) + 3) // 4)
            )
            projected_cost = projected_cost_usd(
                target.model,
                projected_input_tokens,
                max_tokens,
                provider=target.provider,
            )
            budget = self.budget
            with budget.reserve(projected_cost) as reservation:
                start = time.perf_counter()
                try:
                    if on_delta is None and on_reasoning is None:
                        if cancel_check is None:
                            response = client.complete(
                                target.model,
                                system=system,
                                prompt=prompt,
                                max_tokens=max_tokens,
                                json_response=json_response,
                            )
                        else:
                            response = client.complete(
                                target.model,
                                system=system,
                                prompt=prompt,
                                max_tokens=max_tokens,
                                json_response=json_response,
                                cancel_check=cancel_check,
                            )
                    else:
                        if cancel_check is None:
                            response = client.complete_stream(
                                target.model,
                                system=system,
                                prompt=prompt,
                                max_tokens=max_tokens,
                                on_delta=on_delta or (lambda _delta: None),
                                on_reasoning=on_reasoning,
                            )
                        else:
                            response = client.complete_stream(
                                target.model,
                                system=system,
                                prompt=prompt,
                                max_tokens=max_tokens,
                                on_delta=on_delta or (lambda _delta: None),
                                on_reasoning=on_reasoning,
                                cancel_check=cancel_check,
                            )
                except (ProviderError, LLMCancelledError) as exc:
                    incurred = getattr(exc, "incurred_response", None)
                    if isinstance(incurred, LLMResponse):
                        self._record_response(task, incurred, start, budget, reservation)
                    if isinstance(exc, LLMCancelledError):
                        raise
                    if (
                        task
                        in (
                            TaskType.SCREENING,
                            TaskType.ADJUDICATION,
                            TaskType.FULL_TEXT_SCREENING,
                        )
                        and exc.diagnostic_code == "invalid_structured_response"
                    ):
                        # Invalid JSON is a paid, unusable vote, not an outage.
                        # Domain guards retain it as UNSURE; other records may
                        # still use this model without an unrequested retry.
                        raise
                    last_error = exc
                    diagnostic_code, http_status = provider_failure_diagnostics(exc)
                    cause_code, _cause_http_status = provider_failure_diagnostics(exc.__cause__)
                    _LOG.warning(
                        "llm route failure reason=provider_error task=%s provider=%s model=%s "
                        "error_class=%s cause_class=%s "
                        "diagnostic_code=%s http_status=%s cause_code=%s "
                        "configured_routes=%s candidates=%s unavailable_routes=%s",
                        task.value,
                        target.provider,
                        target.model,
                        type(exc).__name__,
                        type(exc.__cause__).__name__ if exc.__cause__ is not None else "none",
                        diagnostic_code,
                        http_status if http_status is not None else "none",
                        cause_code,
                        *self._routing_diagnostic_counts(task, ref),
                    )
                    if "after output began" in str(exc):
                        # Interactive streams can discard an interrupted draft
                        # before retrying the next routed model. Callers without a
                        # reset channel retain the conservative fail-fast path so
                        # partial output is never joined to another model's text.
                        if on_stream_reset is None:
                            raise
                        on_stream_reset()
                    self.mark_unavailable(target)
                    continue
                self._record_response(task, response, start, budget, reservation)
                # A provider response is billable even if cancellation wins while
                # the request is in flight. Persist accounting before suppressing
                # the now-stale result so spend never disappears from the ledger.
                if cancel_check is not None and cancel_check():
                    raise LLMCancelledError("LLM request cancelled")
                return response
        if last_error is not None:
            raise ProviderError(
                f"all routed models failed: {last_error}",
                diagnostic_code="routes_exhausted",
                http_status=provider_failure_diagnostics(last_error)[1],
            ) from last_error
        _LOG.warning(
            "llm routing unavailable reason=no_available_client task=%s "
            "error_class=LLMConfigError cause_class=none "
            "configured_routes=%s candidates=%s unavailable_routes=%s",
            task.value,
            *self._routing_diagnostic_counts(task, ref),
        )
        raise LLMConfigError(f"no available client for task {task.value}")

    def _record_response(
        self,
        task: TaskType,
        response: LLMResponse,
        start: float,
        budget: BudgetGovernor,
        reservation: object,
    ) -> None:
        """Persist successful or uncertain billable egress exactly once."""
        with self._usage_lock:
            budget.record(task, response, reservation=reservation)
            usage = LLMUsage(
                task=task.value,
                provider=response.provider,
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cost_usd=settled_cost_usd(response),
                cost_source=settled_cost_source(response),
                duration_ms=int((time.perf_counter() - start) * 1000),
                cached_input_tokens=response.cached_input_tokens,
            )
            self.usage.append(usage)
            if self.on_usage is not None:
                self.on_usage(usage)

    def complete_json(
        self,
        task: TaskType,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 2048,
        ref: ModelRef | None = None,
        cancel_check: Callable[[], bool] | None = None,
    ) -> LLMResponse:
        """Return a non-streaming JSON response through compatible providers.

        Callers remain responsible for validating the domain schema. The
        provider client accepts only a complete JSON object, retries malformed
        output within its bounded transport budget and otherwise fails closed.
        """

        return self.complete(
            task,
            system=system,
            prompt=prompt,
            max_tokens=max_tokens,
            ref=ref,
            cancel_check=cancel_check,
            json_response=True,
        )

    def _completion_candidates(
        self,
        task: TaskType,
        explicit: ModelRef | None,
    ) -> list[ModelRef]:
        if explicit is not None:
            return [explicit]
        if task in (
            TaskType.PROTOCOL_SYNTHESIS,
            TaskType.QUERY_EXPANSION,
            TaskType.REPOSITORY_ANALYSIS,
            TaskType.CHAT,
        ):
            return self.synthesis_refs()
        if task is TaskType.CLAIM_VERIFICATION and self._uses_internal_gemini_screening():
            return self._workspace_gemini_refs()
        if task in (
            TaskType.ADJUDICATION,
            TaskType.FULL_TEXT_SCREENING,
            TaskType.CLAIM_VERIFICATION,
        ):
            return self.precision_refs()
        target = self._default_ref(task)
        return [target] if target is not None else []

    def _default_ref(self, task: TaskType) -> ModelRef | None:
        if task in (
            TaskType.PROTOCOL_SYNTHESIS,
            TaskType.QUERY_EXPANSION,
            TaskType.REPOSITORY_ANALYSIS,
            TaskType.CHAT,
        ):
            return self.routing.synthesis
        if task in (TaskType.ADJUDICATION, TaskType.FULL_TEXT_SCREENING):
            return self.routing.adjudication
        if task is TaskType.SCREENING and self.routing.screening:
            return self.routing.screening[0]
        if task is TaskType.CLAIM_VERIFICATION:  # wide, mechanical -> cheap model
            if self.routing.screening:
                return self.routing.screening[0]
            return self.routing.adjudication or self.routing.synthesis
        return None
