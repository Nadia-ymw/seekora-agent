"""sentence-transformers 开源权重适配器，采用延迟加载避免影响默认启动。"""

from __future__ import annotations

from typing import Any, Sequence

from ...application.semantic import EmbeddingUnavailable


class SentenceTransformerEmbedding:
    """本地 SentenceTransformer 适配器；默认不允许隐式下载模型。"""

    def __init__(self, model_name_or_path: str, local_files_only: bool = True) -> None:
        if not model_name_or_path.strip():
            raise ValueError("embedding model name or path is required")
        self.model_name_or_path = model_name_or_path.strip()
        self.local_files_only = local_files_only
        self._model: Any | None = None

    @property
    def model_version(self) -> str:
        return f"sentence-transformers:{self.model_name_or_path}"

    @property
    def dimension(self) -> int:
        model = self._load()
        return int(model.get_sentence_embedding_dimension())

    def embed_documents(
        self, texts: Sequence[str], batch_size: int = 32
    ) -> list[list[float]]:
        model = self._load()
        try:
            vectors = model.encode(
                list(texts),
                batch_size=batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            raise EmbeddingUnavailable("embedding inference failed") from exc
        return [[float(value) for value in vector] for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_documents([text], batch_size=1)
        return vectors[0]

    def _load(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                self.model_name_or_path,
                local_files_only=self.local_files_only,
            )
        except Exception as exc:
            raise EmbeddingUnavailable(
                "sentence-transformers dependency or local model is unavailable"
            ) from exc
        return self._model
