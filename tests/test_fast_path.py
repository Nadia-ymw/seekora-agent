import unittest

from langchain.tools import ToolRuntime, tool

from seekora_agent.application.contracts import AgentQuery, ExecutionBudget, RequestContext
from seekora_agent.application.recall import RecallOrchestrator
from seekora_agent.bootstrap import build_runtime
from seekora_agent.infrastructure.intent.rule_based import RuleBasedIntentResolver


class IntentResolverTest(unittest.IsolatedAsyncioTestCase):
    async def test_extracts_price_memory_and_domain(self):
        intent = await RuleBasedIntentResolver().resolve(
            "推荐8000元以内内存32GB以上的轻薄笔记本"
        )
        self.assertEqual("RECOMMEND", intent.mode)
        self.assertEqual("laptop", intent.domain)
        self.assertEqual(
            [("price", "lte", 8000.0), ("memory_gb", "gte", 32)],
            [(rule.field, rule.operator, rule.value) for rule in intent.hard_constraints],
        )
        self.assertNotIn("8000", intent.retrieval_query)


class FastPathIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_dual_recall_rrf_constraints_and_receipt(self):
        runtime = build_runtime()
        events = [event async for event in runtime.run(AgentQuery(
            query="推荐8000元以内适合编程的轻薄笔记本",
            tenant_id="demo",
            session_id="fast-path-test",
            allowed_permission_tags=("public",),
        ))]
        event_names = [event.event for event in events]
        self.assertIn("intent.resolved", event_names)
        self.assertIn("recall.completed", event_names)
        self.assertIn("constraints.applied", event_names)

        result_event = next(event for event in events if event.event == "result")
        items = result_event.data["items"]
        self.assertTrue(items)
        self.assertNotIn("lap-002", [item["item_id"] for item in items])
        self.assertTrue(all(item["constraint_pass"] for item in items))
        self.assertTrue(any(len(item["source_scores"]) == 2 for item in items))

        request_id = events[0].request_id
        receipt = await runtime.receipts.get(request_id)
        self.assertEqual(2, len(receipt.tool_calls))
        self.assertEqual("price", receipt.resolved_intent["hard_constraints"][0]["field"])
        self.assertIn("CONSTRAINT_PRICE", receipt.filtered_reason_counts)

    async def test_single_recall_source_failure_degrades_gracefully(self):
        @tool("good", response_format="content_and_artifact")
        async def good(
            query: str, runtime: ToolRuntime[RequestContext], top_k: int = 10
        ) -> tuple[str, dict]:
            """Return one deterministic candidate."""
            del query, top_k, runtime
            output = {"status": "ok", "source_version": "good-v1", "data": {
                "candidates": [{"item_id": "one", "title": "Item", "score": 1.0}]
            }}
            return "one candidate", output

        @tool("bad", response_format="content_and_artifact")
        async def bad(
            query: str, runtime: ToolRuntime[RequestContext], top_k: int = 10
        ) -> tuple[str, dict]:
            """Raise a simulated source timeout."""
            del query, top_k, runtime
            raise TimeoutError("simulated timeout")

        tools = [good, bad]
        result = await RecallOrchestrator(tools, source_tools=("good", "bad")).recall(
            "query",
            10,
            RequestContext("request", "demo", None, ("public",)),
            ExecutionBudget(),
        )
        self.assertEqual(["one"], [item.item_id for item in result.candidates])
        self.assertEqual("error", result.calls[1].status)
        self.assertEqual("TOOL_TRANSIENT_ERROR", result.calls[1].error_code)
        self.assertTrue(result.calls[1].retryable)

    async def test_programming_error_is_not_silently_degraded(self):
        @tool("buggy", response_format="content_and_artifact")
        async def buggy(
            query: str, runtime: ToolRuntime[RequestContext], top_k: int = 10
        ) -> tuple[str, dict]:
            """Raise a non-retryable programming error for boundary testing."""
            del query, top_k, runtime
            raise RuntimeError("simulated programming error")

        recall = RecallOrchestrator([buggy], source_tools=("buggy",))
        with self.assertRaisesRegex(RuntimeError, "simulated programming error"):
            await recall.recall(
                "query",
                10,
                RequestContext("request", "demo", None, ("public",)),
                ExecutionBudget(),
            )


if __name__ == "__main__":
    unittest.main()
