from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from .models import Experiment
from .services.cleanup import cleanup_expired_workspaces
from .services.orchestrator import ExperimentOrchestrator


class JobManager:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        orchestrator: ExperimentOrchestrator,
    ) -> None:
        self.session_factory = session_factory
        self.orchestrator = orchestrator
        self.queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.worker: asyncio.Task | None = None

    async def start(self) -> None:
        cleanup_expired_workspaces(
            self.orchestrator.settings,
            self.session_factory,
        )
        with self.session_factory() as session:
            session.execute(
                update(Experiment)
                .where(Experiment.status.in_(["preparing", "running"]))
                .values(
                    status="interrupted",
                    error_kind="infra_error",
                    error_message="Service restarted",
                )
            )
            queued = list(
                session.scalars(
                    select(Experiment.id)
                    .where(Experiment.status == "queued")
                    .order_by(Experiment.created_at)
                )
            )
            session.commit()
        self.worker = asyncio.create_task(self._run(), name="skill-eval-worker")
        for experiment_id in queued:
            await self.queue.put(experiment_id)

    async def stop(self) -> None:
        if self.worker is None:
            return
        await self.queue.put(None)
        with suppress(asyncio.CancelledError):
            await self.worker
        self.worker = None

    async def enqueue(self, experiment_id: str) -> None:
        await self.queue.put(experiment_id)

    async def _run(self) -> None:
        while True:
            experiment_id = await self.queue.get()
            try:
                if experiment_id is None:
                    return
                await asyncio.to_thread(self.orchestrator.execute, experiment_id)
            except Exception as exc:
                with self.session_factory() as session:
                    experiment = session.get(Experiment, experiment_id)
                    if experiment is not None:
                        experiment.status = "failed"
                        experiment.error_kind = "infra_error"
                        experiment.error_message = f"{type(exc).__name__}: {exc}"[:10_000]
                        experiment.completed_at = datetime.now(UTC)
                        session.commit()
            finally:
                self.queue.task_done()
