"""Add portal issue tracker tables.

Revision ID: 0008_issue_tracker
Revises: 0007_real_attribution_status
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "0008_issue_tracker"
down_revision = "0007_real_attribution_status"
branch_labels = None
depends_on = None

_DATETIME = sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=3), "mysql")


def upgrade() -> None:
    op.create_table(
        "issue",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("assignee", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("component", sa.String(128), nullable=True),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("sr", sa.String(128), nullable=True),
        sa.Column("ar", sa.String(128), nullable=True),
        sa.Column("created_at", _DATETIME, nullable=False),
        sa.Column("updated_at", _DATETIME, nullable=False),
        sa.Column("resolved_at", _DATETIME, nullable=True),
        sa.CheckConstraint(
            "assignee IN ('张轶勃', '徐哲威', '宋东方', '张立肖', '孙杨宇鑫')",
            name="ck_issue_assignee",
        ),
        sa.CheckConstraint("status IN ('todo', 'in_progress', 'resolved')", name="ck_issue_status"),
        sa.CheckConstraint("priority IN ('low', 'medium', 'high')", name="ck_issue_priority"),
        sa.ForeignKeyConstraint(["workflow_run_id"], ["workflow_run.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_issue_status_updated", "issue", ["status", "updated_at"])
    op.create_index("ix_issue_assignee_updated", "issue", ["assignee", "updated_at"])
    op.create_table(
        "issue_activity",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("issue_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", _DATETIME, nullable=False),
        sa.ForeignKeyConstraint(["issue_id"], ["issue.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_issue_activity_issue_created", "issue_activity", ["issue_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_issue_activity_issue_created", table_name="issue_activity")
    op.drop_table("issue_activity")
    op.drop_index("ix_issue_assignee_updated", table_name="issue")
    op.drop_index("ix_issue_status_updated", table_name="issue")
    op.drop_table("issue")
