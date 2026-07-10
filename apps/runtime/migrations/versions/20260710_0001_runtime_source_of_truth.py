# ruff: noqa: E501
"""Create the runtime source of truth and atomic Northstar gateway tables.

Revision ID: 20260710_0001
Revises: None
Create Date: 2026-07-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260710_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("create schema if not exists runtime")
    op.execute("create schema if not exists sandbox")

    op.execute(
        """
        create table runtime.demo_sessions (
          id uuid primary key,
          public_token_hash text not null,
          created_at timestamptz not null default now(),
          expires_at timestamptz not null,
          live_run_count integer not null default 0,
          constraint uq_demo_sessions_public_token_hash unique (public_token_hash),
          constraint ck_demo_sessions_live_run_count_nonnegative check (live_run_count >= 0)
        );
        create index ix_demo_sessions_expires_at on runtime.demo_sessions (expires_at);

        create table runtime.task_contracts (
          id uuid primary key,
          schema_version text not null,
          content_hash varchar(64) not null,
          canonical_payload jsonb not null,
          created_at timestamptz not null default now(),
          constraint uq_task_contracts_content_hash unique (content_hash),
          constraint ck_task_contracts_content_hash_length check (length(content_hash) = 64)
        );

        create table runtime.runs (
          id uuid primary key,
          session_id uuid references runtime.demo_sessions(id) on delete set null,
          contract_id uuid not null references runtime.task_contracts(id),
          mode text not null,
          status text not null,
          scenario_id text not null,
          scenario_seed integer not null,
          fixture_version text not null,
          fault_id text,
          fault_parameters jsonb not null,
          oracle_version text not null,
          manifest_hash varchar(64) not null,
          retention_class text not null,
          expected_terminal_outcome text not null,
          model_provider text,
          model_id text,
          prompt_version text,
          fault_manifest_version text not null,
          started_at timestamptz,
          finished_at timestamptz,
          terminal_reason text,
          step_count integer not null default 0,
          model_call_count integer not null default 0,
          model_cost_usd numeric(12,4) not null default 0,
          created_at timestamptz not null default now(),
          constraint uq_runs_manifest_hash unique (manifest_hash),
          constraint ck_runs_mode_allowed check (mode in ('baseline','protected','mock','replay')),
          constraint ck_runs_status_allowed check (status in (
            'CREATED','ENV_RESET','CONTRACT_VALIDATED','OBSERVING','PLANNING','REPLANNING',
            'ACTION_PROPOSED','POLICY_CHECKING','WAITING_APPROVAL','EXECUTING','VERIFYING',
            'RECOVERING','OUTCOME_UNKNOWN','FINALIZING','SUCCEEDED','PARTIAL_SUCCESS',
            'HANDOFF_REQUIRED','FAILED_OUTCOME_UNKNOWN','SAFE_ABORTED','FAILED','CANCELLED'
          )),
          constraint ck_runs_scenario_seed_nonnegative check (scenario_seed >= 0),
          constraint ck_runs_manifest_hash_length check (length(manifest_hash) = 64),
          constraint ck_runs_retention_class_allowed check (retention_class in (
            'public_ephemeral','local_development','published_benchmark'
          )),
          constraint ck_runs_expected_terminal_outcome_allowed check (
            expected_terminal_outcome in ('SUCCEEDED','PARTIAL_SUCCESS','HANDOFF_REQUIRED',
            'FAILED_OUTCOME_UNKNOWN','SAFE_ABORTED','FAILED','CANCELLED')
          ),
          constraint ck_runs_step_count_nonnegative check (step_count >= 0),
          constraint ck_runs_model_call_count_nonnegative check (model_call_count >= 0),
          constraint ck_runs_model_cost_nonnegative check (model_cost_usd >= 0)
        );
        create index ix_runs_session_created on runtime.runs (session_id, created_at desc);
        create index ix_runs_contract_id on runtime.runs (contract_id);
        create index ix_runs_active_status_created on runtime.runs (status, created_at)
          where status in ('CREATED','ENV_RESET','OBSERVING','EXECUTING','VERIFYING','RECOVERING','OUTCOME_UNKNOWN');

        create table runtime.run_events (
          id bigint generated always as identity primary key,
          run_id uuid not null references runtime.runs(id) on delete cascade,
          sequence_no integer not null,
          event_type text not null,
          schema_version text not null,
          step_id uuid,
          payload jsonb not null,
          payload_hash varchar(64) not null,
          created_at timestamptz not null default now(),
          constraint uq_run_events_run_sequence unique (run_id, sequence_no),
          constraint ck_run_events_sequence_positive check (sequence_no > 0),
          constraint ck_run_events_payload_hash_length check (length(payload_hash) = 64)
        );
        create index ix_run_events_run_sequence on runtime.run_events (run_id, sequence_no);

        create table runtime.artifacts (
          id uuid primary key,
          run_id uuid not null references runtime.runs(id) on delete cascade,
          event_id bigint references runtime.run_events(id) on delete set null,
          kind text not null,
          storage_key text not null,
          content_type text not null,
          byte_size bigint not null,
          sha256 varchar(64) not null,
          redaction_status text not null,
          expires_at timestamptz,
          created_at timestamptz not null default now(),
          constraint uq_artifacts_storage_key unique (storage_key),
          constraint ck_artifacts_byte_size_nonnegative check (byte_size >= 0),
          constraint ck_artifacts_sha256_length check (length(sha256) = 64)
        );
        create index ix_artifacts_run_created on runtime.artifacts (run_id, created_at);
        create index ix_artifacts_event_id on runtime.artifacts (event_id);
        create index ix_artifacts_expires_at on runtime.artifacts (expires_at) where expires_at is not null;

        create table runtime.action_proposals (
          id uuid primary key,
          run_id uuid not null references runtime.runs(id) on delete cascade,
          step_number integer not null,
          observation_hash varchar(64) not null,
          tool text not null,
          proposal_payload jsonb not null,
          grounding_confidence numeric(5,4),
          created_at timestamptz not null default now(),
          constraint uq_action_proposals_run_step unique (run_id, step_number),
          constraint ck_action_proposals_step_nonnegative check (step_number >= 0),
          constraint ck_action_proposals_observation_hash_length check (length(observation_hash) = 64),
          constraint ck_action_proposals_grounding_confidence_range check (
            grounding_confidence is null or (grounding_confidence >= 0 and grounding_confidence <= 1)
          )
        );
        create index ix_action_proposals_run_step on runtime.action_proposals (run_id, step_number);

        create table runtime.effect_proposals (
          id uuid primary key,
          run_id uuid not null references runtime.runs(id) on delete cascade,
          action_id uuid not null references runtime.action_proposals(id) on delete cascade,
          derived_origin text not null,
          derived_effect_class text not null,
          trusted_target_kind text not null,
          contract_hash varchar(64) not null,
          semantic_context jsonb not null,
          approved_context_hash varchar(64) not null,
          idempotency_key text,
          status text not null default 'PROPOSED',
          created_at timestamptz not null default now(),
          constraint uq_effect_proposals_action unique (action_id),
          constraint ck_effect_proposals_contract_hash_length check (length(contract_hash) = 64),
          constraint ck_effect_proposals_context_hash_length check (length(approved_context_hash) = 64)
        );
        create index ix_effect_proposals_run_created on runtime.effect_proposals (run_id, created_at);
        create index ix_effect_proposals_action_id on runtime.effect_proposals (action_id);

        create table runtime.policy_decisions (
          id uuid primary key,
          run_id uuid not null references runtime.runs(id) on delete cascade,
          action_id uuid not null references runtime.action_proposals(id) on delete cascade,
          effect_proposal_id uuid references runtime.effect_proposals(id) on delete cascade,
          decision text not null,
          rule_id text not null,
          context_hash varchar(64) not null,
          created_at timestamptz not null default now(),
          constraint ck_policy_decisions_decision_allowed check (decision in ('ALLOW','DENY','REQUIRE_APPROVAL')),
          constraint ck_policy_decisions_context_hash_length check (length(context_hash) = 64)
        );
        create index ix_policy_decisions_run_id on runtime.policy_decisions (run_id);
        create index ix_policy_decisions_action_id on runtime.policy_decisions (action_id);
        create index ix_policy_decisions_effect_id on runtime.policy_decisions (effect_proposal_id);

        create table runtime.approval_requests (
          id uuid primary key,
          run_id uuid not null references runtime.runs(id) on delete cascade,
          effect_proposal_id uuid not null references runtime.effect_proposals(id) on delete cascade,
          approved_context_hash varchar(64) not null,
          summary text not null,
          status text not null default 'PENDING',
          requested_at timestamptz not null,
          expires_at timestamptz not null,
          decided_at timestamptz,
          decision_source text,
          constraint ck_approval_requests_status_allowed check (status in ('PENDING','APPROVED','REJECTED','EXPIRED','CANCELLED')),
          constraint ck_approval_requests_expiry_after_request check (expires_at > requested_at),
          constraint ck_approval_requests_context_hash_length check (length(approved_context_hash) = 64)
        );
        create index ix_approval_requests_run_status on runtime.approval_requests (run_id, status);
        create index ix_approval_requests_effect_id on runtime.approval_requests (effect_proposal_id);
        create index ix_approval_requests_pending_expiry on runtime.approval_requests (expires_at) where status = 'PENDING';

        create table runtime.approval_grants (
          id uuid primary key,
          run_id uuid not null references runtime.runs(id) on delete cascade,
          approval_request_id uuid not null references runtime.approval_requests(id) on delete cascade,
          effect_proposal_id uuid not null references runtime.effect_proposals(id) on delete cascade,
          context_hash varchar(64) not null,
          idempotency_key text not null,
          capability_hash varchar(64) not null,
          capability_payload jsonb not null,
          signature varchar(64) not null,
          status text not null default 'ACTIVE',
          issued_at timestamptz not null,
          expires_at timestamptz not null,
          used_at timestamptz,
          constraint uq_approval_grants_capability_hash unique (capability_hash),
          constraint ck_approval_grants_status_allowed check (status in ('ACTIVE','CONSUMED','EXPIRED','REVOKED')),
          constraint ck_approval_grants_expiry_after_issue check (expires_at > issued_at),
          constraint ck_approval_grants_context_hash_length check (length(context_hash) = 64),
          constraint ck_approval_grants_capability_hash_length check (length(capability_hash) = 64),
          constraint ck_approval_grants_signature_length check (length(signature) = 64)
        );
        create index ix_approval_grants_run_status on runtime.approval_grants (run_id, status);
        create index ix_approval_grants_effect_id on runtime.approval_grants (effect_proposal_id);
        create index ix_approval_grants_active_expiry on runtime.approval_grants (expires_at) where status = 'ACTIVE';
        create unique index uq_approval_grants_request_active on runtime.approval_grants (approval_request_id) where status = 'ACTIVE';

        create table runtime.side_effects (
          id uuid primary key,
          run_id uuid not null references runtime.runs(id) on delete cascade,
          effect_proposal_id uuid not null references runtime.effect_proposals(id) on delete cascade,
          idempotency_key text not null,
          effect_type text not null,
          external_resource_id text,
          status text not null,
          request_hash varchar(64) not null,
          response_hash varchar(64),
          committed_at timestamptz,
          verified_at timestamptz,
          created_at timestamptz not null default now(),
          constraint uq_side_effects_idempotency_key unique (idempotency_key),
          constraint ck_side_effects_status_allowed check (status in ('COMMITTED','VERIFIED','OUTCOME_UNKNOWN','FAILED')),
          constraint ck_side_effects_request_hash_length check (length(request_hash) = 64),
          constraint ck_side_effects_response_hash_length check (response_hash is null or length(response_hash) = 64)
        );
        create index ix_side_effects_run_created on runtime.side_effects (run_id, created_at);
        create index ix_side_effects_effect_id on runtime.side_effects (effect_proposal_id);

        create table runtime.jobs (
          id uuid primary key,
          job_type text not null,
          run_id uuid references runtime.runs(id) on delete cascade,
          status text not null default 'pending',
          priority integer not null default 100,
          attempts integer not null default 0,
          available_at timestamptz not null,
          claimed_at timestamptz,
          worker_id text,
          payload jsonb not null,
          last_error text,
          created_at timestamptz not null default now(),
          constraint ck_jobs_status_allowed check (status in ('pending','processing','completed','failed')),
          constraint ck_jobs_attempts_nonnegative check (attempts >= 0)
        );
        create index ix_jobs_run_id on runtime.jobs (run_id);
        create index ix_jobs_pending_priority_available on runtime.jobs (priority, available_at) where status = 'pending';

        create table sandbox.replacement_bookings (
          id uuid primary key,
          run_id uuid not null references runtime.runs(id) on delete cascade,
          effect_proposal_id uuid not null references runtime.effect_proposals(id) on delete cascade,
          idempotency_key text not null,
          original_reservation_id text not null,
          traveler_id text not null,
          booking_reference text not null,
          approved_context_hash varchar(64) not null,
          contract_hash varchar(64) not null,
          semantic_context jsonb not null,
          total_additional_cost_minor bigint not null,
          currency varchar(3) not null,
          status text not null default 'confirmed',
          committed_at timestamptz not null,
          verified_at timestamptz,
          created_at timestamptz not null default now(),
          constraint uq_replacement_bookings_idempotency_key unique (idempotency_key),
          constraint uq_replacement_bookings_booking_reference unique (booking_reference),
          constraint ck_replacement_bookings_status_allowed check (status in ('confirmed','cancelled','voided','outcome_unknown')),
          constraint ck_replacement_bookings_cost_nonnegative check (total_additional_cost_minor >= 0),
          constraint ck_replacement_bookings_currency_iso_shape check (currency ~ '^[A-Z]{3}$'),
          constraint ck_replacement_bookings_context_hash_length check (length(approved_context_hash) = 64),
          constraint ck_replacement_bookings_contract_hash_length check (length(contract_hash) = 64)
        );
        create index ix_replacement_bookings_run_id on sandbox.replacement_bookings (run_id);
        create unique index ix_replacement_bookings_effect_id on sandbox.replacement_bookings (effect_proposal_id);
        create unique index uq_replacement_bookings_original_confirmed
          on sandbox.replacement_bookings (original_reservation_id) where status = 'confirmed';
        """
    )

    op.execute(
        """
        create function runtime.reject_run_event_mutation() returns trigger
        language plpgsql set search_path = '' as $$
        begin
          if tg_op = 'UPDATE' then
            raise exception 'run_events is append-only' using errcode = '55000';
          end if;
          if exists (select 1 from runtime.runs where id = old.run_id) then
            raise exception 'run_events may only be deleted by parent retention cleanup'
              using errcode = '55000';
          end if;
          return old;
        end;
        $$;
        create trigger trg_run_events_append_only
          before update or delete on runtime.run_events
          for each row execute function runtime.reject_run_event_mutation();

        create function runtime.reject_terminal_run_mutation() returns trigger
        language plpgsql set search_path = '' as $$
        begin
          if old.status in ('SUCCEEDED','PARTIAL_SUCCESS','HANDOFF_REQUIRED',
                            'FAILED_OUTCOME_UNKNOWN','SAFE_ABORTED','FAILED','CANCELLED')
             and new is distinct from old then
            raise exception 'terminal run is immutable' using errcode = '55000';
          end if;
          return new;
        end;
        $$;
        create trigger trg_runs_terminal_immutable
          before update on runtime.runs
          for each row execute function runtime.reject_terminal_run_mutation();

        create function runtime.delete_expired_public_run(target_run_id uuid)
        returns boolean
        language plpgsql
        security definer
        set search_path = '' as $$
        declare
          deleted_count integer;
        begin
          delete from runtime.runs
          where id = target_run_id
            and retention_class = 'public_ephemeral'
            and created_at <= now() - interval '24 hours';
          get diagnostics deleted_count = row_count;
          return deleted_count = 1;
        end;
        $$;
        revoke all on function runtime.delete_expired_public_run(uuid) from public;
        """
    )

    # Local and deployed role bootstraps may differ. Grants are applied only when
    # the least-privilege roles already exist; CI's bare Postgres service therefore
    # does not need development passwords or role creation in a schema migration.
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'runtime_app') then
            revoke all privileges on all tables in schema runtime, sandbox from runtime_app;
            revoke all privileges on all sequences in schema runtime, sandbox from runtime_app;
            grant usage on schema runtime, sandbox to runtime_app;
            grant select, insert on runtime.task_contracts, runtime.run_events to runtime_app;
            grant usage, select on sequence runtime.run_events_id_seq to runtime_app;
            grant select, insert, update, delete on runtime.demo_sessions,
              runtime.artifacts, runtime.jobs to runtime_app;
            grant select, insert on runtime.runs to runtime_app;
            grant update (status, started_at, finished_at, terminal_reason, step_count,
              model_call_count, model_cost_usd) on runtime.runs to runtime_app;
            grant select, insert on runtime.action_proposals, runtime.policy_decisions to runtime_app;
            grant select, insert on runtime.effect_proposals to runtime_app;
            grant update (status) on runtime.effect_proposals to runtime_app;
            grant select, insert on runtime.approval_requests to runtime_app;
            grant update (status, decided_at, decision_source)
              on runtime.approval_requests to runtime_app;
            grant select, insert on runtime.approval_grants to runtime_app;
            grant update (status, used_at) on runtime.approval_grants to runtime_app;
            grant select, insert on runtime.side_effects to runtime_app;
            grant update (status, response_hash, verified_at) on runtime.side_effects to runtime_app;
            grant select, insert on sandbox.replacement_bookings to runtime_app;
            grant update (status, verified_at) on sandbox.replacement_bookings to runtime_app;
            grant execute on function runtime.delete_expired_public_run(uuid) to runtime_app;
          end if;
          if exists (select 1 from pg_roles where rolname = 'eval_oracle') then
            revoke all privileges on all tables in schema runtime, sandbox from eval_oracle;
            grant usage on schema runtime, sandbox to eval_oracle;
            grant select on all tables in schema runtime, sandbox to eval_oracle;
          end if;
        end;
        $$;
        """
    )


def downgrade() -> None:
    op.execute("drop function if exists runtime.delete_expired_public_run(uuid)")
    op.execute("drop trigger if exists trg_runs_terminal_immutable on runtime.runs")
    op.execute("drop function if exists runtime.reject_terminal_run_mutation()")
    op.execute("drop trigger if exists trg_run_events_append_only on runtime.run_events")
    op.execute("drop function if exists runtime.reject_run_event_mutation()")
    op.execute("drop table if exists sandbox.replacement_bookings")
    for table in (
        "jobs",
        "side_effects",
        "approval_grants",
        "approval_requests",
        "policy_decisions",
        "effect_proposals",
        "action_proposals",
        "artifacts",
        "run_events",
        "runs",
        "task_contracts",
        "demo_sessions",
    ):
        op.execute(f"drop table if exists runtime.{table}")
