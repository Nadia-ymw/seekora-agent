"""Stable domain contracts for routing and grounded Deep Path planning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


RouteName = Literal["fast", "deep"]


@dataclass(frozen=True)
class RouteDecision:
    """Auditable routing result; reasons are safe to expose and persist."""

    route: RouteName
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"route": self.route, "reasons": list(self.reasons)}


@dataclass(frozen=True)
class ProbeSummary:
    """Small retrieval observation used by the planner, not a user result."""

    candidate_count: int
    source_candidate_counts: dict[str, int]
    overlapping_candidate_count: int
    failed_sources: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_count": self.candidate_count,
            "source_candidate_counts": self.source_candidate_counts,
            "overlapping_candidate_count": self.overlapping_candidate_count,
            "failed_sources": list(self.failed_sources),
        }


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    query: str
    purpose: Literal["primary", "broaden"]

    def as_dict(self) -> dict[str, str]:
        return {"step_id": self.step_id, "query": self.query, "purpose": self.purpose}


@dataclass(frozen=True)
class DeepPlan:
    """Bounded, serializable plan; it intentionally contains no hidden reasoning."""

    steps: tuple[PlanStep, ...]
    max_parallelism: int = 2
    max_replans: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": [step.as_dict() for step in self.steps],
            "max_parallelism": self.max_parallelism,
            "max_replans": self.max_replans,
        }
