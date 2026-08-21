"""基于已验证目录事实生成确定性的推荐解释。"""

from __future__ import annotations

from typing import Any

from ..domain.fast_path import VerifiedCandidate


class EvidenceComposer:
    """只组合工具和约束引擎提供的证据，不生成目录之外的新事实。"""

    def compose(
        self,
        candidate: VerifiedCandidate,
        detail: dict[str, Any] | None,
    ) -> dict[str, Any]:
        result = candidate.as_dict()
        if detail is None:
            result["explanation"] = {
                "summary": "该结果已通过目录状态、权限和硬约束校验。",
                "facts": list(candidate.evidence),
                "sources": sorted({
                    evidence["source_uri"] for evidence in candidate.evidence
                }),
            }
            return result

        catalog_fact = {
            "field": "item_detail",
            "value": {
                "title": detail["title"],
                "category": detail["category"],
                "attributes": detail["attributes"],
            },
            "source_uri": detail["source_uri"],
            "observed_at": detail["observed_at"],
            "trust_level": detail["trust_level"],
        }
        facts = [*candidate.evidence, catalog_fact]
        result.update({
            "description": detail["description"],
            "category": detail["category"],
            "attributes": detail["attributes"],
            "evidence": facts,
            "explanation": {
                "summary": "该结果匹配检索条件，并已通过权威目录的状态、权限和约束校验。",
                "facts": facts,
                "sources": sorted({fact["source_uri"] for fact in facts}),
            },
        })
        return result
