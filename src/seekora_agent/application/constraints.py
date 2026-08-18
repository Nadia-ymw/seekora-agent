"""Deterministic hard-constraint and final catalog validation service."""

from __future__ import annotations

from collections import Counter
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
                (rule for rule in intent.hard_constraints if not constraint_matches(item, rule)),
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
                    "trust_level": "authoritative",
                }
                for rule in intent.hard_constraints
            )
            accepted.append(VerifiedCandidate(
                item_id=item.item_id,
                title=item.title,
                score=candidate.score,
                source_scores=candidate.source_scores,
                reasons=(*candidate.reasons, "catalog_validated", "hard_constraints_passed"),
                evidence=evidence,
            ))
        return ConstraintFilterResult(tuple(accepted), dict(filtered))
