# LangChain/LangGraph 迁移说明

## 1. 迁移目标

项目从自研 Tool Registry 和手写阶段调用迁移到：

- LangChain `@tool`：从函数签名、中文 docstring 和 Field 约束生成工具 Schema；
- LangGraph `ToolNode` / LangChain `ToolRuntime`：执行工具并注入租户、用户和 ACL；
- LangGraph `StateGraph`：显式描述 Fast Path 节点、边和共享状态；
- LangGraph `astream(..., stream_mode="updates")`：将节点更新转换为现有 SSE 事件；
- LangChain `ChatOpenAI.with_structured_output()`：可选地生成经过 Schema 校验的意图；
- Conda `seekora-agent`：唯一开发和测试环境。

Fast Path 是确定性高置信链路，因此没有使用开放式 `create_agent` 循环。工具仍按标准 LangChain 方式注册到 `ToolNode`；由业务路由选择召回集合，避免模型绕过预算、授权或目录复核。

## 2. 图结构

```text
START
  → resolve_intent
  → recall
  → apply_constraints
  → compose_result
  → END
```

`FastPathState` 保存请求、执行预算、结构化意图、召回结果、过滤结果和最终 Item。每个节点只返回状态增量，不原地修改状态。

## 3. 新增或改造文件

### `application/workflow.py`

定义 `FastPathState` 和 `LangChainFastPathWorkflow`，创建并编译 StateGraph。它是新的 Fast Path 主编排器，节点依赖仍通过构造函数注入。

### `application/runtime.py`

不再手动依次调用意图、召回和约束模块，而是消费编译图的异步更新流。Runtime 继续负责框架外围职责：request ID、Session、取消、Receipt、预算异常和 SSE 事件映射。

### `application/recall.py`

根据登录状态动态缩小召回工具集合，并行请求已注册工具，对结构化 Artifact 做 RRF。它不再把租户、用户和 ACL 放进工具参数。

### `application/tool_registry.py`

接收标准 LangChain `BaseTool` 列表，检查重名后统一注册到 `ToolNode`。调用时构造标准 Tool Call，由 ToolNode 自动注入 `ToolRuntime[RequestContext]`；执行结果通过 `ToolMessage.artifact` 返回应用层。

### `infrastructure/tools/catalog_search.py`

使用 `@tool` 创建 `catalog_search`。模型可见 Schema 只有 `query` 和 `top_k`；中文 docstring 与 Field 描述帮助模型理解调用条件，工具同时返回可读摘要和结构化 Artifact。

### `infrastructure/tools/vector_search.py`

使用同样方式创建 `vector_search`。目录工具从 `ToolRuntime.context` 读取租户和 ACL，模型无法生成或覆盖这些可信字段。

### `bootstrap.py`

创建三个 `@tool` 工具列表、RecallOrchestrator、Constraint Engine 和 LangChainFastPathWorkflow，然后把编译图注入 Runtime。所有具体实现仍集中在启动装配层。

0.5.0 起，启动层还根据 `SEEKORA_INTENT_RESOLVER` 选择规则解析器或 OpenAI 结构化解析器。OpenAI 解析异常时回退规则实现，其他图节点不需要感知 Provider。

### `environment.yml`

声明 Conda 环境名 `seekora-agent`、Python 3.11、LangChain、LangGraph、FastAPI、Uvicorn、Pydantic 和测试依赖。项目使用 editable install。

### `tests/test_langchain_workflow.py`

验证工作流和召回源类型，并检查模型工具 Schema 只包含 `query/top_k`，防止可信身份字段再次暴露给模型。

## 4. 环境操作

环境已存在时：

```powershell
conda activate seekora-agent
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
```

从定义文件同步环境：

```powershell
conda env update -n seekora-agent -f environment.yml --prune
```

不激活环境也可以执行：

```powershell
conda run -n seekora-agent python -m unittest discover -s tests -v
conda run -n seekora-agent python -m uvicorn seekora_agent.bootstrap:app --port 8000
```

## 5. 版本和边界

当前验证版本为 LangChain 1.3.x、LangChain Core 1.5.x、LangGraph 1.2.x、Python 3.11。`StructuredTool` 仍适用于不能修改原函数或需要动态组装同步/异步实现的场景，但本项目当前三个业务函数优先采用 `@tool`。内存语义索引仍是 Embedding 服务的开发态替身。

官方参考：

- https://docs.langchain.com/oss/python/langchain/tools
- https://docs.langchain.com/oss/python/langgraph/graph-api
- https://docs.langchain.com/oss/python/langchain/agents
