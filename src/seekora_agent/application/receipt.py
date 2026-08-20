"""Replayable receipt models and persistence port."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ToolCallReceipt:
    tool: str
    arguments: dict[str, Any]
    status: str
    latency_ms: int
    source_version: str
    error_code: str | None = None


@dataclass
class RecommendationReceipt:
    request_id: str
    session_id: str
    tenant_id: str
    query: str
    status: str = "running"
    route: str = "fast"
    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    tool_calls: list[ToolCallReceipt] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)
    exposure_id: str | None = None
    resolved_intent: dict[str, Any] = field(default_factory=dict)
    session_context: dict[str, Any] = field(default_factory=dict)
    route_decision: dict[str, Any] = field(default_factory=dict)
    probe_summary: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    sufficiency_assessments: list[dict[str, Any]] = field(default_factory=list)
    replan_count: int = 0
    terminal_decision: dict[str, Any] = field(default_factory=dict)
    dag_executions: list[dict[str, Any]] = field(default_factory=list)
    filtered_reason_counts: dict[str, int] = field(default_factory=dict)
    error_code: str | None = None
    config_versions: dict[str, str] = field(default_factory=lambda: {
        "agent": "0.17.0",
        "workflow": "langgraph-dual-path-v6",
        "prompt": "none",
        "tool_policy": "langchain-toolnode-v2",
        "ranker": "rrf-v1",
    })

    def finish(self, status: str, error_code: str | None = None) -> None:
        self.status = status
        self.error_code = error_code
        self.finished_at = utc_now()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ReceiptStore(Protocol):
    async def put(self, receipt: RecommendationReceipt) -> None: ...

    async def get(self, request_id: str) -> RecommendationReceipt | None: ...
