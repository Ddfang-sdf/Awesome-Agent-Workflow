"""Add reporter to portal issues.

Revision ID: 0009_issue_reporter
Revises: 0008_issue_tracker
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_issue_reporter"
down_revision = "0008_issue_tracker"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing deployments may already have issues, so add a temporary default before
    # enforcing the non-null application invariant.
    op.add_column(
        "issue",
        sa.Column("reporter", sa.String(100), nullable=False, server_default="未知提出人"),
    )
    op.alter_column("issue", "reporter", server_default=None)


def downgrade() -> None:
    op.drop_column("issue", "reporter")
