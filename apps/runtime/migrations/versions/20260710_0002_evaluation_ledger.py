# ruff: noqa: E501
"""Create immutable-intent evaluation and metric provenance tables.

Revision ID: 20260710_0002
Revises: 20260710_0001
Create Date: 2026-07-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260710_0002"
down_revision: str | None = "20260710_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        create table runtime.evaluation_batches (
          id uuid primary key,
          plan_id text not null,
          status text not null default 'queued',
          requested_by text not null,
          maximum_total_cost_usd numeric(12,4) not null,
          intended_execution_count integer not null,
          manifest_hash varchar(64) not null,
          manifest jsonb not null,
          created_at timestamptz not null default now(),
          started_at timestamptz,
          completed_at timestamptz,
          last_error text,
          constraint ck_evaluation_batches_status_allowed check (
            status in ('queued','running','completed','failed','cancelled')
          ),
          constraint ck_evaluation_batches_cost_cap_positive check (maximum_total_cost_usd > 0),
          constraint ck_evaluation_batches_intent_count_positive check (intended_execution_count > 0),
          constraint ck_evaluation_batches_manifest_hash_length check (length(manifest_hash) = 64)
        );
        create index ix_evaluation_batches_status_created
          on runtime.evaluation_batches(status, created_at);

        create table runtime.eval_cases (
          id uuid primary key,
          evaluation_id uuid not null references runtime.evaluation_batches(id) on delete cascade,
          case_id text not null,
          fault_class text not null,
          seed integer not null,
          manifest_hash varchar(64) not null,
          case_manifest jsonb not null,
          created_at timestamptz not null default now(),
          constraint uq_eval_cases_batch_case unique (evaluation_id, case_id),
          constraint ck_eval_cases_manifest_hash_length check (length(manifest_hash) = 64)
        );
        create index ix_eval_cases_evaluation_id on runtime.eval_cases(evaluation_id);

        create table runtime.eval_executions (
          id uuid primary key,
          evaluation_id uuid not null references runtime.evaluation_batches(id) on delete cascade,
          eval_case_id uuid not null references runtime.eval_cases(id) on delete cascade,
          run_id uuid references runtime.runs(id) on delete set null,
          original_execution_id uuid references runtime.eval_executions(id) on delete restrict,
          arm text not null,
          attempt_kind text not null default 'original',
          status text not null default 'intended',
          oracle_version text not null,
          raw_predicate_results jsonb not null,
          invalid_reason text,
          model_cost_usd numeric(12,4) not null default 0,
          created_at timestamptz not null default now(),
          finished_at timestamptz,
          constraint ck_eval_executions_arm_allowed check (arm in ('baseline','protected')),
          constraint ck_eval_executions_status_allowed check (
            status in ('intended','running','valid','infrastructure_invalid','failed')
          ),
          constraint ck_eval_executions_attempt_kind_allowed check (
            attempt_kind in ('original','replacement')
          ),
          constraint ck_eval_executions_model_cost_nonnegative check (model_cost_usd >= 0),
          constraint uq_eval_execution_intent unique (
            evaluation_id, eval_case_id, arm, attempt_kind
          )
        );
        create index ix_eval_executions_evaluation_id on runtime.eval_executions(evaluation_id);
        create index ix_eval_executions_case_id on runtime.eval_executions(eval_case_id);
        create index ix_eval_executions_run_id on runtime.eval_executions(run_id);
        create index ix_eval_executions_original_id on runtime.eval_executions(original_execution_id);

        create table runtime.metric_results (
          id uuid primary key,
          evaluation_id uuid not null references runtime.evaluation_batches(id) on delete cascade,
          metric_name text not null,
          metric_value numeric(18,8) not null,
          confidence_low numeric(18,8),
          confidence_high numeric(18,8),
          report_version text not null,
          created_at timestamptz not null default now(),
          constraint uq_metric_result_version unique (evaluation_id, metric_name, report_version)
        );
        create index ix_metric_results_evaluation_id on runtime.metric_results(evaluation_id);

        create table runtime.metric_execution_links (
          metric_result_id uuid not null references runtime.metric_results(id) on delete cascade,
          execution_id uuid not null references runtime.eval_executions(id) on delete cascade,
          primary key (metric_result_id, execution_id)
        );
        create index ix_metric_execution_links_execution_id
          on runtime.metric_execution_links(execution_id);
        """
    )
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'runtime_app') then
            grant select, insert on runtime.evaluation_batches, runtime.eval_cases,
              runtime.eval_executions to runtime_app;
            grant update (status, started_at, completed_at, last_error)
              on runtime.evaluation_batches to runtime_app;
            grant update (run_id, status, raw_predicate_results, invalid_reason,
              model_cost_usd, finished_at) on runtime.eval_executions to runtime_app;
            grant select on runtime.metric_results, runtime.metric_execution_links to runtime_app;
          end if;
          if exists (select 1 from pg_roles where rolname = 'eval_oracle') then
            grant select, insert, update on runtime.evaluation_batches, runtime.eval_cases,
              runtime.eval_executions, runtime.metric_results,
              runtime.metric_execution_links to eval_oracle;
          end if;
        end;
        $$;
        """
    )


def downgrade() -> None:
    for table in (
        "metric_execution_links",
        "metric_results",
        "eval_executions",
        "eval_cases",
        "evaluation_batches",
    ):
        op.execute(f"drop table if exists runtime.{table}")
