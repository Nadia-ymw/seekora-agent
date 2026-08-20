import unittest
from datetime import UTC, datetime, timedelta

from seekora_agent.application.exposure import ExposureService, ExposureValidationError
from seekora_agent.application.profile import ProfileService
from seekora_agent.domain.behavior import BehaviorEvent
from seekora_agent.infrastructure.stores.memory import (
    InMemoryExposureStore,
    InMemoryProfileStore,
)


class ExposureServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.profiles = ProfileService(InMemoryProfileStore())
        self.store = InMemoryExposureStore()
        self.service = ExposureService(self.store, self.profiles)

    async def register(self):
        return await self.service.register(
            tenant_id="demo",
            user_id="user-1",
            session_id="session-1",
            request_id="request-1",
            items=[{
                "item_id": "lap-1",
                "source_scores": {"vector_search": 0.8, "catalog_search": 1.0},
            }],
            model_version="agent-test-v1",
        )

    @staticmethod
    def event(exposure_id: str, **overrides) -> BehaviorEvent:
        values = {
            "event_id": "event-1",
            "tenant_id": "demo",
            "user_id": "user-1",
            "session_id": "session-1",
            "request_id": "request-1",
            "exposure_id": exposure_id,
            "item_id": "lap-1",
            "action": "click",
            "occurred_at": datetime.now(UTC).isoformat(),
            "position": 0,
            "recall_sources": ("untrusted_source",),
            "model_version": "untrusted-model",
        }
        values.update(overrides)
        return BehaviorEvent(**values)

    async def test_exposure_is_not_registered_without_storage_consent(self):
        self.assertIsNone(await self.register())

    async def test_validation_uses_server_side_attribution(self):
        await self.profiles.update_consent("demo", "user-1", True, True)
        exposure = await self.register()

        normalized = await self.service.validate_and_normalize(
            self.event(exposure.exposure_id)
        )

        self.assertEqual(("catalog_search", "vector_search"), normalized.recall_sources)
        self.assertEqual("agent-test-v1", normalized.model_version)

    async def test_identity_item_and_position_must_match_exposure(self):
        await self.profiles.update_consent("demo", "user-1", True, True)
        exposure = await self.register()

        invalid_events = [
            self.event(exposure.exposure_id, user_id="another-user"),
            self.event(exposure.exposure_id, item_id="another-item"),
            self.event(exposure.exposure_id, position=1),
        ]
        for event in invalid_events:
            with self.subTest(event=event):
                with self.assertRaises(ExposureValidationError):
                    await self.service.validate_and_normalize(event)

    async def test_feedback_cannot_predate_exposure_beyond_clock_skew(self):
        await self.profiles.update_consent("demo", "user-1", True, True)
        exposure = await self.register()
        too_early = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()

        with self.assertRaises(ExposureValidationError):
            await self.service.validate_and_normalize(
                self.event(exposure.exposure_id, occurred_at=too_early)
            )

    async def test_exposure_delete_is_scoped_to_user(self):
        await self.profiles.update_consent("demo", "user-1", True, True)
        exposure = await self.register()

        self.assertEqual(1, await self.service.delete_user_data("demo", "user-1"))
        self.assertIsNone(await self.store.get("demo", exposure.exposure_id))


if __name__ == "__main__":
    unittest.main()
