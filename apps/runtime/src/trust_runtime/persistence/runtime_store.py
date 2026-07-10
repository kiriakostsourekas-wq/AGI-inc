"""Synchronous short-transaction store for the public runtime boundary.

The browser worker remains asynchronous, but each structured-state write is small
and committed before the public API acknowledges it. Large screenshot bytes stay
in the artifact backend; only their immutable metadata is stored here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from sqlalchemy import Engine, create_engine, delete, func, select
from sqlalchemy.orm import Session, sessionmaker
from trust_contracts import (
    ApprovalGrant,
    ApprovalRequest,
    EffectProposal,
    PolicyDecision,
    RunManifest,
    RunState,
    TaskContract,
    sha256_hex,
    uuid7,
)

from ..artifacts import ArtifactRecord
from ..config import RuntimeSettings
from ..telemetry import traced
from .models import (
    TERMINAL_RUN_STATES,
    ActionProposalRow,
    ApprovalGrantRow,
    ApprovalRequestRow,
    ArtifactRow,
    DemoSessionRow,
    EffectProposalRow,
    EvalCaseRow,
    EvalExecutionRow,
    EvaluationBatchRow,
    JobRow,
    MetricResultRow,
    PolicyDecisionRow,
    ReplacementBookingRow,
    RunEventRow,
    RunRow,
    SideEffectRow,
    TaskContractRow,
)


@dataclass(frozen=True, slots=True)
class StoredRun:
    run_id: UUID
    session_id: UUID | None
    mode: str
    status: str
    contract: TaskContract
    model_provider: str
    model_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StoredEvaluation:
    evaluation_id: UUID
    plan_id: str
    status: str
    maximum_total_cost_usd: Decimal
    intended_execution_count: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    last_error: str | None
    execution_status_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class ClaimedEvaluationExecution:
    job_id: UUID
    execution_id: UUID
    evaluation_id: UUID
    arm: str
    case_manifest: dict[str, object]
    attempt_number: int


@dataclass(frozen=True, slots=True)
class EvaluationFailureContext:
    error_type: str | None
    model_call_count: int
    actor_decision_count: int
    side_effect_count: int


class PostgresRuntimeStore:
    """PostgreSQL source of truth used by the public API and worker trace."""

    def __init__(self, settings: RuntimeSettings) -> None:
        self._engine: Engine = create_engine(
            settings.database_url.get_secret_value(),
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout_seconds,
            pool_pre_ping=True,
            connect_args={
                "options": f"-c statement_timeout={settings.database_statement_timeout_ms}"
            },
        )
        self._sessions = sessionmaker(self._engine, expire_on_commit=False)

    def ping(self) -> None:
        with self._engine.connect() as connection:
            connection.exec_driver_sql("select 1")

    def close(self) -> None:
        self._engine.dispose()

    @traced("db.session.create", component="persistence")
    def create_session(
        self,
        *,
        session_id: UUID,
        token_hash: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> None:
        with self._sessions.begin() as session:
            session.add(
                DemoSessionRow(
                    id=session_id,
                    public_token_hash=token_hash,
                    created_at=created_at,
                    expires_at=expires_at,
                    live_run_count=0,
                )
            )

    def session_by_token_hash(self, token_hash: str) -> DemoSessionRow | None:
        with self._sessions() as session:
            return session.scalar(
                select(DemoSessionRow).where(DemoSessionRow.public_token_hash == token_hash)
            )

    @traced("db.run.create", component="persistence")
    def create_run(self, manifest: RunManifest, *, created_at: datetime) -> None:
        contract = manifest.task_contract
        with self._sessions.begin() as session:
            existing_contract = session.get(TaskContractRow, contract.contract_id)
            if existing_contract is None:
                session.add(
                    TaskContractRow(
                        id=contract.contract_id,
                        schema_version=contract.schema_version,
                        content_hash=contract.content_hash,
                        canonical_payload=contract.model_dump(mode="json"),
                        created_at=created_at,
                    )
                )
                session.flush()
            session.add(
                RunRow(
                    id=manifest.run_id,
                    session_id=manifest.session_id,
                    contract_id=contract.contract_id,
                    mode=manifest.mode.value,
                    status=RunState.CREATED.value,
                    scenario_id=manifest.scenario_id,
                    scenario_seed=manifest.scenario_seed,
                    fixture_version=manifest.fixture_version,
                    fault_id=manifest.fault_id,
                    fault_parameters=manifest.fault_parameters,
                    oracle_version=manifest.oracle_version,
                    manifest_hash=manifest.manifest_hash,
                    retention_class="public_ephemeral",
                    expected_terminal_outcome=manifest.expected_terminal_outcome.value,
                    model_provider=manifest.model.provider,
                    model_id=manifest.model.model_id,
                    prompt_version=manifest.model.prompt_version,
                    fault_manifest_version=manifest.fault_manifest_version,
                    step_count=0,
                    model_call_count=0,
                    model_cost_usd=Decimal("0"),
                    created_at=created_at,
                )
            )
            session.flush()
            self._append_event_locked(
                session,
                run_id=manifest.run_id,
                event_type="run.created",
                payload={"status": RunState.CREATED.value},
                created_at=created_at,
            )

    @traced("db.run.read", component="persistence")
    def load_run(self, run_id: UUID) -> StoredRun | None:
        with self._sessions() as session:
            row = session.get(RunRow, run_id)
            if row is None:
                return None
            contract_row = session.get(TaskContractRow, row.contract_id)
            if contract_row is None:
                raise RuntimeError("persisted run has no task contract")
            return StoredRun(
                run_id=row.id,
                session_id=row.session_id,
                mode=row.mode,
                status=row.status,
                contract=TaskContract.model_validate(contract_row.canonical_payload),
                model_provider=row.model_provider or "unknown",
                model_id=row.model_id or "unknown",
                created_at=row.created_at,
            )

    @traced("db.event.append", component="persistence")
    def append_event(
        self,
        *,
        run_id: UUID,
        event_type: str,
        payload: dict[str, object],
        created_at: datetime,
    ) -> int:
        with self._sessions.begin() as session:
            run = session.scalar(select(RunRow).where(RunRow.id == run_id).with_for_update())
            if run is None:
                raise RuntimeError("cannot append an event for a missing run")
            if event_type == "run.state_transition":
                target = payload.get("to_state") or payload.get("status")
                if isinstance(target, str):
                    if run.status in TERMINAL_RUN_STATES and run.status != target:
                        raise RuntimeError("persisted terminal run is immutable")
                    run.status = target
                    if run.started_at is None and target != RunState.CREATED.value:
                        run.started_at = created_at
                    if target in TERMINAL_RUN_STATES:
                        run.finished_at = created_at
                        reason = payload.get("reason")
                        run.terminal_reason = reason if isinstance(reason, str) else None
            elif event_type == "model.usage":
                call_count = payload.get("model_call_count")
                cumulative_cost = payload.get("cumulative_cost_usd")
                if isinstance(call_count, int) and isinstance(cumulative_cost, str):
                    run.model_call_count = call_count
                    run.model_cost_usd = Decimal(cumulative_cost)
            elif event_type == "action.proposed":
                step_number = payload.get("step_number")
                if isinstance(step_number, int):
                    run.step_count = max(run.step_count, step_number)
            event = self._append_event_locked(
                session,
                run_id=run_id,
                event_type=event_type,
                payload=payload,
                created_at=created_at,
            )
            session.flush()
            return event.sequence_no

    def events_after(self, run_id: UUID, after: int) -> list[RunEventRow]:
        with self._sessions() as session:
            return list(
                session.scalars(
                    select(RunEventRow)
                    .where(RunEventRow.run_id == run_id, RunEventRow.sequence_no > after)
                    .order_by(RunEventRow.sequence_no)
                )
            )

    @traced("db.artifact.record", component="persistence")
    def record_artifact(self, record: ArtifactRecord) -> None:
        with self._sessions.begin() as session:
            session.add(
                ArtifactRow(
                    id=record.artifact_id,
                    run_id=record.run_id,
                    event_id=None,
                    kind=record.kind,
                    storage_key=f"{record.run_id}/{record.artifact_id}.png",
                    content_type=record.content_type,
                    byte_size=record.byte_size,
                    sha256=record.sha256,
                    redaction_status=record.redaction_status,
                    expires_at=record.expires_at,
                    created_at=record.created_at,
                )
            )

    def delete_expired_artifacts(self, *, now: datetime) -> int:
        with self._sessions.begin() as session:
            result = session.execute(
                delete(ArtifactRow)
                .where(ArtifactRow.expires_at.is_not(None), ArtifactRow.expires_at <= now)
                .returning(ArtifactRow.id)
            )
            return len(result.scalars().all())

    @traced("db.evaluation.create", component="persistence")
    def create_evaluation(
        self,
        *,
        manifest: dict[str, object],
        maximum_total_cost_usd: Decimal,
        requested_by: str,
        created_at: datetime,
    ) -> StoredEvaluation:
        design_value = manifest.get("evaluationDesign")
        cases_value = manifest.get("cases")
        schedule_value = manifest.get("executionSchedule")
        if (
            not isinstance(design_value, dict)
            or not isinstance(cases_value, list)
            or not isinstance(schedule_value, list)
        ):
            raise ValueError("evaluation manifest is malformed")
        design = cast(dict[str, object], design_value)
        cases = cast(list[object], cases_value)
        schedule = cast(list[object], schedule_value)
        intended = design.get("intendedExecutionCount")
        plan_id = manifest.get("planId")
        if not isinstance(intended, int) or intended <= 0 or not isinstance(plan_id, str):
            raise ValueError("evaluation manifest is missing intent accounting")
        evaluation_id = uuid7()
        with self._sessions.begin() as session:
            session.add(
                EvaluationBatchRow(
                    id=evaluation_id,
                    plan_id=plan_id,
                    status="queued",
                    requested_by=requested_by,
                    maximum_total_cost_usd=maximum_total_cost_usd,
                    intended_execution_count=intended,
                    manifest_hash=sha256_hex(manifest),
                    manifest=manifest,
                    created_at=created_at,
                )
            )
            session.flush()
            case_rows: dict[str, tuple[UUID, dict[str, object]]] = {}
            for case_value in cases:
                if not isinstance(case_value, dict):
                    raise ValueError("evaluation case is malformed")
                case = cast(dict[str, object], case_value)
                case_id = case.get("caseId")
                fault_class = case.get("faultClass")
                seed = case.get("seed")
                modes = case.get("modes")
                if (
                    not isinstance(case_id, str)
                    or not isinstance(fault_class, str)
                    or not isinstance(seed, int)
                    or not isinstance(modes, list)
                ):
                    raise ValueError("evaluation case is missing required fields")
                row_id = uuid7()
                session.add(
                    EvalCaseRow(
                        id=row_id,
                        evaluation_id=evaluation_id,
                        case_id=case_id,
                        fault_class=fault_class,
                        seed=seed,
                        manifest_hash=sha256_hex(case),
                        case_manifest=case,
                        created_at=created_at,
                    )
                )
                case_rows[case_id] = (row_id, case)

            execution_count = 0
            seen_ordinals: set[int] = set()
            seen_intents: set[str] = set()
            for schedule_value in schedule:
                if not isinstance(schedule_value, dict):
                    raise ValueError("evaluation schedule entry is malformed")
                entry = cast(dict[str, object], schedule_value)
                ordinal = entry.get("ordinal")
                intent_id = entry.get("intentId")
                case_id = entry.get("caseId")
                mode = entry.get("mode")
                if (
                    not isinstance(ordinal, int)
                    or not isinstance(intent_id, str)
                    or not isinstance(case_id, str)
                    or mode not in {"baseline", "protected"}
                ):
                    raise ValueError("evaluation schedule entry is missing required fields")
                if ordinal in seen_ordinals or intent_id in seen_intents:
                    raise ValueError("evaluation schedule contains duplicate intent or ordinal")
                row = case_rows.get(case_id)
                if row is None:
                    raise ValueError("evaluation schedule references an unknown case")
                row_id, case = row
                modes = case.get("modes")
                if not isinstance(modes, list) or mode not in modes:
                    raise ValueError("evaluation schedule arm is not declared by its case")
                seen_ordinals.add(ordinal)
                seen_intents.add(intent_id)
                execution_id = uuid7()
                session.add(
                    EvalExecutionRow(
                        id=execution_id,
                        evaluation_id=evaluation_id,
                        eval_case_id=row_id,
                        run_id=None,
                        original_execution_id=None,
                        arm=cast(str, mode),
                        attempt_kind="original",
                        status="intended",
                        oracle_version="oracle-v1",
                        raw_predicate_results={},
                        model_cost_usd=Decimal("0"),
                        created_at=created_at,
                    )
                )
                session.add(
                    JobRow(
                        id=uuid7(),
                        job_type="evaluation_execution",
                        run_id=None,
                        status="pending",
                        priority=ordinal,
                        attempts=0,
                        available_at=created_at,
                        payload={
                            "evaluation_id": str(evaluation_id),
                            "execution_id": str(execution_id),
                            "intent_id": intent_id,
                            "ordinal": ordinal,
                            "case_id": case_id,
                            "arm": mode,
                        },
                        created_at=created_at,
                    )
                )
                execution_count += 1
            if seen_ordinals != set(range(1, intended + 1)):
                raise ValueError("evaluation schedule ordinals must be contiguous from one")
            if execution_count != intended:
                raise ValueError(
                    f"manifest declares {intended} intents but expands to {execution_count}"
                )
        stored = self.load_evaluation(evaluation_id)
        if stored is None:
            raise RuntimeError("evaluation disappeared after creation")
        return stored

    def load_evaluation(self, evaluation_id: UUID) -> StoredEvaluation | None:
        with self._sessions() as session:
            row = session.get(EvaluationBatchRow, evaluation_id)
            if row is None:
                return None
            count_rows = (
                session.execute(
                    select(EvalExecutionRow.status, func.count())
                    .where(EvalExecutionRow.evaluation_id == evaluation_id)
                    .group_by(EvalExecutionRow.status)
                )
                .tuples()
                .all()
            )
            return StoredEvaluation(
                evaluation_id=row.id,
                plan_id=row.plan_id,
                status=row.status,
                maximum_total_cost_usd=row.maximum_total_cost_usd,
                intended_execution_count=row.intended_execution_count,
                created_at=row.created_at,
                started_at=row.started_at,
                completed_at=row.completed_at,
                last_error=row.last_error,
                execution_status_counts={key: int(value) for key, value in count_rows},
            )

    def evaluation_results(
        self, evaluation_id: UUID
    ) -> tuple[list[EvalExecutionRow], list[MetricResultRow]]:
        with self._sessions() as session:
            executions = list(
                session.scalars(
                    select(EvalExecutionRow)
                    .where(EvalExecutionRow.evaluation_id == evaluation_id)
                    .order_by(EvalExecutionRow.created_at, EvalExecutionRow.id)
                )
            )
            metrics = list(
                session.scalars(
                    select(MetricResultRow)
                    .where(MetricResultRow.evaluation_id == evaluation_id)
                    .order_by(MetricResultRow.metric_name)
                )
            )
            return executions, metrics

    @traced("db.evaluation.claim", component="persistence")
    def claim_evaluation_execution(
        self,
        *,
        worker_id: str,
        claimed_at: datetime,
        maximum_run_cost_usd: Decimal,
        evaluation_id: UUID | None = None,
    ) -> ClaimedEvaluationExecution | None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        with self._sessions.begin() as session:
            statement = select(JobRow).where(
                JobRow.status == "pending",
                JobRow.job_type == "evaluation_execution",
                JobRow.available_at <= claimed_at,
            )
            if evaluation_id is not None:
                statement = statement.where(
                    JobRow.payload["evaluation_id"].as_string() == str(evaluation_id)
                )
            job = session.scalar(
                statement.order_by(JobRow.priority, JobRow.available_at, JobRow.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            raw_execution_id = job.payload.get("execution_id")
            if not isinstance(raw_execution_id, str):
                raise RuntimeError("evaluation job is missing execution_id")
            execution_id = UUID(raw_execution_id)
            execution = session.scalar(
                select(EvalExecutionRow)
                .where(EvalExecutionRow.id == execution_id)
                .with_for_update()
            )
            if execution is None or execution.status != "intended":
                raise RuntimeError("evaluation execution is not claimable")
            batch = session.scalar(
                select(EvaluationBatchRow)
                .where(EvaluationBatchRow.id == execution.evaluation_id)
                .with_for_update()
            )
            case = session.get(EvalCaseRow, execution.eval_case_id)
            if batch is None or case is None:
                raise RuntimeError("evaluation job references missing batch or case")
            if batch.status not in {"queued", "running"}:
                raise RuntimeError("evaluation batch is not executable")
            spent = session.scalar(
                select(func.coalesce(func.sum(EvalExecutionRow.model_cost_usd), 0)).where(
                    EvalExecutionRow.evaluation_id == batch.id,
                    EvalExecutionRow.status.in_({"valid", "infrastructure_invalid", "failed"}),
                )
            )
            running = session.scalar(
                select(func.count()).where(
                    EvalExecutionRow.evaluation_id == batch.id,
                    EvalExecutionRow.status == "running",
                )
            )
            reserved = (
                Decimal(str(spent or 0)) + Decimal(int(running or 0) + 1) * maximum_run_cost_usd
            )
            if reserved > batch.maximum_total_cost_usd:
                raise RuntimeError("evaluation spend reservation exceeds the immutable batch cap")
            job.status = "processing"
            job.worker_id = worker_id
            job.claimed_at = claimed_at
            job.attempts += 1
            execution.status = "running"
            execution.started_at = claimed_at
            if batch.status == "queued":
                batch.status = "running"
                batch.started_at = claimed_at
            return ClaimedEvaluationExecution(
                job_id=job.id,
                execution_id=execution.id,
                evaluation_id=execution.evaluation_id,
                arm=execution.arm,
                case_manifest=cast(dict[str, object], case.case_manifest),
                attempt_number=job.attempts,
            )

    def attach_evaluation_run(self, *, execution_id: UUID, run_id: UUID) -> None:
        with self._sessions.begin() as session:
            execution = session.scalar(
                select(EvalExecutionRow)
                .where(EvalExecutionRow.id == execution_id)
                .with_for_update()
            )
            if execution is None or execution.status != "running" or execution.run_id is not None:
                raise RuntimeError("evaluation execution cannot attach this run")
            execution.run_id = run_id

    def evaluation_failure_context(self, run_id: UUID) -> EvaluationFailureContext:
        with self._sessions() as session:
            run = session.get(RunRow, run_id)
            if run is None:
                raise RuntimeError("evaluation run does not exist")
            failure = session.scalar(
                select(RunEventRow)
                .where(RunEventRow.run_id == run_id, RunEventRow.event_type == "worker.failed")
                .order_by(RunEventRow.sequence_no.desc())
                .limit(1)
            )
            actor_decisions = session.scalar(
                select(func.count()).where(
                    RunEventRow.run_id == run_id,
                    RunEventRow.event_type == "action.proposed",
                )
            )
            side_effects = session.scalar(
                select(func.count()).where(
                    RunEventRow.run_id == run_id,
                    RunEventRow.event_type == "action.executed",
                )
            )
            error_type = None
            if failure is not None:
                raw = failure.payload.get("error_type")
                error_type = raw if isinstance(raw, str) else None
            return EvaluationFailureContext(
                error_type=error_type,
                model_call_count=run.model_call_count,
                actor_decision_count=int(actor_decisions or 0),
                side_effect_count=int(side_effects or 0),
            )

    @traced("db.evaluation.runtime_finish", component="persistence")
    def finish_evaluation_runtime(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        execution_id: UUID,
        run_id: UUID,
        finished_at: datetime,
        infrastructure_invalid_reason: str | None,
    ) -> None:
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRow).where(JobRow.id == job_id).with_for_update())
            execution = session.scalar(
                select(EvalExecutionRow)
                .where(EvalExecutionRow.id == execution_id)
                .with_for_update()
            )
            run = session.get(RunRow, run_id)
            batch = (
                session.scalar(
                    select(EvaluationBatchRow)
                    .where(EvaluationBatchRow.id == execution.evaluation_id)
                    .with_for_update()
                )
                if execution is not None
                else None
            )
            if (
                job is None
                or job.status != "processing"
                or job.worker_id != worker_id
                or execution is None
                or execution.status != "running"
                or execution.run_id != run_id
                or run is None
                or batch is None
            ):
                raise RuntimeError("evaluation runtime completion lost job ownership")
            execution.model_cost_usd = run.model_cost_usd
            execution.finished_at = finished_at
            if infrastructure_invalid_reason is not None:
                execution.status = "infrastructure_invalid"
                execution.invalid_reason = infrastructure_invalid_reason
                remaining = session.scalar(
                    select(func.count()).where(
                        EvalExecutionRow.evaluation_id == batch.id,
                        EvalExecutionRow.status.in_({"intended", "running"}),
                    )
                )
                if int(remaining or 0) == 0:
                    batch.status = "completed"
                    batch.completed_at = finished_at
            else:
                session.add(
                    JobRow(
                        id=uuid7(),
                        job_type="oracle_score",
                        run_id=run_id,
                        status="pending",
                        priority=100,
                        attempts=0,
                        available_at=finished_at,
                        payload={
                            "evaluation_id": str(execution.evaluation_id),
                            "execution_id": str(execution.id),
                            "run_id": str(run_id),
                        },
                        created_at=finished_at,
                    )
                )
            job.status = "completed"

    def fail_evaluation_execution(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        execution_id: UUID,
        error: str,
        finished_at: datetime,
    ) -> None:
        with self._sessions.begin() as session:
            job = session.scalar(select(JobRow).where(JobRow.id == job_id).with_for_update())
            execution = session.scalar(
                select(EvalExecutionRow)
                .where(EvalExecutionRow.id == execution_id)
                .with_for_update()
            )
            if job is None or job.worker_id != worker_id or execution is None:
                raise RuntimeError("evaluation failure lost job ownership")
            job.status = "failed"
            job.last_error = error[:4000]
            execution.status = "failed"
            execution.finished_at = finished_at
            batch = session.get(EvaluationBatchRow, execution.evaluation_id)
            if batch is not None:
                batch.status = "failed"
                batch.completed_at = finished_at
                batch.last_error = error[:4000]

    @traced("db.approval.request", component="persistence")
    def create_approval(
        self,
        *,
        request: ApprovalRequest,
        decision: PolicyDecision,
    ) -> None:
        effect = request.effect
        with self._sessions.begin() as session:
            self._ensure_bundle(session, effect=effect, decision=decision)
            session.add(
                ApprovalRequestRow(
                    id=request.request_id,
                    run_id=request.run_id,
                    effect_proposal_id=effect.effect_id,
                    approved_context_hash=effect.approved_context_hash,
                    summary=request.summary,
                    status=request.status.value,
                    requested_at=request.created_at,
                    expires_at=request.expires_at,
                )
            )

    @traced("db.security_bundle.record", component="persistence")
    def record_bundle(self, *, effect: EffectProposal, decision: PolicyDecision) -> None:
        with self._sessions.begin() as session:
            self._ensure_bundle(session, effect=effect, decision=decision)

    @traced("db.approval.approve", component="persistence")
    def approve(
        self,
        request: ApprovalRequest,
        grant: ApprovalGrant,
        *,
        decided_at: datetime,
    ) -> None:
        payload = grant.payload
        with self._sessions.begin() as session:
            row = session.get(ApprovalRequestRow, request.request_id)
            if row is None or row.status != "PENDING":
                raise RuntimeError("persisted approval request is no longer pending")
            row.status = "APPROVED"
            row.decided_at = decided_at
            row.decision_source = "public_demo_user"
            effect = session.get(EffectProposalRow, request.effect.effect_id)
            if effect is None or effect.status != "APPROVAL_PENDING":
                raise RuntimeError("persisted effect is not awaiting approval")
            effect.status = "AUTHORIZED"
            session.add(
                ApprovalGrantRow(
                    id=payload.grant_id,
                    run_id=payload.run_id,
                    approval_request_id=request.request_id,
                    effect_proposal_id=payload.effect_proposal_id,
                    context_hash=payload.approved_context_hash,
                    idempotency_key=payload.idempotency_key,
                    capability_hash=grant.capability_hash,
                    capability_payload=payload.model_dump(mode="json"),
                    signature=grant.signature,
                    status=grant.status.value,
                    issued_at=payload.issued_at,
                    expires_at=payload.expires_at,
                    used_at=grant.consumed_at,
                )
            )

    def reject(self, request: ApprovalRequest, *, decided_at: datetime) -> None:
        with self._sessions.begin() as session:
            row = session.get(ApprovalRequestRow, request.request_id)
            if row is None or row.status != "PENDING":
                raise RuntimeError("persisted approval request is no longer pending")
            row.status = request.status.value
            row.decided_at = decided_at
            row.decision_source = "public_demo_user"

    @traced("db.approval.consume", component="persistence")
    def consume_grant(self, grant: ApprovalGrant, *, used_at: datetime) -> None:
        with self._sessions.begin() as session:
            row = session.get(ApprovalGrantRow, grant.payload.grant_id)
            if row is None or row.status != "ACTIVE":
                raise RuntimeError("persisted approval grant is not active")
            row.status = "CONSUMED"
            row.used_at = used_at

    def mark_booking_verified(
        self,
        *,
        run_id: UUID,
        verified_at: datetime,
    ) -> bool:
        with self._sessions.begin() as session:
            booking = session.scalar(
                select(ReplacementBookingRow)
                .where(
                    ReplacementBookingRow.run_id == run_id,
                    ReplacementBookingRow.status == "confirmed",
                )
                .with_for_update()
            )
            if booking is None:
                return False
            side_effect = session.scalar(
                select(SideEffectRow)
                .where(
                    SideEffectRow.run_id == run_id,
                    SideEffectRow.effect_proposal_id == booking.effect_proposal_id,
                )
                .with_for_update()
            )
            if side_effect is None:
                return False
            if booking.status != "confirmed" or side_effect.status not in {
                "COMMITTED",
                "VERIFIED",
            }:
                raise RuntimeError("only a committed confirmed booking can be verified")
            booking.verified_at = booking.verified_at or verified_at
            side_effect.verified_at = side_effect.verified_at or verified_at
            side_effect.status = "VERIFIED"
            return True

    @staticmethod
    def _effect_row(effect: EffectProposal) -> EffectProposalRow:
        return EffectProposalRow(
            id=effect.effect_id,
            run_id=effect.action.run_id,
            action_id=effect.action.action_id,
            derived_origin=effect.origin or "runtime://local",
            derived_effect_class=effect.effect_class.value,
            trusted_target_kind=effect.trusted_target_kind.value,
            contract_hash=effect.contract_hash,
            semantic_context=effect.context.model_dump(mode="json"),
            approved_context_hash=effect.approved_context_hash,
            idempotency_key=effect.idempotency_key,
            status="APPROVAL_PENDING",
            created_at=effect.derived_at,
        )

    def _ensure_bundle(
        self,
        session: Session,
        *,
        effect: EffectProposal,
        decision: PolicyDecision,
    ) -> None:
        action = effect.action
        if session.get(ActionProposalRow, action.action_id) is not None:
            return
        session.add(
            ActionProposalRow(
                id=action.action_id,
                run_id=action.run_id,
                step_number=action.step_number,
                observation_hash=action.observation_hash,
                tool=action.tool.value,
                proposal_payload=action.model_dump(mode="json"),
                grounding_confidence=action.grounding_confidence,
                created_at=effect.derived_at,
            )
        )
        session.flush()
        session.add(self._effect_row(effect))
        session.flush()
        session.add(
            PolicyDecisionRow(
                id=decision.decision_id,
                run_id=action.run_id,
                action_id=action.action_id,
                effect_proposal_id=effect.effect_id,
                decision=decision.verdict.value,
                rule_id=decision.rule_id,
                context_hash=decision.context_hash,
                created_at=decision.evaluated_at,
            )
        )
        session.flush()

    @staticmethod
    def _append_event_locked(
        session: Session,
        *,
        run_id: UUID,
        event_type: str,
        payload: dict[str, object],
        created_at: datetime,
    ) -> RunEventRow:
        last_sequence = session.scalar(
            select(func.max(RunEventRow.sequence_no)).where(RunEventRow.run_id == run_id)
        )
        event = RunEventRow(
            run_id=run_id,
            sequence_no=int(last_sequence or 0) + 1,
            event_type=event_type,
            schema_version="1.0.0",
            step_id=None,
            payload=payload,
            payload_hash=sha256_hex(payload),
            created_at=created_at,
        )
        session.add(event)
        return event
