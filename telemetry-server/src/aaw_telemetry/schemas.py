from __future__ import annotations

import re
import uuid
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


UnixMilliseconds = Annotated[StrictInt, Field(ge=0, le=253402300799999)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class StepFile(StrictModel):
    file_name: str = Field(min_length=1, max_length=255)
    sha256: Sha256

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        if "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("file_name must be a plain file name without a path")
        return value


class StepMessageData(StrictModel):
    ar: str | None = Field(default=None, min_length=1, max_length=128)
    step_id: int | None = Field(default=None, ge=1)
    step_type: str = Field(min_length=1, max_length=128)
    step_name: str | None = Field(default=None, min_length=1, max_length=256)
    attempt: int | None = Field(default=None, ge=1)
    execution_type: Literal["skill", "prompt", "manual", "noop"] | None = None
    skill_names: list[str] | None = None
    task_id: str | None = Field(default=None, min_length=1, max_length=128)
    status: Literal["start", "done", "failed", "blocked"]
    started_at: UnixMilliseconds
    completed_at: UnixMilliseconds | None
    file: StepFile | None = None
    development: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_step(self) -> StepMessageData:
        if self.status == "done" and self.completed_at is None:
            raise ValueError("data.completed_at is required when status is done")
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("data.completed_at must not be earlier than data.started_at")
        requires_file = self.step_type == "task-dev" and self.status == "done"
        if requires_file and self.file is None:
            raise ValueError("data.file is required when task-dev is done")
        if not requires_file and self.file is not None:
            raise ValueError("data.file is only allowed when task-dev is done")
        identity = [
            self.step_id,
            self.step_name,
            self.attempt,
            self.execution_type,
            self.skill_names,
        ]
        if any(value is not None for value in identity) and any(
            value is None for value in identity
        ):
            raise ValueError(
                "data.step_id, step_name, attempt, execution_type and skill_names "
                "must be provided together"
            )
        if self.development is not None and not requires_file:
            raise ValueError("data.development is only allowed when task-dev is done")
        return self


class TelemetrySyncRequest(StrictModel):
    message_id: uuid.UUID
    workflow_id: uuid.UUID
    aaw_version: str = Field(min_length=1, max_length=64)
    user_email: str = Field(min_length=1, max_length=320)
    user_name: str
    repository: str = Field(min_length=1, max_length=128)
    sr: str = Field(min_length=1, max_length=128)
    started_at: UnixMilliseconds
    completed_at: UnixMilliseconds | None = None
    updated_at: UnixMilliseconds
    data: StepMessageData

    @field_validator("user_email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized or not re.fullmatch(r"[^@\s]+@[^@\s]+", normalized):
            raise ValueError("user_email must be a valid non-empty email address")
        return normalized

    @model_validator(mode="after")
    def validate_timeline(self) -> TelemetrySyncRequest:
        if self.data.started_at < self.started_at:
            raise ValueError("data.started_at must not be earlier than workflow started_at")
        if self.data.completed_at is not None and self.data.completed_at > self.updated_at:
            raise ValueError("data.completed_at must not be later than updated_at")
        if self.completed_at is not None:
            if self.data.completed_at is not None and self.completed_at < self.data.completed_at:
                raise ValueError("workflow completed_at must not precede data.completed_at")
            if self.completed_at < self.data.started_at:
                raise ValueError("workflow completed_at must not precede data.started_at")
            if self.completed_at > self.updated_at:
                raise ValueError("workflow completed_at must not be later than updated_at")
        return self


class ResultError(StrictModel):
    code: str
    message: str
    retryable: bool = False


class TelemetrySyncResponse(StrictModel):
    request_id: str
    message_id: uuid.UUID
    status: Literal["accepted", "duplicate", "rejected"]
    server_updated_at: int | None = None
    error: ResultError | None = None


class ErrorResponse(StrictModel):
    request_id: str
    code: str
    message: str
    retryable: bool


class ClientReleaseResponse(StrictModel):
    latest_version: str | None = None
    file_name: str | None = None
    size_bytes: int | None = None
    released_at: str | None = None


class DiffUploadResponse(StrictModel):
    request_id: str
    message_id: uuid.UUID
    status: Literal["confirmed"]
    object_key: str
    sha256: Sha256
    confirmed_at: int


IssueAssignee = Literal["张轶勃", "徐哲威", "宋东方", "张立肖", "孙杨宇鑫"]
IssueStatus = Literal["todo", "in_progress", "resolved"]
IssuePriority = Literal["low", "medium", "high"]


class IssueCreate(StrictModel):
    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=10_000)
    reporter: str = Field(min_length=1, max_length=100)
    assignee: IssueAssignee
    priority: IssuePriority = "medium"
    status: IssueStatus = "todo"
    component: str | None = Field(default=None, max_length=128)
    workflow_run_id: uuid.UUID | None = None
    sr: str | None = Field(default=None, max_length=128)
    ar: str | None = Field(default=None, max_length=128)

    @field_validator("title", "description", "reporter")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("component", "sr", "ar")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None


class IssueUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, min_length=1, max_length=10_000)
    reporter: str | None = Field(default=None, min_length=1, max_length=100)
    assignee: IssueAssignee | None = None
    priority: IssuePriority | None = None
    status: IssueStatus | None = None
    component: str | None = Field(default=None, max_length=128)
    workflow_run_id: uuid.UUID | None = None
    sr: str | None = Field(default=None, max_length=128)
    ar: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def require_change(self) -> IssueUpdate:
        if not self.model_fields_set:
            raise ValueError("at least one field is required")
        return self

    @field_validator("title", "description", "reporter")
    @classmethod
    def strip_updated_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("component", "sr", "ar")
    @classmethod
    def strip_updated_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value and value.strip() else None
