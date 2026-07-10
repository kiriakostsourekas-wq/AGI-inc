from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from trust_runtime.approvals import ApprovalAuthority
from trust_runtime.effects import EffectDeriver
from trust_runtime.persistence.gateway import NorthstarCommitCommand, NorthstarGateway
from trust_runtime.persistence.models import (
    ApprovalGrantRow,
    ApprovalRequestRow,
    Base,
    EffectProposalRow,
    JobRow,
    ReplacementBookingRow,
    RunEventRow,
)
from trust_runtime.persistence.repositories import JobRepository


def _indexed_column_sets(table) -> set[tuple[str, ...]]:
    indexed: set[tuple[str, ...]] = set()
    for index in table.indexes:
        names = tuple(column.name for column in index.columns)
        if names:
            indexed.add(names)
    for constraint in table.constraints:
        if isinstance(constraint, (UniqueConstraint,)):
            names = tuple(column.name for column in constraint.columns)
            if names:
                indexed.add(names)
    return indexed


def test_core_schema_compiles_for_postgresql_and_uses_expected_key_types() -> None:
    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert "CREATE TABLE" in ddl

    event_ddl = str(CreateTable(RunEventRow.__table__).compile(dialect=dialect))
    assert "BIGINT GENERATED ALWAYS AS IDENTITY" in event_ddl
    assert "UNIQUE (run_id, sequence_no)" in event_ddl


def test_every_foreign_key_has_a_leftmost_supporting_index() -> None:
    missing: list[str] = []
    for table in Base.metadata.sorted_tables:
        indexes = _indexed_column_sets(table)
        for constraint in table.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            fk_names = tuple(column.name for column in constraint.columns)
            if not any(columns[: len(fk_names)] == fk_names for columns in indexes):
                missing.append(f"{table.fullname}({', '.join(fk_names)})")
    assert missing == []


def test_partial_uniqueness_enforces_active_grant_and_one_confirmed_replacement() -> None:
    dialect = postgresql.dialect()
    grant_index = next(
        index
        for index in Base.metadata.tables["runtime.approval_grants"].indexes
        if index.name == "uq_approval_grants_request_active"
    )
    booking_index = next(
        index
        for index in ReplacementBookingRow.__table__.indexes
        if index.name == "uq_replacement_bookings_original_confirmed"
    )
    grant_sql = str(CreateIndex(grant_index).compile(dialect=dialect))
    booking_sql = str(CreateIndex(booking_index).compile(dialect=dialect))
    assert "UNIQUE" in grant_sql and "WHERE status = 'ACTIVE'" in grant_sql
    assert "UNIQUE" in booking_sql and "WHERE status = 'confirmed'" in booking_sql
    assert tuple(column.name for column in booking_index.columns) == (
        "run_id",
        "original_reservation_id",
    )


def test_job_claim_is_one_atomic_skip_locked_statement() -> None:
    statement = JobRepository.claim_statement(
        worker_id="worker-a", claimed_at=datetime(2026, 7, 10, tzinfo=UTC)
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in sql
    assert "UPDATE runtime.jobs" in sql
    assert "RETURNING" in sql
    assert JobRow.__tablename__ in sql


def test_gateway_command_rejects_non_authoritative_shapes() -> None:
    common = {
        "run_id": "01980000-0000-7000-8000-000000000001",
        "grant_id": "01980000-0000-7000-8000-000000000002",
        "approval_request_id": "01980000-0000-7000-8000-000000000003",
        "effect_proposal_id": "01980000-0000-7000-8000-000000000004",
        "idempotency_key": "stable-effect-key",
        "capability_hash": "a" * 64,
        "approved_context_hash": "b" * 64,
        "contract_hash": "c" * 64,
        "origin": "http://northstar.localhost:3001",
        "original_reservation_id": "NST-P7Q4M2",
        "traveler_id": "traveler_maya_chen",
        "booking_reference": "NB-TEST",
        "semantic_context": {"kind": "booking_commit"},
        "total_additional_cost_minor": 38_900,
        "currency": "USD",
        "request_hash": "d" * 64,
    }
    command = NorthstarCommitCommand(**common)  # type: ignore[arg-type]
    assert command.total_additional_cost_minor == 38_900
    command.semantic_context["attacker_mutation"] = True
    assert command.semantic_context_payload() == {"kind": "booking_commit"}
    with pytest.raises(ValueError, match="currency"):
        NorthstarCommitCommand(**{**common, "currency": "usd"})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="request_hash"):
        NorthstarCommitCommand(**{**common, "request_hash": "not-a-hash"})  # type: ignore[arg-type]


def test_gateway_scope_rejects_any_post_approval_semantic_drift(
    contract, booking_action, booking_target, clock
) -> None:
    effect = EffectDeriver().derive(
        action=booking_action,
        contract=contract,
        trusted_target=booking_target,
        derived_at=clock.now(),
    )
    authority = ApprovalAuthority(signing_key=b"s" * 32, clock=clock, default_ttl_seconds=180)
    request = authority.request(effect=effect, summary="Approve exact itinerary")
    grant = authority.approve(request_id=request.request_id, contract_hash=contract.content_hash)
    context = effect.context.model_dump(mode="json")
    command = NorthstarCommitCommand(
        run_id=booking_action.run_id,
        grant_id=grant.payload.grant_id,
        approval_request_id=request.request_id,
        effect_proposal_id=effect.effect_id,
        idempotency_key=effect.idempotency_key or "",
        capability_hash=grant.capability_hash,
        approved_context_hash=effect.approved_context_hash,
        contract_hash=contract.content_hash,
        origin=effect.origin or "",
        original_reservation_id=grant.payload.reservation_id,
        traveler_id=grant.payload.traveler_id,
        booking_reference="NB-SCOPE-TEST",
        semantic_context=context,
        total_additional_cost_minor=grant.payload.total_additional_cost_minor,
        currency=grant.payload.currency,
        request_hash="d" * 64,
    )
    approval_row = ApprovalRequestRow(
        id=request.request_id,
        run_id=request.run_id,
        effect_proposal_id=effect.effect_id,
        approved_context_hash=effect.approved_context_hash,
        summary=request.summary,
        status="APPROVED",
        requested_at=request.created_at,
        expires_at=request.expires_at,
        decided_at=clock.now(),
        decision_source="pytest",
    )
    effect_row = EffectProposalRow(
        id=effect.effect_id,
        run_id=booking_action.run_id,
        action_id=booking_action.action_id,
        derived_origin=effect.origin or "runtime://local",
        derived_effect_class=effect.effect_class.value,
        trusted_target_kind=effect.trusted_target_kind.value,
        contract_hash=effect.contract_hash,
        semantic_context=context,
        approved_context_hash=effect.approved_context_hash,
        idempotency_key=effect.idempotency_key,
        status="AUTHORIZED",
        created_at=effect.derived_at,
    )
    grant_row = ApprovalGrantRow(
        id=grant.payload.grant_id,
        run_id=grant.payload.run_id,
        approval_request_id=grant.payload.approval_request_id,
        effect_proposal_id=grant.payload.effect_proposal_id,
        context_hash=grant.payload.approved_context_hash,
        idempotency_key=grant.payload.idempotency_key,
        capability_hash=grant.capability_hash,
        capability_payload=grant.payload.model_dump(mode="json"),
        signature=grant.signature,
        status="ACTIVE",
        issued_at=grant.payload.issued_at,
        expires_at=grant.payload.expires_at,
    )
    assert NorthstarGateway._scope_matches(
        grant=grant_row,
        approval=approval_row,
        effect=effect_row,
        command=command,
        context=command.semantic_context_payload(),
    )

    drifted_context = {**context, "total_additional_cost_minor": 39_900}
    drifted = replace(
        command,
        semantic_context=drifted_context,
        total_additional_cost_minor=39_900,
    )
    assert not NorthstarGateway._scope_matches(
        grant=grant_row,
        approval=approval_row,
        effect=effect_row,
        command=drifted,
        context=drifted.semantic_context_payload(),
    )
