"""LangChain vector-search tool backed by the in-memory semantic index.

该工具使用向量相似度执行目录语义召回。
"""
from __future__ import annotations

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ...domain.models import SearchQuery
from ..search.semantic import InMemorySemanticIndex


class VectorSearchInput(BaseModel):
    query: str = Field(min_length=1, description="Normalized semantic query")
    top_k: int = Field(default=10, ge=1, le=50)
    tenant_id: str = Field(min_length=1)
    user_id: str | None = None
    allowed_permission_tags: list[str] = Field(default_factory=lambda: ["public"])


def build_vector_search_tool(
    index: InMemorySemanticIndex, source_version: str = "tfidf-v1"
) -> BaseTool:
    async def vector_search(
        query: str,
        top_k: int,
        tenant_id: str,
        user_id: str | None = None,
        allowed_permission_tags: list[str] | None = None,
    ) -> dict:
        """Search catalog items by semantic vector similarity."""
        del user_id
        results = index.search(SearchQuery(
            text=query,
            tenant_id=tenant_id,
            allowed_permission_tags=tuple(allowed_permission_tags or ["public"]),
        ), top_k)
        return {
            "status": "ok",
            "source_version": source_version,
            "data": {"candidates": [
                {
                    "item_id": result.item.item_id,
                    "title": result.item.title,
                    "score": round(result.score, 6),
                    "reasons": list(result.reasons),
                }
                for result in results
            ]},
        }

    return StructuredTool.from_function(
        coroutine=vector_search,
        name="vector_search",
        description="Semantically search catalog items with tenant and ACL enforcement.",
        args_schema=VectorSearchInput,
    )
