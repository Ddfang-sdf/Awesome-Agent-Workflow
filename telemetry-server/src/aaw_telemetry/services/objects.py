from __future__ import annotations

import hashlib
import logging
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import ProjectRegistry, Settings
from ..errors import ApiError
from ..models import CodeAttribution, DevRun, ObjectUpload, TelemetryMessage
from .attribution_service import (
    AttributionRequest,
    AttributionResult,
    AttributionService,
    AttributionServiceError,
    DevelopmentContext,
    DiffPayload,
    ProjectContext,
    TelemetryContext,
)

logger = logging.getLogger("aaw_telemetry.objects.diff")


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ObjectService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        projects: ProjectRegistry,
        attribution_service: AttributionService,
    ):
        self.session = session
        self.settings = settings
        self.projects = projects
        self.attribution_service = attribution_service
        self.root = settings.object_storage_dir.resolve()

    async def upload_diff(
        self, message_id: uuid.UUID, stream: AsyncIterator[bytes], *, workflow_kind: str = "aaw"
    ) -> ObjectUpload:
        now = datetime.now(UTC)
        message = self.session.get(TelemetryMessage, message_id)
        if message is None:
            raise ApiError(404, "MESSAGE_NOT_FOUND", "Step message does not exist")
        if message.workflow_kind != workflow_kind:
            raise ApiError(404, "MESSAGE_NOT_FOUND", "Step message does not exist")
        dev_run = self.session.get(DevRun, message_id)
        if dev_run is None or message.file_sha256 is None:
            raise ApiError(
                409,
                "FILE_CONFLICT",
                "Step message is not a completed task-dev message with a Diff",
            )
        existing = self.session.scalar(
            select(ObjectUpload).where(ObjectUpload.owner_id == message_id)
        )
        is_confirmed_retry = existing is not None and existing.status == "confirmed"
        if not is_confirmed_retry and (
            dev_run.status != "waiting_objects" or dev_run.window_ends_at is None
        ):
            raise ApiError(
                409,
                "FILE_CONFLICT",
                "Dev run is not waiting for a Diff upload",
            )
        if (
            not is_confirmed_retry
            and dev_run.window_ends_at is not None
            and now >= _utc(dev_run.window_ends_at)
        ):
            raise ApiError(409, "UPLOAD_WINDOW_EXPIRED", "Dev Patch upload window has expired")

        object_key = existing.object_key if existing else f"step-diffs/{message_id}.diff"
        target = self._object_path(object_key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + f".{uuid.uuid4().hex}.part")
        digest = hashlib.sha256()
        received = 0
        try:
            with temporary.open("wb") as output:
                async for chunk in stream:
                    if not chunk:
                        continue
                    received += len(chunk)
                    if received > self.settings.max_patch_bytes:
                        raise ApiError(
                            413,
                            "PAYLOAD_TOO_LARGE",
                            "Diff exceeds configured limit",
                        )
                    digest.update(chunk)
                    output.write(chunk)
            if received == 0:
                raise ApiError(400, "INVALID_REQUEST", "Diff must not be empty")
            if digest.hexdigest() != message.file_sha256:
                raise ApiError(
                    422,
                    "FILE_HASH_MISMATCH",
                    "uploaded Diff SHA-256 does not match the Step declaration",
                )
            if is_confirmed_retry and target.is_file():
                temporary.unlink(missing_ok=True)
                self._retry_failed_attribution(dev_run, message, target.read_bytes(), now)
                return existing
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        upload = existing or ObjectUpload(
            id=uuid.uuid4(),
            object_type="step_diff",
            owner_id=message_id,
            sha256=message.file_sha256,
            compressed_size_bytes=received,
            compression="none",
            status="confirmed",
            object_key=object_key,
            expires_at=_utc(dev_run.window_ends_at),
            uploaded_at=now,
            confirmed_at=now,
            server_updated_at=now,
        )
        if existing is None:
            self.session.add(upload)
        else:
            upload.sha256 = message.file_sha256
            upload.compressed_size_bytes = received
            upload.compression = "none"
            upload.status = "confirmed"
            upload.object_key = object_key
            upload.uploaded_at = now
            upload.confirmed_at = now
        upload.server_updated_at = now
        statistics = self._diff_statistics(target.read_bytes())
        dev_run.code_statistics = statistics
        dev_run.patch_object_key = upload.object_key
        dev_run.status = "completed"
        dev_run.server_updated_at = now
        self._mark_attribution_pending(dev_run, now)
        self.session.commit()
        self._request_attribution(dev_run, message, target.read_bytes(), now)
        logger.info(
            "Dev Patch 上传并校验成功，开发步骤已进入归因处理",
            extra={
                "event": "objects.upload_confirmed",
                "upload_id": str(upload.id),
                "owner_id": str(upload.owner_id),
                "file_name": message.file_name,
                "bytes_received": received,
            },
        )
        return upload

    def _retry_failed_attribution(
        self,
        dev_run: DevRun,
        message: TelemetryMessage,
        diff_bytes: bytes,
        now: datetime,
    ) -> None:
        attribution = self.session.get(CodeAttribution, dev_run.id)
        if attribution is None or attribution.attribution_status in {"failed", "retry_pending"}:
            self._mark_attribution_pending(dev_run, now)
            self.session.commit()
            self._request_attribution(dev_run, message, diff_bytes, now)

    def _mark_attribution_pending(self, dev_run: DevRun, now: datetime) -> None:
        total = int((dev_run.code_statistics or {}).get("total_effective_lines", 0))
        attribution = self.session.get(CodeAttribution, dev_run.id)
        values = {
            "dev_effective_lines": total,
            "attributed_lines_80": 0,
            "attributed_lines_90": 0,
            "confidence": 0.0,
            "quality_flags": ["attribution_pending"],
            "result_status": "finalized_no_match",
            "attribution_status": "pending",
            "retry_count": 0,
            "next_retry_at": None,
            "matched_mr_iid": None,
            "matched_mr_url": None,
            "mr_diff_version": None,
            "mr_source_branch": None,
            "target_branch": None,
            "merge_commit_sha": None,
            "mr_merged_at": None,
            "algorithm_version": "pending",
            "diff_rule_version": "pending",
            "matched_at": now,
            "server_updated_at": now,
        }
        if attribution is None:
            self.session.add(CodeAttribution(dev_run_id=dev_run.id, **values))
            return
        for field, value in values.items():
            setattr(attribution, field, value)

    def _request_attribution(
        self,
        dev_run: DevRun,
        message: TelemetryMessage,
        diff_bytes: bytes,
        now: datetime,
    ) -> None:
        request = self._build_attribution_request(dev_run, message, diff_bytes)
        try:
            result = self.attribution_service.attribute(request)
        except AttributionServiceError:
            self._mark_attribution_failed(dev_run.id, now)
            logger.warning(
                "Attribution service request failed; the confirmed Diff was retained",
                extra={
                    "event": "attribution.remote_failed",
                    "dev_run_id": str(dev_run.id),
                },
                exc_info=True,
            )
            return
        self._persist_attribution_result(result, now)

    def _build_attribution_request(
        self,
        dev_run: DevRun,
        message: TelemetryMessage,
        diff_bytes: bytes,
    ) -> AttributionRequest:
        project_entry = self.projects.get(message.repository)
        return AttributionRequest(
            request_id=dev_run.id,
            project=ProjectContext(
                key=message.repository,
                canonical_url=project_entry.canonical_url if project_entry else None,
                target_branch=project_entry.target_branch if project_entry else None,
            ),
            development=DevelopmentContext(
                branch=dev_run.branch,
                head_sha_start=dev_run.head_sha_start,
                head_sha_end=dev_run.head_sha_end,
                completed_at=dev_run.completed_at,
            ),
            telemetry=TelemetryContext(
                repository=message.repository,
                sr=message.sr,
                ar=message.ar,
                user_email=message.user_email,
            ),
            diff=DiffPayload.from_bytes(diff_bytes, dev_run.code_statistics or {}),
        )

    def _persist_attribution_result(
        self,
        result: AttributionResult,
        now: datetime,
    ) -> None:
        attribution = self.session.get(CodeAttribution, result.request_id)
        if attribution is None:
            raise RuntimeError("pending attribution row is missing")
        values = result.model_dump(exclude={"schema_version", "request_id"})
        values["attribution_status"] = result.result_status
        values["next_retry_at"] = None
        values["server_updated_at"] = now
        for field, value in values.items():
            setattr(attribution, field, value)
        self.session.commit()

    def _mark_attribution_failed(self, dev_run_id: uuid.UUID, now: datetime) -> None:
        attribution = self.session.get(CodeAttribution, dev_run_id)
        if attribution is None:
            raise RuntimeError("pending attribution row is missing")
        attribution.attribution_status = "failed"
        attribution.retry_count += 1
        attribution.quality_flags = ["attribution_service_unavailable"]
        attribution.server_updated_at = now
        self.session.commit()

    @staticmethod
    def _diff_statistics(content: bytes) -> dict:
        """Count effective added lines in a unified Diff for MVP data-path verification."""
        text = content.decode("utf-8", errors="replace")
        effective_lines = sum(
            1
            for line in text.splitlines()
            if line.startswith("+") and not line.startswith("+++") and line[1:].strip()
        )
        files_changed = sum(1 for line in text.splitlines() if line.startswith("+++ b/"))
        return {
            "total_effective_lines": effective_lines,
            "files_changed": files_changed,
            "categories": {
                "production_source": {
                    "effective_lines": effective_lines,
                    "files_changed": files_changed,
                },
                "test_source": {"effective_lines": 0, "files_changed": 0},
                "sql": {"effective_lines": 0, "files_changed": 0},
                "shell": {"effective_lines": 0, "files_changed": 0},
                "configuration": {"effective_lines": 0, "files_changed": 0},
                "other_script": {"effective_lines": 0, "files_changed": 0},
            },
            "quality_flags": ["mock_diff_classification"],
        }

    def _object_path(self, object_key: str) -> Path:
        path = (self.root / object_key).resolve()
        if not path.is_relative_to(self.root):
            raise ApiError(500, "INTERNAL_ERROR", "invalid object storage path")
        return path
