# 编码与扩展规范

## 基本约定

- Python 3.11+，公共函数和端口必须有类型标注；
- 业务对象优先使用 dataclass，HTTP 边界使用 Pydantic；
- application 使用 Protocol 描述外部依赖；
- 工具使用 LangChain `BaseTool`/`StructuredTool`，返回包含状态、版本和数据的结构化字典；
- 所有 Item 必须使用 canonical `item_id`；
- 硬约束和 ACL 必须在语义重排前确定性执行；
- 同分排序必须有稳定的二级排序键，保证回放一致性。

## 新增 Tool

1. 在 `infrastructure/tools` 创建实现；
2. 使用 Pydantic 定义参数 Schema；
3. 通过 `StructuredTool.from_function` 创建同步或异步 Tool；
4. 返回 `status`、`error_code`、`retryable`、`source_version` 和 `data`；
5. 在 `bootstrap.py` 注入 RecallOrchestrator 或 Agent；
6. 增加成功、Schema、权限、超时和部分失败测试；
7. 更新架构文档和 Receipt 字段说明。

## 新增工作流节点

节点放在 `application/workflow.py` 或独立 application 模块中，接收 State 并返回状态增量。不得原地修改 State；新增节点后必须声明边、编译图，并为节点更新和错误路径增加测试。

## 新增 Store

在 application 中定义端口，在 infrastructure 中实现。Runtime 不得通过 `if redis`、`if postgres` 判断具体实现。

## 配置与密钥

非敏感配置可以使用环境变量；密钥不得写入源码、测试数据、日志或 Receipt。后续引入配置类时，仍由 `bootstrap.py` 完成加载。
