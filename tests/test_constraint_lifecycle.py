import unittest
from datetime import UTC, datetime, timedelta

from seekora_agent.application.constraints import (
    ConstraintEngine,
    active_constraints,
    detect_constraint_conflicts,
)
from seekora_agent.application.contracts import RequestContext
from seekora_agent.application.session_context import ConstraintPatch, SessionContextResolver
from seekora_agent.domain.fast_path import FusedCandidate, ResolvedIntent
from seekora_agent.domain.models import Constraint, Item
from seekora_agent.infrastructure.catalog_repository import InMemoryCatalogRepository


NOW = datetime(2026, 8, 20, 8, 0, tzinfo=UTC)


def intent(domain: str | None, constraints: tuple[Constraint, ...]) -> ResolvedIntent:
    return ResolvedIntent(
        mode="SEARCH",
        domain=domain,
        retrieval_query=domain or "商品",
        hard_constraints=constraints,
        confidence=0.9,
        resolver_version="lifecycle-test",
    )


class FollowUpResolver:
    async def resolve(self, query, current, previous):
        del query, current, previous
        return ConstraintPatch(
            task_relation="follow_up",
            use_previous_query=False,
            parser_version="follow-up-test",
        )


class ConstraintContractTest(unittest.TestCase):
    def test_metadata_round_trip_is_backward_compatible(self):
        legacy = Constraint.from_dict({"field": "price", "operator": "lte", "value": 8000})
        self.assertEqual("session", legacy.scope)
        self.assertEqual("active", legacy.status)
        restored = Constraint.from_dict({
            **legacy.as_dict(),
            "scope": "contextual",
            "source": "session",
            "source_turn": 3,
            "confidence": 0.75,
            "priority": 20,
            "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
        })
        self.assertEqual(3, restored.source_turn)
        self.assertEqual("contextual", restored.scope)
        self.assertTrue(restored.is_active(NOW))

    def test_bound_conflicts_are_detected_for_multiple_numeric_values(self):
        for lower, upper in ((9000, 8000), (1.5, 1.2), (33, 32)):
            with self.subTest(lower=lower, upper=upper):
                conflicts = detect_constraint_conflicts((
                    Constraint("price", "gte", lower),
                    Constraint("price", "lte", upper),
                ), NOW)
                self.assertEqual("lower_bound_exceeds_upper_bound", conflicts[0]["reason"])

        conflicts = detect_constraint_conflicts((
            Constraint("category", "in", ["laptop", "phone"]),
            Constraint("category", "in", ["camera"]),
        ), NOW)
        self.assertEqual("allowed_values_do_not_overlap", conflicts[0]["reason"])


class SessionConstraintLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_contextual_and_deadline_constraints_expire_on_next_turn(self):
        previous = intent("laptop", (
            Constraint("price", "lte", 8000, scope="contextual"),
            Constraint("memory_gb", "gte", 16, expires_at=NOW - timedelta(seconds=1)),
        ))
        result = await SessionContextResolver(
            FollowUpResolver(), clock=lambda: NOW
        ).merge("继续", intent("laptop", ()), previous.as_dict(), "request-1")

        self.assertEqual((), active_constraints(result.intent.hard_constraints, NOW))
        self.assertEqual(
            ["expired", "expired"],
            [rule.status for rule in result.intent.hard_constraints],
        )
        self.assertEqual(2, sum(
            change["action"] == "expired" for change in result.lifecycle_changes
        ))

    async def test_category_change_suspends_and_then_restores_specific_constraint(self):
        resolver = SessionContextResolver(FollowUpResolver(), clock=lambda: NOW)
        laptop = intent("laptop", (Constraint("memory_gb", "gte", 32),))
        phone_result = await resolver.merge(
            "换成手机", intent("phone", ()), laptop.as_dict(), "request-1"
        )
        self.assertEqual("suspended", phone_result.intent.hard_constraints[0].status)
        self.assertIn("suspended", [item["action"] for item in phone_result.lifecycle_changes])

        laptop_result = await resolver.merge(
            "换回笔记本", intent("laptop", ()), phone_result.intent.as_dict(), "request-2"
        )
        self.assertEqual("active", laptop_result.intent.hard_constraints[0].status)
        self.assertIn("restored", [item["action"] for item in laptop_result.lifecycle_changes])


class ConstraintRelaxationTest(unittest.IsolatedAsyncioTestCase):
    async def test_conflict_returns_one_confirmation_only_suggestion(self):
        item = Item.from_dict({
            "item_id": "one",
            "tenant_id": "demo",
            "title": "测试笔记本",
            "description": "",
            "category": "laptop",
            "attributes": {"price": 8500},
            "status": "active",
            "permission_tags": ["public"],
            "updated_at": "2026-08-20T00:00:00Z",
        })
        constraints = (
            Constraint("price", "gte", 9000, priority=100),
            Constraint("price", "lte", 8000, priority=10, source_turn=2),
        )
        result = await ConstraintEngine(InMemoryCatalogRepository([item])).apply(
            [FusedCandidate("one", item.title, 1.0, {"catalog": 1.0}, ())],
            intent("laptop", constraints),
            RequestContext("request", "demo", None, ("public",)),
        )

        self.assertEqual((), result.accepted)
        self.assertEqual(1, len(result.conflicts))
        self.assertEqual(1, len(result.relaxation_suggestions))
        suggestion = result.relaxation_suggestions[0]
        self.assertEqual("remove", suggestion["action"])
        self.assertTrue(suggestion["requires_confirmation"])
        # 引擎只提议，不会修改原始约束。
        self.assertEqual(2, len(constraints))

    async def test_acl_only_empty_result_does_not_suggest_relaxing_user_constraint(self):
        item = Item.from_dict({
            "item_id": "private",
            "tenant_id": "demo",
            "title": "内部商品",
            "description": "",
            "category": "laptop",
            "attributes": {"price": 7000},
            "status": "active",
            "permission_tags": ["internal"],
            "updated_at": "2026-08-20T00:00:00Z",
        })
        result = await ConstraintEngine(InMemoryCatalogRepository([item])).apply(
            [FusedCandidate("private", item.title, 1.0, {"catalog": 1.0}, ())],
            intent("laptop", (Constraint("price", "lte", 8000),)),
            RequestContext("request", "demo", None, ("public",)),
        )
        self.assertEqual((), result.relaxation_suggestions)


if __name__ == "__main__":
    unittest.main()
