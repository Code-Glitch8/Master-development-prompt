"""Optional official-API adapters (user supplies their own API key and model).

These use only the providers' documented public HTTPS APIs with the user's own
key. They are OPTIONAL; the application is fully usable without them via the
user-assisted workflow. The model name is always supplied by the user so the
app never hard-codes stale model identifiers.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .base import ExecutionResult, ProviderAdapter, ProviderInfo, ProviderUnavailable

_API_CAPS = ("analysis", "code", "documentation", "review", "testing", "planning")
_MODES = ("automated", "user_assisted", "manual_only")


def _post_json(url: str, headers: dict[str, str], body: dict[str, Any], timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https only)
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:  # pragma: no cover
            pass
        if exc.code == 429:
            # Usage/rate limit: surfaced, never circumvented. Retryable only for short throttling.
            raise ProviderUnavailable(f"rate limited by provider (HTTP 429). {detail}", retryable=True) from exc
        if exc.code in (500, 502, 503, 504, 529):
            raise ProviderUnavailable(f"provider error HTTP {exc.code}. {detail}", retryable=True) from exc
        if exc.code in (401, 403):
            raise ProviderUnavailable(f"authentication rejected (HTTP {exc.code}). Check your API key.", False) from exc
        raise ProviderUnavailable(f"request failed HTTP {exc.code}. {detail}", False) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderUnavailable(f"network error: {exc}", retryable=True) from exc


def _require(config: dict[str, Any], secret: str | None) -> tuple[str, str]:
    model = (config.get("model") or "").strip()
    if not model:
        raise ProviderUnavailable("no model configured for this account", False)
    if not secret:
        raise ProviderUnavailable("no API key available for this account", False)
    return model, secret


class AnthropicAdapter(ProviderAdapter):
    info = ProviderInfo(
        key="claude", name="Claude", website_url="https://claude.ai",
        capabilities=_API_CAPS, supported_modes=_MODES,
        notes="Web account = user-assisted. Optional automated mode uses the official Anthropic API with your own key.",
    )
    url = "https://api.anthropic.com/v1/messages"

    def verify(self, account_config, secret):
        if not (account_config.get("model") or "").strip():
            return False, "model not set"
        if not secret:
            return False, "API key not found"
        return True, "API key and model configured (not contacted; run a task to test live)"

    def execute(self, prompt, account_config, secret, timeout=120):
        model, key = _require(account_config, secret)
        data = _post_json(
            self.url,
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            {"model": model, "max_tokens": int(account_config.get("max_tokens", 8192)),
             "messages": [{"role": "user", "content": prompt}]},
            timeout,
        )
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return ExecutionResult(text=text, raw=data, usage=data.get("usage", {}))


class OpenAIAdapter(ProviderAdapter):
    info = ProviderInfo(
        key="openai", name="ChatGPT / OpenAI", website_url="https://chatgpt.com",
        capabilities=_API_CAPS, supported_modes=_MODES,
        notes="Web account = user-assisted. Optional automated mode uses the official OpenAI API with your own key.",
    )
    url = "https://api.openai.com/v1/chat/completions"

    def verify(self, account_config, secret):
        if not (account_config.get("model") or "").strip():
            return False, "model not set"
        if not secret:
            return False, "API key not found"
        return True, "API key and model configured (not contacted; run a task to test live)"

    def execute(self, prompt, account_config, secret, timeout=120):
        model, key = _require(account_config, secret)
        data = _post_json(
            self.url, {"Authorization": f"Bearer {key}"},
            {"model": model, "messages": [{"role": "user", "content": prompt}]}, timeout,
        )
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailable(f"unexpected response shape: {exc}", False) from exc
        return ExecutionResult(text=text, raw=data, usage=data.get("usage", {}))


class GeminiAdapter(ProviderAdapter):
    info = ProviderInfo(
        key="gemini", name="Google Gemini", website_url="https://gemini.google.com",
        capabilities=_API_CAPS, supported_modes=_MODES,
        notes="Web account = user-assisted. Optional automated mode uses the official Gemini API with your own key.",
    )
    base = "https://generativelanguage.googleapis.com/v1beta/models"

    def verify(self, account_config, secret):
        if not (account_config.get("model") or "").strip():
            return False, "model not set"
        if not secret:
            return False, "API key not found"
        return True, "API key and model configured (not contacted; run a task to test live)"

    def execute(self, prompt, account_config, secret, timeout=120):
        model, key = _require(account_config, secret)
        data = _post_json(
            f"{self.base}/{model}:generateContent",
            {"x-goog-api-key": key},
            {"contents": [{"parts": [{"text": prompt}]}]}, timeout,
        )
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderUnavailable(f"unexpected response shape: {exc}", False) from exc
        return ExecutionResult(text=text, raw=data, usage=data.get("usageMetadata", {}))


class ManualAdapter(ProviderAdapter):
    """Catch-all for any other tool/provider: always user-assisted/manual."""

    info = ProviderInfo(
        key="other", name="Other / Custom", website_url="",
        capabilities=_API_CAPS, supported_modes=("user_assisted", "manual_only"),
        notes="No automation. The app prepares prompts and records what you paste back.",
    )

    def verify(self, account_config, secret):
        return True, "manual provider: nothing to verify"

    def execute(self, prompt, account_config, secret, timeout=120):
        raise ProviderUnavailable("manual provider cannot execute automatically", False)
