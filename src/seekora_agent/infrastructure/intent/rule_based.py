"""Deterministic Chinese rule baseline for Fast Path intent resolution."""

from __future__ import annotations

import re

from ...domain.fast_path import ResolvedIntent
from ...domain.models import Constraint


class RuleBasedIntentResolver:
    version = "rules-zh-v1"

    _constraint_patterns = (
        (re.compile(r"(?:预算|价格)?\s*(\d+(?:\.\d+)?)\s*元?\s*(?:以内|以下|不超过)"), "price", "lte", float),
        (re.compile(r"(?:内存)?\s*(\d+)\s*(?:gb|g)\s*(?:内存)?\s*(?:以上|起)", re.I), "memory_gb", "gte", int),
        (re.compile(r"(?:续航)?\s*(\d+(?:\.\d+)?)\s*(?:小时|h)\s*(?:以上|起)", re.I), "battery_hours", "gte", float),
        (re.compile(r"(?:重量)?\s*(\d+(?:\.\d+)?)\s*(?:kg|公斤)\s*(?:以内|以下|不超过)", re.I), "weight_kg", "lte", float),
    )

    async def resolve(self, query: str) -> ResolvedIntent:
        constraints: list[Constraint] = []
        retrieval_query = query
        for pattern, field, operator, converter in self._constraint_patterns:
            match = pattern.search(retrieval_query)
            if match:
                constraints.append(Constraint(field, operator, converter(match.group(1))))
                retrieval_query = pattern.sub(" ", retrieval_query)

        normalized = " ".join(retrieval_query.split()).strip() or query.strip()
        domain = "laptop" if any(
            token in query.lower() for token in ("笔记本", "轻薄本", "游戏本", "laptop")
        ) else None
        mode = "RECOMMEND" if any(
            token in query for token in ("推荐", "适合", "帮我选", "哪个好")
        ) else "SEARCH"
        soft_preferences = tuple(
            token for token in ("轻薄", "高性能", "长续航", "便携", "性价比")
            if token in query
        )
        ambiguities = () if domain else ("domain",)
        return ResolvedIntent(
            mode=mode,
            domain=domain,
            retrieval_query=normalized,
            hard_constraints=tuple(constraints),
            soft_preferences=soft_preferences,
            confidence=0.9 if domain else 0.65,
            ambiguities=ambiguities,
            resolver_version=self.version,
        )
