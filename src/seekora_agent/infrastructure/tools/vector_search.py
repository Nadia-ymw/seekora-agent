"""LangChain vector-search tool backed by the in-memory semantic index.

该工具使用向量相似度执行目录语义召回。
"""
from __future__ import annotations

from typing import Annotated

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import Field

from ...domain.models import SearchQuery
from ...application.contracts import RequestContext
from ..search.semantic import InMemorySemanticIndex


def build_vector_search_tool(
    index: InMemorySemanticIndex, source_version: str = "tfidf-v1"
) -> BaseTool:
    @tool(
        "vector_search",
        response_format="content_and_artifact",
    )
    async def vector_search(
        query: Annotated[
            str, Field(min_length=1, description="经过规范化的语义检索查询")
        ],
        runtime: ToolRuntime[RequestContext],
        top_k: Annotated[
            int, Field(ge=1, le=50, description="最多返回的候选数量")
        ] = 10,
    ) -> tuple[str, dict]:
        """按语义相似度搜索商品目录，适合描述性需求和近义表达召回。"""
        context = runtime.context
        results = index.search(SearchQuery(
            text=query,
            tenant_id=context.tenant_id,
            allowed_permission_tags=context.allowed_permission_tags,
        ), top_k)
        artifact = {
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
        return f"语义召回 {len(results)} 个候选", artifact

    return vector_search
