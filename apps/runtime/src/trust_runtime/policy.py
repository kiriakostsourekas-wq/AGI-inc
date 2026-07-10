"""Deterministic policy skeleton; model output is never policy authority."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from trust_contracts import (
    BookingCommitContext,
    EffectClass,
    EffectProposal,
    PolicyDecision,
    PolicyVerdict,
    SecurityClock,
    TaskContract,
)


@dataclass(frozen=True, slots=True)
class PolicyContext:
    contract: TaskContract
    verified_predicates: frozenset[str] = frozenset()
    approved_context_hashes: frozenset[str] = frozenset()


class DeterministicPolicyEngine:
    """Small fail-closed policy engine for the MVP effect classes."""

    def __init__(self, clock: SecurityClock) -> None:
        self._clock = clock

    def evaluate(self, effect: EffectProposal, context: PolicyContext) -> PolicyDecision:
        if effect.contract_hash != context.contract.content_hash:
            return self._decision(
                effect,
                PolicyVerdict.DENY,
                "contract/hash-mismatch",
                "effect was derived for a different task contract",
            )
        origin = effect.origin
        if origin is not None and origin not in context.contract.allowed_origins:
            return self._decision(
                effect,
                PolicyVerdict.DENY,
                "origin/not-allowlisted",
                "target origin is outside the immutable allowlist",
            )
        if isinstance(effect.context, BookingCommitContext):
            failed = [
                result["field"]
                for result in booking_constraint_results(effect.context, context.contract)
                if not result["satisfied"]
            ]
            if failed:
                return self._decision(
                    effect,
                    PolicyVerdict.DENY,
                    "contract/hard-constraint-failed",
                    f"booking violates hard constraints: {', '.join(str(item) for item in failed)}",
                )
        if effect.effect_class is EffectClass.EXTERNAL_COMMUNICATION:
            return self._decision(
                effect,
                PolicyVerdict.DENY,
                "effect/external-communication-disabled",
                "external communication is excluded from the MVP",
            )
        if effect.effect_class is EffectClass.FINANCIAL_OR_CONTRACTUAL_COMMIT:
            return self._decision(
                effect,
                PolicyVerdict.REQUIRE_APPROVAL,
                "effect/contractual-commit-requires-approval",
                "booking commits require an exact, single-use human grant",
            )
        if effect.effect_class is EffectClass.CREDENTIAL_OR_IDENTITY:
            verdict = (
                PolicyVerdict.ALLOW
                if effect.approved_context_hash in context.approved_context_hashes
                else PolicyVerdict.REQUIRE_APPROVAL
            )
            return self._decision(
                effect,
                verdict,
                "effect/credential-requires-bound-grant",
                "secret injection is allowed only inside the approved semantic context",
            )
        if effect.effect_class is EffectClass.REVERSIBLE_MUTATION:
            if "replacement_booking_verified" not in context.verified_predicates:
                return self._decision(
                    effect,
                    PolicyVerdict.DENY,
                    "calendar/booking-not-verified",
                    "calendar updates require a verified replacement booking",
                )
            return self._decision(
                effect,
                PolicyVerdict.ALLOW,
                "calendar/verified-booking",
                "verified booking permits the contract-scoped calendar update",
            )
        if effect.effect_class in {EffectClass.READ, EffectClass.DRAFT}:
            return self._decision(
                effect,
                PolicyVerdict.ALLOW,
                "effect/non-committing",
                "allowlisted read or draft action has no external side effect",
            )
        return self._decision(
            effect,
            PolicyVerdict.DENY,
            "effect/fail-closed",
            "effect class has no applicable allow rule",
        )

    def _decision(
        self,
        effect: EffectProposal,
        verdict: PolicyVerdict,
        rule_id: str,
        reason: str,
    ) -> PolicyDecision:
        return PolicyDecision(
            effect_id=effect.effect_id,
            verdict=verdict,
            rule_id=rule_id,
            reason=reason,
            context_hash=effect.approved_context_hash,
            evaluated_at=self._clock.now(),
        )


def booking_constraint_results(
    booking: BookingCommitContext, contract: TaskContract
) -> list[dict[str, Any]]:
    """Evaluate every supported booking constraint conjunctively."""

    values: dict[str, object] = {
        "origin": booking.origin_airport,
        "destination": booking.destination_airport,
        "departure": booking.departure,
        "arrival": booking.arrival,
        "cabin": booking.cabin,
        "seat_type": booking.seat_type,
        "additional_cost": booking.total_additional_cost_minor,
    }
    output: list[dict[str, Any]] = []
    for constraint in contract.hard_constraints:
        actual = values.get(constraint.field)
        expected: object = constraint.value
        if constraint.field in {"departure", "arrival"} and isinstance(expected, str):
            expected = datetime.fromisoformat(expected)
        if constraint.field == "additional_cost" and isinstance(expected, dict):
            amount = expected.get("amount_minor")
            currency = expected.get("currency")
            satisfied = (
                isinstance(amount, int)
                and booking.total_additional_cost_minor <= amount
                and booking.currency == currency
            )
        elif constraint.operator == "equals":
            satisfied = actual == expected
        elif constraint.operator == "on_or_after":
            satisfied = (
                isinstance(actual, datetime)
                and isinstance(expected, datetime)
                and actual >= expected
            )
        elif constraint.operator == "on_or_before":
            satisfied = (
                isinstance(actual, datetime)
                and isinstance(expected, datetime)
                and actual <= expected
            )
        elif constraint.operator == "less_than_or_equal":
            satisfied = Decimal(str(actual)) <= Decimal(str(expected))
        else:
            satisfied = False
        output.append(
            {
                "field": constraint.field,
                "operator": constraint.operator,
                "expected": constraint.value,
                "actual": actual.isoformat() if isinstance(actual, datetime) else actual,
                "satisfied": satisfied,
            }
        )
    return output
