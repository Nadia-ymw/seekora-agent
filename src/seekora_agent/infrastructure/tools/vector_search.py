"""LangChain vector-search tool backed by the in-memory semantic index.

该工具使用向量相似度执行目录语义召回。
"""
from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Annotated, Protocol

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import Field

from ...domain.models import SearchQuery, SearchResult
from ...application.semantic import EmbeddingUnavailable, VectorIndexMismatch
from ...application.contracts import RequestContext


class SemanticSearchIndex(Protocol):
    """工具只依赖搜索契约，可接 TF-IDF、Embedding 精确索引或 ANN。"""

    def search(self, query: SearchQuery, top_k: int = 10) -> list[SearchResult]: ...


def build_vector_search_tool(
    index: SemanticSearchIndex,
    source_version: str | None = None,
    source_name: str = "tfidf",
    fallback_index: SemanticSearchIndex | None = None,
    fallback_version: str = "tfidf-v1",
    fallback_source_name: str = "tfidf_fallback",
    challenger_index: SemanticSearchIndex | None = None,
    challenger_version: str | None = None,
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
        search_query = SearchQuery(
            text=query,
            tenant_id=context.tenant_id,
            allowed_permission_tags=context.allowed_permission_tags,
        )
        active_version = source_version or str(
            getattr(index, "source_version", "unknown")
        )
        degraded = False
        error_code = None
        active_source = source_name
        try:
            results = await asyncio.to_thread(index.search, search_query, top_k)
        except (EmbeddingUnavailable, VectorIndexMismatch):
            if fallback_index is None:
                raise
            # 只捕获已知模型/索引可用性错误；程序缺陷继续抛出，避免静默掩盖。
            results = await asyncio.to_thread(fallback_index.search, search_query, top_k)
            active_version = fallback_version
            active_source = fallback_source_name
            degraded = True
            error_code = "SEMANTIC_FALLBACK_TFIDF"
        if not results and fallback_index is not None and not degraded:
            # Active Qwen 空召回也允许一次有界 TF-IDF 降级，避免自然语言查询直接零结果。
            results = await asyncio.to_thread(fallback_index.search, search_query, top_k)
            active_version = fallback_version
            active_source = fallback_source_name
            degraded = True
            error_code = "SEMANTIC_EMPTY_FALLBACK_TFIDF"

        challenger: dict[str, object] | None = None
        if challenger_index is not None:
            challenger_started = perf_counter()
            try:
                shadow_results = await asyncio.to_thread(
                    challenger_index.search, search_query, top_k
                )
                primary_ids = {result.item.item_id for result in results}
                shadow_ids = {result.item.item_id for result in shadow_results}
                challenger = {
                    "status": "ok",
                    "source_version": challenger_version or str(
                        getattr(challenger_index, "source_version", "unknown")
                    ),
                    "latency_ms": int((perf_counter() - challenger_started) * 1_000),
                    "candidate_ids": [result.item.item_id for result in shadow_results],
                    "scores": [round(result.score, 6) for result in shadow_results],
                    "overlap_count": len(primary_ids & shadow_ids),
                }
            except (EmbeddingUnavailable, VectorIndexMismatch):
                # Shadow 失败只记录，不影响默认 TF-IDF 候选和请求成功状态。
                challenger = {
                    "status": "degraded",
                    "source_version": challenger_version or "unknown",
                    "latency_ms": int((perf_counter() - challenger_started) * 1_000),
                    "error_code": "EMBEDDING_CHALLENGER_UNAVAILABLE",
                }
        artifact = {
            "status": "ok",
            "source_version": active_version,
            "degraded": degraded,
            "error_code": error_code,
            "metadata": {
                "retrieval_source": active_source,
                "embedding_challenger": challenger,
            } if challenger else {"retrieval_source": active_source},
            "data": {"candidates": [
                {
                    "item_id": result.item.item_id,
                    "title": result.item.title,
                    "score": round(result.score, 6),
                    "reasons": list(result.reasons),
                    "source": active_source,
                }
                for result in results
            ]},
        }
        return f"语义召回 {len(results)} 个候选", artifact

    return vector_search
