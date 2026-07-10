"""HTTP boundary schemas; sealed manifests are deliberately absent."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from trust_contracts import ApprovalRequestStatus, EffectClass, RunMode, RunState, TaskContract


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSessionRequest(ApiModel):
    client_label: str | None = Field(default=None, max_length=80)


class SessionResponse(ApiModel):
    session_id: UUID
    session_token: str
    expires_at: datetime


class ScenarioSelection(ApiModel):
    """Sealed orchestration input that is never included in actor context."""

    scenario_id: str = Field(default="disrupted_trip_v1", min_length=1, max_length=120)
    scenario_seed: int = Field(default=1001, ge=0, le=(1 << 31) - 1)
    fault_id: str | None = Field(default=None, min_length=1, max_length=120)


class CreateRunRequest(ApiModel):
    task_contract: TaskContract
    scenario_selection: ScenarioSelection = Field(default_factory=ScenarioSelection)
    mode: RunMode = RunMode.PROTECTED


class RunResponse(ApiModel):
    run_id: UUID
    session_id: UUID
    mode: RunMode
    status: RunState
    task_contract: TaskContract
    created_at: datetime
    execution_kind: Literal["live_model", "deterministic_mock", "recorded_replay"]
    model_provider: str
    model_id: str
    pending_approval: "ApprovalResponse | None" = None


class CancelRunResponse(ApiModel):
    run_id: UUID
    status: RunState


class ApprovalResponse(ApiModel):
    approval_id: UUID
    run_id: UUID
    status: ApprovalRequestStatus
    effect: EffectClass
    summary: str
    approved_context_hash: str
    requested_at: datetime
    expires_at: datetime
    scope: dict[str, object]
    decided_at: datetime | None = None
    resumed: bool = False


class CreateEvaluationRequest(ApiModel):
    plan_id: Literal["paired-primary-v1", "protected-safety-gates-v1"]
    maximum_total_cost_usd: Decimal = Field(gt=0, decimal_places=2)


class EvaluationResponse(ApiModel):
    evaluation_id: UUID
    plan_id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    maximum_total_cost_usd: Decimal
    intended_execution_count: int
    execution_status_counts: dict[str, int]
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    last_error: str | None = None


class EvaluationExecutionResponse(ApiModel):
    execution_id: UUID
    case_id: UUID
    run_id: UUID | None
    arm: Literal["baseline", "protected"]
    attempt_kind: Literal["original", "replacement"]
    status: str
    invalid_reason: str | None
    model_cost_usd: Decimal
    raw_predicate_results: dict[str, object]


class MetricResultResponse(ApiModel):
    metric_name: str
    metric_value: Decimal
    confidence_low: Decimal | None
    confidence_high: Decimal | None
    report_version: str


class EvaluationResultsResponse(ApiModel):
    evaluation_id: UUID
    evidence_status: Literal["PENDING", "RAW_EXECUTIONS_AVAILABLE", "COMPLETE"]
    executions: list[EvaluationExecutionResponse]
    metrics: list[MetricResultResponse]


class GatewayCommitRequest(ApiModel):
    grant_id: UUID
    current_context_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class GatewayCommitResponse(ApiModel):
    booking_id: UUID
    booking_reference: str
    side_effect_id: UUID
    idempotent_replay: bool
    committed_at: datetime


class HealthResponse(ApiModel):
    status: Literal["ok"] = "ok"
    service: Literal["trust-runtime"] = "trust-runtime"
    version: str


class ReadinessResponse(ApiModel):
    status: Literal["ready"] = "ready"
    checks: dict[str, str]


class ApiErrorDetail(ApiModel):
    code: str
    message: str


class ApiErrorEnvelope(ApiModel):
    version: Literal["1.0.0"] = "1.0.0"
    error: ApiErrorDetail
