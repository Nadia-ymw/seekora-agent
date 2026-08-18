import unittest

from seekora_agent.domain.models import GoldenQuery, Item, SearchQuery
from seekora_agent.evaluation.metrics import evaluate
from seekora_agent.infrastructure.search.bm25 import BM25Baseline


class EvaluationTest(unittest.TestCase):
    def test_perfect_ranking_scores_one(self):
        item = Item.from_dict({
            "item_id": "one", "tenant_id": "demo", "title": "编程笔记本",
            "description": "软件开发", "category": "laptop", "attributes": {},
            "status": "active", "permission_tags": ["public"],
            "updated_at": "2026-08-16T08:00:00Z", "quality_score": 1,
        })
        query = SearchQuery("编程笔记本", "demo", ("public",))
        report = evaluate(BM25Baseline([item]), [GoldenQuery("q1", query, {"one": 3})])
        self.assertEqual(1.0, report.recall_at_k)
        self.assertEqual(1.0, report.mrr_at_k)
        self.assertEqual(1.0, report.ndcg_at_k)


if __name__ == "__main__":
    unittest.main()
