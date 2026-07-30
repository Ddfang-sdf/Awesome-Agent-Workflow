"""Add structured issue descriptions and image attachments.

Revision ID: 0011_issue_images
Revises: 0010_workflow_kind
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_issue_images"
down_revision = "0010_workflow_kind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("issue", sa.Column("description_doc", sa.JSON(), nullable=True))
    op.add_column(
        "issue",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.alter_column("issue", "version", server_default=None)
    op.create_table(
        "issue_image",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("issue_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("preview_size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("full_object_key", sa.String(512), nullable=False),
        sa.Column("preview_object_key", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delete_after", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('temporary', 'bound', 'pending_delete')",
            name="ck_issue_image_status",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_issue_image_size_positive"),
        sa.CheckConstraint(
            "width > 0 AND height > 0",
            name="ck_issue_image_dimensions_positive",
        ),
        sa.ForeignKeyConstraint(["issue_id"], ["issue.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("full_object_key"),
        sa.UniqueConstraint("preview_object_key"),
    )
    op.create_index("ix_issue_image_status_created", "issue_image", ["status", "created_at"])
    op.create_index("ix_issue_image_delete_after", "issue_image", ["delete_after"])
    op.create_index("ix_issue_image_issue", "issue_image", ["issue_id"])


def downgrade() -> None:
    op.drop_index("ix_issue_image_issue", table_name="issue_image")
    op.drop_index("ix_issue_image_delete_after", table_name="issue_image")
    op.drop_index("ix_issue_image_status_created", table_name="issue_image")
    op.drop_table("issue_image")
    op.drop_column("issue", "version")
    op.drop_column("issue", "description_doc")
