"""Checkpoints: task/file/conversation state plus a restorable file snapshot.

Snapshot strategy
- Git available: the workspace is committed (protected files are excluded from the
  repo) and the commit hash is the snapshot reference.
- No git: a ZIP snapshot of the workspace is written under data/checkpoints.

Restoring never silently destroys newer work: if the workspace has changes made
after the last checkpoint, restore refuses unless `force=True`, and even then a
`pre-restore` checkpoint is created first so the newer state can be brought back.
"""
from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Checkpoint, Conversation, ConversationMessage, Project, ProjectPrompt, Task, TaskStatus
from ..projects import gitops
from ..projects.importer import project_root, sync_inventory
from ..projects.inventory import walk_files
from ..services.activity import log_event
from ..services.events import bus
from ..utils.paths import is_protected, safe_join, sha256_file


class RestoreConflict(Exception):
    def __init__(self, changed: list[str]):
        super().__init__("workspace has changes newer than this checkpoint")
        self.changed = changed


class CheckpointError(ValueError):
    pass


def manifest(root: Path) -> list[dict[str, Any]]:
    out = []
    for f in walk_files(root)[0]:
        rel = f.relative_to(root).as_posix()
        if is_protected(rel):
            continue
        out.append({"path": rel, "size": f.stat().st_size, "sha256": sha256_file(f)})
    return out


def _task_state(db: Session, project_id: int) -> list[dict[str, Any]]:
    from ..tasks.queue import deps_of

    rows = []
    for t in db.scalars(select(Task).where(Task.project_id == project_id).order_by(Task.position, Task.id)):
        rows.append({"id": t.id, "key": t.key, "title": t.title, "status": t.status, "run_state": t.run_state,
                     "priority": t.priority, "position": t.position, "assigned_account_id": t.assigned_account_id,
                     "progress": t.progress, "retry_count": t.retry_count, "error": t.error,
                     "depends_on": deps_of(db, t.id), "actual_output": t.actual_output[:20000],
                     "completed_at": t.completed_at.isoformat() if t.completed_at else None})
    return rows


def _conversation_refs(db: Session, project_id: int) -> list[dict[str, Any]]:
    refs = []
    for c in db.scalars(select(Conversation).where(Conversation.project_id == project_id)):
        last = db.scalar(select(ConversationMessage.id).where(ConversationMessage.conversation_id == c.id)
                         .order_by(ConversationMessage.id.desc()).limit(1))
        refs.append({"conversation_id": c.id, "task_id": c.task_id, "last_message_id": last})
    return refs


def _ckpt_dir(project: Project) -> Path:
    d = get_settings().checkpoints_dir / str(project.id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _zip_snapshot(root: Path, dest: Path) -> None:
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for f in walk_files(root)[0]:
            rel = f.relative_to(root).as_posix()
            if is_protected(rel):
                continue
            z.write(f, rel)


def create_checkpoint(db: Session, project: Project, reason: str = "manual", label: str = "",
                      task: Task | None = None) -> Checkpoint:
    root = project_root(project)
    commit, snapshot = "", ""
    try:
        if gitops.ensure_repo(root):
            commit = gitops.commit_all(root, f"Project Pilot checkpoint: {reason} {label}".strip())
    except gitops.GitError:
        commit = ""
    ck = Checkpoint(project_id=project.id, task_id=task.id if task else None, reason=reason,
                    label=label or reason.replace("_", " "), git_commit=commit)
    db.add(ck)
    db.flush()
    if not commit:
        snap = _ckpt_dir(project) / f"checkpoint-{ck.id}.zip"
        _zip_snapshot(root, snap)
        ck.snapshot_path = str(snap)
    tasks = _task_state(db, project.id)
    prompt = db.scalar(select(ProjectPrompt).where(ProjectPrompt.project_id == project.id,
                                                   ProjectPrompt.is_template.is_(False))
                       .order_by(ProjectPrompt.id.desc()).limit(1))
    ck.state = {
        "plan_status": project.plan_status,
        "tasks": tasks,
        "completed": [t["id"] for t in tasks if t["status"] == TaskStatus.COMPLETED.value],
        "pending": [t["id"] for t in tasks if t["status"] != TaskStatus.COMPLETED.value
                    and t["status"] != TaskStatus.CANCELLED.value],
        "current_task_id": task.id if task else next(
            (t["id"] for t in tasks if t["status"] in ("running", "awaiting_user")), None),
        "current_prompt": prompt.body if prompt else "",
        "file_manifest": manifest(root),
        "conversation_refs": _conversation_refs(db, project.id),
    }
    db.flush()
    log_event(db, "checkpoint.created", f"Checkpoint #{ck.id}: {ck.label}", project_id=project.id,
              task_id=task.id if task else None, data={"checkpoint_id": ck.id, "reason": reason})
    bus.publish("checkpoint", project_id=project.id, checkpoint_id=ck.id, label=ck.label)
    return ck


def list_checkpoints(db: Session, project_id: int) -> list[Checkpoint]:
    return list(db.scalars(select(Checkpoint).where(Checkpoint.project_id == project_id)
                           .order_by(Checkpoint.id.desc())))


def checkpoint_dict(ck: Checkpoint, full: bool = False) -> dict[str, Any]:
    st = ck.state or {}
    d = {"id": ck.id, "project_id": ck.project_id, "task_id": ck.task_id, "label": ck.label,
         "reason": ck.reason, "git_commit": ck.git_commit, "has_zip": bool(ck.snapshot_path),
         "created_at": ck.created_at.isoformat() if ck.created_at else None,
         "counts": {"tasks": len(st.get("tasks", [])), "completed": len(st.get("completed", [])),
                    "pending": len(st.get("pending", [])), "files": len(st.get("file_manifest", []))}}
    if full:
        d["state"] = st
    return d


def workspace_changes_since(db: Session, project: Project, ck: Checkpoint) -> list[str]:
    """Files whose current content differs from the *latest* checkpoint (i.e. unsaved newer work)."""
    root = project_root(project)
    latest = db.scalar(select(Checkpoint).where(Checkpoint.project_id == project.id)
                       .order_by(Checkpoint.id.desc()).limit(1))
    base = {m["path"]: m["sha256"] for m in ((latest.state or {}).get("file_manifest", []) if latest else [])}
    now = {m["path"]: m["sha256"] for m in manifest(root)}
    changed = [p for p in now if base.get(p) != now[p]] + [p for p in base if p not in now]
    return sorted(set(changed))


def compare(a: Checkpoint, b: Checkpoint) -> dict[str, Any]:
    ma = {m["path"]: m["sha256"] for m in (a.state or {}).get("file_manifest", [])}
    mb = {m["path"]: m["sha256"] for m in (b.state or {}).get("file_manifest", [])}
    ta = {t["id"]: t for t in (a.state or {}).get("tasks", [])}
    tb = {t["id"]: t for t in (b.state or {}).get("tasks", [])}
    return {
        "a": a.id, "b": b.id,
        "files_added": sorted(p for p in mb if p not in ma),
        "files_removed": sorted(p for p in ma if p not in mb),
        "files_modified": sorted(p for p in ma if p in mb and ma[p] != mb[p]),
        "tasks_status_changed": [{"id": i, "title": tb[i]["title"], "from": ta[i]["status"], "to": tb[i]["status"]}
                                 for i in ta if i in tb and ta[i]["status"] != tb[i]["status"]],
        "tasks_added": [i for i in tb if i not in ta],
        "tasks_removed": [i for i in ta if i not in tb],
    }


def restore_checkpoint(db: Session, project: Project, ck: Checkpoint, force: bool = False,
                       restore_tasks: bool = True) -> dict[str, Any]:
    root = project_root(project)
    changed = workspace_changes_since(db, project, ck)
    if changed and not force:
        raise RestoreConflict(changed)
    pre = create_checkpoint(db, project, reason="pre_restore",
                            label=f"Automatic backup before restoring #{ck.id}")
    target = {m["path"]: m["sha256"] for m in (ck.state or {}).get("file_manifest", [])}
    # 1. restore file contents
    if ck.git_commit:
        gitops.run_git(root, "checkout", ck.git_commit, "--", ".")
    elif ck.snapshot_path and Path(ck.snapshot_path).exists():
        with zipfile.ZipFile(ck.snapshot_path) as z:
            for name in z.namelist():
                dest = safe_join(root, name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(name) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out)
    else:
        raise CheckpointError("this checkpoint has no restorable snapshot")
    # 2. remove files that did not exist at the checkpoint (never protected files)
    removed = []
    for f in walk_files(root)[0]:
        rel = f.relative_to(root).as_posix()
        if rel not in target and not is_protected(rel):
            f.unlink()
            removed.append(rel)
    # 3. restore task state
    if restore_tasks:
        _restore_tasks(db, project, ck)
    sync_inventory(db, project)
    try:
        gitops.commit_all(root, f"Project Pilot: restored checkpoint #{ck.id}")
    except gitops.GitError:
        pass
    log_event(db, "checkpoint.restored", f"Restored checkpoint #{ck.id}", project_id=project.id,
              data={"checkpoint_id": ck.id, "pre_restore_checkpoint": pre.id, "removed": len(removed)})
    return {"restored": ck.id, "pre_restore_checkpoint": pre.id, "removed_files": removed,
            "overwrote_newer_changes": changed}


def _restore_tasks(db: Session, project: Project, ck: Checkpoint) -> None:
    saved = {t["id"]: t for t in (ck.state or {}).get("tasks", [])}
    for t in db.scalars(select(Task).where(Task.project_id == project.id)):
        s = saved.get(t.id)
        if s is None:
            continue
        status = s["status"]
        if status in (TaskStatus.RUNNING.value, TaskStatus.ASSIGNED.value):
            status = TaskStatus.READY.value
        t.status = status
        t.run_state = "idle"
        t.progress = s.get("progress", 0.0)
        t.retry_count = s.get("retry_count", 0)
        t.error = s.get("error", "")
        t.lock_token, t.locked_at = None, None
    db.flush()


def export_checkpoint(db: Session, project: Project, ck: Checkpoint) -> Path:
    """Write a ZIP containing the checkpoint state JSON (+ files when snapshot is retrievable)."""
    out = get_settings().exports_dir / f"checkpoint-{project.slug}-{ck.id}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    root = project_root(project)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("checkpoint.json", json.dumps(checkpoint_dict(ck, full=True), indent=2))
        if ck.git_commit:
            for m in (ck.state or {}).get("file_manifest", []):
                content = gitops.show_bytes(root, ck.git_commit, m["path"])
                if content is not None:
                    z.writestr(f"files/{m['path']}", content)
        elif ck.snapshot_path and Path(ck.snapshot_path).exists():
            with zipfile.ZipFile(ck.snapshot_path) as src:
                for n in src.namelist():
                    z.writestr(f"files/{n}", src.read(n))
    return out


def delete_checkpoint(db: Session, ck: Checkpoint) -> None:
    if ck.snapshot_path:
        Path(ck.snapshot_path).unlink(missing_ok=True)
    pid, cid = ck.project_id, ck.id
    db.delete(ck)
    db.flush()
    log_event(db, "checkpoint.deleted", f"Deleted checkpoint #{cid}", project_id=pid)
