import tempfile
import unittest
from pathlib import Path

from seekora_agent.application.contracts import AgentQuery
from seekora_agent.application.receipt import RecommendationReceipt, ToolCallReceipt
from seekora_agent.application.session import (
    SessionIdentityConflict,
    SessionIntentSnapshot,
    SessionMessage,
    SessionVersionConflict,
)
from seekora_agent.infrastructure.stores.sqlite_receipt import SQLiteReceiptStore
from seekora_agent.infrastructure.stores.sqlite_session import SQLiteSessionStore
from test_runtime import runtime_with_one_item


class SQLiteSessionStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_session_survives_recreation_and_trims_messages(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sessions.sqlite3"
            first = SQLiteSessionStore(path, max_messages=2)
            state = await first.get_or_create("session-1", "demo", "user-1")
            await first.append(state, SessionMessage("user", "第一条", "request-1"))
            await first.append(state, SessionMessage("assistant", "第二条", "request-1"))
            await first.append(state, SessionMessage("user", "第三条", "request-2"))
            await first.set_intent(
                state,
                SessionIntentSnapshot("request-2", {"mode": "SEARCH"}),
            )

            restored = await SQLiteSessionStore(path, max_messages=2).get(
                "demo", "session-1"
            )

            self.assertEqual(["第二条", "第三条"], [item.content for item in restored.messages])
            self.assertEqual("request-2", restored.current_intent.request_id)
            self.assertEqual(4, restored.version)

    async def test_expired_session_is_removed(self):
        now = [1000.0]
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteSessionStore(
                Path(directory) / "sessions.sqlite3",
                ttl_seconds=10,
                clock=lambda: now[0],
            )
            await store.get_or_create("session-expired", "demo", None)
            now[0] = 1011.0

            self.assertIsNone(await store.get("demo", "session-expired"))

    async def test_stale_version_and_identity_change_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteSessionStore(Path(directory) / "sessions.sqlite3")
            first = await store.get_or_create("session-1", "demo", "user-1")
            stale = await store.get_or_create("session-1", "demo", "user-1")
            await store.append(first, SessionMessage("user", "消息", "request-1"))

            with self.assertRaises(SessionVersionConflict):
                await store.append(stale, SessionMessage("user", "冲突", "request-2"))
            with self.assertRaises(SessionIdentityConflict):
                await store.get_or_create("session-1", "demo", "user-2")


class SQLiteReceiptStoreTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_receipt_survives_recreation_and_expires(self):
        now = [2000.0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipts.sqlite3"
            receipt = RecommendationReceipt(
                "request-1", "session-1", "demo", "轻薄笔记本"
            )
            receipt.tool_calls.append(ToolCallReceipt(
                "catalog_search", {"query": "轻薄"}, "ok", 12, "catalog-v1"
            ))
            receipt.candidate_ids = ["lap-1"]
            receipt.resolved_intent = {"mode": "SEARCH"}
            receipt.finish("completed")
            first = SQLiteReceiptStore(
                path, retention_seconds=10, clock=lambda: now[0]
            )
            await first.put(receipt)

            restored_store = SQLiteReceiptStore(
                path, retention_seconds=10, clock=lambda: now[0]
            )
            restored = await restored_store.get("request-1")

            self.assertEqual(receipt.as_dict(), restored.as_dict())
            self.assertIsInstance(restored.tool_calls[0], ToolCallReceipt)
            now[0] = 2011.0
            self.assertIsNone(await restored_store.get("request-1"))

    async def test_runtime_restores_multiturn_session_and_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            session_path = Path(directory) / "sessions.sqlite3"
            receipt_path = Path(directory) / "receipts.sqlite3"
            first_runtime = runtime_with_one_item(
                sessions=SQLiteSessionStore(session_path),
                receipts=SQLiteReceiptStore(receipt_path),
            )
            first_events = [event async for event in first_runtime.run(AgentQuery(
                "推荐8000元以内的轻薄笔记本", "demo", "persistent-session"
            ))]

            second_runtime = runtime_with_one_item(
                sessions=SQLiteSessionStore(session_path),
                receipts=SQLiteReceiptStore(receipt_path),
            )
            second_events = [event async for event in second_runtime.run(AgentQuery(
                "预算改成6000元以内", "demo", "persistent-session"
            ))]
            context = next(
                event for event in second_events if event.event == "session.context_applied"
            )
            first_receipt = await second_runtime.receipts.get(first_events[0].request_id)

            self.assertEqual(first_events[0].request_id, context.data["previous_request_id"])
            self.assertEqual("completed", first_receipt.status)


if __name__ == "__main__":
    unittest.main()
