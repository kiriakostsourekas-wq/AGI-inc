from datetime import timedelta

import pytest
from trust_contracts import (
    ApprovalGrantStatus,
    ApprovalRequestStatus,
    CalendarMutationContext,
    EffectClass,
    PolicyVerdict,
    ReadEffectContext,
    ToolName,
    TrustedTargetKind,
)

from trust_runtime.approvals import ApprovalAuthority
from trust_runtime.effects import EffectDerivationError, EffectDeriver, TrustedTargetDescriptor
from trust_runtime.errors import (
    ApprovalError,
    ApprovalExpiredError,
    ApprovalReplayError,
    ApprovalStaleError,
    PolicyDeniedError,
)
from trust_runtime.policy import DeterministicPolicyEngine, PolicyContext


def test_actor_cannot_choose_effect_class(booking_action) -> None:
    assert "effect_class" not in type(booking_action).model_fields
    assert "idempotency_key" not in type(booking_action).model_fields


def test_runtime_derives_commit_and_policy_requires_approval(
    contract, booking_action, booking_target, clock
) -> None:
    effect = EffectDeriver().derive(
        action=booking_action,
        contract=contract,
        trusted_target=booking_target,
        derived_at=clock.now(),
    )
    assert effect.effect_class is EffectClass.FINANCIAL_OR_CONTRACTUAL_COMMIT
    assert effect.approval_required
    assert effect.idempotency_key is not None
    decision = DeterministicPolicyEngine(clock).evaluate(effect, PolicyContext(contract))
    assert decision.verdict is PolicyVerdict.REQUIRE_APPROVAL


def test_booking_that_violates_hard_cost_constraint_is_denied(
    contract, booking_action, booking_target, clock
) -> None:
    expensive_context = booking_target.context.model_copy(
        update={"total_additional_cost_minor": 50_000, "base_fare_minor": 46_100}
    )
    expensive_target = booking_target.model_copy(update={"context": expensive_context})
    effect = EffectDeriver().derive(
        action=booking_action,
        contract=contract,
        trusted_target=expensive_target,
        derived_at=clock.now(),
    )
    decision = DeterministicPolicyEngine(clock).evaluate(effect, PolicyContext(contract))
    assert decision.verdict is PolicyVerdict.DENY
    assert decision.rule_id == "contract/hard-constraint-failed"


def test_unallowlisted_trusted_target_origin_fails_closed(
    contract, booking_action, booking_target, clock
) -> None:
    wrong_target = booking_target.model_copy(update={"origin": "https://northstar.example"})
    with pytest.raises(EffectDerivationError, match="origin"):
        EffectDeriver().derive(
            action=booking_action,
            contract=contract,
            trusted_target=wrong_target,
            derived_at=clock.now(),
        )


def test_approval_is_exact_single_use(contract, booking_action, booking_target, clock) -> None:
    effect = EffectDeriver().derive(
        action=booking_action,
        contract=contract,
        trusted_target=booking_target,
        derived_at=clock.now(),
    )
    decision = DeterministicPolicyEngine(clock).evaluate(effect, PolicyContext(contract))
    authority = ApprovalAuthority(
        signing_key=b"x" * 32,
        clock=clock,
        default_ttl_seconds=180,
    )
    request = authority.request(effect=effect, summary="Book NS451 for exactly USD 389.00")
    grant = authority.approve(request_id=request.request_id, contract_hash=contract.content_hash)
    authorized = authority.consume_and_authorize(
        grant=grant,
        effect=effect,
        policy_decision=decision,
        contract_hash=contract.content_hash,
    )
    assert authorized.action.action_id == booking_action.action_id
    assert authority.get_grant(grant.payload.grant_id).status is ApprovalGrantStatus.CONSUMED
    with pytest.raises(ApprovalReplayError):
        authority.consume_and_authorize(
            grant=grant,
            effect=effect,
            policy_decision=decision,
            contract_hash=contract.content_hash,
        )


def test_durable_approval_authorizes_before_atomic_consumption(
    contract, booking_action, booking_target, clock
) -> None:
    effect = EffectDeriver().derive(
        action=booking_action,
        contract=contract,
        trusted_target=booking_target,
        derived_at=clock.now(),
    )
    decision = DeterministicPolicyEngine(clock).evaluate(effect, PolicyContext(contract))
    authority = ApprovalAuthority(
        signing_key=b"d" * 32,
        clock=clock,
        default_ttl_seconds=180,
    )
    request = authority.request(effect=effect, summary="Book exact durable itinerary")
    grant = authority.approve(request_id=request.request_id, contract_hash=contract.content_hash)

    authorized = authority.authorize_active_grant(
        grant=grant,
        effect=effect,
        policy_decision=decision,
        contract_hash=contract.content_hash,
    )
    assert authorized.grant_id == grant.payload.grant_id
    assert authority.get_grant(grant.payload.grant_id).status is ApprovalGrantStatus.ACTIVE

    consumed_at = clock.now()
    authority.mark_consumed(grant_id=grant.payload.grant_id, consumed_at=consumed_at)
    authority.mark_consumed(grant_id=grant.payload.grant_id, consumed_at=consumed_at)
    assert authority.get_grant(grant.payload.grant_id).status is ApprovalGrantStatus.CONSUMED
    with pytest.raises(ApprovalReplayError):
        authority.authorize_active_grant(
            grant=grant,
            effect=effect,
            policy_decision=decision,
            contract_hash=contract.content_hash,
        )


def test_approval_request_expires(contract, booking_action, booking_target, clock) -> None:
    effect = EffectDeriver().derive(
        action=booking_action,
        contract=contract,
        trusted_target=booking_target,
        derived_at=clock.now(),
    )
    authority = ApprovalAuthority(
        signing_key=b"y" * 32,
        clock=clock,
        default_ttl_seconds=15,
    )
    request = authority.request(effect=effect, summary="Book exact itinerary")
    clock.advance(timedelta(seconds=15))
    with pytest.raises(ApprovalExpiredError):
        authority.approve(request_id=request.request_id, contract_hash=contract.content_hash)


def test_calendar_policy_requires_verified_booking(
    contract, booking_action, booking_target, clock
) -> None:
    calendar_action = booking_action.model_copy(
        update={
            "tool": ToolName.CLICK,
            "target_description": "Save travel block",
        }
    )
    target = TrustedTargetDescriptor(
        target_kind=TrustedTargetKind.CALENDAR_SAVE,
        origin="http://dayplan.localhost:3001",
        trusted_target_id="dayplan.save",
        context=CalendarMutationContext(
            calendar_event_id="travel-block",
            verified_booking_id="booking-1",
            starts_at=booking_target.context.departure,
            ends_at=booking_target.context.arrival,
        ),
    )
    effect = EffectDeriver().derive(
        action=calendar_action,
        contract=contract,
        trusted_target=target,
        derived_at=clock.now(),
    )
    policy = DeterministicPolicyEngine(clock)
    denied = policy.evaluate(effect, PolicyContext(contract))
    allowed = policy.evaluate(
        effect,
        PolicyContext(contract, verified_predicates=frozenset({"replacement_booking_verified"})),
    )
    assert denied.verdict is PolicyVerdict.DENY
    assert allowed.verdict is PolicyVerdict.ALLOW


def test_policy_covers_hash_origin_and_nonbooking_effect_classes(
    contract, booking_action, booking_target, clock
) -> None:
    effect = EffectDeriver().derive(
        action=booking_action,
        contract=contract,
        trusted_target=booking_target,
        derived_at=clock.now(),
    )
    policy = DeterministicPolicyEngine(clock)
    assert (
        policy.evaluate(
            effect.model_copy(update={"contract_hash": "0" * 64}),
            PolicyContext(contract),
        ).rule_id
        == "contract/hash-mismatch"
    )
    assert (
        policy.evaluate(
            effect.model_copy(update={"origin": "https://outside.example"}),
            PolicyContext(contract),
        ).rule_id
        == "origin/not-allowlisted"
    )
    external = effect.model_copy(update={"effect_class": EffectClass.EXTERNAL_COMMUNICATION})
    assert policy.evaluate(external, PolicyContext(contract)).verdict is PolicyVerdict.DENY

    credential = effect.model_copy(update={"effect_class": EffectClass.CREDENTIAL_OR_IDENTITY})
    needs_grant = policy.evaluate(credential, PolicyContext(contract))
    bound = policy.evaluate(
        credential,
        PolicyContext(
            contract,
            approved_context_hashes=frozenset({credential.approved_context_hash}),
        ),
    )
    assert needs_grant.verdict is PolicyVerdict.REQUIRE_APPROVAL
    assert bound.verdict is PolicyVerdict.ALLOW

    for effect_class in (EffectClass.READ, EffectClass.DRAFT):
        harmless = effect.model_copy(update={"effect_class": effect_class})
        assert policy.evaluate(harmless, PolicyContext(contract)).verdict is PolicyVerdict.ALLOW


def test_approval_request_lifecycle_and_invalid_inputs(
    contract, booking_action, booking_target, clock
) -> None:
    with pytest.raises(ValueError, match="32 bytes"):
        ApprovalAuthority(signing_key=b"short", clock=clock, default_ttl_seconds=180)
    with pytest.raises(ValueError, match="TTL"):
        ApprovalAuthority(signing_key=b"x" * 32, clock=clock, default_ttl_seconds=14)

    effect = EffectDeriver().derive(
        action=booking_action,
        contract=contract,
        trusted_target=booking_target,
        derived_at=clock.now(),
    )
    authority = ApprovalAuthority(
        signing_key=b"lifecycle-approval-authority-key!",
        clock=clock,
        default_ttl_seconds=180,
    )
    with pytest.raises(ApprovalError, match="does not require"):
        authority.request(
            effect=effect.model_copy(update={"approval_required": False}), summary="invalid"
        )
    request = authority.request(effect=effect, summary="exact booking")
    assert authority.pending_for_run(effect.action.run_id) == request
    rejected = authority.reject(request.request_id)
    assert rejected.status is ApprovalRequestStatus.REJECTED
    assert authority.expire(request.request_id) == rejected
    with pytest.raises(ApprovalError, match="already rejected"):
        authority.reject(request.request_id)
    with pytest.raises(ApprovalError, match="does not exist"):
        authority.get_request(booking_action.action_id)
    with pytest.raises(ApprovalError, match="does not exist"):
        authority.get_grant(booking_action.action_id)
    with pytest.raises(ApprovalError, match="no issued grant"):
        authority.grant_for_request(request.request_id)


def test_approval_rejects_stale_and_malformed_semantics(
    contract, booking_action, booking_target, clock
) -> None:
    effect = EffectDeriver().derive(
        action=booking_action,
        contract=contract,
        trusted_target=booking_target,
        derived_at=clock.now(),
    )
    authority = ApprovalAuthority(
        signing_key=b"semantic-approval-authority-key!!",
        clock=clock,
        default_ttl_seconds=180,
    )
    stale = authority.request(effect=effect, summary="stale")
    with pytest.raises(ApprovalStaleError, match="contract"):
        authority.approve(request_id=stale.request_id, contract_hash="0" * 64)

    no_origin_effect = effect.model_copy(update={"origin": None})
    with pytest.raises(ValueError, match="semantic effect context"):
        authority.request(effect=no_origin_effect, summary="missing origin")

    wrong_context_effect = effect.model_copy(
        update={
            "context": CalendarMutationContext(
                calendar_event_id="travel-block",
                verified_booking_id="booking-1",
                starts_at=booking_target.context.departure,
                ends_at=booking_target.context.arrival,
            )
        }
    )
    with pytest.raises(ValueError, match="semantic effect context"):
        authority.request(effect=wrong_context_effect, summary="wrong context")


def test_grant_validation_and_authorization_fail_closed(
    contract, booking_action, booking_target, clock
) -> None:
    effect = EffectDeriver().derive(
        action=booking_action,
        contract=contract,
        trusted_target=booking_target,
        derived_at=clock.now(),
    )
    policy = DeterministicPolicyEngine(clock)
    decision = policy.evaluate(effect, PolicyContext(contract))
    authority = ApprovalAuthority(
        signing_key=b"validation-approval-authority-key!",
        clock=clock,
        default_ttl_seconds=180,
    )
    request = authority.request(effect=effect, summary="validate")
    grant = authority.approve(request_id=request.request_id, contract_hash=contract.content_hash)

    foreign = ApprovalAuthority(
        signing_key=b"foreign-approval-authority-key!!!",
        clock=clock,
        default_ttl_seconds=180,
    )
    with pytest.raises(ApprovalError, match="not issued"):
        foreign.validate(grant=grant, effect=effect, contract_hash=contract.content_hash)
    with pytest.raises(ApprovalError, match="does not match"):
        authority.validate(
            grant=grant.model_copy(update={"signature": "0" * 64}),
            effect=effect,
            contract_hash=contract.content_hash,
        )
    with pytest.raises(ApprovalStaleError, match="semantic context"):
        authority.validate(
            grant=grant,
            effect=effect.model_copy(update={"approved_context_hash": "0" * 64}),
            contract_hash=contract.content_hash,
        )

    allow_decision = decision.model_copy(update={"verdict": PolicyVerdict.ALLOW})
    with pytest.raises(PolicyDeniedError, match="REQUIRE_APPROVAL"):
        authority.consume_and_authorize(
            grant=grant,
            effect=effect,
            policy_decision=allow_decision,
            contract_hash=contract.content_hash,
        )
    wrong_effect_decision = decision.model_copy(update={"effect_id": booking_action.action_id})
    with pytest.raises(ApprovalStaleError, match="different effect"):
        authority.consume_and_authorize(
            grant=grant,
            effect=effect,
            policy_decision=wrong_effect_decision,
            contract_hash=contract.content_hash,
        )
    with pytest.raises(PolicyDeniedError, match="ALLOW"):
        authority.authorize_allowed(effect=effect, policy_decision=decision)

    read = EffectDeriver().derive(
        action=booking_action,
        contract=contract,
        trusted_target=TrustedTargetDescriptor(
            target_kind=TrustedTargetKind.READ_ONLY_CONTROL,
            origin="http://northstar.localhost:3001",
            trusted_target_id="northstar.read-only",
            context=ReadEffectContext(resource_type="page", resource_id="manage-trip"),
        ),
        derived_at=clock.now(),
    )
    read_decision = policy.evaluate(read, PolicyContext(contract))
    authorized = authority.authorize_allowed(effect=read, policy_decision=read_decision)
    assert authorized.effect.effect_id == read.effect_id
    with pytest.raises(PolicyDeniedError, match="different effect"):
        authority.authorize_allowed(
            effect=read,
            policy_decision=read_decision.model_copy(
                update={"effect_id": booking_action.action_id}
            ),
        )
