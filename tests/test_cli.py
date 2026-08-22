import contextlib
import io
import unittest

from seekora_agent.interfaces.cli import build_parser


class VectorIndexCliTest(unittest.TestCase):
    def test_vector_index_defaults_to_sqlite(self):
        arguments = build_parser().parse_args(["build-vector-index"])
        self.assertTrue(arguments.output.endswith(".sqlite3"))

    def test_vector_index_rejects_legacy_json_output(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            build_parser().parse_args(
                ["build-vector-index", "--output", ".runtime/vectors.json"]
            )

    def test_compare_recall_parser_exposes_quality_gate_arguments(self):
        arguments = build_parser().parse_args([
            "compare-recall",
            "--golden",
            "data/golden/processed-queries.jsonl",
            "--runs",
            "4",
        ])
        self.assertEqual("compare-recall", arguments.command)
        self.assertEqual(4, arguments.runs)
        self.assertEqual(2_000.0, arguments.p95_limit_ms)
        self.assertEqual(10, arguments.min_query_count)
        self.assertEqual(1.0, arguments.qwen_weight)
        self.assertIsNone(arguments.development_golden)


if __name__ == "__main__":
    unittest.main()
