import json
import unittest
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from seekora_agent.interfaces.http.api import create_app
from test_runtime import runtime_with_one_item


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.runtime = runtime_with_one_item()
        self.client = TestClient(create_app(self.runtime))

    def test_health(self):
        response = self.client.get("/health")
        self.assertEqual(200, response.status_code)
        self.assertEqual({"status": "ok"}, response.json())

    def test_chat_frontend_is_served(self):
        response = self.client.get("/")
        self.assertEqual(200, response.status_code)
        self.assertIn("搜索推荐 Agent", response.text)
        self.assertIn("/static/app.js", response.text)

    def test_public_config_exposes_resolver_without_secrets(self):
        response = self.client.get("/agent/config")
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("langchain/langgraph", payload["framework"])
        self.assertIn("resolver_version", payload)
        self.assertNotIn("api_key", response.text.lower())

    def test_development_account_is_initialized_for_end_to_end_testing(self):
        response = self.client.get("/agent/dev/account")

        self.assertEqual(200, response.status_code)
        account = response.json()
        self.assertEqual("demo", account["tenant_id"])
        self.assertEqual("seekora-demo-user", account["user_id"])
        self.assertTrue(account["development_only"])
        self.assertFalse(account["authentication_enabled"])
        self.assertTrue(account["profile"]["consent"]["personalization_enabled"])
        self.assertTrue(account["profile"]["consent"]["behavior_storage_enabled"])
        self.assertNotIn("password", response.text.lower())

        with self.client.stream("POST", "/agent/query", json={
            "query": "轻薄编程笔记本",
            "tenant_id": account["tenant_id"],
            "user_id": account["user_id"],
            "session_id": account["default_session_id"],
        }) as query_response:
            body = "".join(query_response.iter_text())
        result = next(
            json.loads(line[6:])
            for line in body.splitlines()
            if line.startswith("data: ") and '"event":"result"' in line
        )
        self.assertIn("exposure_id", result["data"]["items"][0])

    def test_query_returns_sse_and_receipt(self):
        with self.client.stream("POST", "/agent/query", json={
            "query": "轻薄编程笔记本",
            "tenant_id": "demo",
            "session_id": "api-session",
        }) as response:
            body = "".join(response.iter_text())
        self.assertEqual(200, response.status_code)
        self.assertIn("event: request.accepted", body)
        self.assertIn("event: result", body)
        self.assertIn("event: done", body)

        data_lines = [line[6:] for line in body.splitlines() if line.startswith("data: ")]
        events = [json.loads(line) for line in data_lines]
        request_id = events[0]["request_id"]
        receipt = self.client.get(f"/agent/receipts/{request_id}")
        self.assertEqual(200, receipt.status_code)
        self.assertEqual("completed", receipt.json()["status"])

    def test_invalid_query_is_rejected(self):
        response = self.client.post("/agent/query", json={
            "query": "",
            "tenant_id": "demo",
        })
        self.assertEqual(422, response.status_code)

    def test_profile_preferences_require_consent(self):
        response = self.client.put(
            "/agent/profiles/user-1/preferences",
            params={"tenant_id": "demo"},
            json={"positive_preferences": ["轻薄"], "negative_preferences": []},
        )
        self.assertEqual(409, response.status_code)

    def test_profile_consent_preferences_and_delete_lifecycle(self):
        consent = self.client.put(
            "/agent/profiles/user-1/consent",
            params={"tenant_id": "demo"},
            json={
                "personalization_enabled": True,
                "behavior_storage_enabled": False,
            },
        )
        self.assertEqual(200, consent.status_code)

        preferences = self.client.put(
            "/agent/profiles/user-1/preferences",
            params={"tenant_id": "demo"},
            json={
                "positive_preferences": ["轻薄", "长续航"],
                "negative_preferences": ["厚重"],
            },
        )
        self.assertEqual(200, preferences.status_code)

        profile = self.client.get(
            "/agent/profiles/user-1", params={"tenant_id": "demo"}
        ).json()
        self.assertEqual(["轻薄", "长续航"], profile["positive_preferences"])
        self.assertEqual(["厚重"], profile["negative_preferences"])

        deleted = self.client.delete(
            "/agent/profiles/user-1", params={"tenant_id": "demo"}
        )
        self.assertEqual(
            {
                "deleted": True,
                "behavior_events_deleted": 0,
                "exposures_deleted": 0,
                "queued_events_deleted": 0,
            },
            deleted.json(),
        )

    def test_feedback_requires_consent_and_is_idempotent(self):
        payload = {
            "event_id": "event-1",
            "tenant_id": "demo",
            "user_id": "user-feedback",
            "session_id": "session-feedback",
            "request_id": "request-feedback",
            "exposure_id": "missing-exposure",
            "item_id": "lap-1",
            "action": "click",
            "occurred_at": datetime.now(UTC).isoformat(),
            "position": 0,
            "recall_sources": ["catalog_search"],
            "model_version": "test-v1",
        }
        denied = self.client.post("/agent/feedback", json=payload)
        self.assertEqual(409, denied.status_code)

        self.client.put(
            "/agent/profiles/user-feedback/consent",
            params={"tenant_id": "demo"},
            json={
                "personalization_enabled": True,
                "behavior_storage_enabled": True,
            },
        )
        with self.client.stream("POST", "/agent/query", json={
            "query": "轻薄编程笔记本",
            "tenant_id": "demo",
            "user_id": "user-feedback",
            "session_id": "session-feedback",
        }) as query_response:
            query_body = "".join(query_response.iter_text())
        query_events = [
            json.loads(line[6:])
            for line in query_body.splitlines()
            if line.startswith("data: ")
        ]
        result_event = next(
            event
            for event in query_events
            if event["event"] == "result" and event["data"]["items"]
        )
        exposed_item = result_event["data"]["items"][0]
        payload.update({
            "request_id": result_event["request_id"],
            "exposure_id": exposed_item["exposure_id"],
            "item_id": exposed_item["item_id"],
            "position": exposed_item["position"],
            # 服务端会忽略客户端自报值，并使用曝光清单中的归因真值。
            "recall_sources": ["untrusted_source"],
            "model_version": "untrusted-model",
            "occurred_at": datetime.now(UTC).isoformat(),
        })
        created = self.client.post("/agent/feedback", json=payload)
        duplicate = self.client.post("/agent/feedback", json=payload)

        self.assertEqual(201, created.status_code)
        self.assertFalse(created.json()["duplicate"])
        self.assertNotEqual(
            ["untrusted_source"], created.json()["event"]["recall_sources"]
        )
        self.assertEqual("0.16.0", created.json()["event"]["model_version"])
        self.assertEqual(200, duplicate.status_code)
        self.assertTrue(duplicate.json()["duplicate"])

        conflicting = dict(payload, action="favorite")
        conflict = self.client.post("/agent/feedback", json=conflicting)
        self.assertEqual(409, conflict.status_code)

        bot_payload = dict(payload, event_id="event-bot")
        bot = self.client.post(
            "/agent/feedback",
            json=bot_payload,
            headers={"User-Agent": "ExampleCrawler/1.0"},
        )
        self.assertEqual(403, bot.status_code)

        deleted = self.client.delete(
            "/agent/profiles/user-feedback", params={"tenant_id": "demo"}
        )
        self.assertEqual(1, deleted.json()["behavior_events_deleted"])
        self.assertEqual(1, deleted.json()["exposures_deleted"])
        self.assertEqual(1, deleted.json()["queued_events_deleted"])


if __name__ == "__main__":
    unittest.main()
