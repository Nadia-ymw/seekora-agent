from __future__ import annotations

import unittest

from langchain_core.runnables import RunnableLambda

from seekora_agent.bootstrap import build_intent_resolver
from seekora_agent.config.settings import AppSettings
from seekora_agent.infrastructure.intent.langchain_llm import (
    LLMConstraint,
    LLMIntentOutput,
    LangChainLLMIntentResolver,
)
from seekora_agent.infrastructure.intent.rule_based import RuleBasedIntentResolver


class LLMSettingsTests(unittest.TestCase):
    def test_rules_are_default_and_safe_summary_hides_secret(self) -> None:
        settings = AppSettings(
            OPENAI_API_KEY="should-not-appear",
            _env_file=None,
        )

        summary = settings.safe_summary()

        self.assertEqual(settings.intent_resolver, "rules")
        self.assertTrue(summary["openai_api_key_configured"])
        self.assertNotIn("should-not-appear", repr(summary))
        self.assertIsInstance(build_intent_resolver(settings), RuleBasedIntentResolver)

    def test_openai_mode_requires_key_and_model(self) -> None:
        settings = AppSettings(
            SEEKORA_INTENT_RESOLVER="openai",
            _env_file=None,
        )

        with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
            settings.require_openai()


class LangChainLLMIntentResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_output_maps_to_domain_intent(self) -> None:
        async def fake_chain(_: dict[str, str]) -> LLMIntentOutput:
            return LLMIntentOutput(
                mode="RECOMMEND",
                domain="laptop",
                retrieval_query="适合编程的轻薄本",
                hard_constraints=[
                    LLMConstraint(field="price", operator="lte", value="8000"),
                    LLMConstraint(field="memory_gb", operator="gte", value="16"),
                ],
                soft_preferences=["轻薄"],
                confidence=0.93,
            )

        resolver = LangChainLLMIntentResolver(
            chain=RunnableLambda(fake_chain),
            resolver_version="fake-model-v1",
        )

        intent = await resolver.resolve("8000 元以内、16GB 内存以上的编程轻薄本")

        self.assertEqual(intent.mode, "RECOMMEND")
        self.assertEqual(intent.resolver_version, "fake-model-v1")
        self.assertEqual(intent.hard_constraints[0].value, 8000.0)
        self.assertEqual(intent.hard_constraints[1].value, 16)

    async def test_provider_failure_falls_back_to_rules(self) -> None:
        async def failing_chain(_: dict[str, str]) -> LLMIntentOutput:
            raise TimeoutError("simulated provider timeout")

        rules = RuleBasedIntentResolver()
        resolver = LangChainLLMIntentResolver(
            chain=RunnableLambda(failing_chain),
            resolver_version="unavailable-model",
            fallback=rules,
        )

        intent = await resolver.resolve("8000元以内适合编程的轻薄本")

        self.assertEqual(intent.resolver_version, rules.version)
        self.assertEqual(intent.hard_constraints[0].field, "price")
        self.assertEqual(intent.hard_constraints[0].value, 8000.0)


if __name__ == "__main__":
    unittest.main()
