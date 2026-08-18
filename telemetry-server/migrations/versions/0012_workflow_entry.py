"""Persist the AAW workflow entry and backfill it from the first step."""

import sqlalchemy as sa
from alembic import op

revision = "0012_workflow_entry"
down_revision = "0011_issue_images"
branch_labels = None
depends_on = None


def _entry_for_step(step_type: str) -> str | None:
    return {"ar-init": "ar", "sr-init": "sr"}.get(step_type)


def upgrade() -> None:
    op.add_column("workflow_run", sa.Column("entry", sa.String(8), nullable=True))
    op.add_column("telemetry_message", sa.Column("entry", sa.String(8), nullable=True))

    connection = op.get_bind()
    workflow_run = sa.table(
        "workflow_run",
        sa.column("id"),
        sa.column("entry"),
    )
    telemetry_message = sa.table(
        "telemetry_message",
        sa.column("workflow_run_id"),
        sa.column("step_started_at"),
        sa.column("step_type"),
        sa.column("entry"),
    )

    first_started_at = {}
    first_step_types: dict[object, set[str | None]] = {}
    rows = connection.execute(
        sa.select(
            telemetry_message.c.workflow_run_id,
            telemetry_message.c.step_started_at,
            telemetry_message.c.step_type,
        ).order_by(
            telemetry_message.c.workflow_run_id,
            telemetry_message.c.step_started_at,
        )
    ).mappings()
    for row in rows:
        workflow_id = row["workflow_run_id"]
        started_at = row["step_started_at"]
        if workflow_id not in first_started_at or started_at < first_started_at[workflow_id]:
            first_started_at[workflow_id] = started_at
            first_step_types[workflow_id] = {_entry_for_step(row["step_type"])}
        elif started_at == first_started_at[workflow_id]:
            first_step_types[workflow_id].add(_entry_for_step(row["step_type"]))

    entries = {}
    for workflow_id, step_types in first_step_types.items():
        known = {value for value in step_types if value is not None}
        entries[workflow_id] = known.pop() if len(known) == 1 else None

    for workflow_id, entry in entries.items():
        connection.execute(
            workflow_run.update()
            .where(workflow_run.c.id == workflow_id)
            .values(entry=entry)
        )
        connection.execute(
            telemetry_message.update()
            .where(telemetry_message.c.workflow_run_id == workflow_id)
            .values(entry=entry)
        )

    op.create_index(
        "ix_workflow_entry_started",
        "workflow_run",
        ["entry", "started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_entry_started", table_name="workflow_run")
    op.drop_column("telemetry_message", "entry")
    op.drop_column("workflow_run", "entry")
