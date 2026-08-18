"""Session state and persistence ports used by the application layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol


@dataclass(frozen=True)
class SessionMessage:
    role: str
    content: str
    request_id: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class SessionState:
    session_id: str
    tenant_id: str
    user_id: str | None
    messages: list[SessionMessage] = field(default_factory=list)
    version: int = 0


class SessionStore(Protocol):
    async def get_or_create(
        self, session_id: str, tenant_id: str, user_id: str | None
    ) -> SessionState: ...

    async def append(self, state: SessionState, message: SessionMessage) -> None: ...

    async def get(self, tenant_id: str, session_id: str) -> SessionState | None: ...


class CancellationRegistry(Protocol):
    async def cancel(self, request_id: str) -> None: ...

    async def is_cancelled(self, request_id: str) -> bool: ...

    async def clear(self, request_id: str) -> None: ...
