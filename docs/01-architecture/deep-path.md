# Grounded Deep Path 首个增量

## 目标

本增量实现阶段 3 的第一段可运行闭环：高置信简单请求继续走 Fast Path；低置信、研究型、多歧义或硬约束较多的请求进入 Deep Path。Deep Path 先执行低成本 Retrieval Probe，再生成有限、可序列化的查询计划，最后复用现有召回、约束和目录校验能力。

当前链路：

```text
resolve_intent → route
  ├─ fast → recall ───────────────────────┐
  └─ deep → probe → plan → deep_recall ──┤
                                          ↓
                         constraints → result
```

## 确定性边界

- 路由只使用结构化意图中的模式、置信度、歧义数和硬约束数；
- Probe 只向 Planner 暴露候选数量、来源计数、来源重叠和失败来源；
- Plan 是有上限的结构化步骤，不保存私有思维链；
- 多查询结果使用第二层 RRF 融合，避免直接比较不同查询和来源的原始分数；
- Probe、计划、路由原因和所有工具调用都会写入 Receipt；
- 最终 Item 仍必须通过 Constraint Engine 和 Catalog 校验。

## 新增文件职责

| 文件 | 作用 |
|---|---|
| `domain/deep_path.py` | 定义路由决定、Probe 摘要和结构化计划等稳定领域契约。 |
| `application/deep_path.py` | 实现复杂度路由、低成本 Probe 和受约束 Planner。 |
| `tests/test_deep_path.py` | 验证路由规则、Deep Path 事件/Receipt，以及 Fast Path 不承担 Probe 成本。 |
| `docs/01-architecture/deep-path.md` | 记录本增量链路、信任边界、文件职责和后续边界。 |

## 当前边界与后续任务

当前 Planner 是确定性实现，最多生成两个并行查询；尚未实现 LLM Planner、一次 Replan、跨节点依赖 DAG、澄清/拒答和外部 Web 检索。下一增量应先增加结果充分性判断与最多一次 Replan，再接入澄清和停止条件。
