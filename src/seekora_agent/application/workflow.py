"""Compiled LangGraph workflow for deterministic Fast and grounded Deep paths."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..domain.deep_path import (
    DeepPlan,
    ProbeSummary,
    RouteDecision,
    SufficiencyAssessment,
    TerminalDecision,
)
from ..domain.fast_path import ConstraintFilterResult, ResolvedIntent
from .constraints import ConstraintEngine
from .contracts import ExecutionBudget, RequestContext
from .deep_path import ComplexityRouter, GroundedPlanner, RetrievalProbe
from .intent import IntentResolver
from .recall import RecallOrchestrator, RecallResult
from .sufficiency import ResultSufficiencyEvaluator


class FastPathState(TypedDict, total=False):
    request_id: str
    query: str
    tenant_id: str
    user_id: str | None
    allowed_permission_tags: tuple[str, ...]
    top_k: int
    budget: ExecutionBudget
    intent: ResolvedIntent
    route_decision: RouteDecision
    probe_summary: ProbeSummary
    probe_result: RecallResult
    plan: DeepPlan
    recall_result: RecallResult
    filter_result: ConstraintFilterResult
    sufficiency: SufficiencyAssessment
    replan_count: int
    terminal_decision: TerminalDecision
    items: list[dict[str, Any]]


class LangChainFastPathWorkflow:
    """Compose routing, retrieval and deterministic validation as a StateGraph.

    保留类名是为了兼容现有调用方；该工作流现在同时承载 Fast/Deep 双路径。
    """
    def __init__(
        self,
        intent_resolver: IntentResolver,
        recall: RecallOrchestrator,
        constraint_engine: ConstraintEngine,
        router: ComplexityRouter | None = None,
        probe: RetrievalProbe | None = None,
        planner: GroundedPlanner | None = None,
        sufficiency: ResultSufficiencyEvaluator | None = None,
    ) -> None:
        self.intent_resolver = intent_resolver
        self.recall = recall
        self.constraint_engine = constraint_engine
        self.router = router or ComplexityRouter()
        self.probe = probe or RetrievalProbe(recall)
        self.planner = planner or GroundedPlanner()
        self.sufficiency = sufficiency or ResultSufficiencyEvaluator()
        builder = StateGraph(FastPathState)
        builder.add_node("resolve_intent", self._resolve_intent)
        builder.add_node("route", self._route)
        builder.add_node("probe", self._probe)
        builder.add_node("escalate_probe", self._escalate_probe)
        builder.add_node("plan", self._plan)
        builder.add_node("replan", self._replan)
        builder.add_node("recall", self._recall)
        builder.add_node("deep_recall", self._deep_recall)
        builder.add_node("apply_constraints", self._apply_constraints)
        builder.add_node("assess_sufficiency", self._assess_sufficiency)
        builder.add_node("compose_result", self._compose_result)
        builder.add_node("compose_terminal", self._compose_terminal)
        builder.add_edge(START, "resolve_intent")
        builder.add_edge("resolve_intent", "route")
        builder.add_conditional_edges(
            "route",
            lambda state: state["route_decision"].route,
            {"fast": "recall", "deep": "probe"},
        )
        builder.add_edge("probe", "plan")
        builder.add_edge("escalate_probe", "plan")
        builder.add_edge("plan", "deep_recall")
        builder.add_edge("replan", "deep_recall")
        builder.add_edge("recall", "apply_constraints")
        builder.add_edge("deep_recall", "apply_constraints")
        builder.add_edge("apply_constraints", "assess_sufficiency")
        builder.add_conditional_edges(
            "assess_sufficiency",
            self._next_after_sufficiency,
            {
                "result": "compose_result",
                "escalate": "escalate_probe",
                "replan": "replan",
                "terminal": "compose_terminal",
            },
        )
        builder.add_edge("compose_result", END)
        builder.add_edge("compose_terminal", END)
        self.graph = builder.compile(name="seekora_dual_path")

    # 从 state 中提取公共请求信息，构建 RequestContext 对象，供召回和约束引擎使用。
    @staticmethod
    def _context(state: FastPathState) -> RequestContext:
        return RequestContext(
            request_id=state["request_id"],
            tenant_id=state["tenant_id"],
            user_id=state.get("user_id"),
            allowed_permission_tags=state["allowed_permission_tags"],
        )

    async def _resolve_intent(self, state: FastPathState) -> dict[str, Any]:
        return {"intent": await self.intent_resolver.resolve(state["query"])}

    async def _route(self, state: FastPathState) -> dict[str, Any]:
        return {"route_decision": self.router.decide(state["intent"])}

    async def _probe(self, state: FastPathState) -> dict[str, Any]:
        summary, result = await self.probe.run(
            state["intent"].retrieval_query,
            self._context(state),
            state["budget"],
        )
        return {"probe_summary": summary, "probe_result": result}

    async def _escalate_probe(self, state: FastPathState) -> dict[str, Any]:
        summary, result = await self.probe.run(
            state["intent"].retrieval_query,
            self._context(state),
            state["budget"],
        )
        # Fast Path 结果不足时显式升级，Receipt 可以区分初始 Deep 与运行时升级。
        decision = RouteDecision("deep", ("fast_path_insufficient",))
        return {
            "route_decision": decision,
            "probe_summary": summary,
            "probe_result": result,
        }

    async def _plan(self, state: FastPathState) -> dict[str, Any]:
        return {"plan": self.planner.plan(state["intent"], state["probe_summary"])}

    async def _replan(self, state: FastPathState) -> dict[str, Any]:
        # 计划修订号和 replan_count 双重限制，保证循环最多执行一次。
        plan = self.planner.replan(state["intent"], state["plan"])
        return {"plan": plan, "replan_count": state.get("replan_count", 0) + 1}

    async def _recall(self, state: FastPathState) -> dict[str, Any]:
        result = await self.recall.recall(
            state["intent"].retrieval_query,
            state["top_k"],
            self._context(state),
            state["budget"],
        )
        return {"recall_result": result}

    async def _deep_recall(self, state: FastPathState) -> dict[str, Any]:
        result = await self.recall.recall_many(
            [step.query for step in state["plan"].steps],
            state["top_k"],
            self._context(state),
            state["budget"],
        )
        return {"recall_result": result}

    async def _apply_constraints(self, state: FastPathState) -> dict[str, Any]:
        result = await self.constraint_engine.apply(
            list(state["recall_result"].candidates),
            state["intent"],
            self._context(state),
        )
        return {"filter_result": result}

    async def _assess_sufficiency(self, state: FastPathState) -> dict[str, Any]:
        plan = state.get("plan")
        max_replans = plan.max_replans if plan else 1
        budget = state["budget"]
        # Replan 至少需要完整调用一轮召回源，因此要在进入循环前预留工具预算。
        has_tool_budget = (
            budget.tool_calls + len(self.recall.source_tools) <= budget.max_tool_calls
            and budget.remaining_ms > 0
        )
        can_replan = has_tool_budget and self.planner.can_replan(state["intent"], plan)
        assessment = self.sufficiency.assess(
            intent=state["intent"],
            filtered=state["filter_result"],
            replan_count=state.get("replan_count", 0),
            max_replans=max_replans,
            can_replan=can_replan,
        )
        return {"sufficiency": assessment}

    @staticmethod
    def _next_after_sufficiency(state: FastPathState) -> str:
        action = state["sufficiency"].action
        if action == "sufficient":
            return "result"
        if action == "replan":
            return "escalate" if state["route_decision"].route == "fast" else "replan"
        return "terminal"

    async def _compose_result(self, state: FastPathState) -> dict[str, Any]:
        candidates = state["filter_result"].accepted[:state["top_k"]]
        return {"items": [candidate.as_dict() for candidate in candidates]}

    async def _compose_terminal(self, state: FastPathState) -> dict[str, Any]:
        decision = self.sufficiency.terminal_decision(state["sufficiency"])
        return {"items": [], "terminal_decision": decision}

    async def astream(self, state: FastPathState) -> AsyncIterator[dict[str, Any]]:
        async for update in self.graph.astream(state, stream_mode="updates"):
            yield update
