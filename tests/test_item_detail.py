import unittest

from seekora_agent.application.contracts import RequestContext
from seekora_agent.application.tool_registry import LangChainToolRegistry
from seekora_agent.domain.models import Item
from seekora_agent.infrastructure.catalog_repository import InMemoryCatalogRepository
from seekora_agent.infrastructure.tools.item_detail import build_item_detail_tool


def item(item_id: str, tenant_id: str, permission_tags: list[str]) -> Item:
    return Item.from_dict({
        "item_id": item_id,
        "tenant_id": tenant_id,
        "title": f"商品 {item_id}",
        "description": "权威目录详情",
        "category": "laptop",
        "attributes": {"price": 5999},
        "status": "active",
        "permission_tags": permission_tags,
        "updated_at": "2026-08-20T08:00:00Z",
    })


class ItemDetailToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_batch_detail_enforces_tenant_and_acl(self):
        tool = build_item_detail_tool(InMemoryCatalogRepository([
            item("public", "demo", ["public"]),
            item("private", "demo", ["staff"]),
            item("other", "other", ["public"]),
        ]))
        registry = LangChainToolRegistry([tool])

        result = await registry.invoke(
            "item_detail",
            {"item_ids": ["public", "private", "other"]},
            RequestContext("request-1", "demo", None, ("public",)),
        )

        self.assertEqual("ok", result.status)
        self.assertEqual(["public"], [entry["item_id"] for entry in result.output["items"]])
        self.assertEqual(2, result.output["omitted_count"])
        self.assertEqual({"item_ids"}, set(tool.tool_call_schema.model_fields))


if __name__ == "__main__":
    unittest.main()
