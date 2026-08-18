"""LangChain catalog-search tool backed by the phase-0 BM25 implementation.

该工具使用阶段 0 的 BM25 算法执行目录关键词召回。
"""
from __future__ import annotations

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ...domain.models import SearchQuery
from ..search.bm25 import BM25Baseline


class CatalogSearchInput(BaseModel):
    query: str = Field(min_length=1, description="Normalized search query")
    top_k: int = Field(default=10, ge=1, le=50)
    tenant_id: str = Field(min_length=1)
    user_id: str | None = None
    allowed_permission_tags: list[str] = Field(default_factory=lambda: ["public"])


def build_catalog_search_tool(
    baseline: BM25Baseline, source_version: str = "sample-v1"
) -> BaseTool:
    async def catalog_search(
        query: str,
        top_k: int,
        tenant_id: str,
        user_id: str | None = None,
        allowed_permission_tags: list[str] | None = None,
    ) -> dict:
        """Search the authoritative catalog using lexical BM25 retrieval."""
        del user_id
        # 执行BM25搜索
        results = baseline.search(SearchQuery(
            text=query,
            tenant_id=tenant_id,
            allowed_permission_tags=tuple(allowed_permission_tags or ["public"]),
        ), top_k)
        # 返回格式化结果
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
        coroutine=catalog_search,
        name="catalog_search",
        description="Lexically search catalog items with tenant and ACL enforcement.",
        args_schema=CatalogSearchInput,
    )
