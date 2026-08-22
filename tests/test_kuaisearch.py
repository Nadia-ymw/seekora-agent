import json
import asyncio
import tempfile
import unittest
from pathlib import Path

from seekora_agent.application.constraints import ConstraintEngine
from seekora_agent.application.contracts import RequestContext
from seekora_agent.domain.fast_path import FusedCandidate, ResolvedIntent
from seekora_agent.domain.models import Constraint
from seekora_agent.infrastructure.catalog import inspect_quality, load_items
from seekora_agent.infrastructure.catalog_repository import InMemoryCatalogRepository
from seekora_agent.infrastructure.kuaisearch import convert_kuaisearch_items


def raw_item(
    item_id: int,
    level1_id: int = 30,
    title: str | None = None,
    level3_name: str = "手机设备",
) -> dict:
    return {
        "item_id": item_id,
        "item_title": title or f"测试电子商品 {item_id}",
        "brand_id": 1,
        "brand_name": "测试品牌",
        "seller_id": 2,
        "seller_name": "测试店铺",
        "category_level1_id": level1_id,
        "category_level1_name": "手机/数码/电脑办公" if level1_id == 30 else "女装",
        "category_level2_id": 59,
        "category_level2_name": "手机及配件",
        "category_level3_id": 57,
        "category_level3_name": level3_name,
    }


class KuaiSearchConversionTest(unittest.TestCase):
    def write_source(self, path: Path, rows: list[dict]) -> None:
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )

    def test_filters_electronics_and_maps_seekora_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jsonl"
            output = Path(directory) / "output.jsonl"
            self.write_source(source, [raw_item(1), raw_item(2, level1_id=1)])

            report = convert_kuaisearch_items(source, output, limit=10)
            items = load_items(output)

            self.assertEqual((2, 1, 1), (
                report.source_rows, report.matched_rows, report.selected_rows
            ))
            self.assertEqual("kuaisearch-1", items[0].item_id)
            self.assertEqual("demo", items[0].tenant_id)
            self.assertEqual("手机设备", items[0].category)
            self.assertEqual("测试品牌", items[0].attributes["brand"])
            self.assertGreaterEqual(items[0].attributes["price"], 5)
            self.assertTrue(items[0].attributes["synthetic_test_data"])
            self.assertIn("price", items[0].attributes["synthetic_fields"])
            self.assertIn("battery_mah", items[0].attributes["synthetic_fields"])
            self.assertIn("warranty_months", items[0].attributes["synthetic_fields"])
            self.assertTrue(inspect_quality(items).passed)

    def test_default_conversion_excludes_phone_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jsonl"
            output = Path(directory) / "output.jsonl"
            self.write_source(source, [
                raw_item(1, level3_name="手机壳配件"),
                raw_item(2, title="无线蓝牙耳机", level3_name="耳机耳麦"),
            ])

            report = convert_kuaisearch_items(source, output, limit=10)
            items = load_items(output)

            self.assertEqual(1, report.excluded_rows)
            self.assertEqual({"phone_case": 1}, report.excluded_product_type_counts)
            self.assertEqual({"audio": 1}, report.product_type_counts)
            self.assertEqual(["audio"], [item.attributes["product_type"] for item in items])
            self.assertIn("noise_cancellation", items[0].attributes)

    def test_laptop_receives_constraint_test_attributes(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jsonl"
            output = Path(directory) / "output.jsonl"
            laptop = raw_item(7, title="测试轻薄笔记本电脑")
            laptop.update({
                "category_level2_name": "电脑整机配件",
                "category_level3_name": "笔记本整机",
            })
            self.write_source(source, [laptop])

            convert_kuaisearch_items(source, output, limit=10)
            attributes = load_items(output)[0].attributes

            self.assertEqual("laptop", attributes["product_type"])
            self.assertIn(attributes["memory_gb"], (4, 8, 16, 32, 64))
            self.assertIn(attributes["battery_hours"], (6, 8, 10, 12, 15))
            self.assertIn(attributes["weight_kg"], (1.15, 1.3, 1.5, 1.8, 2.2, 2.5))

    def test_sampling_is_deterministic_and_respects_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jsonl"
            first = Path(directory) / "first.jsonl"
            second = Path(directory) / "second.jsonl"
            self.write_source(source, [raw_item(item_id) for item_id in range(100)])

            first_report = convert_kuaisearch_items(source, first, limit=10, seed=7)
            second_report = convert_kuaisearch_items(source, second, limit=10, seed=7)

            self.assertEqual(10, first_report.selected_rows)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first_report.output_sha256, second_report.output_sha256)

    def test_rejects_same_source_and_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jsonl"
            self.write_source(source, [raw_item(1)])
            with self.assertRaises(ValueError):
                convert_kuaisearch_items(source, source)

    def test_catalog_rejects_empty_missing_and_duplicate_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(FileNotFoundError):
                load_items(root / "missing.jsonl")

            empty = root / "empty.jsonl"
            empty.write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "no valid items"):
                load_items(empty)

            duplicate = root / "duplicate.jsonl"
            row = {
                "item_id": "same",
                "tenant_id": "demo",
                "title": "商品",
                "description": "描述",
                "category": "电子",
                "attributes": {},
                "status": "active",
                "permission_tags": ["public"],
                "updated_at": "2026-08-21T00:00:00Z",
            }
            self.write_source(duplicate, [row, row])
            with self.assertRaisesRegex(ValueError, "duplicate item_id"):
                load_items(duplicate)

    def test_search_document_uses_fixed_business_field_order(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jsonl"
            output = Path(directory) / "output.jsonl"
            self.write_source(source, [raw_item(1)])
            convert_kuaisearch_items(source, output, limit=10)
            text = load_items(output)[0].searchable_text()

            self.assertLess(text.index("测试品牌"), text.index("测试店铺"))
            self.assertNotIn("synthetic_test_data", text)
            self.assertNotIn("source_item_id", text)

    def test_synthetic_constraint_evidence_is_not_authoritative(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jsonl"
            output = Path(directory) / "output.jsonl"
            self.write_source(source, [raw_item(1)])
            convert_kuaisearch_items(source, output, limit=10)
            item = load_items(output)[0]
            engine = ConstraintEngine(InMemoryCatalogRepository([item]))
            intent = ResolvedIntent(
                mode="SEARCH",
                domain=None,
                retrieval_query="测试商品",
                hard_constraints=(Constraint("price", "lte", 100_000),),
            )

            result = asyncio.run(engine.apply(
                [FusedCandidate(item.item_id, item.title, 1.0, {}, ("test",))],
                intent,
                RequestContext("request-1", "demo", None, ("public",)),
            ))

            self.assertEqual("synthetic", result.accepted[0].evidence[0]["trust_level"])


if __name__ == "__main__":
    unittest.main()
