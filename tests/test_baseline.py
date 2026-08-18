import unittest

from seekora_agent.domain.models import Constraint, Item, SearchQuery
from seekora_agent.infrastructure.search.bm25 import BM25Baseline, item_is_allowed, tokenize


def make_item(item_id: str, title: str, price: int, permission: str = "public") -> Item:
    return Item.from_dict({
        "item_id": item_id,
        "tenant_id": "demo",
        "title": title,
        "description": "适合编程和移动办公",
        "category": "laptop",
        "attributes": {"price": price, "memory_gb": 32},
        "status": "active",
        "permission_tags": [permission],
        "updated_at": "2026-08-16T08:00:00Z",
        "quality_score": 0.8,
    })


class BaselineTest(unittest.TestCase):
    def test_chinese_tokenizer_contains_bigrams(self):
        tokens = tokenize("轻薄本 Python 3.11")
        self.assertIn("轻薄", tokens)
        self.assertIn("python", tokens)
        self.assertIn("3.11", tokens)

    def test_constraints_are_deterministic(self):
        item = make_item("a", "轻薄本", 7000)
        query = SearchQuery(
            text="轻薄本",
            tenant_id="demo",
            allowed_permission_tags=("public",),
            constraints=(Constraint("price", "lte", 6000),),
        )
        self.assertFalse(item_is_allowed(item, query))

    def test_permissions_are_enforced(self):
        item = make_item("secret", "内部笔记本", 100, permission="internal")
        query = SearchQuery("笔记本", "demo", ("public",))
        self.assertFalse(item_is_allowed(item, query))

    def test_search_returns_relevant_item(self):
        items = [
            make_item("light", "轻薄编程笔记本", 7000),
            make_item("game", "高性能游戏本", 9000),
        ]
        results = BM25Baseline(items).search(SearchQuery("轻薄编程", "demo", ("public",)))
        self.assertEqual("light", results[0].item.item_id)


if __name__ == "__main__":
    unittest.main()
