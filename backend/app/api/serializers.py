"""Plain-dict serializers (keeps API payloads explicit and free of secrets)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    AIAccount, Project, ProjectProviderAssignment, Task, TaskStatus,
)
from ..providers.registry import describe_mode, load_secret
from ..tasks.queue import deps_of


def iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def project_dict(db: Session, p: Project, detail: bool = False) -> dict[str, Any]:
    counts = dict(db.execute(select(Task.status, func.count()).where(Task.project_id == p.id)
                             .group_by(Task.status)).all())
    total = sum(counts.values())
    done = counts.get(TaskStatus.COMPLETED.value, 0)
    d = {
        "id": p.id, "name": p.name, "slug": p.slug, "directory": p.directory, "description": p.description,
        "source_type": p.source_type, "plan_status": p.plan_status, "auto_approve_plan": p.auto_approve_plan,
        "instructions": p.instructions, "created_at": iso(p.created_at), "updated_at": iso(p.updated_at),
        "task_counts": counts, "task_total": total, "task_done": done,
        "progress": (done / total) if total else 0.0,
        "inventory": p.inventory, "import_warnings": p.import_warnings,
    }
    if detail:
        d["account_ids"] = list(db.scalars(select(ProjectProviderAssignment.account_id)
                                           .where(ProjectProviderAssignment.project_id == p.id)))
    return d


def task_dict(db: Session, t: Task, detail: bool = False) -> dict[str, Any]:
    d = {
        "id": t.id, "project_id": t.project_id, "key": t.key, "title": t.title, "description": t.description,
        "category": t.category, "priority": t.priority, "position": t.position, "status": t.status,
        "run_state": t.run_state, "assigned_account_id": t.assigned_account_id,
        "required_capability": t.required_capability, "relevant_files": t.relevant_files,
        "expected_output": t.expected_output, "error": t.error, "progress": t.progress,
        "retry_count": t.retry_count, "max_retries": t.max_retries, "depends_on": deps_of(db, t.id),
        "next_attempt_at": iso(t.next_attempt_at), "approved": t.approved,
        "created_at": iso(t.created_at), "completed_at": iso(t.completed_at),
    }
    if detail:
        d["input_prompt"] = t.input_prompt
        d["actual_output"] = t.actual_output
        d["executions"] = [{"id": e.id, "attempt": e.attempt, "mode": e.mode, "outcome": e.outcome,
                            "error": e.error, "started_at": iso(e.started_at), "finished_at": iso(e.finished_at),
                            "account_id": e.account_id} for e in t.executions]
    return d


def account_dict(db: Session, a: AIAccount) -> dict[str, Any]:
    cfg = dict(a.config or {})
    has_secret = bool(load_secret(a)) if a.connection_type == "api_key" else False
    projects = list(db.scalars(select(ProjectProviderAssignment.project_id)
                               .where(ProjectProviderAssignment.account_id == a.id)))
    return {
        "id": a.id, "provider_key": a.provider.key, "provider_name": a.provider.name,
        "provider_url": a.provider.website_url, "display_name": a.display_name,
        "connection_type": a.connection_type, "connection_status": a.connection_status,
        "execution_mode": a.execution_mode, "execution_mode_label": describe_mode(a.execution_mode),
        "permitted_methods": a.permitted_methods, "capabilities": a.capabilities, "enabled": a.enabled,
        "notes": a.notes, "last_verified_at": iso(a.last_verified_at),
        "unavailable_reason": a.unavailable_reason, "assigned_project_ids": projects,
        "config": {k: v for k, v in cfg.items() if k not in ("api_key",)},
        "has_credential": has_secret, "created_at": iso(a.created_at),
    }
