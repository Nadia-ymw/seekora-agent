import asyncio
import unittest
from collections import Counter

from langchain.tools import ToolRuntime, tool

from seekora_agent.application.contracts import ExecutionBudget, RequestContext
from seekora_agent.application.dag import DeepPlanExecutor
from seekora_agent.application.recall import RecallOrchestrator
from seekora_agent.domain.deep_path import DeepPlan, PlanStep


def build_tracking_recall(failing_queries: set[str] | None = None):
    failing_queries = failing_queries or set()
    active_queries: Counter[str] = Counter()
    observation = {"max_parallel_queries": 0}

    def build_source(name: str):
        @tool(name, response_format="content_and_artifact")
        async def invoke(
            query: str, runtime: ToolRuntime[RequestContext], top_k: int = 10
        ) -> tuple[str, dict]:
            """Return a tracked DAG test candidate."""
            del top_k, runtime
            active_queries[query] += 1
            observation["max_parallel_queries"] = max(
                observation["max_parallel_queries"],
                sum(count > 0 for count in active_queries.values()),
            )
            await asyncio.sleep(0.01)
            active_queries[query] -= 1
            if query in failing_queries:
                output = {
                    "status": "error", "error_code": "SIMULATED_FAILURE", "data": {}
                }
                return "simulated failure", output
            output = {
                "status": "ok",
                "source_version": "dag-test-v1",
                "data": {"candidates": [{
                    "item_id": f"{query}-item",
                    "title": query,
                    "score": 1.0,
                }]},
            }
            return "one candidate", output

        return invoke

    tools = [build_source("source-a"), build_source("source-b")]
    return RecallOrchestrator(tools, source_tools=("source-a", "source-b")), observation


class DeepPlanValidationTest(unittest.TestCase):
    def setUp(self):
        recall, _ = build_tracking_recall()
        self.executor = DeepPlanExecutor(recall)

    def test_missing_dependency_is_rejected(self):
        plan = DeepPlan((PlanStep("child", "query", "primary", ("missing",)),))
        with self.assertRaisesRegex(ValueError, "missing dependencies"):
            self.executor.validate(plan)

    def test_dependency_cycle_is_rejected(self):
        plan = DeepPlan((
            PlanStep("one", "one", "primary", ("two",)),
            PlanStep("two", "two", "broaden", ("one",)),
        ))
        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            self.executor.validate(plan)


class DeepPlanExecutorTest(unittest.IsolatedAsyncioTestCase):
    context = RequestContext("request", "demo", None, ("public",))

    async def test_parallelism_is_bounded(self):
        recall, observation = build_tracking_recall()
        executor = DeepPlanExecutor(recall, max_parallelism=2)
        plan = DeepPlan(tuple(
            PlanStep(f"step-{index}", f"query-{index}", "primary")
            for index in range(3)
        ), max_parallelism=2)

        result = await executor.execute(
            plan, 2, self.context, ExecutionBudget(max_tool_calls=6), candidate_target=99
        )

        self.assertEqual(2, observation["max_parallel_queries"])
        self.assertEqual(3, len(result.recall_result.candidates))
        self.assertTrue(all(node.status == "completed" for node in result.summary.nodes))

    async def test_candidate_target_skips_dependent_node(self):
        recall, _ = build_tracking_recall()
        executor = DeepPlanExecutor(recall)
        plan = DeepPlan((
            PlanStep("primary", "primary", "primary"),
            PlanStep("detail", "detail", "broaden", ("primary",)),
        ))

        result = await executor.execute(
            plan, 2, self.context, ExecutionBudget(), candidate_target=1
        )

        self.assertEqual("candidate_target_reached", result.summary.stop_reason)
        self.assertEqual(["completed", "skipped"], [node.status for node in result.summary.nodes])
        self.assertEqual("candidate_target_reached", result.summary.nodes[1].skip_reason)

    async def test_failed_node_degrades_without_discarding_independent_result(self):
        recall, _ = build_tracking_recall({"bad"})
        executor = DeepPlanExecutor(recall)
        plan = DeepPlan((
            PlanStep("good", "good", "primary"),
            PlanStep("bad", "bad", "broaden"),
        ), max_parallelism=2)

        result = await executor.execute(
            plan, 2, self.context, ExecutionBudget(), candidate_target=99
        )

        self.assertTrue(result.summary.degraded)
        self.assertEqual(["good-item"], [item.item_id for item in result.recall_result.candidates])
        statuses = {node.step_id: node.status for node in result.summary.nodes}
        self.assertEqual({"good": "completed", "bad": "failed"}, statuses)
        self.assertEqual(4, len(result.recall_result.calls))


if __name__ == "__main__":
    unittest.main()
