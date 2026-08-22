# Seekora Agent

Seekora 由 `Seek`（探索与检索）和 `Aurora`（照亮信息）组合而来，表达“从复杂信息中发现并照亮可信选择”。仓库和发行包统一使用 `seekora-agent`，Python 导入包使用符合标识符规范的 `seekora_agent`。

这是基于 LangChain/LangGraph、按照《搜索推荐 Agent 技术路线》建设的实现项目。当前已完成阶段 3，并进入阶段 4 的用户画像与授权边界建设：

- 核心 Item、Query、Constraint 和 Golden Query 数据契约；
- 目录数据质量检查；
- 支持中文的轻量 BM25 关键词基线；
- 确定性结构化过滤；
- Recall@K、MRR、NDCG@K 离线评测；
- 30 条、6 个品类的固定样例目录，15 条 Golden Query、测试和基线报告生成入口。
- 单 Agent Fast Path 运行时、SQLite Session、执行预算和取消；
- LangChain `@tool` 定义业务工具，统一注册到 LangGraph `ToolNode`；
- 租户、用户和 ACL 通过 `ToolRuntime` 注入，不暴露给模型填写；
- SQLite Recommendation Receipt，支持跨进程重启查询与保留期清理；
- 基于 `client_request_id` 的 SQLite 请求幂等、并发保护和完整 SSE 回放；
- FastAPI + SSE 查询接口。
- 规则意图与数值约束结构化；
- 关键词/语义双路并行召回和 RRF；
- 确定性 Constraint Engine 与最终 Catalog 校验。
- LangGraph StateGraph/ToolNode 编排和 LangChain `@tool` 工具契约。
- 可选的 LangChain `ChatOpenAI` 结构化意图解析，以及失败时的规则回退。
- 类 GPT 的同源 Web 测试台，可实时查看模型解析器、SSE 执行进度、结果和 Receipt。
- 复杂度路由、Retrieval Probe、结构化计划和受预算约束的 Deep Path 多查询执行。
- 结果充分性判断、最多一次 Replan、Fast Path 零结果升级以及可审计的澄清/拒答。
- 有界 Deep Plan DAG、节点依赖、并发限制、停止条件和独立分支故障降级。
- Session Intent 与长期 Profile 分离，个性化和行为存储默认关闭并要求显式授权。
- 多轮会话由结构化 AI 生成 ConstraintPatch、确定性 Reducer 执行，支持修改、追加、删除和清空，并保留规则降级。
- Constraint 支持作用域、来源、轮次、状态、优先级和过期时间；支持跨轮过期、跨类目挂起/恢复、冲突检测及需确认的最小放宽建议。
- 定义 Embedding/VectorIndex 端口和版本化增量向量索引，并提供本地 SentenceTransformer Embedding Challenger。
- LangGraph 增加无训练 Cross-Encoder 语义复核节点；Challenger 记录分数但不改变 RRF，失败时确定性降级。
- 用户画像查询、授权、显式偏好更新和删除 API，支持租户隔离。
- SQLite 持久化长期画像，服务重启后保留显式授权和偏好。
- 曝光/点击等行为事件的授权写入、幂等去重、删除传播和 ACL 安全行为召回。
- 服务端曝光清单、反馈身份/商品/位置校验，以及可信召回来源和模型版本归因。
- SQLite 持久化反馈队列、24 小时迟到水位线、异常时间拒绝、机器人过滤和幂等重放。
- 批量 Item Detail 工具和基于权威目录事实的可追溯解释链路。
- 曝光—行为排序评测样本、7 天成熟窗口、版本化基础特征和防泄漏时间切分；不进行 LTR 模型训练。
- KuaiSearch-Lite 电子数码商品的流式转换、确定性开发采样、合成测试规格和数据质量报告。
- 预置 `demo / seekora-demo-user` 本地测试账户，可直接覆盖曝光、反馈和行为召回链路。

开发文档入口见 [docs/README.md](docs/README.md)，实施计划见
[docs/00-overview/implementation-plan.md](docs/00-overview/implementation-plan.md)。
API Key 与模型配置见 [docs/02-development/llm-configuration.md](docs/02-development/llm-configuration.md)。
Web 页面使用说明见 [docs/02-development/frontend-testing.md](docs/02-development/frontend-testing.md)。

## 快速开始

安装项目和开发测试依赖：

```powershell
conda env create -f environment.yml
conda activate seekora-agent
python -m pip install -e ".[dev]"
```

运行离线基线：

```powershell
cd seekora-agent
$env:PYTHONPATH = "src"
python -m seekora_agent.interfaces.cli quality
python -m seekora_agent.interfaces.cli search --query "8000元以内适合编程的轻薄本"
python -m seekora_agent.interfaces.cli evaluate --golden data/golden/queries.jsonl --output reports/baseline.json
python -m unittest discover -s tests -v
```

启动阶段 1 API：

```powershell
python -m uvicorn seekora_agent.bootstrap:app --host 127.0.0.1 --port 8000
```

启动后打开 `http://127.0.0.1:8000/` 使用 Web 测试台。

CLI、API 和 Web 测试台默认读取
`data/processed/kuaisearch-electronics/items.jsonl`；最小样例仅在显式传入
`--catalog data/sample/items.jsonl` 或设置 `SEEKORA_CATALOG_PATH` 时启用。

发送流式查询：

```powershell
curl.exe -N -X POST http://127.0.0.1:8000/agent/query `
  -H "Content-Type: application/json" `
  -d '{"query":"适合编程的轻薄本","tenant_id":"demo","session_id":"demo-session"}'
```

## 当前边界

样例数据仅用于验证工程链路，不代表正式业务 Golden Set。进入 Fast Path 开发前，需要确定首个业务域、接入真实目录快照，并把评测集扩充到至少 300 条经人工复核的查询。
