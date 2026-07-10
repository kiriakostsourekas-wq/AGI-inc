from typing import Any, cast

import pytest
from trust_contracts import EffectClass, VerificationResult

from trust_runtime.browser import ActionReceipt, BrowserPage, Observation
from trust_runtime.effects import EffectDeriver
from trust_runtime.verification import BaselinePassThroughVerifier, VisibleTextVerifier


class TextPage:
    def __init__(self, text: object) -> None:
        self.text = text

    async def evaluate(self, _expression: str, _arg: Any = None) -> object:
        return self.text


def receipt(screenshot_hash: str = "b" * 64) -> ActionReceipt:
    return ActionReceipt(
        action_id="action-1",
        tool="ui.click",
        url_after="http://northstar.localhost:3001/",
        screenshot_hash_after=screenshot_hash,
    )


def observation(screenshot_hash: str = "a" * 64) -> Observation:
    return Observation(
        observation_id="observation-1",
        url="http://northstar.localhost:3001/",
        origin="http://northstar.localhost:3001",
        screenshot_png=b"png",
        screenshot_hash=screenshot_hash,
    )


@pytest.fixture
def booking_effect(contract, booking_action, booking_target, clock):
    return EffectDeriver().derive(
        action=booking_action,
        contract=contract,
        trusted_target=booking_target,
        derived_at=clock.now(),
    )


async def verify(verifier, page, booking_action, effect, *, before="a" * 64, after="b" * 64):
    return await verifier.verify_action(
        page=cast(BrowserPage, page),
        action=booking_action,
        effect=effect,
        receipt=receipt(after),
        observation=observation(before),
    )


@pytest.mark.asyncio
async def test_commit_confirmation_and_email_build_corroborated_predicate(
    booking_effect, booking_action
) -> None:
    verifier = VisibleTextVerifier()
    read_effect = booking_effect.model_copy(
        update={"effect_class": EffectClass.READ, "approval_required": False}
    )
    email = await verify(
        verifier,
        TextPage("Replacement confirmed — booking confirmed"),
        booking_action,
        read_effect,
    )
    assert email.result is VerificationResult.VERIFIED
    assert not email.verified_predicates

    confirmed = await verify(
        verifier,
        TextPage("Manage Trip confirmation: NS451 is confirmed"),
        booking_action,
        booking_effect,
    )
    assert confirmed.result is VerificationResult.VERIFIED
    assert confirmed.verified_predicates == frozenset({"replacement_booking_verified"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Outcome unknown", VerificationResult.OUTCOME_UNKNOWN),
        ("Approval stale", VerificationResult.CONSTRAINT_CHANGED),
        ("No definitive booking status", VerificationResult.OUTCOME_UNKNOWN),
    ],
)
async def test_commit_fail_closed_visible_outcomes(
    text, expected, booking_effect, booking_action
) -> None:
    outcome = await verify(VisibleTextVerifier(), TextPage(text), booking_action, booking_effect)
    assert outcome.result is expected
    assert outcome.evidence_ids == ("b" * 64,)


@pytest.mark.asyncio
async def test_reversible_and_read_verification_paths(booking_effect, booking_action) -> None:
    verifier = VisibleTextVerifier()
    reversible = booking_effect.model_copy(
        update={"effect_class": EffectClass.REVERSIBLE_MUTATION, "approval_required": False}
    )
    assert (
        await verify(
            verifier,
            TextPage("Travel block synchronized — confirmed"),
            booking_action,
            reversible,
        )
    ).result is VerificationResult.VERIFIED
    assert (
        await verify(verifier, TextPage("Calendar unchanged"), booking_action, reversible)
    ).result is VerificationResult.NOT_VERIFIED

    read = booking_effect.model_copy(
        update={"effect_class": EffectClass.READ, "approval_required": False}
    )
    assert (
        await verify(verifier, TextPage(42), booking_action, read)
    ).result is VerificationResult.VERIFIED
    assert (
        await verify(
            verifier,
            TextPage("same"),
            booking_action,
            read,
            before="c" * 64,
            after="c" * 64,
        )
    ).result is VerificationResult.NOT_VERIFIED


@pytest.mark.asyncio
async def test_unknown_goal_and_zero_effect_verification(booking_effect, booking_action) -> None:
    verifier = VisibleTextVerifier()
    unresolved = await verifier.verify_unknown_effect(
        page=cast(BrowserPage, TextPage("No definitive state")), effect=booking_effect
    )
    assert unresolved.result is VerificationResult.OUTCOME_UNKNOWN

    resolved = await verifier.verify_unknown_effect(
        page=cast(BrowserPage, TextPage("Manage Trip confirmation: booking is confirmed")),
        effect=booking_effect,
    )
    assert resolved.result is VerificationResult.VERIFIED

    goal_before_email = await verifier.verify_goal(
        page=cast(BrowserPage, TextPage("Travel block synchronized — confirmed"))
    )
    assert goal_before_email.result is VerificationResult.NOT_VERIFIED

    read = booking_effect.model_copy(
        update={"effect_class": EffectClass.READ, "approval_required": False}
    )
    await verify(
        verifier,
        TextPage("Replacement confirmed and booking confirmed"),
        booking_action,
        read,
    )
    goal = await verifier.verify_goal(
        page=cast(BrowserPage, TextPage("Travel block synchronized — confirmed"))
    )
    assert goal.result is VerificationResult.VERIFIED

    zero = await verifier.verify_zero_external_effects(
        page=cast(BrowserPage, TextPage("Inbox only"))
    )
    nonzero = await verifier.verify_zero_external_effects(
        page=cast(BrowserPage, TextPage("Manage Trip confirmation"))
    )
    assert zero.result is VerificationResult.VERIFIED
    assert nonzero.result is VerificationResult.NOT_VERIFIED


@pytest.mark.asyncio
async def test_disclosed_baseline_trusts_assertions_without_reading_page(
    booking_effect, booking_action
) -> None:
    verifier = BaselinePassThroughVerifier()
    page = cast(BrowserPage, TextPage("visible state explicitly says commit failed"))

    action = await verifier.verify_action(
        page=page,
        action=booking_action,
        effect=booking_effect,
        receipt=receipt(),
        observation=observation(),
    )
    goal = await verifier.verify_goal(page=page)
    zero = await verifier.verify_zero_external_effects(page=page)

    assert action.result is VerificationResult.VERIFIED
    assert action.verified_predicates == frozenset({"replacement_booking_verified"})
    assert goal.result is VerificationResult.VERIFIED
    assert zero.result is VerificationResult.NOT_VERIFIED
