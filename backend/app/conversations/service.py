"""Per-task conversations: stored in SQLite and mirrored to JSONL files for durability."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import AIAccount, Conversation, ConversationMessage, MessageKind, Task
from ..services.events import bus
from ..utils.logging_setup import get_logger

log = get_logger("conversations")


def get_or_create(db: Session, task: Task) -> Conversation:
    conv = db.scalar(select(Conversation).where(Conversation.task_id == task.id))
    if conv is None:
        conv = Conversation(project_id=task.project_id, task_id=task.id, title=task.title)
        db.add(conv)
        db.flush()
    return conv


def _mirror(conv: Conversation, msg: ConversationMessage) -> None:
    """Append to data/conversations/<project_id>/task-<id>.jsonl (best effort)."""
    try:
        d = get_settings().conversations_dir / str(conv.project_id)
        d.mkdir(parents=True, exist_ok=True)
        rec = {"id": msg.id, "kind": msg.kind, "content": msg.content, "provider": msg.provider_name,
               "account": msg.account_name, "task_id": msg.task_id, "status": msg.execution_status,
               "meta": msg.meta, "at": msg.created_at.isoformat() if msg.created_at else None}
        with (d / f"task-{conv.task_id or 'general'}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:  # pragma: no cover
        log.warning("conversation mirror failed: %s", exc)


def add_message(db: Session, conv: Conversation, kind: MessageKind | str, content: str,
                account: AIAccount | None = None, execution_status: str = "",
                meta: dict[str, Any] | None = None) -> ConversationMessage:
    msg = ConversationMessage(
        conversation_id=conv.id, kind=str(getattr(kind, "value", kind)), content=content,
        provider_name=account.provider.name if account else "",
        account_id=account.id if account else None,
        account_name=account.display_name if account else "",
        task_id=conv.task_id, execution_status=execution_status, meta=meta or {})
    db.add(msg)
    db.flush()
    _mirror(conv, msg)
    bus.publish("message", conversation_id=conv.id, message_id=msg.id, task_id=conv.task_id,
                project_id=conv.project_id, kind=msg.kind)
    return msg


def list_conversations(db: Session, project_id: int | None = None) -> list[Conversation]:
    q = select(Conversation).order_by(Conversation.id.desc())
    if project_id is not None:
        q = q.where(Conversation.project_id == project_id)
    return list(db.scalars(q))


def search_messages(db: Session, query: str = "", project_id: int | None = None,
                    kind: str | None = None, task_id: int | None = None,
                    provider: str | None = None, limit: int = 200) -> list[ConversationMessage]:
    q = (select(ConversationMessage).join(Conversation, Conversation.id == ConversationMessage.conversation_id)
         .order_by(ConversationMessage.id.desc()).limit(limit))
    if project_id is not None:
        q = q.where(Conversation.project_id == project_id)
    if task_id is not None:
        q = q.where(ConversationMessage.task_id == task_id)
    if kind:
        q = q.where(ConversationMessage.kind == kind)
    if provider:
        q = q.where(ConversationMessage.provider_name.ilike(f"%{provider}%"))
    if query.strip():
        like = f"%{query.strip()}%"
        q = q.where(or_(ConversationMessage.content.ilike(like), ConversationMessage.account_name.ilike(like)))
    return list(db.scalars(q))


def message_dict(m: ConversationMessage) -> dict[str, Any]:
    return {"id": m.id, "conversation_id": m.conversation_id, "kind": m.kind, "content": m.content,
            "provider_name": m.provider_name, "account_id": m.account_id, "account_name": m.account_name,
            "task_id": m.task_id, "execution_status": m.execution_status, "meta": m.meta,
            "created_at": m.created_at.isoformat() if m.created_at else None}


def export_json(db: Session, conv: Conversation) -> dict[str, Any]:
    return {"conversation_id": conv.id, "project_id": conv.project_id, "task_id": conv.task_id,
            "title": conv.title, "messages": [message_dict(m) for m in conv.messages]}


def export_markdown(db: Session, conv: Conversation) -> str:
    labels = {"user_prompt": "Prompt", "ai_response": "AI response", "system_event": "System",
              "file_attachment": "Attachment", "error": "Error", "checkpoint": "Checkpoint"}
    out = [f"# {conv.title or 'Conversation'}", f"Task #{conv.task_id}  ·  Project #{conv.project_id}", ""]
    for m in conv.messages:
        who = f" ({m.provider_name} / {m.account_name})" if m.account_name else ""
        when = m.created_at.isoformat() if m.created_at else ""
        out.append(f"## {labels.get(m.kind, m.kind)}{who} — {when}")
        out.append("")
        out.append(m.content)
        out.append("")
    return "\n".join(out)
