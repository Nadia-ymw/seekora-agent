"""执行结构化 ConstraintPatch 的确定性 Session Reducer。"""

from __future__ import annotations

from typing import Any, Protocol

from ..domain.fast_path import ResolvedIntent
from ..domain.models import Constraint
from ..domain.session_context import (
    ConstraintPatch,
    ConstraintPatchOperation,
    SessionContextResult,
)

# 重新导出 Patch 类型，兼容已经从应用模块导入这些契约的调用方。
__all__ = [
    "ConstraintPatch",
    "ConstraintPatchOperation",
    "SessionContextPatchResolver",
    "SessionContextResolver",
    "SessionContextResult",
]

_ALLOWED_FIELDS = {
    "price", "memory_gb", "battery_hours", "weight_kg", "category"
}
_ALLOWED_OPERATORS = {"eq", "in", "lte", "gte"}


class SessionContextPatchResolver(Protocol):
    async def resolve(
        self, query: str, current: ResolvedIntent, previous: ResolvedIntent
    ) -> ConstraintPatch: ...


class NewTaskPatchResolver:
    """安全默认值：没有装配解析器时不继承历史约束。"""

    async def resolve(
        self, query: str, current: ResolvedIntent, previous: ResolvedIntent
    ) -> ConstraintPatch:
        return ConstraintPatch(task_relation="new_task", parser_version="new-task-v1")


class SessionContextResolver:
    """校验并执行 Patch；该类不分析自然语言，也不读取长期画像。"""

    def __init__(self, patch_resolver: SessionContextPatchResolver | None = None) -> None:
        self.patch_resolver = patch_resolver or NewTaskPatchResolver()

    async def merge(
        self,
        query: str,
        current: ResolvedIntent,             # 本轮解析出的意图
        previous_intent: dict[str, object] | None,          #上一轮意图的字典形式
        previous_request_id: str | None = None,
    ) -> SessionContextResult:
        # 如果没有历史，直接返回当前结果，不进行任何合并。
        if previous_intent is None:
            return SessionContextResult(intent=current)

        previous = ResolvedIntent.from_dict(previous_intent)
        patch = await self.patch_resolver.resolve(query, current, previous)
        if patch.task_relation not in {"new_task", "follow_up"}:
            raise ValueError(f"unsupported task relation: {patch.task_relation}")
        if patch.task_relation == "new_task":
            return SessionContextResult(
                intent=current,
                parser_version=patch.parser_version,
                operations=tuple(item.as_dict() for item in patch.operations),
            )

        # 将上一轮的硬约束 (previous.hard_constraints) 和本次的操作指令 (patch.operations) 进行合并，
        # 得到新的约束列表以及被替换/移除/清空的字段信息。
        constraints, replaced, removed, cleared = self._apply_operations(
            previous.hard_constraints, patch.operations
        )
        # 收集所有被操作涉及的字段名
        changed_fields = {
            operation.constraint.field
            for operation in patch.operations
            if operation.constraint is not None
        }
        # 如果本轮没有指定领域，则继承上一轮的。
        domain = current.domain or previous.domain
        # 构建合并后的完整意图对象
        merged = ResolvedIntent(
            # 如果 use_previous_query 为真，则复用上一轮的；否则使用本轮的
            mode=previous.mode if patch.use_previous_query else current.mode,
            domain=domain,
            retrieval_query=(
                previous.retrieval_query
                if patch.use_previous_query else current.retrieval_query
            ),
            # 使用刚刚计算出的新约束列表
            hard_constraints=constraints,
            # 将上一轮和本轮的软偏好/负面偏好合并，并通过 _deduplicate 去重
            soft_preferences=self._deduplicate(
                (*previous.soft_preferences, *current.soft_preferences)
            ),
            negative_preferences=self._deduplicate(
                (*previous.negative_preferences, *current.negative_preferences)
            ),
            # 取两者置信度的最小值（保守策略）
            confidence=min(previous.confidence, current.confidence),
            ambiguities=tuple(
                value for value in current.ambiguities
                if not (value == "domain" and domain)
            ),
            resolver_version=f"{current.resolver_version}+session-reducer-v2",
        )
        inherited = {rule.field for rule in constraints if rule.field not in changed_fields}
        return SessionContextResult(
            intent=merged,
            applied=True,
            previous_request_id=previous_request_id,
            inherited_fields=tuple(sorted(inherited)),
            replaced_fields=tuple(sorted(replaced)),
            removed_fields=tuple(sorted(removed)),
            constraints_cleared=cleared,
            parser_version=patch.parser_version,
            operations=tuple(item.as_dict() for item in patch.operations),
        )

    @classmethod
    def _apply_operations(
        cls,
        previous: tuple[Constraint, ...],
        operations: tuple[ConstraintPatchOperation, ...],
    ) -> tuple[tuple[Constraint, ...], set[str], set[str], bool]:
        constraints, replaced, removed, cleared = list(previous), set(), set(), False
        # 将旧约束转为可变列表 constraints
        for operation in operations:
            if operation.action == "clear":
                constraints, cleared = [], True
                continue
            if operation.action == "remove":
                field = cls._validate_field(operation.field)
                constraints = [rule for rule in constraints if rule.field != field]
                removed.add(field)
                continue

            constraint = cls._validate_constraint(operation.constraint)
            if operation.action == "set":
                if any(rule.field == constraint.field for rule in constraints):
                    replaced.add(constraint.field)
                constraints = [rule for rule in constraints if rule.field != constraint.field]
            elif operation.action == "add":
                constraints = [
                    rule for rule in constraints
                    if (rule.field, rule.operator)
                    != (constraint.field, constraint.operator)
                ]
            else:
                raise ValueError(f"unsupported patch action: {operation.action}")
            constraints.append(constraint)
        return tuple(constraints), replaced, removed, cleared

    @staticmethod
    def _validate_field(field: str | None) -> str:
        if field not in _ALLOWED_FIELDS:
            raise ValueError(f"unsupported constraint field: {field}")
        return field

    @classmethod
    def _validate_constraint(cls, constraint: Constraint | None) -> Constraint:
        if constraint is None:
            raise ValueError("set/add operation requires a constraint")
        field = cls._validate_field(constraint.field)
        if constraint.operator not in _ALLOWED_OPERATORS:
            raise ValueError(f"unsupported constraint operator: {constraint.operator}")
        return Constraint(
            field,
            constraint.operator,
            cls._normalize_value(field, constraint.operator, constraint.value),
        )

    @staticmethod
    def _normalize_value(field: str, operator: str, value: Any) -> Any:
        values = value if operator == "in" else [value]
        if operator == "in" and not isinstance(values, (list, tuple)):
            raise ValueError("in operator requires a list value")
        converter = (
            int if field == "memory_gb"
            else float if field in {"price", "battery_hours", "weight_kg"}
            else str
        )
        normalized = [converter(item) for item in values]
        return normalized if operator == "in" else normalized[0]

    @staticmethod
    def _deduplicate(values: tuple[str, ...]) -> tuple[str, ...]:
        # dict 保留首次出现顺序，让事件和回放结果保持确定性。
        return tuple(dict.fromkeys(value for value in values if value))
