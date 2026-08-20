"""基于已授权用户行为的 LangChain 召回工具。"""

from __future__ import annotations

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ...application.behavior import BehaviorService
from ...domain.models import Item


class BehaviorRecallInput(BaseModel):
    query: str = Field(min_length=1, description="当前查询；行为召回不直接解析其文本")
    top_k: int = Field(default=10, ge=1, le=50)
    tenant_id: str = Field(min_length=1)
    user_id: str | None = None
    allowed_permission_tags: list[str] = Field(default_factory=lambda: ["public"])


def build_behavior_recall_tool(
    behavior_service: BehaviorService,
    items: list[Item],
    source_version: str = "behavior-memory-v1",
) -> BaseTool:
    catalog = {(item.tenant_id, item.item_id): item for item in items}

    async def behavior_recall(
        query: str,
        top_k: int,
        tenant_id: str,
        user_id: str | None = None,
        allowed_permission_tags: list[str] | None = None,
    ) -> dict:
        """仅在双重授权下召回用户产生过正向行为的有效目录商品。"""
        del query
        if user_id is None:
            return {
                "status": "ok",
                "source_version": source_version,
                "data": {"candidates": []},
            }
        # 从行为服务获取用户对物品的正向行为分数（如点击、收藏、转化等加权得分）
        scores = await behavior_service.item_scores(tenant_id, user_id)
        allowed = set(allowed_permission_tags or ["public"])
        candidates: list[dict] = []
        for item_id, score in scores.items():
            item = catalog.get((tenant_id, item_id))
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
        return {
            "status": "ok",
            "source_version": source_version,
            "data": {"candidates": candidates[:top_k]},
        }
    # 使用 StructuredTool.from_function 将异步函数包装为 LangChain 工具。
    return StructuredTool.from_function(
        coroutine=behavior_recall,
        name="behavior_recall",
        description="Recall ACL-safe catalog items from consented positive user behavior.",
        args_schema=BehaviorRecallInput,
    )
