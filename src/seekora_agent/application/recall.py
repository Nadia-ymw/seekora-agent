"""Parallel LangChain-tool recall and deterministic Reciprocal Rank Fusion."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Sequence

from langchain_core.tools import BaseTool

from ..domain.fast_path import FusedCandidate
from .tool_registry import LangChainToolRegistry
from .contracts import ExecutionBudget, RequestContext

# 单词召回调用记录
@dataclass(frozen=True)
class RecallCall:
    tool: str
    arguments: dict[str, Any] #参数
    status: str
    data: dict[str, Any]  # 工具返回的数据
    latency_ms: int  # 执行耗时
    source_version: str
    error_code: str | None = None
    retryable: bool = False   # 是否可重试
    metadata: dict[str, Any] | None = None  # Challenger 等不参与融合的审计信息


@dataclass(frozen=True)
class RecallResult:
    candidates: tuple[FusedCandidate, ...]
    calls: tuple[RecallCall, ...]


class RecallUnavailable(RuntimeError):
    """所有召回源失败时仍携带调用记录，供 DAG 降级和 Receipt 审计。"""

    def __init__(self, calls: tuple[RecallCall, ...]) -> None:
        super().__init__("ALL_RECALL_SOURCES_FAILED")
        self.calls = calls


class RecallOrchestrator:
    """Runs LangChain BaseTool instances and fuses their structured output."""

    def __init__(
        self,
        tools: Sequence[BaseTool],
        source_tools: tuple[str, ...] = ("catalog_search", "vector_search"),
        rrf_k: int = 60,   # 设置RRF参数
        source_weights: dict[str, float] | None = None,
    ) -> None:
        # 工具统一注册到 LangGraph ToolNode，不再由编排器手工执行 BaseTool。
        self.registry = LangChainToolRegistry(tools)
        self.tools = self.registry.tools
        missing = sorted(set(source_tools) - self.tools.keys())
        if missing:
            raise ValueError(f"missing recall tools: {', '.join(missing)}")
        self.source_tools = source_tools
        self.rrf_k = rrf_k
        self.source_weights = dict(source_weights or {})
        if any(weight <= 0 for weight in self.source_weights.values()):
            raise ValueError("RRF source weights must be positive")
    # 单工具调用
    async def _invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: RequestContext,
    ) -> RecallCall:
        started = perf_counter()
        execution = await self.registry.invoke(tool_name, arguments, context)
        if execution.status != "ok":
            return RecallCall(
                tool=tool_name,
                arguments=arguments,
                status="error",
                data={},
                latency_ms=int((perf_counter() - started) * 1_000),
                source_version="unknown",
                error_code=execution.error_code,
                retryable=execution.error_code == "TOOL_TRANSIENT_ERROR",
                metadata=None,
            )
        output = execution.output
        metadata = dict(output.get("metadata", {}))
        retrieval_source = str(metadata.get("retrieval_source", tool_name))
        # Receipt 保存实际融合权重，便于复现实验与排查排序差异。
        metadata["rrf_weight"] = self.source_weights.get(retrieval_source, 1.0)
        return RecallCall(
            tool=tool_name,
            arguments=arguments,
            status=str(output.get("status", "error")),
            data=dict(output.get("data", {})),
            latency_ms=int((perf_counter() - started) * 1_000),
            source_version=str(output.get("source_version", "unknown")),
            error_code=output.get("error_code"),
            retryable=bool(output.get("retryable", False)),
            metadata=metadata,
        )

    async def recall(
        self,
        query: str,
        top_k: int,
        context: RequestContext,
        budget: ExecutionBudget,
    ) -> RecallResult:
        # 召回参数 top_k 乘 3：因为后续要 RRF 融合和约束过滤，需要冗余候选
        arguments = {
            "query": query,
            "top_k": min(top_k * 3, 50),
        }
        # 未登录请求没有稳定 user_id，不调用行为召回，避免无意义的工具成本。
        active_sources = tuple(
            source
            for source in self.source_tools
            if source != "behavior_recall" or context.user_id is not None
        )
        # 在执行前就扣除调用时间预算，防止超预算执行
        for _ in active_sources:
            budget.consume_tool_call()
        # 并行调用
        calls = tuple(await asyncio.gather(*(
            self._invoke(tool_name, dict(arguments), context)
            for tool_name in active_sources
        )))
        successful = [call for call in calls if call.status == "ok"]
        if not successful:
            raise RecallUnavailable(calls)
        # RRF融合
        fused: dict[str, dict[str, Any]] = {}
        for call in successful:
            for rank, candidate in enumerate(call.data.get("candidates", []), start=1):
                item_id = str(candidate["item_id"])
                # 语义工具显式标记 qwen/tfidf/fallback，Receipt 与最终候选可证明实际来源。
                candidate_source = str(candidate.get("source", call.tool))
                entry = fused.setdefault(item_id, {
                    "title": str(candidate["title"]),
                    "score": 0.0,
                    "source_scores": {},
                    "reasons": [],
                })
                entry["score"] += self.source_weights.get(candidate_source, 1.0) / (
                    self.rrf_k + rank
                )
                entry["source_scores"][candidate_source] = float(candidate["score"])
                entry["reasons"].append(f"recalled_by:{candidate_source}")
        # 排序
        ranked = [
            FusedCandidate(
                item_id=item_id,
                title=value["title"],
                score=value["score"],
                source_scores=value["source_scores"],
                reasons=tuple(value["reasons"]),
            )
            for item_id, value in fused.items()
            # 行为信号只能提升当前查询已召回的商品，不能单独引入无关历史商品。
            if set(value["source_scores"]) != {"behavior_recall"}
        ]
        ranked.sort(key=lambda item: (-item.score, item.item_id))
        return RecallResult(tuple(ranked), calls)

    async def recall_many(
        self,
        queries: Sequence[str],
        top_k: int,
        context: RequestContext,
        budget: ExecutionBudget,
    ) -> RecallResult:
        """Executes bounded plan queries in parallel and fuses their ranked lists."""
        unique_queries = tuple(dict.fromkeys(query.strip() for query in queries if query.strip()))
        results = await asyncio.gather(*(
            self.recall(query, top_k, context, budget) for query in unique_queries
        ))
        return self.fuse_results(results)

    def fuse_results(self, results: Sequence[RecallResult]) -> RecallResult:
        """用第二层 RRF 融合多个 DAG 节点，避免比较不同查询的原始分数。"""
        fused: dict[str, dict[str, Any]] = {}
        for query_index, result in enumerate(results, start=1):
            for rank, candidate in enumerate(result.candidates, start=1):
                entry = fused.setdefault(candidate.item_id, {
                    "title": candidate.title,
                    "score": 0.0,
                    "source_scores": {},
                    "reasons": [],
                })
                # A second RRF layer combines plan steps without comparing raw source scores.
                entry["score"] += 1.0 / (self.rrf_k + rank)
                entry["source_scores"].update(candidate.source_scores)
                entry["reasons"].extend(candidate.reasons)
                entry["reasons"].append(f"planned_query:{query_index}")
        candidates = [
            FusedCandidate(
                item_id=item_id,
                title=value["title"],
                score=value["score"],
                source_scores=value["source_scores"],
                reasons=tuple(dict.fromkeys(value["reasons"])),
            )
            for item_id, value in fused.items()
        ]
        candidates.sort(key=lambda item: (-item.score, item.item_id))
        return RecallResult(tuple(candidates), tuple(
            call for result in results for call in result.calls
        ))
