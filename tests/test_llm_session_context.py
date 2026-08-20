from __future__ import annotations

import unittest

from langchain_core.runnables import RunnableLambda

from seekora_agent.application.session_context import SessionContextResolver
from seekora_agent.domain.fast_path import ResolvedIntent
from seekora_agent.domain.models import Constraint
from seekora_agent.infrastructure.intent.rule_based import RuleBasedIntentResolver
from seekora_agent.infrastructure.session_context.langchain_llm import (
    LLMConstraintPatchOutput,
    LLMPatchOperation,
    LangChainLLMSessionContextPatchResolver,
)
from seekora_agent.infrastructure.session_context.rule_based import (
    RuleBasedSessionContextPatchResolver,
)


def previous_intent() -> ResolvedIntent:
    return ResolvedIntent(
        mode="RECOMMEND",
        domain="laptop",
        retrieval_query="适合编程的轻薄笔记本",
        hard_constraints=(Constraint("price", "lte", 8000.0),),
        confidence=0.9,
        resolver_version="previous-v1",
    )


class LangChainLLMSessionContextPatchResolverTest(unittest.IsolatedAsyncioTestCase):
    async def test_ai_patch_is_executed_by_deterministic_reducer(self) -> None:
        async def fake_chain(_: dict[str, str]) -> LLMConstraintPatchOutput:
            return LLMConstraintPatchOutput(
                task_relation="follow_up",
                use_previous_query=True,
                operations=[
                    LLMPatchOperation(
                        action="set", field="price", operator="lte", value="6000"
                    )
                ],
            )

        patch_resolver = LangChainLLMSessionContextPatchResolver(
            chain=RunnableLambda(fake_chain),
            parser_version="fake-ai-v1",
            fallback=RuleBasedSessionContextPatchResolver(),
        )
        current = await RuleBasedIntentResolver().resolve("预算改成6000元以内")
        result = await SessionContextResolver(patch_resolver).merge(
            "预算改成6000元以内", current, previous_intent().as_dict(), "request-1"
        )

        self.assertTrue(result.applied)
        self.assertEqual("fake-ai-v1", result.parser_version)
        self.assertEqual(6000.0, result.intent.hard_constraints[0].value)
        self.assertEqual("适合编程的轻薄笔记本", result.intent.retrieval_query)

    async def test_ai_failure_falls_back_to_rules(self) -> None:
        async def failing_chain(_: dict[str, str]) -> LLMConstraintPatchOutput:
            raise TimeoutError("simulated provider timeout")

        patch_resolver = LangChainLLMSessionContextPatchResolver(
            chain=RunnableLambda(failing_chain),
            parser_version="unavailable-ai",
            fallback=RuleBasedSessionContextPatchResolver(),
        )
        current = await RuleBasedIntentResolver().resolve("取消预算限制")
        result = await SessionContextResolver(patch_resolver).merge(
            "取消预算限制", current, previous_intent().as_dict(), "request-1"
        )

        self.assertTrue(result.applied)
        self.assertEqual("session-rules-zh-v2", result.parser_version)
        self.assertEqual((), result.intent.hard_constraints)

    async def test_invalid_ai_patch_falls_back_without_mutating_security_fields(self) -> None:
        async def invalid_chain(_: dict[str, str]) -> dict:
            return {
                "task_relation": "follow_up",
                "operations": [{
                    "action": "remove",
                    "field": "tenant_id",
                }],
                "use_previous_query": True,
            }

        patch_resolver = LangChainLLMSessionContextPatchResolver(
            chain=RunnableLambda(invalid_chain),
            parser_version="invalid-ai",
            fallback=RuleBasedSessionContextPatchResolver(),
        )
        current = await RuleBasedIntentResolver().resolve("帮我看看别的")
        patch = await patch_resolver.resolve(
            "帮我看看别的", current, previous_intent()
        )

        self.assertEqual("new_task", patch.task_relation)
        self.assertEqual("session-rules-zh-v2", patch.parser_version)


if __name__ == "__main__":
    unittest.main()
