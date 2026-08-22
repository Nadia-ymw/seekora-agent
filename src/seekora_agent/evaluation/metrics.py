from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from ..domain.models import GoldenQuery, SearchQuery, SearchResult


class RankedSearch(Protocol):
    """离线评测只依赖排序搜索契约，不绑定 BM25 具体实现。"""

    def search(self, query: SearchQuery, top_k: int = 10) -> list[SearchResult]: ...


@dataclass(frozen=True)
class EvaluationReport:
    query_count: int
    recall_at_k: float
    mrr_at_k: float
    ndcg_at_k: float
    zero_result_rate: float
    k: int

    def as_dict(self) -> dict:
        return {
            "query_count": self.query_count,
            f"recall@{self.k}": round(self.recall_at_k, 6),
            f"mrr@{self.k}": round(self.mrr_at_k, 6),
            f"ndcg@{self.k}": round(self.ndcg_at_k, 6),
            "zero_result_rate": round(self.zero_result_rate, 6),
        }


def _dcg(grades: list[int]) -> float:
    return sum((2**grade - 1) / math.log2(index + 2) for index, grade in enumerate(grades))


def evaluate(baseline: RankedSearch, queries: list[GoldenQuery], k: int = 10) -> EvaluationReport:
    if not queries:
        raise ValueError("golden query set must not be empty")
    recalls: list[float] = []
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    zero_results = 0
    for golden in queries:
        results = baseline.search(golden.query, top_k=k)
        ranked_ids = [result.item.item_id for result in results]
        relevant_ids = {item_id for item_id, grade in golden.relevance.items() if grade > 0}
        hits = relevant_ids.intersection(ranked_ids)
        recalls.append(len(hits) / len(relevant_ids) if relevant_ids else 1.0)
        ranks = [index + 1 for index, item_id in enumerate(ranked_ids) if item_id in relevant_ids]
        reciprocal_ranks.append(1 / min(ranks) if ranks else 0.0)
        grades = [golden.relevance.get(item_id, 0) for item_id in ranked_ids]
        ideal_grades = sorted(golden.relevance.values(), reverse=True)[:k]
        ideal_dcg = _dcg(ideal_grades)
        ndcgs.append(_dcg(grades) / ideal_dcg if ideal_dcg else 1.0)
        zero_results += not results
    count = len(queries)
    return EvaluationReport(
        query_count=count,
        recall_at_k=sum(recalls) / count,
        mrr_at_k=sum(reciprocal_ranks) / count,
        ndcg_at_k=sum(ndcgs) / count,
        zero_result_rate=zero_results / count,
        k=k,
    )
