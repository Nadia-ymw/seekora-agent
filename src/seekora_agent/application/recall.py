"""Parallel LangChain-tool recall and deterministic Reciprocal Rank Fusion."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Sequence

from langchain_core.tools import BaseTool

from ..domain.fast_path import FusedCandidate
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
    ) -> None:
        # 工具注册
        self.tools: dict[str, BaseTool] = {}
        for tool in tools:
            if tool.name in self.tools:
                raise ValueError(f"tool already registered: {tool.name}")
            self.tools[tool.name] = tool
        missing = sorted(set(source_tools) - self.tools.keys())
        if missing:
            raise ValueError(f"missing recall tools: {', '.join(missing)}")
        self.source_tools = source_tools
        self.rrf_k = rrf_k
    # 单工具调用
    async def _invoke(self, tool_name: str, arguments: dict[str, Any]) -> RecallCall:
        started = perf_counter()
        try:
            output = await self.tools[tool_name].ainvoke(arguments)
            if not isinstance(output, dict):
                raise TypeError(f"tool {tool_name} must return a dict")
            return RecallCall(
                tool=tool_name,
                arguments=arguments,
                status=str(output.get("status", "error")),
                data=dict(output.get("data", {})),
                latency_ms=int((perf_counter() - started) * 1_000),
                source_version=str(output.get("source_version", "unknown")),
                error_code=output.get("error_code"),
                retryable=bool(output.get("retryable", False)),
            )
        except Exception:
            return RecallCall(
                tool=tool_name,
                arguments=arguments,
                status="error",
                data={},
                latency_ms=int((perf_counter() - started) * 1_000),
                source_version="unknown",
                error_code="TOOL_EXECUTION_ERROR",
                retryable=True,
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
            "tenant_id": context.tenant_id,
            "user_id": context.user_id,
            "allowed_permission_tags": list(context.allowed_permission_tags),
        }
        # 在执行前就扣除调用时间预算，防止超预算执行
        for _ in self.source_tools:
            budget.consume_tool_call()
        # 并行调用
        calls = tuple(await asyncio.gather(*(
            self._invoke(tool_name, dict(arguments)) for tool_name in self.source_tools
        )))
        successful = [call for call in calls if call.status == "ok"]
        if not successful:
            raise RecallUnavailable(calls)
        # RRF融合
        fused: dict[str, dict[str, Any]] = {}
        for call in successful:
            for rank, candidate in enumerate(call.data.get("candidates", []), start=1):
                item_id = str(candidate["item_id"])
                entry = fused.setdefault(item_id, {
                    "title": str(candidate["title"]),
                    "score": 0.0,
                    "source_scores": {},
                    "reasons": [],
                })
                entry["score"] += 1.0 / (self.rrf_k + rank)
                entry["source_scores"][call.tool] = float(candidate["score"])
                entry["reasons"].append(f"recalled_by:{call.tool}")
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
