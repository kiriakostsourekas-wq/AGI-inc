"""Versioned public and sealed data contracts.

``TaskContract`` and ``ActionProposal`` are actor-visible. ``RunManifest``,
``EffectProposal``, and approval grants are runtime-only. Keeping these as separate
types prevents an accidental ``model_dump`` of oracle or fault metadata into model
context.
"""

import re
from datetime import datetime
from decimal import Decimal
from hmac import compare_digest
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_serializer,
    field_validator,
    model_validator,
)

from .canonical import sha256_hex
from .enums import (
    ApprovalGrantStatus,
    ApprovalRequestStatus,
    BeliefStatus,
    EffectClass,
    PolicyVerdict,
    RetentionClass,
    RunMode,
    RunState,
    SubgoalStatus,
    ToolName,
    TrustedTargetKind,
)
from .ids import uuid7
from .money import Money

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_RUNTIME_TOOLS = frozenset({ToolName.FINISH, ToolName.SAFE_ABORT})
_COORDINATE_TOOLS = frozenset({ToolName.CLICK, ToolName.DOUBLE_CLICK, ToolName.TYPE_TEXT})
_MUTATING_EFFECTS = frozenset(
    {
        EffectClass.REVERSIBLE_MUTATION,
        EffectClass.EXTERNAL_COMMUNICATION,
        EffectClass.FINANCIAL_OR_CONTRACTUAL_COMMIT,
        EffectClass.CREDENTIAL_OR_IDENTITY,
    }
)


def _require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def normalize_origin(value: str) -> str:
    """Validate and normalize an exact web origin.

    Plain HTTP is accepted only for ``localhost`` and ``*.localhost`` development
    origins. Paths, credentials, query strings, and fragments are forbidden.
    """

    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("origin must use http or https and include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("origin must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("origin must not include a path, query, or fragment")
    hostname = parsed.hostname.lower()
    local = hostname == "localhost" or hostname.endswith(".localhost")
    if parsed.scheme == "http" and not local:
        raise ValueError("plain HTTP is allowed only for localhost development origins")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("origin contains an invalid port") from error
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = rendered_host if port is None else f"{rendered_host}:{port}"
    return f"{parsed.scheme}://{netloc}"


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_default=True,
        str_strip_whitespace=True,
    )


class RunBudgets(FrozenModel):
    max_steps: int = Field(default=60, ge=1, le=500)
    max_model_calls: int = Field(default=45, ge=1, le=500)
    max_replans: int = Field(default=4, ge=0, le=50)
    max_wall_time_seconds: int = Field(default=600, ge=1, le=3600)
    max_model_cost: Money = Field(default_factory=lambda: Money(amount_minor=150, currency="USD"))
    max_read_retries_per_step: int = Field(default=2, ge=0, le=10)
    max_commit_retries: int = Field(default=0, ge=0, le=0)
    non_progress_limit: int = Field(default=2, ge=1, le=10)
    approval_ttl_seconds: int = Field(default=180, ge=15, le=900)
    max_commit_observation_age_seconds: int = Field(default=15, ge=1, le=120)


class HardConstraint(FrozenModel):
    field: str = Field(min_length=1, max_length=100)
    operator: str = Field(min_length=1, max_length=50)
    value: JsonValue


class Preference(FrozenModel):
    field: str = Field(min_length=1, max_length=100)
    direction: Literal["ascending", "descending"]


class SuccessPredicate(FrozenModel):
    predicate_id: str = Field(min_length=1, max_length=120)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class ApprovalRule(FrozenModel):
    effect: EffectClass
    rule: str = Field(min_length=1, max_length=120)


class TaskContract(FrozenModel):
    """Immutable actor-visible authority and objective.

    Fault assignments, expected answers, oracle handles, and evaluator versions are
    intentionally absent. ``content_hash`` is filled when omitted and verified when
    supplied, making parsed contracts tamper-evident.
    """

    schema_version: Literal["1.0.0"] = "1.0.0"
    contract_id: UUID = Field(default_factory=uuid7)
    content_hash: str = ""
    goal: str = Field(min_length=1, max_length=2000)
    hard_constraints: tuple[HardConstraint, ...]
    preferences: tuple[Preference, ...]
    success_predicates: tuple[SuccessPredicate, ...]
    forbidden_effects: tuple[str, ...]
    approval_rules: tuple[ApprovalRule, ...]
    allowed_origins: tuple[str, ...]
    allowed_tools: tuple[ToolName, ...]
    scenario_now: datetime
    max_steps: int = Field(default=60, ge=1, le=500)
    max_model_calls: int = Field(default=45, ge=1, le=500)
    max_replans: int = Field(default=4, ge=0, le=50)
    max_wall_time_seconds: int = Field(default=600, ge=1, le=3600)
    max_model_cost_usd: Decimal = Field(default=Decimal("1.50"), ge=0)
    max_read_retries_per_step: int = Field(default=2, ge=0, le=10)
    max_commit_retries: Literal[0] = 0
    non_progress_limit: int = Field(default=2, ge=1, le=10)
    approval_ttl_seconds: int = Field(default=180, ge=15, le=900)
    max_commit_observation_age_seconds: int = Field(default=15, ge=1, le=120)

    @field_validator("scenario_now")
    @classmethod
    def scenario_now_is_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "scenario_now")

    @field_validator("max_model_cost_usd", mode="before")
    @classmethod
    def model_cost_is_exact(cls, value: Any) -> Decimal:
        if isinstance(value, float):
            raise ValueError("max_model_cost_usd must not be constructed from a float")
        parsed = value if isinstance(value, Decimal) else Decimal(str(value))
        exponent = parsed.as_tuple().exponent
        if not parsed.is_finite() or not isinstance(exponent, int) or exponent < -2:
            raise ValueError("max_model_cost_usd must be a finite two-decimal value")
        return parsed.quantize(Decimal("0.01"))

    @field_serializer("max_model_cost_usd", when_used="json")
    def serialize_model_cost(self, value: Decimal) -> str:
        return format(value, ".2f")

    @field_validator("allowed_origins")
    @classmethod
    def origins_are_exact(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(normalize_origin(value) for value in values)
        if not normalized:
            raise ValueError("at least one allowed origin is required")
        if len(set(normalized)) != len(normalized):
            raise ValueError("allowed origins must be unique")
        return normalized

    @field_validator("allowed_tools")
    @classmethod
    def tools_are_unique(cls, values: tuple[ToolName, ...]) -> tuple[ToolName, ...]:
        if not values:
            raise ValueError("at least one actor tool is required")
        if len(set(values)) != len(values):
            raise ValueError("allowed tools must be unique")
        return values

    @model_validator(mode="after")
    def set_or_verify_content_hash(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"content_hash"})
        expected = sha256_hex(payload)
        if not self.content_hash:
            object.__setattr__(self, "content_hash", expected)
        elif not _SHA256_PATTERN.fullmatch(self.content_hash) or not compare_digest(
            self.content_hash, expected
        ):
            raise ValueError("task contract content_hash does not match canonical payload")
        return self

    def actor_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @property
    def budgets(self) -> RunBudgets:
        """Internal convenience view; the actor wire contract remains flat."""

        return RunBudgets(
            max_steps=self.max_steps,
            max_model_calls=self.max_model_calls,
            max_replans=self.max_replans,
            max_wall_time_seconds=self.max_wall_time_seconds,
            max_model_cost=Money(amount_minor=int(self.max_model_cost_usd * 100), currency="USD"),
            max_read_retries_per_step=self.max_read_retries_per_step,
            max_commit_retries=self.max_commit_retries,
            non_progress_limit=self.non_progress_limit,
            approval_ttl_seconds=self.approval_ttl_seconds,
            max_commit_observation_age_seconds=self.max_commit_observation_age_seconds,
        )


class ModelRunRecord(FrozenModel):
    provider: str = Field(min_length=1, max_length=50)
    model_id: str = Field(min_length=1, max_length=200)
    effective_parameters: dict[str, JsonValue] = Field(default_factory=dict)
    prompt_version: str = Field(min_length=1, max_length=100)
    price_table_version: str = Field(min_length=1, max_length=100)


class FaultAssignment(FrozenModel):
    fault_id: str = Field(min_length=1, max_length=120)
    fault_class: str = Field(min_length=1, max_length=120)
    seed: int = Field(ge=0, le=(1 << 31) - 1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class RunManifest(FrozenModel):
    """Sealed run configuration that must never be supplied to the actor."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    visibility: Literal["sealed_runtime_only"] = "sealed_runtime_only"
    manifest_id: UUID = Field(default_factory=uuid7)
    manifest_hash: str = ""
    run_id: UUID = Field(default_factory=uuid7)
    session_id: UUID
    mode: RunMode
    task_contract: TaskContract
    scenario_id: str = Field(min_length=1, max_length=120)
    scenario_seed: int = Field(ge=0, le=(1 << 31) - 1)
    fixture_version: str = Field(min_length=1, max_length=120)
    fault_manifest_version: str = Field(min_length=1, max_length=120)
    fault_id: str | None = Field(default=None, min_length=1, max_length=120)
    fault_parameters: dict[str, JsonValue] = Field(default_factory=dict)
    model: ModelRunRecord
    sandbox_snapshot_ref: str = Field(min_length=1, max_length=240)
    oracle_case_ref: str = Field(min_length=1, max_length=240)
    oracle_version: str = Field(min_length=1, max_length=100)
    expected_terminal_outcome: RunState
    retention_class: RetentionClass = RetentionClass.PUBLIC_EPHEMERAL
    evaluation_pair_id: UUID | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def created_at_is_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "created_at")

    @field_validator("expected_terminal_outcome")
    @classmethod
    def expected_state_is_terminal(cls, value: RunState) -> RunState:
        from .enums import TERMINAL_RUN_STATES

        if value not in TERMINAL_RUN_STATES:
            raise ValueError("expected terminal state must be terminal")
        return value

    @model_validator(mode="after")
    def set_or_verify_manifest_hash(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"manifest_hash"})
        expected = sha256_hex(payload)
        if not self.manifest_hash:
            object.__setattr__(self, "manifest_hash", expected)
        elif not _SHA256_PATTERN.fullmatch(self.manifest_hash) or not compare_digest(
            self.manifest_hash, expected
        ):
            raise ValueError("run manifest hash does not match canonical payload")
        return self

    def actor_contract(self) -> TaskContract:
        """Return the only portion of the manifest that may enter actor context."""

        return self.task_contract


class ExpectedPostcondition(FrozenModel):
    predicate_id: str = Field(min_length=1, max_length=160)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class ActionProposal(FrozenModel):
    """Actor-authored UI proposal with no trusted effect or authority fields."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    action_id: UUID = Field(default_factory=uuid7)
    run_id: UUID
    step_number: int = Field(ge=0)
    plan_version: int = Field(ge=0)
    observation_id: UUID
    observation_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool: ToolName
    target_description: str = Field(min_length=1, max_length=300)
    coordinates_normalized: tuple[int, int] | None = None
    text: str | None = Field(default=None, max_length=4000)
    expected_postconditions: tuple[ExpectedPostcondition, ...] = ()
    grounding_confidence: Decimal = Field(ge=0, le=1)
    decision_summary: str = Field(min_length=1, max_length=500)

    @field_validator("coordinates_normalized")
    @classmethod
    def coordinates_in_range(cls, value: tuple[int, int] | None) -> tuple[int, int] | None:
        if value is not None and any(coordinate < 0 or coordinate > 1000 for coordinate in value):
            raise ValueError("coordinates must be normalized to 0..1000")
        return value

    @field_validator("grounding_confidence", mode="before")
    @classmethod
    def confidence_is_exact(cls, value: Any) -> Decimal:
        if isinstance(value, float):
            return Decimal(str(value))
        return value if isinstance(value, Decimal) else Decimal(str(value))

    @field_serializer("grounding_confidence", when_used="json")
    def serialize_confidence(self, value: Decimal) -> str:
        return format(value.normalize(), "f")

    @model_validator(mode="after")
    def validate_tool_shape(self) -> Self:
        if self.tool in _RUNTIME_TOOLS:
            if self.coordinates_normalized is not None:
                raise ValueError("runtime actions cannot target coordinates")

        if self.tool in _COORDINATE_TOOLS and self.coordinates_normalized is None:
            raise ValueError(f"{self.tool.value} requires normalized coordinates")
        if self.tool is ToolName.TYPE_TEXT:
            if self.text is None:
                raise ValueError("ui.type_text requires text")
        elif self.tool in {ToolName.OPEN_URL, ToolName.KEYPRESS, ToolName.SCROLL, ToolName.WAIT}:
            if self.text is None:
                raise ValueError(f"{self.tool.value} requires a text argument")
        elif self.text is not None:
            raise ValueError(
                "text is accepted only by URL, typing, keypress, scroll, or wait tools"
            )
        return self


class ReadEffectContext(FrozenModel):
    kind: Literal["read"] = "read"
    resource_type: str = Field(min_length=1, max_length=100)
    resource_id: str = Field(min_length=1, max_length=200)


class BookingCommitContext(FrozenModel):
    kind: Literal["booking_commit"] = "booking_commit"
    traveler_id: str = Field(min_length=1, max_length=160)
    reservation_id: str = Field(min_length=1, max_length=160)
    offer_version: str = Field(min_length=1, max_length=160)
    marketing_carrier: str = Field(min_length=1, max_length=160)
    operating_carrier: str = Field(min_length=1, max_length=160)
    flight_id: str = Field(min_length=1, max_length=160)
    origin_airport: str = Field(pattern=r"^[A-Z]{3}$")
    destination_airport: str = Field(pattern=r"^[A-Z]{3}$")
    departure: datetime
    arrival: datetime
    stop_count: int = Field(ge=0, le=8)
    cabin: str = Field(min_length=1, max_length=60)
    fare_class: str = Field(min_length=1, max_length=60)
    seat_type: str = Field(min_length=1, max_length=60)
    base_fare_minor: int = Field(ge=0, le=(1 << 53) - 1)
    taxes_and_fees_minor: int = Field(ge=0, le=(1 << 53) - 1)
    total_additional_cost_minor: int = Field(ge=0, le=(1 << 53) - 1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")

    @field_validator("departure", "arrival")
    @classmethod
    def itinerary_time_is_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "itinerary time")

    @model_validator(mode="after")
    def arrival_follows_departure(self) -> Self:
        if self.arrival <= self.departure:
            raise ValueError("arrival must be after departure")
        if self.base_fare_minor + self.taxes_and_fees_minor != self.total_additional_cost_minor:
            raise ValueError("fee-inclusive total must equal base fare plus taxes and fees")
        return self


class CalendarMutationContext(FrozenModel):
    kind: Literal["calendar_update"] = "calendar_update"
    calendar_event_id: str = Field(min_length=1, max_length=160)
    verified_booking_id: str = Field(min_length=1, max_length=160)
    starts_at: datetime
    ends_at: datetime

    @field_validator("starts_at", "ends_at")
    @classmethod
    def event_time_is_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "calendar time")

    @model_validator(mode="after")
    def event_has_positive_duration(self) -> Self:
        if self.ends_at <= self.starts_at:
            raise ValueError("calendar event must have positive duration")
        return self


class GenericEffectContext(FrozenModel):
    kind: Literal["generic"] = "generic"
    resource_type: str = Field(min_length=1, max_length=100)
    resource_id: str = Field(min_length=1, max_length=200)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)


EffectContext = Annotated[
    ReadEffectContext | BookingCommitContext | CalendarMutationContext | GenericEffectContext,
    Field(discriminator="kind"),
]


class EffectProposal(FrozenModel):
    """Trusted, runtime-derived semantic effect for one actor proposal."""

    schema_version: Literal["1.0.0"] = "1.0.0"
    effect_id: UUID = Field(default_factory=uuid7)
    action: ActionProposal
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    origin: str | None
    effect_class: EffectClass
    trusted_target_kind: TrustedTargetKind
    context: EffectContext
    approved_context_hash: str = ""
    idempotency_key: str | None = None
    approval_required: bool
    derived_by_rule: str = Field(min_length=1, max_length=160)
    derived_at: datetime

    @field_validator("origin")
    @classmethod
    def effect_origin_is_exact(cls, value: str | None) -> str | None:
        return None if value is None else normalize_origin(value)

    @field_validator("derived_at")
    @classmethod
    def derived_at_is_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "derived_at")

    @model_validator(mode="after")
    def set_or_verify_context_hash(self) -> Self:
        context_payload = {
            "contract_hash": self.contract_hash,
            "origin": self.origin,
            "effect_class": self.effect_class,
            "target_kind": self.trusted_target_kind,
            "context": self.context,
        }
        expected = sha256_hex(context_payload)
        if not self.approved_context_hash:
            object.__setattr__(self, "approved_context_hash", expected)
        elif not _SHA256_PATTERN.fullmatch(self.approved_context_hash) or not compare_digest(
            self.approved_context_hash, expected
        ):
            raise ValueError("approved_context_hash does not match semantic effect context")
        if self.effect_class in _MUTATING_EFFECTS and not self.idempotency_key:
            raise ValueError("mutating effects require a runtime idempotency key")
        if self.effect_class not in _MUTATING_EFFECTS and self.idempotency_key is not None:
            raise ValueError("read-only effects must not have an idempotency key")
        return self


class PolicyDecision(FrozenModel):
    decision_id: UUID = Field(default_factory=uuid7)
    effect_id: UUID
    verdict: PolicyVerdict
    rule_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=500)
    context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    evaluated_at: datetime

    @field_validator("evaluated_at")
    @classmethod
    def evaluated_at_is_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "evaluated_at")


class ApprovalRequest(FrozenModel):
    """Human-facing request; no capability exists until it is approved."""

    request_id: UUID = Field(default_factory=uuid7)
    run_id: UUID
    action_id: UUID
    effect: EffectProposal
    status: ApprovalRequestStatus = ApprovalRequestStatus.PENDING
    summary: str = Field(min_length=1, max_length=1000)
    created_at: datetime
    expires_at: datetime

    @field_validator("created_at", "expires_at")
    @classmethod
    def request_time_is_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "approval request time")

    @model_validator(mode="after")
    def request_matches_effect(self) -> Self:
        if (
            self.run_id != self.effect.action.run_id
            or self.action_id != self.effect.action.action_id
        ):
            raise ValueError("approval request must bind the exact run and action proposal")
        if not self.effect.approval_required:
            raise ValueError("approval request requires an approval-bound effect")
        if self.expires_at <= self.created_at:
            raise ValueError("approval request expiry must follow creation")
        return self


class ApprovalGrantPayload(FrozenModel):
    """Sealed, signed authorization payload consumed by the trust gateway."""

    version: Literal[1] = 1
    grant_id: UUID = Field(default_factory=uuid7)
    approval_request_id: UUID
    run_id: UUID
    effect_proposal_id: UUID
    idempotency_key: str = Field(min_length=16, max_length=128)
    origin: str
    effect: Literal[EffectClass.FINANCIAL_OR_CONTRACTUAL_COMMIT] = (
        EffectClass.FINANCIAL_OR_CONTRACTUAL_COMMIT
    )
    traveler_id: str = Field(min_length=1, max_length=160)
    reservation_id: str = Field(min_length=1, max_length=160)
    offer_version: str = Field(min_length=1, max_length=160)
    marketing_carrier: str = Field(min_length=1, max_length=160)
    operating_carrier: str = Field(min_length=1, max_length=160)
    flight_id: str = Field(min_length=1, max_length=160)
    origin_airport: str = Field(pattern=r"^[A-Z]{3}$")
    destination_airport: str = Field(pattern=r"^[A-Z]{3}$")
    departure: datetime
    arrival: datetime
    stop_count: int = Field(ge=0, le=8)
    cabin: str = Field(min_length=1, max_length=60)
    fare_class: str = Field(min_length=1, max_length=60)
    seat_type: str = Field(min_length=1, max_length=60)
    base_fare_minor: int = Field(ge=0, le=(1 << 53) - 1)
    taxes_and_fees_minor: int = Field(ge=0, le=(1 << 53) - 1)
    total_additional_cost_minor: int = Field(ge=0, le=(1 << 53) - 1)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    approved_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    observation_hash_at_proposal: str = Field(pattern=r"^[0-9a-f]{64}$")
    issued_at: datetime
    expires_at: datetime
    nonce: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("origin")
    @classmethod
    def grant_origin_is_exact(cls, value: str) -> str:
        return normalize_origin(value)

    @field_validator("departure", "arrival", "issued_at", "expires_at")
    @classmethod
    def grant_time_is_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "approval grant time")

    @model_validator(mode="after")
    def grant_has_valid_lifetime(self) -> Self:
        if self.expires_at <= self.issued_at:
            raise ValueError("grant expiry must follow issue time")
        if self.arrival <= self.departure:
            raise ValueError("arrival must follow departure")
        if self.base_fare_minor + self.taxes_and_fees_minor != self.total_additional_cost_minor:
            raise ValueError("fee-inclusive total must equal base fare plus taxes and fees")
        return self


class ApprovalGrant(FrozenModel):
    visibility: Literal["sealed_runtime_only"] = "sealed_runtime_only"
    payload: ApprovalGrantPayload
    signature: str = Field(pattern=r"^[0-9a-f]{64}$")
    capability_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: ApprovalGrantStatus = ApprovalGrantStatus.ACTIVE
    consumed_at: datetime | None = None

    @field_validator("consumed_at")
    @classmethod
    def consumed_at_is_aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware(value, "consumed_at")

    @model_validator(mode="after")
    def consumed_status_is_consistent(self) -> Self:
        if (self.status is ApprovalGrantStatus.CONSUMED) != (self.consumed_at is not None):
            raise ValueError("only consumed grants may have consumed_at")
        return self


class AuthorizedAction(FrozenModel):
    """The only action envelope accepted by an executor."""

    authorization_id: UUID = Field(default_factory=uuid7)
    action: ActionProposal
    effect: EffectProposal
    policy_decision: PolicyDecision
    grant_id: UUID | None = None
    authorized_at: datetime

    @field_validator("authorized_at")
    @classmethod
    def authorized_at_is_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "authorized_at")

    @model_validator(mode="after")
    def authorization_is_consistent(self) -> Self:
        if self.action.action_id != self.effect.action.action_id:
            raise ValueError("authorized action and effect must bind the same actor proposal")
        if self.policy_decision.effect_id != self.effect.effect_id:
            raise ValueError("policy decision must bind the authorized effect")
        if self.policy_decision.verdict is PolicyVerdict.DENY:
            raise ValueError("a denied effect cannot become an authorized action")
        if self.policy_decision.verdict is PolicyVerdict.REQUIRE_APPROVAL and self.grant_id is None:
            raise ValueError("approval-bound authorization requires a grant")
        return self


class PlanSubgoal(FrozenModel):
    subgoal_id: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    status: SubgoalStatus
    depends_on: tuple[str, ...] = ()
    expected_postconditions: tuple[ExpectedPostcondition, ...] = ()
    evidence_ids: tuple[str, ...] = ()


class ActorPlan(FrozenModel):
    plan_version: int = Field(ge=0)
    goal: str = Field(min_length=1, max_length=2000)
    subgoals: tuple[PlanSubgoal, ...]
    active_subgoal_id: str | None = None
    created_at_step: int = Field(ge=0)


class BeliefFact(FrozenModel):
    fact_id: UUID = Field(default_factory=uuid7)
    subject: str = Field(min_length=1, max_length=160)
    predicate: str = Field(min_length=1, max_length=160)
    value: JsonValue
    confidence: Decimal = Field(ge=0, le=1)
    evidence_ids: tuple[str, ...]
    observed_at: datetime
    expires_after_steps: int | None = Field(default=None, ge=1)
    status: BeliefStatus = BeliefStatus.ACTIVE
    untrusted_content: bool = False

    @field_validator("observed_at")
    @classmethod
    def observed_at_is_aware(cls, value: datetime) -> datetime:
        return _require_aware(value, "observed_at")

    @field_validator("confidence", mode="before")
    @classmethod
    def belief_confidence_is_exact(cls, value: Any) -> Decimal:
        return value if isinstance(value, Decimal) else Decimal(str(value))

    @field_serializer("confidence", when_used="json")
    def serialize_belief_confidence(self, value: Decimal) -> str:
        return format(value.normalize(), "f")
