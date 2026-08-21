"""Session state and persistence ports used by the application layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class SessionMessage:
    role: str
    content: str
    request_id: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class SessionIntentSnapshot:
    """当前会话任务的意图快照，只服务于短期上下文，不属于长期用户画像。"""

    request_id: str
    resolved_intent: dict[str, Any]
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class SessionState:
    session_id: str
    tenant_id: str
    user_id: str | None
    messages: list[SessionMessage] = field(default_factory=list)
    current_intent: SessionIntentSnapshot | None = None
    version: int = 0


class SessionStore(Protocol):
    async def get_or_create(
        self, session_id: str, tenant_id: str, user_id: str | None
    ) -> SessionState: ...

    async def append(self, state: SessionState, message: SessionMessage) -> None: ...

    async def set_intent(
        self, state: SessionState, snapshot: SessionIntentSnapshot
    ) -> None: ...

    async def get(self, tenant_id: str, session_id: str) -> SessionState | None: ...


class SessionVersionConflict(RuntimeError):
    """同一 Session 被并发更新，当前快照已过期。"""


class SessionIdentityConflict(PermissionError):
    """同一租户 Session 不允许在不同用户身份之间复用。"""


class CancellationRegistry(Protocol):
    async def cancel(self, request_id: str) -> None: ...

    async def is_cancelled(self, request_id: str) -> bool: ...

    async def clear(self, request_id: str) -> None: ...
