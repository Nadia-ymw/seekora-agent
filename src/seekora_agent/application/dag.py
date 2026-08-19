"""Bounded DAG executor for grounded Deep Path retrieval plans."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..domain.dag import DagExecutionSummary, DagNodeExecution
from ..domain.deep_path import DeepPlan, PlanStep
from .contracts import BudgetExceeded, ExecutionBudget, RequestContext
from .recall import RecallCall, RecallOrchestrator, RecallResult, RecallUnavailable


@dataclass(frozen=True)
class DagExecutionResult:
    # 融合后的检索结果
    recall_result: RecallResult
    # 执行摘要（每个节点的执行状态、停止原因、是否降级）
    summary: DagExecutionSummary


class DeepPlanExecutor:
    """校验并执行有界查询 DAG，单节点失败时保留其他独立分支结果。"""

    def __init__(
        self,
        recall: RecallOrchestrator,
        max_nodes: int = 8,
        max_parallelism: int = 4,
    ) -> None:
        self.recall = recall
        self.max_nodes = max_nodes
        self.max_parallelism = max_parallelism

    def validate(self, plan: DeepPlan) -> None:
        if not plan.steps:
            raise ValueError("DAG plan must contain at least one node")
        if len(plan.steps) > self.max_nodes:
            raise ValueError(f"DAG plan exceeds maximum nodes: {self.max_nodes}")
        step_by_id = {step.step_id: step for step in plan.steps}
        if len(step_by_id) != len(plan.steps):
            raise ValueError("DAG plan contains duplicate step_id")
        for step in plan.steps:
            missing = sorted(set(step.depends_on) - step_by_id.keys())
            if missing:
                raise ValueError(
                    f"DAG node {step.step_id} has missing dependencies: {', '.join(missing)}"
                )

        # 三色深度优先检查依赖环，避免执行时出现无法推进的死锁。
        colors: dict[str, int] = {step_id: 0 for step_id in step_by_id}

        def visit(step_id: str) -> None:
            if colors[step_id] == 1:
                raise ValueError("DAG plan contains a dependency cycle")
            if colors[step_id] == 2:
                return
            colors[step_id] = 1
            for dependency in step_by_id[step_id].depends_on:
                visit(dependency)
            colors[step_id] = 2

        for step_id in step_by_id:
            visit(step_id)

    async def execute(
        self,
        plan: DeepPlan,
        top_k: int,
        context: RequestContext,
        budget: ExecutionBudget,
        candidate_target: int,
    ) -> DagExecutionResult:
        self.validate(plan)
        pending = {step.step_id: step for step in plan.steps}
        step_by_id = dict(pending)
        statuses: dict[str, str] = {}
        node_records: list[DagNodeExecution] = []
        successful_results: list[RecallResult] = []
        failed_calls: list[RecallCall] = []
        stop_reason = "completed"

        while pending:
            candidate_ids = {
                candidate.item_id
                for result in successful_results
                for candidate in result.candidates
            }
            if len(candidate_ids) >= candidate_target:
                stop_reason = "candidate_target_reached"
                self._skip_pending(pending, node_records, statuses, stop_reason)
                break
            if budget.remaining_ms <= 0:
                stop_reason = "deadline_exhausted"
                self._skip_pending(pending, node_records, statuses, stop_reason)
                break

            ready = [
                step for step in pending.values()
                if all(dependency in statuses for dependency in step.depends_on)
            ]
            if not ready:
                # validate() 已排除依赖环；这里是防御式保护，禁止无限循环。
                raise RuntimeError("DAG_EXECUTION_STALLED")

            executable: list[PlanStep] = []
            for step in ready:
                blocking_dependencies = [
                    dependency for dependency in step.depends_on
                    if statuses[dependency] != "completed" and step_by_id[dependency].required
                ]
                if blocking_dependencies:
                    pending.pop(step.step_id)
                    statuses[step.step_id] = "skipped"
                    node_records.append(DagNodeExecution(
                        step.step_id,
                        step.query,
                        "skipped",
                        skip_reason="dependency_failed",
                    ))
                else:
                    executable.append(step)

            if not executable:
                continue

            calls_per_node = len(self.recall.source_tools)
            tool_capacity = (budget.max_tool_calls - budget.tool_calls) // calls_per_node
            parallelism = min(plan.max_parallelism, self.max_parallelism, tool_capacity)
            if parallelism <= 0:
                stop_reason = "tool_budget_exhausted"
                self._skip_pending(pending, node_records, statuses, stop_reason)
                break

            # 只启动预算容纳的节点；每个节点内部的多路召回仍由 RecallOrchestrator 并行。
            batch = executable[:parallelism]
            outcomes = await asyncio.gather(*(
                self._execute_node(step, top_k, context, budget) for step in batch
            ))
            for step, result, calls, error_code in outcomes:
                pending.pop(step.step_id)
                if result is None:
                    statuses[step.step_id] = "failed"
                    failed_calls.extend(calls)
                    node_records.append(DagNodeExecution(
                        step.step_id,
                        step.query,
                        "failed",
                        error_code=error_code,
                    ))
                else:
                    statuses[step.step_id] = "completed"
                    successful_results.append(result)
                    node_records.append(DagNodeExecution(
                        step.step_id,
                        step.query,
                        "completed",
                        candidate_count=len(result.candidates),
                    ))

        fused = self.recall.fuse_results(successful_results)
        all_calls = (*fused.calls, *failed_calls)
        recall_result = RecallResult(fused.candidates, all_calls)
        degraded = any(
            node.status != "completed" for node in node_records
        ) or any(call.status != "ok" for call in all_calls)
        return DagExecutionResult(
            recall_result,
            DagExecutionSummary(tuple(node_records), stop_reason, degraded),
        )

    async def _execute_node(
        self,
        step: PlanStep,
        top_k: int,
        context: RequestContext,
        budget: ExecutionBudget,
    ) -> tuple[PlanStep, RecallResult | None, tuple[RecallCall, ...], str | None]:
        try:
            result = await self.recall.recall(step.query, top_k, context, budget)
            return step, result, result.calls, None
        except RecallUnavailable as exc:
            return step, None, exc.calls, "ALL_RECALL_SOURCES_FAILED"
        except BudgetExceeded:
            return step, None, (), "BUDGET_EXCEEDED"
        except Exception:
            return step, None, (), "DAG_NODE_EXECUTION_ERROR"

    @staticmethod
    def _skip_pending(
        pending: dict[str, PlanStep],
        records: list[DagNodeExecution],
        statuses: dict[str, str],
        reason: str,
    ) -> None:
        for step in pending.values():
            statuses[step.step_id] = "skipped"
            records.append(DagNodeExecution(
                step.step_id,
                step.query,
                "skipped",
                skip_reason=reason,
            ))
        pending.clear()
