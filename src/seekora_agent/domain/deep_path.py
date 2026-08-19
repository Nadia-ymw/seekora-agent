"""Stable domain contracts for routing and grounded Deep Path planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# 定义了路由名称只能是 "fast" 或 "deep" 两种字面量
RouteName = Literal["fast", "deep"]

# 深度途径 deep → probe → plan → deep_recall
# 记录路由选择的结果，包含可审计的决策理由。
@dataclass(frozen=True)
class RouteDecision:
    """Auditable routing result; reasons are safe to expose and persist."""

    route: RouteName
    reasons: tuple[str, ...]
    # 方法用于序列化，将元组转换为列表以便 JSON 序列化
    def as_dict(self) -> dict[str, Any]:
        return {"route": self.route, "reasons": list(self.reasons)}

# 记录检索/探测阶段的小型观察结果，仅供规划器内部使用，不直接返回给用户
@dataclass(frozen=True)
class ProbeSummary:
    """Small retrieval observation used by the planner, not a user result."""
    # 总候选数量
    candidate_count: int
    # 各数据源的候选数量（字典）
    source_candidate_counts: dict[str, int]
    # 重叠候选数量（不同源返回的相同结果）
    overlapping_candidate_count: int
    # 失败的源列表
    failed_sources: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_count": self.candidate_count,
            "source_candidate_counts": self.source_candidate_counts,
            "overlapping_candidate_count": self.overlapping_candidate_count,
            "failed_sources": list(self.failed_sources),
        }

# 规划步骤
@dataclass(frozen=True)
class PlanStep:
    step_id: str
    query: str
    purpose: Literal["primary", "broaden"]   # 步骤目的，只能是 "primary"（主要查询）或 "broaden"（扩大范围）
    # depends_on 声明前置节点；空元组表示该节点可以立即并行执行。
    depends_on: tuple[str, ...] = ()
    # required 节点失败会阻断依赖它的后续节点，但不影响其他独立分支。
    required: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "query": self.query,
            "purpose": self.purpose,
            "depends_on": list(self.depends_on),
            "required": self.required,
        }

# 完整的、有界的、可序列化的规划方案。
# 通过 max_parallelism 和 max_replans 控制资源消耗
@dataclass(frozen=True)
class DeepPlan:
    """Bounded, serializable plan; it intentionally contains no hidden reasoning."""
    # 规划步骤的元组
    steps: tuple[PlanStep, ...]
    # 最大并行度（默认2），控制同时执行的步骤数
    max_parallelism: int = 2
    # 最大重规划次数（默认1），允许有限的重规划
    max_replans: int = 1
    # 计划修订号：0 表示初始计划，1 表示唯一一次 Replan。
    revision: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": [step.as_dict() for step in self.steps],
            "max_parallelism": self.max_parallelism,
            "max_replans": self.max_replans,
            "revision": self.revision,
        }


# 充分性判断只输出可审计结论，不保存模型的私有思维链。
SufficiencyAction = Literal["sufficient", "replan", "clarify", "refuse"]


@dataclass(frozen=True)
class SufficiencyAssessment:
    """记录候选是否足以回答，以及下一步允许执行的动作。"""

    action: SufficiencyAction
    reason: str
    accepted_count: int
    supported_candidate_count: int
    replan_count: int
    questions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "accepted_count": self.accepted_count,
            "supported_candidate_count": self.supported_candidate_count,
            "replan_count": self.replan_count,
            "questions": list(self.questions),
        }


@dataclass(frozen=True)
class TerminalDecision:
    """Deep Path 无法继续检索时返回的澄清或拒答决定。"""

    action: Literal["clarify", "refuse"]
    reason: str
    message: str
    questions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason,
            "message": self.message,
            "questions": list(self.questions),
        }
