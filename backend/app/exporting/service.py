"""Project export. Credentials, env files, keys, databases and account/session data are never included."""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..checkpoints.service import checkpoint_dict, list_checkpoints
from ..config import get_settings
from ..conversations import handoff
from ..conversations import service as conv
from ..models import FileChange, Project, Task, TaskStatus
from ..projects import gitops
from ..projects.importer import project_root
from ..projects.inventory import walk_files
from ..services.activity import log_event
from ..services.notifications import notify
from ..utils.paths import is_protected


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _out(project: Project, kind: str) -> Path:
    d = get_settings().exports_dir
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{project.slug}-{kind}-{_stamp()}.zip"


def task_report(db: Session, project: Project) -> str:
    tasks = list(db.scalars(select(Task).where(Task.project_id == project.id).order_by(Task.position, Task.id)))
    done = [t for t in tasks if t.status == TaskStatus.COMPLETED.value]
    L = [f"# Task completion report: {project.name}", "",
         f"{len(done)} of {len(tasks)} tasks completed.", "",
         "| # | Task | Status | Completed |", "|---|------|--------|-----------|"]
    for t in tasks:
        L.append(f"| {t.key} | {t.title} | {t.status} | {t.completed_at.isoformat() if t.completed_at else ''} |")
    return "\n".join(L) + "\n"


def final_report(db: Session, project: Project) -> str:
    st = handoff.build_state(db, project)
    changes = list(db.scalars(select(FileChange).where(FileChange.project_id == project.id,
                                                       FileChange.decision == "approved",
                                                       FileChange.change_type != "unchanged")))
    L = [f"# Final report: {project.name}", "", "## Master prompt", st["master_prompt"] or "(none)", "",
         "## Outcome",
         f"- Tasks completed: {len(st['completed_tasks'])}",
         f"- Tasks unfinished: {len(st['unfinished_tasks'])}",
         f"- File changes applied: {len(changes)}", "", "## Completed tasks"]
    for t in st["completed_tasks"]:
        L.append(f"### {t['key']} {t['title']}")
        L.append((t["output"] or "(no output recorded)")[:3000])
        L.append("")
    if st["unfinished_tasks"]:
        L.append("## Unfinished tasks")
        L.extend(f"- {t['key']} {t['title']} ({t['status']})" for t in st["unfinished_tasks"])
    if st["errors"]:
        L.append("\n## Known errors")
        L.extend(f"- {e['title']}: {e['error'][:300]}" for e in st["errors"])
    L.append("\n## Applied file changes")
    L.extend(f"- {c.change_type}: {c.path}" for c in changes) or L.append("- (none)")
    return "\n".join(L) + "\n"


def _write_conversations(db: Session, project: Project, z: zipfile.ZipFile, prefix: str = "conversations/") -> None:
    for c in conv.list_conversations(db, project.id):
        base = f"{prefix}task-{c.task_id or c.id}"
        z.writestr(base + ".md", conv.export_markdown(db, c))
        z.writestr(base + ".json", json.dumps(conv.export_json(db, c), indent=2, ensure_ascii=False))


def _history(project: Project) -> list[dict[str, str]]:
    return gitops.log(project_root(project), 500)


def export_project(db: Session, project: Project, include_original: bool = False,
                   include_git: bool = False) -> Path:
    root = project_root(project)
    out = _out(project, "export")
    skipped: list[str] = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in walk_files(root)[0]:
            rel = f.relative_to(root).as_posix()
            if is_protected(rel):
                skipped.append(rel)
                continue
            z.write(f, f"project/{rel}")
        if include_git and (root / ".git").exists():
            for f in (root / ".git").rglob("*"):
                if f.is_file():
                    z.write(f, f"project/.git/{f.relative_to(root / '.git').as_posix()}")
        if include_original:
            hist = gitops.log(root, 500)
            if hist:
                first = hist[-1]["hash"]
                names = gitops.run_git(root, "ls-tree", "-r", "--name-only", first, check=False).splitlines()
                for n in names:
                    if is_protected(n):
                        continue
                    data = gitops.show_bytes(root, first, n)
                    if data is not None:
                        z.writestr(f"original/{n}", data)
        z.writestr("README.md", (
            f"# {project.name} (exported from Project Pilot)\n\n"
            f"- `project/`: the finished project files\n- `FINAL_REPORT.md`: what was done\n"
            f"- `TASK_COMPLETION_REPORT.md`: status of every task\n- `handoff/`: HANDOFF.md and state JSON\n"
            f"- `conversations/`: every recorded prompt and response\n- `history/`: change and checkpoint history\n\n"
            f"Credentials, environment files, private keys and databases were not exported"
            f"{' (' + str(len(skipped)) + ' file(s) skipped)' if skipped else ''}.\n"))
        z.writestr("FINAL_REPORT.md", final_report(db, project))
        z.writestr("TASK_COMPLETION_REPORT.md", task_report(db, project))
        hd = handoff.write_handoff(db, project)
        for f in hd.iterdir():
            z.write(f, f"handoff/{f.name}")
        _write_conversations(db, project, z)
        z.writestr("history/git-history.json", json.dumps(_history(project), indent=2))
        z.writestr("history/checkpoints.json", json.dumps(
            [checkpoint_dict(c, full=False) for c in list_checkpoints(db, project.id)], indent=2))
    log_event(db, "export.completed", f"Project exported: {out.name}", project_id=project.id,
              data={"path": str(out), "skipped_protected": len(skipped)})
    notify(db, "export_completed", "Export completed", out.name, project.id)
    return out


def export_conversations(db: Session, project: Project) -> Path:
    out = _out(project, "conversations")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        _write_conversations(db, project, z, "")
    return out


def export_history(db: Session, project: Project) -> Path:
    out = _out(project, "history")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("git-history.json", json.dumps(_history(project), indent=2))
        z.writestr("file-changes.json", json.dumps([
            {"path": c.path, "type": c.change_type, "decision": c.decision, "task_id": c.task_id,
             "summary": c.summary, "at": c.created_at.isoformat() if c.created_at else None}
            for c in db.scalars(select(FileChange).where(FileChange.project_id == project.id))], indent=2))
    return out


def export_checkpoints(db: Session, project: Project) -> Path:
    out = _out(project, "checkpoints")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for c in list_checkpoints(db, project.id):
            z.writestr(f"checkpoint-{c.id}.json", json.dumps(checkpoint_dict(c, full=True), indent=2))
    return out


def export_handoff(db: Session, project: Project) -> Path:
    out = _out(project, "handoff")
    hd = handoff.write_handoff(db, project)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in hd.iterdir():
            z.write(f, f.name)
    return out
