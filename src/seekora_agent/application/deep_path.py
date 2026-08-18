"""Deterministic routing, retrieval probing and grounded query planning."""

from __future__ import annotations

from ..domain.deep_path import DeepPlan, PlanStep, ProbeSummary, RouteDecision
from ..domain.fast_path import ResolvedIntent
from .contracts import ExecutionBudget, RequestContext
from .recall import RecallOrchestrator, RecallResult


class ComplexityRouter:
    """Routes only observable high-complexity signals to the costlier path."""

    def __init__(self, confidence_threshold: float = 0.75) -> None:
        self.confidence_threshold = confidence_threshold

    def decide(self, intent: ResolvedIntent) -> RouteDecision:
        reasons: list[str] = []
        if intent.mode in {"RESEARCH", "CLARIFY"}:
            reasons.append(f"mode:{intent.mode.lower()}")
        if intent.confidence < self.confidence_threshold:
            reasons.append("low_intent_confidence")
        if len(intent.ambiguities) >= 2:
            reasons.append("multiple_ambiguities")
        if len(intent.hard_constraints) >= 3:
            reasons.append("many_hard_constraints")
        return RouteDecision("deep" if reasons else "fast", tuple(reasons or ["simple_query"]))


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
        result = await self.recall.recall(query, self.probe_top_k, context, budget)
        per_source_ids = {
            call.tool: {str(item["item_id"]) for item in call.data.get("candidates", [])}
            for call in result.calls
            if call.status == "ok"
        }
        # Intersection measures source agreement without exposing or trusting tool text.
        overlap = set.intersection(*per_source_ids.values()) if per_source_ids else set()
        summary = ProbeSummary(
            candidate_count=len(result.candidates),
            source_candidate_counts={name: len(ids) for name, ids in per_source_ids.items()},
            overlapping_candidate_count=len(overlap),
            failed_sources=tuple(call.tool for call in result.calls if call.status != "ok"),
        )
        return summary, result


class GroundedPlanner:
    """Builds a small executable plan only from intent and probe observations."""

    def plan(self, intent: ResolvedIntent, probe: ProbeSummary) -> DeepPlan:
        steps = [PlanStep("query-1", intent.retrieval_query, "primary")]
        broadened = self._broadened_query(intent)
        # A second query is useful when the probe is sparse or the intent is ambiguous.
        if broadened and broadened != intent.retrieval_query and (
            probe.candidate_count < 5 or intent.ambiguities
        ):
            steps.append(PlanStep("query-2", broadened, "broaden"))
        return DeepPlan(tuple(steps))

    @staticmethod
    def _broadened_query(intent: ResolvedIntent) -> str:
        parts = [intent.domain or "", *intent.soft_preferences[:1]]
        return " ".join(part for part in parts if part).strip()
