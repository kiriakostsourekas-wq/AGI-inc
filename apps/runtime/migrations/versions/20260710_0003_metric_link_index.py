"""Add the leftmost metric-result foreign-key index.

Revision ID: 20260710_0003
Revises: 20260710_0002
Create Date: 2026-07-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260710_0003"
down_revision: str | None = "20260710_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_metric_execution_links_metric_id",
        "metric_execution_links",
        ["metric_result_id"],
        schema="runtime",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_metric_execution_links_metric_id",
        table_name="metric_execution_links",
        schema="runtime",
    )
