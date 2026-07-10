from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from trust_contracts import (
    ActionProposal,
    BookingCommitContext,
    FrozenSecurityClock,
    RunState,
    TaskContract,
    ToolName,
    TrustedTargetKind,
    VerificationResult,
    uuid7,
)

from trust_runtime.approvals import ApprovalAuthority
from trust_runtime.browser import (
    BrowserExecutor,
    BrowserPage,
    ModelUsage,
    Observation,
    ScreenshotObserver,
)
from trust_runtime.effects import TrustedTargetDescriptor
from trust_runtime.orchestrator import (
    AgentOrchestrator,
    BudgetMeter,
    DecisionContextAssembler,
    InMemoryApprovalBroker,
    MemoryEventSink,
)
from trust_runtime.policy import DeterministicPolicyEngine
from trust_runtime.state_machine import RunStateMachine
from trust_runtime.verification import VerificationOutcome


class FakeMouse:
    def __init__(self, page: "FakePage") -> None:
        self.page = page

    async def click(self, _x: float, _y: float) -> None:
        self.page.frame += 1

    async def dblclick(self, _x: float, _y: float) -> None:
        self.page.frame += 1

    async def wheel(self, _x: float, _y: float) -> None:
        self.page.frame += 1


class FakeKeyboard:
    async def type(self, _text: str) -> None:
        return None

    async def press(self, _key: str) -> None:
        return None


class FakePage:
    def __init__(self) -> None:
        self.url = "http://northstar.localhost:3001/northstar?runId=test"
        self.frame = 0
        self.mouse = FakeMouse(self)
        self.keyboard = FakeKeyboard()

    async def screenshot(self, *, type: str = "png") -> bytes:
        return f"png-{self.frame}".encode()

    async def goto(self, url: str, *, wait_until: str = "domcontentloaded") -> None:
        self.url = url
        self.frame += 1

    async def go_back(self, *, wait_until: str = "domcontentloaded") -> None:
        self.frame += 1

    async def wait_for_timeout(self, timeout: float) -> None:  # noqa: ASYNC109
        return None

    async def evaluate(self, expression: str, arg: Any = None) -> Any:
        return ""


class QueueAdapter:
    def __init__(self, run_id, tools: list[ToolName], usage_cost: Decimal = Decimal("0")) -> None:
        self.run_id = run_id
        self.tools = tools
        self.usage_cost = usage_cost
        self.calls = 0

    async def decide(self, *, observation: Observation, context: dict[str, Any]) -> ActionProposal:
        self.calls += 1
        tool = self.tools[min(self.calls - 1, len(self.tools) - 1)]
        return ActionProposal(
            run_id=self.run_id,
            step_number=self.calls,
            plan_version=1,
            observation_id=observation.observation_id,
            observation_hash=observation.screenshot_hash,
            tool=tool,
            target_description="Confirm exact itinerary" if tool is ToolName.CLICK else "Finish",
            coordinates_normalized=(500, 500) if tool is ToolName.CLICK else None,
            grounding_confidence=Decimal("0.9"),
            decision_summary="Use the current visible evidence.",
        )

    def take_usage(self) -> ModelUsage:
        return ModelUsage(input_tokens=100, output_tokens=20, cost_usd=self.usage_cost)


class BookingResolver:
    def __init__(self, target: TrustedTargetDescriptor | None) -> None:
        self.target = target

    async def resolve(
        self, *, page: BrowserPage, action: ActionProposal
    ) -> TrustedTargetDescriptor | None:
        return self.target if action.tool is ToolName.CLICK else None


class StubVerifier:
    def __init__(
        self,
        *,
        action_result: VerificationResult = VerificationResult.VERIFIED,
        unknown_result: VerificationResult = VerificationResult.OUTCOME_UNKNOWN,
        goal_result: VerificationResult = VerificationResult.VERIFIED,
        zero_result: VerificationResult = VerificationResult.VERIFIED,
        predicates: frozenset[str] = frozenset(),
    ) -> None:
        self.action_result = action_result
        self.unknown_result = unknown_result
        self.goal_result = goal_result
        self.zero_result = zero_result
        self.predicates = predicates
        self.unknown_calls = 0

    async def verify_action(self, **_: Any) -> VerificationOutcome:
        return VerificationOutcome(
            self.action_result,
            "stub action evidence",
            verified_predicates=self.predicates,
        )

    async def verify_unknown_effect(self, **_: Any) -> VerificationOutcome:
        self.unknown_calls += 1
        return VerificationOutcome(
            self.unknown_result,
            "stub unknown evidence",
            verified_predicates=self.predicates,
        )

    async def verify_goal(self, **_: Any) -> VerificationOutcome:
        return VerificationOutcome(self.goal_result, "stub goal evidence")

    async def verify_zero_external_effects(self, **_: Any) -> VerificationOutcome:
        return VerificationOutcome(self.zero_result, "stub zero-effect evidence")


def bounded_contract(contract: TaskContract, max_steps: int) -> TaskContract:
    payload = contract.model_dump(mode="json", exclude={"content_hash"})
    payload["max_steps"] = max_steps
    return TaskContract.model_validate(payload)


def booking_target(contract: TaskContract) -> TrustedTargetDescriptor:
    return TrustedTargetDescriptor(
        target_kind=TrustedTargetKind.BOOKING_CONFIRM,
        origin=contract.allowed_origins[1],
        trusted_target_id="northstar.confirm-rebooking",
        context=BookingCommitContext(
            traveler_id="traveler_maya_chen",
            reservation_id="NST-P7Q4M2",
            offer_version="offer-v1",
            marketing_carrier="Northstar Air",
            operating_carrier="Northstar Air",
            flight_id="NS451",
            origin_airport="SFO",
            destination_airport="SEA",
            departure=datetime(2030, 6, 14, 21, 10, tzinfo=UTC),
            arrival=datetime(2030, 6, 14, 23, 15, tzinfo=UTC),
            stop_count=0,
            cabin="economy",
            fare_class="Y",
            seat_type="aisle",
            base_fare_minor=35_000,
            taxes_and_fees_minor=3_900,
            total_additional_cost_minor=38_900,
            currency="USD",
        ),
    )


def make_orchestrator(
    *,
    contract: TaskContract,
    tools: list[ToolName],
    verifier: StubVerifier,
    target_present: bool = True,
    approve: bool = True,
    usage_cost: Decimal = Decimal("0"),
) -> tuple[AgentOrchestrator, QueueAdapter]:
    clock = FrozenSecurityClock(datetime(2026, 7, 10, tzinfo=UTC))
    run_id = uuid7()
    authority = ApprovalAuthority(
        signing_key=b"orchestrator-test-approval-key-32-bytes",
        clock=clock,
        default_ttl_seconds=180,
    )
    adapter = QueueAdapter(run_id, tools, usage_cost)
    page = FakePage()
    orchestrator = AgentOrchestrator(
        machine=RunStateMachine(run_id=run_id, clock=clock),
        contract=contract,
        page=page,
        observer=ScreenshotObserver(observation_id_factory=uuid7),
        adapter=adapter,
        target_resolver=BookingResolver(booking_target(contract) if target_present else None),
        executor=BrowserExecutor(allowed_origins=set(contract.allowed_origins)),
        verifier=verifier,
        policy=DeterministicPolicyEngine(clock),
        approval_broker=InMemoryApprovalBroker(
            authority=authority,
            decide=lambda _request: _approved() if approve else _rejected(),
        ),
        authority=authority,
        events=MemoryEventSink(),
        context=DecisionContextAssembler(
            contract=contract,
            plan={"plan_version": 1, "goal": contract.goal, "subgoals": []},
        ),
    )
    return orchestrator, adapter


async def _approved() -> bool:
    return True


async def _rejected() -> bool:
    return False


@pytest.mark.asyncio
async def test_finish_requires_independent_goal_verification(contract: TaskContract) -> None:
    orchestrator, _ = make_orchestrator(
        contract=bounded_contract(contract, 2),
        tools=[ToolName.FINISH],
        verifier=StubVerifier(goal_result=VerificationResult.VERIFIED),
    )
    assert await orchestrator.run() is RunState.SUCCEEDED


@pytest.mark.asyncio
async def test_unverified_finish_never_succeeds(contract: TaskContract) -> None:
    orchestrator, _ = make_orchestrator(
        contract=bounded_contract(contract, 2),
        tools=[ToolName.FINISH],
        verifier=StubVerifier(goal_result=VerificationResult.NOT_VERIFIED),
    )
    assert await orchestrator.run() is not RunState.SUCCEEDED


@pytest.mark.asyncio
async def test_unknown_commit_blocks_further_actor_decisions(contract: TaskContract) -> None:
    verifier = StubVerifier(action_result=VerificationResult.OUTCOME_UNKNOWN)
    orchestrator, adapter = make_orchestrator(
        contract=bounded_contract(contract, 2),
        tools=[ToolName.CLICK],
        verifier=verifier,
    )
    assert await orchestrator.run() is RunState.FAILED_OUTCOME_UNKNOWN
    assert adapter.calls == 1
    assert verifier.unknown_calls == 1


@pytest.mark.asyncio
async def test_verified_commit_and_resolved_unknown_continue_to_finish(
    contract: TaskContract,
) -> None:
    predicates = frozenset({"replacement_booking_verified"})
    verified, _ = make_orchestrator(
        contract=bounded_contract(contract, 4),
        tools=[ToolName.CLICK, ToolName.FINISH],
        verifier=StubVerifier(predicates=predicates),
    )
    assert await verified.run() is RunState.SUCCEEDED
    assert verified.machine.committed_external_effects == 1
    assert verified.machine.verified_required_effects == 1

    resolved, adapter = make_orchestrator(
        contract=bounded_contract(contract, 4),
        tools=[ToolName.CLICK, ToolName.FINISH],
        verifier=StubVerifier(
            action_result=VerificationResult.OUTCOME_UNKNOWN,
            unknown_result=VerificationResult.VERIFIED,
            predicates=predicates,
        ),
    )
    assert await resolved.run() is RunState.SUCCEEDED
    assert adapter.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action_result",
    [VerificationResult.CONSTRAINT_CHANGED, VerificationResult.NOT_VERIFIED],
)
async def test_recovery_paths_never_claim_success(
    contract: TaskContract, action_result: VerificationResult
) -> None:
    orchestrator, _ = make_orchestrator(
        contract=bounded_contract(contract, 2),
        tools=[ToolName.CLICK],
        verifier=StubVerifier(action_result=action_result),
    )
    assert await orchestrator.run() is not RunState.SUCCEEDED


@pytest.mark.asyncio
async def test_missing_target_and_rejected_approval_fail_closed(contract: TaskContract) -> None:
    missing, _ = make_orchestrator(
        contract=bounded_contract(contract, 2),
        tools=[ToolName.CLICK],
        verifier=StubVerifier(),
        target_present=False,
    )
    assert await missing.run() is not RunState.SUCCEEDED

    rejected, _ = make_orchestrator(
        contract=bounded_contract(contract, 2),
        tools=[ToolName.CLICK],
        verifier=StubVerifier(),
        approve=False,
    )
    assert await rejected.run() is not RunState.SUCCEEDED


@pytest.mark.asyncio
async def test_safe_abort_requires_zero_effect_proof(contract: TaskContract) -> None:
    safe, _ = make_orchestrator(
        contract=bounded_contract(contract, 2),
        tools=[ToolName.SAFE_ABORT],
        verifier=StubVerifier(zero_result=VerificationResult.VERIFIED),
    )
    assert await safe.run() is RunState.SAFE_ABORTED

    handoff, _ = make_orchestrator(
        contract=bounded_contract(contract, 2),
        tools=[ToolName.SAFE_ABORT],
        verifier=StubVerifier(zero_result=VerificationResult.NOT_VERIFIED),
    )
    assert await handoff.run() is RunState.HANDOFF_REQUIRED


@pytest.mark.asyncio
async def test_model_cost_overrun_stops_before_browser_effect(contract: TaskContract) -> None:
    payload = contract.model_dump(mode="json", exclude={"content_hash"})
    payload["max_model_cost_usd"] = "0.01"
    cost_bounded = TaskContract.model_validate(payload)
    orchestrator, adapter = make_orchestrator(
        contract=cost_bounded,
        tools=[ToolName.CLICK],
        verifier=StubVerifier(),
        usage_cost=Decimal("0.02"),
    )
    assert await orchestrator.run() is RunState.SAFE_ABORTED
    assert adapter.calls == 1
    assert orchestrator.machine.committed_external_effects == 0


def test_context_assembler_rejects_sealed_metadata(contract: TaskContract) -> None:
    assembler = DecisionContextAssembler(
        contract=contract,
        plan={"plan_version": 1, "fault_id": "F-AMBIGUOUS-COMMIT"},
    )
    with pytest.raises(ValueError, match="sealed field"):
        assembler.build(BudgetMeter(contract).snapshot())
