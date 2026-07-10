"""Domain errors that map cleanly to API error envelopes."""


class TrustRuntimeError(Exception):
    code = "TRUST_RUNTIME_ERROR"
    status_code = 400


class InvalidTransitionError(TrustRuntimeError):
    code = "INVALID_TRANSITION"
    status_code = 409


class TerminalStateError(TrustRuntimeError):
    code = "TERMINAL_STATE_IMMUTABLE"
    status_code = 409


class PolicyDeniedError(TrustRuntimeError):
    code = "POLICY_DENIED"
    status_code = 403


class ApprovalError(TrustRuntimeError):
    code = "APPROVAL_INVALID"
    status_code = 409


class ApprovalExpiredError(ApprovalError):
    code = "APPROVAL_EXPIRED"


class ApprovalReplayError(ApprovalError):
    code = "APPROVAL_REPLAYED"


class ApprovalStaleError(ApprovalError):
    code = "APPROVAL_STALE"


class IdempotencyConflictError(TrustRuntimeError):
    code = "IDEMPOTENCY_CONFLICT"
    status_code = 409


class SessionNotFoundError(TrustRuntimeError):
    code = "SESSION_NOT_FOUND"
    status_code = 404


class RunNotFoundError(TrustRuntimeError):
    code = "RUN_NOT_FOUND"
    status_code = 404


class InvalidEventCursorError(TrustRuntimeError):
    code = "INVALID_EVENT_CURSOR"
    status_code = 400


class QuotaExceededError(TrustRuntimeError):
    code = "LIVE_RUN_QUOTA_EXCEEDED"
    status_code = 429
    retry_after_seconds = 60


class RequestTooLargeError(TrustRuntimeError):
    code = "REQUEST_TOO_LARGE"
    status_code = 413


class ArtifactAccessError(TrustRuntimeError):
    code = "ARTIFACT_ACCESS_DENIED"
    status_code = 403


class OperatorUnauthorizedError(TrustRuntimeError):
    code = "OPERATOR_UNAUTHORIZED"
    status_code = 401


class EvaluationConfigurationError(TrustRuntimeError):
    code = "EVALUATION_CONFIGURATION_INCOMPLETE"
    status_code = 409


class EvaluationSpendCapError(TrustRuntimeError):
    code = "EVALUATION_SPEND_CAP_INVALID"
    status_code = 422


class EvaluationNotFoundError(TrustRuntimeError):
    code = "EVALUATION_NOT_FOUND"
    status_code = 404


class GatewayUnauthorizedError(TrustRuntimeError):
    code = "GATEWAY_UNAUTHORIZED"
    status_code = 401


class GatewayCommitRejectedError(TrustRuntimeError):
    code = "GATEWAY_COMMIT_REJECTED"
    status_code = 409


class GatewayUnavailableError(TrustRuntimeError):
    code = "GATEWAY_UNAVAILABLE"
    status_code = 503
