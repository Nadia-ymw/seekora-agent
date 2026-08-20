"""基于 LangGraph ToolNode 的标准 LangChain 工具注册与执行适配器。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode

from .contracts import RequestContext


@dataclass(frozen=True)
class ToolExecutionResult:
    """保留 ToolNode 的执行状态和结构化 Artifact，供应用层统一审计。"""

    output: dict[str, Any]
    status: str
    error_code: str | None = None


class LangChainToolRegistry:
    """把 BaseTool 注册到 ToolNode，并通过 Runtime 注入可信请求上下文。"""

    def __init__(self, tools: Sequence[BaseTool]) -> None:
        self.tools: dict[str, BaseTool] = {}
        for registered_tool in tools:
            if registered_tool.name in self.tools:
                raise ValueError(f"tool already registered: {registered_tool.name}")
            self.tools[registered_tool.name] = registered_tool

        builder = StateGraph(MessagesState, context_schema=RequestContext)
        # ToolNode 自动注入 ToolRuntime，并把 runtime 参数从模型可见 Schema 中排除。
        builder.add_node(
            "tools",
            ToolNode(
                list(self.tools.values()),
                # 只把可恢复的临时故障转换为 ToolMessage；程序错误继续抛出。
                handle_tool_errors=(TimeoutError, ConnectionError),
            ),
        )
        builder.add_edge(START, "tools")
        builder.add_edge("tools", END)
        self.graph = builder.compile(name="seekora_tool_registry")

    async def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: RequestContext,
    ) -> ToolExecutionResult:
        if tool_name not in self.tools:
            raise KeyError(f"tool not registered: {tool_name}")
        tool_call_id = uuid4().hex
        result = await self.graph.ainvoke(
            {
                "messages": [AIMessage(content="", tool_calls=[{
                    "name": tool_name,
                    "args": arguments,
                    "id": tool_call_id,
                    "type": "tool_call",
                }])]
            },
            context=context,
        )
        message = next((
            entry
            for entry in reversed(result["messages"])
            if isinstance(entry, ToolMessage) and entry.tool_call_id == tool_call_id
        ), None)
        if message is None:
            return ToolExecutionResult({}, "error", "MISSING_TOOL_MESSAGE")
        if message.status == "error":
            return ToolExecutionResult({}, "error", "TOOL_TRANSIENT_ERROR")
        if not isinstance(message.artifact, dict):
            return ToolExecutionResult({}, "error", "INVALID_TOOL_OUTPUT")
        return ToolExecutionResult(dict(message.artifact), "ok")
