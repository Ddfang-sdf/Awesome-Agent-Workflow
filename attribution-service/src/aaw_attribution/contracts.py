"""Versioned public HTTP contract for attribution requests."""

from __future__ import annotations

import base64
import binascii
import hashlib
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACT_VERSION = "1.0"


class ProjectContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=128)
    canonical_url: str | None = Field(default=None, max_length=2048)
    target_branch: str | None = Field(default=None, max_length=512)


class DevelopmentContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch: str = Field(max_length=512)
    head_sha_start: str = Field(max_length=64)
    head_sha_end: str | None = Field(default=None, max_length=64)
    completed_at: datetime | None = None


class TelemetryContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repository: str = Field(min_length=1, max_length=128)
    sr: str = Field(min_length=1, max_length=128)
    ar: str | None = Field(default=None, max_length=128)
    user_email: str = Field(min_length=1, max_length=320)


class DiffPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    content_base64: str = Field(min_length=1)
    statistics: dict

    def decode_and_verify(self) -> bytes:
        try:
            content = base64.b64decode(self.content_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("content_base64 is not valid base64") from exc
        if hashlib.sha256(content).hexdigest() != self.sha256:
            raise ValueError("diff content does not match sha256")
        return content


class AttributionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = CONTRACT_VERSION
    request_id: uuid.UUID
    project: ProjectContext
    development: DevelopmentContext
    telemetry: TelemetryContext
    diff: DiffPayload

    @model_validator(mode="after")
    def validate_diff(self) -> AttributionRequest:
        self.diff.decode_and_verify()
        return self


class AttributionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = CONTRACT_VERSION
    request_id: uuid.UUID
    result_status: Literal["finalized_match", "finalized_no_match"]
    dev_effective_lines: int = Field(ge=0)
    attributed_lines_80: int = Field(ge=0)
    attributed_lines_90: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    quality_flags: list[str] = Field(default_factory=list)
    matched_mr_iid: str | None = None
    matched_mr_url: str | None = None
    mr_diff_version: str | None = None
    mr_source_branch: str | None = None
    target_branch: str | None = None
    merge_commit_sha: str | None = None
    mr_merged_at: datetime | None = None
    algorithm_version: str = Field(min_length=1, max_length=64)
    diff_rule_version: str = Field(min_length=1, max_length=64)
    matched_at: datetime
