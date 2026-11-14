"""Prompt analysis and task decomposition.

Deterministic and local: no AI call and no API key needed. It splits the master
prompt into discrete requested changes, finds relevant files from the project
inventory, assesses risks, and builds a dependency graph of tasks. Users can
edit, reorder, add and delete the generated tasks before approving the plan.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Project, ProjectFile, ProjectPrompt, Task, TaskDependency, TaskStatus
from ..models.enums import PlanStatus
from ..services.activity import log_event

# category -> (keywords, capability, expected deliverable)
CATEGORIES: dict[str, tuple[tuple[str, ...], str, str]] = {
    "ui": (("ui", "ux", "design", "redesign", "layout", "dashboard", "style", "css", "theme",
            "page", "screen", "component", "interface", "navigation"), "code",
           "Updated UI source files"),
    "mobile": (("mobile", "responsive", "responsiveness", "tablet", "breakpoint", "viewport"),
               "code", "Responsive layout changes"),
    "auth": (("auth", "authentication", "login", "logout", "password", "session", "oauth", "token",
              "permission", "signup", "sign-up"), "code", "Authentication fixes"),
    "performance": (("performance", "optimize", "optimise", "speed", "slow", "latency", "cache",
                     "bundle", "memory", "lazy"), "analysis", "Performance improvements"),
    "bugs": (("bug", "bugs", "fix", "error", "errors", "crash", "broken", "issue", "issues",
              "exception"), "code", "Bug fixes"),
    "tests": (("test", "tests", "testing", "coverage", "qa", "verify", "validate"), "testing",
              "Test code and results"),
    "docs": (("document", "documentation", "readme", "docs", "comment", "comments", "guide"),
             "documentation", "Documentation files"),
    "security": (("security", "vulnerability", "vulnerabilities", "xss", "csrf", "injection",
                  "sanitize", "sanitise", "encrypt"), "review", "Security fixes and notes"),
    "database": (("database", "db", "sql", "schema", "migration", "query", "queries", "orm"),
                 "code", "Database changes"),
    "api": (("api", "endpoint", "endpoints", "rest", "graphql", "backend", "server", "route",
             "routes"), "code", "API changes"),
    "refactor": (("refactor", "cleanup", "clean", "restructure", "modernize", "modernise",
                  "migrate", "upgrade"), "code", "Refactored code"),
}
RISKY_CATEGORIES = {"auth", "database", "security", "refactor"}
STOPWORDS = set("""a an and or the to of in on for with all any each every this that it its be is are
was were by as at from into out up make improve fix update add create implement ensure also then
please should must need needs want completely entire whole application app project code""".split())


@dataclass
class PlannedTask:
    key: str
    title: str
    description: str
    category: str
    capability: str
    expected_output: str
    depends_on: list[str] = field(default_factory=list)
    relevant_files: list[str] = field(default_factory=list)
    priority: int = 3


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z][a-z0-9_]{2,}", text.lower()) if t not in STOPWORDS}


def split_requests(prompt: str) -> list[str]:
    """Break a master prompt into individual requested changes."""
    text = prompt.strip()
    if not text:
        return []
    items: list[str] = []
    # bullet / numbered lines first
    bullet = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*\S)\s*$")
    lines = text.splitlines()
    bullets = [m.group(1) for line in lines if (m := bullet.match(line))]
    if len(bullets) >= 2:
        items = bullets
    else:
        # sentences, then commas / "and" lists inside a sentence
        sentences = re.split(r"(?<=[.!?])\s+|\n{2,}|\n", text)
        for s in sentences:
            s = s.strip().rstrip(".")
            if not s:
                continue
            parts = re.split(r",\s*(?:and\s+)?|;\s*|\s+and\s+(?=(?:fix|improve|update|add|test|optimi[sz]e|redesign|"
                             r"create|implement|refactor|document|review|make|ensure|remove|migrate)\b)", s)
            items.extend(p.strip() for p in parts if p and len(p.strip()) > 2)
    # de-duplicate preserving order
    seen: set[str] = set()
    out = []
    for i in items:
        k = i.lower()
        if k not in seen:
            seen.add(k)
            out.append(i)
    return out[:25]


# Generic verbs ("fix", "errors") appear in nearly every request; they must not outweigh a
# domain word such as "authentication" or "performance".
GENERIC_CATEGORIES = {"bugs", "refactor"}
GENERIC_WEIGHT = 0.4


def classify(text: str) -> str:
    toks = _tokens(text) | set(re.findall(r"[a-z]+", text.lower()))
    best, score = "general", 0.0
    for cat, (kws, _cap, _out) in CATEGORIES.items():
        sc = len(toks & set(kws)) * (GENERIC_WEIGHT if cat in GENERIC_CATEGORIES else 1.0)
        if sc > score:
            best, score = cat, sc
    return best


def _stem_match(a: str, b: str) -> bool:
    """Equal, or one is a prefix of the other (auth ~ authentication), min 4 chars for the prefix."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 4 and long_.startswith(short)


def find_relevant_files(text: str, files: list[str], limit: int = 12) -> list[str]:
    toks = _tokens(text)
    cat = classify(text)
    scored: list[tuple[int, str]] = []
    for f in files:
        p = PurePosixPath(f)
        ftoks = _tokens(" ".join(p.parts).replace(".", " ").replace("_", " ").replace("-", " "))
        sc = sum(1 for ft in ftoks if any(_stem_match(t, ft) for t in toks)) * 3
        if cat in ("ui", "mobile") and p.suffix.lower() in {".css", ".scss", ".html", ".tsx", ".jsx", ".vue", ".svelte"}:
            sc += 1
        if cat == "tests" and ("test" in f.lower() or "spec" in f.lower()):
            sc += 2
        if cat == "docs" and p.suffix.lower() in {".md", ".rst"}:
            sc += 2
        if cat in ("auth", "security") and re.search(r"auth|login|session|token|secur", f.lower()):
            sc += 3
        if sc > 0:
            scored.append((sc, f))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [f for _s, f in scored[:limit]]


def analyze_prompt(prompt: str, inventory: dict[str, Any], files: list[str]) -> dict[str, Any]:
    requests = split_requests(prompt)
    cats = [classify(r) for r in requests]
    caps = sorted({CATEGORIES[c][1] for c in cats if c in CATEGORIES} | {"analysis", "review"})
    deliverables = sorted({CATEGORIES[c][2] for c in cats if c in CATEGORIES}) or ["Updated project files"]
    # per-request matches first (each uses its own category), then the whole prompt
    relevant: list[str] = []
    for r in requests:
        for f in find_relevant_files(r, files, 6):
            if f not in relevant:
                relevant.append(f)
    for f in find_relevant_files(prompt, files, 25):
        if f not in relevant:
            relevant.append(f)
    relevant = relevant[:25]
    risks: list[str] = []
    if any(c in RISKY_CATEGORIES for c in cats):
        risky = sorted({c for c in cats if c in RISKY_CATEGORIES})
        risks.append(f"Request touches sensitive areas ({', '.join(risky)}); review diffs carefully.")
    if not (inventory.get("git") or {}).get("is_repo"):
        risks.append("Project is not a Git repository; Project Pilot snapshots will be used for history.")
    if not any("test" in f.lower() for f in files):
        risks.append("No existing tests detected; regressions are harder to catch.")
    if any(f.endswith((".env",)) or f.startswith(".env") for f in files):
        risks.append("Environment files detected; they are protected and will never be overwritten.")
    if len(requests) > 12:
        risks.append("Prompt describes many changes; consider splitting it across milestones.")
    testing = ["Run existing test suite" if any("test" in f.lower() for f in files) else
               "Manually verify each changed behaviour"]
    if "mobile" in cats or "ui" in cats:
        testing.append("Check layouts at mobile, tablet and desktop widths")
    if "auth" in cats:
        testing.append("Verify login, logout and session expiry flows")
    return {
        "required_changes": requests,
        "categories": cats,
        "relevant_files": relevant,
        "dependencies": inventory.get("dependencies", {}),
        "risks": risks,
        "required_capabilities": caps,
        "expected_deliverables": deliverables,
        "testing_requirements": testing,
    }


def build_plan(prompt: str, inventory: dict[str, Any], files: list[str]) -> tuple[dict[str, Any], list[PlannedTask]]:
    analysis = analyze_prompt(prompt, inventory, files)
    requests: list[str] = analysis["required_changes"]
    cats: list[str] = analysis["categories"]
    tasks: list[PlannedTask] = []

    tasks.append(PlannedTask(
        "T1", "Analyse project structure",
        "Read the project inventory and files. Summarise architecture, entry points, conventions and "
        "anything relevant to the master prompt. Do not modify files.",
        "analysis", "analysis", "A written structure/architecture summary", [],
        analysis["relevant_files"][:15], 1))
    tasks.append(PlannedTask(
        "T2", "Create implementation plan",
        "Using the analysis, write a concrete step-by-step implementation plan for the master prompt, "
        "listing files to change, risks and test approach. Do not modify files yet.",
        "planning", "planning", "An ordered implementation plan", ["T1"], analysis["relevant_files"][:15], 1))
    impl_keys: list[str] = []
    n = 3
    for req, cat in zip(requests, cats):
        cap, out = (CATEGORIES[cat][1], CATEGORIES[cat][2]) if cat in CATEGORIES else ("code", "Updated project files")
        if cat == "tests":
            continue  # handled by dedicated testing tasks below
        key = f"T{n}"
        n += 1
        title = req[0].upper() + req[1:] if req else "Implement change"
        tasks.append(PlannedTask(
            key, title[:200], f"Implement this requested change: {req}", cat, cap, out,
            ["T2"], find_relevant_files(req, files), 2 if cat in RISKY_CATEGORIES else 3))
        impl_keys.append(key)
    if not impl_keys:  # nothing parseable: one generic implementation task
        tasks.append(PlannedTask(f"T{n}", "Implement the requested changes", prompt.strip()[:4000],
                                 "general", "code", "Updated project files", ["T2"],
                                 analysis["relevant_files"][:15]))
        impl_keys.append(f"T{n}")
        n += 1
    test_keys: list[str] = []
    tkey = f"T{n}"
    n += 1
    tasks.append(PlannedTask(
        tkey, "Test and verify changes",
        "Verify every change made by the previous tasks: run/describe tests, check for regressions and "
        "list anything that still fails.", "tests", "testing", "Test results and a list of any failures",
        list(impl_keys), []))
    test_keys.append(tkey)
    if "mobile" in cats or "ui" in cats:
        rkey = f"T{n}"
        n += 1
        tasks.append(PlannedTask(
            rkey, "Test responsive layouts",
            "Check the changed screens at mobile, tablet and desktop widths and report problems.",
            "tests", "testing", "Responsive test report", list(impl_keys), []))
        test_keys.append(rkey)
    review_key = f"T{n}"
    n += 1
    tasks.append(PlannedTask(
        review_key, "Review all changes",
        "Review the complete set of changes for correctness, style consistency and leftover issues.",
        "review", "review", "Review notes with required follow-ups", list(test_keys), [], 2))
    tasks.append(PlannedTask(
        f"T{n}", "Generate final report",
        "Write a final report: what changed, what was tested, known issues and next steps.",
        "docs", "documentation", "FINAL_REPORT.md contents", [review_key], []))
    return analysis, tasks


# ---- persistence ------------------------------------------------------------

def compose_prompt(project: Project, master_prompt: str, t: PlannedTask | Task) -> str:
    """Base prompt for a task (the engine appends handoff context at run time)."""
    title = t.title
    desc = t.description
    files = t.relevant_files if isinstance(t, PlannedTask) else (t.relevant_files or [])
    expected = t.expected_output
    parts = [
        f"You are helping with the software project '{project.name}'.",
        "## Overall goal (master prompt)\n" + master_prompt.strip(),
    ]
    if project.instructions.strip():
        parts.append("## Project-specific instructions\n" + project.instructions.strip())
    parts.append(f"## Your task: {title}\n{desc}")
    if files:
        parts.append("## Relevant files\n" + "\n".join(f"- {f}" for f in files))
    parts.append(f"## Expected output\n{expected}")
    parts.append(
        "## Output format\nExplain what you did. If you create or modify files, return each COMPLETE file "
        "like this (one block per file, path relative to the project root):\n\n"
        "### FILE: path/to/file.ext\n```\n<full file content>\n```\n\n"
        "Never include secrets, credentials or contents of .env files."
    )
    return "\n\n".join(parts)


def replace_plan(db: Session, project: Project, prompt: ProjectPrompt) -> list[Task]:
    """(Re)generate the task plan from a prompt. Existing not-yet-started tasks are replaced."""
    started = db.scalars(select(Task).where(
        Task.project_id == project.id,
        Task.status.notin_([TaskStatus.PENDING.value, TaskStatus.READY.value, TaskStatus.ASSIGNED.value,
                            TaskStatus.PAUSED.value, TaskStatus.CANCELLED.value]))).first()
    if started is not None:
        raise ValueError("Plan already has started or completed tasks; add or edit tasks instead of regenerating.")
    for t in db.scalars(select(Task).where(Task.project_id == project.id)):
        db.delete(t)
    db.flush()
    files = [f.path for f in db.scalars(select(ProjectFile).where(ProjectFile.project_id == project.id))]
    analysis, planned = build_plan(prompt.body, project.inventory or {}, files)
    prompt.analysis = analysis
    key_to_task: dict[str, Task] = {}
    for i, pt in enumerate(planned):
        t = Task(
            project_id=project.id, prompt_id=prompt.id, key=pt.key, title=pt.title,
            description=pt.description, category=pt.category, priority=pt.priority, position=i,
            required_capability=pt.capability, relevant_files=pt.relevant_files,
            expected_output=pt.expected_output, status=TaskStatus.PENDING.value,
        )
        t.input_prompt = compose_prompt(project, prompt.body, pt)
        db.add(t)
        key_to_task[pt.key] = t
    db.flush()
    for pt in planned:
        for dep in pt.depends_on:
            db.add(TaskDependency(task_id=key_to_task[pt.key].id, depends_on_id=key_to_task[dep].id))
    prompt.is_draft = False
    project.plan_status = PlanStatus.PLANNED.value
    db.flush()
    log_event(db, "plan.created", f"Task plan generated with {len(planned)} tasks",
              project_id=project.id, data={"tasks": len(planned)})
    return list(key_to_task.values())
