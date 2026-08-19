import unittest

from seekora_agent.application.contracts import AgentQuery
from seekora_agent.application.deep_path import ComplexityRouter
from seekora_agent.bootstrap import build_runtime
from seekora_agent.domain.fast_path import ResolvedIntent


class ComplexityRouterTest(unittest.TestCase):
    def test_high_confidence_simple_query_stays_on_fast_path(self):
        decision = ComplexityRouter().decide(ResolvedIntent(
            mode="SEARCH",
            domain="laptop",
            retrieval_query="轻薄笔记本",
            confidence=0.9,
        ))
        self.assertEqual("fast", decision.route)
        self.assertEqual(("simple_query",), decision.reasons)

    def test_low_confidence_query_uses_deep_path(self):
        decision = ComplexityRouter().decide(ResolvedIntent(
            mode="SEARCH",
            domain=None,
            retrieval_query="适合开发的设备",
            confidence=0.6,
            ambiguities=("domain",),
        ))
        self.assertEqual("deep", decision.route)
        self.assertIn("low_intent_confidence", decision.reasons)


class DeepPathIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_deep_path_streams_probe_plan_and_persists_receipt(self):
        runtime = build_runtime()
        events = [event async for event in runtime.run(AgentQuery(
            query="编程",
            tenant_id="demo",
            session_id="deep-path-test",
        ))]
        names = [event.event for event in events]

        self.assertLess(names.index("routing.completed"), names.index("probe.completed"))
        self.assertLess(names.index("probe.completed"), names.index("plan.created"))
        self.assertIn("result", names)

        receipt = await runtime.receipts.get(events[0].request_id)
        self.assertEqual("deep", receipt.route)
        self.assertGreater(receipt.probe_summary["candidate_count"], 0)
        self.assertEqual("primary", receipt.plan["steps"][0]["purpose"])
        # Two source calls for the probe and two for the executable plan query.
        self.assertEqual(4, len(receipt.tool_calls))

    async def test_fast_path_does_not_pay_probe_cost(self):
        runtime = build_runtime()
        events = [event async for event in runtime.run(AgentQuery(
            query="轻薄笔记本",
            tenant_id="demo",
            session_id="fast-route-test",
        ))]
        names = [event.event for event in events]
        receipt = await runtime.receipts.get(events[0].request_id)

        self.assertEqual("fast", receipt.route)
        self.assertNotIn("probe.completed", names)
        self.assertEqual(2, len(receipt.tool_calls))

    async def test_non_laptop_category_uses_deep_path_and_returns_catalog_item(self):
        runtime = build_runtime()
        events = [event async for event in runtime.run(AgentQuery(
            query="推荐1000元以内适合通勤的主动降噪耳机",
            tenant_id="demo",
            session_id="deep-headphones-test",
        ))]
        result = next(event for event in events if event.event == "result")
        receipt = await runtime.receipts.get(events[0].request_id)

        self.assertEqual("deep", receipt.route)
        self.assertIn("aud-001", [item["item_id"] for item in result.data["items"]])
        self.assertNotIn("aud-005", [item["item_id"] for item in result.data["items"]])

    async def test_many_laptop_constraints_trigger_deep_path(self):
        runtime = build_runtime()
        events = [event async for event in runtime.run(AgentQuery(
            query="推荐8000元以内内存32GB以上重量1.4kg以内的轻薄笔记本",
            tenant_id="demo",
            session_id="deep-constraints-test",
        ))]
        result = next(event for event in events if event.event == "result")
        receipt = await runtime.receipts.get(events[0].request_id)

        self.assertEqual("deep", receipt.route)
        self.assertIn("many_hard_constraints", receipt.route_decision["reasons"])
        self.assertEqual(["lap-001"], [item["item_id"] for item in result.data["items"]])

    async def test_impossible_constraints_return_empty_grounded_result(self):
        runtime = build_runtime()
        events = [event async for event in runtime.run(AgentQuery(
            query="推荐200元以内内存32GB以上重量1kg以内的轻薄笔记本",
            tenant_id="demo",
            session_id="deep-empty-test",
        ))]
        result = next(event for event in events if event.event == "result")
        receipt = await runtime.receipts.get(events[0].request_id)
        names = [event.event for event in events]

        self.assertEqual("deep", receipt.route)
        self.assertEqual([], result.data["items"])
        self.assertTrue(receipt.filtered_reason_counts)
        self.assertEqual(1, names.count("plan.replanned"))
        self.assertIn("response.refused", names)
        self.assertEqual(1, receipt.replan_count)
        self.assertEqual("refused", receipt.status)
        self.assertLessEqual(len(receipt.tool_calls), 8)

    async def test_unsupported_ambiguous_query_requests_clarification(self):
        runtime = build_runtime()
        events = [event async for event in runtime.run(AgentQuery(
            query="夸克熵泵",
            tenant_id="demo",
            session_id="deep-clarify-test",
        ))]
        names = [event.event for event in events]
        result = next(event for event in events if event.event == "result")
        receipt = await runtime.receipts.get(events[0].request_id)

        self.assertIn("clarification.required", names)
        self.assertEqual([], result.data["items"])
        self.assertEqual("clarification_required", receipt.status)
        self.assertEqual("clarify", receipt.terminal_decision["action"])
        self.assertEqual(["你希望查找哪一类商品？"], receipt.terminal_decision["questions"])


if __name__ == "__main__":
    unittest.main()
