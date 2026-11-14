"""Key/value application settings persisted in SQLite."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import ApplicationSetting

DEFAULTS: dict[str, Any] = {
    "theme": "dark",
    "max_retries": 3,
    "backoff_base_seconds": 2.0,
    "notifications.enabled": True,
    "notifications.windows_toast": True,
    "notifications.task_completed": True,
    "notifications.project_completed": True,
    "notifications.provider_attention": True,
    "notifications.user_action_required": True,
    "notifications.recoverable_error": True,
    "notifications.service_stopped": True,
    "notifications.export_completed": True,
    "worker.auto_start": True,
    "backup.keep": 10,
}


def get_setting(db: Session, key: str, default: Any = None) -> Any:
    row = db.get(ApplicationSetting, key)
    if row is not None:
        return row.value
    if key in DEFAULTS:
        return DEFAULTS[key]
    return default


def set_setting(db: Session, key: str, value: Any) -> None:
    row = db.get(ApplicationSetting, key)
    if row is None:
        db.add(ApplicationSetting(key=key, value=value))
    else:
        row.value = value
    db.flush()


def all_settings(db: Session) -> dict[str, Any]:
    out = dict(DEFAULTS)
    for row in db.query(ApplicationSetting).all():
        out[row.key] = row.value
    return out
