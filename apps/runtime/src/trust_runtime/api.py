"""FastAPI boundary stubs for health, readiness, sessions, and runs."""

import asyncio
import json
import secrets
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Annotated, cast
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.responses import Response
from trust_contracts import TERMINAL_RUN_STATES, SystemSecurityClock

from .config import RuntimeSettings, StateStoreBackend
from .errors import (
    ApprovalError,
    ArtifactAccessError,
    GatewayCommitRejectedError,
    GatewayUnauthorizedError,
    GatewayUnavailableError,
    InvalidEventCursorError,
    RequestTooLargeError,
    TrustRuntimeError,
)
from .idempotency import InMemoryIdempotencyStore, request_fingerprint
from .persistence.database import create_database
from .persistence.errors import PersistenceError
from .persistence.gateway import NorthstarGateway
from .quotas import PublicQuotaGuard
from .schemas import (
    ApiErrorDetail,
    ApiErrorEnvelope,
    ApprovalResponse,
    CancelRunResponse,
    CreateEvaluationRequest,
    CreateRunRequest,
    CreateSessionRequest,
    EvaluationResponse,
    EvaluationResultsResponse,
    GatewayCommitRequest,
    GatewayCommitResponse,
    HealthResponse,
    ReadinessResponse,
    RunResponse,
    SessionResponse,
)
from .service import RuntimeService
from .telemetry import configure_telemetry, set_attributes, tracer

VERSION = "0.1.0"
IdempotencyHeader = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
SessionHeader = Annotated[str, Header(alias="X-Demo-Session-Token", min_length=16)]
OperatorHeader = Annotated[str, Header(alias="Authorization", min_length=16)]
GatewayHeader = Annotated[str | None, Header(alias="X-Sandbox-Gateway-Token")]


def create_app(
    *,
    settings: RuntimeSettings | None = None,
    service: RuntimeService | None = None,
    idempotency: InMemoryIdempotencyStore | None = None,
) -> FastAPI:
    configured = settings or RuntimeSettings()
    runtime_service = service or RuntimeService(settings=configured, clock=SystemSecurityClock())
    configure_telemetry(configured)
    api_tracer = tracer("api")
    idempotency_store = idempotency or InMemoryIdempotencyStore()
    gateway_database = (
        create_database(configured)
        if configured.state_store_backend is StateStoreBackend.POSTGRES
        else None
    )

    async def artifact_cleanup_loop() -> None:
        while True:
            runtime_service.cleanup_expired_artifacts()
            await asyncio.sleep(3600)

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        cleanup_task = asyncio.create_task(artifact_cleanup_loop())
        try:
            yield
        finally:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
            if gateway_database is not None:
                await gateway_database.close()
            runtime_service.close()

    application = FastAPI(title="Trust Runtime", version=VERSION, lifespan=lifespan)
    application.state.runtime_service = runtime_service
    quota = PublicQuotaGuard(
        clock=runtime_service.clock,
        maximum_concurrent=configured.max_public_concurrent_runs,
        maximum_per_ip_per_hour=configured.public_runs_per_ip_per_hour,
    )

    @application.middleware("http")
    async def request_size_guard(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        with api_tracer.start_as_current_span("http.request") as span:
            set_attributes(
                span,
                {
                    "http.request.method": request.method,
                    "url.path": request.url.path,
                },
            )
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > 256 * 1024:
                        error = RequestTooLargeError("request body exceeds 256 KB")
                        envelope = ApiErrorEnvelope(
                            error=ApiErrorDetail(code=error.code, message=str(error))
                        )
                        span.set_attribute("http.response.status_code", error.status_code)
                        return JSONResponse(
                            status_code=error.status_code,
                            content=envelope.model_dump(mode="json"),
                        )
                except ValueError:
                    error = RequestTooLargeError("Content-Length must be an integer")
                    envelope = ApiErrorEnvelope(
                        error=ApiErrorDetail(code=error.code, message=str(error))
                    )
                    span.set_attribute("http.response.status_code", error.status_code)
                    return JSONResponse(
                        status_code=error.status_code,
                        content=envelope.model_dump(mode="json"),
                    )
            response = await call_next(request)
            span.set_attribute("http.response.status_code", response.status_code)
            return response

    @application.exception_handler(TrustRuntimeError)
    async def domain_error_handler(_request: Request, error: TrustRuntimeError) -> JSONResponse:
        envelope = ApiErrorEnvelope(
            error=ApiErrorDetail(code=error.code, message=str(error)),
        )
        headers: dict[str, str] = {}
        retry_after = getattr(error, "retry_after_seconds", None)
        if isinstance(retry_after, int):
            headers["Retry-After"] = str(retry_after)
        return JSONResponse(
            status_code=error.status_code,
            content=envelope.model_dump(mode="json"),
            headers=headers,
        )

    @application.get("/healthz", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(version=VERSION)

    @application.get("/readyz", response_model=ReadinessResponse)
    async def ready() -> ReadinessResponse:
        return ReadinessResponse(checks=runtime_service.readiness())

    def require_operator(authorization: str) -> None:
        expected = f"Bearer {configured.evaluation_operator_token.get_secret_value()}"
        if not secrets.compare_digest(authorization, expected):
            from .errors import OperatorUnauthorizedError

            raise OperatorUnauthorizedError("operator credential is missing or invalid")

    def require_gateway(token: str | None) -> None:
        expected = configured.sandbox_gateway_token.get_secret_value()
        if token is None or not secrets.compare_digest(token, expected):
            raise GatewayUnauthorizedError("sandbox gateway credential is missing or invalid")

    @application.post(
        "/internal/v1/gateway/commit",
        response_model=GatewayCommitResponse,
    )
    async def commit_gateway(
        payload: GatewayCommitRequest,
        gateway_token: GatewayHeader = None,
    ) -> GatewayCommitResponse:
        require_gateway(gateway_token)
        if gateway_database is None:
            raise GatewayUnavailableError("durable gateway requires PostgreSQL state")
        gateway = NorthstarGateway(
            sessions=gateway_database.sessions,
            approval_signing_key=configured.approval_hmac_secret.get_secret_value().encode(),
        )
        try:
            result = await gateway.commit_bound_grant(
                grant_id=payload.grant_id,
                current_context_hash=payload.current_context_hash,
            )
        except PersistenceError as error:
            raise GatewayCommitRejectedError(str(error)) from error
        with suppress(ApprovalError):
            runtime_service.approvals.mark_consumed(
                grant_id=payload.grant_id,
                consumed_at=result.committed_at,
            )
        return GatewayCommitResponse(
            booking_id=result.booking_id,
            booking_reference=result.booking_reference,
            side_effect_id=result.side_effect_id,
            idempotent_replay=result.idempotent_replay,
            committed_at=result.committed_at,
        )

    @application.post(
        "/v1/evaluations",
        response_model=EvaluationResponse,
        status_code=202,
    )
    async def create_evaluation(
        payload: CreateEvaluationRequest,
        authorization: OperatorHeader,
        idempotency_key: IdempotencyHeader,
    ) -> EvaluationResponse:
        require_operator(authorization)
        body = payload.model_dump(mode="json")
        fingerprint = request_fingerprint(method="POST", path="/v1/evaluations", body=body)
        existing = idempotency_store.lookup(
            namespace="operator:evaluations",
            key=idempotency_key,
            fingerprint=fingerprint,
        )
        if existing is not None:
            return EvaluationResponse.model_validate(existing.body)
        response = runtime_service.create_evaluation(
            plan_id=payload.plan_id,
            maximum_total_cost_usd=payload.maximum_total_cost_usd,
        )
        idempotency_store.save(
            namespace="operator:evaluations",
            key=idempotency_key,
            fingerprint=fingerprint,
            status_code=202,
            body=response.model_dump(mode="json"),
        )
        return response

    @application.get("/v1/evaluations/{evaluation_id}", response_model=EvaluationResponse)
    async def get_evaluation(
        evaluation_id: UUID,
        authorization: OperatorHeader,
    ) -> EvaluationResponse:
        require_operator(authorization)
        return runtime_service.get_evaluation(evaluation_id)

    @application.get(
        "/v1/evaluations/{evaluation_id}/results",
        response_model=EvaluationResultsResponse,
    )
    async def get_evaluation_results(
        evaluation_id: UUID,
        authorization: OperatorHeader,
    ) -> EvaluationResultsResponse:
        require_operator(authorization)
        return runtime_service.get_evaluation_results(evaluation_id)

    @application.post("/v1/sessions", response_model=SessionResponse, status_code=201)
    async def create_session(
        payload: CreateSessionRequest, idempotency_key: IdempotencyHeader
    ) -> SessionResponse:
        body = payload.model_dump(mode="json")
        fingerprint = request_fingerprint(method="POST", path="/v1/sessions", body=body)
        existing = idempotency_store.lookup(
            namespace="public-sessions", key=idempotency_key, fingerprint=fingerprint
        )
        if existing is not None:
            return SessionResponse.model_validate(existing.body)
        response = runtime_service.create_session()
        idempotency_store.save(
            namespace="public-sessions",
            key=idempotency_key,
            fingerprint=fingerprint,
            status_code=201,
            body=response.model_dump(mode="json"),
        )
        return response

    @application.post("/v1/runs", response_model=RunResponse, status_code=201)
    async def create_run(
        payload: CreateRunRequest,
        request: Request,
        idempotency_key: IdempotencyHeader,
        session_token: SessionHeader,
    ) -> RunResponse:
        session = runtime_service.authenticate_session(session_token)
        body = payload.model_dump(mode="json")
        fingerprint = request_fingerprint(method="POST", path="/v1/runs", body=body)
        namespace = f"session:{session.session_id}:runs"
        existing = idempotency_store.lookup(
            namespace=namespace, key=idempotency_key, fingerprint=fingerprint
        )
        if existing is not None:
            return RunResponse.model_validate(existing.body)
        reserved = False
        if configured.public_live_runs_enabled:
            client_ip = request.client.host if request.client is not None else "unknown"
            quota.reserve(client_ip)
            reserved = True
        try:
            response = runtime_service.create_run(
                session_token=session_token,
                contract=payload.task_contract,
                mode=payload.mode,
                scenario_id=payload.scenario_selection.scenario_id,
                scenario_seed=payload.scenario_selection.scenario_seed,
                fault_id=payload.scenario_selection.fault_id,
            )
        except Exception:
            if reserved:
                quota.release()
            raise
        idempotency_store.save(
            namespace=namespace,
            key=idempotency_key,
            fingerprint=fingerprint,
            status_code=201,
            body=response.model_dump(mode="json"),
        )
        if configured.public_live_runs_enabled:
            task = runtime_service.start_run(response.run_id)
            task.add_done_callback(lambda _task: quota.release())
        return response

    @application.get("/v1/runs/{run_id}", response_model=RunResponse)
    async def get_run(run_id: UUID, session_token: SessionHeader) -> RunResponse:
        return runtime_service.get_run(session_token=session_token, run_id=run_id)

    @application.get("/v1/runs/{run_id}/events", response_model=None)
    async def run_events(
        run_id: UUID,
        session_token: SessionHeader,
        request: Request,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> JSONResponse | StreamingResponse:
        """Replayable SSE stream; persistence-backed deployments replace the source."""

        try:
            cursor = int(last_event_id or "0")
        except ValueError as error:
            raise InvalidEventCursorError("Last-Event-ID must be an integer") from error
        events = runtime_service.events_after(
            session_token=session_token, run_id=run_id, after=cursor
        )

        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse(
                content={
                    "events": [
                        {
                            "id": str(event.sequence),
                            "sequence_no": event.sequence,
                            "event_type": event.event_type,
                            "payload": event.payload,
                            "created_at": event.occurred_at.isoformat(),
                        }
                        for event in events
                    ]
                }
            )

        async def stream():
            current_cursor = cursor
            while True:
                available = runtime_service.events_after(
                    session_token=session_token,
                    run_id=run_id,
                    after=current_cursor,
                )
                for event in available:
                    current_cursor = event.sequence
                    payload = {
                        "sequence": event.sequence,
                        "event_type": event.event_type,
                        "payload": event.payload,
                        "occurred_at": event.occurred_at.isoformat(),
                    }
                    yield (
                        f"id: {event.sequence}\nevent: {event.event_type}"
                        f"\ndata: {json.dumps(payload)}\n\n"
                    )
                if await request.is_disconnected():
                    break
                current = runtime_service.get_run(
                    session_token=session_token,
                    run_id=run_id,
                )
                if current.status in TERMINAL_RUN_STATES and not available:
                    break
                yield ": keep-alive\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @application.get("/v1/runs/{run_id}/replay")
    async def run_replay(run_id: UUID, session_token: SessionHeader) -> JSONResponse:
        run = runtime_service.get_run(session_token=session_token, run_id=run_id)
        artifacts = runtime_service.artifacts.list_for_run(run_id)
        frames: list[dict[str, object]] = []
        for index, artifact in enumerate(artifacts):
            parsed = urlsplit(artifact.source_url)
            hostname = parsed.hostname or "sandbox"
            app_name = hostname.split(".", maxsplit=1)[0].replace("-", " ").title()
            frames.append(
                {
                    "id": str(artifact.artifact_id),
                    "sequence_no": artifact.sequence_no,
                    "chapter": f"Recorded observation {index + 1}",
                    "app": app_name,
                    "path": parsed.path or "/",
                    "status": "Recorded",
                    "title": f"{app_name} browser state",
                    "description": "Immutable screenshot captured before the actor decision.",
                    "evidence": f"SHA-256 {artifact.sha256[:12]}…",
                    "tone": "accent",
                    "screenshot_url": (
                        f"/api/runtime{runtime_service.artifacts.signed_path(artifact)}"
                    ),
                }
            )
        recorded_at = artifacts[-1].created_at if artifacts else runtime_service.clock.now()
        return JSONResponse(
            content={
                "run_id": str(run_id),
                "label": "Recorded replay",
                "recorded_at": recorded_at.isoformat(),
                "source_execution_kind": run.execution_kind,
                "frames": frames,
            }
        )

    @application.get("/v1/runs/{run_id}/artifacts/{artifact_id}")
    async def get_artifact(
        run_id: UUID,
        artifact_id: UUID,
        expires: int,
        signature: str,
        session_token: SessionHeader,
    ) -> Response:
        runtime_service.get_run(session_token=session_token, run_id=run_id)
        try:
            record, content = runtime_service.artifacts.read_signed(
                run_id=run_id,
                artifact_id=artifact_id,
                expires=expires,
                signature=signature,
            )
        except (OSError, ValueError, PermissionError) as error:
            raise ArtifactAccessError(str(error)) from error
        return Response(
            content=content,
            media_type=record.content_type,
            headers={
                "Cache-Control": "private, max-age=900, immutable",
                "ETag": f'"{record.sha256}"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @application.post("/v1/runs/{run_id}/cancel", response_model=CancelRunResponse)
    async def cancel_run(
        run_id: UUID,
        idempotency_key: IdempotencyHeader,
        session_token: SessionHeader,
    ) -> CancelRunResponse:
        session = runtime_service.authenticate_session(session_token)
        body = {"run_id": str(run_id)}
        path = f"/v1/runs/{run_id}/cancel"
        fingerprint = request_fingerprint(method="POST", path=path, body=body)
        namespace = f"session:{session.session_id}:cancel"
        existing = idempotency_store.lookup(
            namespace=namespace, key=idempotency_key, fingerprint=fingerprint
        )
        if existing is not None:
            return CancelRunResponse.model_validate(existing.body)
        response = runtime_service.cancel_run(session_token=session_token, run_id=run_id)
        idempotency_store.save(
            namespace=namespace,
            key=idempotency_key,
            fingerprint=fingerprint,
            status_code=200,
            body=response.model_dump(mode="json"),
        )
        return response

    @application.post("/v1/approvals/{approval_id}/approve", response_model=ApprovalResponse)
    async def approve(
        approval_id: UUID,
        session_token: SessionHeader,
        idempotency_key: IdempotencyHeader,
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> ApprovalResponse:
        request = runtime_service.approvals.get_request(approval_id)
        fingerprint = request_fingerprint(
            method="POST",
            path=f"/v1/approvals/{approval_id}/approve",
            body={"if_match": if_match},
        )
        namespace = f"session:{request.run_id}:approval"
        existing = idempotency_store.lookup(
            namespace=namespace, key=idempotency_key, fingerprint=fingerprint
        )
        if existing is not None:
            return ApprovalResponse.model_validate(existing.body)
        runtime_service.approve(
            session_token=session_token,
            approval_id=approval_id,
            expected_context_hash=if_match,
        )
        request = runtime_service.approvals.get_request(approval_id)
        response = runtime_service.approval_response(
            request,
            decided_at=runtime_service.clock.now(),
            resumed=True,
        )
        idempotency_store.save(
            namespace=namespace,
            key=idempotency_key,
            fingerprint=fingerprint,
            status_code=200,
            body=response.model_dump(mode="json"),
        )
        return response

    @application.post("/v1/approvals/{approval_id}/reject", response_model=ApprovalResponse)
    async def reject_approval(
        approval_id: UUID,
        session_token: SessionHeader,
        idempotency_key: IdempotencyHeader,
    ) -> ApprovalResponse:
        pending = runtime_service.approvals.get_request(approval_id)
        fingerprint = request_fingerprint(
            method="POST", path=f"/v1/approvals/{approval_id}/reject", body={}
        )
        namespace = f"session:{pending.run_id}:approval"
        existing = idempotency_store.lookup(
            namespace=namespace, key=idempotency_key, fingerprint=fingerprint
        )
        if existing is not None:
            return ApprovalResponse.model_validate(existing.body)
        request = runtime_service.reject_approval(
            session_token=session_token, approval_id=approval_id
        )
        response = runtime_service.approval_response(
            request,
            decided_at=runtime_service.clock.now(),
        )
        idempotency_store.save(
            namespace=namespace,
            key=idempotency_key,
            fingerprint=fingerprint,
            status_code=200,
            body=response.model_dump(mode="json"),
        )
        return response

    return application


app = create_app()


def runtime_service_from_app(application: FastAPI) -> RuntimeService:
    return cast(RuntimeService, application.state.runtime_service)
