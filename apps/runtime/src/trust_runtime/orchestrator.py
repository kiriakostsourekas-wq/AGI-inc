"""Bounded, evidence-driven computer-use orchestration loop."""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from time import monotonic
from typing import Protocol, cast

from trust_contracts import (
    ActionProposal,
    ApprovalGrant,
    ApprovalRequest,
    AuthorizedAction,
    EffectClass,
    EffectProposal,
    FailureClass,
    PolicyDecision,
    PolicyVerdict,
    RunState,
    TaskContract,
    ToolName,
    VerificationResult,
    sha256_hex,
)

from .approvals import ApprovalAuthority
from .browser import (
    AgentAdapter,
    BrowserExecutor,
    BrowserPage,
    ModelUsage,
    Observation,
    ScreenshotObserver,
    UsageReportingAdapter,
)
from .effects import EffectDeriver, TrustedTargetDescriptor
from .policy import DeterministicPolicyEngine, PolicyContext
from .recovery import DeterministicRecoveryController, RecoveryAction
from .state_machine import RunStateMachine
from .telemetry import set_attributes, tracer
from .verification import RuntimeVerifier, VerificationOutcome

SEALED_CONTEXT_KEYS = frozenset(
    {
        "scenario_id",
        "scenario_seed",
        "fault_id",
        "fault_assignments",
        "expected_terminal_outcome",
        "oracle_case_ref",
        "oracle_version",
        "manifest_hash",
    }
)


class TargetResolver(Protocol):
    async def resolve(
        self, *, page: BrowserPage, action: ActionProposal
    ) -> TrustedTargetDescriptor | None: ...


class ApprovalBroker(Protocol):
    async def authorize(
        self, *, effect: EffectProposal, decision: PolicyDecision, contract_hash: str
    ) -> AuthorizedAction | None: ...


class EventSink(Protocol):
    async def append(self, event_type: str, payload: dict[str, object]) -> None: ...


class SecurityRecordSink(Protocol):
    def record(self, effect: EffectProposal, decision: PolicyDecision) -> None: ...


@dataclass(frozen=True, slots=True)
class NullSecurityRecordSink:
    def record(self, effect: EffectProposal, decision: PolicyDecision) -> None:
        return None


@dataclass(slots=True)
class MemoryEventSink:
    events: list[tuple[str, dict[str, object]]] = field(default_factory=lambda: [])

    async def append(self, event_type: str, payload: dict[str, object]) -> None:
        self.events.append((event_type, payload))


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    steps: int
    model_calls: int
    replans: int
    model_cost_usd: Decimal
    elapsed_seconds: float


@dataclass(slots=True)
class BudgetMeter:
    contract: TaskContract
    started_at: float = field(default_factory=monotonic)
    steps: int = 0
    model_calls: int = 0
    replans: int = 0
    model_cost_usd: Decimal = Decimal("0")

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            steps=self.steps,
            model_calls=self.model_calls,
            replans=self.replans,
            model_cost_usd=self.model_cost_usd,
            elapsed_seconds=max(0, monotonic() - self.started_at),
        )

    def exhausted(self) -> bool:
        current = self.snapshot()
        return (
            current.steps >= self.contract.max_steps
            or current.model_calls >= self.contract.max_model_calls
            or current.replans > self.contract.max_replans
            or current.elapsed_seconds >= self.contract.max_wall_time_seconds
            or current.model_cost_usd >= self.contract.max_model_cost_usd
        )


def _assert_actor_context_is_unsealed(value: object) -> None:
    if isinstance(value, dict):
        mapping = cast(dict[str, object], value)
        for key, child in mapping.items():
            if key in SEALED_CONTEXT_KEYS:
                raise ValueError(f"sealed field {key!r} cannot enter actor context")
            _assert_actor_context_is_unsealed(child)
    elif isinstance(value, (list, tuple)):
        sequence = cast(list[object] | tuple[object, ...], value)
        for child in sequence:
            _assert_actor_context_is_unsealed(child)


@dataclass(slots=True)
class DecisionContextAssembler:
    contract: TaskContract
    plan: dict[str, object]
    belief_facts: list[dict[str, object]] = field(default_factory=lambda: [])
    summaries: deque[dict[str, object]] = field(default_factory=lambda: deque(maxlen=8))

    def build(self, budgets: BudgetSnapshot) -> dict[str, object]:
        context: dict[str, object] = {
            "schema_version": "1.0.0",
            "prompt_version": "actor-v1",
            "task_contract": self.contract.actor_payload(),
            "plan": self.plan,
            "belief_facts": self.belief_facts,
            "recent_step_summaries": list(self.summaries),
            "remaining_budgets": {
                "steps": max(0, self.contract.max_steps - budgets.steps),
                "model_calls": max(0, self.contract.max_model_calls - budgets.model_calls),
                "replans": max(0, self.contract.max_replans - budgets.replans),
                "wall_seconds": max(
                    0, self.contract.max_wall_time_seconds - int(budgets.elapsed_seconds)
                ),
                "model_cost_usd": format(
                    max(Decimal("0"), self.contract.max_model_cost_usd - budgets.model_cost_usd),
                    ".2f",
                ),
            },
        }
        _assert_actor_context_is_unsealed(context)
        return context

    def record_summary(self, *, action: ActionProposal, outcome: VerificationOutcome) -> None:
        self.summaries.append(
            {
                "step_number": action.step_number,
                "tool": action.tool.value,
                "decision_summary": action.decision_summary,
                "verification": outcome.result.value,
                "evidence_ids": list(outcome.evidence_ids),
            }
        )


DecisionCallback = Callable[[ApprovalRequest], Awaitable[bool]]
ApprovalBindingCallback = Callable[[EffectProposal], Awaitable[None]]


class InMemoryApprovalBroker:
    """Test/demo broker. Durable live runs use the PostgreSQL trust gateway."""

    def __init__(
        self,
        *,
        authority: ApprovalAuthority,
        decide: DecisionCallback,
        bind: ApprovalBindingCallback | None = None,
    ) -> None:
        self._authority = authority
        self._decide = decide
        self._bind = bind

    async def authorize(
        self, *, effect: EffectProposal, decision: PolicyDecision, contract_hash: str
    ) -> AuthorizedAction | None:
        request = self._authority.request(effect=effect, summary=effect.action.decision_summary)
        if not await self._decide(request):
            self._authority.reject(request.request_id)
            return None
        grant: ApprovalGrant = self._authority.approve(
            request_id=request.request_id, contract_hash=contract_hash
        )
        if self._bind is not None:
            await self._bind(effect)
        return self._authority.consume_and_authorize(
            grant=grant,
            effect=effect,
            policy_decision=decision,
            contract_hash=contract_hash,
        )


@dataclass(slots=True)
class AgentOrchestrator:
    machine: RunStateMachine
    contract: TaskContract
    page: BrowserPage
    observer: ScreenshotObserver
    adapter: AgentAdapter
    target_resolver: TargetResolver
    executor: BrowserExecutor
    verifier: RuntimeVerifier
    policy: DeterministicPolicyEngine
    approval_broker: ApprovalBroker
    authority: ApprovalAuthority
    events: EventSink
    context: DecisionContextAssembler
    security_records: SecurityRecordSink = field(default_factory=NullSecurityRecordSink)
    recovery: DeterministicRecoveryController = field(
        default_factory=DeterministicRecoveryController
    )
    typed_recovery_enabled: bool = True
    budgets: BudgetMeter = field(init=False)
    _pairs: Counter[tuple[str, str]] = field(default_factory=lambda: Counter[tuple[str, str]]())
    _unresolved_effect: EffectProposal | None = None
    _verified_predicates: set[str] = field(default_factory=lambda: set[str]())
    _committed_effect_ids: set[str] = field(default_factory=lambda: set[str]())
    _last_actor_step: int = 0

    def __post_init__(self) -> None:
        self.budgets = BudgetMeter(self.contract)

    async def run(self) -> RunState:
        await self._transition(RunState.ENV_RESET, "isolated environment reset")
        await self._transition(RunState.CONTRACT_VALIDATED, "immutable contract validated")
        await self._transition(RunState.OBSERVING, "begin screenshot observation")

        while self.machine.state not in {
            RunState.SUCCEEDED,
            RunState.SAFE_ABORTED,
            RunState.PARTIAL_SUCCESS,
            RunState.HANDOFF_REQUIRED,
            RunState.FAILED_OUTCOME_UNKNOWN,
            RunState.FAILED,
            RunState.CANCELLED,
        }:
            if self.budgets.exhausted():
                await self._terminate_for_budget()
                break
            if self.machine.state is RunState.OUTCOME_UNKNOWN:
                await self._resolve_unknown()
                continue
            await self._decision_step()
        return self.machine.state

    async def _decision_step(self) -> None:
        if self.machine.state is RunState.OBSERVING:
            await self._transition(RunState.PLANNING, "continue current evidence-linked plan")
        observation = await self.observer.capture(self.page)
        self.budgets.model_calls += 1
        action = await self.adapter.decide(
            observation=observation,
            context=self.context.build(self.budgets.snapshot()),
        )
        usage = (
            self.adapter.take_usage()
            if isinstance(self.adapter, UsageReportingAdapter)
            else ModelUsage(input_tokens=0, output_tokens=0, cost_usd=Decimal("0"))
        )
        self.budgets.model_cost_usd += usage.cost_usd
        await self.events.append(
            "model.usage",
            {
                "model_call_count": self.budgets.model_calls,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "call_cost_usd": format(usage.cost_usd, "f"),
                "cumulative_cost_usd": format(self.budgets.model_cost_usd, "f"),
            },
        )
        if self.budgets.model_cost_usd > self.contract.max_model_cost_usd:
            await self._terminate_for_budget()
            return
        self._validate_action(action, observation)
        self._last_actor_step = action.step_number
        self.budgets.steps += 1
        signature = sha256_hex(
            {
                "tool": action.tool.value,
                "target_description": action.target_description,
                "coordinates": action.coordinates_normalized,
                "text_hash": None if action.text is None else sha256_hex(action.text),
            }
        )
        pair = (observation.screenshot_hash, signature)
        self._pairs[pair] += 1
        if self._pairs[pair] >= self.contract.non_progress_limit:
            await self._recover(FailureClass.NON_PROGRESS)
            return

        await self._transition(RunState.ACTION_PROPOSED, action.decision_summary)
        await self.events.append(
            "action.proposed",
            {
                "action_id": str(action.action_id),
                "step_number": action.step_number,
                "tool": action.tool.value,
                "observation_hash": observation.screenshot_hash,
                "decision_summary": action.decision_summary,
            },
        )
        await self._transition(RunState.POLICY_CHECKING, "derive trusted semantic effect")
        target = await self.target_resolver.resolve(page=self.page, action=action)
        if target is None and action.tool not in {ToolName.FINISH, ToolName.SAFE_ABORT}:
            await self._recover(FailureClass.TARGET_NOT_FOUND)
            return
        effect = EffectDeriver().derive(
            action=action,
            contract=self.contract,
            trusted_target=target,
            derived_at=self.machine.clock.now(),
        )
        decision = self.policy.evaluate(
            effect,
            PolicyContext(
                self.contract,
                verified_predicates=frozenset(self._verified_predicates),
            ),
        )
        self.security_records.record(effect, decision)
        await self.events.append(
            "policy.decision",
            {
                "effect_id": str(effect.effect_id),
                "effect_class": effect.effect_class.value,
                "verdict": decision.verdict.value,
                "rule_id": decision.rule_id,
                "context_hash": effect.approved_context_hash,
            },
        )
        if decision.verdict is PolicyVerdict.DENY:
            await self._recover(FailureClass.POLICY_BLOCKED)
            return
        if decision.verdict is PolicyVerdict.REQUIRE_APPROVAL:
            await self._transition(RunState.WAITING_APPROVAL, "exact semantic approval required")
            authorized = await self.approval_broker.authorize(
                effect=effect,
                decision=decision,
                contract_hash=self.contract.content_hash,
            )
            if authorized is None:
                await self._recover(FailureClass.APPROVAL_REJECTED)
                return
        else:
            authorized = self.authority.authorize_allowed(effect=effect, policy_decision=decision)

        await self._transition(RunState.EXECUTING, "authorized action envelope accepted")
        if action.tool in {ToolName.FINISH, ToolName.SAFE_ABORT}:
            await self._verify_terminal(action)
            return
        receipt = await self.executor.execute(self.page, authorized)
        await self.events.append(
            "action.executed",
            {
                "action_id": receipt.action_id,
                "tool": receipt.tool,
                "url_after": receipt.url_after,
                "screenshot_hash_after": receipt.screenshot_hash_after,
            },
        )
        await self._transition(RunState.VERIFYING, "verify expected visible postconditions")
        with tracer("verification").start_as_current_span("verification.action") as span:
            set_attributes(
                span,
                {
                    "trust.run_id": str(self.machine.run_id),
                    "trust.action_id": str(action.action_id),
                    "trust.step_number": action.step_number,
                },
            )
            outcome = await self.verifier.verify_action(
                page=self.page,
                action=action,
                effect=effect,
                receipt=receipt,
                observation=observation,
            )
        self.context.record_summary(action=action, outcome=outcome)
        await self.events.append(
            "verification.completed",
            {
                "result": outcome.result.value,
                "reason": outcome.reason,
                "effect_id": str(effect.effect_id),
                "verified_predicates": sorted(outcome.verified_predicates),
            },
        )
        if outcome.result is VerificationResult.VERIFIED:
            if effect.effect_class in {
                EffectClass.REVERSIBLE_MUTATION,
                EffectClass.EXTERNAL_COMMUNICATION,
                EffectClass.FINANCIAL_OR_CONTRACTUAL_COMMIT,
            }:
                effect_key = str(effect.effect_id)
                if effect_key not in self._committed_effect_ids:
                    self.machine.record_committed_effect(effect.effect_class)
                    self._committed_effect_ids.add(effect_key)
            for predicate in outcome.verified_predicates:
                if predicate not in self._verified_predicates:
                    self.machine.record_verified_required_effect()
                    self._verified_predicates.add(predicate)
            await self._transition(RunState.OBSERVING, "postcondition verified")
        elif outcome.result is VerificationResult.OUTCOME_UNKNOWN:
            self._unresolved_effect = effect
            await self._transition(RunState.OUTCOME_UNKNOWN, "commit outcome remains ambiguous")
        elif outcome.result is VerificationResult.CONSTRAINT_CHANGED:
            await self._recover(FailureClass.CONSTRAINT_DRIFT)
        else:
            await self._recover(FailureClass.ACTION_NO_EFFECT)

    async def _verify_terminal(self, action: ActionProposal) -> None:
        await self._transition(RunState.VERIFYING, "verify terminal claim")
        if action.tool is ToolName.FINISH:
            with tracer("verification").start_as_current_span("verification.goal"):
                outcome = await self.verifier.verify_goal(page=self.page)
            if outcome.result is not VerificationResult.VERIFIED:
                await self._recover(FailureClass.ACTION_NO_EFFECT)
                return
            self.machine.mark_goal_verified()
            await self._transition(RunState.FINALIZING, "goal predicates verified")
            await self._transition(RunState.SUCCEEDED, "verified task success")
            return
        with tracer("verification").start_as_current_span("verification.zero_effects"):
            outcome = await self.verifier.verify_zero_external_effects(page=self.page)
        if outcome.result is not VerificationResult.VERIFIED:
            await self._transition(RunState.HANDOFF_REQUIRED, "zero side effects not proven")
            return
        self.machine.mark_no_external_effects_verified()
        await self._transition(RunState.FINALIZING, "zero external effects verified")
        await self._transition(RunState.SAFE_ABORTED, "safe abort verified")

    async def _resolve_unknown(self) -> None:
        effect = self._unresolved_effect
        if effect is None:
            await self._transition(RunState.FAILED_OUTCOME_UNKNOWN, "missing unresolved effect")
            return
        self.budgets.steps += 1
        await self._transition(RunState.VERIFYING, "verify external state before any commit")
        with tracer("verification").start_as_current_span("verification.unknown_effect"):
            outcome = await self.verifier.verify_unknown_effect(page=self.page, effect=effect)
        await self.events.append(
            "verification.unknown_effect",
            {
                "result": outcome.result.value,
                "reason": outcome.reason,
                "effect_id": str(effect.effect_id),
                "verified_predicates": sorted(outcome.verified_predicates),
            },
        )
        if outcome.result is VerificationResult.VERIFIED:
            effect_key = str(effect.effect_id)
            if effect_key not in self._committed_effect_ids:
                self.machine.record_committed_effect(effect.effect_class)
                self._committed_effect_ids.add(effect_key)
            for predicate in outcome.verified_predicates:
                if predicate not in self._verified_predicates:
                    self.machine.record_verified_required_effect()
                    self._verified_predicates.add(predicate)
            self._unresolved_effect = None
            await self._transition(RunState.OBSERVING, "ambiguous commit resolved visibly")
        elif outcome.result is VerificationResult.OUTCOME_UNKNOWN:
            await self._transition(RunState.OUTCOME_UNKNOWN, "external state still ambiguous")
        else:
            self._unresolved_effect = None
            await self._transition(RunState.OBSERVING, "commit visibly absent; replanning allowed")

    async def _recover(self, failure: FailureClass) -> None:
        if not self.typed_recovery_enabled:
            await self.events.append(
                "baseline.recovery",
                {
                    "failure_observed": failure.value,
                    "action": "GENERIC_REOBSERVE",
                    "reason": "typed failure-class recovery is ablated",
                },
            )
            if self.machine.state in {RunState.POLICY_CHECKING, RunState.WAITING_APPROVAL}:
                self.budgets.replans += 1
                await self._transition(
                    RunState.REPLANNING, "baseline generic replan after blocked action"
                )
            elif self.machine.state in {RunState.PLANNING, RunState.REPLANNING}:
                await self._transition(
                    RunState.HANDOFF_REQUIRED, "baseline made no progress from planning"
                )
            else:
                await self._transition(
                    RunState.RECOVERING, "baseline generic recovery without failure policy"
                )
                await self._transition(RunState.OBSERVING, "baseline generic reobservation")
            return
        decision = self.recovery.recover(failure)
        await self.events.append(
            "recovery.decided",
            {"failure": failure.value, "action": decision.action.value, "reason": decision.reason},
        )
        if self.machine.state in {RunState.POLICY_CHECKING, RunState.WAITING_APPROVAL}:
            self.budgets.replans += 1
            await self._transition(RunState.REPLANNING, decision.reason)
            return
        if self.machine.state in {RunState.PLANNING, RunState.REPLANNING}:
            self.budgets.replans += 1
            await self._transition(RunState.HANDOFF_REQUIRED, decision.reason)
            return
        await self._transition(RunState.RECOVERING, decision.reason)
        if decision.action is RecoveryAction.REPLAN:
            self.budgets.replans += 1
            await self._transition(RunState.REPLANNING, decision.reason)
        elif decision.action is RecoveryAction.HANDOFF:
            await self._transition(RunState.HANDOFF_REQUIRED, decision.reason)
        else:
            await self._transition(RunState.OBSERVING, decision.reason)

    async def _terminate_for_budget(self) -> None:
        if self.machine.state is RunState.OUTCOME_UNKNOWN:
            await self._transition(
                RunState.FAILED_OUTCOME_UNKNOWN,
                "budget exhausted while commit outcome remained unknown",
            )
            return
        with tracer("verification").start_as_current_span("verification.budget_zero_effects"):
            outcome = await self.verifier.verify_zero_external_effects(page=self.page)
        if (
            outcome.result is VerificationResult.VERIFIED
            and not self.machine.committed_external_effects
        ):
            self.machine.mark_no_external_effects_verified()
        record = self.machine.terminate_for_budget()
        await self.events.append(
            "run.state_transition",
            {
                "from_state": record.from_state.value,
                "to_state": record.to_state.value,
                "reason": record.reason,
            },
        )

    def _validate_action(self, action: ActionProposal, observation: Observation) -> None:
        if action.run_id != self.machine.run_id:
            raise ValueError("actor proposal belongs to another run")
        if str(action.observation_id) != observation.observation_id:
            raise ValueError("actor proposal references another observation")
        if action.observation_hash != observation.screenshot_hash:
            raise ValueError("actor proposal observation hash is stale")
        if action.step_number != self._last_actor_step + 1:
            raise ValueError("actor proposal step number is not monotonic")

    async def _transition(self, target: RunState, reason: str) -> None:
        record = self.machine.transition(target, reason=reason)
        await self.events.append(
            "run.state_transition",
            {
                "from_state": record.from_state.value,
                "to_state": record.to_state.value,
                "reason": reason,
            },
        )
