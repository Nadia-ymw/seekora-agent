"""本地 MVP 各入口共享的非敏感默认配置。"""

DEFAULT_CATALOG_RELATIVE_PATH = "data/processed/kuaisearch-electronics/items.jsonl"

# M2 默认模型配置集中在此处，CLI、运行时和文档使用同一组值。
DEFAULT_EMBEDDING_MODEL_ID = "Qwen/Qwen3-Embedding-0.6B"
DEFAULT_EMBEDDING_REVISION = "main"
DEFAULT_EMBEDDING_DIMENSION = 1024
DEFAULT_EMBEDDING_BATCH_SIZE = 16
DEFAULT_EMBEDDING_DEVICE = "auto"
DEFAULT_EMBEDDING_CACHE_RELATIVE_PATH = ".runtime/model-cache"
DEFAULT_QUERY_INSTRUCTION = (
    "为给定的商品搜索查询，检索满足用户需求的商品"
)
DEFAULT_VECTOR_INDEX_RELATIVE_PATH = ".runtime/qwen3-embedding-0.6b-vectors.sqlite3"
