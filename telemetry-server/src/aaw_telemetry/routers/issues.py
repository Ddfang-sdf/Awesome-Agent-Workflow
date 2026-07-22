from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from ..errors import ApiError
from ..models import Issue, IssueActivity
from ..schemas import IssueCreate, IssueUpdate

ASSIGNEES = ["张轶勃", "徐哲威", "宋东方", "张立肖", "孙杨宇鑫"]


def _now() -> datetime:
    return datetime.now(UTC)


def _issue_payload(issue: Issue, *, include_activities: bool = False) -> dict:
    result = {
        "id": str(issue.id),
        "title": issue.title,
        "description": issue.description,
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


def _get_issue(session: Session, issue_id: uuid.UUID, *, with_activities: bool = False) -> Issue:
    statement = select(Issue).where(Issue.id == issue_id)
    if with_activities:
        statement = statement.options(selectinload(Issue.activities))
    issue = session.scalar(statement)
    if issue is None:
        raise ApiError(404, "ISSUE_NOT_FOUND", "issue was not found")
    return issue


def build_issues_router(session_dependency) -> APIRouter:
    router = APIRouter(prefix="/api/v1", tags=["issues"])

    @router.get("/issues/options")
    def issue_options():
        return {
            "assignees": ASSIGNEES,
            "statuses": ["todo", "in_progress", "resolved"],
            "priorities": ["low", "medium", "high"],
        }

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
        issue = Issue(
            id=uuid.uuid4(),
            **payload.model_dump(),
            created_at=now,
            updated_at=now,
            resolved_at=now if payload.status == "resolved" else None,
        )
        session.add(issue)
        session.add(
            IssueActivity(
                id=uuid.uuid4(), issue=issue, action="created", details={}, created_at=now
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
        issue = _get_issue(session, issue_id)
        changes = {}
        for field, value in payload.model_dump(exclude_unset=True).items():
            old_value = getattr(issue, field)
            if old_value != value:
                changes[field] = {
                    "from": str(old_value) if old_value is not None else None,
                    "to": str(value) if value is not None else None,
                }
                setattr(issue, field, value)
        if not changes:
            return _issue_payload(issue)
        now = _now()
        issue.updated_at = now
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
