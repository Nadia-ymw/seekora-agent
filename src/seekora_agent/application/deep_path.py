"""Deterministic routing, retrieval probing and grounded query planning."""

from __future__ import annotations

from ..domain.deep_path import DeepPlan, PlanStep, ProbeSummary, RouteDecision
from ..domain.fast_path import ResolvedIntent
from .contracts import ExecutionBudget, RequestContext
from .recall import RecallOrchestrator, RecallResult


class ComplexityRouter:
    """仅将高复杂度的信号导向成本更高的路径。"""

    def __init__(self, confidence_threshold: float = 0.75) -> None:
        self.confidence_threshold = confidence_threshold

    def decide(self, intent: ResolvedIntent) -> RouteDecision:
        # 触发深度路径的条件
        reasons: list[str] = []
        # 研究或澄清模式
        if intent.mode in {"RESEARCH", "CLARIFY"}:
            reasons.append(f"mode:{intent.mode.lower()}")
        # 意图置信度低于阈值
        if intent.confidence < self.confidence_threshold:
            reasons.append("low_intent_confidence")
        # 存在多个歧义
        if len(intent.ambiguities) >= 2:
            reasons.append("multiple_ambiguities")
        # 硬约束过多
        if len(intent.hard_constraints) >= 3:
            reasons.append("many_hard_constraints")
        return RouteDecision("deep" if reasons else "fast", tuple(reasons or ["simple_query"]))

# 执行浅层召回，仅收集规划所需的统计数据，不暴露或信任工具文本。
class RetrievalProbe:
    """Runs a shallow recall and keeps only the statistics needed for planning."""

    def __init__(self, recall: RecallOrchestrator, probe_top_k: int = 5) -> None:
        self.recall = recall
        self.probe_top_k = probe_top_k

    async def run(
        self,
        query: str,
        context: RequestContext,
        budget: ExecutionBudget,
    ) -> tuple[ProbeSummary, RecallResult]:
        # 执行召回
        result = await self.recall.recall(query, self.probe_top_k, context, budget)
        # 提取各数据源的候选ID
        per_source_ids = {
            call.tool: {str(item["item_id"]) for item in call.data.get("candidates", [])}
            for call in result.calls
            if call.status == "ok"
        }
        # 多个数据源返回相同候选项的数量
        # 重叠度是衡量源之间一致性的指标，不依赖文本相似度
        overlap = set.intersection(*per_source_ids.values()) if per_source_ids else set()
        # 总候选数、各源候选数、重叠候选数、失败源列表
        summary = ProbeSummary(
            candidate_count=len(result.candidates),
            source_candidate_counts={name: len(ids) for name, ids in per_source_ids.items()},
            overlapping_candidate_count=len(overlap),
            failed_sources=tuple(call.tool for call in result.calls if call.status != "ok"),
        )
        return summary, result

# 仅从意图和探测观察构建可执行的规划。
class GroundedPlanner:
    """Builds a small executable plan only from intent and probe observations."""

    def plan(self, intent: ResolvedIntent, probe: ProbeSummary) -> DeepPlan:
        steps = [PlanStep("query-1", intent.retrieval_query, "primary")]
        broadened = self._broadened_query(intent)
        # A second query is useful when the probe is sparse or the intent is ambiguous.
        if broadened and broadened != intent.retrieval_query and (
            probe.candidate_count < 5 or intent.ambiguities
        ):
            # 宽泛节点依赖主查询，并标记为可选；主查询候选充分时 DAG 会直接停止。
            steps.append(PlanStep(
                "query-2",
                broadened,
                "broaden",
                depends_on=("query-1",),
                required=False,
            ))
        return DeepPlan(tuple(steps), revision=0)

    def can_replan(self, intent: ResolvedIntent, plan: DeepPlan | None) -> bool:
        existing = {step.query for step in plan.steps} if plan else {intent.retrieval_query}
        return any(query not in existing for query in self._fallback_queries(intent))

    def replan(self, intent: ResolvedIntent, plan: DeepPlan) -> DeepPlan:
        """最多选择一个尚未执行的宽泛查询，避免无界循环。"""
        existing = {step.query for step in plan.steps}
        query = next(
            (candidate for candidate in self._fallback_queries(intent) if candidate not in existing),
            None,
        )
        if query is None:
            raise ValueError("no grounded fallback query available")
        return DeepPlan(
            steps=(PlanStep(f"replan-{plan.revision + 1}", query, "broaden"),),
            max_parallelism=1,
            max_replans=plan.max_replans,
            revision=plan.revision + 1,
        )

    # 宽泛化查询
    # 宽泛化查询不同于原始检索查询, 组合：领域 + 第一个软偏好
    # 例如：原始查询是"Python异步编程"，如果候选<5，宽泛化为"编程 Python"（假设domain="编程"）
    @staticmethod
    def _broadened_query(intent: ResolvedIntent) -> str:
        parts = [intent.domain or "", *intent.soft_preferences[:1]]
        return " ".join(part for part in parts if part).strip()

    def _fallback_queries(self, intent: ResolvedIntent) -> tuple[str, ...]:
        """回退查询只能来自已解析领域和偏好，不能凭空扩展新事实。"""
        candidates = (
            self._broadened_query(intent),
            intent.domain or "",
            intent.soft_preferences[0] if intent.soft_preferences else "",
        )
        return tuple(dict.fromkeys(query for query in candidates if query.strip()))
