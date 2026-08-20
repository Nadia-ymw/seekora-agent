"""LangChain catalog-search tool backed by the phase-0 BM25 implementation.

该工具使用阶段 0 的 BM25 算法执行目录关键词召回。
"""
from __future__ import annotations

from typing import Annotated

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import Field

from ...domain.models import SearchQuery
from ...application.contracts import RequestContext
from ..search.bm25 import BM25Baseline


def build_catalog_search_tool(
    baseline: BM25Baseline, source_version: str = "sample-v1"
) -> BaseTool:
    @tool(
        "catalog_search",
        response_format="content_and_artifact",
    )
    async def catalog_search(
        query: Annotated[
            str, Field(min_length=1, description="经过规范化的商品关键词查询")
        ],
        runtime: ToolRuntime[RequestContext],
        top_k: Annotated[
            int, Field(ge=1, le=50, description="最多返回的候选数量")
        ] = 10,
    ) -> tuple[str, dict]:
        """使用 BM25 搜索权威商品目录，适合名称、品牌和属性关键词召回。"""
        context = runtime.context
        # 执行BM25搜索
        results = baseline.search(SearchQuery(
            text=query,
            tenant_id=context.tenant_id,
            allowed_permission_tags=context.allowed_permission_tags,
        ), top_k)
        # 返回格式化结果
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
        return f"BM25 召回 {len(results)} 个候选", artifact

    return catalog_search
