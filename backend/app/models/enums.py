"""String enums shared by models, services and API."""
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover
        return self.value


class TaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    ASSIGNED = "assigned"
    AWAITING_USER = "awaiting_user"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    CANCELLED = "cancelled"


class ExecutionMode(StrEnum):
    """What a provider account can honestly do."""

    AUTOMATED = "automated"  # authorized API/CLI integration
    USER_ASSISTED = "user_assisted"  # app prepares prompt, user runs it, pastes result
    MANUAL_ONLY = "manual_only"  # nothing automatic; record keeping only


class RunState(StrEnum):
    """What the background engine is really doing for a task."""

    IDLE = "idle"
    PROCESSING = "processing"  # local work happening right now
    WAITING_AI = "waiting_ai"  # authorized automated call in flight
    WAITING_USER = "waiting_user"  # needs human action
    WAITING_PROVIDER = "waiting_provider"  # provider unavailable
    COMPLETED = "completed"


class PlanStatus(StrEnum):
    DRAFT = "draft"
    PLANNED = "planned"  # tasks generated, awaiting approval
    APPROVED = "approved"
    EXECUTING = "executing"
    PAUSED = "paused"
    COMPLETED = "completed"


class MessageKind(StrEnum):
    USER_PROMPT = "user_prompt"
    AI_RESPONSE = "ai_response"
    SYSTEM_EVENT = "system_event"
    FILE_ATTACHMENT = "file_attachment"
    ERROR = "error"
    CHECKPOINT = "checkpoint"


class ChangeType(StrEnum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    UNCHANGED = "unchanged"


class ChangeDecision(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ServiceState(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
