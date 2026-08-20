import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from seekora_agent.application.event_pipeline import (
    BehaviorEventPipeline,
    BotTrafficRejected,
    EventTimestampRejected,
    QueueEventConflict,
)
from seekora_agent.domain.behavior import BehaviorEvent, BehaviorWriteResult
from seekora_agent.infrastructure.stores.memory import (
    InMemoryBehaviorEventQueue,
    InMemoryBehaviorStore,
)
from seekora_agent.infrastructure.stores.sqlite_event_queue import (
    SQLiteBehaviorEventQueue,
)


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=UTC)


def event(
    event_id: str = "event-1",
    occurred_at: datetime = NOW,
    action: str = "click",
) -> BehaviorEvent:
    return BehaviorEvent(
        event_id=event_id,
        tenant_id="demo",
        user_id="user-1",
        session_id="session-1",
        request_id="request-1",
        exposure_id="exposure-1",
        item_id="lap-1",
        action=action,
        occurred_at=occurred_at.isoformat(),
        position=0,
        recall_sources=("catalog_search",),
        model_version="test-v1",
    )


class FlakySink:
    def __init__(self) -> None:
        self.calls = 0

    async def put_if_absent(self, item: BehaviorEvent) -> BehaviorWriteResult:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary sink failure")
        return BehaviorWriteResult(item, False)


class BehaviorEventPipelineTest(unittest.IsolatedAsyncioTestCase):
    def pipeline(self, queue=None, sink=None) -> BehaviorEventPipeline:
        return BehaviorEventPipeline(
            queue or InMemoryBehaviorEventQueue(),
            sink or InMemoryBehaviorStore(),
            now=lambda: NOW,
        )

    async def test_late_event_is_marked_and_processed(self):
        result = await self.pipeline().ingest(
            event(occurred_at=NOW - timedelta(days=2)), "Mozilla/5.0"
        )

        self.assertTrue(result.late)
        self.assertEqual("processed", result.queue_status)

    async def test_bot_user_agent_is_rejected_before_queueing(self):
        queue = InMemoryBehaviorEventQueue()
        with self.assertRaises(BotTrafficRejected):
            await self.pipeline(queue=queue).ingest(event(), "ExampleCrawler/1.0")

        self.assertIsNone(await queue.get("demo", "event-1"))

    async def test_too_old_and_future_events_are_rejected(self):
        invalid = [
            event("old", NOW - timedelta(days=31)),
            event("future", NOW + timedelta(minutes=6)),
        ]
        for item in invalid:
            with self.subTest(event_id=item.event_id):
                with self.assertRaises(EventTimestampRejected):
                    await self.pipeline().ingest(item, "Mozilla/5.0")

    async def test_same_id_with_different_payload_conflicts_in_queue(self):
        pipeline = self.pipeline()
        await pipeline.ingest(event(), "Mozilla/5.0")

        with self.assertRaises(QueueEventConflict):
            await pipeline.ingest(event(action="favorite"), "Mozilla/5.0")

    async def test_failed_delivery_can_be_replayed(self):
        queue = InMemoryBehaviorEventQueue()
        pipeline = self.pipeline(queue=queue, sink=FlakySink())
        with self.assertRaises(RuntimeError):
            await pipeline.ingest(event(), "Mozilla/5.0")
        failed = await queue.get("demo", "event-1")
        self.assertEqual("failed", failed.status)

        replayed = await pipeline.replay("demo", "event-1")
        self.assertTrue(replayed.replayed)
        self.assertEqual("processed", replayed.queue_status)
        self.assertEqual(2, (await queue.get("demo", "event-1")).attempts)

    async def test_sqlite_queue_survives_adapter_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.sqlite3"
            first = SQLiteBehaviorEventQueue(path)
            await first.enqueue(event(), late=False)
            await first.mark_processed("demo", "event-1")

            second = SQLiteBehaviorEventQueue(path)
            restored = await second.get("demo", "event-1")

            self.assertEqual("processed", restored.status)
            self.assertEqual(1, restored.attempts)
            self.assertEqual("user-1", restored.event.user_id)


if __name__ == "__main__":
    unittest.main()
