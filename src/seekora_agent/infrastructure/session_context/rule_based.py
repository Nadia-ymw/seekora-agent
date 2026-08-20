"""结构化 AI 不可用时使用的轻量多轮变更规则。"""

from __future__ import annotations

import re

from ...application.session_context import ConstraintPatch, ConstraintPatchOperation
from ...domain.fast_path import ResolvedIntent


_NEW_TASK = ("重新搜索", "重新找", "换个需求", "开始新的", "新需求")
_CLEAR = ("清空条件", "清除条件", "重置条件", "取消所有限制")
_FOLLOW_UP = (
    "改成", "调整为", "修改为", "换成", "再加", "加上", "还要",
    "去掉", "取消", "删除", "不限",
)
_ADD = ("再加", "加上", "还要")
_REMOVE = ("去掉", "取消", "删除", "不限")
_ALIASES = {
    "price": ("价格", "预算", "价位"),
    "memory_gb": ("内存", "运存"),
    "battery_hours": ("续航", "电池"),
    "weight_kg": ("重量",),
    "category": ("类别", "类目", "品类"),
}


class RuleBasedSessionContextPatchResolver:
    """只提供离线降级能力，线上结构化 AI 失败时也可安全回退。"""

    version = "session-rules-zh-v2"

    async def resolve(
        self,
        query: str,
        current: ResolvedIntent,
        previous: ResolvedIntent,
    ) -> ConstraintPatch:
        if any(marker in query for marker in _NEW_TASK):
            return self._new_task()

        clear = any(marker in query for marker in _CLEAR)
        removed = self._removed_fields(query)
        constraint_only = bool(
            current.hard_constraints and self._constraint_only(query)
        )
        follow_up = bool(
            clear or removed or constraint_only
            or any(marker in query for marker in _FOLLOW_UP)
        )
        if not follow_up:
            return self._new_task()

        operations: list[ConstraintPatchOperation] = []
        if clear:
            operations.append(ConstraintPatchOperation("clear"))
        operations.extend(
            ConstraintPatchOperation("remove", field=field)
            for field in sorted(removed)
        )
        action = "add" if any(marker in query for marker in _ADD) else "set"
        operations.extend(
            ConstraintPatchOperation(action, constraint=rule)
            for rule in current.hard_constraints
            if rule.field not in removed and not clear
        )
        return ConstraintPatch(
            task_relation="follow_up",
            operations=tuple(operations),
            use_previous_query=bool(current.domain is None and operations),
            parser_version=self.version,
        )

    def _new_task(self) -> ConstraintPatch:
        return ConstraintPatch(task_relation="new_task", parser_version=self.version)

    @staticmethod
    def _removed_fields(query: str) -> set[str]:
        if not any(marker in query for marker in _REMOVE):
            return set()
        return {
            field for field, aliases in _ALIASES.items()
            if any(alias in query for alias in aliases)
        }

    @staticmethod
    def _constraint_only(query: str) -> bool:
        cleaned = query.lower()
        words = {
            *[alias for aliases in _ALIASES.values() for alias in aliases],
            *_FOLLOW_UP, "以内", "以下", "不超过", "以上", "起", "元",
            "小时", "公斤", "gb", "kg",
        }
        for word in sorted(words, key=len, reverse=True):
            cleaned = cleaned.replace(word, "")
        return not re.sub(r"[\d.\s,，。！？!?、]+", "", cleaned)
