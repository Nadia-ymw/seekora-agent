# Fast Path 设计与新增文件说明

## 1. 本阶段目标

Fast Path 将明确、可直接执行的自然语言请求转换为结构化意图，通过关键词和语义基础召回生成候选；登录且双重授权的用户可增加行为提升信号。随后使用 RRF 融合，并执行确定性硬约束与最终 Catalog 校验。

```text
原始查询
→ 规则意图解析（默认）或 OpenAI 结构化意图解析（可选）
→ catalog_search + vector_search + 可选 behavior_recall 并行召回
→ Reciprocal Rank Fusion
→ Constraint Engine
→ Catalog 权威校验与证据
→ Top-K 结果 + Receipt
```

当前规则解析器和内存语义索引是可替换基线。应用层只依赖端口，后续接入 LLM、Embedding 和 OpenSearch 不需要重写 Runtime。

当前已提供 `LangChainLLMIntentResolver`。启用后它使用结构化输出生成同一个 `ResolvedIntent` 契约；失败时自动回退规则解析器，因此后续召回与约束节点保持不变。

## 2. 新增领域文件

### `domain/fast_path.py`

定义 Fast Path 各阶段之间的稳定数据契约：

- `ResolvedIntent`：模式、领域、净化后的检索文本、硬约束、软偏好、置信度和解析器版本；
- `FusedCandidate`：RRF 融合后的候选、各召回源原始分和召回原因；
- `VerifiedCandidate`：通过 Catalog 和硬约束复核的最终候选，并携带权威证据；
- `ConstraintFilterResult`：通过候选和按原因聚合的过滤统计。

这些对象不依赖 FastAPI、搜索引擎或数据库，使数据契约可以被回放器、Worker 和在线 API 共同使用。

## 3. 新增应用层文件

### `application/intent.py`

定义 `IntentResolver` Protocol。Runtime 只调用 `resolve(query)`，不关心实现来自规则、小模型还是 LLM。替换解析器时只修改 `bootstrap.py` 的依赖装配。

### `application/recall.py`

实现 `RecallOrchestrator`：

1. 为每路召回消耗一次工具预算；
2. 使用 `asyncio.gather` 并行调用基础召回，并为登录用户按 Consent 启用 `behavior_recall`；
3. 将单源异常转换为标准错误结果，只要至少一路成功即可继续；
4. 以 canonical `item_id` 去重；
5. 使用 RRF 融合名次；
6. 保存各源原始分、版本和耗时供 Receipt 使用。

当前公式：

```text
rrf_score(item) = Σ 1 / (60 + rank_in_source)
```

RRF 不直接比较 BM25 与语义分数，避免不同分数空间未经校准直接相加。

### `application/catalog.py`

定义 `CatalogRepository` 端口。Constraint Engine 通过该端口按 canonical ID 获取权威 Item，不直接依赖 JSONL、PostgreSQL 或远程 Catalog API。

### `application/constraints.py`

实现确定性 Constraint Engine：

- 再次检查租户、状态和权限；
- 支持 `eq`、`in`、`lte`、`gte`；
- 字段缺失或类型不兼容时按不满足处理；
- Item 不存在时拒绝展示；
- 为过滤结果生成 `ITEM_NOT_FOUND`、`PERMISSION_DENIED`、`CONSTRAINT_PRICE` 等原因码；
- 对通过的约束生成 `catalog://item/{id}` 权威证据。

这层是最终安全边界。LLM 或召回工具都不能绕过它。

## 4. 新增基础设施文件

### `infrastructure/intent/rule_based.py`

实现首个 `IntentResolver`：

- 识别 SEARCH/RECOMMEND；
- 识别笔记本领域；
- 抽取价格上限、内存下限、续航下限和重量上限；
- 识别轻薄、高性能、长续航、便携等软偏好；
- 从检索文本中移除已经结构化的数值约束；
- 输出置信度、歧义和 `rules-zh-v1` 版本。

它只覆盖明确中文表达，用作可回放基线。复合否定、跨类目和上下文约束将在 LLM/小模型解析器中实现。

### `infrastructure/search/semantic.py`

实现 `InMemorySemanticIndex`。它使用与 BM25 相同的中文字符/双字 Token，计算 TF-IDF 向量和余弦相似度，用于验证第二路召回、并行执行和融合。

这不是生产 Embedding 服务。正式实现应替换为领域 Embedding + ANN/OpenSearch Vector，同时保持 Tool 输出协议不变。

### `infrastructure/tools/vector_search.py`

把 `InMemorySemanticIndex` 包装成标准 `vector_search` Tool，统一返回候选 ID、标题、分数、召回原因和数据源版本。

### `infrastructure/catalog_repository.py`

提供开发态 `InMemoryCatalogRepository`，按 Item ID 返回目录真相。正式环境应新增远程 Catalog 或数据库适配器，并保留相同端口。

## 5. Runtime 和 Receipt 变化

`application/runtime.py` 的事件顺序升级为：

```text
request.accepted
→ intent.resolved
→ recall.started
→ recall.completed
→ constraints.applied
→ result
→ done
```

Receipt 新增：

- 完整结构化意图及解析器版本；
- 各召回源的参数、状态、延迟和数据版本；
- 最终候选 ID；
- 按原因聚合的过滤数量；
- `rrf-v1` 排序配置版本。

## 6. `bootstrap.py` 装配变化

启动时加载一次目录并创建：

- `BM25Baseline`；
- `InMemorySemanticIndex`；
- LangChain `catalog_search` StructuredTool；
- LangChain `vector_search` StructuredTool；
- `RuleBasedIntentResolver`；
- 可选的 `LangChainLLMIntentResolver` 与 `ChatOpenAI`；
- `RecallOrchestrator`；
- `ConstraintEngine`；
- `InMemoryCatalogRepository`。

所有具体实现只在 `bootstrap.py` 组合，应用层不主动创建基础设施对象。

## 7. 新增测试

`tests/test_fast_path.py` 覆盖：

- 从中文查询抽取价格和内存约束；
- 识别推荐模式和笔记本领域；
- 关键词/语义双路召回；
- RRF 候选包含两个来源分数；
- 价格约束确定性过滤；
- Receipt 保存意图、两次工具调用和过滤原因。

当前全项目共 21 个自动化测试，并包含单召回源超时、LangGraph 结构验证、LLM 配置安全与无网络故障回退。

## 8. 当前边界

- 规则解析不等同于完整自然语言理解；LLM 解析也必须通过确定性约束与目录复核；
- TF-IDF 余弦只是语义召回的内存替身；
- 暂无 LTR、Cross-Encoder 和多样性排序；行为召回已有授权安全基线；
- Evidence 当前主要来自硬约束字段，尚未生成自然语言解释；
- 多轮约束已支持结构化 AI Patch、确定性归并和规则降级；
- Deep Path、Probe 和一次 Replan 已实现；
- 正式性能仍需在真实 OpenSearch/Embedding/Catalog 上测试。

下一增量优先实现 Item Detail 与证据解释链路；LTR 按当前开发条件暂缓。
