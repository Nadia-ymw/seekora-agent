# 多轮 Session 约束上下文

本增量采用“AI 负责理解、确定性代码负责执行”的边界，不需要训练模型，也不允许模型直接覆盖 Session。

```text
用户本轮输入 + 上一轮 ResolvedIntent
          ↓
SessionContextPatchResolver
          ↓
ConstraintPatch(new_task/follow_up, set/add/remove/clear)
          ↓
SessionContextResolver（白名单校验、类型标准化、生命周期归并）
          ↓
最终 ResolvedIntent
```

## 运行模式

- `SEEKORA_INTENT_RESOLVER=openai`：使用 LangChain 结构化输出判断任务关系并生成 Patch；模型超时、输出不完整或包含非法字段时回退到规则解析器；
- `SEEKORA_INTENT_RESOLVER=rules`：完全离线，只使用轻量中文规则生成相同 Patch；
- 第一轮没有历史意图时不调用 Patch 解析器，直接采用当前意图。

AI 可以理解“预算放宽一点”“之前的价格不要限制”等表达，但只允许输出价格、内存、续航、重量和类别字段。租户、用户、ACL 和目录状态不在 Patch Schema 中。

## Patch 操作

- `set`：替换指定字段的旧约束；
- `add`：追加上下界，遇到相同字段和操作符时更新原槽位；
- `remove`：删除指定字段的全部约束；
- `clear`：清空全部用户硬约束；
- `new_task`：不读取上一轮约束，防止跨任务污染。

合并成功时发送 `session.context_applied`；存在状态变化或冲突时发送 `constraints.lifecycle`，随后发送最终的 `intent.resolved`。Receipt 的 `session_context` 保存解析器版本、Patch 操作、来源请求以及继承、替换和删除字段，`constraint_lifecycle` 保存逐条状态变化与冲突。

每条约束都携带 `scope/source/source_turn/confidence/status/priority/expires_at`。`contextual` 在下一轮过期，`session` 在同一任务中继承，`identity` 不允许被普通 `remove/clear` Patch 删除。跨类目追问会挂起不适用的类目专属字段，返回原类目时恢复；显式 `new_task` 仍完全隔离上一任务。完整规则见 [Constraint 生命周期与最小放宽](constraint-lifecycle.md)。

## 安全边界与限制

- 只读取同一 `tenant_id + session_id` 的上一轮意图；
- 临时条件不会写入长期 Profile；
- Reducer 再次检查字段、操作符、数值类型和 `in` 列表，不信任模型输出；
- 模型不能直接修改 Session，也不能操作租户和权限字段；
- 无结果或冲突时只返回 `requires_confirmation=true` 的最小放宽建议，未经用户新一轮确认不会修改约束；
- 当前通过 SQLite 保存最近一次 Session Intent 和有限消息历史，支持 TTL、最大消息数裁剪与重启恢复；尚未实现语义摘要压缩；
- OpenAI 模式的多轮请求会额外调用一次结构化模型，需要纳入延迟和费用观测。

## 新增文件职责

- `src/seekora_agent/domain/session_context.py`：定义 Patch 操作、任务关系和合并结果等纯领域契约；
- `src/seekora_agent/infrastructure/session_context/__init__.py`：声明 Session Context 基础设施适配器包；
- `src/seekora_agent/infrastructure/session_context/rule_based.py`：无模型环境及 AI 失败时的轻量 Patch 降级解析器；
- `src/seekora_agent/infrastructure/session_context/langchain_llm.py`：定义结构化 AI Patch Schema、Prompt、类型转换和失败回退；
- `tests/test_llm_session_context.py`：验证 AI Patch、Provider 失败降级和非法安全字段拦截。

## 修改文件职责

- `application/session_context.py`：只保留 Patch 契约、白名单校验和确定性 Reducer，不再分析中文关键词；
- `application/workflow.py`：异步执行 Patch 解析和归并节点；
- `bootstrap.py`：根据现有意图解析配置装配 AI 或规则 Patch 解析器；
- `tests/test_session_context.py`：验证 Reducer 与规则降级的修改、追加、删除、清空和隔离语义。
