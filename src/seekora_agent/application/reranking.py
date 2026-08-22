"""无训练语义复核端口及 Challenger 编排。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Literal, Protocol, Sequence

from ..domain.fast_path import FusedCandidate
from .catalog import CatalogRepository


RerankMode = Literal["off", "challenger", "active"]


class RerankerUnavailable(RuntimeError):
    """重排模型不可用；编排层可以安全退回原始 RRF 顺序。"""


class SemanticReranker(Protocol):
    """Cross-Encoder 或在线轻量重排服务的统一端口。"""

    @property
    def model_version(self) -> str: ...

    async def score(self, query: str, documents: Sequence[str]) -> list[float]: ...


@dataclass(frozen=True)
class RerankResult:
    candidates: tuple[FusedCandidate, ...]
    status: Literal["skipped", "ok", "degraded"]
    mode: RerankMode
    model_version: str
    latency_ms: int = 0
    error_code: str | None = None
    scores: tuple[tuple[str, float], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "mode": self.mode,
            "model_version": self.model_version,
            "latency_ms": self.latency_ms,
            "error_code": self.error_code,
            "scores": [
                {"item_id": item_id, "score": score}
                for item_id, score in self.scores
            ],
        }


class RerankOrchestrator:
    """对融合候选做语义复核，并保证 Challenger 不改变线上顺序。"""

    def __init__(
        self,
        catalog: CatalogRepository,
        reranker: SemanticReranker | None = None,
        mode: RerankMode = "off",
        top_n: int = 30,
    ) -> None:
        if not 1 <= top_n <= 50:
            raise ValueError("rerank top_n must be between 1 and 50")
        if mode != "off" and reranker is None:
            raise ValueError("reranker is required when rerank mode is enabled")
        self.catalog = catalog
        self.reranker = reranker
        self.mode = mode
        self.top_n = top_n

    async def rerank(
        self, query: str, candidates: tuple[FusedCandidate, ...]
    ) -> RerankResult:
        if self.mode == "off" or self.reranker is None or not candidates:
            return RerankResult(candidates, "skipped", self.mode, "none")

        selected = candidates[: self.top_n]
        # 只把已召回候选的目录文本交给重排器，模型不能生成或引入新的 item_id。
        items = await asyncio.gather(*(
            self.catalog.get(candidate.item_id) for candidate in selected
        ))
        documents = [
            item.searchable_text() if item is not None else candidate.title
            for candidate, item in zip(selected, items, strict=True)
        ]
        started = perf_counter()
        try:
            raw_scores = await self.reranker.score(query, documents)
        except RerankerUnavailable:
            return RerankResult(
                candidates=candidates,
                status="degraded",
                mode=self.mode,
                model_version=self.reranker.model_version,
                latency_ms=int((perf_counter() - started) * 1_000),
                error_code="RERANKER_UNAVAILABLE",
            )
        if len(raw_scores) != len(selected):
            # 返回数量错误属于实现契约缺陷，不能伪装成普通模型降级。
            raise ValueError("reranker score count does not match candidate count")

        score_pairs = tuple(
            (candidate.item_id, float(score))
            for candidate, score in zip(selected, raw_scores, strict=True)
        )
        score_by_id = dict(score_pairs)
        annotated = tuple(
            replace(
                candidate,
                rerank_score=score_by_id.get(candidate.item_id),
                rerank_mode=self.mode,
                reasons=(
                    *candidate.reasons,
                    f"semantic_rerank:{self.mode}",
                ) if candidate.item_id in score_by_id else candidate.reasons,
            )
            for candidate in candidates
        )
        if self.mode == "active":
            # Active 仅供离线/受控测试；未评测模型不能由环境配置切到该模式。
            head = sorted(
                annotated[: len(selected)],
                key=lambda candidate: (
                    -(candidate.rerank_score if candidate.rerank_score is not None else float("-inf")),
                    -candidate.score,
                    candidate.item_id,
                ),
            )
            annotated = tuple((*head, *annotated[len(selected):]))
        return RerankResult(
            candidates=annotated,
            status="ok",
            mode=self.mode,
            model_version=self.reranker.model_version,
            latency_ms=int((perf_counter() - started) * 1_000),
            scores=score_pairs,
        )
