"""Concurrency-safe repositories for runtime state, approvals, and jobs."""
# pyright: reportUnknownVariableType=false

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

from sqlalchemy import Select, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Update
from trust_contracts import (
    ActionProposal,
    ApprovalGrant,
    ApprovalRequest,
    EffectProposal,
    PolicyDecision,
    TaskContract,
    sha256_hex,
    uuid7,
)

from .errors import ConcurrentStateError, ImmutableRecordError, RecordNotFoundError
from .models import (
    TERMINAL_RUN_STATES,
    ActionProposalRow,
    ApprovalGrantRow,
    ApprovalRequestRow,
    DemoSessionRow,
    EffectProposalRow,
    JobRow,
    PolicyDecisionRow,
    RunEventRow,
    RunRow,
    TaskContractRow,
)


@dataclass(frozen=True, slots=True)
class NewRun:
    """Sealed fields needed to create a run source-of-truth record."""

    run_id: UUID
    contract_id: UUID
    mode: str
    scenario_id: str
    scenario_seed: int
    fixture_version: str
    oracle_version: str
    manifest_hash: str
    retention_class: str
    expected_terminal_outcome: str
    fault_manifest_version: str
    fault_id: str | None = None
    fault_parameters: dict[str, Any] = field(default_factory=dict)
    session_id: UUID | None = None
    model_provider: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    status: str = "CREATED"


@dataclass(frozen=True, slots=True)
class EventInput:
    event_type: str
    payload: dict[str, Any]
    step_id: UUID | None = None
    schema_version: str = "1.0.0"


@dataclass(frozen=True, slots=True)
class NewJob:
    job_type: str
    available_at: datetime
    payload: dict[str, Any]
    run_id: UUID | None = None
    priority: int = 100
    job_id: UUID = field(default_factory=uuid7)


class RunRepository:
    """Persist contracts, sessions, and sealed run metadata.

    Each mutating method owns a short transaction. Callers must perform model,
    browser, network, and object-storage work before entering these methods.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_session(
        self,
        *,
        session_id: UUID,
        public_token_hash: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> DemoSessionRow:
        row = DemoSessionRow(
            id=session_id,
            public_token_hash=public_token_hash,
            created_at=created_at,
            expires_at=expires_at,
            live_run_count=0,
        )
        async with self._session.begin():
            self._session.add(row)
            await self._session.flush()
        return row

    async def put_contract(self, contract: TaskContract) -> TaskContractRow:
        """Insert the immutable canonical contract or return its exact existing row."""

        async with self._session.begin():
            existing = await self._session.scalar(
                select(TaskContractRow).where(TaskContractRow.id == contract.contract_id)
            )
            payload = contract.model_dump(mode="json")
            if existing is not None:
                if (
                    existing.content_hash != contract.content_hash
                    or existing.canonical_payload != payload
                ):
                    raise ImmutableRecordError("task contract identifier already has other content")
                return existing
            row = TaskContractRow(
                id=contract.contract_id,
                schema_version=contract.schema_version,
                content_hash=contract.content_hash,
                canonical_payload=payload,
            )
            self._session.add(row)
            await self._session.flush()
        return row

    async def create_run(self, new_run: NewRun) -> RunRow:
        row = RunRow(
            id=new_run.run_id,
            session_id=new_run.session_id,
            contract_id=new_run.contract_id,
            mode=new_run.mode,
            status=new_run.status,
            scenario_id=new_run.scenario_id,
            scenario_seed=new_run.scenario_seed,
            fixture_version=new_run.fixture_version,
            fault_id=new_run.fault_id,
            fault_parameters=new_run.fault_parameters,
            oracle_version=new_run.oracle_version,
            manifest_hash=new_run.manifest_hash,
            retention_class=new_run.retention_class,
            expected_terminal_outcome=new_run.expected_terminal_outcome,
            model_provider=new_run.model_provider,
            model_id=new_run.model_id,
            prompt_version=new_run.prompt_version,
            fault_manifest_version=new_run.fault_manifest_version,
            step_count=0,
            model_call_count=0,
            model_cost_usd=Decimal("0"),
        )
        async with self._session.begin():
            self._session.add(row)
            await self._session.flush()
        return row

    async def get(self, run_id: UUID, *, for_update: bool = False) -> RunRow:
        statement: Select[tuple[RunRow]] = select(RunRow).where(RunRow.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        row = await self._session.scalar(statement)
        if row is None:
            raise RecordNotFoundError("run does not exist")
        return row

    async def transition_with_event(
        self,
        *,
        run_id: UUID,
        expected_status: str,
        target_status: str,
        reason: str,
        occurred_at: datetime,
        step_id: UUID | None = None,
    ) -> RunEventRow:
        """Lock the run, persist its transition, then append the trace event."""

        async with self._session.begin():
            run = await self._session.scalar(
                select(RunRow).where(RunRow.id == run_id).with_for_update()
            )
            if run is None:
                raise RecordNotFoundError("run does not exist")
            if run.status in TERMINAL_RUN_STATES:
                raise ImmutableRecordError(f"terminal run is immutable: {run.status}")
            if run.status != expected_status:
                raise ConcurrentStateError(
                    f"run state changed concurrently: expected {expected_status}, got {run.status}"
                )
            run.status = target_status
            if target_status in TERMINAL_RUN_STATES:
                run.finished_at = occurred_at
                run.terminal_reason = reason
            payload: dict[str, Any] = {
                "from_state": expected_status,
                "to_state": target_status,
                "reason": reason,
            }
            event = await EventRepository.append_locked(
                self._session,
                run=run,
                item=EventInput(
                    event_type="run.state_transition",
                    payload=payload,
                    step_id=step_id,
                ),
                created_at=occurred_at,
            )
            await self._session.flush()
        return event

    async def delete_expired_public_run(self, run_id: UUID) -> bool:
        """Invoke the database-owned retention guard; direct run deletion is forbidden."""

        async with self._session.begin():
            deleted = await self._session.scalar(
                text("select runtime.delete_expired_public_run(:run_id)"),
                {"run_id": run_id},
            )
        return bool(deleted)


class EventRepository:
    """Append and page the immutable per-run flight recorder."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(self, *, run_id: UUID, item: EventInput, created_at: datetime) -> RunEventRow:
        async with self._session.begin():
            run = await self._session.scalar(
                select(RunRow).where(RunRow.id == run_id).with_for_update()
            )
            if run is None:
                raise RecordNotFoundError("run does not exist")
            if run.status in TERMINAL_RUN_STATES:
                raise ImmutableRecordError(f"terminal run is immutable: {run.status}")
            event = await self.append_locked(
                self._session, run=run, item=item, created_at=created_at
            )
            await self._session.flush()
        return event

    @staticmethod
    async def append_locked(
        session: AsyncSession,
        *,
        run: RunRow,
        item: EventInput,
        created_at: datetime,
    ) -> RunEventRow:
        """Append after the caller has locked ``runs.id`` to serialize sequence numbers."""

        current = await session.scalar(
            select(func.coalesce(func.max(RunEventRow.sequence_no), 0)).where(
                RunEventRow.run_id == run.id
            )
        )
        sequence_no = int(current or 0) + 1
        event = RunEventRow(
            run_id=run.id,
            sequence_no=sequence_no,
            event_type=item.event_type,
            schema_version=item.schema_version,
            step_id=item.step_id,
            payload=item.payload,
            payload_hash=sha256_hex(item.payload),
            created_at=created_at,
        )
        session.add(event)
        return event

    async def list_after(
        self, *, run_id: UUID, after_sequence: int = 0, limit: int = 200
    ) -> list[RunEventRow]:
        if not 1 <= limit <= 1000:
            raise ValueError("event page limit must be between 1 and 1000")
        result = await self._session.scalars(
            select(RunEventRow)
            .where(
                RunEventRow.run_id == run_id,
                RunEventRow.sequence_no > after_sequence,
            )
            .order_by(RunEventRow.sequence_no)
            .limit(limit)
        )
        return list(result)


class EffectRepository:
    """Persist the actor proposal and independently derived security records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record_bundle(
        self,
        *,
        action: ActionProposal,
        effect: EffectProposal,
        decision: PolicyDecision,
    ) -> tuple[ActionProposalRow, EffectProposalRow, PolicyDecisionRow]:
        if effect.action.action_id != action.action_id:
            raise ValueError("effect is not bound to the supplied action")
        if decision.effect_id != effect.effect_id:
            raise ValueError("policy decision is not bound to the supplied effect")

        action_row = ActionProposalRow(
            id=action.action_id,
            run_id=action.run_id,
            step_number=action.step_number,
            observation_hash=action.observation_hash,
            tool=action.tool.value,
            proposal_payload=action.model_dump(mode="json"),
            grounding_confidence=action.grounding_confidence,
            created_at=effect.derived_at,
        )
        effect_row = EffectProposalRow(
            id=effect.effect_id,
            run_id=action.run_id,
            action_id=action.action_id,
            derived_origin=effect.origin or "runtime://local",
            derived_effect_class=effect.effect_class.value,
            trusted_target_kind=effect.trusted_target_kind.value,
            contract_hash=effect.contract_hash,
            semantic_context=effect.context.model_dump(mode="json"),
            approved_context_hash=effect.approved_context_hash,
            idempotency_key=effect.idempotency_key,
            status="PROPOSED",
            created_at=effect.derived_at,
        )
        decision_row = PolicyDecisionRow(
            id=decision.decision_id,
            run_id=action.run_id,
            action_id=action.action_id,
            effect_proposal_id=effect.effect_id,
            decision=decision.verdict.value,
            rule_id=decision.rule_id,
            context_hash=decision.context_hash,
            created_at=decision.evaluated_at,
        )
        async with self._session.begin():
            self._session.add_all((action_row, effect_row, decision_row))
            await self._session.flush()
        return action_row, effect_row, decision_row


class ApprovalRepository:
    """Persist exact approval requests and their server-only signed grants."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_request(self, request: ApprovalRequest) -> ApprovalRequestRow:
        effect = request.effect
        row = ApprovalRequestRow(
            id=request.request_id,
            run_id=request.run_id,
            effect_proposal_id=effect.effect_id,
            approved_context_hash=effect.approved_context_hash,
            summary=request.summary,
            status=request.status.value,
            requested_at=request.created_at,
            expires_at=request.expires_at,
        )
        async with self._session.begin():
            effect_row = await self._session.scalar(
                select(EffectProposalRow)
                .where(EffectProposalRow.id == effect.effect_id)
                .with_for_update()
            )
            if effect_row is None:
                raise RecordNotFoundError("effect proposal does not exist")
            if (
                effect_row.run_id != request.run_id
                or effect_row.approved_context_hash != effect.approved_context_hash
            ):
                raise ConcurrentStateError("approval request no longer matches effect proposal")
            effect_row.status = "APPROVAL_PENDING"
            self._session.add(row)
            await self._session.flush()
        return row

    async def approve_and_store_grant(
        self,
        *,
        request_id: UUID,
        grant: ApprovalGrant,
        decision_source: str,
        decided_at: datetime,
    ) -> ApprovalGrantRow:
        payload = grant.payload
        if payload.approval_request_id != request_id:
            raise ValueError("grant does not bind the supplied approval request")
        if grant.status.value != "ACTIVE" or grant.consumed_at is not None:
            raise ValueError("only a fresh active grant can be stored")
        if not decision_source.strip():
            raise ValueError("decision_source must not be empty")
        row = ApprovalGrantRow(
            id=payload.grant_id,
            run_id=payload.run_id,
            approval_request_id=request_id,
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
        expired = False
        async with self._session.begin():
            request = await self._session.scalar(
                select(ApprovalRequestRow)
                .where(ApprovalRequestRow.id == request_id)
                .with_for_update()
            )
            if request is None:
                raise RecordNotFoundError("approval request does not exist")
            effect = await self._session.scalar(
                select(EffectProposalRow)
                .where(EffectProposalRow.id == request.effect_proposal_id)
                .with_for_update()
            )
            if effect is None:
                raise RecordNotFoundError("effect proposal does not exist")
            if request.status != "PENDING":
                raise ConcurrentStateError(f"approval request is already {request.status}")
            if decided_at >= request.expires_at:
                request.status = "EXPIRED"
                request.decided_at = decided_at
                request.decision_source = "security_clock"
                effect.status = "APPROVAL_EXPIRED"
                expired = True
            elif (
                request.run_id != payload.run_id
                or request.effect_proposal_id != payload.effect_proposal_id
                or request.approved_context_hash != payload.approved_context_hash
                or effect.run_id != payload.run_id
                or effect.approved_context_hash != payload.approved_context_hash
                or effect.idempotency_key != payload.idempotency_key
                or payload.issued_at < request.requested_at
                or payload.issued_at > decided_at
                or payload.expires_at != request.expires_at
            ):
                raise ConcurrentStateError("grant scope differs from the approval request")
            else:
                request.status = "APPROVED"
                request.decided_at = decided_at
                request.decision_source = decision_source
                effect.status = "AUTHORIZED"
                self._session.add(row)
            await self._session.flush()
        if expired:
            raise ConcurrentStateError("approval request expired before approval")
        return row

    async def reject(
        self, *, request_id: UUID, decision_source: str, decided_at: datetime
    ) -> ApprovalRequestRow:
        async with self._session.begin():
            request = await self._session.scalar(
                select(ApprovalRequestRow)
                .where(ApprovalRequestRow.id == request_id)
                .with_for_update()
            )
            if request is None:
                raise RecordNotFoundError("approval request does not exist")
            if request.status != "PENDING":
                raise ConcurrentStateError(f"approval request is already {request.status}")
            request.status = "REJECTED"
            request.decided_at = decided_at
            request.decision_source = decision_source
            effect = await self._session.scalar(
                select(EffectProposalRow)
                .where(EffectProposalRow.id == request.effect_proposal_id)
                .with_for_update()
            )
            if effect is None:
                raise RecordNotFoundError("effect proposal does not exist")
            effect.status = "APPROVAL_REJECTED"
            await self._session.flush()
        return request


class JobRepository:
    """PostgreSQL-backed worker queue using non-blocking row claims."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue(self, item: NewJob) -> JobRow:
        row = JobRow(
            id=item.job_id,
            job_type=item.job_type,
            run_id=item.run_id,
            status="pending",
            priority=item.priority,
            attempts=0,
            available_at=item.available_at,
            payload=item.payload,
        )
        async with self._session.begin():
            self._session.add(row)
            await self._session.flush()
        return row

    @staticmethod
    def claim_statement(
        *, worker_id: str, claimed_at: datetime, job_types: tuple[str, ...] = ()
    ) -> Update:
        candidate_query = select(JobRow.id).where(
            JobRow.status == "pending", JobRow.available_at <= claimed_at
        )
        if job_types:
            candidate_query = candidate_query.where(JobRow.job_type.in_(job_types))
        candidate = (
            candidate_query.order_by(JobRow.priority, JobRow.available_at, JobRow.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
            .cte("claimable_job")
        )
        return (
            update(JobRow)
            .where(JobRow.id == candidate.c.id)
            .values(
                status="processing",
                worker_id=worker_id,
                claimed_at=claimed_at,
                attempts=JobRow.attempts + 1,
            )
            .returning(JobRow)
        )

    async def claim_next(
        self,
        *,
        worker_id: str,
        claimed_at: datetime,
        job_types: tuple[str, ...] = (),
    ) -> JobRow | None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        async with self._session.begin():
            result = await self._session.execute(
                self.claim_statement(
                    worker_id=worker_id,
                    claimed_at=claimed_at,
                    job_types=job_types,
                )
            )
            row = result.scalar_one_or_none()
        return cast(JobRow | None, row)

    async def complete(self, *, job_id: UUID, worker_id: str) -> JobRow:
        async with self._session.begin():
            row = await self._session.scalar(
                select(JobRow).where(JobRow.id == job_id).with_for_update()
            )
            if row is None:
                raise RecordNotFoundError("job does not exist")
            if row.status != "processing" or row.worker_id != worker_id:
                raise ConcurrentStateError("job is not owned by this worker")
            row.status = "completed"
            await self._session.flush()
        return row

    async def retry(
        self,
        *,
        job_id: UUID,
        worker_id: str,
        available_at: datetime,
        error: str,
    ) -> JobRow:
        async with self._session.begin():
            row = await self._session.scalar(
                select(JobRow).where(JobRow.id == job_id).with_for_update()
            )
            if row is None:
                raise RecordNotFoundError("job does not exist")
            if row.status != "processing" or row.worker_id != worker_id:
                raise ConcurrentStateError("job is not owned by this worker")
            row.status = "pending"
            row.worker_id = None
            row.claimed_at = None
            row.available_at = available_at
            row.last_error = error[:4000]
            await self._session.flush()
        return row
