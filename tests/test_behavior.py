import unittest
from datetime import UTC, datetime

from langchain_core.tools import StructuredTool

from seekora_agent.application.behavior import (
    BehaviorConsentRequired,
    BehaviorService,
)
from seekora_agent.application.event_pipeline import QueueEventConflict
from seekora_agent.application.profile import ProfileService
from seekora_agent.application.exposure import ExposureService
from seekora_agent.application.contracts import ExecutionBudget, RequestContext
from seekora_agent.application.recall import RecallOrchestrator
from seekora_agent.domain.behavior import BehaviorEvent
from seekora_agent.domain.models import Item
from seekora_agent.infrastructure.stores.memory import (
    InMemoryBehaviorStore,
    InMemoryBehaviorEventQueue,
    InMemoryExposureStore,
    InMemoryProfileStore,
)
from seekora_agent.infrastructure.tools.behavior_recall import build_behavior_recall_tool


def behavior_event(
    event_id: str = "event-1",
    action: str = "click",
    item_id: str = "lap-1",
    exposure_id: str = "exposure-1",
    position: int = 0,
) -> BehaviorEvent:
    return BehaviorEvent(
        event_id=event_id,
        tenant_id="demo",
        user_id="user-1",
        session_id="session-1",
        request_id="request-1",
        exposure_id=exposure_id,
        item_id=item_id,
        action=action,
        occurred_at=datetime.now(UTC).isoformat(),
        position=position,
        recall_sources=("catalog_search",),
        model_version="test-v1",
    )


def catalog_item(item_id: str, permission_tags: list[str]) -> Item:
    return Item.from_dict({
        "item_id": item_id,
        "tenant_id": "demo",
        "title": f"测试商品 {item_id}",
        "description": "用于行为召回测试",
        "category": "laptop",
        "attributes": {"price": 5000},
        "status": "active",
        "permission_tags": permission_tags,
        "updated_at": "2026-08-19T08:00:00Z",
    })


class BehaviorServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.profile_service = ProfileService(InMemoryProfileStore())
        self.exposure_service = ExposureService(
            InMemoryExposureStore(), self.profile_service
        )
        self.store = InMemoryBehaviorStore()
        self.service = BehaviorService(
            self.store,
            self.profile_service,
            self.exposure_service,
            InMemoryBehaviorEventQueue(),
        )

    async def register_exposure(self, *item_ids: str):
        return await self.exposure_service.register(
            "demo",
            "user-1",
            "session-1",
            "request-1",
            [
                {"item_id": item_id, "source_scores": {"catalog_search": 1.0}}
                for item_id in item_ids
            ],
            "test-v1",
        )

    async def test_record_requires_behavior_storage_consent(self):
        with self.assertRaises(BehaviorConsentRequired):
            await self.service.record(behavior_event())

    async def test_event_write_is_idempotent_and_conflict_safe(self):
        await self.profile_service.update_consent("demo", "user-1", False, True)
        exposure = await self.register_exposure("lap-1")

        created = await self.service.record(behavior_event(exposure_id=exposure.exposure_id))
        duplicate = await self.service.record(behavior_event(exposure_id=exposure.exposure_id))
        self.assertFalse(created.duplicate)
        self.assertTrue(duplicate.duplicate)

        with self.assertRaises(QueueEventConflict):
            await self.service.record(
                behavior_event(action="favorite", exposure_id=exposure.exposure_id)
            )

    async def test_behavior_scores_require_both_consents(self):
        await self.profile_service.update_consent("demo", "user-1", False, True)
        exposure = await self.register_exposure("lap-1")
        await self.service.record(
            behavior_event("click-1", "click", exposure_id=exposure.exposure_id)
        )
        await self.service.record(
            behavior_event("favorite-1", "favorite", exposure_id=exposure.exposure_id)
        )

        self.assertEqual({}, await self.service.item_scores("demo", "user-1"))
        await self.profile_service.update_consent("demo", "user-1", True, True)
        self.assertEqual({"lap-1": 4.0}, await self.service.item_scores("demo", "user-1"))

    async def test_behavior_recall_enforces_catalog_acl(self):
        await self.profile_service.update_consent("demo", "user-1", True, True)
        exposure = await self.register_exposure("lap-public", "lap-private")
        await self.service.record(behavior_event(
            "public-1", "favorite", "lap-public", exposure.exposure_id, 0
        ))
        await self.service.record(behavior_event(
            "private-1", "conversion", "lap-private", exposure.exposure_id, 1
        ))
        tool = build_behavior_recall_tool(
            self.service,
            [catalog_item("lap-public", ["public"]), catalog_item("lap-private", ["staff"])],
        )

        output = await tool.ainvoke({
            "query": "笔记本",
            "top_k": 10,
            "tenant_id": "demo",
            "user_id": "user-1",
            "allowed_permission_tags": ["public"],
        })

        self.assertEqual(
            ["lap-public"],
            [candidate["item_id"] for candidate in output["data"]["candidates"]],
        )

    async def test_delete_user_data_propagates_after_consent_revocation(self):
        await self.profile_service.update_consent("demo", "user-1", True, True)
        exposure = await self.register_exposure("lap-1")
        await self.service.record(behavior_event(exposure_id=exposure.exposure_id))
        await self.profile_service.update_consent("demo", "user-1", False, False)

        self.assertEqual(1, await self.service.delete_user_data("demo", "user-1"))
        self.assertEqual((), await self.store.list_by_user("demo", "user-1"))

    async def test_behavior_only_candidate_cannot_bypass_query_recall(self):
        def static_tool(name: str, candidates: list[dict]) -> StructuredTool:
            async def invoke(
                query: str,
                top_k: int,
                tenant_id: str,
                user_id: str | None = None,
                allowed_permission_tags: list[str] | None = None,
            ) -> dict:
                del query, top_k, tenant_id, user_id, allowed_permission_tags
                return {
                    "status": "ok",
                    "source_version": "test-v1",
                    "data": {"candidates": candidates},
                }

            return StructuredTool.from_function(
                coroutine=invoke,
                name=name,
                description=f"Static {name} for behavior grounding test.",
            )

        catalog_candidate = {"item_id": "current-1", "title": "当前命中", "score": 1.0}
        behavior_candidates = [
            {"item_id": "current-1", "title": "当前命中", "score": 2.0},
            {"item_id": "history-only", "title": "仅历史命中", "score": 5.0},
        ]
        recall = RecallOrchestrator(
            [
                static_tool("catalog_search", [catalog_candidate]),
                static_tool("behavior_recall", behavior_candidates),
            ],
            source_tools=("catalog_search", "behavior_recall"),
        )

        result = await recall.recall(
            "当前查询",
            10,
            RequestContext("request-1", "demo", "user-1", ("public",)),
            ExecutionBudget(),
        )

        self.assertEqual(["current-1"], [item.item_id for item in result.candidates])
        self.assertIn("behavior_recall", result.candidates[0].source_scores)


if __name__ == "__main__":
    unittest.main()
