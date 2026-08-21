# 技术路线实现状态与后续开发规划

> 对照文档：仓库根目录《搜索推荐Agent技术路线.md》v1.1  
> 评估日期：2026-08-20  
> 当前项目版本：0.21.0  
> 验证基线：`nanobot` 环境 111 个自动化测试通过

## 1. 结论

当前项目已经形成可运行的模块化单体原型：单主 Agent、Fast/Deep 双路径、LangGraph 编排、技术路线五类只读工具中的四类、确定性约束、证据解释、多轮约束、授权画像、行为反馈、SQLite 持久化和 SSE API 均有代码与测试。

注意：现有《实施任务规划》使用“阶段 0～5”，技术路线使用“Phase 0～4”，两者编号口径不同。本文件统一按《搜索推荐Agent技术路线.md》的 Phase 编号评估。

但按技术路线的“门禁”而不是按“是否存在代码”判断，当前准确位置是：

- **Phase 0：部分完成，正式门禁未通过。** 已有样例目录、KuaiSearch 转换、15 条样例 Golden Query 和基础指标；缺少 300～500 条人工金集、正式业务指标、真实数据质量验收和延迟预算。
- **Phase 1：本地工程闭环基本完成，质量/性能门禁未通过。** Fast Path、约束、RRF、详情和证据、多轮修改已实现；向量召回仍是 TF-IDF 开发替身，开源 Cross-Encoder/在线轻量重排服务和正式性能验证未完成。
- **Phase 2：核心编排基本完成，外部检索与验收未完成。** Probe、复杂度路由、结构化计划、有界 DAG、一次 Replan、澄清和拒答已实现；外部 `web_search` Tool 适配、跨源证据安全和复杂集对照评测未实现。
- **Phase 3：完成前半段。** Session/Profile 分离、Consent、SQLite 长期画像、反馈归因、行为召回和排序评测数据契约已实现；预训练小模型评测、Shadow、Canary、A/B 和生产数据闭环未实现。
- **Phase 4：尚未开始。** 共享 Backbone、生成式召回、蒸馏、广告链路和轨迹级优化均应继续保持实验性。

因此项目处于：**Phase 3 基础能力开发期，同时需要补还 Phase 0～2 的正式验收债务**。当前适合本地联调、契约验证和算法原型，不应描述为达到生产发布门禁。

## 2. 状态判定规则

| 状态 | 含义 |
|---|---|
| 已实现 | 主链路有代码、测试和默认装配，可以本地运行 |
| 部分实现 | 存在开发态替身或只完成契约，尚未达到技术路线目标 |
| 未实现 | 当前源码没有对应主链路能力 |
| 暂缓 | 需要训练资源、正式数据或外部平台，已明确不在当前增量执行 |

“已实现”不等于“门禁通过”。例如 Fast Path 可以运行，但没有 300～500 条人工金集和 P95 报告时，Phase 1 仍不能验收。

## 3. 总体架构对照

| 技术路线能力 | 当前状态 | 当前实现 | 主要差距 |
|---|---|---|---|
| 单主 Agent | 已实现 | `AgentRuntime` + LangGraph 工作流统一管理预算、工具、结果和 Receipt | 缺少生产认证网关和分布式追踪 |
| Fast/Deep 双路径 | 已实现 | 复杂度路由、Fast Path、Probe、结构化 Planner、DAG、Replan | 路由置信度未用人工金集校准 |
| 通用 LLM 理解 | 部分实现 | 可选 LangChain ChatModel 解析 Intent 和 Session Patch，失败回退规则 | 没有 Provider 指标、熔断和 Prompt 版本持久化 |
| 专用小模型/重排 | 未实现 | 已有可复用的曝光行为评测数据契约 | 尚未接入开源 Cross-Encoder 或在线轻量模型服务；不规划 LTR 训练 |
| 传统检索推荐 | 部分实现 | BM25、TF-IDF 语义替身、行为加权、RRF | 没有 OpenSearch/ANN、CF/序列模型和正式特征服务 |
| Working State | 已实现 | LangGraph State、预算、取消和节点状态 | 仅单进程执行 |
| Session State | 已实现（单实例） | SQLite 保存最近意图和消息，支持 TTL、裁剪、版本冲突与重启恢复 | 缺少语义摘要和多副本共享状态 |
| Constraint Store | 基本实现 | 结构化硬约束、Session 合并、Scope、来源、状态、过期、冲突和确认式最小放宽 | 类目字段适用矩阵和正式人工集门槛仍待补齐 |
| User Profile | 已实现（单实例） | Consent、显式正负偏好、SQLite、查询/更正/删除 API | 缺少加密、TTL、审计和行为候选偏好确认流程 |
| Execution Trace | 部分实现 | SSE 事件、工具耗时、SQLite Receipt、请求幂等回放 | 没有 OpenTelemetry 和离线轨迹回放器 |

## 4. 请求流程对照

### 4.1 Fast Path

| 路线步骤 | 状态 | 说明 |
|---|---|---|
| Intent、硬约束、软/负偏好解析 | 已实现 | 规则和可选结构化 LLM；支持失败回退 |
| 字段与权限校验 | 已实现 | ToolRuntime 注入可信租户/用户/ACL，Constraint Engine 最终复核 |
| 关键词、向量、行为并行召回 | 部分实现 | 三路 Tool 和并行调用已完成；“向量”为 TF-IDF，行为为轻量加权 |
| 融合、去重、硬约束 | 已实现 | RRF、canonical item_id、Catalog 复核和过滤原因 |
| 预训练小模型重排 | 未实现 | 当前仅有 RRF 和行为提升 | 接入开源 Cross-Encoder 或在线轻量模型服务，并提供确定性降级 |
| Top-K 语义复核 | 未实现 | 尚无 Cross-Encoder/在线重排服务 |
| Top-K 证据解释 | 已实现（目录证据） | Item Detail + EvidenceComposer，不由模型补写事实 |
| 多轮修改约束 | 已实现 | AI 仅生成白名单 Patch，确定性 Reducer 执行 |

### 4.2 Deep Path

| 路线步骤 | 状态 | 说明 |
|---|---|---|
| Retrieval Probe | 已实现 | 输出候选、来源和约束相关摘要 |
| 复杂度路由 | 已实现（待校准） | 规则化、可审计，但缺少真实复杂集校准 |
| 结构化 Planner | 已实现 | 当前为确定性 Planner，不输出自由文本思维链 |
| DAG 依赖与并发 | 已实现 | 节点依赖校验、并发上限、停止和局部降级 |
| 一次 Replan | 已实现 | 受预算、修订号和充分性判断限制 |
| 澄清/拒答 | 已实现 | 结果不足或证据不足时终止开放循环 |
| 外部 Web 检索 | 未实现 | 缺少外部 Web Search Tool 适配、来源策略、引用和 Prompt Injection 隔离 |
| 复杂集优于 Fast Path 的证明 | 未实现 | 缺少分层人工复杂集、对照报告和统计判断 |

## 5. 核心数据契约差距

| 契约 | 当前状态 | 已有字段 | 仍需补充 |
|---|---|---|---|
| Intent | 部分实现 | mode、domain、retrieval_query、hard/soft/negative、confidence、ambiguities | entities、sort_goal、Top-N intent |
| Constraint | 部分实现 | field、operator、value | scope、source、source_turn、confidence、status、expires_at |
| Plan | 基本实现 | route 原因、步骤、依赖、修订、Replan 上限 | 字段级 filters、证据覆盖停止条件、外部源策略 |
| Candidate | 已实现 | item_id、融合分、来源分、原因、constraint_pass | 预训练小模型重排分、semantic judge 和多样性分数 |
| Evidence | 已实现（站内目录） | field、value、source_uri、observed_at、trust_level | 评论/政策/Web 证据、动态字段实时复核和冲突证据 |

## 6. 模块实现状态

### 6.1 已实现的主要模块

- FastAPI + SSE 查询、取消、Receipt、Profile 和 Feedback API；
- LangChain `@tool`、`ToolRuntime` 和 LangGraph `ToolNode` 工具注册；
- `catalog_search`、`vector_search`、`behavior_recall`、`item_detail`；
- BM25、TF-IDF 语义召回、RRF、硬约束和最终 Catalog 校验；
- Fast/Deep 路由、Probe、DAG、停止条件、一次 Replan、澄清和拒答；
- 多轮 ConstraintPatch 与确定性 Reducer；
- SQLite 长期画像、行为事件队列和 `client_request_id` SSE 回放；
- 曝光清单、反馈归因、Consent、删除传播和安全行为召回；
- 曝光行为排序评测样本、成熟窗口、曝光时特征和时间切分契约；
- Item Detail、目录证据和确定性解释；
- KuaiSearch-Lite 电子商品转换及合成测试属性标记；
- 111 个单元、契约、应用和接口自动化测试。

### 6.2 部分实现或开发态替身

- `vector_search` 是内存 TF-IDF 余弦，不是 Embedding + ANN；
- `behavior_recall` 是授权行为加权，不是 ItemCF/UserCF/Swing/序列模型；
- Catalog、Exposure 和行为聚合 Store 仍主要在内存；Session 与 Receipt 已迁移到 SQLite；
- Profile、请求回放和事件队列使用 SQLite，仅面向本地/单实例；
- Receipt 可跨重启查询并按保留期清理，但尚未接入集中审计存储；
- 错误降级、预算和协作取消已实现，但缺少统一的单工具硬超时、熔断和主动中止；
- Web 测试台可以展示执行链路，但不是离线评测 Dashboard；
- 基线报告只有 15 条样例 Query，不代表正式质量结果。

### 6.3 尚未实现

- 外部 `web_search` Tool 及外部证据安全链路；
- 正式 Embedding/ANN 检索和索引版本管理；
- Top-K Cross-Encoder 或小模型语义复核；
- 约束单位字典、完整类目适用矩阵和人工集量化门禁；
- OpenTelemetry Trace、指标采集和告警；
- 持久化 Exposure 和行为聚合，并为多副本部署替换共享 Session/Receipt Store；
- 固定快照的离线 Agent 回放器与评测 Dashboard；
- 负载、故障注入、Prompt Injection、SSRF 和跨租户系统安全测试；
- Shadow、Canary、A/B、Kill Switch 和 Runbook；
- 真实认证网关、可信身份和多租户生产部署；
- 开源 Cross-Encoder/在线轻量重排服务的接入与评测；
- 共享 Backbone、生成式召回和广告独立链路。

## 7. MVP 最小交付清单对照

| 技术路线 MVP 项 | 状态 | 结论 |
|---|---|---|
| `POST /agent/query` 流式接口 | 已实现 | 可本地端到端运行 |
| Intent/Constraint/Plan/Candidate/Evidence Schema | 部分实现 | 主链路字段具备，完整生命周期字段不足 |
| 五类只读工具 | 部分实现 | 4/5，缺少 `web_search` |
| 快慢路由与 Probe | 已实现 | 缺真实复杂集验收 |
| 约束、融合、Top-K 语义复核 | 部分实现 | 约束与融合完成，语义复核缺失 |
| Session Store、Profile API、关闭个性化 | 已实现（单实例） | Session 与 Profile 均使用 SQLite，API 完成 |
| Trace/Receipt、反馈、离线回放、Dashboard | 部分实现 | Receipt/反馈完成；轨迹回放与 Dashboard 缺失 |
| 超时、熔断、降级、拒答、注入测试 | 部分实现 | 预算/降级/拒答已有；硬超时、熔断和系统安全测试缺失 |

## 8. 后续开发原则

1. **先补门禁，再扩 Agent 能力。** 正式数据和可重复评测优先于增加更多模型调用。
2. **确定性安全链路优先。** ACL、约束、目录存在性和动态事实不能交给 LLM 裁决。
3. **LTR 训练移出路线。** 后续不开发 LTR 训练、发布和在线加载链路，现有样本代码仅保留为排序评测与数据质量契约。
4. **直接使用开源预训练权重或在线服务。** Embedding/Cross-Encoder 可以作为可插拔 Challenger，也可以接入在线轻量模型服务；二者都必须有固定评测、版本记录和降级。
5. **本地持久化统一 SQLite。** 长期画像、Session、Receipt、事件队列和请求回放已经使用 SQLite；后续 Exposure 和行为聚合本地实现也采用 SQLite，同时保留 Store Protocol 供生产替换。
6. **外部 Web 通过现成 Search Tool 接入。** 项目不建设通用爬虫；未完成来源策略、内容隔离和引用校验前默认关闭。

## 9. 后续开发步骤规划

### 增量 A：补齐 Phase 0 数据与验收基线（最高优先级）

目标：把“15 条样例能跑”提升为“固定数据上可衡量”。

开发任务：

1. 冻结首个业务域为电子数码，并形成正式 Item 字段字典；
2. 从 KuaiSearch 和自建场景整理 300～500 条分层 Query；
3. 增加 Head/Torso/Tail、简单/复杂/歧义/冲突/越权/注入标签；
4. 建立两人标注与分歧仲裁格式，禁止合成字段作为权威答案；
5. 扩展评测 CLI，输出意图、召回、排序、约束、证据和路径分组报告；
6. 固定目录快照、Golden Set、配置和基线报告版本。

完成门禁：

- 至少 300 条人工复核 Query；
- 数据质量报告无阻断错误；
- 基线可由单条命令重复生成；
- 明确主指标、P95 预算和成本上限。

外部依赖：需要业务标注和指标确认；这部分不能仅靠代码自动完成。

### 增量 B：本地状态持久化与生命周期（Session/Receipt 切片已完成）

目标：完成四类状态在单实例环境的可恢复闭环。

开发任务：

1. 实现 `SQLiteSessionStore`，保存消息、最近意图和版本；
2. 为 Session 增加 TTL、最大轮数和历史摘要/裁剪；
3. 实现 `SQLiteReceiptStore`，支持跨重启查询和按版本回放；
4. 实现 SQLite Exposure 与行为聚合 Store，保留删除传播；
5. 增加迁移版本、事务、并发版本检查和保留期清理；
6. 测试进程重建、并发修改、租户隔离和级联删除。

完成门禁：重启后 Session、Receipt、Profile、Exposure 和反馈状态一致；TTL 与删除后无残留；100% 通过 Store Contract Test。

### 增量 C：完整 Constraint 生命周期

目标：补齐技术路线 4.2 与 5.4 的确定性约束契约。

开发任务：

1. 增加 `scope/source/source_turn/confidence/status/expires_at`；
2. 实现 contextual/session/identity 的继承和过期；
3. 支持跨类目约束挂起、恢复和失效；
4. 检测冲突约束并返回最小放宽建议；
5. 将生命周期变化写入 SSE 和 Receipt；
6. 增加属性测试，覆盖组合约束、单位和未知字段。

完成门禁：硬约束满足率达到正式门槛；跨轮保持、冲突发现和放宽建议在人工集上可量化。

### 增量 D：真实语义召回与无训练语义复核

目标：替换 TF-IDF 开发替身，并在不训练模型的前提下完成 Top-K 语义重排。

开发任务：

1. 定义 Embedding 和 VectorIndex 端口；
2. 接入可直接使用的开源中文/多语 Embedding 权重；
3. 建立版本化向量索引、批量构建和增量更新命令；
4. 保留 TF-IDF 为无模型降级路径；
5. 为 Top 20～50 接入开源 Cross-Encoder，或通过同一接口调用在线轻量重排服务；
6. 使用固定 Golden Set 对比 BM25、TF-IDF、Embedding、Cross-Encoder 和在线服务的收益、延迟、内存与费用。

完成门禁：新语义路径在目标分层集上有稳定增益，失败时自动降级，且 P95/成本满足预算。未达到门禁则保持 Challenger，不替换默认路径。

### 增量 E：受控 `web_search` 与外部证据

目标：通过外部 Web Search Tool/API 完成第五类只读工具，默认仍由 Feature Flag 关闭。

开发任务：

1. 定义统一 WebSearchResult、来源信任级别、发布时间/检索时间和供应商版本；
2. 实现可替换 Provider 适配器、凭据配置、超时、限流、重试和 Feature Flag；
3. 将外部工具返回的摘要和网页片段视为不可信内容，隔离 Prompt Injection；
4. 只允许 Deep Path 在站内证据不足时申请调用；
5. 将网页证据映射到结论和 Citation，冲突时明确标记不确定；
6. 增加恶意来源、注入、版权长度、失效链接和 Provider 故障测试；若 Provider 支持直接抓取 URL，再额外执行 SSRF/DNS/重定向测试。

完成门禁：外部内容不能改变系统策略或工具权限；Citation Precision 和 Evidence Coverage 达标；安全评审通过前 Feature Flag 保持关闭。

### 增量 F：离线回放、评测 Dashboard 与可观测性

目标：让每次改动可以量化比较和定位退化。

开发任务：

1. 建立固定快照的 Agent 轨迹回放器；
2. 从 Receipt 汇总工具选择、参数、Replan、停止质量和错误码；
3. 输出意图、召回、排序、约束、证据、系统指标的分层报告；
4. 建立本地评测 Dashboard，不与聊天测试台混用；
5. 接入 OpenTelemetry Span，覆盖模型、工具、节点和 Store；
6. 固化配置、Prompt、数据、索引和模型版本。

完成门禁：同一快照和版本可重复产生一致报告；任一质量或延迟退化能定位到路径、节点和版本。

### 增量 G：可靠性、安全与性能门禁

目标：从功能原型进入可发布候选。

开发任务：

1. 给模型和每个工具增加独立硬超时、熔断、并发舱壁和主动取消；
2. 注入 LLM、关键词、向量、Catalog、Profile 和事件服务故障；
3. 增加跨租户、Prompt Injection、SSRF、恶意 Tool 输出和删除残留测试；
4. 执行 10→25→50→100 QPS 阶梯负载和一小时稳定性测试；
5. 建立指标、告警、Runbook、Kill Switch 和降级开关；
6. 接入可信认证网关，禁止客户端自报租户、用户和 ACL。

完成门禁：满足项目定义的硬约束、事实准确率、P95、错误率、降级率和 Receipt 完整率目标。

### 增量 H：Shadow、Canary 与 A/B

目标：完成 Phase 3 在线闭环，不直接全量发布。

开发任务：

1. 固定上线候选版本和回滚版本；
2. 先执行历史回放，再执行 Shadow；
3. 内部白名单验证后进入 1% Canary；
4. 通过护栏后开展 5%～20% A/B；
5. 按新老用户、类目、查询头尾和 Fast/Deep 分群分析；
6. 达不到收益或护栏恶化时自动回滚。

完成门禁：离线增益通过在线验证，任何关键分群无明显伤害，回滚和 Kill Switch 演练成功。

### 增量 I：高级模型实验（不包含 LTR 训练）

启动条件：A～H 门禁已通过，基础检索、重排和在线实验稳定。

可选实验：

1. 评测更小的开源重排模型、量化版本或在线服务；
2. 对比 CPU/GPU/远程调用的 P95、吞吐和费用；
3. 评估生成式召回 Challenger，但不得直接替换合法 ID 检索；
4. 评估共享语义表示和广告独立链路；
5. 所有实验先经过离线回放、Shadow 和 A/B；
6. 不建设 LTR 训练、LTR 模型发布或线上 LTR 推理链路。

## 10. 推荐执行顺序与依赖

```text
增量 A 数据与基线 ───────────────┐
                                  ├→ D 真实语义检索 → F 评测与可观测 → G 发布门禁 → H 在线实验
增量 B 状态持久化 → C 约束生命周期 ┘                         │
                                  E 受控 Web ─────────────────┘

A～H 门禁通过后 → I 高级模型实验（不含 LTR 训练）
```

建议实施顺序：

1. 立即开始 B，同时推动需要人工参与的 A；
2. B 完成后实施 C，建立可靠状态与约束底座；
3. A 的首批正式集可用后实施 D，并用数据决定是否启用预训练模型；
4. D 不再等待训练环境，直接评测开源权重或在线轻量服务；E 可并行接入外部 Web Search Tool，但安全测试完成前保持关闭；
5. F 贯穿 C～E，最终形成统一门禁；
6. G 通过后才进入 H；
7. I 仅在 A～H 门禁通过后启动，且不引入 LTR 训练。

## 11. 下一开发增量

增量 C 的首个 Constraint 生命周期切片已经完成：元数据、Session 状态转换、过期、跨类目挂起/恢复、冲突检测、确认式最小放宽以及 SSE/Receipt 审计均已接入。Exposure 与行为聚合持久化继续保留在增量 B 的后续清单。

下一步执行 **增量 D：真实语义召回与无训练语义复核**；在首批正式人工集可用前，新的 Embedding/Cross-Encoder 仅作为 Challenger，不替换默认 TF-IDF/RRF 路径。

增量 C 后续门禁债务包括：

1. 用正式字段字典替换当前电子数码类目的最小适用矩阵；
2. 增加单位归一化与更多组合属性测试；
3. 在人工集上量化硬约束满足率、冲突发现率和放宽建议接受率。
