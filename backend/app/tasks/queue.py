"""Persistent task queue, dependency graph and state transitions (all in SQLite)."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Iterable

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from ..models import (
    AIAccount,
    Project,
    ProjectProviderAssignment,
    RunState,
    Task,
    TaskDependency,
    TaskStatus,
    utcnow,
)
from ..models.enums import PlanStatus
from ..services.activity import log_event
from ..services.events import bus

TERMINAL = {TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value}
EDITABLE_FIELDS = {"title", "description", "priority", "input_prompt", "expected_output",
                   "relevant_files", "category", "required_capability", "max_retries"}


class QueueError(ValueError):
    pass


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---- dependency graph ---------------------------------------------------------

def deps_of(db: Session, task_id: int) -> list[int]:
    return list(db.scalars(select(TaskDependency.depends_on_id).where(TaskDependency.task_id == task_id)))


def dependency_map(db: Session, project_id: int) -> dict[int, list[int]]:
    ids = list(db.scalars(select(Task.id).where(Task.project_id == project_id)))
    m: dict[int, list[int]] = {i: [] for i in ids}
    if ids:
        for d in db.scalars(select(TaskDependency).where(TaskDependency.task_id.in_(ids))):
            m[d.task_id].append(d.depends_on_id)
    return m


def would_cycle(graph: dict[int, list[int]], task_id: int, new_deps: Iterable[int]) -> bool:
    """True if making `task_id` depend on `new_deps` creates a cycle."""
    g = {k: list(v) for k, v in graph.items()}
    g[task_id] = list(new_deps)
    visiting: set[int] = set()
    done: set[int] = set()

    def dfs(n: int) -> bool:
        if n in done:
            return False
        if n in visiting:
            return True
        visiting.add(n)
        for m in g.get(n, []):
            if dfs(m):
                return True
        visiting.discard(n)
        done.add(n)
        return False

    return any(dfs(n) for n in list(g))


def set_dependencies(db: Session, task: Task, dep_ids: list[int]) -> None:
    dep_ids = sorted(set(dep_ids))
    if task.id in dep_ids:
        raise QueueError("a task cannot depend on itself")
    valid = set(db.scalars(select(Task.id).where(Task.project_id == task.project_id, Task.id.in_(dep_ids))))
    if valid != set(dep_ids):
        raise QueueError("dependencies must be tasks in the same project")
    if would_cycle(dependency_map(db, task.project_id), task.id, dep_ids):
        raise QueueError("those dependencies would create a cycle")
    for row in db.scalars(select(TaskDependency).where(TaskDependency.task_id == task.id)):
        db.delete(row)
    db.flush()
    for d in dep_ids:
        db.add(TaskDependency(task_id=task.id, depends_on_id=d))
    db.flush()
    refresh_ready(db, task.project_id)


def graph(db: Session, project_id: int) -> dict:
    tasks = list(db.scalars(select(Task).where(Task.project_id == project_id).order_by(Task.position, Task.id)))
    m = dependency_map(db, project_id)
    return {
        "nodes": [{"id": t.id, "key": t.key, "title": t.title, "status": t.status} for t in tasks],
        "edges": [{"from": d, "to": tid} for tid, ds in m.items() for d in ds],
    }


# ---- readiness -----------------------------------------------------------------

def dependencies_complete(db: Session, task: Task) -> bool:
    deps = deps_of(db, task.id)
    if not deps:
        return True
    done = db.scalar(select(Task.id).where(Task.id.in_(deps), Task.status != TaskStatus.COMPLETED.value))
    return done is None


def refresh_ready(db: Session, project_id: int) -> int:
    """Promote pending->ready when deps are complete; demote ready->pending otherwise."""
    changed = 0
    tasks = db.scalars(select(Task).where(Task.project_id == project_id,
                                          Task.status.in_([TaskStatus.PENDING.value, TaskStatus.READY.value])))
    for t in tasks:
        ok = dependencies_complete(db, t)
        if ok and t.status == TaskStatus.PENDING.value:
            t.status = TaskStatus.READY.value
            changed += 1
        elif not ok and t.status == TaskStatus.READY.value:
            t.status = TaskStatus.PENDING.value
            changed += 1
    db.flush()
    return changed


def publish_task(task: Task) -> None:
    bus.publish("task", task_id=task.id, project_id=task.project_id, status=task.status,
                run_state=task.run_state, progress=task.progress, title=task.title)


# ---- CRUD -----------------------------------------------------------------------

def create_task(db: Session, project: Project, title: str, description: str = "",
                depends_on: list[int] | None = None, **fields) -> Task:
    if not title.strip():
        raise QueueError("title is required")
    pos = (db.scalar(select(Task.position).where(Task.project_id == project.id)
                     .order_by(Task.position.desc()).limit(1)) or 0) + 1
    t = Task(project_id=project.id, title=title.strip(), description=description, position=pos,
             key=f"T{pos + 1}", status=TaskStatus.PENDING.value)
    for k, v in fields.items():
        if k in EDITABLE_FIELDS:
            setattr(t, k, v)
    db.add(t)
    db.flush()
    if not t.input_prompt:
        from .planner import compose_prompt

        master = next((p.body for p in project.prompts if not p.is_template), "")
        t.input_prompt = compose_prompt(project, master, t)
    if depends_on:
        set_dependencies(db, t, depends_on)
    refresh_ready(db, project.id)
    log_event(db, "task.created", f"Task added: {t.title}", project_id=project.id, task_id=t.id)
    return t


def update_task(db: Session, task: Task, **fields) -> Task:
    if task.status == TaskStatus.RUNNING.value:
        raise QueueError("cannot edit a running task; pause or cancel it first")
    deps = fields.pop("depends_on", None)
    for k, v in fields.items():
        if k in EDITABLE_FIELDS and v is not None:
            setattr(task, k, v)
    if deps is not None:
        set_dependencies(db, task, deps)
    db.flush()
    log_event(db, "task.updated", f"Task edited: {task.title}", project_id=task.project_id, task_id=task.id)
    publish_task(task)
    return task


def delete_task(db: Session, task: Task) -> None:
    if task.status == TaskStatus.RUNNING.value:
        raise QueueError("cannot delete a running task; cancel it first")
    pid, title, tid = task.project_id, task.title, task.id
    db.delete(task)
    db.flush()
    refresh_ready(db, pid)
    log_event(db, "task.deleted", f"Task deleted: {title}", project_id=pid, task_id=tid)


def reorder(db: Session, project_id: int, ordered_ids: list[int]) -> None:
    existing = set(db.scalars(select(Task.id).where(Task.project_id == project_id)))
    if set(ordered_ids) != existing:
        raise QueueError("ordered_ids must contain every task of the project exactly once")
    for pos, tid in enumerate(ordered_ids):
        db.execute(update(Task).where(Task.id == tid).values(position=pos))
    db.flush()


# ---- assignment ------------------------------------------------------------------

def project_accounts(db: Session, project_id: int) -> list[AIAccount]:
    ids = list(db.scalars(select(ProjectProviderAssignment.account_id)
                          .where(ProjectProviderAssignment.project_id == project_id)))
    if not ids:
        return []
    return list(db.scalars(select(AIAccount).where(AIAccount.id.in_(ids), AIAccount.enabled.is_(True))))


def assign_account(db: Session, task: Task, account_id: int | None) -> Task:
    if account_id is None:
        task.assigned_account_id = None
        db.flush()
        return task
    allowed = {a.id: a for a in project_accounts(db, task.project_id)}
    acct = allowed.get(account_id)
    if acct is None:
        raise QueueError("account is not enabled for this project")
    if task.required_capability and acct.capabilities and task.required_capability not in acct.capabilities:
        raise QueueError(f"account does not support the '{task.required_capability}' capability")
    task.assigned_account_id = account_id
    db.flush()
    log_event(db, "task.assigned", f"'{task.title}' assigned to {acct.display_name}",
              project_id=task.project_id, task_id=task.id)
    publish_task(task)
    return task


def auto_assign(db: Session, project: Project) -> int:
    """Initial, visible default assignment for unassigned tasks (never overrides user choices)."""
    accts = project_accounts(db, project.id)
    if not accts:
        return 0
    prefs: dict = (project.inventory or {}).get("_category_accounts", {})
    n = 0
    for t in db.scalars(select(Task).where(Task.project_id == project.id, Task.assigned_account_id.is_(None),
                                           Task.status.notin_(list(TERMINAL)))):
        pref = prefs.get(t.category)
        cand = next((a for a in accts if a.id == pref), None)
        if cand is None:
            cand = next((a for a in accts if not t.required_capability or t.required_capability in (a.capabilities or [])), None)
        if cand is not None:
            t.assigned_account_id = cand.id
            n += 1
    db.flush()
    return n


# ---- plan / engine control ----------------------------------------------------------

def approve_plan(db: Session, project: Project) -> None:
    if project.plan_status not in (PlanStatus.PLANNED.value, PlanStatus.APPROVED.value):
        raise QueueError("generate a task plan first")
    project.plan_status = PlanStatus.APPROVED.value
    auto_assign(db, project)
    refresh_ready(db, project.id)
    log_event(db, "plan.approved", "Task plan approved", project_id=project.id)


def start_project(db: Session, project: Project) -> None:
    if project.plan_status == PlanStatus.PLANNED.value and project.auto_approve_plan:
        approve_plan(db, project)
    if project.plan_status not in (PlanStatus.APPROVED.value, PlanStatus.PAUSED.value, PlanStatus.EXECUTING.value):
        raise QueueError("the task plan must be approved before execution can start")
    project.plan_status = PlanStatus.EXECUTING.value
    refresh_ready(db, project.id)
    log_event(db, "project.started", "Execution started", project_id=project.id)
    bus.publish("project", project_id=project.id, plan_status=project.plan_status)


def pause_project(db: Session, project: Project) -> None:
    if project.plan_status != PlanStatus.EXECUTING.value:
        raise QueueError("project is not executing")
    project.plan_status = PlanStatus.PAUSED.value
    log_event(db, "project.paused", "Execution paused", project_id=project.id)
    bus.publish("project", project_id=project.id, plan_status=project.plan_status)


def resume_project(db: Session, project: Project) -> None:
    if project.plan_status != PlanStatus.PAUSED.value:
        raise QueueError("project is not paused")
    project.plan_status = PlanStatus.EXECUTING.value
    log_event(db, "project.resumed", "Execution resumed", project_id=project.id)
    bus.publish("project", project_id=project.id, plan_status=project.plan_status)


def cancel_task(db: Session, task: Task) -> None:
    if task.status in TERMINAL:
        raise QueueError("task is already finished")
    task.status = TaskStatus.CANCELLED.value
    task.run_state = RunState.IDLE.value
    task.lock_token = None
    task.locked_at = None
    db.flush()
    log_event(db, "task.cancelled", f"Task cancelled: {task.title}", project_id=task.project_id, task_id=task.id)
    publish_task(task)


def pause_task(db: Session, task: Task) -> None:
    if task.status not in (TaskStatus.PENDING.value, TaskStatus.READY.value, TaskStatus.ASSIGNED.value):
        raise QueueError("only queued tasks can be paused")
    task.status = TaskStatus.PAUSED.value
    db.flush()
    publish_task(task)


def resume_task(db: Session, task: Task) -> None:
    if task.status != TaskStatus.PAUSED.value:
        raise QueueError("task is not paused")
    task.status = TaskStatus.PENDING.value
    db.flush()
    refresh_ready(db, task.project_id)
    publish_task(task)


def retry_task(db: Session, task: Task) -> None:
    if task.status not in (TaskStatus.FAILED.value, TaskStatus.AWAITING_USER.value, TaskStatus.CANCELLED.value,
                           TaskStatus.NEEDS_REVIEW.value, TaskStatus.PAUSED.value):
        raise QueueError(f"cannot retry a task that is {task.status}")
    task.status = TaskStatus.PENDING.value
    task.run_state = RunState.IDLE.value
    task.error = ""
    task.retry_count = 0
    task.next_attempt_at = None
    task.lock_token = None
    task.locked_at = None
    task.completed_at = None
    db.flush()
    refresh_ready(db, task.project_id)
    log_event(db, "task.retry", f"Retry requested: {task.title}", project_id=task.project_id, task_id=task.id)
    publish_task(task)


def approve_task(db: Session, task: Task) -> None:
    if task.status != TaskStatus.NEEDS_REVIEW.value:
        raise QueueError("task is not waiting for review")
    task.status = TaskStatus.COMPLETED.value
    task.approved = True
    task.completed_at = task.completed_at or utcnow()
    db.flush()
    refresh_ready(db, task.project_id)
    log_event(db, "task.approved", f"Task approved: {task.title}", project_id=task.project_id, task_id=task.id)
    publish_task(task)
    maybe_complete_project(db, task.project_id)


def maybe_complete_project(db: Session, project_id: int) -> bool:
    project = db.get(Project, project_id)
    if project is None:
        return False
    open_tasks = db.scalar(select(Task.id).where(
        Task.project_id == project_id,
        Task.status.notin_([TaskStatus.COMPLETED.value, TaskStatus.CANCELLED.value])).limit(1))
    has_tasks = db.scalar(select(Task.id).where(Task.project_id == project_id).limit(1)) is not None
    if has_tasks and open_tasks is None and project.plan_status != PlanStatus.COMPLETED.value:
        project.plan_status = PlanStatus.COMPLETED.value
        log_event(db, "project.completed", "All tasks completed", project_id=project_id)
        bus.publish("project", project_id=project_id, plan_status=project.plan_status)
        return True
    return False


# ---- claiming (duplicate-execution prevention) -----------------------------------------

def claim_next(db: Session) -> Task | None:
    """Atomically claim the next runnable task. Returns None if nothing is runnable.

    A task is runnable if: status ready/assigned, its project is executing, deps complete,
    and its backoff time (if any) has passed. The claim is a conditional UPDATE guarded by a
    NULL lock_token, so two workers can never claim the same task.
    """
    now = utcnow()
    q = (select(Task).join(Project, Project.id == Task.project_id)
         .where(Project.plan_status == PlanStatus.EXECUTING.value,
                Task.status.in_([TaskStatus.READY.value, TaskStatus.ASSIGNED.value]),
                Task.lock_token.is_(None))
         .order_by(Task.priority, Task.position, Task.id).limit(20))
    for cand in db.scalars(q):
        nxt = _aware(cand.next_attempt_at)
        if nxt is not None and nxt > now:
            continue
        if not dependencies_complete(db, cand):
            continue
        token = secrets.token_hex(16)
        res = db.execute(update(Task).where(and_(Task.id == cand.id, Task.lock_token.is_(None),
                                                 Task.status.in_([TaskStatus.READY.value, TaskStatus.ASSIGNED.value])))
                         .values(lock_token=token, locked_at=now, status=TaskStatus.RUNNING.value,
                                 run_state=RunState.PROCESSING.value))
        if res.rowcount == 1:
            db.flush()
            db.refresh(cand)
            return cand
    return None


def recover_interrupted(db: Session) -> list[Task]:
    """After a crash/restart: unlock tasks that were mid-run so they can be re-claimed.

    A task whose claim survived a restart can't still be running (workers live in this
    process), so it is returned to READY with a log entry. User-assisted tasks waiting on the
    user are untouched: their state is fully persisted.
    """
    recovered: list[Task] = []
    for t in db.scalars(select(Task).where(or_(Task.status == TaskStatus.RUNNING.value,
                                               Task.lock_token.isnot(None)))):
        if t.status == TaskStatus.RUNNING.value:
            t.status = TaskStatus.READY.value
        t.run_state = RunState.IDLE.value
        t.lock_token = None
        t.locked_at = None
        recovered.append(t)
        log_event(db, "task.recovered", f"Recovered interrupted task after restart: {t.title}",
                  level="warning", project_id=t.project_id, task_id=t.id)
    db.flush()
    return recovered


def backoff_delay(base: float, attempt: int, cap: float = 3600.0) -> timedelta:
    return timedelta(seconds=min(cap, base * (2 ** max(attempt - 1, 0))))
