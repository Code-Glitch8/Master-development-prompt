"""Conversations, engine control, dashboard, activity, notifications, settings, exports, backups."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..conversations import service as conv
from ..database import get_db, get_engine
from ..exporting import service as exp
from ..models import (
    AIAccount, Conversation, Notification, Project, ServiceState, Task, TaskStatus,
)
from ..models.enums import PlanStatus
from ..services import activity
from ..services.settings_service import all_settings, set_setting
from ..workers.engine import engine
from . import serializers as ser
from .route import CommitRoute
from .deps import project_or_404

router = APIRouter(prefix="/api", tags=["system"], route_class=CommitRoute)


class SettingsBody(BaseModel):
    values: dict[str, Any]


class ExportBody(BaseModel):
    include_original: bool = False
    include_git: bool = False


@router.get("/health")
def health():
    return {"status": "ok", "version": __version__, "engine": engine.state}


# ---- engine --------------------------------------------------------------------------------

@router.get("/engine/status")
def engine_status():
    return engine.status()


@router.post("/engine/start")
async def engine_start():
    await engine.start()
    return engine.status()


@router.post("/engine/pause")
def engine_pause():
    if engine.state != ServiceState.RUNNING.value:
        raise HTTPException(409, "engine is not running")
    engine.pause()
    return engine.status()


@router.post("/engine/resume")
def engine_resume():
    if engine.state != ServiceState.PAUSED.value:
        raise HTTPException(409, "engine is not paused")
    engine.resume()
    return engine.status()


@router.post("/engine/stop")
async def engine_stop():
    await engine.stop()
    return engine.status()


# ---- dashboard ---------------------------------------------------------------------------------

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    counts = dict(db.execute(select(Task.status, func.count()).group_by(Task.status)).all())
    active = sum(counts.get(s, 0) for s in ("running", "assigned", "ready"))
    accounts = list(db.scalars(select(AIAccount)))
    running = db.scalars(select(Task).where(Task.status == TaskStatus.RUNNING.value)).all()
    awaiting = db.scalars(select(Task).where(Task.status.in_(["awaiting_user", "needs_review"]))).all()
    executing = db.scalar(select(func.count()).select_from(Project).where(
        Project.plan_status == PlanStatus.EXECUTING.value))
    return {
        "projects": db.scalar(select(func.count()).select_from(Project)) or 0,
        "executing_projects": executing or 0,
        "active_tasks": active,
        "completed_tasks": counts.get("completed", 0),
        "awaiting_input": counts.get("awaiting_user", 0) + counts.get("needs_review", 0),
        "failed_tasks": counts.get("failed", 0),
        "task_counts": counts,
        "accounts_total": len(accounts),
        "accounts_enabled": sum(1 for a in accounts if a.enabled),
        "accounts_automated": sum(1 for a in accounts if a.execution_mode == "automated"),
        "engine": engine.status(),
        "current_tasks": [ser.task_dict(db, t) for t in running],
        "awaiting_tasks": [ser.task_dict(db, t) for t in awaiting][:10],
        "recent_activity": [_log(a) for a in activity.recent(db, 15)],
    }


def _log(a) -> dict[str, Any]:
    return {"id": a.id, "event": a.event, "level": a.level, "message": a.message, "project_id": a.project_id,
            "task_id": a.task_id, "created_at": ser.iso(a.created_at)}


@router.get("/activity")
def get_activity(limit: int = 200, project_id: int | None = None, level: str | None = None,
                 db: Session = Depends(get_db)):
    return [_log(a) for a in activity.recent(db, min(limit, 1000), project_id, level)]


# ---- conversations -------------------------------------------------------------------------------

@router.get("/conversations")
def conversations(project_id: int | None = None, db: Session = Depends(get_db)):
    out = []
    for c in conv.list_conversations(db, project_id):
        last = c.messages[-1] if c.messages else None
        out.append({"id": c.id, "project_id": c.project_id, "task_id": c.task_id, "title": c.title,
                    "message_count": len(c.messages), "last_kind": last.kind if last else None,
                    "last_at": ser.iso(last.created_at) if last else None})
    return out


@router.get("/conversations/search")
def search(q: str = "", project_id: int | None = None, kind: str | None = None, task_id: int | None = None,
           provider: str | None = None, limit: int = 200, db: Session = Depends(get_db)):
    return [conv.message_dict(m) for m in conv.search_messages(db, q, project_id, kind, task_id, provider,
                                                               min(limit, 1000))]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, db: Session = Depends(get_db)):
    c = db.get(Conversation, conversation_id)
    if c is None:
        raise HTTPException(404, "conversation not found")
    return conv.export_json(db, c)


@router.get("/conversations/{conversation_id}/export")
def export_conversation(conversation_id: int, format: str = "markdown", db: Session = Depends(get_db)):
    c = db.get(Conversation, conversation_id)
    if c is None:
        raise HTTPException(404, "conversation not found")
    if format == "json":
        return conv.export_json(db, c)
    if format != "markdown":
        raise HTTPException(400, "format must be 'markdown' or 'json'")
    return PlainTextResponse(conv.export_markdown(db, c), media_type="text/markdown")


# ---- notifications ----------------------------------------------------------------------------------

@router.get("/notifications")
def notifications(unread_only: bool = False, db: Session = Depends(get_db)):
    q = select(Notification).order_by(Notification.id.desc()).limit(100)
    if unread_only:
        q = q.where(Notification.read.is_(False))
    return [{"id": n.id, "kind": n.kind, "title": n.title, "body": n.body, "project_id": n.project_id,
             "read": n.read, "created_at": ser.iso(n.created_at)} for n in db.scalars(q)]


@router.post("/notifications/read-all")
def read_all(db: Session = Depends(get_db)):
    for n in db.scalars(select(Notification).where(Notification.read.is_(False))):
        n.read = True
    return {"ok": True}


# ---- settings --------------------------------------------------------------------------------------------

@router.get("/settings")
def get_settings_api(db: Session = Depends(get_db)):
    return all_settings(db)


@router.put("/settings")
def put_settings(body: SettingsBody, db: Session = Depends(get_db)):
    from ..services.settings_service import DEFAULTS

    allowed = set(DEFAULTS) | {"review.require_task_approval"}
    for k, v in body.values.items():
        if k not in allowed:
            raise HTTPException(400, f"unknown setting: {k}")
        set_setting(db, k, v)
    return all_settings(db)


# ---- exports / backups ----------------------------------------------------------------------------------------

@router.post("/projects/{project_id}/export")
def export_project(body: ExportBody, p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    out = exp.export_project(db, p, body.include_original, body.include_git)
    return {"file": out.name, "size": out.stat().st_size, "download": f"/api/exports/{out.name}"}


@router.post("/projects/{project_id}/export/{kind}")
def export_part(kind: str, p: Project = Depends(project_or_404), db: Session = Depends(get_db)):
    fn = {"conversations": exp.export_conversations, "history": exp.export_history,
          "checkpoints": exp.export_checkpoints, "handoff": exp.export_handoff}.get(kind)
    if fn is None:
        raise HTTPException(404, "unknown export kind")
    out = fn(db, p)
    return {"file": out.name, "size": out.stat().st_size, "download": f"/api/exports/{out.name}"}


@router.get("/exports")
def list_exports():
    d = get_settings().exports_dir
    d.mkdir(parents=True, exist_ok=True)
    return [{"file": f.name, "size": f.stat().st_size,
             "created": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()}
            for f in sorted(d.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True)]


@router.get("/exports/{name}")
def download_export(name: str):
    d = get_settings().exports_dir.resolve()
    f = (d / Path(name).name).resolve()
    if f.parent != d or not f.is_file():
        raise HTTPException(404, "export not found")
    return FileResponse(f, filename=f.name)


def backup_database(keep: int = 10) -> Path:
    """Consistent online SQLite backup (safe while the app is running)."""
    s = get_settings()
    s.backups_dir.mkdir(parents=True, exist_ok=True)
    out = s.backups_dir / f"project_pilot-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.db"
    get_engine().dispose()  # ensure no pending writers on pooled connections
    src = sqlite3.connect(s.db_path)
    try:
        dst = sqlite3.connect(out)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    for old in sorted(s.backups_dir.glob("project_pilot-*.db"))[:-keep]:
        old.unlink(missing_ok=True)
    return out


@router.post("/backup")
def backup():
    out = backup_database()
    return {"file": out.name, "size": out.stat().st_size}
