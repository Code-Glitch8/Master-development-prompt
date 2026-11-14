"""Activity log: persisted event stream (also pushed to live dashboards)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ActivityLog
from .events import bus


def log_event(
    db: Session,
    event: str,
    message: str,
    *,
    level: str = "info",
    project_id: int | None = None,
    task_id: int | None = None,
    data: dict[str, Any] | None = None,
) -> ActivityLog:
    row = ActivityLog(
        event=event,
        message=message,
        level=level,
        project_id=project_id,
        task_id=task_id,
        data=data or {},
    )
    db.add(row)
    db.flush()
    bus.publish(
        "activity",
        id=row.id,
        event=event,
        level=level,
        message=message,
        project_id=project_id,
        task_id=task_id,
    )
    return row


def recent(db: Session, limit: int = 100, project_id: int | None = None,
           level: str | None = None) -> list[ActivityLog]:
    q = select(ActivityLog).order_by(ActivityLog.id.desc()).limit(limit)
    if project_id is not None:
        q = q.where(ActivityLog.project_id == project_id)
    if level:
        q = q.where(ActivityLog.level == level)
    return list(db.scalars(q))
