"""基于已授权用户行为的 LangChain 召回工具。"""

from __future__ import annotations

from typing import Annotated

from langchain.tools import ToolRuntime, tool
from langchain_core.tools import BaseTool
from pydantic import Field

from ...application.behavior import BehaviorService
from ...application.contracts import RequestContext
from ...domain.models import Item


def build_behavior_recall_tool(
    behavior_service: BehaviorService,
    items: list[Item],
    source_version: str = "behavior-memory-v1",
) -> BaseTool:
    catalog = {(item.tenant_id, item.item_id): item for item in items}

    @tool(
        "behavior_recall",
        response_format="content_and_artifact",
    )
    async def behavior_recall(
        query: Annotated[
            str, Field(min_length=1, description="当前查询；行为召回不直接解析其文本")
        ],
        runtime: ToolRuntime[RequestContext],
        top_k: Annotated[
            int, Field(ge=1, le=50, description="最多返回的候选数量")
        ] = 10,
    ) -> tuple[str, dict]:
        """仅在双重授权下召回用户产生过正向行为的有效目录商品。"""
        del query
        context = runtime.context
        user_id = context.user_id
        if user_id is None:
            artifact = {
                "status": "ok",
                "source_version": source_version,
                "data": {"candidates": []},
            }
            return "匿名请求没有行为召回候选", artifact
        # 从行为服务获取用户对物品的正向行为分数（如点击、收藏、转化等加权得分）
        scores = await behavior_service.item_scores(context.tenant_id, user_id)
        allowed = set(context.allowed_permission_tags)
        candidates: list[dict] = []
        for item_id, score in scores.items():
            item = catalog.get((context.tenant_id, item_id))
            # 行为记录不能绕过目录状态、租户和 ACL 的最终信任边界。
            if item is None or item.status != "active":
                continue
            if not allowed.intersection(item.permission_tags):
                continue
            candidates.append({
                "item_id": item.item_id,
                "title": item.title,
                "score": score,
                "reasons": ["positive_behavior"],
            })
        candidates.sort(key=lambda candidate: (-candidate["score"], candidate["item_id"]))
        artifact = {
            "status": "ok",
            "source_version": source_version,
            "data": {"candidates": candidates[:top_k]},
        }
        return f"行为召回 {min(len(candidates), top_k)} 个候选", artifact

    return behavior_recall
