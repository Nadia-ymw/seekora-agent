"""Framework-independent request, event and execution-budget contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic
from typing import Any


@dataclass(frozen=True)
class AgentQuery:
    query: str
    tenant_id: str
    session_id: str
    user_id: str | None = None
    client_request_id: str | None = None
    allowed_permission_tags: tuple[str, ...] = ("public",)
    top_k: int = 10

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be empty")
        if not self.tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        if not self.session_id.strip():
            raise ValueError("session_id must not be empty")
        if not 1 <= self.top_k <= 50:
            raise ValueError("top_k must be between 1 and 50")


@dataclass(frozen=True)
class AgentEvent:
    event: str
    request_id: str
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"event": self.event, "request_id": self.request_id, "data": self.data}


@dataclass(frozen=True)
class RequestContext:
    request_id: str
    tenant_id: str
    user_id: str | None
    allowed_permission_tags: tuple[str, ...]


@dataclass
class ExecutionBudget:
    # Deep Path uses one two-source probe plus at most two two-source plan queries.
    max_tool_calls: int = 8
    deadline_ms: int = 8_000
    started_at: float = field(default_factory=monotonic)
    tool_calls: int = 0

    @property
    def elapsed_ms(self) -> int:
        return int((monotonic() - self.started_at) * 1_000)

    @property
    def remaining_ms(self) -> int:
        return max(0, self.deadline_ms - self.elapsed_ms)
    # 检查调用时间预算
    def consume_tool_call(self) -> None:
        if self.remaining_ms <= 0:
            raise BudgetExceeded("request deadline exceeded")
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetExceeded("tool call budget exceeded")
        self.tool_calls += 1


class BudgetExceeded(RuntimeError):
    pass


class RequestCancelled(RuntimeError):
    pass
