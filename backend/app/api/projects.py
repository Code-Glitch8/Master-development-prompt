from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..checkpoints.service import create_checkpoint
from ..config import get_settings
from ..database import get_db
from ..models import AIAccount, Project, ProjectFile, ProjectPrompt, ProjectProviderAssignment
from ..projects import gitops, importer
from ..services.activity import log_event
from ..tasks import planner, queue
from ..utils.paths import UnsafePathError, is_protected, safe_join
from . import serializers as ser
from .route import CommitRoute
from .deps import project_or_404, prompt_or_404

router = APIRouter(prefix="/api", tags=["projects"], route_class=CommitRoute)

PROMPT_TEMPLATES = [
    {"id": "redesign", "title": "Redesign & polish UI",
     "body": "Redesign the dashboard, improve mobile responsiveness, fix any visual bugs and test the navigation."},
    {"id": "bugfix", "title": "Find and fix bugs",
     "body": "Identify bugs and errors across the project, fix them, add tests for each fix and review all changes."},
    {"id": "performance", "title": "Performance pass",
     "body": "Optimize performance, reduce bundle size, fix slow queries and verify nothing regressed."},
    {"id": "security", "title": "Security review",
     "body": "Review authentication and input handling, fix security vulnerabilities and document the changes."},
    {"id": "docs", "title": "Documentation",
     "body": "Write a README, document the main modules and add usage examples."},
    {"id": "tests", "title": "Add tests",
     "body": "Add automated tests for the core features, fix failing tests and report coverage gaps."},
]


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class FolderImport(BaseModel):
    path: str
    name: str | None = None
    description: str = ""


class GitImport(BaseModel):
    url: str
    name: str | None = None
    branch: str | None = None
    description: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    auto_approve_plan: bool | None = None


class PromptBody(BaseModel):
    title: str = "Master prompt"
    body: str = ""
    is_draft: bool = True
    is_template: bool = False
    preferences: dict[str, Any] = {}


class ProvidersBody(BaseModel):
    account_ids: list[int]
    category_accounts: dict[str, int] | None = None


class PlanBody(BaseModel):
    prompt_id: int | None = None


@router.get("/projects")
def list_projects(db: Session = Depends(get_db)):
    return [ser.project_dict(db, p) for p in db.scalars(select(Project).order_by(Project.id.desc()))]


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)):
    try:
        p = importer.create_project(db, body.name, body.description)
        gitops.ensure_repo(Path(p.directory))
    except importer.ImportError_ as exc:
        raise HTTPException(400, str(exc))
    return ser.project_dict(db, p, True)


@router.post("/projects/import/zip", status_code=201)
async def import_zip(file: UploadFile = File(...), name: str | None = Form(None), db: Session = Depends(get_db)):
    tmp = Path(tempfile.mkdtemp(prefix="pp-up-", dir=str(get_settings().data_dir)))
    try:
        dest = tmp / "upload.zip"
        with dest.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                f.write(chunk)
        p = importer.import_zip(db, dest, name or Path(file.filename or "project").stem)
    except importer.ImportError_ as exc:
        raise HTTPException(400, str(exc))
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)
    return ser.project_dict(db, p, True)


@router.post("/projects/import/folder", status_code=201)
def import_folder(body: FolderImport, db: Session = Depends(get_db)):
    try:
        p = importer.import_folder(db, Path(body.path), body.name, body.description)
    except importer.ImportError_ as exc:
        raise HTTPException(400, str(exc))
    return ser.project_dict(db, p, True)


@router.post("/projects/import/git", status_code=201)
def import_git(body: GitImport, db: Session = Depends(get_db)):
    try:
        p = importer.import_git(db, body.url, body.name, body.description, body.branch)
    except importer.ImportError_ as exc:
        raise HTTPException(400, str(exc))
    return ser.project_dict(db, p, True)


@router.post("/projects/import/files", status_code=201)
async def import_files(files: list[UploadFile] = File(...), name: str | None = Form(None),
                       db: Session = Depends(get_db)):
    data = [(f.filename or "file", await f.read()) for f in files]
    try:
        p = importer.import_files(db, data, name)
    except importer.ImportError_ as exc:
        raise HTTPException(400, str(exc))
    return ser.project_dict(db, p, True)


@router.get("/prompt-templates")
def prompt_templates():
    return PROMPT_TEMPLATES


@router.get("/projects/{project_id}")
def get_project(p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    return ser.project_dict(db, p, True)


@router.patch("/projects/{project_id}")
def patch_project(body: ProjectPatch, p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(p, k, v)
    db.flush()
    return ser.project_dict(db, p, True)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(delete_files: bool = True, p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    importer.delete_project(db, p, delete_files)


@router.get("/projects/{project_id}/files")
def project_files(p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    rows = db.scalars(select(ProjectFile).where(ProjectFile.project_id == p.id).order_by(ProjectFile.path))
    return [{"path": f.path, "size": f.size, "language": f.language, "is_config": f.is_config,
             "is_doc": f.is_doc, "is_protected": f.is_protected, "sha256": f.sha256} for f in rows]


@router.get("/projects/{project_id}/file")
def project_file(path: str, p: Project = Depends(project_or_404)):
    if is_protected(path):
        raise HTTPException(403, "protected files are never displayed")
    try:
        target = safe_join(Path(p.directory), path)
    except UnsafePathError as exc:
        raise HTTPException(400, str(exc))
    if not target.is_file():
        raise HTTPException(404, "file not found")
    if target.stat().st_size > 1_000_000:
        raise HTTPException(413, "file too large to preview")
    try:
        return {"path": path, "content": target.read_text(encoding="utf-8")}
    except UnicodeDecodeError:
        raise HTTPException(415, "binary file")


@router.post("/projects/{project_id}/rescan")
def rescan(p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    importer.sync_inventory(db, p)
    return ser.project_dict(db, p, True)


@router.get("/projects/{project_id}/providers")
def get_providers(p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    ids = list(db.scalars(select(ProjectProviderAssignment.account_id).where(ProjectProviderAssignment.project_id == p.id)))
    accts = [ser.account_dict(db, a) for a in db.scalars(select(AIAccount).where(AIAccount.id.in_(ids)))] if ids else []
    return {"account_ids": ids, "accounts": accts,
            "category_accounts": (p.inventory or {}).get("_category_accounts", {})}


@router.put("/projects/{project_id}/providers")
def set_providers(body: ProvidersBody, p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    valid = set(db.scalars(select(AIAccount.id).where(AIAccount.id.in_(body.account_ids))))
    if valid != set(body.account_ids):
        raise HTTPException(400, "unknown account id")
    for row in db.scalars(select(ProjectProviderAssignment).where(ProjectProviderAssignment.project_id == p.id)):
        db.delete(row)
    db.flush()
    for aid in body.account_ids:
        db.add(ProjectProviderAssignment(project_id=p.id, account_id=aid))
    inv = dict(p.inventory or {})
    if body.category_accounts is not None:
        if any(a not in valid for a in body.category_accounts.values()):
            raise HTTPException(400, "category accounts must be among the project's accounts")
        inv["_category_accounts"] = body.category_accounts
        p.inventory = inv
    # tasks pointing at an account that was removed lose the assignment (visibly, never silently swapped)
    from ..models import Task

    for t in db.scalars(select(Task).where(Task.project_id == p.id, Task.assigned_account_id.isnot(None))):
        if t.assigned_account_id not in valid:
            t.assigned_account_id = None
    db.flush()
    log_event(db, "project.providers", f"Project accounts updated ({len(body.account_ids)})", project_id=p.id)
    return get_providers(p, db)


# ---- prompts / plan ---------------------------------------------------------------

def _prompt_dict(pr: ProjectPrompt) -> dict[str, Any]:
    return {"id": pr.id, "project_id": pr.project_id, "title": pr.title, "body": pr.body,
            "is_draft": pr.is_draft, "is_template": pr.is_template, "preferences": pr.preferences,
            "analysis": pr.analysis, "created_at": ser.iso(pr.created_at), "updated_at": ser.iso(pr.updated_at)}


@router.get("/projects/{project_id}/prompts")
def list_prompts(p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    return [_prompt_dict(x) for x in db.scalars(select(ProjectPrompt).where(ProjectPrompt.project_id == p.id)
                                                .order_by(ProjectPrompt.id.desc()))]


@router.post("/projects/{project_id}/prompts", status_code=201)
def create_prompt(body: PromptBody, p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    pr = ProjectPrompt(project_id=p.id, **body.model_dump())
    db.add(pr)
    db.flush()
    log_event(db, "prompt.saved", f"Prompt saved: {pr.title}", project_id=p.id)
    return _prompt_dict(pr)


@router.put("/prompts/{prompt_id}")
def update_prompt(body: PromptBody, pr: ProjectPrompt = Depends(prompt_or_404), db: Session = Depends(get_db)):
    for k, v in body.model_dump().items():
        setattr(pr, k, v)
    db.flush()
    return _prompt_dict(pr)


@router.delete("/prompts/{prompt_id}", status_code=204)
def delete_prompt(pr: ProjectPrompt = Depends(prompt_or_404), db: Session = Depends(get_db)):
    db.delete(pr)


@router.post("/projects/{project_id}/plan")
def generate_plan(body: PlanBody, p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    pr = (db.get(ProjectPrompt, body.prompt_id) if body.prompt_id else
          db.scalar(select(ProjectPrompt).where(ProjectPrompt.project_id == p.id, ProjectPrompt.is_template.is_(False))
                    .order_by(ProjectPrompt.id.desc()).limit(1)))
    if pr is None or pr.project_id != p.id:
        raise HTTPException(400, "save a master prompt first")
    if not pr.body.strip():
        raise HTTPException(400, "the master prompt is empty")
    try:
        tasks = planner.replace_plan(db, p, pr)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if p.auto_approve_plan:
        queue.approve_plan(db, p)
    return {"analysis": pr.analysis, "tasks": [ser.task_dict(db, t) for t in tasks],
            "plan_status": p.plan_status}


@router.post("/projects/{project_id}/plan/approve")
def approve_plan(p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    try:
        queue.approve_plan(db, p)
    except queue.QueueError as exc:
        raise HTTPException(409, str(exc))
    return ser.project_dict(db, p, True)


@router.post("/projects/{project_id}/start")
def start(p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    try:
        queue.start_project(db, p)
    except queue.QueueError as exc:
        raise HTTPException(409, str(exc))
    return ser.project_dict(db, p, True)


@router.post("/projects/{project_id}/pause")
def pause(p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    try:
        queue.pause_project(db, p)
        create_checkpoint(db, p, reason="paused", label="Project paused")
    except queue.QueueError as exc:
        raise HTTPException(409, str(exc))
    return ser.project_dict(db, p, True)


@router.post("/projects/{project_id}/resume")
def resume(p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    try:
        queue.resume_project(db, p)
    except queue.QueueError as exc:
        raise HTTPException(409, str(exc))
    return ser.project_dict(db, p, True)


@router.get("/projects/{project_id}/graph")
def graph(p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    return queue.graph(db, p.id)
