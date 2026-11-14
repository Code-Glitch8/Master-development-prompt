"""Path validation, hashing and protected-file rules."""
from __future__ import annotations

import fnmatch
import hashlib
import re
from pathlib import Path, PurePosixPath

# Files that must never be silently overwritten.
PROTECTED_PATTERNS = [
    ".env",
    ".env.*",
    "*.env",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "credentials.json",
    "secrets.json",
    "secrets.*.json",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
    "serviceAccount*.json",
    ".npmrc",
    ".pypirc",
]

# Config files that need explicit approval to change (not blocked, but flagged).
CONFIG_NAMES = {
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "tsconfig.json",
    "vite.config.ts", "vite.config.js", "webpack.config.js", "requirements.txt",
    "pyproject.toml", "setup.py", "setup.cfg", "pom.xml", "build.gradle",
    "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradle.properties",
    "Cargo.toml", "go.mod", "Dockerfile", "docker-compose.yml", "Makefile",
    "CMakeLists.txt", "AndroidManifest.xml", ".gitignore", ".editorconfig",
    "tailwind.config.js", "tailwind.config.ts", "next.config.js", "manifest.json",
    "ProjectSettings.asset", "packages.config", "global.json", "nuget.config",
}
CONFIG_SUFFIXES = (".csproj", ".sln", ".vcxproj", ".yml", ".yaml", ".toml", ".ini", ".cfg")

DOC_NAMES = {"readme", "readme.md", "readme.txt", "changelog", "changelog.md", "contributing.md",
             "license", "license.md", "license.txt", "handoff.md"}


class UnsafePathError(ValueError):
    pass


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


SAFE_SUFFIXES = (".example", ".sample", ".template", ".dist")


def is_protected(rel_path: str) -> bool:
    name = PurePosixPath(rel_path).name
    if name.lower().endswith(SAFE_SUFFIXES):
        return False  # e.g. .env.example holds no secrets
    return any(fnmatch.fnmatch(name.lower(), p.lower()) for p in PROTECTED_PATTERNS)


def is_config(rel_path: str) -> bool:
    name = PurePosixPath(rel_path).name
    return name in CONFIG_NAMES or name.lower().endswith(CONFIG_SUFFIXES)


def is_doc(rel_path: str) -> bool:
    p = PurePosixPath(rel_path)
    return p.name.lower() in DOC_NAMES or p.suffix.lower() in {".md", ".rst", ".txt"} and "docs" in [
        x.lower() for x in p.parts[:-1]
    ]


def normalize_rel(path: str) -> str:
    """Normalise an untrusted relative path into a safe posix relative path.

    Rejects absolute paths, drive letters, `..` segments, NUL bytes and
    Windows reserved characters. Raises UnsafePathError.
    """
    if path is None or path == "":
        raise UnsafePathError("empty path")
    if "\x00" in path:
        raise UnsafePathError("NUL byte in path")
    p = path.replace("\\", "/")
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p) or p.startswith("//"):
        raise UnsafePathError(f"absolute path not allowed: {path!r}")
    parts = []
    for seg in p.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            raise UnsafePathError(f"path traversal not allowed: {path!r}")
        if re.search(r'[<>:"|?*\x00-\x1f]', seg):
            raise UnsafePathError(f"invalid characters in path: {path!r}")
        parts.append(seg)
    if not parts:
        raise UnsafePathError("empty path")
    return "/".join(parts)


def safe_join(root: Path, rel: str) -> Path:
    """Join `rel` under `root`, guaranteeing the result stays inside root."""
    rel_n = normalize_rel(rel)
    root_r = root.resolve()
    target = (root_r / rel_n).resolve()
    try:
        target.relative_to(root_r)
    except ValueError as exc:
        raise UnsafePathError(f"path escapes root: {rel!r}") from exc
    return target


def slugify(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-._")
    return (s or "project")[:80].lower()


def unique_dir(parent: Path, base: str) -> Path:
    """Return a non-existing directory path under parent (base, base-2, ...)."""
    cand = parent / base
    n = 2
    while cand.exists():
        cand = parent / f"{base}-{n}"
        n += 1
    return cand
