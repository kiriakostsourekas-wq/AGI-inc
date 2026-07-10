from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from trust_contracts import TERMINAL_RUN_STATES, EffectClass, FrozenSecurityClock, RunState, uuid7

from trust_runtime.errors import InvalidTransitionError, TerminalStateError
from trust_runtime.state_machine import RunStateMachine, allowed_transitions


def _advance_to_verifying(machine: RunStateMachine) -> None:
    for state in (
        RunState.ENV_RESET,
        RunState.CONTRACT_VALIDATED,
        RunState.OBSERVING,
        RunState.PLANNING,
        RunState.ACTION_PROPOSED,
        RunState.POLICY_CHECKING,
        RunState.EXECUTING,
        RunState.VERIFYING,
    ):
        machine.transition(state, reason="test path")


def test_success_requires_independent_verification(clock) -> None:
    machine = RunStateMachine(run_id=uuid7(), clock=clock)
    _advance_to_verifying(machine)
    machine.transition(RunState.FINALIZING, reason="actor requested finish")
    with pytest.raises(InvalidTransitionError, match="verified"):
        machine.transition(RunState.SUCCEEDED, reason="model claimed success")
    machine.mark_goal_verified()
    machine.transition(RunState.SUCCEEDED, reason="predicates verified")
    assert machine.state is RunState.SUCCEEDED


def test_safe_abort_after_commit_becomes_handoff(clock) -> None:
    machine = RunStateMachine(run_id=uuid7(), clock=clock)
    _advance_to_verifying(machine)
    machine.record_committed_effect(EffectClass.FINANCIAL_OR_CONTRACTUAL_COMMIT)
    machine.transition(RunState.SAFE_ABORTED, reason="calendar budget exhausted")
    assert machine.state is RunState.HANDOFF_REQUIRED


def test_terminal_state_is_immutable(clock) -> None:
    machine = RunStateMachine(run_id=uuid7(), clock=clock)
    machine.transition(RunState.CANCELLED, reason="user cancelled")
    with pytest.raises(TerminalStateError):
        machine.transition(RunState.FAILED, reason="late worker error")


def test_invalid_transition_and_terminal_mutators_fail_closed(clock) -> None:
    machine = RunStateMachine(run_id=uuid7(), clock=clock)
    with pytest.raises(InvalidTransitionError, match="not allowed"):
        machine.transition(RunState.SUCCEEDED, reason="skip trust states")
    machine.transition(RunState.CANCELLED, reason="done")
    with pytest.raises(TerminalStateError):
        machine.record_committed_effect(EffectClass.READ)
    with pytest.raises(TerminalStateError):
        machine.record_verified_required_effect()
    with pytest.raises(TerminalStateError):
        machine.mark_goal_verified()
    with pytest.raises(TerminalStateError):
        machine.mark_no_external_effects_verified()


def test_effect_and_zero_effect_accounting(clock) -> None:
    machine = RunStateMachine(run_id=uuid7(), clock=clock)
    machine.record_committed_effect(EffectClass.READ)
    assert machine.committed_external_effects == 0
    machine.mark_no_external_effects_verified()
    assert machine.no_external_effects_verified

    committed = RunStateMachine(run_id=uuid7(), clock=clock)
    committed.record_committed_effect(EffectClass.REVERSIBLE_MUTATION)
    with pytest.raises(InvalidTransitionError, match="zero effects"):
        committed.mark_no_external_effects_verified()


def test_cancel_selects_clean_unknown_and_committed_terminals(clock) -> None:
    clean = RunStateMachine(run_id=uuid7(), clock=clock)
    assert clean.cancel(reason="user").to_state is RunState.CANCELLED

    unknown = RunStateMachine(run_id=uuid7(), clock=clock)
    _advance_to_verifying(unknown)
    unknown.transition(RunState.OUTCOME_UNKNOWN, reason="ambiguous")
    assert unknown.cancel(reason="user").to_state is RunState.FAILED_OUTCOME_UNKNOWN

    committed = RunStateMachine(run_id=uuid7(), clock=clock)
    _advance_to_verifying(committed)
    committed.record_committed_effect(EffectClass.FINANCIAL_OR_CONTRACTUAL_COMMIT)
    assert committed.cancel(reason="user").to_state is RunState.HANDOFF_REQUIRED


def test_budget_termination_requires_zero_effect_proof(clock) -> None:
    safe = RunStateMachine(run_id=uuid7(), clock=clock)
    safe.transition(RunState.ENV_RESET, reason="start")
    safe.mark_no_external_effects_verified()
    assert safe.terminate_for_budget().to_state is RunState.SAFE_ABORTED

    handoff = RunStateMachine(run_id=uuid7(), clock=clock)
    _advance_to_verifying(handoff)
    assert handoff.terminate_for_budget().to_state is RunState.HANDOFF_REQUIRED

    unknown = RunStateMachine(run_id=uuid7(), clock=clock)
    _advance_to_verifying(unknown)
    unknown.transition(RunState.OUTCOME_UNKNOWN, reason="ambiguous")
    assert unknown.terminate_for_budget().to_state is RunState.FAILED_OUTCOME_UNKNOWN


def test_partial_success_requires_committed_and_verified_effect(clock) -> None:
    machine = RunStateMachine(run_id=uuid7(), clock=clock)
    _advance_to_verifying(machine)
    machine.transition(RunState.FINALIZING, reason="partial")
    with pytest.raises(InvalidTransitionError, match="committed and verified"):
        machine.transition(RunState.PARTIAL_SUCCESS, reason="missing evidence")
    machine.record_committed_effect(EffectClass.REVERSIBLE_MUTATION)
    machine.record_verified_required_effect()
    transition = machine.transition(RunState.PARTIAL_SUCCESS, reason="verified")
    assert transition.to_state is RunState.PARTIAL_SUCCESS


def test_allowed_transition_introspection() -> None:
    assert RunState.ENV_RESET in allowed_transitions(RunState.CREATED)
    assert not allowed_transitions(RunState.SUCCEEDED)


@given(
    st.sampled_from(
        [
            EffectClass.REVERSIBLE_MUTATION,
            EffectClass.EXTERNAL_COMMUNICATION,
            EffectClass.FINANCIAL_OR_CONTRACTUAL_COMMIT,
        ]
    )
)
def test_property_committed_effect_never_ends_safe_aborted(effect_class) -> None:
    clock = FrozenSecurityClock(datetime(2026, 7, 9, 18, 0, tzinfo=UTC))
    machine = RunStateMachine(run_id=uuid7(), clock=clock)
    _advance_to_verifying(machine)
    machine.record_committed_effect(effect_class)
    machine.transition(RunState.SAFE_ABORTED, reason="property test termination")
    assert machine.state in TERMINAL_RUN_STATES
    assert machine.state is not RunState.SAFE_ABORTED
    assert machine.state is not RunState.CANCELLED
