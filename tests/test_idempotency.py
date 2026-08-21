import tempfile
import unittest
from pathlib import Path

from seekora_agent.application.contracts import AgentQuery
from seekora_agent.application.idempotency import request_fingerprint
from seekora_agent.infrastructure.stores.sqlite_request_replay import (
    SQLiteRequestReplayStore,
)
from test_runtime import runtime_with_one_item


class RequestIdempotencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_completed_request_replays_after_runtime_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request-replays.sqlite3"
            query = AgentQuery(
                "轻薄编程笔记本",
                "demo",
                "session-idempotent",
                client_request_id="client-request-1",
            )
            first_runtime = runtime_with_one_item(SQLiteRequestReplayStore(path))
            first = [event async for event in first_runtime.run(query)]

            second_runtime = runtime_with_one_item(SQLiteRequestReplayStore(path))
            second = [event async for event in second_runtime.run(query)]

            self.assertEqual(
                [event.as_dict() for event in first],
                [event.as_dict() for event in second],
            )
            self.assertEqual(first[0].request_id, second[0].request_id)
            self.assertIsNone(
                await second_runtime.sessions.get("demo", "session-idempotent")
            )

    async def test_same_client_id_with_different_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRequestReplayStore(Path(directory) / "request-replays.sqlite3")
            runtime = runtime_with_one_item(store)
            first_query = AgentQuery(
                "轻薄编程笔记本",
                "demo",
                "session-conflict",
                client_request_id="same-client-id",
            )
            await self._collect(runtime, first_query)

            conflicting = AgentQuery(
                "游戏笔记本",
                "demo",
                "session-conflict",
                client_request_id="same-client-id",
            )
            events = await self._collect(runtime, conflicting)

            self.assertEqual(["error", "done"], [event.event for event in events])
            self.assertEqual(
                "CLIENT_REQUEST_ID_CONFLICT", events[0].data["error_code"]
            )
            self.assertEqual(0, events[-1].data["tool_calls"])

    async def test_processing_request_is_not_executed_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteRequestReplayStore(Path(directory) / "request-replays.sqlite3")
            query = AgentQuery(
                "轻薄编程笔记本",
                "demo",
                "session-processing",
                client_request_id="processing-client-id",
            )
            await store.reserve(
                query.tenant_id,
                query.client_request_id,
                request_fingerprint(query),
                "existing-request",
            )

            events = await self._collect(runtime_with_one_item(store), query)

            self.assertEqual("CLIENT_REQUEST_IN_PROGRESS", events[0].data["error_code"])
            self.assertEqual("existing-request", events[0].data["existing_request_id"])
            self.assertTrue(events[0].data["retryable"])

    @staticmethod
    async def _collect(runtime, query):
        return [event async for event in runtime.run(query)]


if __name__ == "__main__":
    unittest.main()
