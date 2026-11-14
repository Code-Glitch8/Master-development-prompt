"""Provider adapter interface.

Every adapter declares what it can *really* do. The engine only calls
`execute()` when the account's execution mode is AUTOMATED; otherwise the task
goes to the user-assisted flow. Nothing here scrapes websites, reuses browser
sessions or touches cookies.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderUnavailable(Exception):
    """Provider can't serve the request right now (rate limit, outage, auth).

    `retryable` indicates a transient technical failure that backoff can fix.
    Quota/usage limits are never worked around: they surface to the user.
    """

    def __init__(self, reason: str, retryable: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


@dataclass
class ExecutionResult:
    text: str
    raw: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderInfo:
    key: str
    name: str
    website_url: str
    capabilities: tuple[str, ...]
    # Modes this provider category can offer at all.
    supported_modes: tuple[str, ...]
    notes: str = ""


class ProviderAdapter(ABC):
    info: ProviderInfo

    @abstractmethod
    def verify(self, account_config: dict[str, Any], secret: str | None) -> tuple[bool, str]:
        """Check the connection. Returns (ok, message). Must not mutate anything."""

    @abstractmethod
    def execute(
        self, prompt: str, account_config: dict[str, Any], secret: str | None, timeout: float = 120
    ) -> ExecutionResult:
        """Run a prompt through an authorized integration. Raises ProviderUnavailable."""
