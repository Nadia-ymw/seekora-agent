"""Deterministic hard-constraint and final catalog validation service."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from ..domain.fast_path import ConstraintFilterResult, FusedCandidate, ResolvedIntent, VerifiedCandidate
from ..domain.models import Constraint, Item
from .catalog import CatalogRepository
from .contracts import RequestContext


def constraint_matches(item: Item, rule: Constraint) -> bool:
    actual = item.field_value(rule.field)
    if actual is None:
        return False
    try:
        if rule.operator == "eq":
            return actual == rule.value
        if rule.operator == "in":
            return actual in rule.value
        if rule.operator == "lte":
            return actual <= rule.value
        if rule.operator == "gte":
            return actual >= rule.value
    except TypeError:
        return False
    return False


def active_constraints(
    constraints: tuple[Constraint, ...], at: datetime | None = None
) -> tuple[Constraint, ...]:
    """只返回当前可执行约束；挂起和过期约束保留用于审计/恢复。"""
    current = at or datetime.now(UTC)
    return tuple(rule for rule in constraints if rule.is_active(current))


def detect_constraint_conflicts(
    constraints: tuple[Constraint, ...], at: datetime | None = None
) -> tuple[dict[str, Any], ...]:
    """检测同字段上可确定的矛盾，不尝试猜测单位或改写用户条件。"""
    grouped: defaultdict[str, list[Constraint]] = defaultdict(list)
    for rule in active_constraints(constraints, at):
        grouped[rule.field].append(rule)

    conflicts: list[dict[str, Any]] = []
    for field, rules in sorted(grouped.items()):
        equals = [rule for rule in rules if rule.operator == "eq"]
        included = [rule for rule in rules if rule.operator == "in"]
        lowers = [rule for rule in rules if rule.operator == "gte"]
        uppers = [rule for rule in rules if rule.operator == "lte"]
        reason: str | None = None
        involved: list[Constraint] = []
        if len({repr(rule.value) for rule in equals}) > 1:
            reason, involved = "multiple_equal_values", equals
        else:
            try:
                lower = max(lowers, key=lambda rule: rule.value) if lowers else None
                upper = min(uppers, key=lambda rule: rule.value) if uppers else None
                equal = equals[0] if equals else None
                allowed_sets = [set(rule.value) for rule in included]
                allowed = set.intersection(*allowed_sets) if allowed_sets else None
                if allowed is not None and not allowed:
                    reason, involved = "allowed_values_do_not_overlap", included
                elif equal and allowed is not None and equal.value not in allowed:
                    reason, involved = "equal_value_not_allowed", [equal, *included]
                elif lower and upper and lower.value > upper.value:
                    reason, involved = "lower_bound_exceeds_upper_bound", [lower, upper]
                elif equal and lower and equal.value < lower.value:
                    reason, involved = "equal_value_below_lower_bound", [equal, lower]
                elif equal and upper and equal.value > upper.value:
                    reason, involved = "equal_value_above_upper_bound", [equal, upper]
            except TypeError:
                reason, involved = "incomparable_values", rules
        if reason:
            conflicts.append({
                "field": field,
                "reason": reason,
                "constraints": [rule.as_dict() for rule in involved],
            })
    return tuple(conflicts)


def minimum_relaxation_suggestions(
    constraints: tuple[Constraint, ...],
    filtered_reason_counts: dict[str, int],
    conflicts: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    """提出单约束删除建议；建议永远不直接修改已确认的硬约束。"""
    active = active_constraints(constraints)
    candidates: list[tuple[int, int, Constraint, str]] = []
    conflict_fields = {str(item["field"]) for item in conflicts}
    for rule in active:
        filtered = filtered_reason_counts.get(f"CONSTRAINT_{rule.field.upper()}", 0)
        if rule.field in conflict_fields or filtered:
            reason = "constraint_conflict" if rule.field in conflict_fields else "zero_result_filter"
            # 低优先级、低置信度、较新的约束优先建议放宽。
            candidates.append((rule.priority, int(rule.confidence * 1000), rule, reason))
    if not candidates:
        return ()
    _, _, rule, reason = min(candidates, key=lambda item: (item[0], item[1], -item[2].source_turn))
    return ({
        "action": "remove",
        "field": rule.field,
        "operator": rule.operator,
        "value": rule.value,
        "reason": reason,
        "requires_confirmation": True,
    },)


def visibility_failure(item: Item, context: RequestContext) -> str | None:
    if item.tenant_id != context.tenant_id:
        return "TENANT_MISMATCH"
    if item.status != "active":
        return "ITEM_INACTIVE"
    if item.permission_tags and not set(item.permission_tags).intersection(
        context.allowed_permission_tags
    ):
        return "PERMISSION_DENIED"
    return None


class ConstraintEngine:
    def __init__(self, catalog: CatalogRepository) -> None:
        self.catalog = catalog

    async def apply(
        self,
        candidates: list[FusedCandidate],
        intent: ResolvedIntent,
        context: RequestContext,
    ) -> ConstraintFilterResult:
        executable = active_constraints(intent.hard_constraints)
        conflicts = detect_constraint_conflicts(intent.hard_constraints)
        accepted: list[VerifiedCandidate] = []
        filtered: Counter[str] = Counter()
        for candidate in candidates:
            item = await self.catalog.get(candidate.item_id)
            if item is None:
                filtered["ITEM_NOT_FOUND"] += 1
                continue
            visibility_reason = visibility_failure(item, context)
            if visibility_reason:
                filtered[visibility_reason] += 1
                continue
            failed_rule = next(
                (rule for rule in executable if not constraint_matches(item, rule)),
                None,
            )
            if failed_rule is not None:
                filtered[f"CONSTRAINT_{failed_rule.field.upper()}"] += 1
                continue
            evidence = tuple(
                {
                    "field": rule.field,
                    "value": item.field_value(rule.field),
                    "source_uri": f"catalog://item/{item.item_id}",
                    "observed_at": item.updated_at.isoformat(),
                    # 测试合成字段不能伪装成目录权威事实。
                    "trust_level": (
                        "synthetic"
                        if item.attributes.get("synthetic_test_data")
                        else "authoritative"
                    ),
                }
                for rule in executable
            )
            accepted.append(VerifiedCandidate(
                item_id=item.item_id,
                title=item.title,
                score=candidate.score,
                source_scores=candidate.source_scores,
                reasons=(*candidate.reasons, "catalog_validated", "hard_constraints_passed"),
                evidence=evidence,
            ))
        suggestions = ()
        if not accepted:
            suggestions = minimum_relaxation_suggestions(
                intent.hard_constraints, dict(filtered), conflicts
            )
        return ConstraintFilterResult(
            tuple(accepted), dict(filtered), conflicts, suggestions
        )
