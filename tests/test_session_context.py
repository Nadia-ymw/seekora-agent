import unittest

from seekora_agent.application.session_context import (
    ConstraintPatch,
    ConstraintPatchOperation,
    SessionContextResolver,
)
from seekora_agent.domain.fast_path import ResolvedIntent
from seekora_agent.domain.models import Constraint
from seekora_agent.infrastructure.intent.rule_based import RuleBasedIntentResolver
from seekora_agent.infrastructure.session_context.rule_based import (
    RuleBasedSessionContextPatchResolver,
)


def previous_intent() -> ResolvedIntent:
    return ResolvedIntent(
        mode="RECOMMEND",
        domain="laptop",
        retrieval_query="适合编程的轻薄笔记本",
        hard_constraints=(
            Constraint("price", "lte", 8000.0),
            Constraint("memory_gb", "gte", 16),
        ),
        soft_preferences=("轻薄",),
        confidence=0.9,
        resolver_version="test-v1",
    )


def context_resolver() -> SessionContextResolver:
    return SessionContextResolver(RuleBasedSessionContextPatchResolver())


class SessionContextResolverTest(unittest.IsolatedAsyncioTestCase):
    async def test_reducer_rejects_non_whitelisted_field(self):
        class UnsafePatchResolver:
            async def resolve(self, query, current, previous):
                return ConstraintPatch(
                    task_relation="follow_up",
                    operations=(
                        ConstraintPatchOperation("remove", field="tenant_id"),
                    ),
                    parser_version="unsafe-test",
                )

        current = await RuleBasedIntentResolver().resolve("修改租户")
        with self.assertRaisesRegex(ValueError, "unsupported constraint field"):
            await SessionContextResolver(UnsafePatchResolver()).merge(
                "修改租户", current, previous_intent().as_dict(), "request-1"
            )

    async def test_replaces_one_constraint_and_keeps_query_subject(self):
        current = await RuleBasedIntentResolver().resolve("预算改成6000元以内")
        result = await context_resolver().merge(
            "预算改成6000元以内", current, previous_intent().as_dict(), "request-1"
        )

        self.assertTrue(result.applied)
        self.assertEqual("request-1", result.previous_request_id)
        self.assertEqual(("price",), result.replaced_fields)
        self.assertEqual("适合编程的轻薄笔记本", result.intent.retrieval_query)
        self.assertEqual(
            [("memory_gb", "gte", 16), ("price", "lte", 6000.0)],
            [(item.field, item.operator, item.value) for item in result.intent.hard_constraints],
        )

    async def test_removes_named_constraint(self):
        current = await RuleBasedIntentResolver().resolve("取消预算限制")
        result = await context_resolver().merge(
            "取消预算限制", current, previous_intent().as_dict(), "request-1"
        )

        self.assertEqual(("price",), result.removed_fields)
        self.assertEqual(
            ["memory_gb"], [item.field for item in result.intent.hard_constraints]
        )

    async def test_adds_constraint_without_dropping_existing_fields(self):
        current = await RuleBasedIntentResolver().resolve("再加32GB内存以上")
        result = await context_resolver().merge(
            "再加32GB内存以上", current, previous_intent().as_dict(), "request-1"
        )

        self.assertTrue(result.applied)
        self.assertEqual(
            [("price", "lte", 8000.0), ("memory_gb", "gte", 32)],
            [(item.field, item.operator, item.value) for item in result.intent.hard_constraints],
        )

    async def test_clears_all_constraints_without_losing_subject(self):
        current = await RuleBasedIntentResolver().resolve("清空条件")
        result = await context_resolver().merge(
            "清空条件", current, previous_intent().as_dict(), "request-1"
        )

        self.assertTrue(result.constraints_cleared)
        self.assertEqual((), result.intent.hard_constraints)
        self.assertEqual("laptop", result.intent.domain)

    async def test_explicit_new_task_does_not_inherit_constraints(self):
        current = await RuleBasedIntentResolver().resolve("重新搜索游戏本")
        result = await context_resolver().merge(
            "重新搜索游戏本", current, previous_intent().as_dict(), "request-1"
        )

        self.assertFalse(result.applied)
        self.assertEqual((), result.intent.hard_constraints)

    async def test_new_subject_with_constraint_does_not_inherit_previous_task(self):
        current = await RuleBasedIntentResolver().resolve("5000元以内手机")
        result = await context_resolver().merge(
            "5000元以内手机", current, previous_intent().as_dict(), "request-1"
        )

        self.assertFalse(result.applied)
        self.assertEqual("手机", result.intent.retrieval_query)


if __name__ == "__main__":
    unittest.main()
