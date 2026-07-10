"""Runtime service boundary with sealed manifests and PostgreSQL-backed public state."""

import asyncio
import copy
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path
from threading import RLock
from typing import Literal, cast
from uuid import UUID

from trust_contracts import (
    ApprovalGrant,
    ApprovalRequest,
    AuthorizedAction,
    BookingCommitContext,
    EffectProposal,
    ModelRunRecord,
    PolicyDecision,
    RunManifest,
    RunMode,
    RunState,
    SecurityClock,
    TaskContract,
    uuid7,
)

from .approvals import ApprovalAuthority
from .artifacts import ArtifactRecord, LocalArtifactStore, S3ArtifactStore
from .config import AgentProvider, ObjectStorageBackend, RuntimeSettings, StateStoreBackend
from .errors import (
    EvaluationConfigurationError,
    EvaluationNotFoundError,
    EvaluationSpendCapError,
    PolicyDeniedError,
    RunNotFoundError,
    SessionNotFoundError,
)
from .persistence.runtime_store import PostgresRuntimeStore, StoredEvaluation, StoredRun
from .policy import booking_constraint_results
from .schemas import (
    ApprovalResponse,
    CancelRunResponse,
    EvaluationExecutionResponse,
    EvaluationResponse,
    EvaluationResultsResponse,
    MetricResultResponse,
    RunResponse,
    SessionResponse,
)
from .state_machine import RunStateMachine
from .telemetry import current_trace_id


@dataclass(frozen=True, slots=True)
class DemoSessionRecord:
    session_id: UUID
    token_hash: str
    created_at: datetime
    expires_at: datetime


@dataclass(slots=True)
class RunRecord:
    manifest: RunManifest
    machine: RunStateMachine
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    sequence: int
    event_type: str
    payload: dict[str, object]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class EvaluationRunHandle:
    run: RunResponse
    session_token: str


class RuntimeService:
    def __init__(self, *, settings: RuntimeSettings, clock: SecurityClock) -> None:
        self.settings = settings
        self.clock = clock
        self._sessions: dict[UUID, DemoSessionRecord] = {}
        self._sessions_by_token: dict[str, UUID] = {}
        self._runs: dict[UUID, RunRecord] = {}
        self._events: dict[UUID, list[RuntimeEvent]] = {}
        self._approval_waiters: dict[UUID, asyncio.Event] = {}
        self._worker_tasks: dict[UUID, asyncio.Task[RunState]] = {}
        self._evaluation_run_tokens: dict[UUID, str] = {}
        self.store = (
            PostgresRuntimeStore(settings)
            if settings.state_store_backend is StateStoreBackend.POSTGRES
            else None
        )
        self.approvals = ApprovalAuthority(
            signing_key=settings.approval_hmac_secret.get_secret_value().encode(),
            clock=clock,
            default_ttl_seconds=settings.approval_ttl_seconds,
        )
        if settings.object_storage_backend is ObjectStorageBackend.S3:
            assert settings.object_storage_bucket is not None
            assert settings.object_storage_endpoint is not None
            assert settings.object_storage_access_key is not None
            assert settings.object_storage_secret_key is not None
            self.artifacts = S3ArtifactStore(
                bucket=settings.object_storage_bucket,
                endpoint_url=settings.object_storage_endpoint,
                access_key=settings.object_storage_access_key.get_secret_value(),
                secret_key=settings.object_storage_secret_key.get_secret_value(),
                prefix=settings.object_storage_path,
                signing_key=settings.artifact_signing_secret.get_secret_value().encode(),
                clock=clock,
            )
        else:
            self.artifacts = LocalArtifactStore(
                root=settings.artifact_storage_dir,
                signing_key=settings.artifact_signing_secret.get_secret_value().encode(),
                clock=clock,
            )
        self._lock = RLock()

    def create_session(self) -> SessionResponse:
        now = self.clock.now()
        raw_token = secrets.token_urlsafe(32)
        token_hash = self._token_hash(raw_token)
        session = DemoSessionRecord(
            session_id=uuid7(),
            token_hash=token_hash,
            created_at=now,
            expires_at=now + timedelta(seconds=self.settings.public_session_ttl_seconds),
        )
        with self._lock:
            self._sessions[session.session_id] = session
            self._sessions_by_token[token_hash] = session.session_id
        if self.store is not None:
            self.store.create_session(
                session_id=session.session_id,
                token_hash=token_hash,
                created_at=session.created_at,
                expires_at=session.expires_at,
            )
        return SessionResponse(
            session_id=session.session_id,
            session_token=raw_token,
            expires_at=session.expires_at,
        )

    def create_run(
        self,
        *,
        session_token: str,
        contract: TaskContract,
        mode: RunMode = RunMode.PROTECTED,
        scenario_id: str = "disrupted_trip_v1",
        scenario_seed: int = 1001,
        fault_id: str | None = None,
        internal_evaluation: bool = False,
        expected_terminal_outcome: RunState = RunState.SUCCEEDED,
    ) -> RunResponse:
        session = self.authenticate_session(session_token)
        configured_origins = set(self.settings.browser_allowed_origins)
        if not set(contract.allowed_origins).issubset(configured_origins):
            raise PolicyDeniedError(
                "task contract includes an origin outside the runtime browser egress allowlist"
            )
        if mode is RunMode.BASELINE and not internal_evaluation:
            raise PolicyDeniedError("baseline ablation is restricted to operator evaluations")
        now = self.clock.now()
        run_id = uuid7()
        model_id = self.settings.agent_model or "deterministic-mock-v1"
        effective_mode = RunMode.MOCK if self.settings.agent_provider.value == "mock" else mode
        manifest = RunManifest(
            run_id=run_id,
            session_id=session.session_id,
            mode=effective_mode,
            task_contract=contract,
            scenario_id=scenario_id,
            scenario_seed=scenario_seed,
            fixture_version="disrupted-trip-v1",
            fault_manifest_version="1.0.0",
            fault_id=fault_id,
            fault_parameters={},
            model=ModelRunRecord(
                provider=self.settings.agent_provider.value,
                model_id=model_id,
                effective_parameters={
                    "temperature": self.settings.agent_temperature,
                    "max_output_tokens": self.settings.agent_max_output_tokens,
                },
                prompt_version="actor-v1",
                price_table_version=self.settings.model_price_table_version,
            ),
            sandbox_snapshot_ref=f"sandbox:{scenario_id}:{scenario_seed}",
            oracle_case_ref=f"sealed:{scenario_id}:{scenario_seed}",
            oracle_version="oracle-v1",
            expected_terminal_outcome=expected_terminal_outcome,
            created_at=now,
        )
        record = RunRecord(
            manifest=manifest,
            machine=RunStateMachine(run_id=run_id, clock=self.clock),
            created_at=now,
        )
        if self.store is not None:
            self.store.create_run(manifest, created_at=now)
        with self._lock:
            self._runs[run_id] = record
            self._events[run_id] = [
                RuntimeEvent(1, "run.created", {"status": RunState.CREATED.value}, now)
            ]
        return self._response(record)

    def get_run(self, *, session_token: str, run_id: UUID) -> RunResponse:
        session = self.authenticate_session(session_token)
        stored = self.store.load_run(run_id) if self.store is not None else None
        if stored is not None:
            if stored.session_id != session.session_id:
                raise RunNotFoundError("run does not belong to this session")
            with self._lock:
                record = self._runs.get(run_id)
            if record is None:
                return self._stored_response(stored)
            record.machine.state = RunState(stored.status)
        else:
            record = self._run(run_id)
            if record.manifest.session_id != session.session_id:
                raise RunNotFoundError("run does not belong to this session")
        return self._response(record)

    def cancel_run(self, *, session_token: str, run_id: UUID) -> CancelRunResponse:
        session = self.authenticate_session(session_token)
        record = self._run(run_id)
        if record.manifest.session_id != session.session_id:
            raise RunNotFoundError("run does not belong to this session")
        record.machine.cancel(reason="cancelled by session owner")
        self._append_event(
            run_id,
            "run.state_transition",
            {"status": record.machine.state.value, "reason": "cancelled by session owner"},
        )
        return CancelRunResponse(run_id=run_id, status=record.machine.state)

    def events_after(
        self, *, session_token: str, run_id: UUID, after: int = 0
    ) -> list[RuntimeEvent]:
        session = self.authenticate_session(session_token)
        stored = self.store.load_run(run_id) if self.store is not None else None
        if stored is not None:
            if stored.session_id != session.session_id:
                raise RunNotFoundError("run does not belong to this session")
        else:
            record = self._run(run_id)
            if record.manifest.session_id != session.session_id:
                raise RunNotFoundError("run does not belong to this session")
        if after < 0:
            raise ValueError("event cursor must be non-negative")
        if self.store is not None:
            return [
                RuntimeEvent(
                    sequence=row.sequence_no,
                    event_type=row.event_type,
                    payload=row.payload,
                    occurred_at=row.created_at,
                )
                for row in self.store.events_after(run_id, after)
            ]
        with self._lock:
            return [event for event in self._events.get(run_id, []) if event.sequence > after]

    def create_approval(
        self,
        *,
        run_id: UUID,
        effect: EffectProposal,
        decision: PolicyDecision,
        summary: str,
    ) -> ApprovalRequest:
        record = self._run(run_id)
        if record.manifest.run_id != effect.action.run_id:
            raise RunNotFoundError("approval effect does not belong to the run")
        request = self.approvals.request(effect=effect, summary=summary)
        if self.store is not None:
            self.store.create_approval(request=request, decision=decision)
        self._approval_waiters[request.request_id] = asyncio.Event()
        self._append_event(
            run_id,
            "approval.requested",
            {"approval_id": str(request.request_id), "effect_id": str(effect.effect_id)},
        )
        return request

    def approve(
        self,
        *,
        session_token: str,
        approval_id: UUID,
        expected_context_hash: str | None = None,
    ) -> ApprovalGrant:
        request = self.approvals.get_request(approval_id)
        self.get_run(session_token=session_token, run_id=request.run_id)
        normalized_context_hash = (
            expected_context_hash.strip('"') if expected_context_hash is not None else None
        )
        if (
            normalized_context_hash is not None
            and normalized_context_hash != request.effect.approved_context_hash
        ):
            from .errors import ApprovalStaleError

            raise ApprovalStaleError("approval preview no longer matches the pending effect")
        grant = self.approvals.approve(
            request_id=approval_id, contract_hash=request.effect.contract_hash
        )
        if self.store is not None:
            self.store.approve(request, grant, decided_at=self.clock.now())
        self._append_event(request.run_id, "approval.approved", {"approval_id": str(approval_id)})
        waiter = self._approval_waiters.get(approval_id)
        if waiter is not None:
            waiter.set()
        return grant

    def reject_approval(self, *, session_token: str, approval_id: UUID) -> ApprovalRequest:
        request = self.approvals.get_request(approval_id)
        self.get_run(session_token=session_token, run_id=request.run_id)
        rejected = self.approvals.reject(approval_id)
        if self.store is not None:
            self.store.reject(rejected, decided_at=self.clock.now())
        self._append_event(request.run_id, "approval.rejected", {"approval_id": str(approval_id)})
        waiter = self._approval_waiters.get(approval_id)
        if waiter is not None:
            waiter.set()
        return rejected

    async def wait_for_approval(self, approval_id: UUID, timeout_seconds: int) -> ApprovalRequest:
        waiter = self._approval_waiters.get(approval_id)
        if waiter is None:
            return self.approvals.get_request(approval_id)
        try:
            await asyncio.wait_for(waiter.wait(), timeout=timeout_seconds)
        except TimeoutError:
            return self.approvals.expire(approval_id)
        finally:
            self._approval_waiters.pop(approval_id, None)
        return self.approvals.get_request(approval_id)

    def run_machine(self, run_id: UUID) -> RunStateMachine:
        return self._run(run_id).machine

    def append_worker_event(
        self, run_id: UUID, event_type: str, payload: dict[str, object]
    ) -> None:
        self._append_event(run_id, event_type, payload)

    def record_security_bundle(self, *, effect: EffectProposal, decision: PolicyDecision) -> None:
        if self.store is not None:
            self.store.record_bundle(effect=effect, decision=decision)

    def consume_approval(
        self,
        *,
        grant: ApprovalGrant,
        effect: EffectProposal,
        policy_decision: PolicyDecision,
        contract_hash: str,
    ) -> AuthorizedAction:
        authorized = self.approvals.consume_and_authorize(
            grant=grant,
            effect=effect,
            policy_decision=policy_decision,
            contract_hash=contract_hash,
        )
        if self.store is not None:
            self.store.consume_grant(grant, used_at=authorized.authorized_at)
        return authorized

    def authorize_active_approval(
        self,
        *,
        grant: ApprovalGrant,
        effect: EffectProposal,
        policy_decision: PolicyDecision,
        contract_hash: str,
    ) -> AuthorizedAction:
        return self.approvals.authorize_active_grant(
            grant=grant,
            effect=effect,
            policy_decision=policy_decision,
            contract_hash=contract_hash,
        )

    def record_screenshot(self, *, run_id: UUID, content: bytes, source_url: str) -> ArtifactRecord:
        self._run(run_id)
        sequence_no = len(self._events.get(run_id, [])) + 1
        artifact = self.artifacts.put_screenshot(
            run_id=run_id,
            content=content,
            source_url=source_url,
            sequence_no=sequence_no,
        )
        if self.store is not None:
            self.store.record_artifact(artifact)
        self._append_event(
            run_id,
            "artifact.recorded",
            {
                "artifact_id": str(artifact.artifact_id),
                "kind": artifact.kind,
                "sha256": artifact.sha256,
                "byte_size": artifact.byte_size,
                "redaction_status": artifact.redaction_status,
            },
        )
        return artifact

    def mark_booking_verified(self, *, run_id: UUID) -> bool:
        if self.store is None:
            return False
        return self.store.mark_booking_verified(
            run_id=run_id,
            verified_at=self.clock.now(),
        )

    def start_run(self, run_id: UUID) -> asyncio.Task[RunState]:
        from .worker import start_browser_worker

        task = start_browser_worker(service=self, run_id=run_id)
        self._worker_tasks[run_id] = task
        task.add_done_callback(lambda _task: self._worker_tasks.pop(run_id, None))
        return task

    def create_evaluation_run(
        self,
        *,
        contract: TaskContract,
        mode: RunMode,
        scenario_id: str,
        scenario_seed: int,
        fault_id: str | None,
        expected_terminal_outcome: RunState,
    ) -> EvaluationRunHandle:
        if mode not in {RunMode.BASELINE, RunMode.PROTECTED}:
            raise EvaluationConfigurationError("evaluation arm must be baseline or protected")
        session = self.create_session()
        run = self.create_run(
            session_token=session.session_token,
            contract=contract,
            mode=mode,
            scenario_id=scenario_id,
            scenario_seed=scenario_seed,
            fault_id=fault_id,
            internal_evaluation=True,
            expected_terminal_outcome=expected_terminal_outcome,
        )
        self._evaluation_run_tokens[run.run_id] = session.session_token
        return EvaluationRunHandle(run=run, session_token=session.session_token)

    def decide_evaluation_approval(
        self, *, request: ApprovalRequest, effect: EffectProposal
    ) -> bool | None:
        token = self._evaluation_run_tokens.get(effect.action.run_id)
        if token is None:
            return None
        approved = self.evaluation_approval_decision(effect=effect)
        assert approved is not None
        if approved:
            self.approve(
                session_token=token,
                approval_id=request.request_id,
                expected_context_hash=effect.approved_context_hash,
            )
        else:
            self.reject_approval(session_token=token, approval_id=request.request_id)
        return approved

    def evaluation_approval_decision(self, *, effect: EffectProposal) -> bool | None:
        if effect.action.run_id not in self._evaluation_run_tokens:
            return None
        context = effect.context
        contract = self._run(effect.action.run_id).manifest.task_contract
        return (
            isinstance(context, BookingCommitContext)
            and context.total_additional_cost_minor in {38_900, 39_900}
            and all(item["satisfied"] for item in booking_constraint_results(context, contract))
        )

    def release_evaluation_run(self, run_id: UUID) -> None:
        self._evaluation_run_tokens.pop(run_id, None)

    def create_evaluation(
        self, *, plan_id: str, maximum_total_cost_usd: Decimal
    ) -> EvaluationResponse:
        if self.store is None:
            raise EvaluationConfigurationError("evaluations require PostgreSQL state")
        if self.settings.agent_provider is not AgentProvider.OPENAI:
            raise EvaluationConfigurationError("evaluations require the OpenAI provider")
        if not self.settings.git_commit_sha or not self.settings.browser_version:
            raise EvaluationConfigurationError(
                "evaluations require pinned git and browser versions"
            )
        manifest = self._load_evaluation_manifest(plan_id)
        design_value = manifest.get("evaluationDesign")
        if not isinstance(design_value, dict):
            raise EvaluationConfigurationError("evaluation manifest design is malformed")
        design = cast(dict[str, object], design_value)
        intended = design.get("intendedExecutionCount")
        if not isinstance(intended, int):
            raise EvaluationConfigurationError("evaluation intent count is missing")
        projected_cap = Decimal(self.settings.run_max_model_cost_usd) * intended
        if maximum_total_cost_usd > self.settings.evaluation_max_total_cost_usd:
            raise EvaluationSpendCapError("requested cap exceeds the server-enforced maximum")
        if maximum_total_cost_usd < projected_cap:
            raise EvaluationSpendCapError(
                f"requested cap cannot cover {intended} bounded execution intents"
            )
        benchmark_value = manifest.get("benchmarkConfiguration")
        if not isinstance(benchmark_value, dict):
            raise EvaluationConfigurationError("benchmark configuration is malformed")
        benchmark = cast(dict[str, object], benchmark_value)
        benchmark.update(
            {
                "status": "PINNED",
                "referenceProvider": self.settings.agent_provider.value,
                "exactModelId": self.settings.agent_model,
                "gitCommitSha": self.settings.git_commit_sha,
                "promptVersion": "actor-v1",
                "browserVersion": self.settings.browser_version,
                "playwrightVersion": version("playwright"),
                "modelPriceTableVersion": self.settings.model_price_table_version,
                "effectiveGenerationParameters": {
                    "temperature": self.settings.agent_temperature,
                    "maxOutputTokens": self.settings.agent_max_output_tokens,
                    "requestTimeoutSeconds": self.settings.agent_request_timeout_seconds,
                },
            }
        )
        stored = self.store.create_evaluation(
            manifest=manifest,
            maximum_total_cost_usd=maximum_total_cost_usd,
            requested_by="operator_credential",
            created_at=self.clock.now(),
        )
        return self._evaluation_response(stored)

    def get_evaluation(self, evaluation_id: UUID) -> EvaluationResponse:
        if self.store is None:
            raise EvaluationNotFoundError("evaluation does not exist")
        stored = self.store.load_evaluation(evaluation_id)
        if stored is None:
            raise EvaluationNotFoundError("evaluation does not exist")
        return self._evaluation_response(stored)

    def get_evaluation_results(self, evaluation_id: UUID) -> EvaluationResultsResponse:
        evaluation = self.get_evaluation(evaluation_id)
        assert self.store is not None
        executions, metrics = self.store.evaluation_results(evaluation_id)
        evidence_status = (
            "COMPLETE"
            if evaluation.status == "completed" and metrics
            else "RAW_EXECUTIONS_AVAILABLE"
            if any(row.status == "valid" for row in executions)
            else "PENDING"
        )
        return EvaluationResultsResponse(
            evaluation_id=evaluation_id,
            evidence_status=evidence_status,
            executions=[
                EvaluationExecutionResponse(
                    execution_id=row.id,
                    case_id=row.eval_case_id,
                    run_id=row.run_id,
                    arm=cast(Literal["baseline", "protected"], row.arm),
                    attempt_kind=cast(Literal["original", "replacement"], row.attempt_kind),
                    status=row.status,
                    invalid_reason=row.invalid_reason,
                    model_cost_usd=row.model_cost_usd,
                    raw_predicate_results=row.raw_predicate_results,
                )
                for row in executions
            ],
            metrics=[
                MetricResultResponse(
                    metric_name=row.metric_name,
                    metric_value=row.metric_value,
                    confidence_low=row.confidence_low,
                    confidence_high=row.confidence_high,
                    report_version=row.report_version,
                )
                for row in metrics
            ],
        )

    def approval_response(
        self,
        request: ApprovalRequest,
        *,
        decided_at: datetime | None = None,
        resumed: bool = False,
    ) -> ApprovalResponse:
        context = request.effect.context
        if not isinstance(context, BookingCommitContext):
            raise ValueError("public approval preview requires a booking context")
        contract = self._run(request.run_id).manifest.task_contract
        constraints = [
            {
                "label": str(result["field"]).replace("_", " ").title(),
                "value": str(result["expected"]),
                "satisfied": bool(result["satisfied"]),
            }
            for result in booking_constraint_results(context, contract)
        ]
        return ApprovalResponse(
            approval_id=request.request_id,
            run_id=request.run_id,
            status=request.status,
            effect=request.effect.effect_class,
            summary=request.summary,
            approved_context_hash=request.effect.approved_context_hash,
            requested_at=request.created_at,
            expires_at=request.expires_at,
            scope={
                "marketing_carrier": context.marketing_carrier,
                "operating_carrier": context.operating_carrier,
                "flight_id": context.flight_id,
                "origin_airport": context.origin_airport,
                "destination_airport": context.destination_airport,
                "departure": context.departure.isoformat(),
                "arrival": context.arrival.isoformat(),
                "stop_count": context.stop_count,
                "cabin": context.cabin,
                "fare_class": context.fare_class,
                "seat_type": context.seat_type,
                "traveler_display_name": "Maya Chen",
                "total_additional_cost_minor": context.total_additional_cost_minor,
                "currency": context.currency,
                "constraints": constraints,
                "immediate_effect": "Create one replacement booking for the cancelled reservation.",
            },
            decided_at=decided_at,
            resumed=resumed,
        )

    def authenticate_session(self, raw_token: str) -> DemoSessionRecord:
        token_hash = self._token_hash(raw_token)
        with self._lock:
            session_id = self._sessions_by_token.get(token_hash)
            session = None if session_id is None else self._sessions.get(session_id)
        if session is None and self.store is not None:
            stored = self.store.session_by_token_hash(token_hash)
            if stored is not None:
                session = DemoSessionRecord(
                    session_id=stored.id,
                    token_hash=stored.public_token_hash,
                    created_at=stored.created_at,
                    expires_at=stored.expires_at,
                )
                with self._lock:
                    self._sessions[session.session_id] = session
                    self._sessions_by_token[token_hash] = session.session_id
        if session is None or self.clock.now() >= session.expires_at:
            raise SessionNotFoundError("session is missing or expired")
        return session

    def sealed_manifest(self, run_id: UUID) -> RunManifest:
        """Runtime-only accessor; never bind this result to an HTTP response."""

        return self._run(run_id).manifest

    def readiness(self) -> dict[str, str]:
        if self.store is None:
            return {"configuration": "ok", "state_store": "memory-development"}
        self.store.ping()
        return {"configuration": "ok", "state_store": "postgresql"}

    def clear(self) -> None:
        with self._lock:
            self._runs.clear()
            self._sessions.clear()
            self._sessions_by_token.clear()
            self._events.clear()
            for task in self._worker_tasks.values():
                task.cancel()
            self._worker_tasks.clear()
            self._approval_waiters.clear()

    def close(self) -> None:
        if self.store is not None:
            self.store.close()

    def cleanup_expired_artifacts(self) -> int:
        deleted = self.artifacts.cleanup_expired()
        if self.store is not None:
            self.store.delete_expired_artifacts(now=self.clock.now())
        return deleted

    def _append_event(
        self, run_id: UUID, event_type: str, payload: dict[str, object]
    ) -> RuntimeEvent:
        now = self.clock.now()
        correlated_payload = {**payload, "run_id": str(run_id)}
        trace_id = current_trace_id()
        if trace_id is not None:
            correlated_payload["trace_id"] = trace_id
        persisted_sequence = None
        if self.store is not None:
            persisted_sequence = self.store.append_event(
                run_id=run_id,
                event_type=event_type,
                payload=correlated_payload,
                created_at=now,
            )
        with self._lock:
            stream = self._events.setdefault(run_id, [])
            event = RuntimeEvent(
                persisted_sequence or len(stream) + 1,
                event_type,
                correlated_payload,
                now,
            )
            stream.append(event)
            return event

    def _run(self, run_id: UUID) -> RunRecord:
        with self._lock:
            record = self._runs.get(run_id)
        if record is None:
            raise RunNotFoundError("run does not exist")
        return record

    def _response(self, record: RunRecord) -> RunResponse:
        manifest = record.manifest
        pending = self.approvals.pending_for_run(manifest.run_id)
        pending_response = None
        if pending is not None:
            pending_response = self.approval_response(pending)
        return RunResponse(
            run_id=manifest.run_id,
            session_id=manifest.session_id,
            mode=manifest.mode,
            status=record.machine.state,
            task_contract=manifest.actor_contract(),
            created_at=record.created_at,
            execution_kind=(
                "deterministic_mock" if manifest.mode is RunMode.MOCK else "live_model"
            ),
            model_provider=manifest.model.provider,
            model_id=manifest.model.model_id,
            pending_approval=pending_response,
        )

    @staticmethod
    def _stored_response(stored: StoredRun) -> RunResponse:
        mode = RunMode(stored.mode)
        if stored.session_id is None:
            raise RunNotFoundError("public run has no owning session")
        return RunResponse(
            run_id=stored.run_id,
            session_id=stored.session_id,
            mode=mode,
            status=RunState(stored.status),
            task_contract=stored.contract,
            created_at=stored.created_at,
            execution_kind=("deterministic_mock" if mode is RunMode.MOCK else "live_model"),
            model_provider=stored.model_provider,
            model_id=stored.model_id,
            pending_approval=None,
        )

    @staticmethod
    def _evaluation_response(stored: StoredEvaluation) -> EvaluationResponse:
        return EvaluationResponse(
            evaluation_id=stored.evaluation_id,
            plan_id=stored.plan_id,
            status=cast(
                Literal["queued", "running", "completed", "failed", "cancelled"],
                stored.status,
            ),
            maximum_total_cost_usd=stored.maximum_total_cost_usd,
            intended_execution_count=stored.intended_execution_count,
            execution_status_counts=stored.execution_status_counts,
            created_at=stored.created_at,
            started_at=stored.started_at,
            completed_at=stored.completed_at,
            last_error=stored.last_error,
        )

    @staticmethod
    def _load_evaluation_manifest(plan_id: str) -> dict[str, object]:
        filenames = {
            "paired-primary-v1": "paired-primary.v1.json",
            "protected-safety-gates-v1": "protected-safety-gates.v1.json",
        }
        filename = filenames.get(plan_id)
        if filename is None:
            raise EvaluationConfigurationError("evaluation plan is not allowlisted")
        repository_root = Path(__file__).resolve().parents[4]
        raw = json.loads(
            (repository_root / "evals" / "manifests" / filename).read_text(encoding="utf-8")
        )
        if not isinstance(raw, dict):
            raise EvaluationConfigurationError("evaluation manifest is malformed")
        return copy.deepcopy(cast(dict[str, object], raw))

    @staticmethod
    def _token_hash(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
