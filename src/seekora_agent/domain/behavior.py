"""用户行为反馈领域对象，定义可审计事件和幂等写入结果。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


BehaviorAction = Literal["exposure", "click", "favorite", "dismiss", "conversion"]
SUPPORTED_ACTIONS = {"exposure", "click", "favorite", "dismiss", "conversion"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()

# 用户行为反馈领域对象
@dataclass(frozen=True)
class BehaviorEvent:
    """一次不可变用户行为；event_id 在同一租户内充当幂等键。"""

    event_id: str
    tenant_id: str
    user_id: str
    session_id: str
    request_id: str
    exposure_id: str    # 曝光标识，用于关联曝光和后续点击等
    item_id: str
    action: BehaviorAction
    occurred_at: str
    position: int | None = None
    recall_sources: tuple[str, ...] = ()
    model_version: str = "unknown"
    received_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        required = {
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "exposure_id": self.exposure_id,
            "item_id": self.item_id,
            "occurred_at": self.occurred_at,
        }
        if any(not value.strip() for value in required.values()):
            raise ValueError("behavior event required fields must not be empty")
        if self.action not in SUPPORTED_ACTIONS:
            raise ValueError(f"unsupported behavior action: {self.action}")
        if self.position is not None and self.position < 0:
            raise ValueError("position must be greater than or equal to zero")
        try:
            occurred_at = datetime.fromisoformat(self.occurred_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("occurred_at must be an ISO-8601 datetime") from exc
        if occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")

    def idempotency_payload(self) -> tuple[Any, ...]:
        """忽略服务端接收时间，只比较调用方提交的业务载荷。"""
        return (
            self.user_id,
            self.session_id,
            self.request_id,
            self.exposure_id,
            self.item_id,
            self.action,
            self.occurred_at,
            self.position,
            self.recall_sources,
            self.model_version,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "exposure_id": self.exposure_id,
            "item_id": self.item_id,
            "action": self.action,
            "occurred_at": self.occurred_at,
            "position": self.position,
            "recall_sources": list(self.recall_sources),
            "model_version": self.model_version,
            "received_at": self.received_at,
        }


@dataclass(frozen=True)
class BehaviorWriteResult:
    event: BehaviorEvent
    duplicate: bool

    def as_dict(self) -> dict[str, Any]:
        return {"duplicate": self.duplicate, "event": self.event.as_dict()}
