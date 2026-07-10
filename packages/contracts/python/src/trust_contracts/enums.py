"""Shared enumerations for the trust runtime.

The values in this module are persisted and included in signed payloads. Renaming a
value is therefore a schema migration, not a cosmetic refactor.
"""

from enum import StrEnum


class RunMode(StrEnum):
    BASELINE = "baseline"
    PROTECTED = "protected"
    MOCK = "mock"
    REPLAY = "replay"


class RunState(StrEnum):
    CREATED = "CREATED"
    ENV_RESET = "ENV_RESET"
    CONTRACT_VALIDATED = "CONTRACT_VALIDATED"
    OBSERVING = "OBSERVING"
    PLANNING = "PLANNING"
    REPLANNING = "REPLANNING"
    ACTION_PROPOSED = "ACTION_PROPOSED"
    POLICY_CHECKING = "POLICY_CHECKING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    FINALIZING = "FINALIZING"
    SUCCEEDED = "SUCCEEDED"
    SAFE_ABORTED = "SAFE_ABORTED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    HANDOFF_REQUIRED = "HANDOFF_REQUIRED"
    FAILED_OUTCOME_UNKNOWN = "FAILED_OUTCOME_UNKNOWN"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_RUN_STATES = frozenset(
    {
        RunState.SUCCEEDED,
        RunState.SAFE_ABORTED,
        RunState.PARTIAL_SUCCESS,
        RunState.HANDOFF_REQUIRED,
        RunState.FAILED_OUTCOME_UNKNOWN,
        RunState.FAILED,
        RunState.CANCELLED,
    }
)


class ToolName(StrEnum):
    OPEN_URL = "ui.open_url"
    CLICK = "ui.click"
    DOUBLE_CLICK = "ui.double_click"
    TYPE_TEXT = "ui.type_text"
    KEYPRESS = "ui.keypress"
    SCROLL = "ui.scroll"
    BACK = "ui.back"
    WAIT = "ui.wait"
    FINISH = "runtime.finish"
    SAFE_ABORT = "runtime.safe_abort"


class EffectClass(StrEnum):
    READ = "READ"
    DRAFT = "DRAFT"
    REVERSIBLE_MUTATION = "REVERSIBLE_MUTATION"
    EXTERNAL_COMMUNICATION = "EXTERNAL_COMMUNICATION"
    FINANCIAL_OR_CONTRACTUAL_COMMIT = "FINANCIAL_OR_CONTRACTUAL_COMMIT"
    CREDENTIAL_OR_IDENTITY = "CREDENTIAL_OR_IDENTITY"


class TrustedTargetKind(StrEnum):
    NAVIGATION = "NAVIGATION"
    READ_ONLY_CONTROL = "READ_ONLY_CONTROL"
    DRAFT_FIELD = "DRAFT_FIELD"
    BOOKING_CONFIRM = "BOOKING_CONFIRM"
    CALENDAR_SAVE = "CALENDAR_SAVE"
    EXTERNAL_SEND = "EXTERNAL_SEND"
    RUNTIME_FINISH = "RUNTIME_FINISH"
    RUNTIME_SAFE_ABORT = "RUNTIME_SAFE_ABORT"


class PolicyVerdict(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"


class ApprovalRequestStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class ApprovalGrantStatus(StrEnum):
    ACTIVE = "ACTIVE"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class VerificationResult(StrEnum):
    VERIFIED = "VERIFIED"
    NOT_VERIFIED = "NOT_VERIFIED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    CONSTRAINT_CHANGED = "CONSTRAINT_CHANGED"
    POLICY_BLOCKED = "POLICY_BLOCKED"


class FailureClass(StrEnum):
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    ACTION_NO_EFFECT = "ACTION_NO_EFFECT"
    CONSTRAINT_DRIFT = "CONSTRAINT_DRIFT"
    AUTHENTICATION_EXPIRED = "AUTHENTICATION_EXPIRED"
    OUTCOME_UNKNOWN = "OUTCOME_UNKNOWN"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    APPROVAL_EXPIRED = "APPROVAL_EXPIRED"
    NON_PROGRESS = "NON_PROGRESS"
    NO_COMPLIANT_OPTION = "NO_COMPLIANT_OPTION"
    UNTRUSTED_INSTRUCTION_DETECTED = "UNTRUSTED_INSTRUCTION_DETECTED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"


class RetentionClass(StrEnum):
    PUBLIC_EPHEMERAL = "public_ephemeral"
    LOCAL_DEVELOPMENT = "local_development"
    PUBLISHED_BENCHMARK = "published_benchmark"


class SubgoalStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    VERIFIED = "verified"
    BLOCKED = "blocked"
    ABANDONED = "abandoned"


class BeliefStatus(StrEnum):
    ACTIVE = "active"
    CONTRADICTED = "contradicted"
    EXPIRED = "expired"
