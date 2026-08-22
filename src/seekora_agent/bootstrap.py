from __future__ import annotations

import json
from pathlib import Path

from .application.behavior import BehaviorService
from .application.intent import IntentResolver
from .application.exposure import ExposureService
from .application.profile import ProfileService
from .application.runtime import AgentRuntime
from .application.session_context import SessionContextResolver
from .application.constraints import ConstraintEngine
from .application.recall import RecallOrchestrator
from .application.reranking import RerankOrchestrator
from .application.semantic import VectorIndexMismatch
from .application.tool_registry import LangChainToolRegistry
from .application.workflow import LangChainFastPathWorkflow
from .config.settings import AppSettings
from .infrastructure.catalog import (
    catalog_snapshot_sha256,
    load_items,
    resolve_catalog_path,
)
from .infrastructure.catalog_repository import InMemoryCatalogRepository
from .infrastructure.intent.langchain_llm import LangChainLLMIntentResolver
from .infrastructure.intent.rule_based import RuleBasedIntentResolver
from .infrastructure.llm.openai import build_openai_chat_model
from .infrastructure.search.bm25 import BM25Baseline
from .infrastructure.search.semantic import InMemorySemanticIndex
from .infrastructure.search.vector_index import (
    EmbeddingSemanticIndex,
    UnavailableEmbeddingSemanticIndex,
    VersionedVectorIndex,
)
from .infrastructure.search.sqlite_vector_index import SQLiteVectorIndex
from .infrastructure.embeddings.qwen3 import Qwen3Embedding
from .infrastructure.rerankers.cross_encoder import SentenceTransformerCrossEncoder
from .infrastructure.session_context.langchain_llm import (
    LangChainLLMSessionContextPatchResolver,
)
from .infrastructure.session_context.rule_based import (
    RuleBasedSessionContextPatchResolver,
)
from .infrastructure.stores.memory import (
    InMemoryCancellationRegistry,
    InMemoryBehaviorStore,
    InMemoryExposureStore,
)
from .infrastructure.stores.sqlite_event_queue import SQLiteBehaviorEventQueue
from .infrastructure.stores.sqlite_profile import SQLiteProfileStore
from .infrastructure.stores.sqlite_receipt import SQLiteReceiptStore
from .infrastructure.stores.sqlite_request_replay import SQLiteRequestReplayStore
from .infrastructure.stores.sqlite_session import SQLiteSessionStore
from .infrastructure.tools.catalog_search import build_catalog_search_tool
from .infrastructure.tools.behavior_recall import build_behavior_recall_tool
from .infrastructure.tools.vector_search import build_vector_search_tool
from .infrastructure.tools.item_detail import build_item_detail_tool
from .interfaces.http.api import create_app
from .domain.test_account import build_default_test_account


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


def build_session_context_resolver(settings: AppSettings) -> SessionContextResolver:
    """按意图解析配置装配结构化 AI，并始终保留本地规则降级。"""
    rules = RuleBasedSessionContextPatchResolver()
    if settings.intent_resolver == "rules":
        return SessionContextResolver(rules)
    model = build_openai_chat_model(settings)
    patch_resolver = LangChainLLMSessionContextPatchResolver.from_chat_model(
        model=model,
        model_name=settings.openai_model or "unknown",
        fallback=rules,
    )
    return SessionContextResolver(patch_resolver)


def build_runtime(
    catalog_path: str | Path | None = None,
    settings: AppSettings | None = None,
) -> AgentRuntime:
    # 1.初始化与加载商品目录
    # 加载商品
    project_root = Path(__file__).resolve().parents[2]
    settings = settings or AppSettings(_env_file=project_root / ".env")
    # CLI、API 与 Web 测试台统一从 Settings 取目录；相对路径始终相对项目根目录。
    path = resolve_catalog_path(catalog_path or settings.catalog_path)
    # 加载目录数据（JSONL格式）
    items = load_items(path)
    catalog_snapshot = catalog_snapshot_sha256(path)
    catalog_version = f"sha256:{catalog_snapshot}:items:{len(items)}"

    # 2. 构建搜索索引
    # BM25
    baseline = BM25Baseline(items)
    # 向量相似度
    semantic = InMemorySemanticIndex(items)
    embedding_index = None

    # 3. Challenger 仅审计；Active 作为正式语义源，异常时由工具有界降级到 TF-IDF。
    if settings.embedding_mode in {"challenger", "active"}:
        embedding_config = settings.require_embedding()
        vector_path = Path(embedding_config.vector_index_path)
        if not vector_path.is_absolute():
            vector_path = project_root / vector_path
        cache_dir = embedding_config.cache_dir
        if cache_dir and not Path(cache_dir).is_absolute():
            cache_dir = str(project_root / cache_dir)
        embedding = Qwen3Embedding(
            embedding_config.model_id,
            revision=embedding_config.revision,
            dimension=embedding_config.dimension,
            query_instruction=embedding_config.query_instruction,
            device=embedding_config.device,
            cache_dir=cache_dir,
            local_files_only=settings.semantic_local_files_only,
        )
        try:
            # 启动时同时校验模型、维度和 Catalog 快照；权重仍延迟到首次查询加载。
            vector_index = (
                VersionedVectorIndex.load(
                    vector_path,
                    expected_embedding_version=embedding.model_version,
                    expected_dimension=embedding.dimension,
                    expected_catalog_snapshot_sha256=catalog_snapshot,
                    expected_item_count=len(items),
                )
                if vector_path.suffix.lower() == ".json"
                else SQLiteVectorIndex.load(
                    vector_path,
                    expected_embedding_version=embedding.model_version,
                    expected_dimension=embedding.dimension,
                    expected_catalog_snapshot_sha256=catalog_snapshot,
                    expected_item_count=len(items),
                )
            )
            # 任何不一致都会触发 VectorIndexMismatch，错误索引不会进入检索链路
            embedding_index = EmbeddingSemanticIndex(items, embedding, vector_index)
        except (FileNotFoundError, OSError, json.JSONDecodeError, VectorIndexMismatch) as exc:
            # 错误索引绝不参与查询，但启动保留 TF-IDF，并在 Challenger 审计中标记降级。
            embedding_index = UnavailableEmbeddingSemanticIndex(
                source_version=f"unavailable:{embedding.model_version}",
                reason=str(exc),
            )
    test_account = build_default_test_account()
    profile_path = Path(
        settings.profile_db_path
        or project_root / ".runtime" / "long-term-profiles.sqlite3"
    )

    # 4. 长期画像使用 SQLite 持久化；预置账户只在数据库中不存在时写入。
    profile_service = ProfileService(
        SQLiteProfileStore(profile_path, [test_account.initial_profile])
    )
    exposure_service = ExposureService(InMemoryExposureStore(), profile_service)
    queue_path = Path(
        settings.behavior_queue_path
        or project_root / ".runtime" / "behavior-events.sqlite3"
    )
    behavior_service = BehaviorService(
        InMemoryBehaviorStore(),
        profile_service,
        exposure_service,
        SQLiteBehaviorEventQueue(queue_path),
    )
    request_replay_path = Path(
        settings.request_replay_db_path
        or project_root / ".runtime" / "request-replays.sqlite3"
    )
    session_path = Path(
        settings.session_db_path
        or project_root / ".runtime" / "sessions.sqlite3"
    )
    receipt_path = Path(
        settings.receipt_db_path
        or project_root / ".runtime" / "receipts.sqlite3"
    )

    # 5. 包装成 LangChain 工具；行为召回与反馈 API 共享同一授权存储实例。
    catalog_repository = InMemoryCatalogRepository(items)
    tools = [
        build_catalog_search_tool(baseline, source_version=catalog_version),
        # Active 不预先执行 TF-IDF 全表扫描；只有 Qwen 失败或为空时才触发 fallback。
        build_vector_search_tool(
            embedding_index if settings.embedding_mode == "active" else semantic,
            source_version=(
                embedding_index.source_version
                if settings.embedding_mode == "active" and embedding_index is not None
                else f"{catalog_version}:tfidf-v1"
            ),
            source_name="qwen" if settings.embedding_mode == "active" else "tfidf",
            fallback_index=semantic if settings.embedding_mode == "active" else None,
            fallback_version=f"{catalog_version}:tfidf-v1",
            challenger_index=(
                embedding_index if settings.embedding_mode == "challenger" else None
            ),
            challenger_version=(
                embedding_index.source_version if embedding_index else None
            ),
        ),
        build_behavior_recall_tool(behavior_service, items),
    ]
    item_detail_registry = LangChainToolRegistry([
        build_item_detail_tool(catalog_repository, source_version=catalog_version),
    ])

    # 6.编排工作流
    # 用户输入 → 意图解析 → 召回(并行执行两个搜索) → 约束过滤 → 返回结果
    recall = RecallOrchestrator(
        tools,
        source_tools=("catalog_search", "vector_search", "behavior_recall"),
        # 仅 Active 的 qwen 候选使用可调权重；TF-IDF、fallback 和行为源保持原契约。
        source_weights={"qwen": settings.qwen_rrf_weight},
    )
    workflow = LangChainFastPathWorkflow(
        intent_resolver=build_intent_resolver(settings),
        recall=recall,
        constraint_engine=ConstraintEngine(catalog_repository),
        session_context=build_session_context_resolver(settings),
        item_detail_registry=item_detail_registry,
        reranker=(
            RerankOrchestrator(
                catalog_repository,
                SentenceTransformerCrossEncoder(
                    settings.require_reranker(),
                    local_files_only=settings.semantic_local_files_only,
                ),
                mode="challenger",
                top_n=settings.rerank_top_n,
            )
            if settings.rerank_mode == "challenger"
            else RerankOrchestrator(catalog_repository, mode="off")
        ),
    )

    # 7.组装 AgentRuntime 并绑定 Web 服务
    return AgentRuntime(
        workflow=workflow,
        # 会话与执行回执落入 SQLite，进程重启后仍可继续查询。
        sessions=SQLiteSessionStore(
            session_path,
            ttl_seconds=settings.session_ttl_seconds,
            max_messages=settings.session_max_messages,
        ),
        receipts=SQLiteReceiptStore(
            receipt_path,
            retention_seconds=settings.receipt_retention_seconds,
        ),
        cancellations=InMemoryCancellationRegistry(),   # 取消操作管理
        profiles=profile_service,  # 经用户授权的长期画像
        behaviors=behavior_service,  # 经用户授权的曝光与行为反馈
        exposures=exposure_service,  # 服务端生成并校验真实曝光清单
        test_account=test_account,  # 本地联调用只读测试身份
        request_replays=SQLiteRequestReplayStore(request_replay_path),
    )


app = create_app(build_runtime())
