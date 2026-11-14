"""Thin, safe wrapper around the git CLI (read/write on project workspaces).

Hooks are disabled (`core.hooksPath=<empty>`) so imported repositories can never
run code through git. Every call has a timeout.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

GIT_TIMEOUT = 120
_NO_HOOKS = ["-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
             "-c", "protocol.ext.allow=never"]


class GitError(RuntimeError):
    pass


def git_available() -> bool:
    return shutil.which("git") is not None


def run_git(cwd: Path, *args: str, check: bool = True, input_text: str | None = None) -> str:
    if not git_available():
        raise GitError("git executable not found")
    cmd = ["git", *_NO_HOOKS, *args]
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=GIT_TIMEOUT,
            input=input_text, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out") from exc
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def is_repo(root: Path) -> bool:
    return (root / ".git").exists()


_IDENT = ["-c", "user.name=Project Pilot", "-c", "user.email=project-pilot@localhost"]


def ensure_repo(root: Path) -> bool:
    """Initialise a repo if missing. Returns True if a repo exists afterwards."""
    if not git_available():
        return False
    if not is_repo(root):
        run_git(root, "init", "-q")
    _exclude_protected(root)
    return True


def _exclude_protected(root: Path) -> None:
    """Keep secrets/DB files out of the workspace repo (never committed, never restored over)."""
    from ..utils.paths import PROTECTED_PATTERNS

    info = root / ".git" / "info"
    try:
        info.mkdir(parents=True, exist_ok=True)
        f = info / "exclude"
        current = f.read_text(encoding="utf-8") if f.exists() else ""
        lines = ["# Project Pilot: protected files are never tracked"]
        lines += [p for p in PROTECTED_PATTERNS if p not in current]
        lines += ["!*.example", "!*.sample", "!*.template", "!*.dist"]
        if len(lines) > 1 and "Project Pilot: protected" not in current:
            f.write_text(current + ("\n" if current and not current.endswith("\n") else "")
                         + "\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def commit_all(root: Path, message: str) -> str:
    """Stage everything and commit. Returns the commit hash ('' if nothing to commit)."""
    run_git(root, "add", "-A")
    status = run_git(root, "status", "--porcelain")
    if not status.strip():
        try:
            return run_git(root, "rev-parse", "HEAD").strip()
        except GitError:
            return ""
    run_git(root, *_IDENT, "commit", "-q", "-m", message, "--no-verify")
    return run_git(root, "rev-parse", "HEAD").strip()


def head(root: Path) -> str:
    try:
        return run_git(root, "rev-parse", "HEAD").strip()
    except GitError:
        return ""


def log(root: Path, limit: int = 50, path: str | None = None) -> list[dict[str, str]]:
    args = ["log", f"-{limit}", "--format=%H%x1f%h%x1f%an%x1f%aI%x1f%s"]
    if path:
        args += ["--", path]
    try:
        out = run_git(root, *args)
    except GitError:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 5:
            rows.append({"hash": parts[0], "short": parts[1], "author": parts[2],
                         "date": parts[3], "subject": parts[4]})
    return rows


def show_file(root: Path, commit: str, path: str) -> str | None:
    try:
        return run_git(root, "show", f"{commit}:{path}")
    except GitError:
        return None


def show_bytes(root: Path, commit: str, path: str) -> bytes | None:
    """Binary-safe `git show commit:path`."""
    try:
        proc = subprocess.run(["git", *_NO_HOOKS, "show", f"{commit}:{path}"], cwd=str(root),
                              capture_output=True, timeout=GIT_TIMEOUT,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def diff_commits(root: Path, a: str, b: str, path: str | None = None) -> str:
    args = ["diff", "--no-color", a, b]
    if path:
        args += ["--", path]
    try:
        return run_git(root, *args)
    except GitError as exc:
        return f"(diff unavailable: {exc})"


def changed_files(root: Path, a: str, b: str) -> list[dict[str, str]]:
    try:
        out = run_git(root, "diff", "--name-status", a, b)
    except GitError:
        return []
    res = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            res.append({"status": parts[0], "path": parts[-1]})
    return res
