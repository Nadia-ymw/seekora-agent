"""Environment-backed settings without secret values in logs or receipts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .defaults import (
    DEFAULT_CATALOG_RELATIVE_PATH,
    DEFAULT_EMBEDDING_BATCH_SIZE,
    DEFAULT_EMBEDDING_CACHE_RELATIVE_PATH,
    DEFAULT_EMBEDDING_DEVICE,
    DEFAULT_EMBEDDING_DIMENSION,
    DEFAULT_EMBEDDING_MODEL_ID,
    DEFAULT_EMBEDDING_REVISION,
    DEFAULT_QUERY_INSTRUCTION,
    DEFAULT_VECTOR_INDEX_RELATIVE_PATH,
)


@dataclass(frozen=True)
class EmbeddingConfig:
    """运行时装配 Qwen 与索引所需的完整、非敏感配置。"""

    model_id: str
    revision: str
    dimension: int
    query_instruction: str
    device: str
    cache_dir: str | None
    batch_size: int
    vector_index_path: str


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    catalog_path: str = Field(
        default=DEFAULT_CATALOG_RELATIVE_PATH,
        validation_alias=AliasChoices("SEEKORA_CATALOG_PATH", "SEARCH_REC_CATALOG_PATH"),
    )
    intent_resolver: Literal["rules", "openai"] = Field(
        default="rules",
        # Accept the pre-Seekora key during migration without advertising it to new users.
        validation_alias=AliasChoices("SEEKORA_INTENT_RESOLVER", "SEARCH_REC_INTENT_RESOLVER"),
    )
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str | None = Field(default=None, alias="OPENAI_MODEL")
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")
    openai_timeout_seconds: float = Field(
        default=30.0, gt=0, le=300, alias="OPENAI_TIMEOUT_SECONDS"
    )
    openai_max_retries: int = Field(default=2, ge=0, le=5, alias="OPENAI_MAX_RETRIES")
    behavior_queue_path: str | None = Field(
        default=None, alias="SEEKORA_BEHAVIOR_QUEUE_PATH"
    )
    profile_db_path: str | None = Field(
        default=None, alias="SEEKORA_PROFILE_DB_PATH"
    )
    request_replay_db_path: str | None = Field(
        default=None, alias="SEEKORA_REQUEST_REPLAY_DB_PATH"
    )
    session_db_path: str | None = Field(
        default=None, alias="SEEKORA_SESSION_DB_PATH"
    )
    session_ttl_seconds: int = Field(
        default=86_400, ge=300, alias="SEEKORA_SESSION_TTL_SECONDS"
    )
    session_max_messages: int = Field(
        default=40, ge=2, le=500, alias="SEEKORA_SESSION_MAX_MESSAGES"
    )
    receipt_db_path: str | None = Field(
        default=None, alias="SEEKORA_RECEIPT_DB_PATH"
    )
    receipt_retention_seconds: int = Field(
        default=30 * 86_400, ge=3600, alias="SEEKORA_RECEIPT_RETENTION_SECONDS"
    )
    rerank_mode: Literal["off", "challenger"] = Field(
        default="off", alias="SEEKORA_RERANK_MODE"
    )
    reranker_model: str | None = Field(
        default=None, alias="SEEKORA_RERANKER_MODEL"
    )
    rerank_top_n: int = Field(
        default=30, ge=1, le=50, alias="SEEKORA_RERANK_TOP_N"
    )
    semantic_local_files_only: bool = Field(
        default=True, alias="SEEKORA_SEMANTIC_LOCAL_FILES_ONLY"
    )
    embedding_mode: Literal["off", "challenger", "active"] = Field(
        default="off", alias="SEEKORA_EMBEDDING_MODE"
    )
    embedding_model: str = Field(
        default=DEFAULT_EMBEDDING_MODEL_ID, alias="SEEKORA_EMBEDDING_MODEL"
    )
    embedding_revision: str = Field(
        default=DEFAULT_EMBEDDING_REVISION, alias="SEEKORA_EMBEDDING_REVISION"
    )
    embedding_dimension: int = Field(
        default=DEFAULT_EMBEDDING_DIMENSION,
        ge=32,
        le=1024,
        alias="SEEKORA_EMBEDDING_DIMENSION",
    )
    embedding_query_instruction: str = Field(
        default=DEFAULT_QUERY_INSTRUCTION,
        alias="SEEKORA_EMBEDDING_QUERY_INSTRUCTION",
    )
    embedding_device: str = Field(
        default=DEFAULT_EMBEDDING_DEVICE, alias="SEEKORA_EMBEDDING_DEVICE"
    )
    embedding_cache_dir: str | None = Field(
        default=DEFAULT_EMBEDDING_CACHE_RELATIVE_PATH,
        alias="SEEKORA_EMBEDDING_CACHE_DIR",
    )
    embedding_batch_size: int = Field(
        default=DEFAULT_EMBEDDING_BATCH_SIZE,
        ge=1,
        le=512,
        alias="SEEKORA_EMBEDDING_BATCH_SIZE",
    )
    vector_index_path: str | None = Field(
        default=DEFAULT_VECTOR_INDEX_RELATIVE_PATH,
        alias="SEEKORA_VECTOR_INDEX_PATH",
    )
    qwen_rrf_weight: float = Field(
        default=1.0,
        gt=0.0,
        le=2.0,
        alias="SEEKORA_QWEN_RRF_WEIGHT",
    )

    def require_openai(self) -> tuple[str, str]:
        if self.openai_api_key is None or not self.openai_api_key.get_secret_value().strip():
            raise ValueError(
                "OPENAI_API_KEY is required when SEEKORA_INTENT_RESOLVER=openai"
            )
        if self.openai_model is None or not self.openai_model.strip():
            raise ValueError(
                "OPENAI_MODEL is required when SEEKORA_INTENT_RESOLVER=openai"
            )
        return self.openai_api_key.get_secret_value(), self.openai_model.strip()

    def safe_summary(self) -> dict[str, str | int | float | bool | None]:
        key_configured = bool(
            self.openai_api_key
            and self.openai_api_key.get_secret_value().strip()
        )
        return {
            "catalog_path": self.catalog_path,
            "intent_resolver": self.intent_resolver,
            "openai_model": self.openai_model,
            "openai_base_url": self.openai_base_url,
            "openai_timeout_seconds": self.openai_timeout_seconds,
            "openai_max_retries": self.openai_max_retries,
            "openai_api_key_configured": key_configured,
            "behavior_queue_path": self.behavior_queue_path,
            "profile_db_path": self.profile_db_path,
            "request_replay_db_path": self.request_replay_db_path,
            "session_db_path": self.session_db_path,
            "session_ttl_seconds": self.session_ttl_seconds,
            "session_max_messages": self.session_max_messages,
            "receipt_db_path": self.receipt_db_path,
            "receipt_retention_seconds": self.receipt_retention_seconds,
            "rerank_mode": self.rerank_mode,
            "reranker_model": self.reranker_model,
            "rerank_top_n": self.rerank_top_n,
            "semantic_local_files_only": self.semantic_local_files_only,
            "embedding_mode": self.embedding_mode,
            "embedding_model": self.embedding_model,
            "embedding_revision": self.embedding_revision,
            "embedding_dimension": self.embedding_dimension,
            "embedding_query_instruction": self.embedding_query_instruction,
            "embedding_device": self.embedding_device,
            "embedding_cache_dir": self.embedding_cache_dir,
            "embedding_batch_size": self.embedding_batch_size,
            "vector_index_path": self.vector_index_path,
            "qwen_rrf_weight": self.qwen_rrf_weight,
        }

    def require_reranker(self) -> str:
        """Challenger 开启时必须显式提供本地或可解析的模型标识。"""
        if self.reranker_model is None or not self.reranker_model.strip():
            raise ValueError(
                "SEEKORA_RERANKER_MODEL is required when SEEKORA_RERANK_MODE=challenger"
            )
        return self.reranker_model.strip()

    def require_embedding(self) -> EmbeddingConfig:
        """启用 Challenger 或 Active 时必须固定模型、指令、维度和索引。"""
        if not self.embedding_model.strip():
            raise ValueError(
                "SEEKORA_EMBEDDING_MODEL is required when SEEKORA_EMBEDDING_MODE is enabled"
            )
        if not self.embedding_revision.strip():
            raise ValueError("SEEKORA_EMBEDDING_REVISION must not be empty")
        if not self.embedding_query_instruction.strip():
            raise ValueError("SEEKORA_EMBEDDING_QUERY_INSTRUCTION must not be empty")
        if self.vector_index_path is None or not self.vector_index_path.strip():
            raise ValueError(
                "SEEKORA_VECTOR_INDEX_PATH is required when SEEKORA_EMBEDDING_MODE is enabled"
            )
        return EmbeddingConfig(
            model_id=self.embedding_model.strip(),
            revision=self.embedding_revision.strip(),
            dimension=self.embedding_dimension,
            query_instruction=self.embedding_query_instruction.strip(),
            device=self.embedding_device.strip() or "auto",
            cache_dir=(self.embedding_cache_dir.strip() if self.embedding_cache_dir else None),
            batch_size=self.embedding_batch_size,
            vector_index_path=self.vector_index_path.strip(),
        )

    def require_embedding_challenger(self) -> EmbeddingConfig:
        """兼容 M2 调用方；配置校验现已同时服务 Challenger 与 Active。"""
        return self.require_embedding()
