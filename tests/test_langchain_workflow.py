import unittest

from langchain_core.tools import BaseTool

from seekora_agent.bootstrap import build_runtime


class LangChainWorkflowTest(unittest.TestCase):
    def test_fast_path_is_compiled_langgraph(self):
        runtime = build_runtime()
        nodes = set(runtime.workflow.graph.nodes)
        self.assertTrue({
            "resolve_intent", "route", "probe", "plan", "recall", "deep_recall",
            "escalate_probe", "replan", "apply_constraints", "assess_sufficiency",
            "compose_result", "compose_terminal"
        }.issubset(nodes))

    def test_recall_sources_are_langchain_tools(self):
        runtime = build_runtime()
        self.assertEqual({"catalog_search", "vector_search"}, set(runtime.workflow.recall.tools))
        self.assertTrue(all(
            isinstance(tool, BaseTool) for tool in runtime.workflow.recall.tools.values()
        ))


if __name__ == "__main__":
    unittest.main()
