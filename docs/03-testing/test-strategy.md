# 测试策略

## 当前测试分层

| 文件 | 层级 | 覆盖重点 |
|---|---|---|
| `tests/test_baseline.py` | 单元测试 | 中文 Token、硬约束、权限和排序 |
| `tests/test_evaluation.py` | 单元测试 | Recall、MRR、NDCG 正确性 |
| `tests/test_runtime.py` | 应用集成测试 | 预算、Registry、事件、Session、Receipt 和取消 |
| `tests/test_api.py` | 接口测试 | 健康检查、Web 页面、安全公共配置、Pydantic、SSE 和 Receipt API |
| `tests/test_fast_path.py` | Fast Path 集成测试 | 意图、双路召回、RRF、约束和 Receipt |
| `tests/test_sqlite_state.py` | SQLite 状态集成测试 | Session/Receipt 重启恢复、TTL、裁剪、身份隔离和并发版本 |
| `tests/test_langchain_workflow.py` | 框架测试 | LangGraph 节点、LangChain Tool 类型和可信字段 Schema 隔离 |
| `tests/test_llm_intent.py` | Provider 边界测试 | 配置脱敏、必填项、结构化映射和规则回退；不访问网络 |
| `tests/test_dag.py` | Deep Path DAG 测试 | 依赖校验、并发上限、节点停止和局部故障降级 |
| `tests/test_profile.py` | 画像与授权测试 | 默认拒绝、显式授权、租户隔离、排序屏蔽和删除 |
| `tests/test_sqlite_profile.py` | 长期记忆持久化测试 | SQLite 恢复、删除和种子不覆盖已有授权 |
| `tests/test_item_detail.py` | 详情工具测试 | 工具 Schema、批量详情、租户隔离和 ACL |
| `tests/test_idempotency.py` | 请求幂等测试 | SQLite 跨重启回放、载荷冲突和处理中保护 |
| `tests/test_behavior.py` | 行为闭环测试 | 写入授权、幂等冲突、双重 Consent、ACL、相关性和删除传播 |
| `tests/test_exposure.py` | 曝光归因测试 | 授权登记、服务端归因、关联校验、时钟偏差和删除隔离 |
| `tests/test_event_pipeline.py` | 事件管道测试 | 持久化、迟到策略、机器人过滤、冲突、失败重放和 SQLite 恢复 |
| `tests/test_demo_account.py` | 测试账户测试 | 初始 Profile、授权状态、普通用户隐私默认值和无认证秘密 |
| `tests/test_training.py` | 排序评测数据测试 | 分级标签、成熟窗口、归因复核、曝光时特征和时间切分 |
| `tests/test_session_context.py` | 多轮约束测试 | 条件替换、删除、清空和新任务隔离 |
| `tests/test_constraint_lifecycle.py` | 约束生命周期测试 | 元数据兼容、组合边界、过期、类目挂起/恢复、冲突和确认式放宽 |
| `tests/test_llm_session_context.py` | 多轮 AI 边界测试 | 结构化 Patch、规则降级和非法安全字段拦截 |
| `tests/test_kuaisearch.py` | 外部目录转换测试 | 类目过滤、Schema 映射、合成测试属性、确定性采样和路径保护 |

当前共 111 个测试。测试必须在安装项目依赖的 Python 环境执行。

## 必须保持的不变量

- 最终 Item 属于检索候选集合；
- 所有返回 Item 满足租户、权限、状态和硬约束；
- 未注册或重名工具被明确拒绝；
- 租户、用户、ACL 和 ToolRuntime 不得出现在模型可见工具 Schema 中；
- 网络等临时工具故障允许降级，程序错误必须继续抛出而不能被静默吞掉；
- 工具调用不能超过预算；
- 成功、取消和失败请求均生成 Receipt；
- 非法 HTTP 请求在进入 Runtime 前被拒绝；
- 相同输入和版本下的排序结果可重复。
- 未配置 API Key 时默认模式可启动，测试日志与配置摘要不得包含 Key；
- LLM Provider 失败时不得绕过规则回退和最终 Catalog 约束复核。
- Session Intent 不得被隐式写入长期 Profile；
- Session 和 Receipt 必须可跨 Store 重建恢复，并按配置的 TTL/保留期清理；
- Session 旧版本写入必须失败，同一租户会话不得静默切换用户身份；
- AI 只能提交白名单 ConstraintPatch，最终 Session 必须由确定性 Reducer 修改；
- 只有 active 约束可以参与过滤；过期/挂起状态必须可审计，identity 约束不能被普通 Session Patch 删除；
- 冲突或零结果只允许返回需用户确认的单约束放宽建议，不能自动修改硬约束；
- 未授权的长期偏好不得写入或进入排序链路；
- 相同 `user_id` 在不同租户下的 Profile 必须隔离。
- 长期画像必须在 SQLite Store 重建后恢复，开发种子不得覆盖用户已有授权；
- Item Detail 必须再次执行租户、状态和 ACL 校验，解释只能引用已验证证据；
- 相同 `client_request_id` 与相同载荷不得重复执行，不同载荷不得复用该 ID；
- 相同事件重复写入必须幂等，不同载荷不得复用同一事件 ID；
- 行为召回必须经过双重授权，且不能独立引入当前查询未召回的商品；
- 删除 Profile 必须同步传播到同租户用户的行为数据。
- 反馈身份、请求、商品和位置必须与服务端曝光清单一致；
- 客户端提交的召回来源和模型版本不得覆盖服务端归因真值。
- 事件必须先入持久化队列再投递 Sink，失败状态必须可重放；
- 超龄或明显机器人事件不得进入行为数据，迟到事件必须被显式标记。
- 未走完归因窗口的曝光不得生成负样本，模型特征不得读取曝光后的行为结果；
- 开发、验证和测试集必须按曝光时间切分，同一曝光不得跨集合。

## 后续补充

- Contract Test：行为反馈、Search、Catalog、Ranker 和 LLM Provider；
- 属性测试：继续扩展单位换算、租户隔离和未知 Item；
- Golden Set：至少 300 条真实人工查询；
- 故障注入：单召回源、Store、LLM 和 Catalog 故障；
- 负载测试：100 QPS、SSE、TP95/TP99 和长时间稳定性；
- 安全测试：Prompt Injection、越权、SSRF 和敏感信息泄漏。
