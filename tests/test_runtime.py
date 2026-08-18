import unittest

from seekora_agent.application.contracts import AgentQuery, BudgetExceeded, ExecutionBudget
from seekora_agent.application.constraints import ConstraintEngine
from seekora_agent.application.recall import RecallOrchestrator
from seekora_agent.application.runtime import AgentRuntime
from seekora_agent.application.workflow import LangChainFastPathWorkflow
from seekora_agent.domain.models import Item
from seekora_agent.infrastructure.catalog_repository import InMemoryCatalogRepository
from seekora_agent.infrastructure.intent.rule_based import RuleBasedIntentResolver
from seekora_agent.infrastructure.search.bm25 import BM25Baseline
from seekora_agent.infrastructure.search.semantic import InMemorySemanticIndex
from seekora_agent.infrastructure.stores.memory import (
    InMemoryCancellationRegistry,
    InMemoryReceiptStore,
    InMemorySessionStore,
)
from seekora_agent.infrastructure.tools.catalog_search import build_catalog_search_tool
from seekora_agent.infrastructure.tools.vector_search import build_vector_search_tool


def runtime_with_one_item() -> AgentRuntime:
    item = Item.from_dict({
        "item_id": "lap-1",
        "tenant_id": "demo",
        "title": "轻薄编程笔记本",
        "description": "适合软件开发",
        "category": "laptop",
        "attributes": {"price": 7000},
        "status": "active",
        "permission_tags": ["public"],
        "updated_at": "2026-08-16T08:00:00Z",
        "quality_score": 0.9,
    })
    tools = [
        build_catalog_search_tool(BM25Baseline([item]), "test-catalog-v1"),
        build_vector_search_tool(InMemorySemanticIndex([item]), "test-vector-v1"),
    ]
    return AgentRuntime(
        workflow=LangChainFastPathWorkflow(
            intent_resolver=RuleBasedIntentResolver(),
            recall=RecallOrchestrator(tools),
            constraint_engine=ConstraintEngine(InMemoryCatalogRepository([item])),
        ),
        sessions=InMemorySessionStore(),
        receipts=InMemoryReceiptStore(),
        cancellations=InMemoryCancellationRegistry(),
    )


class BudgetTest(unittest.TestCase):
    def test_tool_call_budget_is_enforced(self):
        budget = ExecutionBudget(max_tool_calls=1)
        budget.consume_tool_call()
        with self.assertRaises(BudgetExceeded):
            budget.consume_tool_call()

    def test_duplicate_tool_registration_is_rejected(self):
        tool = build_catalog_search_tool(BM25Baseline([]))
        with self.assertRaises(ValueError):
            RecallOrchestrator([tool, tool])


class RuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_streams_events_and_persists_receipt(self):
        runtime = runtime_with_one_item()
        query = AgentQuery("轻薄编程笔记本", "demo", "session-1")
        events = [event async for event in runtime.run(query)]

        self.assertEqual("request.accepted", events[0].event)
        self.assertIn("result", [event.event for event in events])
        self.assertEqual("done", events[-1].event)
        request_id = events[0].request_id

        receipt = await runtime.receipts.get(request_id)
        self.assertIsNotNone(receipt)
        self.assertEqual("completed", receipt.status)
        self.assertEqual(["lap-1"], receipt.candidate_ids)
        self.assertEqual("catalog_search", receipt.tool_calls[0].tool)

        session = await runtime.sessions.get("demo", "session-1")
        self.assertEqual(2, len(session.messages))
        self.assertEqual(["user", "assistant"], [message.role for message in session.messages])

    async def test_pre_cancelled_request_stops_before_tool_call(self):
        runtime = runtime_with_one_item()
        stream = runtime.run(AgentQuery("编程", "demo", "session-cancel"))
        accepted = await anext(stream)
        await runtime.cancellations.cancel(accepted.request_id)
        remaining = [event async for event in stream]
        self.assertEqual(["cancelled", "done"], [event.event for event in remaining])
        receipt = await runtime.receipts.get(accepted.request_id)
        self.assertEqual("cancelled", receipt.status)


if __name__ == "__main__":
    unittest.main()
