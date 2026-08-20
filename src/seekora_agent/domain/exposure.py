"""服务端曝光清单领域模型，用于约束后续反馈归因。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ExposedItem:
    """曝光批次中的单个商品及其服务端可信位置和召回来源。"""

    item_id: str
    position: int
    recall_sources: tuple[str, ...] = ()
    source_scores: tuple[tuple[str, float], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "position": self.position,
            "recall_sources": list(self.recall_sources),
            "source_scores": dict(self.source_scores),
        }


@dataclass(frozen=True)
class ExposureRecord:
    """一次结果展示的不可变服务端清单，exposure_id 在租户内唯一。"""

    exposure_id: str
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    items: tuple[ExposedItem, ...]
    model_version: str
    created_at: str = field(default_factory=utc_now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "exposure_id": self.exposure_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "items": [item.as_dict() for item in self.items],
            "model_version": self.model_version,
            "created_at": self.created_at,
        }
