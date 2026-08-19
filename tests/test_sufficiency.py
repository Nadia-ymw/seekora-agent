import unittest

from seekora_agent.application.sufficiency import ResultSufficiencyEvaluator
from seekora_agent.domain.fast_path import ConstraintFilterResult, ResolvedIntent, VerifiedCandidate


def verified_candidate(source_count: int = 2) -> VerifiedCandidate:
    source_scores = {f"source-{index}": 1.0 for index in range(source_count)}
    return VerifiedCandidate("item-1", "Item", 1.0, source_scores, ("verified",))


class ResultSufficiencyEvaluatorTest(unittest.TestCase):
    def setUp(self):
        self.evaluator = ResultSufficiencyEvaluator()

    def test_supported_candidate_is_sufficient(self):
        assessment = self.evaluator.assess(
            ResolvedIntent("SEARCH", None, "耳机", confidence=0.6),
            ConstraintFilterResult((verified_candidate(),)),
            replan_count=0,
            max_replans=1,
            can_replan=True,
        )
        self.assertEqual("sufficient", assessment.action)

    def test_empty_result_requests_one_replan_when_available(self):
        assessment = self.evaluator.assess(
            ResolvedIntent("SEARCH", "laptop", "轻薄本", confidence=0.9),
            ConstraintFilterResult(()),
            replan_count=0,
            max_replans=1,
            can_replan=True,
        )
        self.assertEqual("replan", assessment.action)

    def test_ambiguity_requests_clarification_after_search_stops(self):
        assessment = self.evaluator.assess(
            ResolvedIntent(
                "SEARCH", None, "夸克熵泵", confidence=0.6, ambiguities=("domain",)
            ),
            ConstraintFilterResult(()),
            replan_count=0,
            max_replans=1,
            can_replan=False,
        )
        self.assertEqual("clarify", assessment.action)
        self.assertEqual(("你希望查找哪一类商品？",), assessment.questions)

    def test_unsatisfied_constraints_are_refused_after_replan(self):
        assessment = self.evaluator.assess(
            ResolvedIntent("SEARCH", "laptop", "轻薄本", confidence=0.9),
            ConstraintFilterResult(()),
            replan_count=1,
            max_replans=1,
            can_replan=False,
        )
        self.assertEqual("refuse", assessment.action)


if __name__ == "__main__":
    unittest.main()
