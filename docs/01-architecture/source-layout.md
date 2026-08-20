# 源码目录与依赖规范

## 1. 目录结构

```text
src/seekora_agent/
├── config/                 # 环境配置模型，不输出敏感值
│   └── settings.py
├── domain/                 # 业务实体与不变量，不依赖其他项目层
│   ├── fast_path.py
│   └── models.py
├── application/            # 用例编排、协议、端口和执行状态机
│   ├── contracts.py
│   ├── catalog.py
│   ├── constraints.py
│   ├── intent.py
│   ├── recall.py
│   ├── tool_registry.py
│   ├── receipt.py
│   ├── runtime.py
│   ├── session.py
│   └── workflow.py
├── infrastructure/         # 文件、搜索引擎、Store 和工具的具体适配器
│   ├── catalog.py
│   ├── catalog_repository.py
│   ├── intent/
│   │   ├── rule_based.py
│   │   └── langchain_llm.py
│   ├── llm/openai.py
│   ├── search/bm25.py
│   ├── search/semantic.py
│   ├── stores/memory.py
│   ├── tools/catalog_search.py
│   └── tools/vector_search.py
├── interfaces/             # 外部输入适配层
│   ├── cli.py
│   ├── http/api.py
│   └── http/static/        # 同源 Web 测试台
│       ├── index.html
│       ├── styles.css
│       └── app.js
├── evaluation/             # 离线指标和评测逻辑
│   └── metrics.py
├── bootstrap.py            # 依赖装配与应用启动入口
└── __init__.py             # 包版本
```

## 2. 依赖方向

```text
interfaces ─┐
bootstrap ──┼→ application → domain
            └→ infrastructure → application/domain
evaluation ────────────────→ domain + search adapter
```

约束如下：

- `domain` 不得导入 FastAPI、数据库、搜索引擎或 application；
- `application` 只依赖 domain 和自己定义的 Protocol/DTO；
- `infrastructure` 实现 application 定义的端口，可以依赖第三方 SDK；
- `interfaces` 只负责协议转换和参数校验，不编写排序、过滤或会话规则；
- `bootstrap.py` 是唯一负责选择具体实现并完成依赖注入的位置；
- 测试可以直接构造内存适配器，但生产逻辑不得直接实例化它们。

## 3. 模块职责

| 模块 | 作用 | 不应承担的职责 |
|---|---|---|
| `domain/models.py` | Item、Constraint、Query、Result 领域对象 | 文件读取、HTTP、数据库调用 |
| `application/contracts.py` | Agent 请求、事件、预算和控制异常 | FastAPI/Pydantic 请求模型 |
| `application/runtime.py` | 消费 LangGraph 更新并映射 SSE/Receipt | 创建具体基础设施 |
| `application/workflow.py` | 定义并编译 Fast Path StateGraph | HTTP 和具体搜索引擎 SDK |
| `application/recall.py` | 选择召回工具、并行调用和 RRF | 具体搜索引擎 SDK |
| `application/tool_registry.py` | 将工具注册到 ToolNode，并通过 ToolRuntime 注入可信上下文 | 具体搜索实现和模型工具选择策略 |
| `application/constraints.py` | 硬约束与最终目录复核 | 自然语言约束猜测 |
| `application/session.py` | Session 模型和 Store/Cancellation 端口 | Redis 实现 |
| `application/receipt.py` | Receipt 模型和持久化端口 | 数据库实现 |
| `config/settings.py` | 环境变量校验、安全配置摘要和 Provider 开关 | Prompt、业务规则或 SDK 调用 |
| `infrastructure/search/bm25.py` | 阶段 0 BM25 和确定性过滤 | HTTP 路由、Session 管理 |
| `infrastructure/catalog.py` | JSONL 数据加载和质量检查 | Agent 编排 |
| `infrastructure/stores/memory.py` | 本地 Session、Receipt、取消适配器 | 生产持久化承诺 |
| `infrastructure/tools/catalog_search.py` | 用 `@tool` 定义 BM25 工具，仅暴露任务参数 | 工具选择策略和身份参数生成 |
| `infrastructure/intent/langchain_llm.py` | 结构化 LLM 意图解析、类型标准化和规则回退 | 召回、排序或最终约束裁决 |
| `infrastructure/llm/openai.py` | 根据安全配置创建 LangChain `ChatOpenAI` | Prompt 与领域决策 |
| `interfaces/http/api.py` | FastAPI、SSE 和 HTTP 校验 | 业务排序和权限推断 |
| `interfaces/http/static/*` | 模型调用与 Fast Path 的浏览器测试台 | API Key、业务规则或服务端状态 |
| `interfaces/cli.py` | 离线质量、搜索和评测命令 | 在线服务状态 |
| `evaluation/metrics.py` | Recall、MRR、NDCG | 在线请求处理 |
| `bootstrap.py` | 读取配置和装配依赖 | 领域规则 |

## 4. 新模块放置判断

- 新业务对象或业务不变量：`domain`；
- 新用例、状态机、端口或跨工具编排：`application`；
- OpenSearch、Redis、PostgreSQL、LLM Provider：`infrastructure`；
- HTTP、CLI、消息消费者入口：`interfaces`；
- 实现选择和环境变量读取：`bootstrap.py`；
- 评测数据处理和指标：`evaluation`。
