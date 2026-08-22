# Embedding 索引与语义复核

增量 D 的首个切片建立真实语义模型接入边界。Embedding 已提供受控 Active 模式，但在固定查询集质量门禁通过前，本地默认仍保持 Challenger；Cross-Encoder 继续只提供 Challenger。

## 数据流

```text
目录 Item ─→ EmbeddingProvider ─→ SQLiteVectorIndex / VectorIndex 端口
                                      ↓
Challenger：查询 ─→ TF-IDF 正式候选 ─→ RRF
                 └→ Qwen 对照 ─────→ metadata/Receipt（不参与融合）

Active：查询 ─→ Qwen 正式候选 ─────→ RRF
              └→ 失败或空结果时 TF-IDF 有界降级

RRF 候选 ─→ rerank 节点 ─→ Challenger 分数 ─→ Catalog/ACL/Constraint 复核
                └────────→ 模型失败时保持原始 RRF 顺序
```

## 端口和索引

- `EmbeddingProvider` 定义模型版本、维度、批量文档编码和查询编码；
- `VectorIndex` 定义版本、维度、搜索、Upsert、删除和内容哈希；
- `SQLiteVectorIndex` 是 5 万商品的默认实现：普通表保存内容哈希和版本元数据，`sqlite-vec/vec0` 保存 float32 向量并执行原生精确余弦 KNN；
- `VersionedVectorIndex` 的 JSON 实现仅保留为小样本兼容与端口契约测试；后续仍可替换为 ANN；
- 索引同时固定 Schema、索引实现、Embedding 模型版本和维度，版本不匹配时拒绝查询；
- 增量同步按 `searchable_text` 的 SHA-256 判断变化，只重算新增/变化文档并删除目录中已消失条目。

向量命中只包含 `item_id + score`。`EmbeddingSemanticIndex` 必须回到可信 Catalog 映射，并再次执行租户、状态和 ACL 过滤；向量库不能成为授权真值。

## Challenger、Active 与降级

`vector_search` 默认仍返回 TF-IDF 候选。启用 Embedding Challenger 后，同一工具额外执行向量检索，把候选 ID、分数、重叠数、版本和耗时写入非融合 `metadata`。Challenger 失败不影响主结果。

启用 Embedding Active 后，Qwen 候选直接参与 BM25+Qwen RRF，且不会在成功请求中重复扫描 TF-IDF。只有已知模型/索引故障或 Qwen 空结果才回退 TF-IDF。实际来源通过 `qwen/tfidf/tfidf_fallback` 写入候选，工具 `source_version` 与 Receipt 保存实际索引版本；所有候选之后仍执行 Catalog、租户、状态、ACL 和硬约束复核。

LangGraph 在召回和约束过滤之间增加 `rerank` 节点：

- `off`：完全跳过，不产生额外 SSE；
- `challenger`：保存 Cross-Encoder 分数，但保持 RRF 顺序；
- `active`：仅供代码级离线测试，当前环境配置不允许启用；
- 已知模型不可用时记录 `RERANKER_UNAVAILABLE` 并保持 RRF；分数数量错误等程序契约缺陷继续抛出。

重排分保存在独立 `rerank_score/rerank_mode` 字段中，不写入 `source_scores`，因此不会冒充第二召回源或改变充分性判断。重排器只能处理已有候选，不能生成新的商品 ID，之后仍必须通过 Catalog、ACL 和硬约束复核。

## 文件职责

- `application/semantic.py`：Embedding 和 VectorIndex 端口；
- `application/reranking.py`：重排端口、Challenger 编排和降级；
- `infrastructure/search/vector_index.py`：精确索引、增量同步和 SearchResult 适配；
- `infrastructure/search/sqlite_vector_index.py`：SQLite 元数据、批事务和 `sqlite-vec` KNN 适配；
- `infrastructure/embeddings/sentence_transformer.py`：本地开源 Embedding 适配器；
- `infrastructure/rerankers/cross_encoder.py`：本地 Cross-Encoder 适配器；
- `interfaces/cli.py`：批量构建/增量更新命令；
- `tests/test_semantic_retrieval.py`：版本、增量、ACL、Shadow、重排和故障降级测试。

## 当前边界

SQLite 方案移除了巨大 JSON 的启动解析和 Python 全表余弦循环，但 `vec0` 当前仍是精确扫描，不应描述为 ANN。真实 5 万商品索引已完成 Active 冒烟，但尚未在 300～500 条人工集上比较 BM25、TF-IDF、Embedding 和 Cross-Encoder，也没有形成完整质量、并发 P95、内存和成本报告，因此不能把 Active 描述为已通过 Phase 1 门禁或本地默认路径。
