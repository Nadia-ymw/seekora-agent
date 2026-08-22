"""可持久化、可增量更新的本地精确向量索引。"""
# 持久化（存盘）、增量更新（非全量重建）、本地精确（暴力余弦计算）

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Callable, Protocol, Sequence
from uuid import uuid4

from ...application.semantic import (
    EmbeddingProvider,
    EmbeddingUnavailable,
    VectorHit,
    VectorIndex,
    VectorIndexMismatch,
)
from ...domain.models import Item, SearchQuery, SearchResult
from ..search.bm25 import item_is_allowed


INDEX_SCHEMA_VERSION = 2


class SynchronizableVectorIndex(VectorIndex, Protocol):
    """离线同步所需的可变索引能力；在线查询仍只依赖 VectorIndex。"""

    embedding_model_id: str
    embedding_revision: str
    query_instruction: str | None

    def item_ids(self) -> tuple[str, ...]: ...

    def mark_synchronized(
        self, catalog_snapshot_sha256: str, item_count: int, synchronized_at: str
    ) -> None: ...

# 声明一个不可变数据类，用于记录索引同步（构建/更新）的详细审计报告
@dataclass(frozen=True)
class IndexSyncReport:
    """索引同步报告，用于 CLI、测试和后续离线评测审计。"""

    total_items: int
    embedded_items: int
    unchanged_items: int
    deleted_items: int
    failed_items: int
    duration_seconds: float
    peak_batch_size: int
    index_version: str
    embedding_version: str
    dimension: int
    catalog_snapshot_sha256: str
    synchronized_at: str

    def as_dict(self) -> dict[str, int | float | str]:
        return {
            "total_items": self.total_items,
            "embedded_items": self.embedded_items,
            "unchanged_items": self.unchanged_items,
            "deleted_items": self.deleted_items,
            "failed_items": self.failed_items,
            "duration_seconds": round(self.duration_seconds, 3),
            "peak_batch_size": self.peak_batch_size,
            "index_version": self.index_version,
            "embedding_version": self.embedding_version,
            "dimension": self.dimension,
            "catalog_snapshot_sha256": self.catalog_snapshot_sha256,
            "synchronized_at": self.synchronized_at,
        }


class VersionedVectorIndex:
    """开发态精确余弦索引；端口可在生产环境替换为 ANN 实现。"""

    def __init__(
        self,
        embedding_version: str,
        dimension: int,   # 向量维度
        entries: dict[str, tuple[list[float], str]] | None = None,   # 内存数据结构，键为 item_id，值为 (归一化向量, 内容哈希)
        # 内容哈希的唯一使命就是：判断商品内容是否发生了变更，从而决定是否需要重新计算向量
        index_version: str = "exact-cosine-v1",
        *,
        embedding_model_id: str | None = None,
        embedding_revision: str | None = None,
        query_instruction: str | None = None,
        catalog_snapshot_sha256: str | None = None,
        item_count: int = 0,
        built_at: str | None = None,
        last_synchronized_at: str | None = None,
    ) -> None:
        if dimension <= 0:
            raise ValueError("vector dimension must be positive")
        self._embedding_version = embedding_version
        self._dimension = dimension
        self._index_version = index_version
        self._entries = entries or {}
        self.embedding_model_id = embedding_model_id or embedding_version
        self.embedding_revision = embedding_revision or "unknown"
        self.query_instruction = query_instruction
        self.catalog_snapshot_sha256 = catalog_snapshot_sha256
        self.item_count = item_count
        self.built_at = built_at
        self.last_synchronized_at = last_synchronized_at

    @property
    def index_version(self) -> str:
        return self._index_version

    @property
    def embedding_version(self) -> str:
        return self._embedding_version

    @property
    def dimension(self) -> int:
        return self._dimension

    def search(self, vector: Sequence[float], top_k: int) -> tuple[VectorHit, ...]:
        if len(vector) != self.dimension:
            raise VectorIndexMismatch(
                f"query dimension {len(vector)} does not match index dimension {self.dimension}"
            )
        query = self._normalize(vector)
        # 计算余弦相似度
        hits = [
            VectorHit(
                item_id,
                sum(left * right for left, right in zip(query, stored, strict=True)),
            )
            for item_id, (stored, _) in self._entries.items()
        ]
        # 降序排序
        hits.sort(key=lambda hit: (-hit.score, hit.item_id))
        return tuple(hit for hit in hits if hit.score > 0)[:top_k]

    def upsert(self, item_id: str, vector: Sequence[float], content_hash: str) -> None:
        if len(vector) != self.dimension:
            raise VectorIndexMismatch(
                f"document dimension {len(vector)} does not match index dimension {self.dimension}"
            )
        self._entries[item_id] = (self._normalize(vector), content_hash)

    def delete(self, item_id: str) -> bool:
        return self._entries.pop(item_id, None) is not None

    def content_hash(self, item_id: str) -> str | None:
        entry = self._entries.get(item_id)
        return entry[1] if entry else None

    def item_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def mark_synchronized(
        self, catalog_snapshot_sha256: str, item_count: int, synchronized_at: str
    ) -> None:
        """完整同步成功后才更新快照元数据，避免半成品冒充有效索引。"""
        self.catalog_snapshot_sha256 = catalog_snapshot_sha256
        self.item_count = item_count
        self.built_at = self.built_at or synchronized_at
        self.last_synchronized_at = synchronized_at

    """
        持久化方法主要在 build-vector-index（构建向量索引） 命令中被调用，且构成了一个“先加载（如有）→ 增量更新 → 保存”的完整生命周期。
    """
    # 持久化保存,被调用的时机（写入新索引到磁盘）
    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": INDEX_SCHEMA_VERSION,
            "index_version": self.index_version,
            "embedding_version": self.embedding_version,
            "dimension": self.dimension,
            "metadata": {
                "embedding_model_id": self.embedding_model_id,
                "embedding_revision": self.embedding_revision,
                "query_instruction": self.query_instruction,
                "catalog_snapshot_sha256": self.catalog_snapshot_sha256,
                "item_count": self.item_count,
                "built_at": self.built_at,
                "last_synchronized_at": self.last_synchronized_at,
            },
            "entries": {
                item_id: {"vector": vector, "content_hash": content_hash}
                for item_id, (vector, content_hash) in sorted(self._entries.items())
            },
        }
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    # 持久化加载, 被调用的时机（读取已存索引）
    @classmethod
    def load(
        cls,
        path: str | Path,
        expected_embedding_version: str | None = None,
        expected_dimension: int | None = None,
        expected_catalog_snapshot_sha256: str | None = None,
        expected_item_count: int | None = None,
    ) -> "VersionedVectorIndex":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        schema_version = int(raw.get("schema_version", 0))
        if schema_version not in {1, INDEX_SCHEMA_VERSION}:
            raise VectorIndexMismatch("unsupported vector index schema version")
        embedding_version = str(raw["embedding_version"])
        dimension = int(raw["dimension"])
        if expected_embedding_version and embedding_version != expected_embedding_version:
            raise VectorIndexMismatch(
                "embedding model version does not match persisted vector index"
            )
        if expected_dimension and dimension != expected_dimension:
            raise VectorIndexMismatch(
                "embedding dimension does not match persisted vector index"
            )
        metadata = raw.get("metadata", {})
        snapshot_hash = metadata.get("catalog_snapshot_sha256")
        item_count = int(metadata.get("item_count", 0))
        if expected_catalog_snapshot_sha256 and snapshot_hash != expected_catalog_snapshot_sha256:
            raise VectorIndexMismatch("catalog snapshot does not match persisted vector index")
        if expected_item_count is not None and item_count != expected_item_count:
            raise VectorIndexMismatch("catalog item count does not match vector index")
        entries = {
            str(item_id): (
                [float(value) for value in entry["vector"]],
                str(entry["content_hash"]),
            )
            for item_id, entry in raw.get("entries", {}).items()
        }
        if schema_version == INDEX_SCHEMA_VERSION and item_count != len(entries):
            raise VectorIndexMismatch("vector entry count does not match index metadata")
        return cls(
            embedding_version=embedding_version,
            dimension=dimension,
            entries=entries,
            index_version=str(raw["index_version"]),
            embedding_model_id=metadata.get("embedding_model_id"),
            embedding_revision=metadata.get("embedding_revision"),
            query_instruction=metadata.get("query_instruction"),
            catalog_snapshot_sha256=snapshot_hash,
            item_count=item_count,
            built_at=metadata.get("built_at"),
            last_synchronized_at=metadata.get("last_synchronized_at"),
        )

    @staticmethod
    def _normalize(vector: Sequence[float]) -> list[float]:
        values = [float(value) for value in vector]
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]

# 同步函数
def synchronize_vector_index(
    index: SynchronizableVectorIndex,
    items: Sequence[Item],
    embedding: EmbeddingProvider,
    batch_size: int = 16,
    *,
    catalog_snapshot_sha256: str | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> IndexSyncReport:
    """按内容哈希批量更新变化文档，并删除目录中已经消失的条目。"""
    started = perf_counter()
    if embedding.model_version != index.embedding_version:
        raise VectorIndexMismatch("embedding version does not match vector index")
    if embedding.dimension != index.dimension:
        raise VectorIndexMismatch("embedding dimension does not match vector index")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    # 增量变更检测
    changed: list[tuple[Item, str]] = []
    all_content_hashes: list[str] = []
    for item in items:
        # 对每个 item，计算其可搜索文本（searchable_text()）的 SHA256
        digest = hashlib.sha256(item.searchable_text().encode("utf-8")).hexdigest()
        all_content_hashes.append(f"{item.item_id}:{digest}")
        if index.content_hash(item.item_id) != digest:
            changed.append((item, digest))
    unchanged = len(items) - len(changed)
    snapshot_hash = catalog_snapshot_sha256 or hashlib.sha256(
        "\n".join(all_content_hashes).encode("utf-8")
    ).hexdigest()

    completed = 0
    peak_batch_size = 0
    # 批处理嵌入更新
    for offset in range(0, len(changed), batch_size):
        batch = changed[offset : offset + batch_size]
        peak_batch_size = max(peak_batch_size, len(batch))
        # 批量生成向量
        vectors = embedding.embed_documents(
            [item.searchable_text() for item, _ in batch], batch_size=len(batch)
        )
        if len(vectors) != len(batch):
            raise ValueError("embedding result count does not match document count")
        entries = tuple(
            (item.item_id, vector, digest)
            for (item, digest), vector in zip(batch, vectors, strict=True)
        )
        # SQLite 实现按一个 Embedding 批次提交一次事务；内存实现继续逐条写入。
        upsert_many = getattr(index, "upsert_many", None)
        if upsert_many is not None:
            upsert_many(entries)
        else:
            for item_id, vector, digest in entries:
                index.upsert(item_id, vector, digest)
        completed += len(batch)
        if progress:
            progress(completed, len(changed))

    # 删除过期条目
    current_ids = {item.item_id for item in items}
    stale_ids = tuple(
        item_id for item_id in index.item_ids() if item_id not in current_ids
    )
    delete_many = getattr(index, "delete_many", None)
    deleted = (
        delete_many(stale_ids)
        if delete_many is not None
        else sum(index.delete(item_id) for item_id in stale_ids)
    )
    # 同步元数据
    synchronized_at = datetime.now(UTC).isoformat()
    index.embedding_model_id = str(
        getattr(embedding, "model_id", embedding.model_version)
    )
    index.embedding_revision = str(getattr(embedding, "model_revision", "unknown"))
    index.query_instruction = getattr(embedding, "query_instruction", None)
    index.mark_synchronized(snapshot_hash, len(items), synchronized_at)
    return IndexSyncReport(
        total_items=len(items),
        embedded_items=len(changed),
        unchanged_items=unchanged,
        deleted_items=deleted,
        failed_items=0,
        duration_seconds=perf_counter() - started,
        peak_batch_size=peak_batch_size,
        index_version=index.index_version,
        embedding_version=index.embedding_version,
        dimension=index.dimension,
        catalog_snapshot_sha256=snapshot_hash,
        synchronized_at=synchronized_at,
    )


class EmbeddingSemanticIndex:
    """把 Embedding/VectorIndex 端口适配为现有 SearchResult 契约。"""

    def __init__(
        self,
        items: Sequence[Item],
        embedding: EmbeddingProvider,
        index: VectorIndex,
    ) -> None:
        # 任何不一致都会触发 VectorIndexMismatch，错误索引不会进入检索链路
        if embedding.model_version != index.embedding_version:
            raise VectorIndexMismatch("query embedding version does not match vector index")
        self.items = {item.item_id: item for item in items}
        self.embedding = embedding
        self.index = index

    @property
    def source_version(self) -> str:
        return f"{self.index.index_version}:{self.embedding.model_version}"

    """
        将用户查询转化为向量，然后从向量索引中检索出最相关的商品，并经过权限过滤后返回符合业务规则的结果

        用户查询
        → 添加查询指令
        → 生成查询向量
        → 精确余弦检索
        → 扩大到 top_k × 5
        → 回到可信 Catalog
        → 检查租户、状态和 ACL
        → 截取最终 top_k
    """
    def search(self, query: SearchQuery, top_k: int = 10) -> list[SearchResult]:
        # 生成查询向量
        vector = self.embedding.embed_query(query.text)
        # 调用精确向量索引（暴力遍历）中查找与查询向量最相似的 max(top_k * 5, top_k) 个商品
        """
        为什么乘以 5？
        这是为了对抗后续过滤导致的候选不足。因为后续会进行权限过滤（item_is_allowed），
        可能剔除大量商品（例如租户不匹配、权限标签不符）。如果只取 top_k 个，过滤后可能只剩下极少数甚至 0 个结果。
        通过多取 5 倍候选，能显著提高最终能凑齐 top_k 个有效结果的概率。这是一个典型的 「先扩招，后精选」 工程策略。
        """
        
        hits = self.index.search(vector, max(top_k * 5, top_k))
        results: list[SearchResult] = []
        for hit in hits:
            item = self.items.get(hit.item_id)
            # 过滤,检查租户、状态和 ACL 商品的租户 ID 是否与查询的租户匹配？商品的权限标签是否包含在查询允许的标签集合中？
            if item is None or not item_is_allowed(item, query):
                continue
            results.append(SearchResult(item, hit.score, ("semantic_embedding",)))
            # 一旦累积结果达到 top_k，立即跳出循环
            if len(results) >= top_k:
                break
        return results

"""
启动时发现 Qwen 索引缺失/损坏/版本不一致
→ 不让整个项目启动失败
→ 创建 UnavailableEmbeddingSemanticIndex
→ 用户查询时该占位对象抛出 EmbeddingUnavailable
→ vector_search 捕获异常
→ 保留 TF-IDF 结果
→ Receipt 记录 Embedding 已降级及原因
"""
class UnavailableEmbeddingSemanticIndex:
    """把启动期模型/索引错误延迟为可审计降级，不阻断 BM25/TF-IDF。"""

    def __init__(self, source_version: str, reason: str) -> None:
        self.source_version = source_version
        self.reason = reason

    def search(self, query: SearchQuery, top_k: int = 10) -> list[SearchResult]:
        del query, top_k
        raise EmbeddingUnavailable(self.reason)
