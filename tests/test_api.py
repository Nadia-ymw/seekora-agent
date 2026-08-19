import json
import unittest

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
        self.assertEqual({"deleted": True}, deleted.json())


if __name__ == "__main__":
    unittest.main()
