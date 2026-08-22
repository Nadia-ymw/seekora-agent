"""sentence-transformers CrossEncoder 适配器。"""

from __future__ import annotations

import asyncio
from typing import Any, Sequence

from ...application.reranking import RerankerUnavailable


class SentenceTransformerCrossEncoder:
    """本地 CrossEncoder 适配器；加载和推理失败均触发确定性 RRF 降级。"""

    def __init__(self, model_name_or_path: str, local_files_only: bool = True) -> None:
        if not model_name_or_path.strip():
            raise ValueError("reranker model name or path is required")
        self.model_name_or_path = model_name_or_path.strip()
        self.local_files_only = local_files_only
        self._model: Any | None = None

    @property
    def model_version(self) -> str:
        return f"cross-encoder:{self.model_name_or_path}"

    async def score(self, query: str, documents: Sequence[str]) -> list[float]:
        try:
            model = await asyncio.to_thread(self._load)
            pairs = [(query, document) for document in documents]
            values = await asyncio.to_thread(model.predict, pairs, show_progress_bar=False)
        except RerankerUnavailable:
            raise
        except Exception as exc:
            raise RerankerUnavailable("cross-encoder inference failed") from exc
        return [float(value) for value in values]

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(
                self.model_name_or_path,
                local_files_only=self.local_files_only,
            )
        except Exception as exc:
            raise RerankerUnavailable(
                "sentence-transformers dependency or local reranker is unavailable"
            ) from exc
        return self._model
