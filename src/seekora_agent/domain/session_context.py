"""多轮约束 Patch 及合并结果的领域契约。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .fast_path import ResolvedIntent
from .models import Constraint


TaskRelation = Literal["new_task", "follow_up"]
PatchAction = Literal["set", "add", "remove", "clear"]


@dataclass(frozen=True)
class ConstraintPatchOperation:
    """描述一项约束变更，解析器不能直接修改 Session。"""

    action: PatchAction
    field: str | None = None
    constraint: Constraint | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"action": self.action}
        if self.field is not None:
            payload["field"] = self.field
        if self.constraint is not None:
            payload["constraint"] = {
                "field": self.constraint.field,
                "operator": self.constraint.operator,
                "value": self.constraint.value,
            }
        return payload


@dataclass(frozen=True)
class ConstraintPatch:
    """AI 或降级规则输出的变更集合，不包含最终合并状态。"""

    task_relation: TaskRelation
    operations: tuple[ConstraintPatchOperation, ...] = ()
    use_previous_query: bool = False
    parser_version: str = "unknown"


@dataclass(frozen=True)
class SessionContextResult:
    """记录合并结果，供 SSE 事件和 Receipt 审计。"""

    intent: ResolvedIntent
    applied: bool = False
    previous_request_id: str | None = None
    inherited_fields: tuple[str, ...] = ()
    replaced_fields: tuple[str, ...] = ()
    removed_fields: tuple[str, ...] = ()
    constraints_cleared: bool = False
    parser_version: str = "not-invoked"
    operations: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "previous_request_id": self.previous_request_id,
            "inherited_fields": list(self.inherited_fields),
            "replaced_fields": list(self.replaced_fields),
            "removed_fields": list(self.removed_fields),
            "constraints_cleared": self.constraints_cleared,
            "parser_version": self.parser_version,
            "operations": list(self.operations),
        }
