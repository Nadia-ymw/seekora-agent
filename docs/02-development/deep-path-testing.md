# Deep Path 测试指南

## 1. 用途

本文档提供可以直接复现的 Fast/Deep 路由测试用例，并说明预期事件和结果。它用于本地开发验收，不替代正式 Golden Set 或复杂场景质量评测。

启动服务：

```powershell
conda activate seekora-agent
python -m uvicorn seekora_agent.bootstrap:app --host 127.0.0.1 --port 8000
```

可在 Web 测试台输入下列查询，也可以把查询放入 `/agent/query` 请求正文。

## 2. 推荐用例

| 用例 | 查询 | 预期路由 | 关键预期 |
|---|---|---|---|
| Fast 对照组 | `轻薄笔记本` | `fast` | 无 `probe.completed`，只执行两次召回工具调用。 |
| 跨品类低置信 | `推荐1000元以内适合通勤的主动降噪耳机` | `deep` | 返回 `aud-001`；internal 的 `aud-005` 不得出现。 |
| 多硬约束 | `推荐8000元以内内存32GB以上重量1.4kg以内的轻薄笔记本` | `deep` | 路由原因包含 `many_hard_constraints`，最终仅返回 `lap-001`。 |
| 无法满足 | `推荐200元以内内存32GB以上重量1kg以内的轻薄笔记本` | `deep` | 结果为空，Receipt 记录约束过滤原因，不得编造商品。 |
| 开放式探索 | `帮我找适合远程办公的一套设备` | `deep` | 触发低置信路由；可观察跨品类召回结果。 |

Deep Path 正常事件顺序：

```text
request.accepted
intent.resolved
routing.completed
probe.completed
plan.created
recall.started
dag.completed
recall.completed
constraints.applied
sufficiency.assessed
result
done
```

结果不足时可能在 `sufficiency.assessed` 后出现一次 `plan.replanned`；最终仍不充分时出现 `clarification.required` 或 `response.refused`。

## 3. SSE 调用示例

```powershell
$body = @{
  query = "推荐1000元以内适合通勤的主动降噪耳机"
  tenant_id = "demo"
  session_id = "deep-path-manual-test"
  top_k = 10
} | ConvertTo-Json

curl.exe -N -X POST http://127.0.0.1:8000/agent/query `
  -H "Content-Type: application/json" `
  -d $body
```

从 `request.accepted` 事件取得 `request_id` 后查看执行凭据：

```powershell
curl.exe http://127.0.0.1:8000/agent/receipts/<request_id>
```

重点检查 `route`、`route_decision`、`probe_summary`、`plan`、`dag_executions`、`tool_calls`、`candidate_ids` 和 `filtered_reason_counts`。

## 4. 自动化测试

```powershell
$env:PYTHONPATH = "src"
python -m unittest tests.test_deep_path -v
```

“无法满足”用例会执行最多一次 Replan，之后返回可审计的拒答；无明确商品领域且证据不足时返回最多两个澄清问题。
