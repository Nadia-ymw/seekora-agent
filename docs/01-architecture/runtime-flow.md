# 运行时与请求链路

## 正常链路

```text
POST /agent/query
→ interfaces/http/api.py：Pydantic 校验、构造 AgentQuery、建立 SSE
→ application/runtime.py：建立 request_id、预算、Session、Receipt
→ application/workflow.py：启动编译后的 LangGraph
→ resolve_intent 节点：结构化意图与硬约束
→ route 节点：根据可审计复杂度信号选择 Fast/Deep Path
→ Fast：recall 节点并行调用 catalog_search 与 vector_search Tool
→ Deep：probe → plan → deep_recall，执行低成本探测和有限多查询
→ infrastructure/search/*：关键词和语义召回
→ recall 节点：RRF 融合
→ apply_constraints 节点：硬约束与 Catalog 最终复核
→ assess_sufficiency 节点：判断返回、一次 Replan、澄清或拒答
→ compose_result 节点：生成结构化结果
→ application/runtime.py：消费图更新，记录 Session 和 Receipt
→ interfaces/http/api.py：输出 result 与 done 事件
```

正常事件顺序：

```text
request.accepted → intent.resolved → routing.completed → recall.started → recall.completed
→ constraints.applied → result → done
```

Deep Path 会增加 `probe.completed`、`plan.created` 和 `sufficiency.assessed`。结果不足时最多出现一次 `plan.replanned`，随后必须返回结果、澄清或拒答。这些事件只包含结构化、可公开的执行信息，不包含私有思维链。

取消路径为 `cancelled → done`，异常路径为 `error → done`。无论成功或失败，Runtime 都会在结束前写入 Receipt。

## 信任边界

当前演示 HTTP 接口只授予 `public` 权限。生产环境中租户、用户和 ACL 必须由认证 Gateway 注入，不能采用客户端请求正文中的自报值。Item、网页和工具输出均视为不可信数据。

## 当前适配器

Session、Receipt 和 Cancellation 使用内存实现，只适合单进程开发。生产部署需在 `infrastructure/stores` 添加 Redis/PostgreSQL 适配器，并在 `bootstrap.py` 替换装配。
