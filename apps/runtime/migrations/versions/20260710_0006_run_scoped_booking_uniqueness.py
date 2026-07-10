"""Scope replacement-booking uniqueness to the isolated run namespace.

Revision ID: 20260710_0006
Revises: 20260710_0005
Create Date: 2026-07-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260710_0006"
down_revision: str | None = "20260710_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("drop index if exists sandbox.uq_replacement_bookings_original_confirmed")
    op.execute(
        """
        create unique index uq_replacement_bookings_original_confirmed
          on sandbox.replacement_bookings(run_id, original_reservation_id)
          where status = 'confirmed'
        """
    )


def downgrade() -> None:
    op.execute("drop index if exists sandbox.uq_replacement_bookings_original_confirmed")
    op.execute(
        """
        create unique index uq_replacement_bookings_original_confirmed
          on sandbox.replacement_bookings(original_reservation_id)
          where status = 'confirmed'
        """
    )
