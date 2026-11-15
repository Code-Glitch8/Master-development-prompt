from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import AIAccount, AIProvider, ProjectProviderAssignment, utcnow
from ..providers import ADAPTERS
from ..providers.registry import (
    CONNECTION_TYPES, delete_secret, load_secret, refresh_account, store_secret,
)
from ..services.activity import log_event
from . import serializers as ser
from .route import CommitRoute
from .deps import account_or_404

router = APIRouter(prefix="/api", tags=["accounts"], route_class=CommitRoute)


class AccountCreate(BaseModel):
    provider_key: str
    display_name: str = Field(min_length=1, max_length=120)
    connection_type: str = "web_manual"
    notes: str = ""
    model: str | None = None
    api_key: str | None = Field(None, repr=False)  # stored in the OS keyring, never in the DB
    api_key_env: str | None = None  # alternative: name of an environment variable holding the key
    capabilities: list[str] | None = None
    enabled: bool = True


class AccountPatch(BaseModel):
    display_name: str | None = Field(None, max_length=120)
    connection_type: str | None = None
    notes: str | None = None
    model: str | None = None
    api_key: str | None = Field(None, repr=False)
    api_key_env: str | None = None
    capabilities: list[str] | None = None
    enabled: bool | None = None
    unavailable_reason: str | None = None


def _apply(db: Session, a: AIAccount, data: dict[str, Any]) -> None:
    cfg = dict(a.config or {})
    if "connection_type" in data and data["connection_type"] is not None:
        if data["connection_type"] not in CONNECTION_TYPES:
            raise HTTPException(400, f"connection_type must be one of {sorted(CONNECTION_TYPES)}")
        if data["connection_type"] == "api_key" and a.provider.key == "other":
            raise HTTPException(400, "custom providers have no automated integration")
        a.connection_type = data["connection_type"]
    for k in ("display_name", "notes", "capabilities", "enabled", "unavailable_reason"):
        if data.get(k) is not None:
            setattr(a, k, data[k])
    if data.get("model") is not None:
        cfg["model"] = data["model"].strip()
    if data.get("api_key_env") is not None:
        env = data["api_key_env"].strip()
        if env and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", env):
            raise HTTPException(400, "invalid environment variable name")
        cfg["api_key_env"] = env
    key = data.get("api_key")
    if key:
        ref = a.credential_ref or f"account-{a.id}"
        if not store_secret(ref, key):
            raise HTTPException(
                400, "No OS keyring is available to store the key securely. Set an environment variable and "
                     "enter its name in 'api_key_env' instead. The key was NOT saved.")
        a.credential_ref = ref
    a.config = cfg
    refresh_account(a)


@router.get("/providers")
def providers(db: Session = Depends(get_db)):
    return [{"id": p.id, "key": p.key, "name": p.name, "website_url": p.website_url,
             "capabilities": p.capabilities, "supported_modes": p.supported_modes, "notes": p.notes}
            for p in db.scalars(select(AIProvider).order_by(AIProvider.id))]


@router.get("/connection-types")
def connection_types():
    return CONNECTION_TYPES


@router.get("/accounts")
def list_accounts(db: Session = Depends(get_db)):
    return [ser.account_dict(db, a) for a in db.scalars(select(AIAccount).order_by(AIAccount.id))]


@router.post("/accounts", status_code=201)
def create_account(body: AccountCreate, db: Session = Depends(get_db)):
    prov = db.scalar(select(AIProvider).where(AIProvider.key == body.provider_key))
    if prov is None:
        raise HTTPException(400, "unknown provider")
    a = AIAccount(provider_id=prov.id, display_name=body.display_name.strip(), notes=body.notes,
                  connection_type="web_manual", enabled=body.enabled, config={})
    db.add(a)
    db.flush()
    db.refresh(a)
    _apply(db, a, body.model_dump(exclude={"provider_key", "display_name", "notes", "enabled"}))
    if body.capabilities is None:
        a.capabilities = list(prov.capabilities or [])
    db.flush()
    log_event(db, "account.created", f"Account added: {a.display_name}")
    return ser.account_dict(db, a)


@router.get("/accounts/{account_id}")
def get_account(a: AIAccount = Depends(account_or_404), db: Session = Depends(get_db)):
    return ser.account_dict(db, a)


@router.patch("/accounts/{account_id}")
def patch_account(body: AccountPatch, a: AIAccount = Depends(account_or_404), db: Session = Depends(get_db)):
    _apply(db, a, body.model_dump(exclude_unset=True))
    db.flush()
    return ser.account_dict(db, a)


@router.delete("/accounts/{account_id}", status_code=204)
def delete_account(a: AIAccount = Depends(account_or_404), db: Session = Depends(get_db)):
    if a.credential_ref:
        delete_secret(a.credential_ref)
    for row in db.scalars(select(ProjectProviderAssignment).where(ProjectProviderAssignment.account_id == a.id)):
        db.delete(row)
    name = a.display_name
    db.delete(a)
    db.flush()
    log_event(db, "account.deleted", f"Account removed: {name}")


@router.post("/accounts/{account_id}/verify")
def verify(a: AIAccount = Depends(account_or_404), db: Session = Depends(get_db)):
    """Local configuration check only: never logs in, never contacts consumer websites."""
    adapter = ADAPTERS.get(a.provider.key) or ADAPTERS["other"]
    if a.connection_type == "api_key":
        ok, msg = adapter.verify(a.config or {}, load_secret(a))
    else:
        ok, msg = True, "Nothing to verify: this account is used through the provider's official website."
    a.connection_status = "configured" if ok else "incomplete"
    a.last_verified_at = utcnow()
    refresh_account(a)
    db.flush()
    return {"ok": ok, "message": msg, "account": ser.account_dict(db, a)}
