"""Qwen3 Embedding 适配器：文档无指令，查询使用商品检索任务指令。"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Sequence

from ...application.semantic import EmbeddingUnavailable


class Qwen3Embedding:
    """延迟加载 Qwen3 权重，并固定模型修订版、维度和查询指令。"""

    def __init__(
        self,
        model_id: str,
        *,
        revision: str,
        dimension: int,
        query_instruction: str,
        device: str = "auto",
        cache_dir: str | None = None,
        local_files_only: bool = True,
    ) -> None:
        if not model_id.strip() or not revision.strip():
            raise ValueError("model_id and revision are required")
        if not 32 <= dimension <= 1024:
            raise ValueError("Qwen3 embedding dimension must be between 32 and 1024")
        if not query_instruction.strip():
            raise ValueError("query instruction is required")
        self.model_id = model_id.strip()
        self.model_revision = revision.strip()
        self._dimension = dimension
        self.query_instruction = query_instruction.strip()
        self.device = device.strip() or "auto"
        self.cache_dir = cache_dir
        self.local_files_only = local_files_only
        self._model: Any | None = None

    @property
    def model_version(self) -> str:
        instruction_hash = hashlib.sha256(
            self.query_instruction.encode("utf-8")
        ).hexdigest()[:12]
        return (
            f"qwen3:{self.model_id}@{self.model_revision}:dim={self.dimension}:"
            f"query={instruction_hash}"
        )

    @property
    def dimension(self) -> int:
        return self._dimension
    
    # 商品文档直接编码，不添加指令
    def embed_documents(
        self, texts: Sequence[str], batch_size: int = 8
    ) -> list[list[float]]:
        """商品文档保持原文编码，不添加查询任务指令。"""
        return self._encode(texts, batch_size=batch_size)

    # 用户查询添加指令
    def embed_query(self, text: str) -> list[float]:
        """按 Qwen3 推荐格式给查询增加任务指令，与文档编码明确区分。"""
        instructed = f"Instruct: {self.query_instruction}\nQuery: {text.strip()}"
        return self._encode([instructed], batch_size=1)[0]

    def _encode(self, texts: Sequence[str], *, batch_size: int) -> list[list[float]]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        model = self._load()
        try:
            vectors = model.encode(
                list(texts),
                batch_size=batch_size,
                normalize_embeddings=False,
                show_progress_bar=False,
                truncate_dim=self.dimension,
            )
        except Exception as exc:
            raise EmbeddingUnavailable("Qwen3 embedding inference failed") from exc
        return [self._normalize(vector) for vector in vectors]

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer

            kwargs: dict[str, Any] = {
                "revision": self.model_revision,
                "cache_folder": self.cache_dir,
                "local_files_only": self.local_files_only,
                "truncate_dim": self.dimension,
            }
            if self.device != "auto":
                kwargs["device"] = self.device
            self._model = SentenceTransformer(self.model_id, **kwargs)
            dimension_getter = getattr(
                self._model,
                "get_embedding_dimension",
                self._model.get_sentence_embedding_dimension,
            )
            native_dimension = int(dimension_getter())
            if native_dimension < self.dimension:
                raise ValueError(
                    f"model dimension {native_dimension} is smaller than {self.dimension}"
                )
        except Exception as exc:
            hint = (
                " Ensure --cache-dir points to the downloaded model cache, or rerun "
                "build-vector-index with --allow-download."
                if self.local_files_only
                else " Check the Hugging Face connection and configured cache directory."
            )
            raise EmbeddingUnavailable(
                "Qwen3 model dependency, weights or configured device is unavailable."
                + hint
            ) from exc
        return self._model

    @staticmethod
    def _normalize(vector: Sequence[float]) -> list[float]:
        values = [float(value) for value in vector]
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]
