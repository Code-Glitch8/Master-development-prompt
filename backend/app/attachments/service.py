"""Attachment storage and the file-handoff pipeline.

Nothing an AI session returns is applied directly: files are *staged*, compared
with the workspace (added / modified / deleted / unchanged) and only written to
the project after the user approves each change. Existing files are backed up
first, and protected files (.env, keys, databases) can never be overwritten.
"""
from __future__ import annotations

import difflib
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    Attachment, ChangeDecision, ChangeType, FileChange, Project, Task, utcnow,
)
from ..projects import gitops
from ..projects.importer import project_root, sync_inventory
from ..services.activity import log_event
from ..services.events import bus
from ..utils.paths import (
    UnsafePathError, is_config, is_protected, normalize_rel, safe_join, sha256_bytes, sha256_file, rmtree_force,
)
from ..utils.safe_zip import ArchiveError, extract_zip, inspect_zip

SOURCE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".scss", ".json", ".md", ".txt",
              ".java", ".kt", ".c", ".cpp", ".h", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".sh",
              ".bat", ".ps1", ".yml", ".yaml", ".xml", ".toml", ".sql", ".gradle", ".vue", ".svelte"}
DOC_EXT = {".pdf", ".docx", ".csv"}
FILE_BLOCK = re.compile(
    r"^#{2,4}\s*FILE:\s*(?P<path>[^\r\n`]+?)\s*\r?\n```[^\r\n]*\r?\n(?P<body>.*?)\r?\n```[ \t]*$",
    re.DOTALL | re.MULTILINE)


class AttachmentError(ValueError):
    pass


def file_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".zip":
        return "zip"
    if ext in SOURCE_EXT:
        return "source" if ext not in {".txt", ".md", ".json"} else ext.lstrip(".")
    if ext in DOC_EXT:
        return ext.lstrip(".")
    return "other"


def parse_file_blocks(text: str) -> list[tuple[str, str]]:
    """Extract `### FILE: path` fenced blocks from an AI response. Unsafe paths are dropped."""
    out: list[tuple[str, str]] = []
    for m in FILE_BLOCK.finditer(text or ""):
        try:
            out.append((normalize_rel(m.group("path").strip().strip("`")), m.group("body")))
        except UnsafePathError:
            continue
    return out


def _att_dir(project: Project) -> Path:
    d = get_settings().attachments_dir / str(project.id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_attachment(db: Session, project: Project, filename: str, content: bytes,
                    task: Task | None = None, message_id: int | None = None) -> Attachment:
    name = Path(filename.replace("\\", "/")).name
    if not name:
        raise AttachmentError("attachment needs a filename")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    stored = _att_dir(project) / "originals" / f"{stamp}-{re.sub(r'[^A-Za-z0-9._-]', '_', name)}"
    stored.parent.mkdir(parents=True, exist_ok=True)
    stored.write_bytes(content)
    att = Attachment(project_id=project.id, task_id=task.id if task else None, message_id=message_id,
                     filename=name, stored_path=str(stored), file_type=file_type(name), size=len(content),
                     sha256=sha256_bytes(content))
    db.add(att)
    db.flush()
    log_event(db, "attachment.saved", f"Attachment saved: {name}", project_id=project.id,
              task_id=task.id if task else None, data={"attachment_id": att.id, "type": att.file_type})
    return att


def _stage_dir(project: Project, key: str) -> Path:
    d = _att_dir(project) / "staging" / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def stage_files(db: Session, project: Project, files: dict[str, bytes], task: Task | None = None,
                attachment: Attachment | None = None, full_snapshot: bool = False,
                target_dir: str = "") -> list[FileChange]:
    """Compare incoming files to the workspace and record pending FileChanges."""
    root = project_root(project)
    key = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    stage = _stage_dir(project, key)
    changes: list[FileChange] = []
    incoming: set[str] = set()
    prefix = normalize_rel(target_dir) + "/" if target_dir else ""
    for rel, data in files.items():
        try:
            rel_n = prefix + normalize_rel(rel)
            normalize_rel(rel_n)
            dest = safe_join(root, rel_n)
        except UnsafePathError:
            continue
        incoming.add(rel_n)
        staged = safe_join(stage, rel_n)
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(data)
        new_sha = sha256_bytes(data)
        if dest.exists() and dest.is_file():
            old_sha = sha256_file(dest)
            ctype = ChangeType.UNCHANGED if old_sha == new_sha else ChangeType.MODIFIED
        else:
            old_sha, ctype = "", ChangeType.ADDED
        prot = is_protected(rel_n)
        summary = _summary(rel_n, ctype, dest if dest.exists() else None, data, prot)
        fc = FileChange(project_id=project.id, task_id=task.id if task else None,
                        attachment_id=attachment.id if attachment else None, path=rel_n,
                        change_type=ctype.value, previous_sha=old_sha, new_sha=new_sha,
                        staged_path=str(staged), protected=prot, summary=summary,
                        decision=(ChangeDecision.APPROVED.value if ctype == ChangeType.UNCHANGED
                                  else ChangeDecision.PENDING.value))
        db.add(fc)
        changes.append(fc)
    if full_snapshot:
        from ..projects.inventory import walk_files

        for f in walk_files(root)[0]:
            rel = f.relative_to(root).as_posix()
            if rel in incoming or rel.startswith(".git/"):
                continue
            prot = is_protected(rel)
            fc = FileChange(project_id=project.id, task_id=task.id if task else None,
                            attachment_id=attachment.id if attachment else None, path=rel,
                            change_type=ChangeType.DELETED.value, previous_sha=sha256_file(f),
                            protected=prot,
                            summary="Missing from the supplied snapshot" + (" (protected: will be kept)" if prot else ""))
            db.add(fc)
            changes.append(fc)
    db.flush()
    log_event(db, "files.staged", f"{len(changes)} file change(s) staged for review", project_id=project.id,
              task_id=task.id if task else None,
              data={"added": sum(c.change_type == "added" for c in changes),
                    "modified": sum(c.change_type == "modified" for c in changes),
                    "deleted": sum(c.change_type == "deleted" for c in changes)})
    bus.publish("file_changes", project_id=project.id, count=len(changes))
    return changes


def _summary(rel: str, ctype: ChangeType, existing: Path | None, data: bytes, prot: bool) -> str:
    bits = []
    if ctype == ChangeType.ADDED:
        bits.append("New file")
    elif ctype == ChangeType.MODIFIED and existing is not None:
        try:
            a = existing.read_text(encoding="utf-8").splitlines()
            b = data.decode("utf-8").splitlines()
            plus = sum(1 for l in difflib.ndiff(a, b) if l.startswith("+ "))
            minus = sum(1 for l in difflib.ndiff(a, b) if l.startswith("- "))
            bits.append(f"+{plus} / -{minus} lines")
        except (UnicodeDecodeError, OSError):
            bits.append("Binary or non-UTF-8 content changed")
    elif ctype == ChangeType.UNCHANGED:
        bits.append("Identical to current file")
    if is_config(rel):
        bits.append("configuration file")
    if prot:
        bits.append("PROTECTED: cannot be applied")
    return "; ".join(bits)


def ingest_attachment(db: Session, project: Project, filename: str, content: bytes,
                      task: Task | None = None, message_id: int | None = None,
                      full_snapshot: bool = False, target_dir: str = "") -> tuple[Attachment, list[FileChange]]:
    """Save an attachment, extract archives safely and stage resulting file changes."""
    att = save_attachment(db, project, filename, content, task, message_id)
    files: dict[str, bytes] = {}
    if att.file_type == "zip":
        tmp = Path(tempfile.mkdtemp(prefix="pp-att-", dir=str(get_settings().data_dir)))
        try:
            try:
                # Preserve the full folder hierarchy. Only drop a single wrapper folder when it is
                # named after this project (an AI re-zipping the whole project); never flatten `src/`.
                prefix = inspect_zip(Path(att.stored_path)).root_prefix.lower()
                own_names = {project.slug.lower(), project.name.lower(), Path(project.directory).name.lower()}
                extract_zip(Path(att.stored_path), tmp, strip_root=bool(prefix) and prefix in own_names)
            except ArchiveError as exc:
                raise AttachmentError(str(exc)) from exc
            for f in sorted(tmp.rglob("*")):
                if f.is_file():
                    files[f.relative_to(tmp).as_posix()] = f.read_bytes()
        finally:
            rmtree_force(tmp)
    else:
        default_dir = "attachments" if att.file_type in {"pdf", "docx", "csv", "other"} and not target_dir else target_dir
        files[att.filename] = content
        target_dir = default_dir
    changes = stage_files(db, project, files, task, att, full_snapshot=full_snapshot and att.file_type == "zip",
                          target_dir=target_dir)
    return att, changes


def stage_from_response(db: Session, project: Project, task: Task, response_text: str,
                        message_id: int | None = None) -> list[FileChange]:
    blocks = parse_file_blocks(response_text)
    if not blocks:
        return []
    files = {p: b.encode("utf-8") for p, b in blocks}
    return stage_files(db, project, files, task, None)


# ---- review ---------------------------------------------------------------------------

def diff_for(change: FileChange, project: Project, context: int = 3) -> str:
    root = project_root(project)
    try:
        current = safe_join(root, change.path)
    except UnsafePathError:
        return ""
    old = current.read_text(encoding="utf-8", errors="replace").splitlines() if current.exists() else []
    new: list[str] = []
    if change.staged_path and Path(change.staged_path).exists():
        new = Path(change.staged_path).read_text(encoding="utf-8", errors="replace").splitlines()
    if change.change_type == ChangeType.DELETED.value:
        new = []
    return "\n".join(difflib.unified_diff(old, new, f"a/{change.path}", f"b/{change.path}",
                                          lineterm="", n=context))


def decide(db: Session, change: FileChange, approve: bool, project: Project) -> FileChange:
    if change.decision != ChangeDecision.PENDING.value:
        raise AttachmentError(f"change already {change.decision}")
    if not approve:
        change.decision = ChangeDecision.REJECTED.value
        change.decided_at = utcnow()
        db.flush()
        log_event(db, "files.rejected", f"Rejected change to {change.path}", project_id=project.id,
                  task_id=change.task_id)
        return change
    if change.protected and change.change_type in (ChangeType.MODIFIED.value, ChangeType.DELETED.value):
        raise AttachmentError(f"{change.path} is a protected file (credentials/env/database) and will never be overwritten")
    if change.protected and change.change_type == ChangeType.ADDED.value:
        raise AttachmentError(f"{change.path} looks like a secrets file and cannot be added automatically")
    root = project_root(project)
    target = safe_join(root, change.path)
    # backup first
    if target.exists():
        bdir = _att_dir(project) / "backups" / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        bfile = safe_join(bdir, change.path)
        bfile.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, bfile)
        change.backup_path = str(bfile)
    if change.change_type == ChangeType.DELETED.value:
        if target.exists():
            target.unlink()
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = Path(change.staged_path)
        if not staged.exists():
            raise AttachmentError("staged file is missing")
        shutil.copy2(staged, target)
    change.decision = ChangeDecision.APPROVED.value
    change.decided_at = utcnow()
    db.flush()
    log_event(db, "files.applied", f"Applied {change.change_type} change to {change.path}",
              project_id=project.id, task_id=change.task_id)
    return change


def finalize_applied(db: Session, project: Project, task_id: int | None, message: str) -> str:
    """After applying changes: refresh inventory and commit to the workspace repo."""
    sync_inventory(db, project)
    try:
        if gitops.ensure_repo(project_root(project)):
            return gitops.commit_all(project_root(project), message)
    except gitops.GitError as exc:
        log_event(db, "git.warning", f"git commit skipped: {exc}", level="warning", project_id=project.id)
    return ""


def pending_changes(db: Session, project_id: int, task_id: int | None = None) -> list[FileChange]:
    q = select(FileChange).where(FileChange.project_id == project_id,
                                 FileChange.decision == ChangeDecision.PENDING.value)
    if task_id is not None:
        q = q.where(FileChange.task_id == task_id)
    return list(db.scalars(q.order_by(FileChange.id)))


def change_dict(c: FileChange) -> dict[str, Any]:
    return {"id": c.id, "project_id": c.project_id, "task_id": c.task_id, "attachment_id": c.attachment_id,
            "path": c.path, "change_type": c.change_type, "decision": c.decision, "protected": c.protected,
            "summary": c.summary, "previous_sha": c.previous_sha, "new_sha": c.new_sha,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "decided_at": c.decided_at.isoformat() if c.decided_at else None}
