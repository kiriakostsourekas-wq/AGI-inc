"""Typed, scenario-agnostic recovery decisions for the bounded agent loop."""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from trust_contracts import FailureClass


class RecoveryAction(StrEnum):
    REOBSERVE = "REOBSERVE"
    REACQUIRE_TARGET = "REACQUIRE_TARGET"
    SAFE_READ_RETRY = "SAFE_READ_RETRY"
    REPLAN = "REPLAN"
    VERIFY_EXTERNAL_STATE = "VERIFY_EXTERNAL_STATE"
    HANDOFF = "HANDOFF"


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    failure: FailureClass
    action: RecoveryAction
    reason: str


class DeterministicRecoveryController:
    """Dispatch only on typed failure classes, never scenario or expected answers."""

    _ACTIONS: ClassVar[dict[FailureClass, RecoveryAction]] = {
        FailureClass.TARGET_NOT_FOUND: RecoveryAction.REACQUIRE_TARGET,
        FailureClass.ACTION_NO_EFFECT: RecoveryAction.REOBSERVE,
        FailureClass.CONSTRAINT_DRIFT: RecoveryAction.REPLAN,
        FailureClass.AUTHENTICATION_EXPIRED: RecoveryAction.HANDOFF,
        FailureClass.OUTCOME_UNKNOWN: RecoveryAction.VERIFY_EXTERNAL_STATE,
        FailureClass.POLICY_BLOCKED: RecoveryAction.REPLAN,
        FailureClass.APPROVAL_REJECTED: RecoveryAction.REPLAN,
        FailureClass.APPROVAL_EXPIRED: RecoveryAction.REPLAN,
        FailureClass.NON_PROGRESS: RecoveryAction.REPLAN,
        FailureClass.NO_COMPLIANT_OPTION: RecoveryAction.HANDOFF,
        FailureClass.UNTRUSTED_INSTRUCTION_DETECTED: RecoveryAction.REPLAN,
        FailureClass.BUDGET_EXHAUSTED: RecoveryAction.HANDOFF,
    }

    def recover(self, failure: FailureClass) -> RecoveryDecision:
        action = self._ACTIONS[failure]
        return RecoveryDecision(
            failure=failure,
            action=action,
            reason=f"typed recovery for {failure.value.lower()}",
        )
