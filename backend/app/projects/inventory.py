"""Project inventory: languages, frameworks, dependencies, config/doc files.

Read-only analysis. Nothing in an imported project is ever executed.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..utils.paths import is_config, is_doc, is_protected, sha256_file

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".idea", ".vs",
             ".gradle", "Pods", ".pytest_cache", ".mypy_cache"}

LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".cjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".html": "HTML",
    ".htm": "HTML", ".css": "CSS", ".scss": "CSS", ".sass": "CSS", ".less": "CSS",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".c": "C", ".h": "C/C++ header",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".cs": "C#",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift",
    ".sh": "Shell", ".bat": "Batch", ".ps1": "PowerShell", ".sql": "SQL",
    ".json": "JSON", ".yml": "YAML", ".yaml": "YAML", ".xml": "XML", ".md": "Markdown",
    ".gradle": "Gradle", ".vue": "Vue", ".svelte": "Svelte",
}
CODE_LANGS = {"Python", "JavaScript", "TypeScript", "HTML", "CSS", "Java", "Kotlin", "C", "C++",
              "C#", "Go", "Rust", "Ruby", "PHP", "Swift", "Vue", "Svelte", "Shell", "SQL"}

TEXT_EXT = set(LANG_BY_EXT) | {".txt", ".rst", ".toml", ".ini", ".cfg", ".csv", ".env.example"}


@dataclass
class FileRecord:
    path: str
    size: int
    sha256: str
    language: str
    is_config: bool
    is_doc: bool
    is_protected: bool


@dataclass
class Inventory:
    file_count: int = 0
    total_bytes: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    frameworks: list[str] = field(default_factory=list)
    project_types: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    config_files: list[str] = field(default_factory=list)
    doc_files: list[str] = field(default_factory=list)
    git: dict[str, Any] = field(default_factory=dict)
    skipped_dirs: list[str] = field(default_factory=list)
    records: list[FileRecord] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "languages": self.languages,
            "frameworks": self.frameworks,
            "project_types": self.project_types,
            "package_managers": self.package_managers,
            "dependencies": self.dependencies,
            "config_files": self.config_files[:100],
            "doc_files": self.doc_files[:100],
            "git": self.git,
            "skipped_dirs": self.skipped_dirs,
        }


def language_of(rel: str) -> str:
    return LANG_BY_EXT.get(Path(rel).suffix.lower(), "")


def walk_files(root: Path) -> tuple[list[Path], set[str]]:
    files: list[Path] = []
    skipped: set[str] = set()
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.is_symlink():
                continue
            if e.is_dir():
                if e.name in SKIP_DIRS:
                    skipped.add(e.relative_to(root).as_posix())
                    continue
                stack.append(e)
            elif e.is_file():
                files.append(e)
    return sorted(files), skipped


def _read_json(p: Path) -> dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _detect(root: Path, rels: list[str], inv: Inventory) -> None:
    names = {Path(r).name for r in rels}
    lower_rels = [r.lower() for r in rels]
    fw: set[str] = set()
    types: set[str] = set()
    pms: set[str] = set()
    deps: dict[str, list[str]] = {}

    # Node / JS
    pkgs = [r for r in rels if Path(r).name == "package.json" and "node_modules" not in r]
    if pkgs:
        pms.add("npm")
        types.add("Node.js")
        node_deps: set[str] = set()
        for r in pkgs[:10]:
            data = _read_json(root / r)
            for sect in ("dependencies", "devDependencies"):
                node_deps.update((data.get(sect) or {}).keys())
        deps["npm"] = sorted(node_deps)[:300]
        for needle, label in [("react", "React"), ("vue", "Vue"), ("next", "Next.js"),
                              ("vite", "Vite"), ("@angular/core", "Angular"), ("svelte", "Svelte"),
                              ("express", "Express"), ("tailwindcss", "Tailwind CSS"),
                              ("typescript", "TypeScript")]:
            if needle in node_deps:
                fw.add(label)
                if label == "React":
                    types.add("React")
    if "yarn.lock" in names:
        pms.add("yarn")
    if "pnpm-lock.yaml" in names:
        pms.add("pnpm")

    # Python
    py_deps: set[str] = set()
    for r in rels:
        n = Path(r).name
        if n == "requirements.txt" or (n.startswith("requirements") and n.endswith(".txt")):
            pms.add("pip")
            for line in (root / r).read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith(("#", "-")):
                    py_deps.add(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].lower())
        if n == "pyproject.toml":
            pms.add("pip/pyproject")
            txt = (root / r).read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'"([A-Za-z0-9_.-]+)\s*[<>=!~]', txt):
                py_deps.add(m.group(1).lower())
        if n == "Pipfile":
            pms.add("pipenv")
    if py_deps or any(r.endswith(".py") for r in lower_rels):
        types.add("Python")
    if py_deps:
        deps["pip"] = sorted(py_deps)[:300]
        for needle, label in [("django", "Django"), ("flask", "Flask"), ("fastapi", "FastAPI"),
                              ("sqlalchemy", "SQLAlchemy"), ("pytest", "pytest")]:
            if needle in py_deps:
                fw.add(label)

    # JVM / Android
    gradle = [r for r in rels if Path(r).name in ("build.gradle", "build.gradle.kts")]
    poms = [r for r in rels if Path(r).name == "pom.xml"]
    if gradle:
        pms.add("gradle")
        gdeps: set[str] = set()
        for r in gradle[:10]:
            txt = (root / r).read_text(encoding="utf-8", errors="replace")
            gdeps.update(re.findall(r"""['"]([\w.\-]+:[\w.\-]+)(?::[\w.\-+]+)?['"]""", txt))
            if "com.android" in txt:
                types.add("Android")
                fw.add("Android")
        deps["gradle"] = sorted(gdeps)[:300]
    if poms:
        pms.add("maven")
        mdeps: set[str] = set()
        for r in poms[:10]:
            txt = (root / r).read_text(encoding="utf-8", errors="replace")
            mdeps.update(re.findall(r"<artifactId>([^<]+)</artifactId>", txt))
        deps["maven"] = sorted(mdeps)[:300]
        if any("spring" in d for d in mdeps):
            fw.add("Spring")
    if any(r.endswith(".java") for r in lower_rels):
        types.add("Java")
    if any(r.endswith((".kt", ".kts")) for r in lower_rels):
        types.add("Kotlin")
    if "androidmanifest.xml" in [Path(r).name.lower() for r in rels]:
        types.add("Android")
        fw.add("Android")

    # .NET / C#
    csproj = [r for r in rels if r.endswith(".csproj")]
    if csproj:
        pms.add("nuget")
        types.add("C#")
        nd: set[str] = set()
        for r in csproj[:10]:
            txt = (root / r).read_text(encoding="utf-8", errors="replace")
            nd.update(re.findall(r'<PackageReference\s+Include="([^"]+)"', txt))
        if nd:
            deps["nuget"] = sorted(nd)[:300]
    elif any(r.endswith(".cs") for r in lower_rels):
        types.add("C#")

    # Unity
    if "ProjectSettings/ProjectVersion.txt" in rels or (
        any(r.startswith("Assets/") for r in rels) and any(r.startswith("ProjectSettings/") for r in rels)
    ):
        types.add("Unity")
        fw.add("Unity")

    # C / C++
    if "CMakeLists.txt" in names:
        pms.add("cmake")
        types.add("C/C++")
    if any(r.endswith((".c", ".cpp", ".cc", ".cxx", ".h", ".hpp")) for r in lower_rels):
        types.add("C/C++")

    # TypeScript / plain web
    if any(r.endswith((".ts", ".tsx")) for r in lower_rels):
        types.add("TypeScript")
    web_only = any(r.endswith((".html", ".htm")) for r in lower_rels) and not pkgs
    if web_only or (any(r.endswith(".html") for r in lower_rels) and any(r.endswith(".css") for r in lower_rels)):
        types.add("HTML/CSS/JavaScript")

    inv.frameworks = sorted(fw)
    inv.project_types = sorted(types)
    inv.package_managers = sorted(pms)
    inv.dependencies = deps


def git_status(root: Path) -> dict[str, Any]:
    """Read-only git status. Never runs hooks or project scripts."""
    from .gitops import is_repo, run_git

    if not is_repo(root):
        return {"is_repo": False}
    out: dict[str, Any] = {"is_repo": True}
    try:
        out["branch"] = run_git(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    except Exception:
        out["branch"] = ""
    try:
        porcelain = run_git(root, "status", "--porcelain").splitlines()
        out["dirty_files"] = len(porcelain)
        out["clean"] = not porcelain
    except Exception:
        out["dirty_files"], out["clean"] = 0, True
    try:
        out["last_commit"] = run_git(root, "log", "-1", "--format=%h %s").strip()
    except Exception:
        out["last_commit"] = ""
    return out


def build_inventory(root: Path) -> Inventory:
    inv = Inventory()
    files, skipped = walk_files(root)
    inv.skipped_dirs = sorted(skipped)
    rels = [f.relative_to(root).as_posix() for f in files]
    langs: Counter[str] = Counter()
    for f, rel in zip(files, rels):
        try:
            size = f.stat().st_size
            sha = sha256_file(f)
        except OSError:
            continue
        lang = language_of(rel)
        rec = FileRecord(rel, size, sha, lang, is_config(rel), is_doc(rel), is_protected(rel))
        inv.records.append(rec)
        inv.total_bytes += size
        if lang in CODE_LANGS:
            langs[lang] += 1
        if rec.is_config:
            inv.config_files.append(rel)
        if rec.is_doc:
            inv.doc_files.append(rel)
    inv.file_count = len(inv.records)
    inv.languages = dict(langs.most_common())
    _detect(root, rels, inv)
    inv.git = git_status(root)
    return inv
