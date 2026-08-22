import unittest

from langchain_core.tools import BaseTool

from seekora_agent.bootstrap import build_runtime


class LangChainWorkflowTest(unittest.TestCase):
    def test_fast_path_is_compiled_langgraph(self):
        runtime = build_runtime(catalog_path="data/sample/items.jsonl")
        nodes = set(runtime.workflow.graph.nodes)
        self.assertTrue({
            "resolve_intent", "merge_session_context", "route", "probe", "plan", "recall", "deep_recall",
            "escalate_probe", "replan", "apply_constraints", "assess_sufficiency",
            "rerank", "compose_result", "compose_terminal"
        }.issubset(nodes))

    def test_recall_sources_are_langchain_tools(self):
        runtime = build_runtime(catalog_path="data/sample/items.jsonl")
        self.assertEqual(
            {"catalog_search", "vector_search", "behavior_recall"},
            set(runtime.workflow.recall.tools),
        )
        self.assertTrue(all(
            isinstance(tool, BaseTool) for tool in runtime.workflow.recall.tools.values()
        ))

    def test_trusted_context_is_hidden_from_model_tool_schema(self):
        runtime = build_runtime(catalog_path="data/sample/items.jsonl")
        for registered_tool in runtime.workflow.recall.tools.values():
            properties = registered_tool.tool_call_schema.model_json_schema()["properties"]
            self.assertEqual({"query", "top_k"}, set(properties))
            self.assertNotIn("runtime", properties)
            self.assertNotIn("tenant_id", properties)
            self.assertNotIn("user_id", properties)
            self.assertNotIn("allowed_permission_tags", properties)


if __name__ == "__main__":
    unittest.main()
