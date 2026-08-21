"""Environment-backed settings without secret values in logs or receipts."""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
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
        }
