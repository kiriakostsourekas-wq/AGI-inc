"""In-memory approval authority modeling the durable gateway contract."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from threading import RLock
from uuid import UUID

from trust_contracts import (
    ApprovalGrant,
    ApprovalGrantPayload,
    ApprovalGrantStatus,
    ApprovalRequest,
    ApprovalRequestStatus,
    AuthorizedAction,
    BookingCommitContext,
    EffectProposal,
    PolicyDecision,
    PolicyVerdict,
    SecurityClock,
    jcs_canonicalize,
)

from .errors import (
    ApprovalError,
    ApprovalExpiredError,
    ApprovalReplayError,
    ApprovalStaleError,
    PolicyDeniedError,
)


class ApprovalAuthority:
    """Issue and consume exact-context capabilities.

    This implementation is intentionally in-memory. The persistence layer must
    replace ``consume`` with one PostgreSQL transaction that marks the grant used
    and creates the external booking. The types and verification behavior remain
    the same.
    """

    def __init__(
        self, *, signing_key: bytes, clock: SecurityClock, default_ttl_seconds: int
    ) -> None:
        if len(signing_key) < 32:
            raise ValueError("approval signing key must contain at least 32 bytes")
        if not 15 <= default_ttl_seconds <= 900:
            raise ValueError("approval TTL must be between 15 and 900 seconds")
        self._key = signing_key
        self._clock = clock
        self._default_ttl_seconds = default_ttl_seconds
        self._requests: dict[UUID, ApprovalRequest] = {}
        self._grants: dict[UUID, ApprovalGrant] = {}
        self._lock = RLock()

    def request(self, *, effect: EffectProposal, summary: str) -> ApprovalRequest:
        if not effect.approval_required or effect.idempotency_key is None:
            raise ApprovalError("effect does not require an approval capability")
        now = self._clock.now()
        request = ApprovalRequest(
            run_id=effect.action.run_id,
            action_id=effect.action.action_id,
            effect=effect,
            summary=summary,
            created_at=now,
            expires_at=now + timedelta(seconds=self._default_ttl_seconds),
        )
        with self._lock:
            self._requests[request.request_id] = request
        return request

    def reject(self, request_id: UUID) -> ApprovalRequest:
        with self._lock:
            request = self._current_request(request_id)
            self._ensure_pending_and_fresh(request)
            rejected = request.model_copy(update={"status": ApprovalRequestStatus.REJECTED})
            self._requests[request_id] = rejected
            return rejected

    def expire(self, request_id: UUID) -> ApprovalRequest:
        with self._lock:
            request = self._current_request(request_id)
            if request.status is not ApprovalRequestStatus.PENDING:
                return request
            expired = request.model_copy(update={"status": ApprovalRequestStatus.EXPIRED})
            self._requests[request_id] = expired
            return expired

    def approve(self, *, request_id: UUID, contract_hash: str) -> ApprovalGrant:
        with self._lock:
            request = self._current_request(request_id)
            self._ensure_pending_and_fresh(request)
            effect = request.effect
            if effect.contract_hash != contract_hash:
                raise ApprovalStaleError("task contract changed before approval")
            if effect.origin is None or effect.idempotency_key is None:
                raise ApprovalError("approved effect lacks a commit origin or idempotency key")
            if not isinstance(effect.context, BookingCommitContext):
                raise ApprovalError("MVP approvals are limited to booking commit contexts")

            now = self._clock.now()
            context = effect.context
            payload = ApprovalGrantPayload(
                approval_request_id=request.request_id,
                run_id=request.run_id,
                effect_proposal_id=effect.effect_id,
                idempotency_key=effect.idempotency_key,
                origin=effect.origin,
                traveler_id=context.traveler_id,
                reservation_id=context.reservation_id,
                offer_version=context.offer_version,
                marketing_carrier=context.marketing_carrier,
                operating_carrier=context.operating_carrier,
                flight_id=context.flight_id,
                origin_airport=context.origin_airport,
                destination_airport=context.destination_airport,
                departure=context.departure,
                arrival=context.arrival,
                stop_count=context.stop_count,
                cabin=context.cabin,
                fare_class=context.fare_class,
                seat_type=context.seat_type,
                base_fare_minor=context.base_fare_minor,
                taxes_and_fees_minor=context.taxes_and_fees_minor,
                total_additional_cost_minor=context.total_additional_cost_minor,
                currency=context.currency,
                approved_context_hash=effect.approved_context_hash,
                contract_hash=contract_hash,
                observation_hash_at_proposal=effect.action.observation_hash,
                issued_at=now,
                expires_at=request.expires_at,
                nonce=secrets.token_hex(32),
            )
            signature = self._sign(payload)
            grant = ApprovalGrant(
                payload=payload,
                signature=signature,
                capability_hash=self._capability_hash(payload, signature),
            )
            approved = request.model_copy(update={"status": ApprovalRequestStatus.APPROVED})
            self._requests[request_id] = approved
            self._grants[payload.grant_id] = grant
            return grant

    def validate(
        self,
        *,
        grant: ApprovalGrant,
        effect: EffectProposal,
        contract_hash: str,
    ) -> None:
        with self._lock:
            stored = self._grants.get(grant.payload.grant_id)
            if stored is None:
                raise ApprovalError("grant was not issued by this authority")
            if stored.status is ApprovalGrantStatus.CONSUMED:
                raise ApprovalReplayError("grant has already been consumed")
            if stored.status is not ApprovalGrantStatus.ACTIVE:
                raise ApprovalError(f"grant is not active: {stored.status.value}")
            if self._clock.now() >= stored.payload.expires_at:
                expired = stored.model_copy(update={"status": ApprovalGrantStatus.EXPIRED})
                self._grants[stored.payload.grant_id] = expired
                raise ApprovalExpiredError("grant expired before execution")
            if not hmac.compare_digest(stored.signature, self._sign(stored.payload)):
                raise ApprovalError("grant signature is invalid")
            expected_capability_hash = self._capability_hash(stored.payload, stored.signature)
            if not hmac.compare_digest(stored.capability_hash, expected_capability_hash):
                raise ApprovalError("capability hash is invalid")
            if grant != stored:
                raise ApprovalError("supplied grant does not match the authority record")

            payload = stored.payload
            stale = (
                payload.run_id != effect.action.run_id
                or payload.effect_proposal_id != effect.effect_id
                or payload.idempotency_key != effect.idempotency_key
                or payload.origin != effect.origin
                or payload.effect != effect.effect_class
                or payload.approved_context_hash != effect.approved_context_hash
                or payload.contract_hash != contract_hash
                or payload.observation_hash_at_proposal != effect.action.observation_hash
                or not self._payload_matches_context(payload, effect.context)
            )
            if stale:
                raise ApprovalStaleError("effect no longer matches the approved semantic context")

    def consume_and_authorize(
        self,
        *,
        grant: ApprovalGrant,
        effect: EffectProposal,
        policy_decision: PolicyDecision,
        contract_hash: str,
    ) -> AuthorizedAction:
        """Consume a grant and produce the exact executor envelope.

        Durable callers must make this operation atomic with the external commit.
        """

        if policy_decision.verdict is not PolicyVerdict.REQUIRE_APPROVAL:
            raise PolicyDeniedError("grant consumption requires REQUIRE_APPROVAL policy decision")
        if policy_decision.effect_id != effect.effect_id:
            raise ApprovalStaleError("policy decision belongs to a different effect")
        with self._lock:
            self.validate(grant=grant, effect=effect, contract_hash=contract_hash)
            now = self._clock.now()
            consumed = grant.model_copy(
                update={"status": ApprovalGrantStatus.CONSUMED, "consumed_at": now}
            )
            self._grants[grant.payload.grant_id] = consumed
            return AuthorizedAction(
                action=effect.action,
                effect=effect,
                policy_decision=policy_decision,
                grant_id=grant.payload.grant_id,
                authorized_at=now,
            )

    def authorize_active_grant(
        self,
        *,
        grant: ApprovalGrant,
        effect: EffectProposal,
        policy_decision: PolicyDecision,
        contract_hash: str,
    ) -> AuthorizedAction:
        """Authorize the UI click while leaving durable consumption to the atomic gateway."""

        if policy_decision.verdict is not PolicyVerdict.REQUIRE_APPROVAL:
            raise PolicyDeniedError("active grant authorization requires REQUIRE_APPROVAL")
        if policy_decision.effect_id != effect.effect_id:
            raise ApprovalStaleError("policy decision belongs to a different effect")
        self.validate(grant=grant, effect=effect, contract_hash=contract_hash)
        return AuthorizedAction(
            action=effect.action,
            effect=effect,
            policy_decision=policy_decision,
            grant_id=grant.payload.grant_id,
            authorized_at=self._clock.now(),
        )

    def mark_consumed(self, *, grant_id: UUID, consumed_at: datetime) -> None:
        """Mirror an atomic durable gateway result into the process-local authority cache."""

        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None:
                raise ApprovalError("grant was not issued by this authority")
            if grant.status is ApprovalGrantStatus.CONSUMED:
                return
            if grant.status is not ApprovalGrantStatus.ACTIVE:
                raise ApprovalError(f"grant is not active: {grant.status.value}")
            self._grants[grant_id] = grant.model_copy(
                update={"status": ApprovalGrantStatus.CONSUMED, "consumed_at": consumed_at}
            )

    def authorize_allowed(
        self, *, effect: EffectProposal, policy_decision: PolicyDecision
    ) -> AuthorizedAction:
        if policy_decision.verdict is not PolicyVerdict.ALLOW:
            raise PolicyDeniedError("only ALLOW decisions can execute without a grant")
        if policy_decision.effect_id != effect.effect_id:
            raise PolicyDeniedError("policy decision belongs to a different effect")
        return AuthorizedAction(
            action=effect.action,
            effect=effect,
            policy_decision=policy_decision,
            authorized_at=self._clock.now(),
        )

    def get_request(self, request_id: UUID) -> ApprovalRequest:
        with self._lock:
            return self._current_request(request_id)

    def pending_for_run(self, run_id: UUID) -> ApprovalRequest | None:
        with self._lock:
            pending = [
                request
                for request in self._requests.values()
                if request.run_id == run_id and request.status is ApprovalRequestStatus.PENDING
            ]
            return max(pending, key=lambda request: request.created_at, default=None)

    def get_grant(self, grant_id: UUID) -> ApprovalGrant:
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None:
                raise ApprovalError("approval grant does not exist")
            return grant

    def grant_for_request(self, request_id: UUID) -> ApprovalGrant:
        with self._lock:
            matches = [
                grant
                for grant in self._grants.values()
                if grant.payload.approval_request_id == request_id
            ]
            if not matches:
                raise ApprovalError("approval request has no issued grant")
            return max(matches, key=lambda grant: grant.payload.issued_at)

    def _current_request(self, request_id: UUID) -> ApprovalRequest:
        request = self._requests.get(request_id)
        if request is None:
            raise ApprovalError("approval request does not exist")
        return request

    def _ensure_pending_and_fresh(self, request: ApprovalRequest) -> None:
        if request.status is not ApprovalRequestStatus.PENDING:
            raise ApprovalError(f"approval request is already {request.status.value.lower()}")
        if self._clock.now() >= request.expires_at:
            expired = request.model_copy(update={"status": ApprovalRequestStatus.EXPIRED})
            self._requests[request.request_id] = expired
            raise ApprovalExpiredError("approval request expired")

    def _sign(self, payload: ApprovalGrantPayload) -> str:
        return hmac.new(self._key, jcs_canonicalize(payload), hashlib.sha256).hexdigest()

    @staticmethod
    def _capability_hash(payload: ApprovalGrantPayload, signature: str) -> str:
        digest = hashlib.sha256()
        digest.update(jcs_canonicalize(payload))
        digest.update(b".")
        digest.update(signature.encode("ascii"))
        return digest.hexdigest()

    @staticmethod
    def _payload_matches_context(payload: ApprovalGrantPayload, context: object) -> bool:
        if not isinstance(context, BookingCommitContext):
            return False
        return (
            payload.traveler_id == context.traveler_id
            and payload.reservation_id == context.reservation_id
            and payload.offer_version == context.offer_version
            and payload.marketing_carrier == context.marketing_carrier
            and payload.operating_carrier == context.operating_carrier
            and payload.flight_id == context.flight_id
            and payload.origin_airport == context.origin_airport
            and payload.destination_airport == context.destination_airport
            and payload.departure == context.departure
            and payload.arrival == context.arrival
            and payload.stop_count == context.stop_count
            and payload.cabin == context.cabin
            and payload.fare_class == context.fare_class
            and payload.seat_type == context.seat_type
            and payload.base_fare_minor == context.base_fare_minor
            and payload.taxes_and_fees_minor == context.taxes_and_fees_minor
            and payload.total_additional_cost_minor == context.total_additional_cost_minor
            and payload.currency == context.currency
        )
