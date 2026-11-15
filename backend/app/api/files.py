"""File changes (review), attachments, history/diffs and checkpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..attachments import service as att
from ..checkpoints import service as ck
from ..database import get_db
from ..models import Attachment, Checkpoint, FileChange, Project, Task
from ..projects import gitops
from ..projects.importer import project_root
from ..tasks import execution
from .route import CommitRoute
from .deps import change_or_404, checkpoint_or_404, project_or_404, task_or_404

router = APIRouter(prefix="/api", tags=["files"], route_class=CommitRoute)


class DecideBody(BaseModel):
    approve: bool


class DecideAllBody(BaseModel):
    approve: bool
    ids: list[int] | None = None


class RestoreBody(BaseModel):
    force: bool = False
    restore_tasks: bool = True


class CheckpointBody(BaseModel):
    label: str = ""


# ---- attachments / handoff -------------------------------------------------------------------

@router.post("/projects/{project_id}/attachments", status_code=201)
async def upload_attachment(file: UploadFile = File(...), task_id: int | None = Form(None),
                            full_snapshot: bool = Form(False), target_dir: str = Form(""),
                            p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    task = db.get(Task, task_id) if task_id else None
    if task_id and (task is None or task.project_id != p.id):
        raise HTTPException(400, "task does not belong to this project")
    try:
        a, changes = att.ingest_attachment(db, p, file.filename or "upload", await file.read(), task,
                                           full_snapshot=full_snapshot, target_dir=target_dir)
    except (att.AttachmentError, ValueError) as exc:
        raise HTTPException(400, str(exc))
    return {"attachment": {"id": a.id, "filename": a.filename, "file_type": a.file_type, "size": a.size},
            "changes": [att.change_dict(c) for c in changes]}


@router.get("/projects/{project_id}/attachments")
def list_attachments(p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    rows = db.scalars(select(Attachment).where(Attachment.project_id == p.id).order_by(Attachment.id.desc()))
    return [{"id": a.id, "filename": a.filename, "file_type": a.file_type, "size": a.size, "task_id": a.task_id,
             "created_at": a.created_at.isoformat() if a.created_at else None} for a in rows]


@router.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: int, db: Session = Depends(get_db)):
    a = db.get(Attachment, attachment_id)
    if a is None or not Path(a.stored_path).exists():
        raise HTTPException(404, "attachment not found")
    return FileResponse(a.stored_path, filename=a.filename)


# ---- file changes -----------------------------------------------------------------------------

@router.get("/projects/{project_id}/changes")
def list_changes(decision: str | None = None, task_id: int | None = None,
                 p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    q = select(FileChange).where(FileChange.project_id == p.id).order_by(FileChange.id.desc())
    if decision:
        q = q.where(FileChange.decision == decision)
    if task_id:
        q = q.where(FileChange.task_id == task_id)
    rows = list(db.scalars(q.limit(1000)))
    summary = {k: sum(1 for r in rows if r.change_type == k) for k in ("added", "modified", "deleted", "unchanged")}
    return {"summary": summary, "changes": [att.change_dict(c) for c in rows]}


@router.get("/changes/{change_id}/diff")
def change_diff(c: FileChange = Depends(change_or_404), db: Session = Depends(get_db)):
    p = db.get(Project, c.project_id)
    return {"path": c.path, "diff": att.diff_for(c, p)}


def _decide(db: Session, c: FileChange, approve: bool):
    p = db.get(Project, c.project_id)
    try:
        att.decide(db, c, approve, p)
    except (att.AttachmentError, ValueError) as exc:
        raise HTTPException(409, str(exc))
    return p


@router.post("/changes/{change_id}/decide")
def decide(body: DecideBody, c: FileChange = Depends(change_or_404), db: Session = Depends(get_db)):
    p = _decide(db, c, body.approve)
    if body.approve:
        att.finalize_applied(db, p, c.task_id, f"Project Pilot: apply {c.change_type} {c.path}")
    t = db.get(Task, c.task_id) if c.task_id else None
    if t is not None:
        execution.finish_review_if_clear(db, t)
    return att.change_dict(c)


@router.post("/projects/{project_id}/changes/decide-all")
def decide_all(body: DecideAllBody, p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    pending = att.pending_changes(db, p.id)
    if body.ids is not None:
        pending = [c for c in pending if c.id in set(body.ids)]
    errors, done, tasks = [], 0, set()
    for c in pending:
        try:
            att.decide(db, c, body.approve, p)
            done += 1
            if c.task_id:
                tasks.add(c.task_id)
        except (att.AttachmentError, ValueError) as exc:
            errors.append({"id": c.id, "path": c.path, "error": str(exc)})
    if body.approve and done:
        att.finalize_applied(db, p, None, f"Project Pilot: apply {done} approved change(s)")
    for tid in tasks:
        t = db.get(Task, tid)
        if t is not None:
            execution.finish_review_if_clear(db, t)
    return {"decided": done, "errors": errors}


# ---- history ---------------------------------------------------------------------------------------

@router.get("/projects/{project_id}/history")
def history(path: str | None = None, limit: int = 100, p: Project = Depends(project_or_404)):
    return gitops.log(project_root(p), min(limit, 500), path)


@router.get("/projects/{project_id}/history/diff", response_class=PlainTextResponse)
def history_diff(a: str, b: str, path: str | None = None, p: Project = Depends(project_or_404)):
    for ref in (a, b):
        if not ref.replace("~", "").replace("^", "").isalnum() and ref != "HEAD":
            raise HTTPException(400, "invalid revision")
    return gitops.diff_commits(project_root(p), a, b, path)


# ---- checkpoints -----------------------------------------------------------------------------------

@router.get("/projects/{project_id}/checkpoints")
def list_checkpoints(p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    return [ck.checkpoint_dict(c) for c in ck.list_checkpoints(db, p.id)]


@router.post("/projects/{project_id}/checkpoints", status_code=201)
def create_checkpoint(body: CheckpointBody, p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    return ck.checkpoint_dict(ck.create_checkpoint(db, p, "manual", body.label or "Manual checkpoint"))


@router.get("/checkpoints/compare")
def compare(a: int, b: int, db: Session = Depends(get_db)):
    ca, cb = db.get(Checkpoint, a), db.get(Checkpoint, b)
    if ca is None or cb is None or ca.project_id != cb.project_id:
        raise HTTPException(400, "checkpoints must exist and belong to the same project")
    return ck.compare(ca, cb)


@router.get("/checkpoints/{checkpoint_id}")
def get_checkpoint(c: Checkpoint = Depends(checkpoint_or_404)):
    return ck.checkpoint_dict(c, full=True)


@router.post("/checkpoints/{checkpoint_id}/restore")
def restore(body: RestoreBody, c: Checkpoint = Depends(checkpoint_or_404), db: Session = Depends(get_db)):
    p = db.get(Project, c.project_id)
    try:
        return ck.restore_checkpoint(db, p, c, force=body.force, restore_tasks=body.restore_tasks)
    except ck.RestoreConflict as exc:
        raise HTTPException(409, {"message": "The workspace has changes newer than the latest checkpoint. "
                                             "Review them, then retry with force=true (a backup checkpoint "
                                             "is created automatically).", "changed_files": exc.changed[:200]})
    except (ck.CheckpointError, gitops.GitError) as exc:
        raise HTTPException(400, str(exc))


@router.get("/checkpoints/{checkpoint_id}/export")
def export_ck(c: Checkpoint = Depends(checkpoint_or_404), db: Session = Depends(get_db)):
    p = db.get(Project, c.project_id)
    out = ck.export_checkpoint(db, p, c)
    return FileResponse(out, filename=out.name)


@router.delete("/checkpoints/{checkpoint_id}", status_code=204)
def delete_ck(c: Checkpoint = Depends(checkpoint_or_404), db: Session = Depends(get_db)):
    ck.delete_checkpoint(db, c)
