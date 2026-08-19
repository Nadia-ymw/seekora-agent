# Deep Path DAG 执行设计

## 1. 目标

Deep Plan 不再作为无约束查询列表直接并行执行，而是先校验为有界 DAG，再按依赖和预算分批调度。执行器保证：节点数和并发数有上限、依赖环在执行前拒绝、单节点失败不丢弃独立分支结果、达到停止条件后不再调用工具。

## 2. 执行流程

```text
DeepPlan
→ 校验 step_id、依赖存在性、依赖环、最大节点数
→ 选择依赖已完成的 ready 节点
→ 按 min(计划并发、系统并发、工具预算容量) 执行
→ 合并成功节点的 RecallResult
→ 记录 completed / failed / skipped
→ 检查候选目标、截止时间和工具预算
→ 第二层 RRF → Constraint Engine → Sufficiency
```

Planner 生成的 `query-2` 是依赖 `query-1` 的可选宽泛节点。如果主查询已经达到候选目标，执行器将其标记为 `skipped`，原因是 `candidate_target_reached`；候选不足时才继续执行。

## 3. 停止和降级

| 原因 | 行为 |
|---|---|
| `candidate_target_reached` | 跳过剩余可选节点，进入约束复核。 |
| `tool_budget_exhausted` | 不再启动新节点，保留已有结果。 |
| `deadline_exhausted` | 停止剩余节点，保留已有结果。 |
| `dependency_failed` | 只跳过依赖失败必需节点的分支。 |
| 单节点全部召回源失败 | 节点标记为 failed；其他独立节点继续。 |

只要至少一个独立节点成功，DAG 就以 `degraded=true` 返回可用候选；最终结果仍必须经过硬约束和 Catalog 验证。如果所有节点均无可用候选，后续 Sufficiency 根据预算决定 Replan、澄清或拒答。

## 4. 新增文件职责

| 文件 | 作用 |
|---|---|
| `domain/dag.py` | 定义节点执行记录和 DAG 执行摘要，作为 SSE 与 Receipt 的稳定契约。 |
| `application/dag.py` | 实现依赖校验、拓扑调度、并发限制、节点停止、局部故障降级和结果融合。 |
| `tests/test_dag.py` | 验证依赖缺失、依赖环、并发上限、候选停止和独立分支降级。 |
| `docs/01-architecture/dag-execution.md` | 说明 DAG 调度语义、停止原因、降级边界和新增文件职责。 |

## 5. 可观测性

每次 `deep_recall` 发送 `dag.completed` 事件，并在 Receipt 的 `dag_executions` 中保存：

- 每个节点的查询、状态、候选数、错误码或跳过原因；
- DAG 停止原因；
- 是否发生降级。

Receipt 不保存私有思维链，只保存可回放的结构化计划和执行事实。
