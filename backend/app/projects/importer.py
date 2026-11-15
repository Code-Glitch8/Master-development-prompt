"""Project creation and importing (ZIP, folder, Git URL, single files)."""
from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Project, ProjectFile
from ..services.activity import log_event
from ..utils.paths import normalize_rel, safe_join, slugify, rmtree_force
from ..utils.safe_zip import ArchiveError, extract_zip
from . import gitops
from .inventory import Inventory, build_inventory


class ImportError_(ValueError):
    """Raised for invalid import requests (bad path, unsafe URL, ...)."""


def _unique_slug(db: Session, base: str) -> str:
    slug, n = base, 2
    while db.scalar(select(Project).where(Project.slug == slug)) is not None or (
        get_settings().projects_dir / slug
    ).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


def create_project(db: Session, name: str, description: str = "", source_type: str = "empty") -> Project:
    name = (name or "").strip()
    if not name:
        raise ImportError_("project name is required")
    if len(name) > 200:
        raise ImportError_("project name too long")
    settings = get_settings()
    slug = _unique_slug(db, slugify(name))
    directory = settings.projects_dir / slug
    directory.mkdir(parents=True, exist_ok=False)
    p = Project(name=name, slug=slug, directory=str(directory), description=description,
                source_type=source_type)
    db.add(p)
    db.flush()
    log_event(db, "project.created", f"Project '{name}' created", project_id=p.id)
    return p


def project_root(p: Project) -> Path:
    return Path(p.directory)


def sync_inventory(db: Session, p: Project, inv: Inventory | None = None) -> Inventory:
    """Rebuild the project_files table and stored inventory from disk."""
    root = project_root(p)
    inv = inv or build_inventory(root)
    existing = {f.path: f for f in db.scalars(select(ProjectFile).where(ProjectFile.project_id == p.id))}
    seen = set()
    for rec in inv.records:
        seen.add(rec.path)
        row = existing.get(rec.path)
        if row is None:
            db.add(ProjectFile(project_id=p.id, path=rec.path, size=rec.size, sha256=rec.sha256,
                               language=rec.language, is_config=rec.is_config, is_doc=rec.is_doc,
                               is_protected=rec.is_protected))
        else:
            row.size, row.sha256, row.language = rec.size, rec.sha256, rec.language
            row.is_config, row.is_doc, row.is_protected = rec.is_config, rec.is_doc, rec.is_protected
    for path, row in existing.items():
        if path not in seen:
            db.delete(row)
    p.inventory = inv.summary()
    db.flush()
    return inv


def _finish_import(db: Session, p: Project, warnings: list[str], what: str) -> Project:
    root = project_root(p)
    try:
        if gitops.ensure_repo(root):
            gitops.commit_all(root, f"Project Pilot: baseline after {what}")
    except gitops.GitError as exc:
        warnings.append(f"git baseline skipped: {exc}")
    inv = sync_inventory(db, p)
    p.import_warnings = warnings
    log_event(db, "project.imported", f"Imported {what}: {inv.file_count} files", project_id=p.id,
              data={"files": inv.file_count, "warnings": len(warnings)})
    return p


def import_zip(db: Session, zip_path: Path, name: str | None = None, description: str = "") -> Project:
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        raise ImportError_("ZIP file not found")
    p = create_project(db, name or zip_path.stem, description, "zip")
    try:
        report = extract_zip(zip_path, project_root(p))
    except ArchiveError as exc:
        rmtree_force(project_root(p))
        db.delete(p)
        db.flush()
        raise ImportError_(str(exc)) from exc
    warnings = list(report.warnings)
    if report.root_prefix:
        warnings.append(f"project root detected inside '{report.root_prefix}/' (prefix removed)")
    return _finish_import(db, p, warnings, f"ZIP '{zip_path.name}'")


def import_folder(db: Session, folder: Path, name: str | None = None, description: str = "") -> Project:
    src = Path(folder).expanduser()
    if not src.is_dir():
        raise ImportError_(f"folder not found: {folder}")
    src = src.resolve()
    data_dir = get_settings().data_dir.resolve()
    if src == data_dir or data_dir in src.parents or src in data_dir.parents:
        raise ImportError_("cannot import the application's own data folder (or a parent of it)")
    p = create_project(db, name or src.name, description, "folder")
    warnings: list[str] = []
    dest = project_root(p)

    def _ignore(_dir: str, names: list[str]) -> list[str]:
        return [n for n in names if n in {"node_modules", "__pycache__", ".venv", "venv"}]

    try:
        shutil.copytree(src, dest, dirs_exist_ok=True, symlinks=False, ignore=_ignore)
    except (OSError, shutil.Error) as exc:
        warnings.append(f"some files could not be copied: {exc}")
    for link in (x for x in dest.rglob("*") if x.is_symlink()):
        link.unlink(missing_ok=True)
        warnings.append(f"removed symlink: {link.relative_to(dest).as_posix()}")
    return _finish_import(db, p, warnings, f"folder '{src.name}'")


_GIT_URL = re.compile(r"^(https://[^\s]+|git@[A-Za-z0-9.\-]+:[^\s]+|ssh://[^\s]+)$")


def import_git(db: Session, url: str, name: str | None = None, description: str = "",
               branch: str | None = None) -> Project:
    url = (url or "").strip()
    if not _GIT_URL.match(url) or url.startswith("-"):
        raise ImportError_("only https://, ssh:// and git@host:path repository URLs are allowed")
    if not gitops.git_available():
        raise ImportError_("git is not installed")
    default_name = re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1].split(":")[-1]) or "repository"
    p = create_project(db, name or default_name, description, "git")
    dest = project_root(p)
    rmtree_force(dest)
    args = ["clone", "--quiet"]
    if branch:
        if not re.match(r"^[\w./-]+$", branch):
            raise ImportError_("invalid branch name")
        args += ["--branch", branch]
    # Uses the user's own git credentials/credential manager; no secrets handled here.
    try:
        gitops.run_git(dest.parent, *args, "--", url, str(dest))
    except gitops.GitError as exc:
        rmtree_force(dest)
        db.delete(p)
        db.flush()
        raise ImportError_(f"clone failed: {exc}") from exc
    return _finish_import(db, p, [], f"Git repository {url}")


def import_files(db: Session, files: list[tuple[str, bytes]], name: str | None = None,
                 project: Project | None = None) -> Project:
    """Import individual files (filename, content). Creates a project unless one is given."""
    if not files:
        raise ImportError_("no files provided")
    p = project or create_project(db, name or "Imported files", "", "files")
    root = project_root(p)
    warnings: list[str] = []
    for filename, content in files:
        try:
            rel = normalize_rel(Path(filename).name)
        except ValueError as exc:
            warnings.append(f"skipped {filename!r}: {exc}")
            continue
        target = safe_join(root, rel)
        if target.exists():
            stem, suffix, n = target.stem, target.suffix, 2
            while target.exists():
                target = root / f"{stem} ({n}){suffix}"
                n += 1
            warnings.append(f"duplicate filename {rel!r} saved as {target.name!r}")
        target.write_bytes(content)
    if project is not None:
        sync_inventory(db, p)
        p.import_warnings = list(p.import_warnings or []) + warnings
        return p
    return _finish_import(db, p, warnings, "individual files")


def delete_project(db: Session, p: Project, delete_files: bool = True) -> None:
    root = project_root(p)
    pid, name = p.id, p.name
    db.delete(p)
    db.flush()
    if delete_files and root.exists():
        # only ever delete inside the managed projects dir
        projects_dir = get_settings().projects_dir.resolve()
        if projects_dir in root.resolve().parents:
            rmtree_force(root)
    log_event(db, "project.deleted", f"Project '{name}' deleted", project_id=pid)


def tempdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="pp-", dir=str(get_settings().data_dir)))
