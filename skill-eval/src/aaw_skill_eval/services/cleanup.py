from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..models import Run


def cleanup_expired_workspaces(
    settings: Settings,
    session_factory: sessionmaker[Session],
    *,
    now: datetime | None = None,
) -> int:
    """Remove retained failure workspaces after the configured grace period."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=settings.failed_workspace_retention_days)
    workspace_root = settings.workspaces_dir.resolve()
    cleaned = 0

    with session_factory() as session:
        runs = session.scalars(
            select(Run).where(
                Run.workspace_retained.is_(True),
                Run.completed_at.is_not(None),
                Run.completed_at < cutoff,
            )
        ).all()
        for run in runs:
            candidate = (settings.workspaces_dir / run.experiment_id / "runs" / run.id).resolve()
            if not candidate.is_relative_to(workspace_root):
                continue
            if candidate.exists():
                try:
                    shutil.rmtree(candidate)
                except OSError:
                    continue
            run.workspace_retained = False
            cleaned += 1
        session.commit()
    return cleaned
