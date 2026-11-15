from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..attachments.service import AttachmentError
from ..conversations import handoff
from ..conversations import service as conv
from ..database import get_db
from ..models import AIAccount, MessageKind, Project, Task, TaskStatus
from ..tasks import execution, queue
from ..workers.engine import build_prompt
from . import serializers as ser
from .route import CommitRoute
from .deps import project_or_404, task_or_404

router = APIRouter(prefix="/api", tags=["tasks"], route_class=CommitRoute)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    priority: int = Field(3, ge=1, le=5)
    depends_on: list[int] = []
    category: str = "general"
    required_capability: str = ""
    relevant_files: list[str] = []
    expected_output: str = ""


class TaskPatch(BaseModel):
    title: str | None = Field(None, max_length=300)
    description: str | None = None
    priority: int | None = Field(None, ge=1, le=5)
    depends_on: list[int] | None = None
    category: str | None = None
    required_capability: str | None = None
    relevant_files: list[str] | None = None
    expected_output: str | None = None
    input_prompt: str | None = None
    max_retries: int | None = Field(None, ge=0, le=10)


class ReorderBody(BaseModel):
    ordered_ids: list[int]


class AssignBody(BaseModel):
    account_id: int | None


def _q(fn, *a):
    try:
        return fn(*a)
    except queue.QueueError as exc:
        raise HTTPException(409, str(exc))


@router.get("/tasks")
def all_tasks(status: str | None = None, db: Session = Depends(get_db)):
    q = select(Task).order_by(Task.project_id, Task.position, Task.id)
    if status:
        q = q.where(Task.status == status)
    return [ser.task_dict(db, t) for t in db.scalars(q)]


@router.get("/projects/{project_id}/tasks")
def project_tasks(p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    return [ser.task_dict(db, t) for t in db.scalars(select(Task).where(Task.project_id == p.id)
                                                     .order_by(Task.position, Task.id))]


@router.post("/projects/{project_id}/tasks", status_code=201)
def create_task(body: TaskCreate, p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    data = body.model_dump()
    deps, title, desc = data.pop("depends_on"), data.pop("title"), data.pop("description")
    t = _q(lambda: queue.create_task(db, p, title, desc, deps, **data))
    return ser.task_dict(db, t, True)


@router.get("/tasks/{task_id}")
def get_task(t: Task = Depends(task_or_404), db: Session = Depends(get_db)):
    return ser.task_dict(db, t, True)


@router.patch("/tasks/{task_id}")
def patch_task(body: TaskPatch, t: Task = Depends(task_or_404), db: Session = Depends(get_db)):
    _q(lambda: queue.update_task(db, t, **body.model_dump(exclude_none=True)))
    return ser.task_dict(db, t, True)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(t: Task = Depends(task_or_404), db: Session = Depends(get_db)):
    _q(queue.delete_task, db, t)


@router.post("/projects/{project_id}/tasks/reorder")
def reorder(body: ReorderBody, p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    _q(queue.reorder, db, p.id, body.ordered_ids)
    return project_tasks(p, db)


@router.post("/tasks/{task_id}/assign")
def assign(body: AssignBody, t: Task = Depends(task_or_404), db: Session = Depends(get_db)):
    _q(queue.assign_account, db, t, body.account_id)
    return ser.task_dict(db, t, True)


@router.post("/tasks/{task_id}/retry")
def retry(t: Task = Depends(task_or_404), db: Session = Depends(get_db)):
    _q(queue.retry_task, db, t)
    return ser.task_dict(db, t, True)


@router.post("/tasks/{task_id}/cancel")
def cancel(t: Task = Depends(task_or_404), db: Session = Depends(get_db)):
    _q(queue.cancel_task, db, t)
    return ser.task_dict(db, t, True)


@router.post("/tasks/{task_id}/pause")
def pause(t: Task = Depends(task_or_404), db: Session = Depends(get_db)):
    _q(queue.pause_task, db, t)
    return ser.task_dict(db, t, True)


@router.post("/tasks/{task_id}/resume")
def resume(t: Task = Depends(task_or_404), db: Session = Depends(get_db)):
    _q(queue.resume_task, db, t)
    return ser.task_dict(db, t, True)


@router.post("/tasks/{task_id}/approve")
def approve(t: Task = Depends(task_or_404), db: Session = Depends(get_db)):
    _q(queue.approve_task, db, t)
    return ser.task_dict(db, t, True)


@router.get("/tasks/{task_id}/prompt")
def task_prompt(t: Task = Depends(task_or_404), db: Session = Depends(get_db)):
    """The exact prompt to run, including explicit context from earlier tasks (for copy/paste)."""
    project = db.get(Project, t.project_id)
    acct = db.get(AIAccount, t.assigned_account_id) if t.assigned_account_id else None
    return {"task_id": t.id, "prompt": build_prompt(db, project, t),
            "provider_url": acct.provider.website_url if acct else "",
            "account": ser.account_dict(db, acct) if acct else None}


@router.post("/tasks/{task_id}/submit")
async def submit_result(response: str = Form(""), files: list[UploadFile] = File(default=[]),
                        t: Task = Depends(task_or_404), db: Session = Depends(get_db)):
    """User-assisted flow: paste the AI's response and/or upload returned files."""
    if t.status not in (TaskStatus.AWAITING_USER.value, TaskStatus.READY.value, TaskStatus.ASSIGNED.value,
                        TaskStatus.PENDING.value, TaskStatus.FAILED.value, TaskStatus.NEEDS_REVIEW.value):
        raise HTTPException(409, f"task is {t.status}; it cannot accept a result right now")
    if t.status == TaskStatus.PENDING.value and not queue.dependencies_complete(db, t):
        raise HTTPException(409, "dependencies are not complete")
    if not response.strip() and not files:
        raise HTTPException(400, "provide a response and/or at least one file")
    data = [(f.filename or "file", await f.read()) for f in files]
    try:
        execution.complete_with_response(db, t, response, data, None, "user_supplied")
    except AttachmentError as exc:
        raise HTTPException(400, str(exc))
    return ser.task_dict(db, t, True)


@router.get("/projects/{project_id}/handoff")
def get_handoff(p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    return {"markdown": handoff.handoff_markdown(db, p)}
