import unittest

from seekora_agent.domain.models import GoldenQuery, Item, SearchQuery, SearchResult
from seekora_agent.evaluation.metrics import evaluate
from seekora_agent.evaluation.recall_comparison import (
    ReciprocalRankFusionSearch,
    compare_recall,
    select_qwen_weight,
    validate_golden_catalog,
)
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

    def test_rrf_comparison_reports_quality_latency_and_stability_gate(self):
        first = self._item("one", "编程笔记本")
        second = self._item("two", "办公电脑")

        class FixedSearch:
            def __init__(self, items):
                self.items = items

            def search(self, query, top_k=10):
                del query
                return [
                    SearchResult(item, 1.0 / rank, ("fixed",))
                    for rank, item in enumerate(self.items[:top_k], start=1)
                ]

        golden = [GoldenQuery(
            "q1", SearchQuery("开发电脑", "demo", ("public",)), {"one": 3}
        )]
        baseline = ReciprocalRankFusionSearch((
            FixedSearch([second, first]), FixedSearch([first, second])
        ))
        active = ReciprocalRankFusionSearch((
            FixedSearch([first, second]), FixedSearch([first, second])
        ))
        report = compare_recall(
            baseline,
            active,
            golden,
            top_k=2,
            runs=2,
            p95_limit_ms=2_000,
            min_query_count=1,
        )
        self.assertTrue(report.gate_passed)
        self.assertEqual(1.0, report.active_latency.stability_rate)
        self.assertGreaterEqual(report.active.ndcg_at_k, report.baseline.ndcg_at_k)

    def test_golden_catalog_validation_rejects_sample_ids(self):
        golden = [GoldenQuery(
            "q1", SearchQuery("query", "demo"), {"sample-only": 3}
        )]
        with self.assertRaisesRegex(ValueError, "absent from catalog"):
            validate_golden_catalog(golden, {"processed-item"})

    def test_golden_catalog_validation_rejects_empty_labels(self):
        golden = [GoldenQuery("q1", SearchQuery("query", "demo"), {})]
        with self.assertRaisesRegex(ValueError, "must have relevance labels"):
            validate_golden_catalog(golden, set())

    def test_weighted_rrf_can_prioritize_keyword_source(self):
        keyword_item = self._item("keyword", "精确关键词商品")
        semantic_item = self._item("semantic", "语义商品")

        class FixedSearch:
            def __init__(self, items):
                self.items = items

            def search(self, query, top_k=10):
                del query
                return [
                    SearchResult(item, 1.0, ("fixed",))
                    for item in self.items[:top_k]
                ]

        searcher = ReciprocalRankFusionSearch(
            (FixedSearch([keyword_item]), FixedSearch([semantic_item])),
            weights=(1.0, 0.5),
        )
        results = searcher.search(SearchQuery("关键词", "demo"), 2)
        self.assertEqual(["keyword", "semantic"], [item.item.item_id for item in results])

    def test_weight_selection_uses_development_ndcg(self):
        relevant = self._item("relevant", "相关")
        distractor = self._item("distractor", "干扰")

        class FixedSearch:
            def __init__(self, items):
                self.items = items

            def search(self, query, top_k=10):
                del query
                return [
                    SearchResult(item, 1.0, ("fixed",))
                    for item in self.items[:top_k]
                ]

        development = [GoldenQuery(
            "dev", SearchQuery("查询", "demo"), {"relevant": 3}
        )]
        selection = select_qwen_weight(
            FixedSearch([relevant]),
            FixedSearch([distractor]),
            development,
            (0.25, 1.0),
            top_k=2,
        )
        self.assertEqual(0.25, selection.selected_weight)
        self.assertEqual(2, len(selection.trials))

    @staticmethod
    def _item(item_id: str, title: str) -> Item:
        return Item.from_dict({
            "item_id": item_id,
            "tenant_id": "demo",
            "title": title,
            "description": "",
            "category": "laptop",
            "attributes": {},
            "status": "active",
            "permission_tags": ["public"],
            "updated_at": "2026-08-16T08:00:00Z",
            "quality_score": 1,
        })


if __name__ == "__main__":
    unittest.main()
