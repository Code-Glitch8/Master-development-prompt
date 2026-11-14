"""Provider registry, DB seeding and execution-mode resolution."""
from __future__ import annotations

import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AIAccount, AIProvider, ExecutionMode
from .base import ProviderAdapter
from .http_adapters import AnthropicAdapter, GeminiAdapter, ManualAdapter, OpenAIAdapter

ADAPTERS: dict[str, ProviderAdapter] = {
    a.info.key: a for a in (AnthropicAdapter(), OpenAIAdapter(), GeminiAdapter(), ManualAdapter())
}

CONNECTION_TYPES = {
    "web_manual": "Official website, you run the prompt and paste the result (user-assisted)",
    "manual_only": "Record keeping only; nothing is prepared or sent for you",
    "api_key": "Official API with your own key (automated; optional)",
}


def seed_providers(db: Session) -> None:
    existing = {p.key: p for p in db.scalars(select(AIProvider))}
    for key, ad in ADAPTERS.items():
        info = ad.info
        row = existing.get(key)
        if row is None:
            db.add(
                AIProvider(
                    key=key, name=info.name, website_url=info.website_url,
                    capabilities=list(info.capabilities),
                    supported_modes=list(info.supported_modes), notes=info.notes,
                )
            )
        else:
            row.name, row.website_url = info.name, info.website_url
            row.capabilities = list(info.capabilities)
            row.supported_modes = list(info.supported_modes)
            row.notes = info.notes
    db.flush()


# ---- credentials -----------------------------------------------------------

def _keyring():
    try:
        import keyring  # type: ignore

        return keyring
    except Exception:
        return None


def store_secret(ref: str, secret: str) -> bool:
    """Store a secret in the OS keyring. Returns False if keyring unavailable."""
    kr = _keyring()
    if kr is None:
        return False
    try:
        kr.set_password("project-pilot", ref, secret)
        return True
    except Exception:
        return False


def delete_secret(ref: str) -> None:
    kr = _keyring()
    if kr is None:
        return
    try:
        kr.delete_password("project-pilot", ref)
    except Exception:
        pass


def load_secret(account: AIAccount) -> str | None:
    """Resolve an account's API key: env var named in config, else OS keyring."""
    cfg = account.config or {}
    env_name = cfg.get("api_key_env")
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    if account.credential_ref:
        kr = _keyring()
        if kr is not None:
            try:
                return kr.get_password("project-pilot", account.credential_ref)
            except Exception:
                return None
    return None


# ---- capability / mode resolution --------------------------------------------

def resolve_mode(account: AIAccount) -> str:
    """Honest execution mode: AUTOMATED only if a usable authorized path exists."""
    key = account.provider.key
    if account.connection_type == "api_key" and key in ("claude", "openai", "gemini"):
        cfg: dict[str, Any] = account.config or {}
        if (cfg.get("model") or "").strip() and load_secret(account):
            return ExecutionMode.AUTOMATED.value
        return ExecutionMode.USER_ASSISTED.value  # configured for API but incomplete
    if account.connection_type == "manual_only":
        return ExecutionMode.MANUAL_ONLY.value
    return ExecutionMode.USER_ASSISTED.value


def describe_mode(mode: str) -> str:
    return {
        "automated": "Authorized automated execution",
        "user_assisted": "User-assisted execution",
        "manual_only": "Manual-only interaction",
    }.get(mode, mode)


def refresh_account(account: AIAccount) -> None:
    mode = resolve_mode(account)
    account.execution_mode = mode
    account.permitted_methods = [mode]
    if not account.capabilities:
        account.capabilities = list(account.provider.capabilities or [])
