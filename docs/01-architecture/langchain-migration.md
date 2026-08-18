# LangChain/LangGraph 迁移说明

## 1. 迁移目标

项目从自研 Tool Registry 和手写阶段调用迁移到：

- LangChain `BaseTool` / `StructuredTool`：统一工具 Schema、异步调用和未来模型 Tool Calling；
- LangGraph `StateGraph`：显式描述 Fast Path 节点、边和共享状态；
- LangGraph `astream(..., stream_mode="updates")`：将节点更新转换为现有 SSE 事件；
- LangChain `ChatOpenAI.with_structured_output()`：可选地生成经过 Schema 校验的意图；
- Conda `seekora-agent`：唯一开发和测试环境。

Fast Path 是确定性高置信链路，因此没有使用开放式 `create_agent` 循环。LangChain 官方 Agent 本身构建于 LangGraph；后续 Deep Path 需要模型自主选工具时，可将 `create_agent` 作为一个子图节点接入当前 StateGraph。

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

移除自研 Tool Registry，直接接收 LangChain `BaseTool`。通过 `tool.ainvoke()` 并行调用召回源，对结构化 Tool 输出做 RRF。重复工具名、缺少必需工具和工具异常均在这里收口。

### `infrastructure/tools/catalog_search.py`

使用 Pydantic `CatalogSearchInput` 定义参数 Schema，通过 `StructuredTool.from_function(coroutine=...)` 创建 `catalog_search`。返回结构化字典，而不是供模型阅读的自由文本。

### `infrastructure/tools/vector_search.py`

使用同样方式创建 `vector_search`。两类 Tool 都显式接收租户和 ACL，调用底层搜索前执行权限过滤。

### `bootstrap.py`

创建两个 StructuredTool、RecallOrchestrator、Constraint Engine 和 LangChainFastPathWorkflow，然后把编译图注入 Runtime。所有具体实现仍集中在启动装配层。

0.5.0 起，启动层还根据 `SEEKORA_INTENT_RESOLVER` 选择规则解析器或 OpenAI 结构化解析器。OpenAI 解析异常时回退规则实现，其他图节点不需要感知 Provider。

### `environment.yml`

声明 Conda 环境名 `seekora-agent`、Python 3.11、LangChain、LangGraph、FastAPI、Uvicorn、Pydantic 和测试依赖。项目使用 editable install。

### `tests/test_langchain_workflow.py`

验证工作流确实是编译后的 LangGraph，且召回源确实是 LangChain `BaseTool`，防止后续重构意外退回自研 Registry。

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

当前验证版本为 LangChain 1.3.x、LangChain Core 1.5.x、LangGraph 1.2.x、Python 3.11。规则意图解析仍是默认确定性基线；OpenAI 意图解析必须显式启用。内存语义索引仍是 Embedding 服务的开发态替身。

官方参考：

- https://docs.langchain.com/oss/python/langchain/tools
- https://docs.langchain.com/oss/python/langgraph/graph-api
- https://docs.langchain.com/oss/python/langchain/agents
