import unittest

from seekora_agent.application.behavior import BehaviorService
from seekora_agent.application.contracts import AgentQuery, BudgetExceeded, ExecutionBudget
from seekora_agent.application.constraints import ConstraintEngine
from seekora_agent.application.recall import RecallOrchestrator
from seekora_agent.application.runtime import AgentRuntime
from seekora_agent.application.session_context import SessionContextResolver
from seekora_agent.application.profile import ProfileService
from seekora_agent.application.exposure import ExposureService
from seekora_agent.application.workflow import LangChainFastPathWorkflow
from seekora_agent.domain.models import Item
from seekora_agent.domain.test_account import build_default_test_account
from seekora_agent.infrastructure.catalog_repository import InMemoryCatalogRepository
from seekora_agent.infrastructure.intent.rule_based import RuleBasedIntentResolver
from seekora_agent.infrastructure.search.bm25 import BM25Baseline
from seekora_agent.infrastructure.search.semantic import InMemorySemanticIndex
from seekora_agent.infrastructure.session_context.rule_based import (
    RuleBasedSessionContextPatchResolver,
)
from seekora_agent.infrastructure.stores.memory import (
    InMemoryCancellationRegistry,
    InMemoryBehaviorStore,
    InMemoryBehaviorEventQueue,
    InMemoryExposureStore,
    InMemoryProfileStore,
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
    test_account = build_default_test_account()
    profile_service = ProfileService(
        InMemoryProfileStore([test_account.initial_profile])
    )
    exposure_service = ExposureService(InMemoryExposureStore(), profile_service)
    behavior_service = BehaviorService(
        InMemoryBehaviorStore(),
        profile_service,
        exposure_service,
        InMemoryBehaviorEventQueue(),
    )
    return AgentRuntime(
        workflow=LangChainFastPathWorkflow(
            intent_resolver=RuleBasedIntentResolver(),
            recall=RecallOrchestrator(tools),
            constraint_engine=ConstraintEngine(InMemoryCatalogRepository([item])),
            session_context=SessionContextResolver(
                RuleBasedSessionContextPatchResolver()
            ),
        ),
        sessions=InMemorySessionStore(),
        receipts=InMemoryReceiptStore(),
        cancellations=InMemoryCancellationRegistry(),
        profiles=profile_service,
        behaviors=behavior_service,
        exposures=exposure_service,
        test_account=test_account,
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
        self.assertEqual(request_id, session.current_intent.request_id)
        self.assertEqual("SEARCH", session.current_intent.resolved_intent["mode"])

    async def test_pre_cancelled_request_stops_before_tool_call(self):
        runtime = runtime_with_one_item()
        stream = runtime.run(AgentQuery("编程", "demo", "session-cancel"))
        accepted = await anext(stream)
        await runtime.cancellations.cancel(accepted.request_id)
        remaining = [event async for event in stream]
        self.assertEqual(["cancelled", "done"], [event.event for event in remaining])
        receipt = await runtime.receipts.get(accepted.request_id)
        self.assertEqual("cancelled", receipt.status)

    async def test_runtime_applies_previous_session_constraints(self):
        runtime = runtime_with_one_item()
        first_events = [event async for event in runtime.run(
            AgentQuery("推荐8000元以内的轻薄笔记本", "demo", "session-context")
        )]
        first_request_id = first_events[0].request_id
        first_intent = next(
            event for event in first_events if event.event == "intent.resolved"
        )

        second_events = [event async for event in runtime.run(
            AgentQuery("预算改成6000元以内", "demo", "session-context")
        )]
        context_event = next(
            event for event in second_events if event.event == "session.context_applied"
        )
        intent_event = next(
            event for event in second_events if event.event == "intent.resolved"
        )
        receipt = await runtime.receipts.get(second_events[0].request_id)

        self.assertEqual(first_request_id, context_event.data["previous_request_id"])
        self.assertEqual(["price"], context_event.data["replaced_fields"])
        self.assertEqual(
            first_intent.data["retrieval_query"], intent_event.data["retrieval_query"]
        )
        self.assertEqual(6000.0, intent_event.data["hard_constraints"][0]["value"])
        self.assertTrue(receipt.session_context["applied"])

    async def test_runtime_registers_server_exposure_for_consented_user(self):
        runtime = runtime_with_one_item()
        await runtime.profiles.update_consent("demo", "user-1", True, True)

        events = [event async for event in runtime.run(
            AgentQuery("轻薄编程笔记本", "demo", "session-exposure", "user-1")
        )]
        result = next(event for event in events if event.event == "result")
        item = result.data["items"][0]
        receipt = await runtime.receipts.get(result.request_id)
        exposure = await runtime.exposures.store.get("demo", item["exposure_id"])

        self.assertEqual(0, item["position"])
        self.assertEqual(item["exposure_id"], receipt.exposure_id)
        self.assertEqual(result.request_id, exposure.request_id)
        self.assertEqual("lap-1", exposure.items[0].item_id)


if __name__ == "__main__":
    unittest.main()
