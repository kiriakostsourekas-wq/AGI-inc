"""Explicit trust-runtime state machine and terminal-state invariants."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from trust_contracts import TERMINAL_RUN_STATES, EffectClass, RunState, SecurityClock, uuid7

from .errors import InvalidTransitionError, TerminalStateError

_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.ENV_RESET, RunState.CANCELLED, RunState.FAILED}),
    RunState.ENV_RESET: frozenset(
        {RunState.CONTRACT_VALIDATED, RunState.SAFE_ABORTED, RunState.FAILED}
    ),
    RunState.CONTRACT_VALIDATED: frozenset(
        {RunState.OBSERVING, RunState.SAFE_ABORTED, RunState.FAILED}
    ),
    RunState.OBSERVING: frozenset(
        {
            RunState.PLANNING,
            RunState.REPLANNING,
            RunState.VERIFYING,
            RunState.FINALIZING,
            RunState.SAFE_ABORTED,
            RunState.HANDOFF_REQUIRED,
            RunState.FAILED,
        }
    ),
    RunState.PLANNING: frozenset(
        {
            RunState.ACTION_PROPOSED,
            RunState.SAFE_ABORTED,
            RunState.HANDOFF_REQUIRED,
            RunState.FAILED,
        }
    ),
    RunState.REPLANNING: frozenset(
        {
            RunState.ACTION_PROPOSED,
            RunState.SAFE_ABORTED,
            RunState.HANDOFF_REQUIRED,
            RunState.FAILED,
        }
    ),
    RunState.ACTION_PROPOSED: frozenset({RunState.POLICY_CHECKING, RunState.FAILED}),
    RunState.POLICY_CHECKING: frozenset(
        {
            RunState.WAITING_APPROVAL,
            RunState.EXECUTING,
            RunState.REPLANNING,
            RunState.SAFE_ABORTED,
            RunState.HANDOFF_REQUIRED,
            RunState.FAILED,
        }
    ),
    RunState.WAITING_APPROVAL: frozenset(
        {
            RunState.EXECUTING,
            RunState.REPLANNING,
            RunState.SAFE_ABORTED,
            RunState.HANDOFF_REQUIRED,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    RunState.EXECUTING: frozenset(
        {
            RunState.VERIFYING,
            RunState.OUTCOME_UNKNOWN,
            RunState.RECOVERING,
            RunState.FAILED,
        }
    ),
    RunState.VERIFYING: frozenset(
        {
            RunState.OBSERVING,
            RunState.RECOVERING,
            RunState.OUTCOME_UNKNOWN,
            RunState.FINALIZING,
            RunState.HANDOFF_REQUIRED,
            RunState.FAILED,
        }
    ),
    RunState.RECOVERING: frozenset(
        {
            RunState.OBSERVING,
            RunState.REPLANNING,
            RunState.SAFE_ABORTED,
            RunState.HANDOFF_REQUIRED,
            RunState.FAILED,
        }
    ),
    RunState.OUTCOME_UNKNOWN: frozenset(
        {
            RunState.VERIFYING,
            RunState.OBSERVING,
            RunState.HANDOFF_REQUIRED,
            RunState.FAILED_OUTCOME_UNKNOWN,
            RunState.FAILED,
        }
    ),
    RunState.FINALIZING: frozenset(
        {
            RunState.SUCCEEDED,
            RunState.SAFE_ABORTED,
            RunState.PARTIAL_SUCCESS,
            RunState.HANDOFF_REQUIRED,
            RunState.FAILED,
        }
    ),
}

_EXTERNAL_EFFECTS = frozenset(
    {
        EffectClass.REVERSIBLE_MUTATION,
        EffectClass.EXTERNAL_COMMUNICATION,
        EffectClass.FINANCIAL_OR_CONTRACTUAL_COMMIT,
    }
)


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    transition_id: UUID
    from_state: RunState
    to_state: RunState
    reason: str
    occurred_at: datetime


@dataclass(slots=True)
class RunStateMachine:
    run_id: UUID
    clock: SecurityClock
    state: RunState = RunState.CREATED
    committed_external_effects: int = 0
    verified_required_effects: int = 0
    goal_verified: bool = False
    no_external_effects_verified: bool = False
    history: list[TransitionRecord] = field(default_factory=list[TransitionRecord])

    def transition(self, target: RunState, *, reason: str) -> TransitionRecord:
        if self.state in TERMINAL_RUN_STATES:
            raise TerminalStateError(f"run is already terminal in {self.state.value}")
        normalized_target = self._normalize_terminal(target)
        allowed = _TRANSITIONS.get(self.state, frozenset())
        if normalized_target not in allowed:
            raise InvalidTransitionError(
                f"transition {self.state.value} -> {normalized_target.value} is not allowed"
            )
        self._validate_terminal(normalized_target)
        record = TransitionRecord(
            transition_id=uuid7(),
            from_state=self.state,
            to_state=normalized_target,
            reason=reason,
            occurred_at=self.clock.now(),
        )
        self.history.append(record)
        self.state = normalized_target
        return record

    def record_committed_effect(self, effect_class: EffectClass) -> None:
        if self.state in TERMINAL_RUN_STATES:
            raise TerminalStateError("cannot record an effect after termination")
        if effect_class in _EXTERNAL_EFFECTS:
            self.committed_external_effects += 1

    def record_verified_required_effect(self) -> None:
        if self.state in TERMINAL_RUN_STATES:
            raise TerminalStateError("cannot record verification after termination")
        self.verified_required_effects += 1

    def mark_goal_verified(self) -> None:
        if self.state in TERMINAL_RUN_STATES:
            raise TerminalStateError("cannot verify a terminal run")
        self.goal_verified = True

    def mark_no_external_effects_verified(self) -> None:
        if self.state in TERMINAL_RUN_STATES:
            raise TerminalStateError("cannot verify a terminal run")
        if self.committed_external_effects:
            raise InvalidTransitionError("cannot prove zero effects after a committed effect")
        self.no_external_effects_verified = True

    def cancel(self, *, reason: str) -> TransitionRecord:
        if self.state is RunState.OUTCOME_UNKNOWN:
            target = RunState.FAILED_OUTCOME_UNKNOWN
        else:
            target = (
                RunState.CANCELLED
                if self.committed_external_effects == 0
                else RunState.HANDOFF_REQUIRED
            )
        return self.transition(target, reason=reason)

    def terminate_for_budget(self, *, reason: str = "budget exhausted") -> TransitionRecord:
        if self.state is RunState.OUTCOME_UNKNOWN:
            target = RunState.FAILED_OUTCOME_UNKNOWN
        elif self.committed_external_effects == 0 and self.no_external_effects_verified:
            target = RunState.SAFE_ABORTED
        else:
            target = RunState.HANDOFF_REQUIRED
        return self.transition(target, reason=reason)

    def _normalize_terminal(self, target: RunState) -> RunState:
        if (
            target in {RunState.SAFE_ABORTED, RunState.CANCELLED}
            and self.committed_external_effects
        ):
            return RunState.HANDOFF_REQUIRED
        return target

    def _validate_terminal(self, target: RunState) -> None:
        if target is RunState.SUCCEEDED and not self.goal_verified:
            raise InvalidTransitionError(
                "SUCCEEDED requires independently verified goal predicates"
            )
        if target is RunState.PARTIAL_SUCCESS:
            if self.committed_external_effects == 0 or self.verified_required_effects == 0:
                raise InvalidTransitionError(
                    "PARTIAL_SUCCESS requires a committed and verified required effect"
                )
        if target is RunState.SAFE_ABORTED and self.committed_external_effects:
            raise InvalidTransitionError("SAFE_ABORTED cannot contain committed external effects")
        if target is RunState.SAFE_ABORTED and not self.no_external_effects_verified:
            raise InvalidTransitionError("SAFE_ABORTED requires verified zero external effects")
        if target is RunState.FAILED_OUTCOME_UNKNOWN and self.state is not RunState.OUTCOME_UNKNOWN:
            raise InvalidTransitionError("FAILED_OUTCOME_UNKNOWN requires an unresolved commit")
        if target is RunState.CANCELLED and self.committed_external_effects:
            raise InvalidTransitionError("CANCELLED cannot contain committed external effects")


def allowed_transitions(state: RunState) -> frozenset[RunState]:
    return _TRANSITIONS.get(state, frozenset())
