from __future__ import annotations

import os
from pathlib import Path

from .application.intent import IntentResolver
from .application.runtime import AgentRuntime
from .application.constraints import ConstraintEngine
from .application.recall import RecallOrchestrator
from .application.workflow import LangChainFastPathWorkflow
from .config.settings import AppSettings
from .infrastructure.catalog import load_items
from .infrastructure.catalog_repository import InMemoryCatalogRepository
from .infrastructure.intent.langchain_llm import LangChainLLMIntentResolver
from .infrastructure.intent.rule_based import RuleBasedIntentResolver
from .infrastructure.llm.openai import build_openai_chat_model
from .infrastructure.search.bm25 import BM25Baseline
from .infrastructure.search.semantic import InMemorySemanticIndex
from .infrastructure.stores.memory import (
    InMemoryCancellationRegistry,
    InMemoryReceiptStore,
    InMemorySessionStore,
)
from .infrastructure.tools.catalog_search import build_catalog_search_tool
from .infrastructure.tools.vector_search import build_vector_search_tool
from .interfaces.http.api import create_app


def build_intent_resolver(settings: AppSettings) -> IntentResolver:
    rules = RuleBasedIntentResolver()
    if settings.intent_resolver == "rules":
        return rules
    # 构建llm
    model = build_openai_chat_model(settings)
    return LangChainLLMIntentResolver.from_chat_model(
        model=model,
        model_name=settings.openai_model or "unknown",
        fallback=rules,   # 当大模型调用失败时，降级到规则
    )


def build_runtime(
    catalog_path: str | Path | None = None,
    settings: AppSettings | None = None,
) -> AgentRuntime:
    # 加载商品
    project_root = Path(__file__).resolve().parents[2]
    settings = settings or AppSettings(_env_file=project_root / ".env")
    # The legacy variable remains as a migration fallback for existing local .env files.
    path = Path(catalog_path or os.getenv(
        "SEEKORA_CATALOG_PATH",
        os.getenv("SEARCH_REC_CATALOG_PATH", project_root / "data" / "sample" / "items.jsonl"),
    ))
    # 加载目录数据（JSONL格式）
    items = load_items(path)
    # 构建搜索索引
    # BM25
    baseline = BM25Baseline(items)
    # 向量相似度
    semantic = InMemorySemanticIndex(items)
    # 包装成LangChain工具
    tools = [
        build_catalog_search_tool(baseline, source_version=path.name),
        build_vector_search_tool(semantic, source_version=f"{path.name}:tfidf-v1"),
    ]
    # 调用工作流
    # 用户输入 → 意图解析 → 召回(并行执行两个搜索) → 约束过滤 → 返回结果
    recall = RecallOrchestrator(tools)
    workflow = LangChainFastPathWorkflow(
        intent_resolver=build_intent_resolver(settings),
        recall=recall,
        constraint_engine=ConstraintEngine(InMemoryCatalogRepository(items)),
    )
    return AgentRuntime(
        workflow=workflow,
        sessions=InMemorySessionStore(),    # 会话管理（内存）
        receipts=InMemoryReceiptStore(),    # 收据/历史记录
        cancellations=InMemoryCancellationRegistry(),   # 取消操作管理
    )


app = create_app(build_runtime())
