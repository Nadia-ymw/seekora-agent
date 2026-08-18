"""OpenAI chat-model factory for LangChain."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from ...config.settings import AppSettings


def build_openai_chat_model(settings: AppSettings) -> ChatOpenAI:
    api_key, model = settings.require_openai()
    arguments: dict = {
        "model": model,
        "api_key": api_key,
        "temperature": 0,
        "timeout": settings.openai_timeout_seconds,
        "max_retries": settings.openai_max_retries,
    }
    if settings.openai_base_url:
        arguments["base_url"] = settings.openai_base_url
    return ChatOpenAI(**arguments)
