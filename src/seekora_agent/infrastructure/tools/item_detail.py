"""用于最终结果补全和证据生成的只读 Item Detail 工具。"""

from __future__ import annotations

from typing import Annotated

from langchain.tools import ToolRuntime, tool
from pydantic import Field

from ...application.catalog import CatalogRepository
from ...application.constraints import visibility_failure
from ...application.contracts import RequestContext


def build_item_detail_tool(
    catalog: CatalogRepository,
    source_version: str = "catalog-detail-v1",
):
    @tool(
        "item_detail",
        response_format="content_and_artifact",
    )
    async def item_detail(
        item_ids: Annotated[
            list[str],
            Field(min_length=1, max_length=50, description="需要获取详情的商品 ID 列表"),
        ],
        runtime: ToolRuntime[RequestContext],
    ) -> tuple[str, dict]:
        """批量读取权威目录中的商品详情，用于结果展示和可追溯解释。"""
        details: list[dict] = []
        omitted = 0
        # 去重并保持候选原顺序，避免模型或调用方借重复 ID 放大响应。
        for item_id in dict.fromkeys(item_ids):
            item = await catalog.get(item_id)
            if item is None or visibility_failure(item, runtime.context) is not None:
                omitted += 1
                continue
            details.append({
                "item_id": item.item_id,
                "title": item.title,
                "description": item.description,
                "category": item.category,
                "attributes": dict(item.attributes),
                "source_uri": f"catalog://item/{item.item_id}",
                "observed_at": item.updated_at.isoformat(),
                "trust_level": (
                    "synthetic"
                    if item.attributes.get("synthetic_test_data")
                    else "authoritative"
                ),
            })
        artifact = {
            "status": "ok",
            "source_version": source_version,
            "items": details,
            "omitted_count": omitted,
        }
        return f"已读取 {len(details)} 个可见商品详情", artifact

    return item_detail
