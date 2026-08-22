"""固定查询集上的双路 RRF 质量、稳定性与暖态延迟对照。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from statistics import median
from time import perf_counter
from typing import Sequence

from ..domain.models import GoldenQuery, SearchQuery, SearchResult
from .metrics import EvaluationReport, RankedSearch, evaluate


class ReciprocalRankFusionSearch:
    """把多个召回源按名次融合，避免直接比较不可校准的原始分数。"""

    def __init__(
        self,
        sources: Sequence[RankedSearch],
        rrf_k: int = 60,
        weights: Sequence[float] | None = None,
    ) -> None:
        if len(sources) < 2:
            raise ValueError("RRF comparison requires at least two sources")
        self.sources = tuple(sources)
        self.rrf_k = rrf_k
        self.weights = tuple(weights or (1.0,) * len(sources))
        if len(self.weights) != len(self.sources):
            raise ValueError("RRF weights must match source count")
        if any(weight <= 0 for weight in self.weights):
            raise ValueError("RRF weights must be positive")

    def search(self, query: SearchQuery, top_k: int = 10) -> list[SearchResult]:
        candidate_k = min(max(top_k * 3, top_k), 50)
        fused: dict[str, dict[str, object]] = {}
        # 与线上 RecallOrchestrator 一致并行执行，避免把两个来源延迟错误相加。
        with ThreadPoolExecutor(max_workers=len(self.sources)) as executor:
            ranked_lists = tuple(executor.map(
                lambda source: source.search(query, candidate_k), self.sources
            ))
        for source_index, results in enumerate(ranked_lists):
            for rank, result in enumerate(results, start=1):
                entry = fused.setdefault(result.item.item_id, {
                    "item": result.item,
                    "score": 0.0,
                    "reasons": [],
                })
                entry["score"] = float(entry["score"]) + self.weights[source_index] / (
                    self.rrf_k + rank
                )
                reasons = entry["reasons"]
                assert isinstance(reasons, list)
                reasons.append(f"rrf_source:{source_index + 1}")
        ranked = [
            SearchResult(
                item=entry["item"],  # type: ignore[arg-type]
                score=float(entry["score"]),
                reasons=tuple(entry["reasons"]),  # type: ignore[arg-type]
            )
            for entry in fused.values()
        ]
        ranked.sort(key=lambda result: (-result.score, result.item.item_id))
        return ranked[:top_k]


@dataclass(frozen=True)
class LatencyReport:
    samples: int
    p50_ms: float
    p95_ms: float
    maximum_ms: float
    zero_result_rate: float
    stability_rate: float

    def as_dict(self) -> dict[str, float | int]:
        return {
            "samples": self.samples,
            "p50_ms": round(self.p50_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "maximum_ms": round(self.maximum_ms, 3),
            "zero_result_rate": round(self.zero_result_rate, 6),
            "stability_rate": round(self.stability_rate, 6),
        }


@dataclass(frozen=True)
class RecallComparisonReport:
    baseline: EvaluationReport
    active: EvaluationReport
    baseline_latency: LatencyReport
    active_latency: LatencyReport
    coverage_gate_passed: bool
    quality_not_degraded: bool
    latency_gate_passed: bool

    @property
    def gate_passed(self) -> bool:
        return (
            self.coverage_gate_passed
            and self.quality_not_degraded
            and self.latency_gate_passed
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "baseline": self.baseline.as_dict(),
            "active": self.active.as_dict(),
            "quality_delta": {
                "recall": round(self.active.recall_at_k - self.baseline.recall_at_k, 6),
                "mrr": round(self.active.mrr_at_k - self.baseline.mrr_at_k, 6),
                "ndcg": round(self.active.ndcg_at_k - self.baseline.ndcg_at_k, 6),
                "zero_result_rate": round(
                    self.active.zero_result_rate - self.baseline.zero_result_rate, 6
                ),
            },
            "baseline_latency": self.baseline_latency.as_dict(),
            "active_latency": self.active_latency.as_dict(),
            "gate": {
                "minimum_query_count_passed": self.coverage_gate_passed,
                "quality_not_degraded": self.quality_not_degraded,
                "active_p95_lte_2000ms": self.latency_gate_passed,
                "passed": self.gate_passed,
            },
        }


@dataclass(frozen=True)
class WeightSelectionReport:
    selected_weight: float
    trials: tuple[tuple[float, EvaluationReport], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "selected_qwen_weight": self.selected_weight,
            "selection_metric": "ndcg_then_recall_then_mrr",
            "trials": [
                {"qwen_weight": weight, **report.as_dict()}
                for weight, report in self.trials
            ],
        }


class _MemoizedSearch:
    """权重扫描只执行一次底层召回，避免重复进行相同模型推理。"""

    def __init__(self, searcher: RankedSearch) -> None:
        self.searcher = searcher
        self.cache: dict[tuple[object, ...], list[SearchResult]] = {}

    def search(self, query: SearchQuery, top_k: int = 10) -> list[SearchResult]:
        key = (
            query.text,
            query.tenant_id,
            query.allowed_permission_tags,
            repr(query.constraints),
            top_k,
        )
        if key not in self.cache:
            self.cache[key] = self.searcher.search(query, top_k)
        return list(self.cache[key])


def _percentile(samples: list[float], percentile: float) -> float:
    """使用最近秩分位数；样本较少时仍给出保守、可复现结果。"""
    ordered = sorted(samples)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * percentile + 0.999999) - 1))
    return ordered[index]


def measure_search(
    searcher: RankedSearch,
    queries: Sequence[GoldenQuery],
    *,
    top_k: int,
    runs: int,
) -> LatencyReport:
    if runs < 2:
        raise ValueError("latency comparison requires at least two runs")
    samples: list[float] = []
    zero_results = 0
    stable = 0
    compared = 0
    previous: dict[str, tuple[str, ...]] = {}
    for _ in range(runs):
        for golden in queries:
            started = perf_counter()
            results = searcher.search(golden.query, top_k)
            samples.append((perf_counter() - started) * 1_000)
            zero_results += not results
            ranked_ids = tuple(result.item.item_id for result in results)
            if golden.query_id in previous:
                compared += 1
                stable += ranked_ids == previous[golden.query_id]
            previous[golden.query_id] = ranked_ids
    return LatencyReport(
        samples=len(samples),
        p50_ms=median(samples),
        p95_ms=_percentile(samples, 0.95),
        maximum_ms=max(samples),
        zero_result_rate=zero_results / len(samples),
        stability_rate=stable / compared if compared else 1.0,
    )


def compare_recall(
    baseline: RankedSearch,
    active: RankedSearch,
    queries: list[GoldenQuery],
    *,
    top_k: int = 10,
    runs: int = 3,
    p95_limit_ms: float = 2_000.0,
    min_query_count: int = 10,
) -> RecallComparisonReport:
    """评估质量后测暖态性能；调用方应在进入本函数前完成模型预热。"""
    baseline_quality = evaluate(baseline, queries, top_k)
    active_quality = evaluate(active, queries, top_k)
    baseline_latency = measure_search(baseline, queries, top_k=top_k, runs=runs)
    active_latency = measure_search(active, queries, top_k=top_k, runs=runs)
    quality_not_degraded = (
        active_quality.recall_at_k >= baseline_quality.recall_at_k
        and active_quality.mrr_at_k >= baseline_quality.mrr_at_k
        and active_quality.ndcg_at_k >= baseline_quality.ndcg_at_k
        and active_quality.zero_result_rate <= baseline_quality.zero_result_rate
    )
    return RecallComparisonReport(
        baseline=baseline_quality,
        active=active_quality,
        baseline_latency=baseline_latency,
        active_latency=active_latency,
        coverage_gate_passed=len(queries) >= min_query_count,
        quality_not_degraded=quality_not_degraded,
        latency_gate_passed=active_latency.p95_ms <= p95_limit_ms,
    )


def select_qwen_weight(
    keyword: RankedSearch,
    qwen: RankedSearch,
    queries: list[GoldenQuery],
    weights: Sequence[float],
    *,
    top_k: int = 10,
) -> WeightSelectionReport:
    """只在开发集选权重；最终门禁必须由调用方在独立留出集执行。"""
    candidates = tuple(dict.fromkeys(float(weight) for weight in weights))
    if not candidates or any(weight <= 0 for weight in candidates):
        raise ValueError("candidate Qwen weights must be positive")
    cached_keyword = _MemoizedSearch(keyword)
    cached_qwen = _MemoizedSearch(qwen)
    trials = tuple(
        (
            weight,
            evaluate(
                ReciprocalRankFusionSearch(
                    (cached_keyword, cached_qwen), weights=(1.0, weight)
                ),
                queries,
                top_k,
            ),
        )
        for weight in candidates
    )
    selected_weight, _ = max(
        trials,
        key=lambda trial: (
            trial[1].ndcg_at_k,
            trial[1].recall_at_k,
            trial[1].mrr_at_k,
            -abs(trial[0] - 1.0),
        ),
    )
    return WeightSelectionReport(selected_weight, trials)


def validate_golden_catalog(
    queries: Sequence[GoldenQuery], catalog_item_ids: set[str]
) -> None:
    """拒绝把 sample 标注误用于 processed 快照，避免产生虚假的零质量报告。"""
    query_ids = [query.query_id for query in queries]
    if len(set(query_ids)) != len(query_ids):
        raise ValueError("golden set query_id values must be unique")
    empty = [query.query_id for query in queries if not query.relevance]
    if empty:
        raise ValueError(f"golden queries must have relevance labels: {', '.join(empty[:5])}")
    invalid_grades = sorted({
        grade for query in queries for grade in query.relevance.values()
        if grade < 1 or grade > 3
    })
    if invalid_grades:
        raise ValueError("golden relevance grades must be integers from 1 to 3")
    missing = sorted({
        item_id
        for query in queries
        for item_id in query.relevance
        if item_id not in catalog_item_ids
    })
    if missing:
        preview = ", ".join(missing[:5])
        raise ValueError(
            f"golden set references {len(missing)} items absent from catalog: {preview}"
        )
