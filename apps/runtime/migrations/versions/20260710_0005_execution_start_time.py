"""Record the actual start time of each evaluation attempt.

Revision ID: 20260710_0005
Revises: 20260710_0004
Create Date: 2026-07-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260710_0005"
down_revision: str | None = "20260710_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("alter table runtime.eval_executions add column started_at timestamptz")
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'runtime_app') then
            grant update (started_at) on runtime.eval_executions to runtime_app;
          end if;
        end;
        $$;
        """
    )


def downgrade() -> None:
    op.execute("alter table runtime.eval_executions drop column if exists started_at")
