"""SSE-oriented runtime around the compiled LangChain/LangGraph Fast Path.

实现了一个面向 SSE（Server-Sent Events）的 Agent 运行时，封装编译后的
LangGraph 工作流，并提供请求生命周期管理、事件流式输出、状态持久化和异常处理。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

from .contracts import AgentEvent, AgentQuery, BudgetExceeded, ExecutionBudget, RequestCancelled
from .profile import ProfileService
from .receipt import ReceiptStore, RecommendationReceipt, ToolCallReceipt
from .session import CancellationRegistry, SessionIntentSnapshot, SessionMessage, SessionStore
from .workflow import FastPathState, LangChainFastPathWorkflow


class AgentRuntime:
    def __init__(
        self,
        workflow: LangChainFastPathWorkflow,
        sessions: SessionStore,
        receipts: ReceiptStore,
        cancellations: CancellationRegistry,
        profiles: ProfileService | None = None,
    ) -> None:
        self.workflow = workflow
        self.sessions = sessions
        self.receipts = receipts
        self.cancellations = cancellations
        self.profiles = profiles

    async def _ensure_active(self, request_id: str) -> None:
        if await self.cancellations.is_cancelled(request_id):
            raise RequestCancelled("request cancelled")

    async def run(self, query: AgentQuery) -> AsyncIterator[AgentEvent]:
        request_id = uuid4().hex
        # 创建执行预算（包含最大工具调用次数、截止时间等）
        budget = ExecutionBudget()
        receipt = RecommendationReceipt(
            request_id=request_id,
            session_id=query.session_id,
            tenant_id=query.tenant_id,
            query=query.query,
        )
        session = await self.sessions.get_or_create(
            query.session_id, query.tenant_id, query.user_id
        )
        await self.sessions.append(session, SessionMessage("user", query.query, request_id))
        yield AgentEvent("request.accepted", request_id, {
            "session_id": query.session_id,
            "route": "pending",
            "framework": "langchain/langgraph",
            "budget": {"tool_calls": budget.max_tool_calls, "deadline_ms": budget.deadline_ms},
        })

        final_items: list[dict] = []
        terminal_status = "completed"
        # 构建工作流状态
        state: FastPathState = {
            "request_id": request_id,
            "query": query.query,
            "tenant_id": query.tenant_id,
            "user_id": query.user_id,
            "allowed_permission_tags": query.allowed_permission_tags,
            "top_k": query.top_k,
            "budget": budget,
            "replan_count": 0,
        }
        try:
            await self._ensure_active(request_id)
            # 核心工作流
            async for update in self.workflow.astream(state):
                await self._ensure_active(request_id)
                node_name, payload = next(iter(update.items()))
                # 意图解析
                if node_name == "resolve_intent":
                    intent = payload["intent"]
                    receipt.resolved_intent = intent.as_dict()
                    # 解析结果属于本次会话任务；即使存在用户画像，也禁止在这里隐式写入。
                    await self.sessions.set_intent(
                        session,
                        SessionIntentSnapshot(request_id, intent.as_dict()),
                    )
                    yield AgentEvent("intent.resolved", request_id, intent.as_dict())
                elif node_name == "route":
                    decision = payload["route_decision"]
                    receipt.route = decision.route
                    receipt.route_decision = decision.as_dict()
                    yield AgentEvent("routing.completed", request_id, decision.as_dict())
                    if decision.route == "fast":
                        yield AgentEvent("recall.started", request_id, {
                            "sources": list(self.workflow.recall.source_tools), "route": "fast",
                        })
                elif node_name in {"probe", "escalate_probe"}:
                    summary = payload["probe_summary"]
                    probe_result = payload["probe_result"]
                    if node_name == "escalate_probe":
                        decision = payload["route_decision"]
                        receipt.route = decision.route
                        receipt.route_decision = decision.as_dict()
                        yield AgentEvent("routing.escalated", request_id, decision.as_dict())
                    receipt.probe_summary = summary.as_dict()
                    self._record_tool_calls(receipt, probe_result.calls)
                    yield AgentEvent("probe.completed", request_id, summary.as_dict())
                elif node_name == "plan":
                    plan = payload["plan"]
                    receipt.plan = plan.as_dict()
                    yield AgentEvent("plan.created", request_id, plan.as_dict())
                    yield AgentEvent("recall.started", request_id, {
                        "sources": list(self.workflow.recall.source_tools),
                        "route": "deep",
                        "queries": [step.query for step in plan.steps],
                    })
                elif node_name == "replan":
                    plan = payload["plan"]
                    receipt.plan = plan.as_dict()
                    receipt.replan_count = payload["replan_count"]
                    yield AgentEvent("plan.replanned", request_id, plan.as_dict())
                    yield AgentEvent("recall.started", request_id, {
                        "sources": list(self.workflow.recall.source_tools),
                        "route": "deep",
                        "queries": [step.query for step in plan.steps],
                        "revision": plan.revision,
                    })
                # 召回
                elif node_name in {"recall", "deep_recall"}:
                    recall_result = payload["recall_result"]
                    self._record_tool_calls(receipt, recall_result.calls)
                    if node_name == "deep_recall":
                        summary = payload["dag_execution"].summary
                        receipt.dag_executions.append(summary.as_dict())
                        yield AgentEvent("dag.completed", request_id, summary.as_dict())
                    yield AgentEvent("recall.completed", request_id, {
                        "candidate_count": len(recall_result.candidates),
                        "sources": {
                            call.tool: {
                                "status": call.status,
                                "latency_ms": call.latency_ms,
                                "source_version": call.source_version,
                            }
                            for call in recall_result.calls
                        },
                    })
                elif node_name == "apply_constraints": # 约束过滤
                    filtered = payload["filter_result"]
                    receipt.filtered_reason_counts = filtered.filtered_reason_counts
                    yield AgentEvent("constraints.applied", request_id, {
                        "accepted_count": min(len(filtered.accepted), query.top_k),
                        "filtered_reason_counts": filtered.filtered_reason_counts,
                    })
                elif node_name == "assess_sufficiency":
                    assessment = payload["sufficiency"]
                    receipt.sufficiency_assessments.append(assessment.as_dict())
                    yield AgentEvent("sufficiency.assessed", request_id, assessment.as_dict())
                # 结果组装
                elif node_name == "compose_result":
                    final_items = payload["items"]
                    receipt.candidate_ids = [str(item["item_id"]) for item in final_items]
                    yield AgentEvent("result", request_id, {"items": final_items})
                elif node_name == "compose_terminal":
                    final_items = payload["items"]
                    decision = payload["terminal_decision"]
                    receipt.candidate_ids = []
                    receipt.terminal_decision = decision.as_dict()
                    terminal_status = (
                        "clarification_required" if decision.action == "clarify" else "refused"
                    )
                    event_name = (
                        "clarification.required"
                        if decision.action == "clarify"
                        else "response.refused"
                    )
                    yield AgentEvent(event_name, request_id, decision.as_dict())
                    yield AgentEvent("result", request_id, {
                        "items": [], "decision": decision.as_dict(),
                    })

            await self.sessions.append(
                session,
                SessionMessage("assistant", f"returned {len(final_items)} items", request_id),
            )
            # 完成收据，标记完成
            receipt.finish(terminal_status)
        # 异常处理
        except RequestCancelled:
            receipt.finish("cancelled", "REQUEST_CANCELLED")
            yield AgentEvent("cancelled", request_id, {"error_code": "REQUEST_CANCELLED"})
        except BudgetExceeded as exc:
            receipt.finish("degraded", "BUDGET_EXCEEDED")
            yield AgentEvent("error", request_id, {
                "error_code": "BUDGET_EXCEEDED", "message": str(exc), "retryable": False,
            })
        except Exception as exc:
            receipt.finish("failed", "AGENT_RUNTIME_ERROR")
            yield AgentEvent("error", request_id, {
                "error_code": "AGENT_RUNTIME_ERROR", "message": str(exc), "retryable": False,
            })
        finally:
            await self.receipts.put(receipt)
            await self.cancellations.clear(request_id)

        yield AgentEvent("done", request_id, {
            "status": receipt.status,
            "receipt_id": request_id,
            "elapsed_ms": budget.elapsed_ms,
            "tool_calls": budget.tool_calls,
        })

    @staticmethod
    def _record_tool_calls(receipt: RecommendationReceipt, calls: tuple) -> None:
        """Copies external call metadata into the replayable receipt."""
        for call in calls:
            receipt.tool_calls.append(ToolCallReceipt(
                tool=call.tool,
                arguments=call.arguments,
                status=call.status,
                latency_ms=call.latency_ms,
                source_version=call.source_version,
                error_code=call.error_code,
            ))
