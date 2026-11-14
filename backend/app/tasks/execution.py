"""Task result handling shared by the automated worker and the user-assisted API."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..attachments import service as att_service
from ..checkpoints.service import create_checkpoint
from ..conversations import handoff
from ..conversations import service as conv
from ..models import (
    AIAccount, MessageKind, Project, RunState, Task, TaskExecution, TaskStatus, utcnow,
)
from ..services.activity import log_event
from ..services.notifications import notify
from ..services.settings_service import get_setting
from . import queue


def current_execution(db: Session, task: Task) -> TaskExecution | None:
    return db.scalar(select(TaskExecution).where(TaskExecution.task_id == task.id, TaskExecution.finished_at.is_(None))
                     .order_by(TaskExecution.id.desc()).limit(1))


def start_execution(db: Session, task: Task, account: AIAccount | None, mode: str) -> TaskExecution:
    ex = TaskExecution(task_id=task.id, account_id=account.id if account else None,
                       attempt=task.retry_count + 1, mode=mode, run_state=task.run_state, outcome="started")
    db.add(ex)
    db.flush()
    return ex


def finish_execution(db: Session, task: Task, outcome: str, error: str = "") -> None:
    ex = current_execution(db, task)
    if ex is not None:
        ex.outcome, ex.error, ex.finished_at, ex.run_state = outcome, error, utcnow(), task.run_state
        db.flush()


def complete_with_response(db: Session, task: Task, response_text: str,
                           attachments: list[tuple[str, bytes]] | None = None,
                           account: AIAccount | None = None, source: str = "automated") -> Task:
    """Record an AI/user-supplied response, stage returned files and move the task forward."""
    project = db.get(Project, task.project_id)
    assert project is not None
    c = conv.get_or_create(db, task)
    account = account or (db.get(AIAccount, task.assigned_account_id) if task.assigned_account_id else None)
    msg = conv.add_message(db, c, MessageKind.AI_RESPONSE, response_text, account, "completed",
                           {"source": source})
    task.actual_output = response_text
    changes = att_service.stage_from_response(db, project, task, response_text, msg.id)
    for fname, content in attachments or []:
        _att, ch = att_service.ingest_attachment(db, project, fname, content, task, msg.id)
        changes.extend(ch)
        conv.add_message(db, c, MessageKind.FILE_ATTACHMENT, f"Attachment received: {fname} "
                         f"({len(ch)} file change(s) staged)", account, "completed", {"attachment_id": _att.id})
    pending = [x for x in changes if x.decision == "pending"]
    task.error = ""
    task.progress = 1.0
    task.lock_token, task.locked_at, task.next_attempt_at = None, None, None
    task.run_state = RunState.COMPLETED.value
    needs_review = bool(pending) or bool(get_setting(db, "review.require_task_approval", False))
    if needs_review:
        task.status = TaskStatus.NEEDS_REVIEW.value
        conv.add_message(db, c, MessageKind.SYSTEM_EVENT,
                         f"Result recorded. {len(pending)} file change(s) await your review." if pending
                         else "Result recorded. Awaiting your approval.", None, "needs_review")
        notify(db, "user_action_required", "Review needed", f"'{task.title}' is ready for review.", project.id)
    else:
        task.status = TaskStatus.COMPLETED.value
        task.completed_at = utcnow()
        task.approved = True
        conv.add_message(db, c, MessageKind.SYSTEM_EVENT, "Task completed.", None, "completed")
        notify(db, "task_completed", "Task completed", task.title, project.id)
    finish_execution(db, task, "completed")
    ck = create_checkpoint(db, project, reason="task_completed", label=f"After: {task.title}", task=task)
    conv.add_message(db, c, MessageKind.CHECKPOINT, f"Checkpoint #{ck.id} created", None, "completed",
                     {"checkpoint_id": ck.id})
    queue.refresh_ready(db, project.id)
    handoff.write_handoff(db, project)
    log_event(db, "task.completed" if not needs_review else "task.needs_review",
              f"{task.title}: {'needs review' if needs_review else 'completed'}",
              project_id=project.id, task_id=task.id)
    queue.publish_task(task)
    if not needs_review and queue.maybe_complete_project(db, project.id):
        notify(db, "project_completed", "Project completed", project.name, project.id)
    return task


def finish_review_if_clear(db: Session, task: Task) -> bool:
    """After file changes were decided: auto-complete the task if nothing else is pending."""
    if task.status != TaskStatus.NEEDS_REVIEW.value:
        return False
    if att_service.pending_changes(db, task.project_id, task.id):
        return False
    if get_setting(db, "review.require_task_approval", False):
        return False
    task.status = TaskStatus.COMPLETED.value
    task.approved = True
    task.completed_at = utcnow()
    db.flush()
    queue.refresh_ready(db, task.project_id)
    log_event(db, "task.completed", f"{task.title}: completed after review", project_id=task.project_id,
              task_id=task.id)
    queue.publish_task(task)
    if queue.maybe_complete_project(db, task.project_id):
        project = db.get(Project, task.project_id)
        notify(db, "project_completed", "Project completed", project.name if project else "", task.project_id)
    return True
