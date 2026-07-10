"""Isolated Playwright worker entrypoint for runtime-created runs."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Protocol, cast
from uuid import UUID

from playwright.async_api import async_playwright
from trust_contracts import (
    TERMINAL_RUN_STATES,
    ActionProposal,
    ApprovalRequestStatus,
    AuthorizedAction,
    BookingCommitContext,
    EffectProposal,
    PolicyDecision,
    RunManifest,
    RunMode,
    RunState,
    ToolName,
    uuid7,
)

from .browser import (
    AgentAdapter,
    BrowserExecutor,
    BrowserPage,
    Observation,
    OpenAIResponsesAdapter,
    ScreenshotObserver,
    install_browser_egress_guard,
)
from .config import AgentProvider, RuntimeSettings
from .orchestrator import (
    AgentOrchestrator,
    ApprovalBroker,
    DecisionContextAssembler,
    EventSink,
    NullSecurityRecordSink,
    SecurityRecordSink,
)
from .policy import DeterministicPolicyEngine
from .sandbox_bridge import PageWithContext, SandboxTargetResolver, bind_sandbox_approval
from .state_machine import RunStateMachine
from .telemetry import current_trace_id, set_attributes, tracer
from .verification import BaselinePassThroughVerifier, VisibleTextVerifier


class WorkerService(Protocol):
    settings: RuntimeSettings
    approvals: Any

    def sealed_manifest(self, run_id: UUID) -> RunManifest: ...

    def run_machine(self, run_id: UUID) -> RunStateMachine: ...

    def append_worker_event(
        self, run_id: UUID, event_type: str, payload: dict[str, object]
    ) -> None: ...

    def record_security_bundle(
        self, *, effect: EffectProposal, decision: PolicyDecision
    ) -> None: ...

    def create_approval(
        self,
        *,
        run_id: UUID,
        effect: EffectProposal,
        decision: PolicyDecision,
        summary: str,
    ) -> Any: ...

    def decide_evaluation_approval(
        self, *, request: Any, effect: EffectProposal
    ) -> bool | None: ...

    def evaluation_approval_decision(self, *, effect: EffectProposal) -> bool | None: ...

    async def wait_for_approval(self, approval_id: UUID, timeout_seconds: int) -> Any: ...

    def consume_approval(
        self,
        *,
        grant: Any,
        effect: EffectProposal,
        policy_decision: PolicyDecision,
        contract_hash: str,
    ) -> AuthorizedAction: ...

    def authorize_active_approval(
        self,
        *,
        grant: Any,
        effect: EffectProposal,
        policy_decision: PolicyDecision,
        contract_hash: str,
    ) -> AuthorizedAction: ...

    def record_screenshot(self, *, run_id: UUID, content: bytes, source_url: str) -> Any: ...

    def mark_booking_verified(self, *, run_id: UUID) -> bool: ...


class RecordingScreenshotObserver(ScreenshotObserver):
    def __init__(self, *, service: WorkerService, run_id: UUID) -> None:
        super().__init__(observation_id_factory=uuid7)
        self._service = service
        self._run_id = run_id

    async def capture(self, page: BrowserPage) -> Observation:
        observation = await super().capture(page)
        self._service.record_screenshot(
            run_id=self._run_id,
            content=observation.screenshot_png,
            source_url=observation.url,
        )
        return observation


class ServiceEventSink(EventSink):
    def __init__(self, *, service: WorkerService, run_id: UUID) -> None:
        self._service = service
        self._run_id = run_id

    async def append(self, event_type: str, payload: dict[str, object]) -> None:
        correlated = {**payload, "run_id": str(self._run_id)}
        trace_id = current_trace_id()
        if trace_id is not None:
            correlated["trace_id"] = trace_id
        self._service.append_worker_event(self._run_id, event_type, correlated)
        predicates = payload.get("verified_predicates")
        effect_id = payload.get("effect_id")
        if (
            event_type in {"verification.completed", "verification.unknown_effect"}
            and payload.get("result") == "VERIFIED"
            and isinstance(predicates, list)
            and "replacement_booking_verified" in predicates
            and isinstance(effect_id, str)
        ):
            self._service.mark_booking_verified(run_id=self._run_id)


class ServiceSecurityRecordSink(SecurityRecordSink):
    def __init__(self, *, service: WorkerService) -> None:
        self._service = service

    def record(self, effect: EffectProposal, decision: PolicyDecision) -> None:
        self._service.record_security_bundle(effect=effect, decision=decision)


class ServiceApprovalBroker(ApprovalBroker):
    def __init__(
        self,
        *,
        service: WorkerService,
        page: PageWithContext,
        northstar_origin: str,
    ) -> None:
        self._service = service
        self._page = page
        self._northstar_origin = northstar_origin

    async def authorize(
        self, *, effect: EffectProposal, decision: PolicyDecision, contract_hash: str
    ) -> AuthorizedAction | None:
        request = self._service.create_approval(
            run_id=effect.action.run_id,
            effect=effect,
            decision=decision,
            summary=effect.action.decision_summary,
        )
        self._service.decide_evaluation_approval(request=request, effect=effect)
        current = await self._service.wait_for_approval(
            request.request_id,
            timeout_seconds=self._service.settings.approval_ttl_seconds,
        )
        if current.status is not ApprovalRequestStatus.APPROVED:
            return None
        grant = self._service.approvals.grant_for_request(request.request_id)
        if not isinstance(effect.context, BookingCommitContext):
            return None
        await bind_sandbox_approval(
            page=self._page,
            sandbox_origin=self._northstar_origin,
            admin_token=self._service.settings.sandbox_admin_token.get_secret_value(),
            run_id=str(effect.action.run_id),
            context=effect.context,
            runtime_grant_id=str(grant.payload.grant_id),
        )
        return self._service.authorize_active_approval(
            grant=grant,
            effect=effect,
            policy_decision=decision,
            contract_hash=contract_hash,
        )


class BaselineApprovalBroker(ApprovalBroker):
    """Evaluation-only approval opportunity without a bound capability or durable ledger."""

    def __init__(
        self,
        *,
        service: WorkerService,
        page: PageWithContext,
        northstar_origin: str,
        events: EventSink,
    ) -> None:
        self._service = service
        self._page = page
        self._northstar_origin = northstar_origin
        self._events = events

    async def authorize(
        self, *, effect: EffectProposal, decision: PolicyDecision, contract_hash: str
    ) -> AuthorizedAction | None:
        if not isinstance(effect.context, BookingCommitContext):
            return None
        await self._events.append(
            "approval.requested",
            {
                "baseline_ablation": True,
                "effect_id": str(effect.effect_id),
                "approved_context_hash": effect.approved_context_hash,
                "human_fixture": "approve-exact-compliant-context-v1",
            },
        )
        approved = self._service.evaluation_approval_decision(effect=effect)
        if approved is not True:
            await self._events.append(
                "approval.rejected",
                {
                    "baseline_ablation": True,
                    "effect_id": str(effect.effect_id),
                    "human_fixture": "approve-exact-compliant-context-v1",
                },
            )
            return None
        fixture_approval_id = uuid7()
        await bind_sandbox_approval(
            page=self._page,
            sandbox_origin=self._northstar_origin,
            admin_token=self._service.settings.sandbox_admin_token.get_secret_value(),
            run_id=str(effect.action.run_id),
            context=effect.context,
        )
        await self._events.append(
            "approval.approved",
            {
                "baseline_ablation": True,
                "effect_id": str(effect.effect_id),
                "fixture_approval_id": str(fixture_approval_id),
                "human_fixture": "approve-exact-compliant-context-v1",
            },
        )
        return AuthorizedAction(
            action=effect.action,
            effect=effect,
            policy_decision=decision,
            grant_id=fixture_approval_id,
            authorized_at=self._service.run_machine(effect.action.run_id).clock.now(),
        )


class FixtureWorkflowAdapter:
    """Recorded-as-mock coordinate adapter for no-key smoke and replay generation."""

    def __init__(self, *, run_id: UUID, origins: tuple[str, ...]) -> None:
        by_name = {origin.split("//", 1)[1].split(".", 1)[0]: origin for origin in origins}
        self._run_id = run_id
        self._calls = 0
        self._actions: list[tuple[ToolName, tuple[int, int] | None, str | None, str]] = [
            (
                ToolName.OPEN_URL,
                None,
                f"{by_name['northstar']}/?run={run_id}",
                "Open the rendered airline application.",
            ),
            (ToolName.CLICK, (793, 571), None, "Search rendered replacement options."),
            (ToolName.SCROLL, None, "down", "Inspect all rendered compliant options."),
            (ToolName.CLICK, (816, 863), None, "Review the preferred compliant nonstop option."),
            (ToolName.SCROLL, None, "down", "Reach the rendered confirmation control."),
            (ToolName.CLICK, (805, 857), None, "Confirm the exact paused rebooking effect."),
            (
                ToolName.OPEN_URL,
                None,
                f"{by_name['gomail']}/?run={run_id}",
                "Inspect the rendered confirmation email.",
            ),
            (
                ToolName.OPEN_URL,
                None,
                f"{by_name['northstar']}/?run={run_id}",
                "Corroborate the booking in rendered Manage Trip.",
            ),
            (
                ToolName.OPEN_URL,
                None,
                f"{by_name['dayplan']}/?run={run_id}",
                "Open the rendered calendar after verification.",
            ),
            (ToolName.CLICK, (874, 338), None, "Synchronize the verified travel block."),
            (ToolName.FINISH, None, None, "Finish after visible final verification."),
        ]

    async def decide(self, *, observation: Observation, context: dict[str, Any]) -> ActionProposal:
        if self._calls >= len(self._actions):
            raise RuntimeError("mock workflow exhausted without a verified terminal state")
        tool, coordinates, text, summary = self._actions[self._calls]
        self._calls += 1
        return ActionProposal(
            run_id=self._run_id,
            step_number=self._calls,
            plan_version=1,
            observation_id=UUID(observation.observation_id),
            observation_hash=observation.screenshot_hash,
            tool=tool,
            target_description=summary,
            coordinates_normalized=coordinates,
            text=text,
            grounding_confidence=Decimal("1"),
            decision_summary=summary,
        )


def _agent_adapter(settings: RuntimeSettings, manifest: RunManifest) -> AgentAdapter:
    if settings.agent_provider is AgentProvider.MOCK:
        return FixtureWorkflowAdapter(
            run_id=manifest.run_id,
            origins=manifest.task_contract.allowed_origins,
        )
    key = settings.openai_api_key
    if key is None or settings.agent_model is None:
        raise RuntimeError("live model configuration is incomplete")
    if "api.openai.com" not in settings.service_allowed_hosts:
        raise RuntimeError("OpenAI provider host is outside the service egress allowlist")
    return OpenAIResponsesAdapter(
        model=settings.agent_model,
        api_key=key.get_secret_value(),
        schema=ActionProposal.model_json_schema(),
        input_cost_per_million_usd=settings.model_input_cost_per_million_usd,
        output_cost_per_million_usd=settings.model_output_cost_per_million_usd,
        temperature=float(settings.agent_temperature),
        max_output_tokens=settings.agent_max_output_tokens,
        timeout_seconds=settings.agent_request_timeout_seconds,
    )


async def run_browser_worker(*, service: WorkerService, run_id: UUID) -> RunState:
    manifest = service.sealed_manifest(run_id)
    worker_tracer = tracer("worker")
    with worker_tracer.start_as_current_span("runtime.browser_run") as span:
        set_attributes(
            span,
            {
                "trust.run_id": str(run_id),
                "trust.mode": manifest.mode.value,
                "gen_ai.provider.name": manifest.model.provider,
                "gen_ai.request.model": manifest.model.model_id,
            },
        )
        return await _run_browser_worker_impl(service=service, run_id=run_id)


async def _run_browser_worker_impl(*, service: WorkerService, run_id: UUID) -> RunState:
    manifest = service.sealed_manifest(run_id)
    machine = service.run_machine(run_id)
    contract = manifest.task_contract
    service.append_worker_event(
        run_id,
        "worker.started",
        {
            "execution_kind": (
                "deterministic_mock" if manifest.mode is RunMode.MOCK else "live_model"
            ),
            "model_id": manifest.model.model_id,
        },
    )
    playwright = await async_playwright().start()
    browser = None
    try:
        browser = await playwright.chromium.launch(
            channel=service.settings.browser_channel,
            headless=True,
        )
        context = await browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1,
            locale="en-US",
            timezone_id="America/Los_Angeles",
            color_scheme="light",
        )
        await install_browser_egress_guard(
            cast(Any, context),
            allowed_origins=set(contract.allowed_origins),
        )
        reset_origin = contract.allowed_origins[0]
        reset = await context.request.post(
            f"{reset_origin}/api/sandbox/reset",
            data={
                "runId": str(run_id),
                "scenarioSeed": manifest.scenario_seed,
                "faultId": manifest.fault_id or "NONE",
                "mode": ("baseline" if manifest.mode is RunMode.BASELINE else "protected"),
            },
            headers={
                "x-sandbox-admin-token": service.settings.sandbox_admin_token.get_secret_value()
            },
        )
        if not reset.ok:
            raise RuntimeError("sandbox reset failed")
        page = await context.new_page()
        gomail_origin = next(origin for origin in contract.allowed_origins if "gomail" in origin)
        northstar_origin = next(
            origin for origin in contract.allowed_origins if "northstar" in origin
        )
        await page.goto(f"{gomail_origin}/?run={run_id}")
        await page.wait_for_timeout(500)
        browser_page = cast(BrowserPage, page)
        events = ServiceEventSink(service=service, run_id=run_id)
        baseline = manifest.mode is RunMode.BASELINE
        orchestrator = AgentOrchestrator(
            machine=machine,
            contract=contract,
            page=browser_page,
            observer=RecordingScreenshotObserver(service=service, run_id=run_id),
            adapter=_agent_adapter(service.settings, manifest),
            target_resolver=SandboxTargetResolver(),
            executor=BrowserExecutor(allowed_origins=set(contract.allowed_origins)),
            verifier=BaselinePassThroughVerifier() if baseline else VisibleTextVerifier(),
            policy=DeterministicPolicyEngine(machine.clock),
            approval_broker=(
                BaselineApprovalBroker(
                    service=service,
                    page=cast(PageWithContext, page),
                    northstar_origin=northstar_origin,
                    events=events,
                )
                if baseline
                else ServiceApprovalBroker(
                    service=service,
                    page=cast(PageWithContext, page),
                    northstar_origin=northstar_origin,
                )
            ),
            authority=service.approvals,
            events=events,
            security_records=(
                NullSecurityRecordSink() if baseline else ServiceSecurityRecordSink(service=service)
            ),
            context=DecisionContextAssembler(
                contract=contract,
                plan={"plan_version": 1, "goal": contract.goal, "subgoals": []},
            ),
            typed_recovery_enabled=not baseline,
        )
        state = await orchestrator.run()
        service.append_worker_event(run_id, "worker.finished", {"status": state.value})
        return state
    except Exception as error:
        service.append_worker_event(
            run_id,
            "worker.failed",
            {
                "error_type": type(error).__name__,
                "error_message": str(error)[:300],
            },
        )
        if machine.state not in TERMINAL_RUN_STATES:
            transition = machine.transition(RunState.FAILED, reason="browser worker failed closed")
            service.append_worker_event(
                run_id,
                "run.state_transition",
                {
                    "from_state": transition.from_state.value,
                    "to_state": transition.to_state.value,
                    "reason": transition.reason,
                },
            )
        return machine.state
    finally:
        if browser is not None:
            await browser.close()
        await playwright.stop()


def start_browser_worker(*, service: WorkerService, run_id: UUID) -> asyncio.Task[RunState]:
    return asyncio.create_task(run_browser_worker(service=service, run_id=run_id))
