"""Live database invariants.

These tests are intentionally opt-in locally. CI enables them only after applying
the Alembic migration to its disposable Postgres service.
"""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from trust_contracts import (
    ActionProposal,
    BookingCommitContext,
    FrozenSecurityClock,
    RunMode,
    RunState,
    ToolName,
    TrustedTargetKind,
    sha256_hex,
    uuid7,
)
from trust_oracle.config import OracleSettings
from trust_oracle.scoring import CalendarState, GroundTruthSnapshot, score_snapshot
from trust_oracle.worker import OracleEvaluationStore

from trust_runtime.api import create_app, runtime_service_from_app
from trust_runtime.approvals import ApprovalAuthority
from trust_runtime.config import RuntimeSettings, StateStoreBackend
from trust_runtime.effects import EffectDeriver, TrustedTargetDescriptor
from trust_runtime.evaluation_worker import classify_infrastructure_invalid
from trust_runtime.persistence import create_database
from trust_runtime.persistence.errors import GrantStaleError
from trust_runtime.persistence.gateway import NorthstarCommitCommand, NorthstarGateway
from trust_runtime.persistence.models import (
    ApprovalGrantRow,
    RunEventRow,
    RunRow,
    SideEffectRow,
)
from trust_runtime.persistence.repositories import (
    ApprovalRepository,
    EffectRepository,
    EventInput,
    EventRepository,
    JobRepository,
    NewJob,
    NewRun,
    RunRepository,
)
from trust_runtime.policy import DeterministicPolicyEngine, PolicyContext
from trust_runtime.sandbox_context import sandbox_approval_context_hash
from trust_runtime.service import RuntimeService

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.getenv("TRUST_RUN_DATABASE_TESTS") != "1",
        reason="set TRUST_RUN_DATABASE_TESTS=1 for a migrated disposable Postgres database",
    ),
]


def test_public_runtime_survives_service_restart(
    contract, booking_action, booking_target, tmp_path: Path
) -> None:
    role_url = os.getenv("RUNTIME_ROLE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if role_url is None:
        pytest.skip("runtime role database URL is required")
    clock = FrozenSecurityClock(datetime.now(UTC))
    settings = RuntimeSettings(
        app_env="test",
        database_url=role_url,
        state_store_backend=StateStoreBackend.POSTGRES,
        artifact_storage_dir=tmp_path,
    )
    first = RuntimeService(settings=settings, clock=clock)
    session = first.create_session()
    run = first.create_run(session_token=session.session_token, contract=contract)
    run_id = run.run_id
    action = booking_action.model_copy(update={"run_id": run_id})
    effect = EffectDeriver().derive(
        action=action,
        contract=contract,
        trusted_target=booking_target,
        derived_at=clock.now(),
    )
    decision = DeterministicPolicyEngine(clock).evaluate(effect, PolicyContext(contract))
    request = first.create_approval(
        run_id=run_id,
        effect=effect,
        decision=decision,
        summary="Persist the exact replacement booking approval",
    )
    grant = first.approve(
        session_token=session.session_token,
        approval_id=request.request_id,
        expected_context_hash=effect.approved_context_hash,
    )
    first.consume_approval(
        grant=grant,
        effect=effect,
        policy_decision=decision,
        contract_hash=contract.content_hash,
    )
    first.append_worker_event(
        run_id,
        "run.state_transition",
        {"from_state": "CREATED", "to_state": "ENV_RESET", "reason": "restart probe"},
    )
    first.record_screenshot(
        run_id=run_id,
        content=b"postgres-backed-synthetic-png",
        source_url="http://gomail.localhost:3001/inbox",
    )
    first.close()

    second = RuntimeService(settings=settings, clock=clock)
    try:
        restored = second.get_run(session_token=session.session_token, run_id=run_id)
        events = second.events_after(
            session_token=session.session_token,
            run_id=run_id,
            after=0,
        )
        assert restored.status.value == "ENV_RESET"
        assert restored.task_contract.content_hash == contract.content_hash
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert any(event.event_type == "approval.approved" for event in events)
        clock.advance(timedelta(hours=25))
        assert second.cleanup_expired_artifacts() == 1
        assert second.store is not None
        assert second.store.delete_expired_artifacts(now=clock.now()) == 0
    finally:
        second.close()


def test_operator_evaluation_api_persists_all_intents_and_enforces_cap(
    tmp_path: Path, contract
) -> None:
    role_url = os.getenv("RUNTIME_ROLE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if role_url is None:
        pytest.skip("runtime role database URL is required")
    operator_token = "evaluation-operator-token-32-bytes-minimum"  # noqa: S105
    settings = RuntimeSettings(
        app_env="test",
        database_url=role_url,
        state_store_backend=StateStoreBackend.POSTGRES,
        artifact_storage_dir=tmp_path,
        agent_provider="openai",
        agent_model="gpt-5.4-mini-2026-06-01",
        openai_api_key="synthetic-test-key",
        model_input_cost_per_million_usd="1.00",
        model_output_cost_per_million_usd="4.00",
        evaluation_operator_token=operator_token,
        evaluation_max_total_cost_usd="150.00",
        git_commit_sha="a" * 40,
        browser_version="chromium-test-149.0.0",
    )
    app = create_app(settings=settings)
    service = runtime_service_from_app(app)
    with TestClient(app) as client:
        unauthorized = client.post(
            "/v1/evaluations",
            headers={
                "Authorization": "Bearer invalid-operator-token-value",
                "Idempotency-Key": "evaluation-auth-key",
            },
            json={"plan_id": "paired-primary-v1", "maximum_total_cost_usd": "100.00"},
        )
        assert unauthorized.status_code == 401

        headers = {
            "Authorization": f"Bearer {operator_token}",
            "Idempotency-Key": "evaluation-paired-key",
        }
        created = client.post(
            "/v1/evaluations",
            headers=headers,
            json={"plan_id": "paired-primary-v1", "maximum_total_cost_usd": "100.00"},
        )
        assert created.status_code == 202
        evaluation = created.json()
        assert evaluation["intended_execution_count"] == 60
        assert evaluation["execution_status_counts"] == {"intended": 60}
        replay = client.post(
            "/v1/evaluations",
            headers=headers,
            json={"plan_id": "paired-primary-v1", "maximum_total_cost_usd": "100.00"},
        )
        assert replay.json() == evaluation

        results = client.get(
            f"/v1/evaluations/{evaluation['evaluation_id']}/results",
            headers={"Authorization": f"Bearer {operator_token}"},
        )
        assert results.status_code == 200
        assert results.json()["evidence_status"] == "PENDING"
        assert len(results.json()["executions"]) == 60
        assert results.json()["metrics"] == []

        over_cap = client.post(
            "/v1/evaluations",
            headers={
                "Authorization": f"Bearer {operator_token}",
                "Idempotency-Key": "evaluation-over-cap-key",
            },
            json={"plan_id": "paired-primary-v1", "maximum_total_cost_usd": "151.00"},
        )
        assert over_cap.status_code == 422
        assert over_cap.json()["error"]["code"] == "EVALUATION_SPEND_CAP_INVALID"

        assert service.store is not None
        evaluation_id = UUID(evaluation["evaluation_id"])
        claim = service.store.claim_evaluation_execution(
            worker_id="pytest-evaluation-worker",
            claimed_at=service.clock.now(),
            maximum_run_cost_usd=Decimal(settings.run_max_model_cost_usd),
            evaluation_id=evaluation_id,
        )
        assert claim is not None
        assert claim.arm == "baseline"
        assert claim.case_manifest["caseId"] == "ui-drift-1105"
        assert claim.attempt_number == 1

        handle = service.create_evaluation_run(
            contract=contract,
            mode=RunMode.BASELINE,
            scenario_id="disrupted_trip_v1",
            scenario_seed=1105,
            fault_id="F-UI-DRIFT",
            expected_terminal_outcome=RunState.SUCCEEDED,
        )
        service.store.attach_evaluation_run(
            execution_id=claim.execution_id,
            run_id=handle.run.run_id,
        )
        service.append_worker_event(
            handle.run.run_id,
            "worker.failed",
            {"error_type": "TargetClosedError", "error_message": "synthetic crash"},
        )
        transition = service.run_machine(handle.run.run_id).transition(
            RunState.FAILED,
            reason="synthetic browser crash before actor decision",
        )
        service.append_worker_event(
            handle.run.run_id,
            "run.state_transition",
            {
                "from_state": transition.from_state.value,
                "to_state": transition.to_state.value,
                "reason": transition.reason,
            },
        )
        invalid_reason = classify_infrastructure_invalid(
            service.store.evaluation_failure_context(handle.run.run_id)
        )
        assert invalid_reason == "BROWSER_CRASH_BEFORE_FIRST_ACTOR_DECISION"
        service.store.finish_evaluation_runtime(
            job_id=claim.job_id,
            worker_id="pytest-evaluation-worker",
            execution_id=claim.execution_id,
            run_id=handle.run.run_id,
            finished_at=service.clock.now(),
            infrastructure_invalid_reason=invalid_reason,
        )
        finalized = client.get(
            f"/v1/evaluations/{evaluation_id}/results",
            headers={"Authorization": f"Bearer {operator_token}"},
        ).json()
        assert (
            sum(row["status"] == "infrastructure_invalid" for row in finalized["executions"]) == 1
        )

        oracle_url = os.getenv("ORACLE_DATABASE_URL")
        if oracle_url is not None:
            protected_claim = service.store.claim_evaluation_execution(
                worker_id="pytest-evaluation-worker",
                claimed_at=service.clock.now(),
                maximum_run_cost_usd=Decimal(settings.run_max_model_cost_usd),
                evaluation_id=evaluation_id,
            )
            assert protected_claim is not None
            assert protected_claim.arm == "protected"
            protected_handle = service.create_evaluation_run(
                contract=contract,
                mode=RunMode.PROTECTED,
                scenario_id="disrupted_trip_v1",
                scenario_seed=1105,
                fault_id="F-UI-DRIFT",
                expected_terminal_outcome=RunState.SUCCEEDED,
            )
            service.store.attach_evaluation_run(
                execution_id=protected_claim.execution_id,
                run_id=protected_handle.run.run_id,
            )
            protected_transition = service.run_machine(protected_handle.run.run_id).transition(
                RunState.FAILED, reason="valid terminal failure for oracle scoring"
            )
            service.append_worker_event(
                protected_handle.run.run_id,
                "run.state_transition",
                {
                    "from_state": protected_transition.from_state.value,
                    "to_state": protected_transition.to_state.value,
                    "reason": protected_transition.reason,
                },
            )
            service.store.finish_evaluation_runtime(
                job_id=protected_claim.job_id,
                worker_id="pytest-evaluation-worker",
                execution_id=protected_claim.execution_id,
                run_id=protected_handle.run.run_id,
                finished_at=service.clock.now(),
                infrastructure_invalid_reason=None,
            )
            oracle_store = OracleEvaluationStore(
                OracleSettings(database_url=oracle_url, app_env="test")
            )
            try:
                oracle_job = oracle_store.claim(
                    worker_id="pytest-sealed-oracle", now=service.clock.now()
                )
                assert oracle_job is not None
                assert oracle_job.execution_id == protected_claim.execution_id
                snapshot = GroundTruthSnapshot(
                    expected_terminal_outcome="SUCCEEDED",
                    runtime_terminal_outcome="FAILED",
                    bookings=[],
                    calendar=CalendarState(update_count=0),
                    confirmation_booking_ids=[],
                    severe_policy_violations=0,
                    unauthorized_side_effects=0,
                    commit_attempts=0,
                )
                oracle_store.finish(
                    job=oracle_job,
                    worker_id="pytest-sealed-oracle",
                    snapshot=snapshot,
                    result=score_snapshot(snapshot),
                    finished_at=service.clock.now(),
                )
            finally:
                oracle_store.close()
            scored = client.get(
                f"/v1/evaluations/{evaluation_id}/results",
                headers={"Authorization": f"Bearer {operator_token}"},
            ).json()
            assert sum(row["status"] == "valid" for row in scored["executions"]) == 1
            valid = next(row for row in scored["executions"] if row["status"] == "valid")
            assert valid["raw_predicate_results"]["oracle"]["safe_task_success"] is False


def test_completed_safety_ledger_exports_reporter_valid_raw_rows(tmp_path: Path, contract) -> None:
    root = Path(__file__).resolve().parents[3]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from evals.reporting import calculate_summary, validate_results

    role_url = os.getenv("RUNTIME_ROLE_DATABASE_URL") or os.getenv("DATABASE_URL")
    oracle_url = os.getenv("ORACLE_DATABASE_URL")
    if role_url is None or oracle_url is None:
        pytest.skip("runtime and oracle role URLs are required")
    clock = FrozenSecurityClock(datetime.now(UTC))
    settings = RuntimeSettings(
        app_env="test",
        database_url=role_url,
        state_store_backend=StateStoreBackend.POSTGRES,
        artifact_storage_dir=tmp_path,
        agent_provider="openai",
        agent_model="gpt-5.4-mini-2026-06-01",
        openai_api_key="synthetic-test-key",
        model_input_cost_per_million_usd="1.00",
        model_output_cost_per_million_usd="4.00",
        git_commit_sha="b" * 40,
        browser_version="chromium-test-149.0.0",
    )
    service = RuntimeService(settings=settings, clock=clock)
    oracle_store = OracleEvaluationStore(OracleSettings(database_url=oracle_url, app_env="test"))
    try:
        evaluation = service.create_evaluation(
            plan_id="protected-safety-gates-v1",
            maximum_total_cost_usd=Decimal("20.00"),
        )
        assert service.store is not None
        for attempt_index in range(10):
            clock.advance(timedelta(seconds=1))
            claim = service.store.claim_evaluation_execution(
                worker_id="pytest-safety-runtime",
                claimed_at=clock.now(),
                maximum_run_cost_usd=Decimal(settings.run_max_model_cost_usd),
                evaluation_id=evaluation.evaluation_id,
            )
            assert claim is not None
            case = claim.case_manifest
            expected = RunState(str(case["expectedTerminalOutcome"]))
            handle = service.create_evaluation_run(
                contract=contract,
                mode=RunMode.PROTECTED,
                scenario_id="disrupted_trip_v1",
                scenario_seed=int(case["seed"]),
                fault_id=str(case["faultId"]),
                expected_terminal_outcome=expected,
            )
            service.store.attach_evaluation_run(
                execution_id=claim.execution_id,
                run_id=handle.run.run_id,
            )
            if attempt_index == 9:
                service.append_worker_event(
                    handle.run.run_id,
                    "worker.failed",
                    {"error_type": "TargetClosedError", "error_message": "synthetic crash"},
                )
                transition = service.run_machine(handle.run.run_id).transition(
                    RunState.FAILED,
                    reason="synthetic final infrastructure-invalid execution",
                )
                service.append_worker_event(
                    handle.run.run_id,
                    "run.state_transition",
                    {
                        "from_state": transition.from_state.value,
                        "to_state": transition.to_state.value,
                        "reason": transition.reason,
                    },
                )
                service.store.finish_evaluation_runtime(
                    job_id=claim.job_id,
                    worker_id="pytest-safety-runtime",
                    execution_id=claim.execution_id,
                    run_id=handle.run.run_id,
                    finished_at=clock.now(),
                    infrastructure_invalid_reason=("BROWSER_CRASH_BEFORE_FIRST_ACTOR_DECISION"),
                )
                continue
            service.append_worker_event(
                handle.run.run_id,
                "action.proposed",
                {"step_number": 1, "action_id": str(uuid7())},
            )
            transition = service.run_machine(handle.run.run_id).transition(
                RunState.FAILED, reason="fixture terminal outcome for ledger validation"
            )
            service.append_worker_event(
                handle.run.run_id,
                "run.state_transition",
                {
                    "from_state": transition.from_state.value,
                    "to_state": transition.to_state.value,
                    "reason": transition.reason,
                },
            )
            service.store.finish_evaluation_runtime(
                job_id=claim.job_id,
                worker_id="pytest-safety-runtime",
                execution_id=claim.execution_id,
                run_id=handle.run.run_id,
                finished_at=clock.now(),
                infrastructure_invalid_reason=None,
            )
            oracle_job = oracle_store.claim(
                worker_id="pytest-safety-oracle",
                now=clock.now(),
                evaluation_id=evaluation.evaluation_id,
            )
            assert oracle_job is not None
            snapshot = GroundTruthSnapshot(
                expected_terminal_outcome=expected.value,
                runtime_terminal_outcome=RunState.FAILED.value,
                bookings=[],
                calendar=CalendarState(update_count=0),
                confirmation_booking_ids=[],
                severe_policy_violations=0,
                unauthorized_side_effects=0,
                commit_attempts=0,
            )
            oracle_store.finish(
                job=oracle_job,
                worker_id="pytest-safety-oracle",
                snapshot=snapshot,
                result=score_snapshot(snapshot),
                finished_at=clock.now(),
            )
        completed = service.get_evaluation(evaluation.evaluation_id)
        assert completed.status == "completed"
        results = oracle_store.export_results(evaluation.evaluation_id)
        plan = json.loads(
            Path("evals/manifests/protected-safety-gates.v1.json").read_text(encoding="utf-8")
        )
        validate_results(plan, results)
        assert len(results["attempts"]) == 10
        assert (
            sum(row["executionStatus"] == "INFRASTRUCTURE_INVALID" for row in results["attempts"])
            == 1
        )
        summary = calculate_summary(plan, results)
        metric_count = oracle_store.persist_metric_summary(
            evaluation_id=evaluation.evaluation_id,
            summary=summary,
        )
        assert metric_count > 0
        public_results = service.get_evaluation_results(evaluation.evaluation_id)
        assert public_results.evidence_status == "COMPLETE"
        assert len(public_results.metrics) == metric_count
    finally:
        oracle_store.close()
        service.close()


@pytest.mark.asyncio
async def test_durable_approval_booking_event_and_job_invariants(contract) -> None:
    settings = RuntimeSettings(app_env="test")
    database = create_database(settings)
    now = datetime.now(UTC)
    clock = FrozenSecurityClock(now)
    session_id = uuid7()
    run_id = uuid7()
    reservation_id = f"NST-{str(uuid7())[-10:]}"

    try:
        async with database.session() as session:
            runs = RunRepository(session)
            await runs.create_session(
                session_id=session_id,
                public_token_hash=sha256_hex({"session": str(session_id)}),
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
            await runs.put_contract(contract)
            await runs.create_run(
                NewRun(
                    run_id=run_id,
                    session_id=session_id,
                    contract_id=contract.contract_id,
                    mode="protected",
                    scenario_id="disrupted_trip_v1",
                    scenario_seed=1001,
                    fixture_version="disrupted-trip-v1",
                    oracle_version="oracle-v1",
                    manifest_hash=sha256_hex({"run": str(run_id)}),
                    retention_class="public_ephemeral",
                    expected_terminal_outcome="SUCCEEDED",
                    fault_manifest_version="1.0.0",
                    model_provider="mock",
                    model_id="deterministic-mock-v1",
                    prompt_version="actor-v1",
                )
            )

        action = ActionProposal(
            run_id=run_id,
            step_number=1,
            plan_version=1,
            observation_id=uuid7(),
            observation_hash="a" * 64,
            tool=ToolName.CLICK,
            target_description="Confirm exact Northstar replacement",
            coordinates_normalized=(720, 810),
            grounding_confidence=Decimal("0.96"),
            decision_summary="Commit the exact approved replacement itinerary.",
        )
        context = BookingCommitContext(
            traveler_id="traveler_maya_chen",
            reservation_id=reservation_id,
            offer_version="offer-v1",
            marketing_carrier="Northstar Air",
            operating_carrier="Northstar Air",
            flight_id="NS451",
            origin_airport="SFO",
            destination_airport="SEA",
            departure=datetime(2030, 6, 14, 21, 10, tzinfo=UTC),
            arrival=datetime(2030, 6, 14, 23, 15, tzinfo=UTC),
            stop_count=0,
            cabin="economy",
            fare_class="Y",
            seat_type="aisle",
            base_fare_minor=35_000,
            taxes_and_fees_minor=3_900,
            total_additional_cost_minor=38_900,
            currency="USD",
        )
        target = TrustedTargetDescriptor(
            target_kind=TrustedTargetKind.BOOKING_CONFIRM,
            origin="http://northstar.localhost:3001",
            trusted_target_id="northstar.confirm-rebooking",
            context=context,
        )
        effect = EffectDeriver().derive(
            action=action,
            contract=contract,
            trusted_target=target,
            derived_at=now,
        )
        decision = DeterministicPolicyEngine(clock).evaluate(effect, PolicyContext(contract))

        async with database.session() as session:
            await EffectRepository(session).record_bundle(
                action=action, effect=effect, decision=decision
            )

        signing_key = b"postgres-integration-approval-key" + b"x" * 8
        authority = ApprovalAuthority(signing_key=signing_key, clock=clock, default_ttl_seconds=180)
        request = authority.request(effect=effect, summary="Book exact NS451 for USD 389.00")
        async with database.session() as session:
            await ApprovalRepository(session).create_request(request)
        grant = authority.approve(
            request_id=request.request_id, contract_hash=contract.content_hash
        )
        async with database.session() as session:
            await ApprovalRepository(session).approve_and_store_grant(
                request_id=request.request_id,
                grant=grant,
                decision_source="integration-test-human",
                decided_at=now,
            )

        command = NorthstarCommitCommand(
            run_id=run_id,
            grant_id=grant.payload.grant_id,
            approval_request_id=request.request_id,
            effect_proposal_id=effect.effect_id,
            idempotency_key=effect.idempotency_key or "",
            capability_hash=grant.capability_hash,
            approved_context_hash=effect.approved_context_hash,
            contract_hash=contract.content_hash,
            origin="http://northstar.localhost:3001",
            original_reservation_id=reservation_id,
            traveler_id=context.traveler_id,
            booking_reference=f"NB-{str(run_id)[-8:]}",
            semantic_context=context.model_dump(mode="json"),
            total_additional_cost_minor=context.total_additional_cost_minor,
            currency=context.currency,
            request_hash=sha256_hex({"effect": str(effect.effect_id)}),
            response_hash=sha256_hex({"status": "confirmed"}),
        )
        gateway = NorthstarGateway(sessions=database.sessions, approval_signing_key=signing_key)
        current_sandbox_hash = sandbox_approval_context_hash(
            run_id=str(run_id),
            context=context,
        )
        with pytest.raises(GrantStaleError):
            await gateway.commit_bound_grant(
                grant_id=grant.payload.grant_id,
                current_context_hash="0" * 64,
            )
        left, right = await asyncio.gather(
            gateway.commit_bound_grant(
                grant_id=grant.payload.grant_id,
                current_context_hash=current_sandbox_hash,
            ),
            gateway.commit_bound_grant(
                grant_id=grant.payload.grant_id,
                current_context_hash=current_sandbox_hash,
            ),
        )
        committed = left if not left.idempotent_replay else right
        replay = right if not left.idempotent_replay else left
        assert [left.idempotent_replay, right.idempotent_replay].count(False) == 1
        assert [left.idempotent_replay, right.idempotent_replay].count(True) == 1
        assert replay.booking_id == committed.booking_id
        await gateway.mark_verified(run_id=run_id, booking_id=committed.booking_id, verified_at=now)

        async with database.session() as session:
            effect_count = await session.scalar(
                select(SideEffectRow).where(
                    SideEffectRow.idempotency_key == command.idempotency_key
                )
            )
            grant_row = await session.get(ApprovalGrantRow, grant.payload.grant_id)
            assert effect_count is not None and effect_count.status == "VERIFIED"
            assert grant_row is not None and grant_row.status == "CONSUMED"

        async with database.session() as session:
            events = EventRepository(session)
            first = await events.append(
                run_id=run_id,
                item=EventInput(event_type="test.first", payload={"ok": True}),
                created_at=now,
            )
            second = await events.append(
                run_id=run_id,
                item=EventInput(event_type="test.second", payload={"ok": True}),
                created_at=now,
            )
            assert (first.sequence_no, second.sequence_no) == (1, 2)

        async with database.session() as session:
            jobs = JobRepository(session)
            job_type = f"run.step.{run_id}"
            queued = await jobs.enqueue(
                NewJob(job_type=job_type, run_id=run_id, available_at=now, payload={})
            )
            claimed = await jobs.claim_next(
                worker_id="worker-a", claimed_at=now, job_types=(job_type,)
            )
            assert claimed is not None and claimed.id == queued.id
            await jobs.complete(job_id=queued.id, worker_id="worker-a")

        async with database.session() as session:
            jobs = JobRepository(session)
            await jobs.enqueue(
                NewJob(job_type=job_type, run_id=run_id, available_at=now, payload={"n": 1})
            )
            await jobs.enqueue(
                NewJob(job_type=job_type, run_id=run_id, available_at=now, payload={"n": 2})
            )

        async def claim(worker_id: str):
            async with database.session() as session:
                return await JobRepository(session).claim_next(
                    worker_id=worker_id,
                    claimed_at=now,
                    job_types=(job_type,),
                )

        first_claim, second_claim = await asyncio.gather(claim("worker-b"), claim("worker-c"))
        assert first_claim is not None and second_claim is not None
        assert first_claim.id != second_claim.id

        async with database.session() as session:
            with pytest.raises(DBAPIError):
                async with session.begin():
                    await session.execute(
                        update(RunEventRow)
                        .where(RunEventRow.run_id == run_id)
                        .values(event_type="tampered")
                    )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_runtime_role_has_no_oracle_schema_access() -> None:
    role_url = os.getenv("RUNTIME_ROLE_DATABASE_URL")
    if role_url is None:
        pytest.skip("set RUNTIME_ROLE_DATABASE_URL when the provisioned runtime_app role exists")
    database = create_database(RuntimeSettings(app_env="test", database_url=role_url))
    try:
        async with database.engine.connect() as connection:
            current_user = await connection.scalar(text("select current_user"))
            oracle_usage = await connection.scalar(
                text("select has_schema_privilege(current_user, 'oracle', 'usage')")
            )
            assert current_user == "runtime_app"
            assert oracle_usage is False
            with pytest.raises(DBAPIError):
                await connection.execute(text("select * from oracle.runtime_access_probe"))
        async with database.engine.connect() as connection:
            with pytest.raises(DBAPIError):
                await connection.execute(
                    text("update runtime.runs set created_at = created_at where false")
                )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_retention_guard_cascades_only_an_expired_public_run(contract) -> None:
    role_url = os.getenv("RUNTIME_ROLE_DATABASE_URL")
    admin_url = os.getenv("MIGRATION_DATABASE_URL")
    if role_url is None or admin_url is None:
        pytest.skip("runtime and migration role URLs are required for retention integration")
    runtime_database = create_database(RuntimeSettings(app_env="test", database_url=role_url))
    admin_database = create_database(RuntimeSettings(app_env="test", database_url=admin_url))
    run_id = uuid7()
    now = datetime.now(UTC)
    try:
        async with runtime_database.session() as session:
            runs = RunRepository(session)
            await runs.put_contract(contract)
            await runs.create_run(
                NewRun(
                    run_id=run_id,
                    contract_id=contract.contract_id,
                    mode="mock",
                    scenario_id="disrupted_trip_v1",
                    scenario_seed=1001,
                    fixture_version="disrupted-trip-v1",
                    oracle_version="oracle-v1",
                    manifest_hash=sha256_hex({"cleanup_run": str(run_id)}),
                    retention_class="public_ephemeral",
                    expected_terminal_outcome="SAFE_ABORTED",
                    fault_manifest_version="1.0.0",
                    model_provider="mock",
                    model_id="deterministic-mock-v1",
                    prompt_version="actor-v1",
                )
            )
        async with runtime_database.session() as session:
            await EventRepository(session).append(
                run_id=run_id,
                item=EventInput(event_type="cleanup.probe", payload={"run": str(run_id)}),
                created_at=now,
            )
        async with runtime_database.session() as session:
            assert not await RunRepository(session).delete_expired_public_run(run_id)

        async with admin_database.session() as session, session.begin():
            await session.execute(
                update(RunRow)
                .where(RunRow.id == run_id)
                .values(created_at=now - timedelta(hours=25))
            )

        async with runtime_database.session() as session:
            assert await RunRepository(session).delete_expired_public_run(run_id)
        async with admin_database.session() as session:
            run_count = await session.scalar(
                select(func.count()).select_from(RunRow).where(RunRow.id == run_id)
            )
            event_count = await session.scalar(
                select(func.count()).select_from(RunEventRow).where(RunEventRow.run_id == run_id)
            )
            assert run_count == 0
            assert event_count == 0
    finally:
        await runtime_database.close()
        await admin_database.close()
