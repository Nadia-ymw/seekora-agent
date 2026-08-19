"""Serializable execution contracts for bounded Deep Path DAGs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


DagNodeStatus = Literal["completed", "failed", "skipped"]


@dataclass(frozen=True)
class DagNodeExecution:
    """单个计划节点的公开执行记录，不包含私有推理过程。"""

    step_id: str
    query: str
    status: DagNodeStatus
    candidate_count: int = 0
    error_code: str | None = None
    skip_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "query": self.query,
            "status": self.status,
            "candidate_count": self.candidate_count,
            "error_code": self.error_code,
            "skip_reason": self.skip_reason,
        }


@dataclass(frozen=True)
class DagExecutionSummary:
    """一次 DAG 执行的停止原因、降级状态和节点记录。"""

    nodes: tuple[DagNodeExecution, ...]
    stop_reason: str
    degraded: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.as_dict() for node in self.nodes],
            "stop_reason": self.stop_reason,
            "degraded": self.degraded,
        }
