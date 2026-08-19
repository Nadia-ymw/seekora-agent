# 测试策略

## 当前测试分层

| 文件 | 层级 | 覆盖重点 |
|---|---|---|
| `tests/test_baseline.py` | 单元测试 | 中文 Token、硬约束、权限和排序 |
| `tests/test_evaluation.py` | 单元测试 | Recall、MRR、NDCG 正确性 |
| `tests/test_runtime.py` | 应用集成测试 | 预算、Registry、事件、Session、Receipt 和取消 |
| `tests/test_api.py` | 接口测试 | 健康检查、Web 页面、安全公共配置、Pydantic、SSE 和 Receipt API |
| `tests/test_fast_path.py` | Fast Path 集成测试 | 意图、双路召回、RRF、约束和 Receipt |
| `tests/test_langchain_workflow.py` | 框架测试 | LangGraph 节点和 LangChain Tool 类型 |
| `tests/test_llm_intent.py` | Provider 边界测试 | 配置脱敏、必填项、结构化映射和规则回退；不访问网络 |

当前共 35 个测试。测试必须在 `seekora-agent` Conda 环境执行。

## 必须保持的不变量

- 最终 Item 属于检索候选集合；
- 所有返回 Item 满足租户、权限、状态和硬约束；
- 未注册或重名工具被明确拒绝；
- 工具调用不能超过预算；
- 成功、取消和失败请求均生成 Receipt；
- 非法 HTTP 请求在进入 Runtime 前被拒绝；
- 相同输入和版本下的排序结果可重复。
- 未配置 API Key 时默认模式可启动，测试日志与配置摘要不得包含 Key；
- LLM Provider 失败时不得绕过规则回退和最终 Catalog 约束复核。

## 后续补充

- Contract Test：Search、Catalog、Profile、Ranker 和 LLM Provider；
- 属性测试：约束组合、租户隔离和未知 Item；
- Golden Set：至少 300 条真实人工查询；
- 故障注入：单召回源、Store、LLM 和 Catalog 故障；
- 负载测试：100 QPS、SSE、TP95/TP99 和长时间稳定性；
- 安全测试：Prompt Injection、越权、SSRF 和敏感信息泄漏。
