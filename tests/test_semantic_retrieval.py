import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from seekora_agent.application.contracts import AgentQuery, RequestContext
from seekora_agent.application.reranking import (
    RerankOrchestrator,
    RerankerUnavailable,
)
from seekora_agent.application.semantic import EmbeddingUnavailable, VectorIndexMismatch
from seekora_agent.application.tool_registry import LangChainToolRegistry
from seekora_agent.bootstrap import build_runtime
from seekora_agent.config.settings import AppSettings
from seekora_agent.domain.fast_path import FusedCandidate
from seekora_agent.domain.models import Item, SearchQuery
from seekora_agent.infrastructure.catalog_repository import InMemoryCatalogRepository
from seekora_agent.infrastructure.embeddings.qwen3 import Qwen3Embedding
from seekora_agent.infrastructure.search.semantic import InMemorySemanticIndex
from seekora_agent.infrastructure.search.vector_index import (
    EmbeddingSemanticIndex,
    VersionedVectorIndex,
    synchronize_vector_index,
)
from seekora_agent.infrastructure.search.sqlite_vector_index import SQLiteVectorIndex
from seekora_agent.infrastructure.tools.vector_search import build_vector_search_tool


def make_item(
    item_id: str,
    title: str,
    permission: str = "public",
    description: str = "",
) -> Item:
    return Item.from_dict({
        "item_id": item_id,
        "tenant_id": "demo",
        "title": title,
        "description": description,
        "category": "electronics",
        "attributes": {},
        "status": "active",
        "permission_tags": [permission],
        "updated_at": "2026-08-20T00:00:00Z",
    })


class FakeEmbedding:
    model_version = "fake-multilingual-v1"
    dimension = 2

    def __init__(self) -> None:
        self.document_calls = 0

    @staticmethod
    def vector(text: str) -> list[float]:
        if "轻薄" in text:
            return [1.0, 0.0]
        if "相机" in text:
            return [0.0, 1.0]
        return [0.5, 0.5]

    def embed_documents(self, texts, batch_size=32):
        del batch_size
        self.document_calls += len(texts)
        return [self.vector(text) for text in texts]

    def embed_query(self, text):
        return self.vector(text)


class FakeReranker:
    model_version = "fake-cross-encoder-v1"

    async def score(self, query, documents):
        del query
        # 分数与输入顺序相反，便于验证 Challenger 不改序、Active 才改序。
        return [float(index) for index, _ in enumerate(documents)]


class UnavailableReranker:
    model_version = "unavailable-v1"

    async def score(self, query, documents):
        del query, documents
        raise RerankerUnavailable("simulated model outage")


class InvalidContractReranker:
    model_version = "invalid-contract-v1"

    async def score(self, query, documents):
        del query, documents
        return [0.5]


class VersionedVectorIndexTest(unittest.TestCase):
    def test_incremental_sync_persistence_and_version_guard(self):
        items = [make_item("light", "轻薄笔记本"), make_item("camera", "专业相机")]
        embedding = FakeEmbedding()
        index = VersionedVectorIndex(embedding.model_version, embedding.dimension)

        first = synchronize_vector_index(index, items, embedding, batch_size=1)
        second = synchronize_vector_index(index, items, embedding, batch_size=2)
        self.assertEqual(2, first.embedded_items)
        self.assertEqual(0, second.embedded_items)
        self.assertEqual(2, embedding.document_calls)

        # 修改一条、删除一条时只重算变化文档，并清理已经消失的向量。
        changed = [replace(items[0], description="适合移动办公")]
        third = synchronize_vector_index(index, changed, embedding)
        self.assertEqual((1, 1), (third.embedded_items, third.deleted_items))

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vectors.json"
            index.save(path)
            restored = VersionedVectorIndex.load(
                path, expected_embedding_version=embedding.model_version
            )
            self.assertEqual(("light",), restored.item_ids())
            with self.assertRaises(VectorIndexMismatch):
                VersionedVectorIndex.load(path, expected_embedding_version="other-v2")

    def test_index_metadata_guards_dimension_and_catalog_snapshot(self):
        items = [make_item("light", "轻薄笔记本")]
        embedding = FakeEmbedding()
        index = VersionedVectorIndex(embedding.model_version, embedding.dimension)
        report = synchronize_vector_index(
            index,
            items,
            embedding,
            catalog_snapshot_sha256="catalog-v1",
        )
        self.assertEqual(0, report.failed_items)
        self.assertEqual("catalog-v1", report.catalog_snapshot_sha256)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vectors.json"
            index.save(path)
            restored = VersionedVectorIndex.load(
                path,
                expected_dimension=2,
                expected_catalog_snapshot_sha256="catalog-v1",
                expected_item_count=1,
            )
            self.assertEqual("catalog-v1", restored.catalog_snapshot_sha256)
            self.assertIsNotNone(restored.built_at)
            with self.assertRaises(VectorIndexMismatch):
                VersionedVectorIndex.load(path, expected_dimension=3)
            with self.assertRaises(VectorIndexMismatch):
                VersionedVectorIndex.load(
                    path, expected_catalog_snapshot_sha256="catalog-v2"
                )

    def test_embedding_search_applies_catalog_acl_after_vector_hit(self):
        items = [
            make_item("public", "轻薄公开笔记本"),
            make_item("private", "轻薄内部笔记本", permission="internal"),
        ]
        embedding = FakeEmbedding()
        index = VersionedVectorIndex(embedding.model_version, embedding.dimension)
        synchronize_vector_index(index, items, embedding)
        semantic = EmbeddingSemanticIndex(items, embedding, index)

        results = semantic.search(SearchQuery("轻薄", "demo", ("public",)), top_k=10)
        self.assertEqual(["public"], [result.item.item_id for result in results])


class SQLiteVectorIndexTest(unittest.TestCase):
    def test_incremental_sync_knn_and_restart_validation(self):
        items = [make_item("light", "轻薄笔记本"), make_item("camera", "专业相机")]
        embedding = FakeEmbedding()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vectors.sqlite3"
            index = SQLiteVectorIndex.create(
                path,
                embedding_version=embedding.model_version,
                dimension=embedding.dimension,
            )
            first = synchronize_vector_index(
                index,
                items,
                embedding,
                batch_size=2,
                catalog_snapshot_sha256="catalog-v1",
            )
            second = synchronize_vector_index(
                index,
                items,
                embedding,
                catalog_snapshot_sha256="catalog-v1",
            )
            self.assertEqual((2, 0), (first.embedded_items, second.embedded_items))
            self.assertEqual("light", index.search([1.0, 0.0], 1)[0].item_id)

            changed = [replace(items[0], description="适合移动办公")]
            third = synchronize_vector_index(
                index,
                changed,
                embedding,
                catalog_snapshot_sha256="catalog-v2",
            )
            self.assertEqual((1, 1), (third.embedded_items, third.deleted_items))
            index.close()

            restored = SQLiteVectorIndex.load(
                path,
                expected_embedding_version=embedding.model_version,
                expected_dimension=2,
                expected_catalog_snapshot_sha256="catalog-v2",
                expected_item_count=1,
            )
            self.assertEqual(("light",), restored.item_ids())
            restored.close()

            with self.assertRaises(VectorIndexMismatch):
                SQLiteVectorIndex.load(path, expected_dimension=3)

    def test_invalid_sqlite_file_is_rejected_as_index_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.sqlite3"
            path.write_text("not a database", encoding="utf-8")
            with self.assertRaises(VectorIndexMismatch):
                SQLiteVectorIndex.load(path)


class SemanticFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_active_embedding_candidates_replace_tfidf_and_expose_qwen_source(self):
        items = [make_item("light", "轻薄笔记本"), make_item("camera", "专业相机")]
        embedding = FakeEmbedding()
        index = VersionedVectorIndex(embedding.model_version, embedding.dimension)
        synchronize_vector_index(index, items, embedding)
        active = EmbeddingSemanticIndex(items, embedding, index)
        registry = LangChainToolRegistry([
            build_vector_search_tool(
                active,
                source_version=active.source_version,
                source_name="qwen",
                fallback_index=InMemorySemanticIndex(items),
            )
        ])

        execution = await registry.invoke(
            "vector_search",
            {"query": "相机", "top_k": 10},
            RequestContext("request", "demo", None, ("public",)),
        )
        self.assertFalse(execution.output["degraded"])
        self.assertEqual("qwen", execution.output["metadata"]["retrieval_source"])
        self.assertEqual(
            ["camera"],
            [item["item_id"] for item in execution.output["data"]["candidates"]],
        )
        self.assertTrue(all(
            item["source"] == "qwen"
            for item in execution.output["data"]["candidates"]
        ))

    async def test_known_embedding_failure_falls_back_to_tfidf(self):
        item = make_item("light", "轻薄笔记本")

        class BrokenIndex:
            def search(self, query, top_k=10):
                del query, top_k
                raise EmbeddingUnavailable("simulated")

        registry = LangChainToolRegistry([
            build_vector_search_tool(
                BrokenIndex(),
                source_version="embedding-v1",
                fallback_index=InMemorySemanticIndex([item]),
                fallback_version="tfidf-v1",
            )
        ])
        execution = await registry.invoke(
            "vector_search",
            {"query": "轻薄", "top_k": 10},
            RequestContext("request", "demo", None, ("public",)),
        )
        self.assertEqual("ok", execution.status)
        self.assertTrue(execution.output["degraded"])
        self.assertEqual("tfidf-v1", execution.output["source_version"])
        self.assertEqual("SEMANTIC_FALLBACK_TFIDF", execution.output["error_code"])
        self.assertEqual(
            "tfidf_fallback", execution.output["metadata"]["retrieval_source"]
        )

    async def test_empty_embedding_result_falls_back_to_tfidf(self):
        item = make_item("light", "轻薄笔记本")

        class EmptyIndex:
            def search(self, query, top_k=10):
                del query, top_k
                return []

        registry = LangChainToolRegistry([
            build_vector_search_tool(
                EmptyIndex(),
                source_version="embedding-v1",
                source_name="qwen",
                fallback_index=InMemorySemanticIndex([item]),
            )
        ])
        execution = await registry.invoke(
            "vector_search",
            {"query": "轻薄", "top_k": 10},
            RequestContext("request", "demo", None, ("public",)),
        )
        self.assertTrue(execution.output["degraded"])
        self.assertEqual(
            "SEMANTIC_EMPTY_FALLBACK_TFIDF", execution.output["error_code"]
        )
        self.assertEqual(
            ["light"],
            [item["item_id"] for item in execution.output["data"]["candidates"]],
        )

    async def test_embedding_challenger_is_audited_without_replacing_tfidf_candidates(self):
        items = [make_item("light", "轻薄笔记本"), make_item("camera", "专业相机")]
        embedding = FakeEmbedding()
        index = VersionedVectorIndex(embedding.model_version, embedding.dimension)
        synchronize_vector_index(index, items, embedding)
        challenger = EmbeddingSemanticIndex(items, embedding, index)
        registry = LangChainToolRegistry([
            build_vector_search_tool(
                InMemorySemanticIndex(items),
                source_version="tfidf-v1",
                challenger_index=challenger,
            )
        ])

        execution = await registry.invoke(
            "vector_search",
            {"query": "轻薄", "top_k": 10},
            RequestContext("request", "demo", None, ("public",)),
        )
        self.assertEqual("tfidf-v1", execution.output["source_version"])
        self.assertEqual(
            "ok",
            execution.output["metadata"]["embedding_challenger"]["status"],
        )
        self.assertEqual(
            ["light"],
            [item["item_id"] for item in execution.output["data"]["candidates"]],
        )

    async def test_missing_qwen_index_keeps_runtime_on_tfidf(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = AppSettings(
                SEEKORA_EMBEDDING_MODE="challenger",
                SEEKORA_VECTOR_INDEX_PATH=str(Path(directory) / "missing.json"),
                _env_file=None,
            )
            runtime = build_runtime(
                catalog_path="data/sample/items.jsonl", settings=settings
            )
            events = [event async for event in runtime.run(AgentQuery(
                query="推荐适合编程的轻薄笔记本",
                tenant_id="demo",
                session_id="missing-qwen-index-test",
            ))]
            receipt = await runtime.receipts.get(events[0].request_id)
            vector_call = next(
                call for call in receipt.tool_calls if call.tool == "vector_search"
            )
            challenger = vector_call.metadata["embedding_challenger"]
            self.assertEqual("degraded", challenger["status"])
            self.assertEqual(
                "EMBEDDING_CHALLENGER_UNAVAILABLE", challenger["error_code"]
            )

    async def test_active_missing_qwen_index_uses_formal_tfidf_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = AppSettings(
                SEEKORA_EMBEDDING_MODE="active",
                SEEKORA_VECTOR_INDEX_PATH=str(Path(directory) / "missing.sqlite3"),
                _env_file=None,
            )
            runtime = build_runtime(
                catalog_path="data/sample/items.jsonl", settings=settings
            )
            events = [event async for event in runtime.run(AgentQuery(
                query="推荐适合编程的轻薄笔记本",
                tenant_id="demo",
                session_id="active-missing-qwen-index-test",
            ))]
            receipt = await runtime.receipts.get(events[0].request_id)
            vector_call = next(
                call for call in receipt.tool_calls if call.tool == "vector_search"
            )
            self.assertEqual("SEMANTIC_FALLBACK_TFIDF", vector_call.error_code)
            self.assertEqual(
                "tfidf_fallback", vector_call.metadata["retrieval_source"]
            )
            result = next(event for event in events if event.event == "result")
            self.assertTrue(result.data["items"])
            self.assertTrue(any(
                "tfidf_fallback" in item["source_scores"]
                for item in result.data["items"]
            ))


class Qwen3EmbeddingTest(unittest.TestCase):
    def test_query_instruction_is_not_applied_to_documents(self):
        class FakeModel:
            def __init__(self):
                self.calls = []

            def encode(self, texts, **kwargs):
                self.calls.append((texts, kwargs))
                return [[3.0, 4.0, *([0.0] * 30)] for _ in texts]

        embedding = Qwen3Embedding(
            "Qwen/Qwen3-Embedding-0.6B",
            revision="revision-1",
            dimension=32,
            query_instruction="retrieve relevant products",
        )
        fake = FakeModel()
        embedding._model = fake

        document = embedding.embed_documents(["轻薄笔记本"], batch_size=8)
        query = embedding.embed_query("移动办公电脑")

        self.assertEqual(["轻薄笔记本"], fake.calls[0][0])
        self.assertEqual(
            ["Instruct: retrieve relevant products\nQuery: 移动办公电脑"],
            fake.calls[1][0],
        )
        self.assertEqual(32, fake.calls[0][1]["truncate_dim"])
        self.assertAlmostEqual(1.0, sum(value * value for value in document[0]))
        self.assertAlmostEqual(1.0, sum(value * value for value in query))
        self.assertIn("revision-1", embedding.model_version)


class RerankOrchestratorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.items = [make_item("a", "商品 A"), make_item("b", "商品 B")]
        self.catalog = InMemoryCatalogRepository(self.items)
        self.candidates = (
            FusedCandidate("a", "商品 A", 1.0, {"catalog": 1.0}, ("first",)),
            FusedCandidate("b", "商品 B", 0.5, {"catalog": 0.5}, ("second",)),
        )

    async def test_challenger_records_scores_without_changing_rrf_order(self):
        result = await RerankOrchestrator(
            self.catalog, FakeReranker(), mode="challenger"
        ).rerank("查询", self.candidates)

        self.assertEqual(["a", "b"], [item.item_id for item in result.candidates])
        self.assertEqual([0.0, 1.0], [item.rerank_score for item in result.candidates])
        self.assertEqual({"catalog": 1.0}, result.candidates[0].source_scores)

    async def test_active_mode_reorders_only_existing_candidates(self):
        result = await RerankOrchestrator(
            self.catalog, FakeReranker(), mode="active"
        ).rerank("查询", self.candidates)
        self.assertEqual(["b", "a"], [item.item_id for item in result.candidates])

    async def test_model_outage_degrades_to_original_rrf_order(self):
        result = await RerankOrchestrator(
            self.catalog, UnavailableReranker(), mode="challenger"
        ).rerank("查询", self.candidates)
        self.assertEqual("degraded", result.status)
        self.assertEqual("RERANKER_UNAVAILABLE", result.error_code)
        self.assertEqual(self.candidates, result.candidates)

    async def test_invalid_score_count_is_not_silently_degraded(self):
        orchestrator = RerankOrchestrator(
            self.catalog, InvalidContractReranker(), mode="challenger"
        )
        with self.assertRaisesRegex(ValueError, "score count"):
            await orchestrator.rerank("查询", self.candidates)

    async def test_runtime_streams_and_persists_challenger_audit(self):
        runtime = build_runtime(catalog_path="data/sample/items.jsonl")
        runtime.workflow.reranker = RerankOrchestrator(
            runtime.workflow.constraint_engine.catalog,
            FakeReranker(),
            mode="challenger",
        )
        events = [event async for event in runtime.run(AgentQuery(
            query="推荐适合编程的轻薄笔记本",
            tenant_id="demo",
            session_id="rerank-challenger-test",
        ))]
        receipt = await runtime.receipts.get(events[0].request_id)
        self.assertIn("rerank.completed", [event.event for event in events])
        self.assertEqual("challenger", receipt.rerank_executions[0]["mode"])
        self.assertTrue(receipt.rerank_executions[0]["scores"])


class RerankSettingsTest(unittest.TestCase):
    def test_challenger_requires_explicit_model(self):
        settings = AppSettings(
            SEEKORA_RERANK_MODE="challenger",
            SEEKORA_RERANKER_MODEL=None,
            _env_file=None,
        )
        with self.assertRaisesRegex(ValueError, "SEEKORA_RERANKER_MODEL"):
            settings.require_reranker()

    def test_embedding_challenger_requires_model_and_index(self):
        settings = AppSettings(
            SEEKORA_EMBEDDING_MODE="challenger",
            SEEKORA_EMBEDDING_MODEL="fake-model",
            SEEKORA_VECTOR_INDEX_PATH=None,
            _env_file=None,
        )
        with self.assertRaisesRegex(ValueError, "SEEKORA_VECTOR_INDEX_PATH"):
            settings.require_embedding_challenger()

    def test_embedding_active_is_a_valid_mode_and_uses_shared_validation(self):
        settings = AppSettings(
            SEEKORA_EMBEDDING_MODE="active",
            SEEKORA_EMBEDDING_MODEL="fake-model",
            SEEKORA_VECTOR_INDEX_PATH="vectors.sqlite3",
            _env_file=None,
        )
        self.assertEqual("active", settings.embedding_mode)
        self.assertEqual("fake-model", settings.require_embedding().model_id)


if __name__ == "__main__":
    unittest.main()
