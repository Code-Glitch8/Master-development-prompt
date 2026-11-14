"""Notifications: stored in DB, pushed live, and shown as Windows toasts."""
from __future__ import annotations

import subprocess
import sys

from sqlalchemy.orm import Session

from ..models import Notification
from ..utils.logging_setup import get_logger
from .events import bus
from .settings_service import get_setting

log = get_logger("notifications")

# kind -> settings key
KIND_SETTING = {
    "task_completed": "notifications.task_completed",
    "project_completed": "notifications.project_completed",
    "provider_attention": "notifications.provider_attention",
    "user_action_required": "notifications.user_action_required",
    "recoverable_error": "notifications.recoverable_error",
    "service_stopped": "notifications.service_stopped",
    "export_completed": "notifications.export_completed",
}


def _windows_toast(title: str, body: str) -> bool:
    """Best-effort native toast via PowerShell. Returns True if attempted."""
    if sys.platform != "win32":
        return False
    esc = lambda s: s.replace("'", "''").replace("\n", " ")[:250]  # noqa: E731
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType = WindowsRuntime] | Out-Null;"
        "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02);"
        "$n=$t.GetElementsByTagName('text');"
        f"$n.Item(0).AppendChild($t.CreateTextNode('{esc(title)}')) | Out-Null;"
        f"$n.Item(1).AppendChild($t.CreateTextNode('{esc(body)}')) | Out-Null;"
        "$toast=[Windows.UI.Notifications.ToastNotification]::new($t);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Project Pilot')"
        ".Show($toast)"
    )
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception as exc:  # pragma: no cover - platform specific
        log.warning("toast failed: %s", exc)
        return False


def notify(
    db: Session,
    kind: str,
    title: str,
    body: str = "",
    project_id: int | None = None,
) -> Notification | None:
    """Create a notification if the user's preferences allow this kind."""
    if not get_setting(db, "notifications.enabled", True):
        return None
    key = KIND_SETTING.get(kind)
    if key and not get_setting(db, key, True):
        return None
    n = Notification(kind=kind, title=title, body=body, project_id=project_id)
    db.add(n)
    db.flush()
    if get_setting(db, "notifications.windows_toast", True):
        n.delivered = _windows_toast(title, body)
    bus.publish("notification", id=n.id, kind=kind, title=title, body=body, project_id=project_id)
    return n
