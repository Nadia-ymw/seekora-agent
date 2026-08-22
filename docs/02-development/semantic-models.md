# 本地语义模型配置

默认安装不包含大模型依赖，API 继续使用 TF-IDF/RRF。需要本地评测开源权重时安装可选依赖：

```powershell
python -m pip install -e ".[dev,semantic]"
```

## 构建或增量更新向量索引

首次运行会批量编码全部目录，后续使用同一命令只处理文本发生变化的条目：

```powershell
python -m seekora_agent.interfaces.cli build-vector-index `
  --catalog data/processed/kuaisearch-electronics/items.jsonl `
  --output .runtime/qwen3-embedding-0.6b-vectors.sqlite3 `
  --model Qwen/Qwen3-Embedding-0.6B `
  --batch-size 16
```

不传参数时，上述 Catalog、模型、批大小和输出路径就是默认值，模型缓存默认统一使用 `.runtime/model-cache`。索引由普通 SQLite 元数据/条目表和 `sqlite-vec` 的 `vec0` 向量表组成；每个 Embedding 批次只提交一次事务，重复执行时按内容哈希增量更新。CLI 只接受 `.sqlite`、`.sqlite3` 或 `.db` 输出，不再构建旧 JSON 索引。

默认只允许读取本地缓存或本地模型目录，避免命令意外下载大文件。首次明确允许下载时增加 `--allow-download`；模型发生变化时必须指定 `--rebuild` 并生成新索引，不能复用旧模型向量。构建索引无需先启动 API。

## 启用 Embedding Challenger

```powershell
$env:SEEKORA_EMBEDDING_MODE = "challenger"
$env:SEEKORA_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
$env:SEEKORA_VECTOR_INDEX_PATH = ".runtime/qwen3-embedding-0.6b-vectors.sqlite3"
```

先完成一次成功构建，再设置 `challenger`。启用后 `vector_search` 的正式候选仍来自 TF-IDF；Embedding 结果只出现在工具调用 `metadata.embedding_challenger` 和 Receipt 中。索引缺失、损坏或模型/维度/Catalog 快照不一致时，Embedding Challenger 会标记降级，TF-IDF/BM25 仍可服务。

## 受控启用 Embedding Active

```powershell
$env:SEEKORA_EMBEDDING_MODE = "active"
```

`active` 会让 Qwen 成为 `vector_search` 的正式候选源，并与 BM25 执行 RRF；同一请求不会预先执行 TF-IDF 全表扫描。模型调用失败、索引不可用、版本不匹配或 Qwen 返回空结果时，工具才执行一次 TF-IDF 降级。候选 `source_scores`、SSE 和 Receipt 会分别记录 `qwen`、`tfidf` 或 `tfidf_fallback` 及实际索引版本。

当前本地默认仍保持 `challenger`。只有固定查询集证明质量不退化且暖态 P95 不高于 2 秒后，才允许把本机默认切为 `active`。

`SEEKORA_QWEN_RRF_WEIGHT` 控制 Active 中 Qwen 相对 BM25 的 RRF 权重，取值范围 `(0, 2]`，默认 `1.0`。该值必须通过开发集选择并在留出集复验，不允许直接在验收集上手工调优。当前 0.25～2.0 扫描仍选择 1.0，因此本地配置不修改。

使用 processed 固定查询集执行对照：

```powershell
python -m seekora_agent.interfaces.cli compare-recall `
  --development-golden data/golden/processed-recall-development.jsonl `
  --golden data/golden/processed-recall-queries.jsonl `
  --runs 3
```

命令会拒绝不存在于当前 Catalog 的标注商品、空相关性、重复查询 ID 和越界相关性等级，并分别报告冷启动、暖态 P50/P95、Recall、MRR、NDCG、零结果率和排序稳定率。当前首轮门禁结果为质量未通过，详见[质量门禁报告](../03-testing/m3-recall-quality-report.md)。

`sqlite-vec` 当前执行的是原生 C 精确 KNN，不是 ANN。相比 Python 读取巨大 JSON 后逐条计算，它显著减少启动内存、文件解析和 Python 循环开销；如果后续规模和 P95 需要近似索引，可在不改 `VectorIndex` 端口的前提下替换实现。

## 启用 Cross-Encoder Challenger

```powershell
$env:SEEKORA_RERANK_MODE = "challenger"
$env:SEEKORA_RERANKER_MODEL = "BAAI/bge-reranker-base"
$env:SEEKORA_RERANK_TOP_N = "30"
```

结果会新增 `rerank_score` 和 `rerank_mode`，并发送 `rerank.completed`；顺序仍由 RRF 决定。模型缺失或推理失败时事件和 Receipt 标记降级。`SEEKORA_SEMANTIC_LOCAL_FILES_ONLY=false` 可以允许运行时解析非本地模型，但开发和发布环境应优先预下载、校验并固定权重。

Cross-Encoder 重排仍不开放环境级 `active` 模式。正式切换重排路径前，必须先在固定人工集上证明质量增益并满足延迟、资源和成本预算。
