"""语义向量与版本化索引的框架无关端口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class VectorHit:
    """向量索引返回的最小命中结构，目录事实仍由 Catalog 复核。"""

    item_id: str
    score: float


class EmbeddingProvider(Protocol):
    """Embedding Provider 端口；实现可以是本地开源模型或远程服务。"""

    @property
    def model_version(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed_documents(
        self, texts: Sequence[str], batch_size: int = 32
    ) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class VectorIndex(Protocol):
    """版本化向量索引端口，不暴露租户和 ACL 决策能力。"""

    @property
    def index_version(self) -> str: ...

    @property
    def embedding_version(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def search(self, vector: Sequence[float], top_k: int) -> tuple[VectorHit, ...]: ...

    def upsert(self, item_id: str, vector: Sequence[float], content_hash: str) -> None: ...

    def delete(self, item_id: str) -> bool: ...

    def content_hash(self, item_id: str) -> str | None: ...


class EmbeddingUnavailable(RuntimeError):
    """模型文件、可选依赖或推理服务暂时不可用。"""


class VectorIndexMismatch(ValueError):
    """查询模型与索引的版本或维度不一致，禁止混用。"""
