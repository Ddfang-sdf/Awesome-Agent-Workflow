from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from ..config import Settings
from ..errors import ApiError
from ..models import Issue, IssueActivity, IssueImage
from ..schemas import (
    IssueCreate,
    IssueDescriptionDocument,
    IssueUpdate,
    issue_document_image_ids,
)
from ..services.issue_images import IssueImageRateLimiter, IssueImageService

ASSIGNEES = ["张轶勃", "徐哲威", "宋东方", "张立肖", "孙杨宇鑫"]


def _now() -> datetime:
    return datetime.now(UTC)


def _text_document(description: str) -> dict:
    return {"version": 1, "nodes": [{"type": "text", "text": description}]}


def _document_dict(document: IssueDescriptionDocument | None, description: str) -> dict:
    return (
        document.model_dump(mode="json")
        if document is not None
        else _text_document(description)
    )


def _document_ids(document: dict | None) -> list[uuid.UUID]:
    if not document:
        return []
    return [
        uuid.UUID(str(node["image_id"]))
        for node in document.get("nodes", [])
        if node.get("type") == "image"
    ]


def _issue_payload(issue: Issue, *, include_activities: bool = False) -> dict:
    document = issue.description_doc or _text_document(issue.description)
    result = {
        "id": str(issue.id),
        "title": issue.title,
        "description": issue.description,
        "description_doc": document,
        "image_count": len(_document_ids(document)),
        "version": issue.version,
        "reporter": issue.reporter,
        "assignee": issue.assignee,
        "status": issue.status,
        "priority": issue.priority,
        "component": issue.component,
        "workflow_run_id": str(issue.workflow_run_id) if issue.workflow_run_id else None,
        "sr": issue.sr,
        "ar": issue.ar,
        "created_at": issue.created_at,
        "updated_at": issue.updated_at,
        "resolved_at": issue.resolved_at,
    }
    if include_activities:
        result["activities"] = [
            {
                "id": str(activity.id),
                "action": activity.action,
                "details": activity.details,
                "created_at": activity.created_at,
            }
            for activity in issue.activities
        ]
    return result


def _get_issue(
    session: Session,
    issue_id: uuid.UUID,
    *,
    with_activities: bool = False,
    for_update: bool = False,
) -> Issue:
    statement = select(Issue).where(Issue.id == issue_id)
    if with_activities:
        statement = statement.options(selectinload(Issue.activities))
    if for_update:
        statement = statement.with_for_update()
    issue = session.scalar(statement)
    if issue is None:
        raise ApiError(404, "ISSUE_NOT_FOUND", "issue was not found")
    return issue


def _bind_document_images(
    session: Session,
    issue: Issue,
    old_ids: set[uuid.UUID],
    new_ids: set[uuid.UUID],
    now: datetime,
    settings: Settings,
) -> tuple[int, int]:
    images = (
        session.scalars(select(IssueImage).where(IssueImage.id.in_(new_ids))).all()
        if new_ids
        else []
    )
    by_id = {image.id: image for image in images}
    missing = new_ids - set(by_id)
    if missing:
        raise ApiError(400, "ISSUE_IMAGE_NOT_FOUND", "description references an unknown image")
    if sum(image.size_bytes for image in images) > settings.issue_image_max_total_bytes:
        raise ApiError(400, "ISSUE_IMAGE_TOTAL_TOO_LARGE", "issue images exceed 20 MiB total")

    for image in images:
        if image.status == "temporary":
            image.issue_id = issue.id
            image.status = "bound"
            image.bound_at = now
            image.delete_after = None
        elif image.issue_id == issue.id and image.status in {"bound", "pending_delete"}:
            image.status = "bound"
            image.delete_after = None
        else:
            raise ApiError(
                409,
                "ISSUE_IMAGE_ALREADY_BOUND",
                "an image is already bound to another issue",
            )

    removed_ids = old_ids - new_ids
    if removed_ids:
        removed = session.scalars(
            select(IssueImage).where(
                IssueImage.id.in_(removed_ids), IssueImage.issue_id == issue.id
            )
        ).all()
        for image in removed:
            image.status = "pending_delete"
            image.delete_after = now + timedelta(seconds=settings.issue_image_temp_seconds)
    return len(new_ids - old_ids), len(removed_ids)


def build_issues_router(session_dependency, settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["issues"])
    image_service = IssueImageService(settings)
    rate_limiter = IssueImageRateLimiter(settings.issue_image_uploads_per_minute)

    @router.get("/issues/options")
    def issue_options():
        return {
            "assignees": ASSIGNEES,
            "statuses": ["todo", "in_progress", "resolved"],
            "priorities": ["low", "medium", "high"],
            "images": {
                "types": ["image/png", "image/jpeg", "image/webp"],
                "max_bytes": settings.issue_image_max_bytes,
                "max_count": settings.issue_image_max_count,
                "max_total_bytes": settings.issue_image_max_total_bytes,
            },
        }

    @router.post("/issues/images", status_code=201)
    async def upload_issue_image(
        request: Request,
        image: Annotated[UploadFile, File()],
        session: Session = Depends(session_dependency),
    ):
        # Production Nginx overwrites X-Real-IP, so uploads are limited per originating client
        # instead of grouping every request under the loopback proxy address.
        client_ip = request.headers.get("x-real-ip") or (
            request.client.host if request.client else "-"
        )
        rate_limiter.check(client_ip, time.monotonic())
        payload = await image.read(settings.issue_image_max_bytes + 1)
        uploaded = image_service.create_temporary(session, payload)
        base = f"/issues/images/{uploaded.id}"
        return {
            "id": str(uploaded.id),
            "status": uploaded.status,
            "media_type": uploaded.media_type,
            "size_bytes": uploaded.size_bytes,
            "width": uploaded.width,
            "height": uploaded.height,
            "preview_url": f"{base}/preview",
            "original_url": f"{base}/original",
            "alt": "问题截图",
        }

    @router.get("/issues/images/{image_id}/{variant}")
    def read_issue_image(
        image_id: uuid.UUID,
        variant: Literal["preview", "original"],
        session: Session = Depends(session_dependency),
    ):
        image = session.get(IssueImage, image_id)
        if image is None:
            raise ApiError(404, "ISSUE_IMAGE_NOT_FOUND", "image was not found")
        path, media_type = image_service.path_for(image, variant)
        return FileResponse(
            path,
            media_type=media_type,
            headers={
                "Cache-Control": "private, max-age=31536000, immutable",
                "ETag": f'"{image.sha256}-{variant}"',
                "Content-Disposition": "inline",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.get("/issues")
    def list_issues(
        status: Literal["todo", "in_progress", "resolved"] | None = None,
        assignee: str | None = None,
        q: Annotated[str | None, Query(max_length=100)] = None,
        page: Annotated[int, Query(ge=1)] = 1,
        page_size: Annotated[int, Query(ge=1, le=100)] = 100,
        session: Session = Depends(session_dependency),
    ):
        statement = select(Issue)
        if status:
            statement = statement.where(Issue.status == status)
        if assignee:
            if assignee not in ASSIGNEES:
                raise ApiError(400, "INVALID_ASSIGNEE", "assignee is not supported")
            statement = statement.where(Issue.assignee == assignee)
        if q and q.strip():
            pattern = f"%{q.strip()}%"
            statement = statement.where(
                or_(Issue.title.ilike(pattern), Issue.description.ilike(pattern))
            )
        total = len(session.scalars(statement).all())
        issues = session.scalars(
            statement.order_by(Issue.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        return {
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": [_issue_payload(issue) for issue in issues],
        }

    @router.post("/issues", status_code=201)
    def create_issue(payload: IssueCreate, session: Session = Depends(session_dependency)):
        now = _now()
        values = payload.model_dump(exclude={"description_doc"})
        issue = Issue(
            id=uuid.uuid4(),
            **values,
            description_doc=_document_dict(payload.description_doc, payload.description),
            version=1,
            created_at=now,
            updated_at=now,
            resolved_at=now if payload.status == "resolved" else None,
        )
        session.add(issue)
        session.flush()
        new_ids = set(issue_document_image_ids(payload.description_doc))
        added, _ = _bind_document_images(session, issue, set(), new_ids, now, settings)
        details = {"images_added": added} if added else {}
        session.add(
            IssueActivity(
                id=uuid.uuid4(), issue=issue, action="created", details=details, created_at=now
            )
        )
        session.commit()
        return _issue_payload(issue)

    @router.get("/issues/{issue_id}")
    def get_issue(issue_id: uuid.UUID, session: Session = Depends(session_dependency)):
        issue = _get_issue(session, issue_id, with_activities=True)
        return _issue_payload(issue, include_activities=True)

    @router.patch("/issues/{issue_id}")
    def update_issue(
        issue_id: uuid.UUID, payload: IssueUpdate, session: Session = Depends(session_dependency)
    ):
        issue = _get_issue(session, issue_id, for_update=True)
        if payload.version is not None and payload.version != issue.version:
            raise ApiError(
                409,
                "ISSUE_VERSION_CONFLICT",
                "issue was updated by someone else; reload before saving",
            )

        submitted = payload.model_dump(exclude_unset=True, exclude={"version", "description_doc"})
        old_ids = set(_document_ids(issue.description_doc))
        if "description" in submitted:
            next_document = _document_dict(payload.description_doc, submitted["description"])
            new_ids = set(_document_ids(next_document))
        else:
            next_document = issue.description_doc or _text_document(issue.description)
            new_ids = old_ids

        changes = {}
        for field, value in submitted.items():
            old_value = getattr(issue, field)
            if old_value != value:
                changes[field] = (
                    {"changed": True}
                    if field == "description"
                    else {
                        "from": str(old_value) if old_value is not None else None,
                        "to": str(value) if value is not None else None,
                    }
                )
                setattr(issue, field, value)

        current_document = issue.description_doc or _text_document(issue.description)
        document_changed = next_document != current_document
        if document_changed:
            issue.description_doc = next_document
            changes.setdefault("description", {"changed": True})
        if not changes:
            return _issue_payload(issue)

        now = _now()
        added, removed = _bind_document_images(
            session, issue, old_ids, new_ids, now, settings
        )
        if added:
            changes["images_added"] = added
        if removed:
            changes["images_removed"] = removed
        issue.updated_at = now
        issue.version += 1
        if "status" in changes:
            issue.resolved_at = now if issue.status == "resolved" else None
        session.add(
            IssueActivity(
                id=uuid.uuid4(),
                issue_id=issue.id,
                action="updated",
                details=changes,
                created_at=now,
            )
        )
        session.commit()
        return _issue_payload(issue)

    return router
