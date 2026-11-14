"""Handoff package: everything a fresh AI session (or a returning user) needs.

A new AI session never remembers earlier ones, so context is rebuilt from saved
records and handed over explicitly.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    ActivityLog, Conversation, ConversationMessage, FileChange, Project, ProjectPrompt, Task, TaskStatus,
)
from ..projects.importer import project_root
from ..tasks.queue import deps_of

DONE = {TaskStatus.COMPLETED.value}


def _task_row(db: Session, t: Task, with_output: bool = True) -> dict[str, Any]:
    return {"id": t.id, "key": t.key, "title": t.title, "description": t.description, "status": t.status,
            "run_state": t.run_state, "priority": t.priority, "depends_on": deps_of(db, t.id),
            "assigned_account_id": t.assigned_account_id, "relevant_files": t.relevant_files,
            "error": t.error, "retry_count": t.retry_count,
            "output": (t.actual_output[:8000] if with_output else ""),
            "completed_at": t.completed_at.isoformat() if t.completed_at else None}


def master_prompt(db: Session, project: Project) -> str:
    p = db.scalar(select(ProjectPrompt).where(ProjectPrompt.project_id == project.id,
                                              ProjectPrompt.is_template.is_(False))
                  .order_by(ProjectPrompt.id.desc()).limit(1))
    return p.body if p else ""


def build_state(db: Session, project: Project) -> dict[str, Any]:
    tasks = list(db.scalars(select(Task).where(Task.project_id == project.id).order_by(Task.position, Task.id)))
    completed = [_task_row(db, t) for t in tasks if t.status in DONE]
    unfinished = [_task_row(db, t, with_output=False) for t in tasks
                  if t.status not in DONE and t.status != TaskStatus.CANCELLED.value]
    current = next((t for t in tasks if t.status in (TaskStatus.RUNNING.value, TaskStatus.AWAITING_USER.value,
                                                     TaskStatus.NEEDS_REVIEW.value)), None)
    errors = [{"task_id": t.id, "title": t.title, "error": t.error} for t in tasks if t.error]
    pending_changes = [{"id": c.id, "path": c.path, "type": c.change_type} for c in db.scalars(
        select(FileChange).where(FileChange.project_id == project.id, FileChange.decision == "pending"))]
    manifest = [{"path": f.path, "size": f.size, "sha256": f.sha256} for f in project.files]
    return {
        "project": {"id": project.id, "name": project.name, "description": project.description,
                    "plan_status": project.plan_status, "inventory": project.inventory},
        "master_prompt": master_prompt(db, project),
        "completed_tasks": completed, "unfinished_tasks": unfinished,
        "current_task": _task_row(db, current) if current else None,
        "errors": errors, "pending_file_changes": pending_changes, "file_manifest": manifest,
    }


def conversation_index(db: Session, project: Project) -> list[dict[str, Any]]:
    out = []
    for c in db.scalars(select(Conversation).where(Conversation.project_id == project.id)):
        n = db.scalar(select(ConversationMessage.id).where(ConversationMessage.conversation_id == c.id)
                      .order_by(ConversationMessage.id.desc()).limit(1))
        count = len(c.messages)
        out.append({"conversation_id": c.id, "task_id": c.task_id, "title": c.title,
                    "messages": count, "last_message_id": n})
    return out


def handoff_markdown(db: Session, project: Project, state: dict[str, Any] | None = None) -> str:
    st = state or build_state(db, project)
    inv = project.inventory or {}
    L: list[str] = []
    L.append(f"# HANDOFF: {project.name}")
    L.append(f"_Generated {datetime.now(timezone.utc).isoformat()}_\n")
    L.append("> You are continuing work started in earlier sessions. You do NOT remember them; everything "
             "you need is in this document and the task details below.\n")
    L.append("## Project overview")
    L.append(f"- Description: {project.description or '(none)'}")
    L.append(f"- Languages: {', '.join(inv.get('languages', {}).keys()) or 'n/a'}")
    L.append(f"- Frameworks: {', '.join(inv.get('frameworks', [])) or 'n/a'}")
    L.append(f"- Files: {inv.get('file_count', 0)}  ·  Plan status: {project.plan_status}\n")
    L.append("## Master prompt")
    L.append(st["master_prompt"] or "(none)")
    L.append("")
    L.append("## Current project status")
    total = len(st["completed_tasks"]) + len(st["unfinished_tasks"])
    L.append(f"{len(st['completed_tasks'])} of {total} tasks completed.\n")
    L.append("## Completed tasks")
    for t in st["completed_tasks"] or []:
        out = (t["output"] or "").strip().replace("\n", " ")[:400]
        L.append(f"- [x] **{t['key']} {t['title']}** — {out}")
    if not st["completed_tasks"]:
        L.append("- (none yet)")
    L.append("\n## Unfinished tasks")
    for t in st["unfinished_tasks"]:
        dep = f" (after {t['depends_on']})" if t["depends_on"] else ""
        L.append(f"- [ ] **{t['key']} {t['title']}** — {t['status']}{dep}")
    if not st["unfinished_tasks"]:
        L.append("- (none)")
    L.append("\n## Current task")
    ct = st["current_task"]
    L.append(f"**{ct['key']} {ct['title']}** ({ct['status']})\n\n{ct['description']}" if ct else "(none active)")
    L.append("\n## Relevant files")
    rel = sorted({f for t in st["unfinished_tasks"] for f in t["relevant_files"]})[:40]
    L.extend(f"- {f}" for f in rel) if rel else L.append("- (see file-manifest.json)")
    L.append("\n## Important decisions")
    decisions = db.scalars(select(ActivityLog).where(ActivityLog.project_id == project.id, ActivityLog.event.in_(
        ["plan.approved", "files.applied", "files.rejected", "task.approved", "checkpoint.restored"]))
        .order_by(ActivityLog.id.desc()).limit(15))
    L.extend(f"- {d.message}" for d in decisions)
    L.append("\n## Known bugs / errors")
    L.extend(f"- Task {e['task_id']} ({e['title']}): {e['error'][:300]}" for e in st["errors"]) if st["errors"] \
        else L.append("- (none recorded)")
    L.append("\n## Pending questions")
    pend = [t for t in st["unfinished_tasks"] if t["status"] == "awaiting_user"]
    L.extend(f"- Waiting for the user on: {t['title']}" for t in pend) if pend else L.append("- (none)")
    if st["pending_file_changes"]:
        L.append(f"- {len(st['pending_file_changes'])} staged file change(s) await approval")
    L.append("\n## Next recommended actions")
    nxt = [t for t in st["unfinished_tasks"] if t["status"] in ("ready", "awaiting_user", "needs_review", "failed")]
    L.extend(f"- {t['status']}: {t['title']}" for t in nxt[:5]) if nxt else L.append("- Resume the next pending task.")
    return "\n".join(L) + "\n"


def write_handoff(db: Session, project: Project) -> Path:
    """Write HANDOFF.md and JSON files to data/conversations/<id>/handoff/. Returns the directory."""
    st = build_state(db, project)
    d = get_settings().conversations_dir / str(project.id) / "handoff"
    d.mkdir(parents=True, exist_ok=True)
    (d / "HANDOFF.md").write_text(handoff_markdown(db, project, st), encoding="utf-8")
    dump = lambda name, obj: (d / name).write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")  # noqa: E731
    dump("completed-tasks.json", st["completed_tasks"])
    dump("unfinished-tasks.json", st["unfinished_tasks"])
    dump("project-state.json", {k: st[k] for k in ("project", "master_prompt", "current_task", "errors",
                                                  "pending_file_changes")})
    dump("file-manifest.json", st["file_manifest"])
    dump("conversation-index.json", conversation_index(db, project))
    return d


def context_for_task(db: Session, project: Project, task: Task, max_chars: int = 6000) -> str:
    """Explicit context block prepended to a task prompt (new sessions have no memory)."""
    done = list(db.scalars(select(Task).where(Task.project_id == project.id, Task.status == TaskStatus.COMPLETED.value)
                           .order_by(Task.position, Task.id)))
    deps = set(deps_of(db, task.id))
    lines = ["# Context from earlier work (you have no memory of previous sessions)"]
    budget = max_chars
    for t in done:
        out = (t.actual_output or "").strip()
        cap = 1500 if t.id in deps else 300
        snippet = out[:cap] + ("…" if len(out) > cap else "")
        entry = f"- {t.key} {t.title}: {snippet}"
        if budget - len(entry) < 0:
            break
        budget -= len(entry)
        lines.append(entry)
    if len(lines) == 1:
        lines.append("- No tasks completed yet.")
    inv = project.inventory or {}
    lines.append(f"\nProject has {inv.get('file_count', 0)} files; languages: "
                 f"{', '.join(inv.get('languages', {}).keys()) or 'n/a'}.")
    return "\n".join(lines)
