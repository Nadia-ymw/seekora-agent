# 实施任务规划

> 依据：仓库根目录《搜索推荐Agent技术路线.md》《搜索推荐Agent系统设计文档.md》《搜索推荐Agent需求分析文档.md》  
> 计划周期：10～12 周（已有目录、搜索和模型基础设施时）  
> 当前状态：阶段 0～3 已实现；Grounded Deep Path 已具备有界 DAG、并发限制、节点停止、一次 Replan、澄清/拒答和故障降级

## 1. 目标和交付边界

建设单主 Agent、Fast/Deep 双路径系统。LLM 负责理解、计划和解释，目录、召回、约束和排序服务负责确定性执行。MVP 默认只读，不执行购买、出价或修改预算等操作。

上线门禁：意图 Macro-F1 ≥ 0.90；硬约束满足率 ≥ 99.5%，安全约束 100%；Item 目录存在率 100%；受支持事实准确率 ≥ 98%；NDCG@10 或任务成功率相对基线提升 ≥ 5%；普通模式 TP95 ≤ 8 秒；首个流式事件 TP95 ≤ 1.5 秒；Receipt 完整率 100%。

## 2. 阶段与任务分解

| 阶段 | 周期 | 关键任务 | 主要交付物 | 完成门禁 |
|---|---:|---|---|---|
| 0. 业务、数据与基线 | 1～2 周 | 冻结业务域和 Item Schema；接目录快照；建立日志协议、数据质量规则、Golden Set 和关键词基线 | 数据字典、版本化快照、300～500 条 Golden Query、基线报告 | 基线可复现，数据质量合格 |
| 1. Agent 基础平台 | 1～2 周 | FastAPI/SSE、Session、Context、Tool Registry、预算、取消、Trace、Receipt | `/agent/query`、工具契约、回放骨架 | 端到端链路和 Contract Test 通过 |
| 2. Fast Path MVP | 2～3 周 | 意图与约束结构化；关键词/向量并行召回；RRF；约束引擎；Ranker；Evidence-only 解释 | Fast Path、结果卡片、降级策略 | MVP 离线质量与 TP95 门禁通过 |
| 3. Grounded Deep Path | 2～3 周 | Retrieval Probe、复杂度路由、结构化 Planner、DAG 执行、一次 Replan、澄清与拒答 | Deep Path、预算与停止条件 | 复杂集显著优于 Fast Path，简单流量不退化 |
| 4. Profile 与反馈闭环 | 2～3 周 | Session/Profile 分离；Consent；曝光反馈；行为召回；LTR；Teacher/Judge | Profile API、反馈管道、训练集与模型 | 用户可控制，分群无明显伤害 |
| 5. 上线准备 | 1～2 周 | 安全、故障注入、负载、Shadow、Canary、A/B、Runbook、Kill Switch | 测试报告、Dashboard、回滚方案 | 发布门禁全部通过 |

## 3. 数据获取与治理

### 3.1 Item 目录

从 Catalog/PIM/CMS/知识库真相源获取全量快照和 CDC 增量。最少字段为 `item_id`、租户、标题、描述、类目、结构化属性、状态、权限标签和更新时间；商品域增加价格、库存、地域和有效期。

数据管道：真相源 → 原始快照 → 清洗标准化 → 版本化目录 → 关键词索引 → Embedding → 向量索引。每次构建记录数据版本、Schema 版本、内容哈希和索引版本。价格、库存等动态字段在展示前从权威接口复核。

### 3.2 行为与反馈

埋点必须同时记录曝光和行为，字段至少包含 `user_id`（伪匿名）、`session_id`、`request_id`、`exposure_id`、`item_id`、位置、召回源、模型版本、Action、时间和 Consent。管道需要事件去重、迟到处理、机器人过滤、归因和删除传播。

### 3.3 Golden Set

从脱敏真实查询分层抽样，并补充复杂、歧义、冲突、不可满足、越权和 Prompt Injection 场景。每条查询标注意图、硬约束、软偏好、分级相关 Item、禁止出现 Item、是否澄清和期望证据。两人独立标注、分歧仲裁，测试集固定且不参与 Prompt 调优。

首批 300～500 条；稳定后扩展到 2,000 条高价值样本。LLM 弱标签仅用于扩充训练数据，不能直接替代人工 Golden Set。

### 3.4 外部证据

仅访问允许的数据源，保存来源 URI、抓取时间、内容哈希、权限和信任等级。网页和 Item 文本均视为不可信输入。MVP 实现工具接口但默认关闭外部 Web，完成 SSRF、版权和 Prompt Injection 评审后按 Feature Flag 开放。

## 4. 测试计划

### 4.1 质量测试

- 意图：Macro-F1、Top-N Recall、ECE；
- 召回：Recall@K、零结果率、有效候选数、来源覆盖率；
- 排序：NDCG@10、MRR、HitRate；
- 推荐：Coverage、Diversity、Novelty、校准度；
- 约束：满足率、跨轮保持准确率、冲突发现率；
- 证据：Citation Precision、Evidence Coverage、事实错误率；
- Agent：工具/参数准确率、无效调用率、Replan 率；
- 成本：Token、模型/搜索费用、工具调用数。

所有指标按 Head/Torso/Tail、查询模式、用户类型、类目和 Fast/Deep Path 分组报告。

### 4.2 工程性能测试

以 1,000 万 Item、100 QPS 为设计基线：先做 10→25→50→100 QPS 阶梯负载，再做 100 QPS 一小时目标负载、2 倍流量峰值、8～24 小时稳定性、空缓存冷启动和 SSE 断连/取消测试。采集端到端及各 Span 的 TP50/TP95/TP99、首事件时间、错误率、降级率、资源饱和度和单请求成本。

### 4.3 可靠性与安全

分别注入 LLM、关键词召回、向量召回、Ranker、Profile、Catalog、Redis 和事件服务故障；验证单源失败可降级。安全测试覆盖跨租户越权、Prompt Injection、SSRF、恶意工具输出、敏感信息泄漏和删除数据残留。

### 4.4 在线验证

依次执行固定回放、Shadow、内部白名单、1% Canary、5%～20% A/B 和逐级扩量。除主业务指标外，护栏包括延迟、零结果、放弃、错误事实、约束违规、投诉、退订和成本。

## 5. 当前已实现的第一步

- 创建标准 Python 项目结构；
- 定义核心 Item、Constraint、SearchQuery、GoldenQuery Schema；
- 提供 JSONL 目录和 Golden Query 读取器；
- 实现中文 unigram/bigram + 英文 token 的 BM25 基线；
- 实现确定性 `eq/in/lte/gte` 过滤；
- 实现目录质量检查及 Recall@K、MRR、NDCG@K；
- 提供样例目录、样例 Golden Set 和自动化测试。

## 6. 进入下一阶段前的待办

1. 业务负责人确定首个业务域和主指标；
2. 数据负责人提供脱敏目录快照及字段字典；
3. 明确目标 QPS、外部检索策略、模型供应商和成本上限；
4. 将样例 Golden Set 替换为至少 300 条人标查询；
5. 冻结阶段 0 基线报告并由产品、算法和工程共同签字。

## 7. 阶段 1：Agent 基础平台实现说明

阶段 1 建立一个可以通过 HTTP/SSE 调用的单 Agent Fast Path 骨架。当前调用链为：

```text
POST /agent/query
→ api.py 校验请求并建立 SSE
→ runtime.py 创建请求、预算、Session 和 Receipt
→ workflow.py 执行 LangGraph Fast Path
→ intent resolver 生成结构化意图
→ recall.py 通过 ToolNode 并行调用 catalog_search、vector_search 与可选 behavior_recall
→ Constraint Engine 与 Catalog 复核
→ runtime.py 流式发送进度与结果
→ receipt.py 保存本次执行凭据
```

### 7.1 `application/contracts.py`

定义框架无关的运行时协议：

- `AgentQuery`：已经通过 API 校验的内部请求，包含查询、租户、会话、用户、权限和 Top-K；
- `AgentEvent`：统一流式事件，所有事件携带 `request_id`；
- `ExecutionBudget`：维护 8 秒默认截止时间和最多 6 次工具调用；
- `BudgetExceeded`、`RequestCancelled`：将预算和取消变成显式状态，而不是普通未知异常。

该文件不依赖 FastAPI，因此 CLI、后台 Worker 或测试都可以复用相同协议。

### 7.2 `application/session.py` 与 `infrastructure/stores/memory.py`

定义会话消息、会话状态、开发态 Session Store 和取消注册表：

- `SessionMessage` 保存角色、内容、请求 ID 和时间；
- `SessionState` 保存租户隔离的消息列表和版本；
- `InMemorySessionStore` 用异步锁保护并发读写；
- `CancellationRegistry` 接收取消请求，运行时在工具调用前后检查状态。

当前实现只用于单进程开发和测试，服务重启后数据会消失。生产环境需要替换为 Redis/数据库实现，并加入 TTL、并发版本检查和历史压缩。

### 7.3 `application/recall.py` 与 `infrastructure/tools/*`

使用 LangChain 建立统一工具边界：

- Pydantic Schema 校验查询、Top-K、租户和权限参数；
- LangChain `@tool` 提供名称、描述、参数 Schema 和结构化 Artifact；
- `LangChainToolRegistry` 把工具列表注册到 `ToolNode`，并注入可信 `ToolRuntime`；
- `RecallOrchestrator` 只提交查询与 Top-K，并行执行和融合工具结果；
- Tool 结构化返回状态、错误码、是否可重试、数据源版本和候选；
- BM25、语义索引和授权行为分别包装为只读 `catalog_search`、`vector_search`、`behavior_recall`。

后续增加 `item_detail`、`behavior_recall` 时不需要修改 Runtime，只需实现相同 Tool 接口并在召回编排器中装配。

### 7.4 `application/receipt.py`

保存可审计、可回放的执行凭据：

- `ToolCallReceipt` 记录工具名、参数、状态、耗时、错误码和数据版本；
- `RecommendationReceipt` 记录请求、路由、候选 ID、配置版本和最终状态；
- `InMemoryReceiptStore` 提供开发态写入和查询。

当前 Receipt 仍在内存中。生产实现需要不可变持久化、脱敏/加密、保留策略以及数据/索引/模型/Prompt 完整版本。

### 7.5 `application/runtime.py`

实现单主 Agent 的 Fast Path 状态机，是阶段 1 的核心编排层。主要流程：

1. 生成服务端 `request_id`；
2. 创建执行预算和 Receipt；
3. 获取 Session 并记录用户消息；
4. 发送 `request.accepted` 并解析结构化意图；
5. 消耗工具预算，并行调用关键词、语义以及登录用户可用的行为召回；
6. 使用 RRF 融合并检查取消状态；
7. 执行硬约束和 Catalog 最终校验；
8. 发送 `intent.resolved`、召回、过滤和 `result` 事件；
9. 记录助手消息、候选 ID 和过滤原因；
10. 对成功、取消、预算耗尽或异常分别收口，持久化 Receipt 并发送 `done`。

运行时只依赖 LangGraph Workflow 和 Store 端口，不依赖 HTTP，因此可以独立做确定性单元测试。

### 7.6 `interfaces/http/api.py`

提供 FastAPI 薄适配层：

- `GET /health`：存活检查；
- `POST /agent/query`：校验请求并返回 `text/event-stream`；
- `POST /agent/requests/{request_id}/cancel`：登记取消请求；
- `GET /agent/receipts/{request_id}`：读取开发态 Receipt；
- `encode_sse()`：将内部 `AgentEvent` 转为 SSE 协议。

公开演示接口固定只授予 `public` 权限，不接受客户端自报 ACL。生产环境的 `tenant_id`、`user_id` 和权限必须由认证 Gateway 注入，不能相信请求正文。

### 7.7 `bootstrap.py`

负责组装默认应用：

1. 从 `SEEKORA_CATALOG_PATH` 或样例路径加载目录；
2. 建立 BM25 基线；
3. 使用 `@tool` 创建 `catalog_search`、`vector_search` 与 `behavior_recall`，注册到 `ToolNode`；
4. 创建 RecallOrchestrator 并装配三个召回工具，匿名请求跳过行为工具；
5. 装配意图解析、RRF、约束引擎和 Catalog Repository；
6. 创建 Agent Runtime 并导出 Uvicorn 可发现的全局 `app`。

它是依赖装配文件，不放业务编排逻辑。将来切换 OpenSearch、Redis 或持久化 Receipt 时主要修改这里的装配，而不是修改 Runtime。

### 7.8 新增测试

- `tests/test_runtime.py`：验证预算上限、工具重名、事件顺序、Session 写入、Receipt 和取消；
- `tests/test_api.py`：验证健康检查、SSE 查询、Receipt 查询和非法请求 422。

当前共 70 个自动化测试，统一在 `seekora-agent` Conda 环境执行。其中 LLM 边界测试使用假的 LangChain Runnable，不访问外部 API。

### 7.9 可选 LLM 意图解析增量

- `.env.example` 提供不含真实密钥的配置模板；
- `config/settings.py` 校验解析器、模型、超时和重试配置，并提供不泄漏 Key 的摘要；
- `infrastructure/llm/openai.py` 集中创建 LangChain `ChatOpenAI`；
- `infrastructure/intent/langchain_llm.py` 用结构化输出解析意图并标准化硬约束；
- `bootstrap.py` 根据环境变量选择规则或 OpenAI 实现；
- OpenAI 调用或输出校验失败时回退规则解析器，最终结果仍经过 Constraint Engine 和 Catalog 复核。

详细配置和每个文件的职责见 [LLM 配置与新增文件说明](../02-development/llm-configuration.md)。

### 7.10 运行和接口示例

```powershell
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m uvicorn seekora_agent.bootstrap:app --host 127.0.0.1 --port 8000
```

事件顺序示例：

```text
request.accepted
intent.resolved
routing.completed
recall.started
recall.completed
constraints.applied
result
done
```

Deep Path 在 `routing.completed` 后额外发送 `probe.completed` 和 `plan.created`。

异常时会出现 `error → done`，取消时出现 `cancelled → done`。

### 7.11 阶段 2 边界

- 目前已有 `catalog_search`、`vector_search` 和授权行为召回，尚未加入 Item Detail 工具；
- LLM 意图解析已提供可选 OpenAI 实现，但尚未用真实 Golden Set 完成模型质量和成本评估；
- Session、取消和 Receipt 是单进程内存实现；
- `client_request_id` 已进入协议但尚未实现幂等缓存；
- Trace 目前只有请求事件、工具耗时和 Receipt，尚未接 OpenTelemetry；
- 取消是协作式检查，长耗时工具还需要支持超时和主动中止；
- 没有认证 Gateway，当前 API 仅适合本地开发；
- 阶段 2 已完成意图结构化、多路召回、RRF、Constraint Engine 和最终 Catalog 校验。

## 8. 阶段 3：Grounded Deep Path 首个增量

当前已完成复杂度路由、Retrieval Probe、结构化 Planner、有界 DAG、节点依赖、并发限制、节点停止、局部故障降级、二层 RRF、充分性判断、最多一次 Replan、澄清/拒答，以及完整的 SSE 与 Receipt 记录。简单请求不执行 Probe，保持原 Fast Path 的两次工具调用；Fast Path 结果不足时可以在预算内升级。

详细设计和新增文件职责见 [Grounded Deep Path 首个增量](../01-architecture/deep-path.md) 与 [Deep Path DAG 执行设计](../01-architecture/dag-execution.md)。

## 9. 阶段 4：Session Intent、Profile 与 Consent 首个增量

当前已完成：

- 将当前任务的 `SessionIntentSnapshot` 与跨会话 `LongTermProfile` 分离；
- 个性化和行为保存授权默认关闭，不从会话文本隐式推断长期画像；
- 长期偏好写入前强制检查个性化授权；
- 授权关闭后阻止排序读取，同时保留用户查询和删除数据的能力；
- 画像按 `tenant_id + user_id` 隔离，并提供查询、授权、偏好更新和删除 API；
- Runtime 在意图解析后只更新 Session Intent，不写入 Profile。

随后完成了行为反馈闭环的第二个增量：

- 建立曝光、点击、收藏、负反馈和转化事件契约；
- 使用租户范围内的 `event_id` 实现幂等写入和冲突检测；
- 写入检查行为保存授权，召回同时检查行为保存和个性化授权；
- 提供 `POST /agent/feedback`，并在删除 Profile 时传播删除行为数据；
- 新增 `behavior_recall` LangChain Tool，执行目录状态、ACL 和查询相关性校验；
- 行为信号只提升当前查询已经召回的商品，不独立引入历史商品。

第三个增量完成服务端曝光清单与反馈归因校验：Runtime 为已授权登录用户生成曝光批次，在结果和 Receipt 中返回 `exposure_id`；反馈必须匹配租户、用户、会话、请求、商品、位置和时间，召回来源与模型版本使用服务端真值。删除 Profile 会同步删除曝光清单。

第四个增量完成行为事件处理管道：使用 SQLite 先持久化再投递行为 Store；区分正常、24 小时以上迟到和超过 30 天拒绝的事件；拦截明显机器人 User-Agent；记录 `pending/processed/failed`、尝试次数和错误摘要，并支持幂等重放。删除 Profile 会同步删除队列载荷。

第五个增量完成曝光—行为训练样本生成器、基础 LTR 特征契约和确定性时间切分：只使用曝光时保存的召回分数，按 7 天成熟窗口构造分级标签，复核事件身份与归因范围，并以固定时间边界切分训练、验证和测试集，避免行为结果、未成熟负样本或随机切分造成数据泄漏。

为简化阶段 4 的端到端联调，默认 Bootstrap 还会初始化无密码、无 Token 的 `demo / seekora-demo-user` 测试账户，并预置明确授权的 Profile。该能力仅用于本地开发，不属于正式用户管理或认证设计。

第六个增量完成多轮 Session 约束上下文：工作流读取上一轮意图，支持硬约束修改、追加、删除和清空，并通过事件与 Receipt 记录合并依据；显式新任务不会继承旧约束，临时条件也不会写入长期 Profile。

随后将多轮理解简化为“结构化 AI Patch + 确定性 Reducer”：AI 只判断新任务/追问并输出 `set/add/remove/clear` 操作，Reducer 负责字段白名单、类型标准化和状态修改；模型失败或输出非法时回退到轻量规则。该方案直接调用已配置模型，不需要训练 LTR 或其他模型。

详细设计和新增文件职责见 [Session Intent、Profile 与 Consent](../01-architecture/profile-consent.md)、[多轮 Session 约束上下文](../01-architecture/session-context.md)、[行为反馈与授权召回](../01-architecture/behavior-feedback.md)、[服务端曝光清单与反馈归因](../01-architecture/exposure-validation.md)、[行为事件持久化队列](../01-architecture/event-pipeline.md) 和 [曝光行为训练样本与 LTR 特征契约](../01-architecture/ltr-training.md)。由于当前不具备模型训练条件，LTR Ranker、位置偏差校正和 Teacher/Judge 暂缓；下一步优先实现不依赖训练的 Item Detail 与证据解释链路。
