"""Atomic approval consumption and synthetic Northstar booking commit."""

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from trust_contracts import (
    BookingCommitContext,
    jcs_canonicalize,
    normalize_origin,
    sha256_hex,
    uuid7,
)

from ..sandbox_context import sandbox_approval_context_hash
from .errors import (
    DuplicateReplacementError,
    GrantExpiredError,
    GrantInvalidError,
    GrantReplayError,
    GrantStaleError,
    RecordNotFoundError,
    SideEffectConflictError,
)
from .models import (
    ApprovalGrantRow,
    ApprovalRequestRow,
    EffectProposalRow,
    ReplacementBookingRow,
    SideEffectRow,
)

_HASH = re.compile(r"^[0-9a-f]{64}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True, slots=True)
class NorthstarCommitCommand:
    """Server-derived commit input; no field is accepted from model authority."""

    run_id: UUID
    grant_id: UUID
    approval_request_id: UUID
    effect_proposal_id: UUID
    idempotency_key: str
    capability_hash: str
    approved_context_hash: str
    contract_hash: str
    origin: str
    original_reservation_id: str
    traveler_id: str
    booking_reference: str
    semantic_context: dict[str, Any]
    total_additional_cost_minor: int
    currency: str
    request_hash: str
    response_hash: str | None = None
    _canonical_semantic_context: bytes = field(init=False, repr=False)

    def __post_init__(self) -> None:
        for label, value in (
            ("capability_hash", self.capability_hash),
            ("approved_context_hash", self.approved_context_hash),
            ("contract_hash", self.contract_hash),
            ("request_hash", self.request_hash),
        ):
            if _HASH.fullmatch(value) is None:
                raise ValueError(f"{label} must be a lowercase SHA-256 hex digest")
        if self.response_hash is not None and _HASH.fullmatch(self.response_hash) is None:
            raise ValueError("response_hash must be a lowercase SHA-256 hex digest")
        if not self.idempotency_key or len(self.idempotency_key) > 256:
            raise ValueError("idempotency_key must be present and bounded")
        if self.total_additional_cost_minor < 0:
            raise ValueError("booking cost must not be negative")
        if _CURRENCY.fullmatch(self.currency) is None:
            raise ValueError("currency must be a three-letter uppercase code")
        object.__setattr__(self, "origin", normalize_origin(self.origin))
        canonical_context = jcs_canonicalize(self.semantic_context)
        decoded_object: object = json.loads(canonical_context)
        if not isinstance(decoded_object, dict):
            raise ValueError("semantic_context must be a booking_commit object")
        decoded = cast(dict[str, Any], decoded_object)
        if decoded.get("kind") != "booking_commit":
            raise ValueError("semantic_context must be a booking_commit object")
        object.__setattr__(self, "_canonical_semantic_context", canonical_context)

    def semantic_context_payload(self) -> dict[str, Any]:
        """Return a fresh copy of the construction-time semantic snapshot."""

        return cast(dict[str, Any], json.loads(self._canonical_semantic_context))


@dataclass(frozen=True, slots=True)
class NorthstarCommitResult:
    booking_id: UUID
    booking_reference: str
    side_effect_id: UUID
    idempotent_replay: bool
    committed_at: datetime


class NorthstarGateway:
    """Validate and consume one grant in the same transaction as booking creation."""

    def __init__(
        self,
        *,
        sessions: async_sessionmaker[AsyncSession],
        approval_signing_key: bytes,
    ) -> None:
        if len(approval_signing_key) < 32:
            raise ValueError("approval signing key must contain at least 32 bytes")
        self._sessions = sessions
        self._signing_key = approval_signing_key

    async def commit_bound_grant(
        self,
        *,
        grant_id: UUID,
        current_context_hash: str,
    ) -> NorthstarCommitResult:
        """Construct the commit only from persisted authority and trusted sandbox observation."""

        if _HASH.fullmatch(current_context_hash) is None:
            raise GrantStaleError("sandbox context hash is malformed")
        async with self._sessions() as session:
            grant = await session.get(ApprovalGrantRow, grant_id)
            if grant is None:
                raise GrantInvalidError("approval grant does not exist")
            approval = await session.get(ApprovalRequestRow, grant.approval_request_id)
            effect = await session.get(EffectProposalRow, grant.effect_proposal_id)
            if approval is None or effect is None:
                raise RecordNotFoundError("approval request or effect proposal is missing")
            context = effect.semantic_context
            booking_context = BookingCommitContext.model_validate(context)
            expected_sandbox_hash = sandbox_approval_context_hash(
                run_id=str(grant.run_id),
                context=booking_context,
            )
            if not hmac.compare_digest(expected_sandbox_hash, current_context_hash):
                raise GrantStaleError("rendered itinerary changed after approval")
            payload = grant.capability_payload
            run_id = grant.run_id
            command = NorthstarCommitCommand(
                run_id=run_id,
                grant_id=grant.id,
                approval_request_id=approval.id,
                effect_proposal_id=effect.id,
                idempotency_key=grant.idempotency_key,
                capability_hash=grant.capability_hash,
                approved_context_hash=effect.approved_context_hash,
                contract_hash=effect.contract_hash,
                origin=effect.derived_origin,
                original_reservation_id=str(context.get("reservation_id", "")),
                traveler_id=str(context.get("traveler_id", "")),
                booking_reference=f"NB-{str(grant.id).replace('-', '')[:12].upper()}",
                semantic_context=context,
                total_additional_cost_minor=int(context.get("total_additional_cost_minor", -1)),
                currency=str(context.get("currency", "")),
                request_hash=sha256_hex(
                    {
                        "run_id": str(run_id),
                        "grant_id": str(grant.id),
                        "approved_context_hash": effect.approved_context_hash,
                    }
                ),
                response_hash=sha256_hex(
                    {
                        "status": "confirmed",
                        "flight_id": payload.get("flight_id"),
                    }
                ),
            )
        return await self.commit(command)

    async def commit(self, command: NorthstarCommitCommand) -> NorthstarCommitResult:
        pending_error: Exception | None = None
        result: NorthstarCommitResult | None = None
        try:
            async with self._sessions() as session, session.begin():
                grant = await session.scalar(
                    select(ApprovalGrantRow)
                    .where(ApprovalGrantRow.id == command.grant_id)
                    .with_for_update()
                )
                if grant is None:
                    raise GrantInvalidError("approval grant does not exist")
                approval = await session.scalar(
                    select(ApprovalRequestRow)
                    .where(ApprovalRequestRow.id == command.approval_request_id)
                    .with_for_update()
                )
                effect = await session.scalar(
                    select(EffectProposalRow)
                    .where(EffectProposalRow.id == command.effect_proposal_id)
                    .with_for_update()
                )
                if approval is None or effect is None:
                    raise RecordNotFoundError("approval request or effect proposal is missing")

                # Locking the grant before the ledger makes identical concurrent
                # requests serialize without blocking unrelated grants.
                existing = await self._existing_effect(session, command)
                now = await session.scalar(select(func.now()))
                if now is None:
                    raise RuntimeError("database security clock returned no timestamp")

                context = command.semantic_context_payload()
                signature_valid = self._valid_signature(grant)
                scope_valid = self._scope_matches(
                    grant=grant,
                    approval=approval,
                    effect=effect,
                    command=command,
                    context=context,
                )
                if not signature_valid:
                    if grant.status == "ACTIVE":
                        grant.status = "REVOKED"
                    pending_error = GrantInvalidError("approval capability signature is invalid")
                elif not scope_valid:
                    if grant.status == "ACTIVE":
                        grant.status = "REVOKED"
                    pending_error = GrantStaleError("approved semantic context changed")
                elif existing is not None:
                    if grant.status != "CONSUMED" or effect.status != "COMMITTED":
                        pending_error = GrantInvalidError(
                            "effect ledger and grant state are inconsistent"
                        )
                    else:
                        result = existing
                elif grant.status == "CONSUMED":
                    pending_error = GrantReplayError(
                        "consumed grant is missing its committed side-effect record"
                    )
                elif grant.status != "ACTIVE":
                    pending_error = GrantInvalidError(f"approval grant is {grant.status.lower()}")
                elif now >= grant.expires_at:
                    grant.status = "EXPIRED"
                    pending_error = GrantExpiredError("approval grant expired before commit")
                elif approval.status != "APPROVED":
                    pending_error = GrantInvalidError("approval request is not approved")
                elif effect.status != "AUTHORIZED":
                    pending_error = GrantInvalidError("effect proposal is not authorized")
                else:
                    booking_id = uuid7()
                    side_effect_id = uuid7()
                    booking = ReplacementBookingRow(
                        id=booking_id,
                        run_id=command.run_id,
                        effect_proposal_id=command.effect_proposal_id,
                        idempotency_key=command.idempotency_key,
                        original_reservation_id=command.original_reservation_id,
                        traveler_id=command.traveler_id,
                        booking_reference=command.booking_reference,
                        approved_context_hash=command.approved_context_hash,
                        contract_hash=command.contract_hash,
                        semantic_context=context,
                        total_additional_cost_minor=command.total_additional_cost_minor,
                        currency=command.currency,
                        status="confirmed",
                        committed_at=now,
                    )
                    side_effect = SideEffectRow(
                        id=side_effect_id,
                        run_id=command.run_id,
                        effect_proposal_id=command.effect_proposal_id,
                        idempotency_key=command.idempotency_key,
                        effect_type="FINANCIAL_OR_CONTRACTUAL_COMMIT",
                        external_resource_id=command.booking_reference,
                        status="COMMITTED",
                        request_hash=command.request_hash,
                        response_hash=command.response_hash,
                        committed_at=now,
                    )
                    session.add_all((booking, side_effect))
                    grant.status = "CONSUMED"
                    grant.used_at = now
                    effect.status = "COMMITTED"
                    await session.flush()
                    result = NorthstarCommitResult(
                        booking_id=booking_id,
                        booking_reference=command.booking_reference,
                        side_effect_id=side_effect_id,
                        idempotent_replay=False,
                        committed_at=now,
                    )
        except IntegrityError as error:
            constraint = getattr(getattr(error.orig, "diag", None), "constraint_name", None)
            if constraint == "uq_replacement_bookings_original_confirmed":
                raise DuplicateReplacementError(
                    "a confirmed replacement already exists for the original reservation"
                ) from error
            if constraint in {
                "uq_side_effects_idempotency_key",
                "uq_replacement_bookings_idempotency_key",
            }:
                raise SideEffectConflictError(
                    "the idempotency key committed concurrently"
                ) from error
            raise

        if pending_error is not None:
            raise pending_error
        if result is None:
            raise RuntimeError("booking transaction ended without a result")
        return result

    async def mark_verified(
        self, *, run_id: UUID, booking_id: UUID, verified_at: datetime
    ) -> NorthstarCommitResult:
        """Persist independent verification in a separate, short transaction."""

        async with self._sessions() as session, session.begin():
            booking = await session.scalar(
                select(ReplacementBookingRow)
                .where(
                    ReplacementBookingRow.id == booking_id,
                    ReplacementBookingRow.run_id == run_id,
                )
                .with_for_update()
            )
            if booking is None:
                raise RecordNotFoundError("replacement booking does not exist")
            side_effect = await session.scalar(
                select(SideEffectRow)
                .where(SideEffectRow.idempotency_key == booking.idempotency_key)
                .with_for_update()
            )
            if side_effect is None:
                raise RecordNotFoundError("booking side-effect ledger entry does not exist")
            if booking.status != "confirmed" or side_effect.status not in {
                "COMMITTED",
                "VERIFIED",
            }:
                raise GrantInvalidError("only a committed confirmed booking can be verified")
            already_verified = booking.verified_at is not None
            booking.verified_at = booking.verified_at or verified_at
            side_effect.verified_at = side_effect.verified_at or verified_at
            side_effect.status = "VERIFIED"
            await session.flush()
            return NorthstarCommitResult(
                booking_id=booking.id,
                booking_reference=booking.booking_reference,
                side_effect_id=side_effect.id,
                idempotent_replay=already_verified,
                committed_at=booking.committed_at,
            )

    async def _existing_effect(
        self, session: AsyncSession, command: NorthstarCommitCommand
    ) -> NorthstarCommitResult | None:
        existing = await session.scalar(
            select(SideEffectRow)
            .where(SideEffectRow.idempotency_key == command.idempotency_key)
            .with_for_update()
        )
        if existing is None:
            return None
        if (
            existing.run_id != command.run_id
            or existing.effect_proposal_id != command.effect_proposal_id
            or existing.request_hash != command.request_hash
        ):
            raise SideEffectConflictError(
                "idempotency key was previously used with a different request body"
            )
        booking = await session.scalar(
            select(ReplacementBookingRow).where(
                ReplacementBookingRow.idempotency_key == command.idempotency_key
            )
        )
        if booking is None or existing.committed_at is None:
            raise SideEffectConflictError("idempotent effect is missing its booking receipt")
        context = command.semantic_context_payload()
        if (
            booking.run_id != command.run_id
            or booking.effect_proposal_id != command.effect_proposal_id
            or booking.original_reservation_id != command.original_reservation_id
            or booking.traveler_id != command.traveler_id
            or booking.approved_context_hash != command.approved_context_hash
            or booking.contract_hash != command.contract_hash
            or booking.semantic_context != context
            or booking.total_additional_cost_minor != command.total_additional_cost_minor
            or booking.currency != command.currency
        ):
            raise SideEffectConflictError("idempotent booking receipt has a different scope")
        return NorthstarCommitResult(
            booking_id=booking.id,
            booking_reference=booking.booking_reference,
            side_effect_id=existing.id,
            idempotent_replay=True,
            committed_at=existing.committed_at,
        )

    def _valid_signature(self, grant: ApprovalGrantRow) -> bool:
        canonical = jcs_canonicalize(grant.capability_payload)
        expected_signature = hmac.new(self._signing_key, canonical, hashlib.sha256).hexdigest()
        digest = hashlib.sha256()
        digest.update(canonical)
        digest.update(b".")
        digest.update(grant.signature.encode("ascii"))
        expected_hash = digest.hexdigest()
        return hmac.compare_digest(grant.signature, expected_signature) and hmac.compare_digest(
            grant.capability_hash, expected_hash
        )

    @classmethod
    def _scope_matches(
        cls,
        *,
        grant: ApprovalGrantRow,
        approval: ApprovalRequestRow,
        effect: EffectProposalRow,
        command: NorthstarCommitCommand,
        context: dict[str, Any],
    ) -> bool:
        expected_context_hash = sha256_hex(
            {
                "contract_hash": command.contract_hash,
                "origin": command.origin,
                "effect_class": "FINANCIAL_OR_CONTRACTUAL_COMMIT",
                "target_kind": "BOOKING_CONFIRM",
                "context": context,
            }
        )
        return (
            approval.status == "APPROVED"
            and approval.run_id == command.run_id
            and approval.effect_proposal_id == command.effect_proposal_id
            and approval.approved_context_hash == command.approved_context_hash
            and effect.run_id == command.run_id
            and effect.derived_origin == command.origin
            and effect.derived_effect_class == "FINANCIAL_OR_CONTRACTUAL_COMMIT"
            and effect.trusted_target_kind == "BOOKING_CONFIRM"
            and effect.contract_hash == command.contract_hash
            and effect.semantic_context == context
            and effect.approved_context_hash == command.approved_context_hash
            and effect.idempotency_key == command.idempotency_key
            and context.get("reservation_id") == command.original_reservation_id
            and context.get("traveler_id") == command.traveler_id
            and context.get("total_additional_cost_minor") == command.total_additional_cost_minor
            and context.get("currency") == command.currency
            and hmac.compare_digest(expected_context_hash, command.approved_context_hash)
            and cls._grant_matches(grant, command, context)
        )

    @staticmethod
    def _grant_matches(
        grant: ApprovalGrantRow,
        command: NorthstarCommitCommand,
        context: dict[str, Any],
    ) -> bool:
        payload = grant.capability_payload
        context_fields = (
            "traveler_id",
            "reservation_id",
            "offer_version",
            "marketing_carrier",
            "operating_carrier",
            "flight_id",
            "origin_airport",
            "destination_airport",
            "departure",
            "arrival",
            "stop_count",
            "cabin",
            "fare_class",
            "seat_type",
            "base_fare_minor",
            "taxes_and_fees_minor",
            "total_additional_cost_minor",
            "currency",
        )
        return (
            grant.run_id == command.run_id
            and grant.approval_request_id == command.approval_request_id
            and grant.effect_proposal_id == command.effect_proposal_id
            and grant.idempotency_key == command.idempotency_key
            and hmac.compare_digest(grant.capability_hash, command.capability_hash)
            and hmac.compare_digest(grant.context_hash, command.approved_context_hash)
            and payload.get("run_id") == str(command.run_id)
            and payload.get("approval_request_id") == str(command.approval_request_id)
            and payload.get("effect_proposal_id") == str(command.effect_proposal_id)
            and payload.get("idempotency_key") == command.idempotency_key
            and payload.get("origin") == command.origin
            and payload.get("effect") == "FINANCIAL_OR_CONTRACTUAL_COMMIT"
            and payload.get("approved_context_hash") == command.approved_context_hash
            and payload.get("contract_hash") == command.contract_hash
            and all(payload.get(name) == context.get(name) for name in context_fields)
        )
