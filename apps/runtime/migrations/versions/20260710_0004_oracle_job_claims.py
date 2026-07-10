"""Allow the sealed oracle role to claim only existing evaluation jobs.

Revision ID: 20260710_0004
Revises: 20260710_0003
Create Date: 2026-07-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260710_0004"
down_revision: str | None = "20260710_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'eval_oracle') then
            grant update (status, attempts, available_at, claimed_at, worker_id, last_error)
              on runtime.jobs to eval_oracle;
          end if;
        end;
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        do $$
        begin
          if exists (select 1 from pg_roles where rolname = 'eval_oracle') then
            revoke update (status, attempts, available_at, claimed_at, worker_id, last_error)
              on runtime.jobs from eval_oracle;
          end if;
        end;
        $$;
        """
    )
