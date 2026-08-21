# Constraint 生命周期与最小放宽

本增量把硬约束从三元组扩展为可审计状态对象，同时保持旧 JSON 兼容。安全原则是：系统可以判断约束是否可执行、是否冲突，也可以提出建议，但不能替用户静默放宽硬条件。

## 数据契约

`Constraint` 在 `field/operator/value` 外包含：

- `scope`：`contextual` 只对当前轮有效，`session` 在当前任务内继承，`identity` 来自可信身份上下文；
- `source`：`query/session/profile/system`，记录原始来源而不是当前读取位置；
- `source_turn`：首次产生该约束的会话轮次；
- `confidence`：0～1 的解析置信度；
- `status`：`active/suspended/expired`；
- `priority`：非负整数，数值越小越优先成为放宽候选；
- `expires_at`：可选 ISO-8601 截止时间。

旧数据缺少这些字段时按 `session/query/active/priority=100` 恢复，因此现有 Golden Query 和 Session 快照无需迁移。

## 状态转换

```text
创建 ─→ active ─→ expired
              └→ suspended ─→ active
```

- 下一轮归并前，`contextual` 或到达 `expires_at` 的约束转为 `expired`；
- 跨类目追问时，内存、续航和重量等笔记本专属条件转为 `suspended`，返回笔记本类目时恢复；
- `remove/clear/set` 生成删除或替换审计记录，但不会允许普通 Session Patch 删除 `identity` 约束；
- `new_task` 不继承任何上一任务条件，并记录 `invalidated_by_new_task`，防止约束污染。

只有当前时间点的 `active` 约束会参与路由计数、BM25 基线过滤和最终 Catalog 复核。挂起与过期对象只为审计和可控恢复而保留。

## 冲突与最小放宽

确定性冲突检测覆盖同字段多个不同等值、下界高于上界、等值落在边界之外以及不可比较值。冲突不会进入无意义的 Replan。

当冲突或过滤后零结果时，`ConstraintEngine` 最多返回一条删除建议，按低优先级、低置信度和较新来源选择。建议同时出现在 `constraints.applied`、终止事件和 Receipt 的 `relaxation_suggestions` 中，并固定携带 `requires_confirmation=true`。只有用户后续明确提交 Patch，Reducer 才会改变约束。

## 文件职责

- `domain/models.py`：Constraint 元数据、校验、序列化与 active 判断；
- `application/session_context.py`：轮次归并、过期、挂起、恢复和身份作用域保护；
- `application/constraints.py`：active 过滤、冲突检测、Catalog 复核和最小放宽建议；
- `application/runtime.py`：SSE 与 Receipt 审计；
- `tests/test_constraint_lifecycle.py`：兼容恢复、组合边界、过期、跨类目与确认式放宽测试。
