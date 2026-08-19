"""Deterministic result sufficiency, clarification and refusal policy."""

from __future__ import annotations

from ..domain.deep_path import SufficiencyAssessment, TerminalDecision
from ..domain.fast_path import ConstraintFilterResult, ResolvedIntent


class ResultSufficiencyEvaluator:
    """根据已校验候选、来源支持和剩余预算决定是否继续执行。"""

    def assess(
        self,
        intent: ResolvedIntent,
        filtered: ConstraintFilterResult,  # 经过硬约束过滤后的候选结果
        replan_count: int,    # 已经重规划的次数
        max_replans: int,     # 允许的最大重规划次数
        can_replan: bool,     # 当前是否允许重规划
    ) -> SufficiencyAssessment:
        accepted_count = len(filtered.accepted)
        # 双来源共同召回可排除只有 BM25 质量分、没有文本匹配的弱候选。
        supported_count = sum(
            len(candidate.source_scores) >= 2 for candidate in filtered.accepted
        )
        # 条件判断是否充分
        # 至少有一个候选 且
        # 至少有一个候选拥有 2 个及以上来源（supported_count > 0）——这排除了只有 BM25 文本匹配但没有其他证据的弱候选 或
        # 或者意图置信度 ≥ 0.75（即使来源单一，但用户意图很明确，也可接受）
        if accepted_count and (supported_count or intent.confidence >= 0.75):
            return SufficiencyAssessment(
                "sufficient",
                "verified_candidates_available",
                accepted_count,
                supported_count,
                replan_count,
            )
        # 判断是否重规划
        if can_replan and replan_count < max_replans:
            return SufficiencyAssessment(
                "replan",
                "no_supported_candidate",
                accepted_count,
                supported_count,
                replan_count,
            )
        # 判断是否需要澄清，是否有存在歧义的地方
        if intent.ambiguities:
            return SufficiencyAssessment(
                "clarify",
                "missing_material_information",
                accepted_count,
                supported_count,
                replan_count,
                self._clarification_questions(intent),   # 向用户提问的问题
            )
        return SufficiencyAssessment(
            "refuse",
            "constraints_or_evidence_not_satisfied",
            accepted_count,
            supported_count,
            replan_count,
        )

    @staticmethod
    def _clarification_questions(intent: ResolvedIntent) -> tuple[str, ...]:
        questions: list[str] = []
        # 每轮最多两个问题，并优先询问会显著改变候选集的信息。
        if "domain" in intent.ambiguities:
            questions.append("你希望查找哪一类商品？")
        for ambiguity in intent.ambiguities:
            if ambiguity != "domain" and len(questions) < 2:
                questions.append(f"请补充关于“{ambiguity}”的要求。")
        if not questions:
            questions.append("请补充预算、使用场景或不可妥协的条件。")
        return tuple(questions[:2])

    @staticmethod
    def terminal_decision(assessment: SufficiencyAssessment) -> TerminalDecision:
        if assessment.action == "clarify":
            return TerminalDecision(
                action="clarify",
                reason=assessment.reason,
                message="当前信息不足以形成可靠推荐，请先补充关键条件。",
                questions=assessment.questions,
            )
        if assessment.action != "refuse":
            raise ValueError("terminal decision requires clarify or refuse assessment")
        return TerminalDecision(
            action="refuse",
            reason=assessment.reason,
            message="目录中没有同时满足硬约束且证据充分的商品。",
        )
