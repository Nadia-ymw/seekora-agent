# 三层模型搜索推荐 Agent 重构主控执行规划

> 制定日期：2026-08-22  
> 适用范围：`seekora-agent` 单实例本地 MVP 后续重构  
> 规划级别：主控执行规划（阶段任务、代码设计和测试验收均不得与本文冲突）  
> 依据：《搜索推荐Agent技术路线.md》《2026大模型搜广推论文调研报告.md》、M3 质量报告及 2026-08-22 DS 实际调用测试  
> 当前基线：50,000 条电子商品，BM25+TF-IDF 为默认正式路径，Qwen Embedding 为 Challenger，CrossEncoder 关闭  
> 禁止事项：不训练 LTR；不让通用 LLM 扫描全量商品、绕过 ACL/硬约束或生成目录外事实

## 1. 文档目的与执行规则

本文不是概念路线图，而是后续分步骤重构的唯一主控清单。后续每次开发必须从本文领取一个边界明确的工作包，完成后回写状态、测试证据、指标和遗留问题。不得因为某个阶段已经“有代码”就跳过其质量门禁，也不得在未完成上游契约时提前切换默认路径。

执行优先级如下：

1. 本文的架构不变量和阶段门禁；
2. 根目录《搜索推荐Agent技术路线.md》的职责边界；
3. `docs/01-architecture` 中的具体设计；
4. `docs/02-development` 中的操作说明；
5. 单次开发任务的局部实现方案。

如局部实现需要偏离本文，必须先新增 ADR 或在本文“决策记录”中说明：偏离原因、替代方案、影响范围、验证方法和回滚方式。未记录的偏离不得进入下一阶段。

## 2. 重构背景与已确认问题

### 2.1 M3 检索质量基线

固定留出集共 13 条查询，历史结果如下：

| 路径 | Recall@10 | MRR@10 | NDCG@10 | 零结果率 |
|---|---:|---:|---:|---:|
| BM25+TF-IDF 基线 | 0.851282 | 0.938462 | 0.744787 | 0 |
| BM25+Qwen Active | 0.691026 | 0.884615 | 0.667385 | 0 |

Qwen Active 的主要错误是跨品类语义误召回。扫描 Qwen RRF 权重 0.25～2.0 后，开发集仍选择 1.0，说明全局权重不能修复单商品级别的类目错误。

### 2.2 DS 实际调用基线

`.env` 已配置 `SEEKORA_INTENT_RESOLVER=openai`、DS 模型、兼容 Base URL 和 API Key，但当前 LangChain 默认使用 `json_schema`：

- `json_schema`：Provider 返回 `response_format` 不可用；
- `function_calling`：Thinking 模式不支持当前 `tool_choice`；
- `json_mode`：现有 Prompt 未明确包含 JSON 要求，Provider 拒绝请求；
- 生产代码捕获所有异常并静默回退规则。

13 条留出查询中 DS 成功 0 条、规则回退 13 条。LLM 配置组的 Recall@10、MRR@10、NDCG@10 分别为 0.774359、0.923077、0.715029，零结果率为 0.076923；下降来自一条查询在失败耗时后进入澄清终态。该结果不能视为 DS 质量结果，只能视为 Provider 兼容和预算失败结果。

### 2.3 当前代码与目标路线的关键差距

1. DS 只接入首轮 Intent 和多轮 Constraint Patch，且当前协议不兼容；
2. `domain` 是自由字符串，没有进入受限商品类型标准化契约；
3. `product_type`、硬约束、软偏好和负偏好没有完整传给召回工具；
4. Qwen 先在全量向量中取近邻，再只按 tenant、状态和 ACL 后过滤；
5. 当前图顺序是 `recall → rerank → apply_constraints`，不符合“硬约束复核后重排”；
6. Deep Planner 是确定性查询宽泛化器，不是受控 LLM Grounded Planner；
7. CrossEncoder 只有 `off/challenger`，没有通过质量门禁的 Active 路径；
8. 当前 sqlite-vec 是精确 KNN，不是支持元数据过滤的 HNSW ANN；
9. LLM 调用缺少独立审计、错误分类、硬超时和预算传播；
10. 当前 13+13 查询只适合作为回归烟雾集，不足以决定默认模型链路。

## 3. 不可违反的架构不变量

### 3.1 三层模型职责

| 层级 | 当前/目标组件 | 唯一职责 | 明确禁止 |
|---|---|---|---|
| 高层理解 | DS LLM | 意图、标准类目候选、实体、硬约束、软/负偏好、多意图拆分、澄清、复杂计划、证据解释 | 扫描全量商品；直接裁决 ACL；跳过 Constraint Engine；生成目录外商品或事实；直接输出不可验证 ID |
| 专用轻模型 | Qwen Embedding、预训练 CrossEncoder 或在线轻量重排服务 | 向量召回、Top 20～50 候选相关性复核 | 维护 Session；决定权限；替代目录真实性校验；进行 LTR 训练 |
| 规模化执行 | BM25、过滤式 HNSW、行为召回、Catalog、过滤器、RRF | 全量检索、ANN、结构化过滤、ACL、去重、批量计算、确定性融合 | 解释自然语言歧义；编造条件；依赖自由文本作为安全边界 |

### 3.2 数据和安全边界

- tenant、user、ACL 必须来自可信 RequestContext，不得接受 LLM 或客户端工具参数覆盖；
- LLM 输出一律视为不可信建议，必须经过 Pydantic Schema、枚举、字段白名单、单位转换和 Catalog Taxonomy 校验；
- `product_type` 必须来自版本化受限枚举或 Taxonomy ID；自由 `domain` 只能作为解析中间值；
- 可索引硬约束在召回前执行，所有硬约束在候选进入重排前再次复核；
- 最终结果在输出前必须再次做 Catalog 存在性、状态、tenant、ACL 和动态事实复核；
- 外部模型和 Tool 失败不得改变安全边界，只能降级、澄清或拒答；
- 所有最终解释必须逐条绑定 Evidence，Evidence 不足时使用确定性模板或省略该断言。

### 3.3 模型和排序边界

- Qwen Embedding 负责召回，不负责最终事实判断；
- CrossEncoder 只处理过滤和 RRF 后的有限候选，不扫描全量 Catalog；
- 专用重排层直接使用开源预训练权重或在线轻量服务，不建设 LTR 训练、特征训练、模型发布链路；
- RRF 只融合来源排名，不直接比较 BM25、向量、行为的原始分数；
- ANN 的目的首先是规模和延迟；类目正确性必须由标准化和元数据过滤保证；
- 精确 KNN 保留为离线 Oracle、ANN 质量基准和本地故障回退，不作为最终 Qwen Active 主路径。

### 3.4 Agent 边界

- Fast Path 对简单、高置信、单意图请求执行固定有界链路；
- Deep Path 只在明确信号触发，不因“使用了 LLM”而默认进入复杂规划；
- Planner 只能输出受限 DAG Schema，不保存或暴露思维链；
- 最多一次 Replan；每个 Plan 有节点数、并发数、工具数、Token、时间预算；
- 简单查询不得因 Deep Path、解释生成或外部模型显著退化；
- Agent 的增益必须分别以检索质量和轨迹质量证明，不能只以语言流畅度证明。

## 4. 目标端到端链路

最终主链路固定为：

```text
用户查询
  ↓
DS 结构化理解
  - mode
  - product_type candidates
  - entities
  - hard constraints
  - soft preferences
  - negative preferences
  - ambiguities
  - confidence
  ↓
确定性 Schema 校验、单位归一和 Catalog Taxonomy 标准化
  ↓
Session Constraint 生命周期合并与冲突检测
  ↓
Fast / Deep 路由
  ├─ Fast：构造一个 RetrievalRequest
  └─ Deep：Probe → 受控 Plan → 最多一次 Replan → 一个或多个 RetrievalRequest
  ↓
可信过滤上下文
  - tenant
  - ACL
  - status
  - product_type
  - 可索引硬约束
  ↓
并行召回
  - BM25
  - Qwen filtered HNSW ANN
  - 已授权行为召回
  ↓
Catalog 回表、ACL/product_type/硬约束确定性复核
  ↓
按 canonical item_id 去重和 RRF 初排
  ↓
CrossEncoder Top 20～30（允许配置到 50）重排
  ↓
Catalog 真实性、动态字段、ACL 和硬约束最终复核
  ↓
Top 10 结果与 Evidence
  ↓
可选 DS 证据解释；失败时确定性解释降级
  ↓
SSE + Receipt + LLM/Tool/Rerank Audit
```

### 4.1 Qwen 向量路径的强制顺序

Qwen Active 路径必须是：

```text
解析并标准化 product_type 与硬约束
  ↓
构造可信 metadata filter：tenant + ACL + status + product_type + 可索引硬约束
  ↓
在过滤后的候选空间执行 HNSW ANN
  ↓
ANN 扩大召回，默认 Top 100，可按评测在 50～200 内调整
  ↓
Catalog 回表并再次复核全部硬约束
  ↓
进入多来源 RRF
  ↓
CrossEncoder 重排 RRF Top 20～30，最大 50
  ↓
最终 Top 10
```

以下实现不算完成：

- 全量 ANN Top K 后只做 product_type 后过滤；
- 使用不支持 metadata filter 的 HNSW，再依赖固定倍数 oversampling 猜测能补齐候选；
- 将 product_type 拼进查询文本但不做结构化过滤；
- 让 LLM 生成 SQL/过滤表达式后不校验直接执行；
- 用 ANN 替代类目标准化或 CrossEncoder；
- 只验证延迟、不验证 ANN 相对精确 KNN 的 Recall 保留率。

## 5. 目标核心契约

重构必须先冻结契约，再修改执行顺序。字段名可在实现评审中微调，但语义不得缺失。

### 5.1 `CanonicalProductType`

首版受限类目至少覆盖：

```text
phone
laptop
desktop
display
keyboard_mouse
audio
camera
tablet
storage
network
smart_device
projector
other_electronics
```

要求：

- 每个类型有稳定 ID、中文别名、允许属性集合和版本号；
- Alias 只用于标准化，不作为最终存储值；
- `unknown` 表示无法判断，不得当作全量硬过滤值；
- 多意图允许 1～3 个候选类型及置信度；
- LLM 输出的类型必须通过枚举映射，映射失败进入 ambiguity，不得自动创建新类型。

### 5.2 `ResolvedIntentV2`

```text
mode
product_type_candidates[]: {product_type, confidence, evidence_text}
entities[]: {type, normalized_value, source_span}
retrieval_query
hard_constraints[]
soft_preferences[]
negative_preferences[]
ambiguities[]
confidence
resolver_version
prompt_version
taxonomy_version
```

约束：

- `evidence_text` 只能是用户原文短片段，不允许模型思维链；
- 低置信或多类目冲突由路由器决定澄清/Deep，不得任意硬过滤；
- `retrieval_query` 保留型号、品牌、场景和软偏好，删除已结构化的纯数值约束；
- hard constraint 字段必须同时存在于全局字段字典和对应 product_type 属性矩阵。

### 5.3 `RetrievalRequest`

```text
query_text
tenant_id
user_id
allowed_permission_tags[]
product_types[]
hard_constraints[]
soft_preferences[]
negative_preferences[]
top_k
candidate_k_by_source
request_id
plan_step_id
taxonomy_version
```

要求：

- tenant、user、ACL 只能由 RequestContext 注入；
- RecallOrchestrator 和 Tool 统一接收该契约，不再只接收裸字符串；
- Tool Schema 不允许模型提交 tenant/ACL；
- BM25、Qwen、行为召回必须对相同 product_type 和安全过滤边界负责；
- Tool 回执记录实际应用的过滤字段和不可下推字段。

### 5.4 `FilterPlan`

```text
trusted_filters
  tenant_id
  allowed_permission_tags
  status
intent_filters
  product_types
  indexable_constraints
post_filter_constraints
filter_version
unsupported_fields[]
```

要求：

- 由确定性编译器从 RequestContext、Taxonomy 和 hard constraints 生成；
- LLM 不得直接生成后端 DSL；
- `unsupported_fields` 不得静默忽略：进入后过滤、澄清或明确错误；
- 同一个 FilterPlan 同时驱动 BM25、ANN 和最终 Constraint Engine，避免各路语义漂移。

### 5.5 `CandidateV2`

```text
item_id
source
source_rank
source_score
applied_filter_version
recall_reason_codes[]
rrf_score
rerank_score
constraint_pass
evidence[]
catalog_version
```

原始分数只用于来源内审计；跨来源排序以 source rank/RRF 为准。Rerank score 只在通过过滤的候选上产生。

### 5.6 `LLMCallAudit`

```text
node
provider
model
base_url_identifier
structured_output_method
prompt_version
schema_version
status: success | fallback | timeout | invalid_output | provider_error | cancelled
latency_ms
attempt_count
error_code
http_status
validation_passed
fallback_target
input_token_count
output_token_count
```

要求：

- 不保存 API Key、Authorization、完整用户隐私或模型思维链；
- 4xx 协议错误默认不可重试；429/5xx/网络错误按剩余预算决定是否最多重试一次；
- Receipt、SSE 诊断事件和离线评测共享同一错误码字典；
- 成功不能仅以 HTTP 200 判断，必须通过 Schema 和业务枚举校验。

### 5.7 `DeepPlanV2`

```text
reason_codes[]
steps[]
  step_id
  operation: retrieve | clarify
  query_text
  product_types[]
  constraint_patch[]
  depends_on[]
  required
max_parallelism
max_replans = 1
stop_conditions[]
revision
planner_model_version
prompt_version
```

Planner 只能引用已解析实体、约束、Probe 统计和允许的工具。不得引入用户未表达的品牌、预算、权限或商品事实。

### 5.8 `GroundedExplanation`

```text
item_id
summary
claims[]
  text
  evidence_ids[]
  claim_type: hard_constraint | soft_preference | tradeoff | catalog_fact
unsupported_claim_count
model_version
prompt_version
```

任一 claim 必须绑定 Evidence ID。解析失败、超时或存在不支持断言时，降级到确定性 EvidenceComposer。

## 6. 配置与 Feature Flag 规划

所有高风险能力使用 `off/challenger/active` 三态，默认值只在门禁通过后修改。

| 配置 | 首次引入默认值 | Active 前置门禁 |
|---|---|---|
| `SEEKORA_LLM_INTENT_MODE` | `challenger` | Provider 兼容、成功率、意图质量、延迟和降级通过 |
| `SEEKORA_LLM_PROVIDER` | `deepseek_compatible` | Provider Contract Test 通过 |
| `SEEKORA_LLM_STRUCTURED_OUTPUT_METHOD` | `json_mode` 候选，验证后冻结 | Intent/Patch/Plan 三类 Schema 均通过 |
| `SEEKORA_LLM_TIMEOUT_SECONDS` | 不超过 Agent 预算的分配值 | 超时与取消测试通过 |
| `SEEKORA_LLM_MAX_RETRIES` | `0`，仅明确瞬态错误允许 `1` | 预算测试通过 |
| `SEEKORA_VECTOR_BACKEND` | `sqlite_exact` | HNSW Shadow 门禁通过后改为目标后端 |
| `SEEKORA_ANN_MODE` | `challenger` | ANN 质量保留、过滤、安全和延迟通过 |
| `SEEKORA_EMBEDDING_MODE` | `challenger` | 类目感知 Qwen 质量不退化 |
| `SEEKORA_RERANK_MODE` | `challenger` | CrossEncoder 增益和延迟通过后允许 `active` |
| `SEEKORA_LLM_PLANNER_MODE` | `off` | Fast/Deep 复杂集门禁通过后先 `challenger` |
| `SEEKORA_LLM_EXPLANATION_MODE` | `off` | Evidence 事实门禁通过后先 `challenger` |

兼容要求：现有 `SEEKORA_INTENT_RESOLVER=rules|openai` 在迁移期保留为别名，但新代码内部必须区分“Provider”“能力节点”“运行模式”，不能继续用一个开关同时控制 Intent 和 Session Patch。

`/agent/config` 至少公开非敏感信息：

```text
configured_intent_mode
configured_provider
model_id
structured_output_method
intent_prompt_version
taxonomy_version
ann_backend
ann_mode
rerank_mode
planner_mode
explanation_mode
```

该接口只能说明配置，不代表本次请求实际成功。实际执行结果以 SSE 和 Receipt 的 `LLMCallAudit` 为准。

## 7. 分阶段执行规划

阶段编号严格按依赖推进。除测试基础设施外，不并行开发存在契约依赖的下游阶段。

### R0：冻结基线、建立防漂移护栏

**目标：** 在修改主链路前冻结当前数据、配置、指标、失败样例和执行路径。

**开发任务：**

1. 冻结 Catalog SHA-256、Qwen 模型/修订版/维度、SQLite 精确索引版本；
2. 冻结 M3 开发集和留出集，标记为 smoke/regression，不再用于 Prompt 或 HNSW 调参；
3. 保存 BM25+TF-IDF、BM25+Qwen、完整 Rules Agent 三组可复现报告；
4. 增加 DS Provider 失败样例：json_schema 400、Thinking tool_choice 400、json_mode Prompt 400；
5. 建立错误分类表和阶段状态文件；
6. 为每个后续阶段定义独立输出目录，报告必须包含代码版本、配置、数据、索引、模型和 Prompt 版本；
7. 将本文加入文档索引并标记为当前主控规划。

**测试：**

- Golden Set 唯一性和 Catalog 引用完整性；
- 当前基线复现误差为 0；
- 配置安全摘要不泄露 Key；
- 现有完整自动化测试全部通过。

**完成门禁：** 基线报告可以单命令复现，任何后续阶段均能与 R0 比较。

**禁止：** 本阶段不得切换默认模型、修改标注相关性或调 Qwen 权重。

### R1：LLM Provider 兼容、预算与审计重构

**目标：** 让 DS 在 Intent 和 Session Patch 节点真正成功，并使失败可观察、可取消、可预算。

**开发任务：**

1. 拆分 ChatModel 工厂、Provider Capability 和结构化输出策略；
2. 为 `json_schema/json_mode/function_calling` 建立明确配置，不依赖 LangChain 默认值；
3. 为 JSON Mode 修改 Prompt：明确只输出 JSON、嵌入/引用 Schema、禁止 Markdown 和额外文本；
4. 如采用 Function Calling，必须显式解决 Thinking 模式兼容，不能在代码中猜测；
5. 为 Intent 和 Session Patch 分配独立 Prompt Version、Schema Version；
6. 将通配 `except Exception` 替换为分类捕获、审计后降级；最外层仍保留安全兜底，但必须记录 `UNEXPECTED_LLM_ERROR`；
7. LLM 调用加入硬超时、取消传播和剩余预算检查；
8. 4xx 协议错误不重试，429/5xx/网络错误最多一次且必须有预算；
9. 新增 `LLMCallAudit`，写入 Receipt；SSE 仅暴露安全摘要；
10. `/agent/config` 返回已配置 Provider/模型/方法/Prompt 版本；
11. 增加启动后可选的非阻断 Provider Probe，Probe 结果不能替代请求级审计；
12. 保留规则回退，但回退必须可量化，不得伪装成 LLM 成功。

**主要预计修改文件：**

```text
config/settings.py
infrastructure/llm/*
infrastructure/intent/langchain_llm.py
infrastructure/session_context/langchain_llm.py
application/receipt.py
application/runtime.py
interfaces/http/api.py
```

**测试矩阵：**

- Provider Contract：三种结构化方法的支持/拒绝行为；
- Schema：合法、缺字段、额外字段、错误类型、未知类目、注入文本；
- 错误：400、401、429、500、连接失败、超时、取消、无效 JSON；
- 预算：LLM 超时不得超过分配预算，结束后不得继续后台请求；
- 安全：Receipt/SSE/日志不含 Key、Authorization 和完整敏感输入；
- 回退：每种失败对应稳定错误码并返回规则结果或安全终态。

**完成门禁：**

- 受控 DS 测试集中结构化调用成功率 100%，`resolver_version` 均为 DS；
- Provider 失败测试回退率 100%，错误码准确率 100%；
- 任一 LLM 调用不越过分配预算；
- 13 条 smoke 集不再出现“配置为 LLM 但 13/13 静默规则回退”；
- 不修改检索默认排序。

**回滚：** `SEEKORA_LLM_INTENT_MODE=off`，规则链路必须保持可用且质量等于 R0。

### R2：商品类型 Taxonomy、意图 V2 与召回契约

**目标：** 让 DS 的结构化理解变成可验证的 `product_type` 和过滤条件，并完整传给所有召回器。

**开发任务：**

1. 建立 `CanonicalProductType`、别名字典、属性适用矩阵和 taxonomy version；
2. 将现有 KuaiSearch `product_type` 映射到标准枚举，生成覆盖统计和 unknown 报告；
3. 引入 `ResolvedIntentV2`，将自由 `domain` 降级为兼容字段；
4. DS Prompt 只允许输出标准 Product Type ID 或候选列表；
5. 实现确定性 Intent Validator：枚举、单位、字段、operator、类目属性兼容；
6. 对高置信单类目直接选择；低置信、多类目或未知类目生成 ambiguity；
7. 扩展 `SearchQuery`/`RetrievalRequest`，传入 product_types、硬/软/负偏好；
8. 修改 RecallOrchestrator、Tool Schema、BM25、Vector、Behavior 适配器；
9. tenant/user/ACL 继续由 RequestContext 注入，禁止出现在 LLM Schema；
10. 实现 `FilterPlanCompiler`，区分可下推过滤和召回后过滤；
11. Receipt 记录原始 LLM 输出摘要、标准化结果、taxonomy version、拒绝字段和实际过滤计划；
12. 保留 V1 读取迁移器，避免已有 Session 无法恢复。

**测试矩阵：**

- 12+ 商品类型的中英文别名、型号和复合查询；
- 单类目、多类目、配件歧义、未知类目；
- 价格、内存、重量、续航、品牌等合法/非法字段；
- 类目切换时约束挂起、恢复和失效；
- LLM 输出不存在类型、越权字段、非法单位；
- Tool 接收到的 product_types/constraints 与 Intent 完全一致；
- 旧 Session V1 到 V2 的兼容恢复。

**完成门禁：**

- 人工复核类目集 Macro-F1 ≥ 0.90；
- 高置信类目标准化准确率达到预设门槛，未知类型不发生错误硬过滤；
- ACL/tenant 不能被 LLM 或 Tool 参数覆盖；
- Recall Contract Test 对 BM25/Qwen/Behavior 100% 通过；
- 尚不切换 Qwen Active。

**回滚：** Intent V2 可关闭，V1 Rules + 原检索契约仍可运行；V2 数据只增不破坏旧 Session。

### R3：过滤前置、双重复核与工作流顺序重构

**目标：** 修复 `recall → rerank → constraints` 顺序，建立所有召回源一致的过滤边界。

**目标图顺序：**

```text
resolve_intent
→ validate_and_normalize_intent
→ merge_session_context
→ compile_filter_plan
→ route
→ recall/probe/deep_recall
→ catalog_and_constraint_filter
→ rrf_merge
→ rerank
→ final_catalog_verify
→ assess_sufficiency
→ enrich/evidence
→ compose
```

**开发任务：**

1. 将 RecallOrchestrator 的“调用来源”和“RRF 融合”拆为可独立测试节点；
2. BM25 在搜索前应用 tenant、ACL、status、product_type 和可索引字段过滤；
3. 行为召回在聚合前应用相同 FilterPlan，未授权时不调用；
4. Qwen 在 R3 先使用“过滤后的精确 KNN”实现参考路径，验证过滤语义；
5. 召回后统一 Catalog 回表，执行 ACL/product_type/全部硬约束复核；
6. 只允许 `constraint_pass=True` 的候选进入 RRF 和 CrossEncoder；
7. 最终输出前再次复核 Catalog、动态字段和权限；
8. 过滤导致候选不足时进入 sufficiency/Deep，不允许自动删除硬约束；
9. 记录每层过滤前后数量、原因码、字段和耗时；
10. 更新 Fast/Deep 两条路径，确保共享同一 FilterPlan 和复核实现。

**为什么保留过滤后精确 KNN：** R3 需要先证明产品类型和硬约束逻辑正确。若同时引入 ANN，就无法区分质量变化来自过滤实现还是 ANN 近似误差。该精确路径是 R4 的质量 Oracle，不是最终 Active 路径。

**测试矩阵：**

- tenant/ACL/status/product_type 的单项和组合过滤；
- 价格、内存等前置过滤与最终复核一致性；
- 过滤后候选不足、零结果和不可满足约束；
- 错误类目不得进入 RRF/CrossEncoder；
- Fast/Deep 同请求产生相同安全边界；
- 行为召回不能重新引入被过滤商品；
- Property Test：任何最终 item 必须通过 FilterPlan 和最终 Catalog 校验。

**完成门禁：**

- tenant/ACL 安全约束满足率 100%；
- product_type 过滤违规率 0；
- 硬约束满足率 ≥ 99.5%，安全硬约束 100%；
- 13 条留出 smoke 中跨品类错误显著下降且 BM25+TF-IDF 不退化；
- CrossEncoder 仍保持 Challenger/off。

**回滚：** 旧工作流可通过图版本开关恢复；新 Receipt 必须记录 graph version。

### R4：支持元数据过滤的 HNSW ANN

**目标：** 将 Qwen 向量路径从精确 KNN 迁移为支持元数据过滤的 HNSW，同时保留精确 Oracle。

**技术决策约束：**

- 首选评估 Qdrant HNSW payload filter；也可评估满足同等契约的 OpenSearch/Milvus；
- 不能使用仅支持 HNSW、但无法在近邻搜索中可靠执行 tenant/ACL/product_type 过滤的库作为最终实现；
- 若更换首选后端，必须记录 ADR，禁止无记录替换；
- SQLite 精确索引继续用于离线 Oracle、回归和服务降级。

**开发任务：**

1. 扩展 `VectorIndex` 为 filtered search：vector、top_k、FilterPlan、search params；
2. 定义 HNSW 索引 Schema 和 Payload：tenant、permission tags、status、product_type、可索引数值字段；
3. 建立全量构建、幂等增量 upsert、删除和 Catalog snapshot 校验；
4. 记录模型、维度、distance、M、ef_construct、ef_search、payload schema 和数据哈希；
5. 默认 ANN candidate_k=100，在 50/100/150/200 上评估；
6. 调参 HNSW M、ef_construct、ef_search，但只在开发集选择；
7. 对每条查询同时运行精确过滤 KNN 和 filtered HNSW Shadow；
8. 计算 ANN Recall Retention、Top-K overlap、NDCG 差、过滤违规、延迟、吞吐、内存、构建时间和更新延迟；
9. ANN 无结果、超时或后端不可用时降级到过滤后精确 KNN，再降级到 BM25/TF-IDF；
10. Receipt 记录实际后端、参数、过滤器、降级原因和索引版本；
11. 增加启动 Readiness：索引版本、Catalog hash、维度或 Payload Schema 不一致时不得 Active；
12. HNSW 先 Challenger，门禁通过后才允许 Active。

**开发集调参顺序：**

1. 固定 FilterPlan 和 Qwen 模型；
2. 选择 candidate_k；
3. 选择 ef_search；
4. 必要时调整 M/ef_construct 并重建；
5. 冻结参数；
6. 只在独立留出集执行一次门禁，不用留出集继续调参。

**完成门禁：**

- ANN 相对过滤后精确 KNN 的 Recall@10 保留率 ≥ 99%；
- ANN 相对精确 Oracle 的 NDCG@10 降幅不超过 1%；
- tenant/ACL/product_type 过滤违规率 0；
- 向量查询 P95 相对当前精确路径有显著改善，目标至少降低 30%，否则保持 Challenger；
- 索引构建、增量更新、删除、版本不匹配和故障降级测试全部通过；
- Qwen 整体质量仍需通过 R8 门禁，ANN 门禁通过不等于 Qwen 可默认 Active。

**回滚：** `SEEKORA_VECTOR_BACKEND=sqlite_exact`；不得删除精确索引构建能力。

### R5：CrossEncoder 无训练重排层

**目标：** 使用开源预训练 CrossEncoder 或在线轻量服务，对过滤后 RRF Top 20～30 做正式相关性复核，不训练 LTR。

**开发任务：**

1. 冻结 `SemanticReranker` 契约：query、候选文档、分数、模型版本、超时；
2. 商品文档只使用可信 Catalog 字段，固定字段顺序和最大长度；
3. 默认重排 Top 30，评估 20/30/50；
4. CrossEncoder 输入只包含通过 ACL/product_type/硬约束的候选；
5. `challenger` 保存独立分数和拟议顺序，不改变用户结果；
6. 评估开源预训练模型与可选在线轻量服务的质量、P95、显存/内存和成本；
7. 模型失败、超时、分数数量错误或 NaN 时回退 RRF；
8. Active 模式只重排 Top N，其余候选保持稳定尾序；
9. 不引入点击标签训练、LTR 特征训练或模型发布流水线；
10. Receipt 记录模型、Top N、分数摘要、耗时、失败和回退。

**测试矩阵：**

- 细粒度属性、否定偏好、型号、配件混淆和跨类目候选；
- Top N 边界、稳定排序、同分处理、空候选；
- 模型缺失、CUDA OOM、超时、远程 429/5xx；
- Challenger 不改变 RRF；Active 只改变允许范围；
- 过滤失败商品永远不进入模型输入。

**完成门禁：**

- 独立留出集 NDCG@10、MRR@10 不低于过滤后 RRF，目标 NDCG 有显著增益；
- Recall@10 不因重排丢候选；
- 重排 P95 满足总请求预算，失败时 RRF 结果完整；
- Active 模式有独立 Kill Switch；
- 无任何 LTR 训练代码或产物进入主链路。

**回滚：** `SEEKORA_RERANK_MODE=off|challenger`，RRF 顺序作为确定性降级。

### R6：受控 LLM Grounded Planner

**目标：** 只在检索环境证明存在缺口时，让 DS 生成有限、可验证的查询计划。

**允许触发信号：**

```text
low_recall
category_drift
multi_intent
constraint_conflict
entity_ambiguity
```

**Probe 必须增加：**

- 总候选数和每来源候选数；
- 目标 product_type 占比和 Top 类目分布；
- 硬约束命中率；
- BM25/Qwen/Behavior 重叠；
- 品牌/价格区间分布；
- 字段缺失率；
- 来源失败、降级和延迟；
- 是否存在足够证据完成当前任务。

**开发任务：**

1. 定义 `ProbeSummaryV2` 和 `DeepPlanV2`；
2. 由确定性 Router 根据允许信号决定是否调用 Planner；
3. DS Planner 只读取 Intent、FilterPlan、Probe 统计和工具能力声明；
4. Planner 输出受限 JSON，不输出自由 ReAct 文本；
5. Validator 校验工具白名单、依赖无环、节点数、过滤条件、预算和事实来源；
6. 默认最大 2 个检索步骤、最大并发 2、最大 Replan 1；
7. Replan 只能根据第一次执行摘要修订，不得无限反思；
8. Plan 不得放松用户硬约束；需要放松时只能生成澄清/建议，由用户确认；
9. Planner 失败回退当前确定性 Planner；
10. 记录触发信号、Plan、Validator 结果、执行状态、停止原因和模型审计；
11. 简单查询不得调用 Planner；
12. 初期 Challenger 只比较拟议 Plan，不增加真实工具调用，验证后再 Active。

**测试矩阵：**

- 五类触发信号和不触发反例；
- 非法工具、循环依赖、过多步骤、越权参数、虚构约束；
- 一次 Replan 上限和预算耗尽；
- Planner/Tool 部分失败；
- 简单查询延迟不退化；
- Deep 相对 Fast 的复杂集增益。

**完成门禁：**

- 简单集 Planner 调用率接近 0，误触发率达到预设门槛；
- 复杂集任务成功率/NDCG 显著优于 Fast-only；
- 无效工具和参数率为 0；
- Replan 次数永远 ≤1；
- 约束放松未经用户确认的发生率为 0；
- 总请求 P95 和 Token 成本满足预算。

**回滚：** `SEEKORA_LLM_PLANNER_MODE=off`，使用确定性 Planner/Fast Path。

### R7：基于证据的 DS 解释

**目标：** 让 DS 只基于最终 Top K 的可信 Catalog 和 Evidence 生成简短、有引用的解释。

**开发任务：**

1. 在最终 Catalog 复核后构造最小 Evidence Bundle；
2. Bundle 只包含允许展示的标题、品牌、类目、价格、结构化属性、召回原因和约束命中；
3. 每条 Evidence 分配稳定 ID、来源、观察时间和信任等级；
4. DS 输出 `GroundedExplanation` Schema；
5. Claim Validator 检查每条断言绑定 Evidence，数值和枚举可回查；
6. 区分硬约束命中、软偏好匹配和妥协，不把软偏好描述为保证；
7. 负偏好只能描述为“未发现/尽量避免”，除非 Evidence 可证明；
8. DS 超时、无效输出、无 Evidence 或存在不支持 Claim 时回退确定性解释；
9. 解释生成不阻塞已验证商品结果：可作为后续 SSE 事件或受限尾阶段；
10. Receipt 保存 Prompt/模型/Claim-Evidence 映射和审计，不保存思维链。

**测试矩阵：**

- 完整证据、部分证据、冲突证据、无证据；
- 数值、比较级、绝对化用语和目录外事实；
- Prompt Injection 商品文本；
- 超时、无效 JSON、模型不可用；
- 确定性解释降级一致性。

**完成门禁：**

- Item 目录存在率 100%；
- 支持事实准确率 ≥98%，安全/权限事实 100%；
- unsupported claim 进入用户结果的比例为 0；
- Explanation 失败不影响 Top K 结果返回；
- 不增加未经证据支持的商品属性。

**回滚：** `SEEKORA_LLM_EXPLANATION_MODE=off`，使用当前 EvidenceComposer。

### R8：综合评测、默认切换与文档收口

**目标：** 用充分的人工数据证明三层链路的质量、延迟、可靠性和 Agent 增益，再切换默认值。

**评测集建设：**

1. 13+13 集继续作为快速回归，不作为最终门禁；
2. 建立至少 300～500 条人工复核 Golden Query；
3. 覆盖 Head/Torso/Tail、12+ 类目、简单/复杂/歧义/冲突、多意图、否定偏好和不可满足；
4. 每条标注意图、product_type、硬约束、相关商品等级、禁止商品、是否澄清和期望证据；
5. 开发集、验证集、留出集隔离；留出集不参与 Prompt、ANN 或 CrossEncoder 调参；
6. 两人独立标注并仲裁，记录一致性。

**必须执行的四组主对照：**

| 组 | Intent | 召回 | 重排 | 目的 |
|---|---|---|---|---|
| A | Rules | BM25+TF-IDF | RRF | 冻结基线 |
| B | DS | BM25+TF-IDF | RRF | 测 DS 理解增益 |
| C | Rules | BM25+filtered Qwen HNSW | RRF/CrossEncoder | 测检索与重排增益 |
| D | DS | BM25+filtered Qwen HNSW+Behavior | RRF+CrossEncoder | 验收完整三层链路 |

**离线指标：**

- Intent：Macro-F1、Product Type Accuracy、Constraint F1、ECE；
- Recall：Recall@10/50/100、零结果率、跨类目误召回率、来源覆盖；
- Ranking：MRR@10、NDCG@10、HitRate；
- ANN：Exact Recall Retention、Top-K overlap、过滤违规；
- Constraint：硬约束满足率、跨轮保持率、冲突发现率；
- Agent：路由准确率、工具/参数准确率、Planner 调用率、无效调用率、Replan、停止正确率；
- Evidence：事实准确率、Evidence Coverage、unsupported claim；
- System：P50/P95/P99、首事件、错误率、降级率、Token、GPU/CPU/内存和成本。

**质量门禁分两级：**

候选启用门禁：

- D 的 Recall@10、MRR@10、NDCG@10 均不得低于 A；
- 零结果率不得高于 A；
- product_type/ACL/硬约束违规满足前述安全门禁；
- DS 结构化成功率 ≥99%，失败全部可审计和降级；
- ANN 相对精确 Oracle 达到 R4 门禁。

最终目标门禁：

- D 在主要目标 NDCG@10 或任务成功率上相对 A 提升 ≥5%；
- 复杂集 Deep Path 显著优于 Fast-only；
- 简单查询延迟和成本不发生不可接受退化；
- 普通模式 TP95 ≤8 秒，首个流式事件 TP95 ≤1.5 秒；
- Receipt 完整率 100%。

**默认切换顺序：**

```text
离线通过
→ Challenger/Shadow
→ 本地白名单 Active
→ 完整回归和故障注入
→ 修改本地默认值
```

不得一次性同时切换 LLM Intent、ANN、CrossEncoder、Planner 和 Explanation。每次只切一个默认开关，并保存前后报告。

## 8. 测试分层和最低覆盖要求

### 8.1 单元测试

- Taxonomy 映射、单位归一、FilterPlan 编译；
- Schema 验证和 Provider 错误分类；
- RRF、约束复核、Candidate 状态；
- HNSW 参数和元数据 Filter 序列化；
- Planner DAG 校验、预算和停止；
- Claim-Evidence 校验。

### 8.2 Contract Test

- LLM Provider Contract；
- Intent Resolver Contract；
- Recall Tool Contract；
- Filtered VectorIndex Contract，精确/HNSW 使用同一套测试；
- Reranker Contract；
- Planner Contract；
- Receipt/Store Contract。

### 8.3 集成测试

- Rules/DS × Exact/HNSW × Rerank off/challenger/active；
- Fast/Deep 两路径；
- 匿名/授权行为召回；
- Session 跨轮约束；
- Index/Catalog/Taxonomy 版本不匹配；
- SSE、Receipt、回放和取消。

### 8.4 质量回归

- 13 条开发集和 13 条留出 smoke 每次提交可快速运行；
- 300～500 条主 Golden 在阶段门禁运行；
- 所有指标按类目、复杂度、Fast/Deep、Head/Torso/Tail 分组；
- 任何总指标提升不得掩盖安全类目或关键分组退化。

### 8.5 性能与可靠性

- 冷启动、暖态、1/5/10/25 并发；
- LLM、HNSW、CrossEncoder 单节点和端到端 Span；
- 429、5xx、超时、网络断开、CUDA OOM、索引不可用；
- 取消后无遗留模型/网络任务；
- 一小时稳定性和资源泄漏；
- 降级链路返回结果的质量和 Receipt 完整性。

### 8.6 安全测试

- 跨租户和 ACL 越权；
- LLM 尝试输出 tenant/ACL/未知过滤字段；
- 商品标题、描述和 Tool 输出中的 Prompt Injection；
- Planner 越权工具、无限循环和硬约束放松；
- Explanation 目录外事实和无 Evidence 断言；
- 日志、SSE、Receipt 的 Secret/PII 泄漏。

## 9. 代码结构目标与依赖方向

建议新增/调整模块边界：

```text
domain/
  intent_v2.py              # ResolvedIntentV2、ProductTypeCandidate
  taxonomy.py               # 领域契约，不包含具体数据加载
  retrieval.py              # RetrievalRequest、FilterPlan、CandidateV2
  llm_audit.py              # LLMCallAudit、错误码
  explanation.py            # GroundedExplanation Schema

application/
  intent_validation.py      # 确定性标准化
  filter_planning.py        # FilterPlanCompiler
  recall.py                 # 来源执行，不直接承担所有过滤和融合
  fusion.py                 # RRF
  reranking.py              # CrossEncoder 编排
  deep_path.py              # Router/Probe/Planner 接口
  explanation.py            # Evidence Bundle 与 Claim 校验

infrastructure/
  llm/                      # DS/OpenAI-compatible Provider 与结构化策略
  taxonomy/                 # 版本化电子类目数据
  search/                   # BM25、SQLite Exact、HNSW Adapter
  rerankers/                # 预训练 CrossEncoder/在线轻量服务
  tools/                    # 将应用契约包装为 LangChain Tool
```

依赖方向固定为：

```text
interfaces → application → domain
infrastructure → application/domain contracts
bootstrap → all concrete adapters
domain 不依赖 LangChain、FastAPI、Qdrant、SQLite 或模型 SDK
```

LangChain/LangGraph 只负责编排和 Tool 适配，不承载业务安全规则。更换 LangChain 版本不得改变 domain/application 契约。

## 10. 数据与索引迁移规划

1. 为现有 Catalog 增加 taxonomy version，不修改 canonical item_id；
2. 输出 product_type 映射覆盖率、unknown 和冲突报告；
3. 先构建带 Payload 的新 HNSW 索引，不覆盖 SQLite 精确索引；
4. 两套索引使用相同 Qwen 模型、维度、文档内容哈希和 Catalog snapshot；
5. 新索引完成 Shadow 对照后才允许 Active；
6. Session 的 Intent V1/V2 使用显式 schema version 和迁移器；
7. Receipt 读取器兼容旧字段，新字段只追加；
8. 所有迁移命令幂等，失败不删除旧索引；
9. 删除商品必须传播到 BM25、HNSW、精确 Oracle、行为候选和缓存；
10. 回滚时只改配置指针，不需要重新构建旧索引。

## 11. 预算分配原则

默认 8 秒请求预算必须拆分，不允许每个组件各自拥有 30 秒超时。初始建议仅用于 R1 压测校准：

| 阶段 | 建议上限 | 说明 |
|---|---:|---|
| Intent LLM | 2.0～2.5 秒 | 失败立即规则降级；协议 4xx 不重试 |
| Session Patch LLM | 1.5～2.0 秒 | 仅多轮需要；可与部分本地准备并行评估 |
| BM25/Qwen/Behavior Recall | 1.5～2.0 秒 | 并行关键路径，以最慢必需来源为准 |
| CrossEncoder | 0.5～1.0 秒 | Top 20～30；超时回退 RRF |
| Deep Planner | 1.5～2.0 秒 | 只在 Deep；必须预留执行预算 |
| Explanation | 0.8～1.5 秒 | 可选、可后置；失败不影响结果 |
| Catalog/过滤/序列化余量 | ≥0.5 秒 | 确定性复核和 SSE |

实际分配必须由 Span 数据校准。进入某个可选节点前，必须检查剩余预算是否足够完成该节点及后续必需步骤。

## 12. 每个工作包的标准模板

后续分步骤执行时，每个任务必须在开始前声明：

```text
工作包编号：R阶段-序号
目标：只描述一个可验收增量
上游依赖：已通过的工作包和门禁
允许修改：具体模块/文件
禁止修改：默认开关、Golden 留出集、无关模块
契约变化：Schema、版本和兼容策略
实现步骤：按依赖排序
测试：单元、契约、集成、质量、故障
指标：基线、目标、允许误差
输出物：代码、测试、报告、文档、Receipt 示例
回滚：配置或代码路径
完成证据：命令、通过数、指标和文件链接
遗留问题：不得隐藏在“后续优化”一句话中
```

工作包完成后只允许三种状态：

- `完成`：代码、测试、指标、文档和回滚均齐全；
- `部分完成`：不得启动依赖它的下游工作包；
- `阻塞`：记录阻塞条件和已验证证据。

## 13. 防止逐步跑偏的检查清单

每次代码评审必须逐项回答：

1. 这次修改属于三层模型中的哪一层，是否越权承担其他层职责？
2. LLM 输出是否经过受限 Schema、Taxonomy 和确定性校验？
3. tenant/ACL 是否完全来自可信上下文？
4. product_type 和可索引硬约束是否在 ANN 前执行？
5. 全部硬约束是否在 CrossEncoder 前再次复核？
6. CrossEncoder 是否只处理有限候选，且没有引入 LTR 训练？
7. 最终商品是否经过 Catalog 真实性复核？
8. 解释中的每个事实是否绑定 Evidence？
9. 新模型调用是否受硬超时、取消和总预算约束？
10. 失败是否有稳定错误码、Receipt 和明确降级？
11. 简单查询是否被无必要地送入 Deep Planner？
12. 是否修改了默认开关？若修改，阶段门禁证据在哪里？
13. 是否使用留出集调参或修改标注？若是，必须回退并重新划分数据；
14. 是否同时切换了多个高风险变量？若是，应拆分；
15. 文档、运行图、配置示例和测试报告是否同步更新？

## 14. 推荐执行顺序、依赖与预估工作量

| 阶段 | 依赖 | 预估开发日 | 是否允许与下一阶段并行 |
|---|---|---:|---|
| R0 基线护栏 | 无 | 1～2 | 仅可并行准备测试数据 |
| R1 LLM 兼容/审计/预算 | R0 | 2～4 | 不与 R2 核心契约并行 |
| R2 Taxonomy/IntentV2/Recall Contract | R1 | 4～6 | 可并行人工类目标注 |
| R3 过滤与工作流重排 | R2 | 4～6 | 不与 R4 主实现并行 |
| R4 Filtered HNSW ANN | R3 | 5～8 | 可并行后端环境准备和基准脚本 |
| R5 CrossEncoder Active 候选 | R3；建议 R4 稳定 | 3～5 | 可与 R4 后半段评测并行，但不得共同切默认 |
| R6 Grounded Planner | R1～R5 | 4～6 | 可并行建设复杂集标注 |
| R7 Evidence Explanation | R3；建议 R6 契约稳定 | 3～4 | 可在 Explanation Challenger 独立开发 |
| R8 综合评测与默认切换 | R1～R7 | 5～10，另含标注时间 | 不与重大架构修改并行 |

总开发量约 31～51 个开发日，不包含 300～500 条人工标注、模型下载、HNSW 后端部署和长时间压测等待。任何压缩排期都不得删除 R1～R4 的门禁。

## 15. 里程碑状态表

| 阶段 | 当前状态 | 进入条件 | 退出证据 |
|---|---|---|---|
| R0 | 待执行 | 本文批准 | 冻结基线与可复现报告 |
| R1 | 待执行 | R0 完成 | DS 成功、审计和预算报告 |
| R2 | 待执行 | R1 完成 | Taxonomy/Intent/Recall Contract 报告 |
| R3 | 待执行 | R2 完成 | 过滤安全和工作流质量报告 |
| R4 | 待执行 | R3 完成 | HNSW 对精确 Oracle 报告 |
| R5 | 待执行 | R3 完成 | CrossEncoder Challenger/Active 门禁报告 |
| R6 | 待执行 | R1～R5 核心门禁 | Deep vs Fast 复杂集报告 |
| R7 | 待执行 | Evidence 契约稳定 | Claim-Evidence 事实报告 |
| R8 | 待执行 | R1～R7 完成 | 综合验收和默认切换记录 |

## 16. 当前立即执行的下一个工作包

下一步固定为 `R0-01：冻结重构基线与失败样例`，不得直接开始 HNSW、CrossEncoder Active 或 LLM Planner。

`R0-01` 输出物：

1. 当前代码/数据/索引/配置的非敏感版本清单；
2. M3 三组基线的机器可读报告；
3. DS 三种结构化协议失败的脱敏 Fixture 和错误码；
4. 13+13 smoke 集用途声明；
5. 重构阶段状态文件；
6. 全量自动化测试结果；
7. `R1-01` 的 Provider Contract 设计输入。

完成 R0-01 后，下一任务只能是 `R1-01：结构化输出策略与 Provider Capability 契约`。

## 17. 决策记录

### ADR-REF-001：三层模型职责固定

DS 只负责理解、受控规划和证据解释；Qwen/CrossEncoder 负责语义召回和有限候选复核；传统组件负责规模化、安全和确定性执行。

### ADR-REF-002：不进行 LTR 训练

重排直接使用开源预训练 CrossEncoder 或在线轻量服务。现有曝光/行为数据只用于评测、分析和未来独立研究，不进入本轮 LTR 训练链路。

### ADR-REF-003：最终 Qwen Active 必须使用过滤式 HNSW

sqlite-vec 精确 KNN 保留为 Oracle 和回退；最终 Active 路径必须支持 tenant、ACL、status、product_type 和可索引约束的元数据过滤。仅 HNSW 后过滤不满足本决策。

### ADR-REF-004：先验证过滤语义，再评估 ANN 误差

R3 使用过滤后的精确 KNN 证明业务过滤正确，R4 再以它为 Oracle 验证 HNSW。该顺序不得被解释为放弃 HNSW。

### ADR-REF-005：高风险能力逐项切换

LLM Intent、HNSW、CrossEncoder、Planner、Explanation 分别使用独立开关和门禁；禁止一次提交同时把多个能力改为默认 Active。

### ADR-REF-006：LLM 流畅度不是质量门禁

LLM 节点必须以结构化成功率、意图/约束准确率、轨迹正确率、证据事实率、延迟和降级率验收，不以主观语言效果替代。
