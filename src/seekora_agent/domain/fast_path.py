"""Domain contracts produced and consumed by the Fast Path pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .models import Constraint


IntentMode = Literal["SEARCH", "RECOMMEND", "HYBRID", "RESEARCH", "CLARIFY"]


@dataclass(frozen=True)
class ResolvedIntent:
    mode: IntentMode
    domain: str | None
    retrieval_query: str
    hard_constraints: tuple[Constraint, ...] = ()
    soft_preferences: tuple[str, ...] = ()
    negative_preferences: tuple[str, ...] = ()
    confidence: float = 0.0
    ambiguities: tuple[str, ...] = ()
    resolver_version: str = "unknown"

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "domain": self.domain,
            "retrieval_query": self.retrieval_query,
            "hard_constraints": [
                {"field": rule.field, "operator": rule.operator, "value": rule.value}
                for rule in self.hard_constraints
            ],
            "soft_preferences": list(self.soft_preferences),
            "negative_preferences": list(self.negative_preferences),
            "confidence": self.confidence,
            "ambiguities": list(self.ambiguities),
            "resolver_version": self.resolver_version,
        }


@dataclass(frozen=True)
class FusedCandidate:
    item_id: str
    title: str
    score: float
    source_scores: dict[str, float]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class VerifiedCandidate:
    item_id: str
    title: str
    score: float
    source_scores: dict[str, float]
    reasons: tuple[str, ...]
    evidence: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "score": round(self.score, 8),
            "source_scores": self.source_scores,
            "reasons": list(self.reasons),
            "constraint_pass": True,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class ConstraintFilterResult:
    accepted: tuple[VerifiedCandidate, ...]
    filtered_reason_counts: dict[str, int] = field(default_factory=dict)
